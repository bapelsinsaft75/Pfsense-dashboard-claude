"""Inställningar som kan ändras från webbsidan. Sparas i /data/settings.json.

Värden från miljövariabler används som standard; det som sparas från webbsidan går före.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import logging
import os
import re
import threading
from zoneinfo import ZoneInfo

from .config import Config

log = logging.getLogger("settings")
TZ = ZoneInfo("Europe/Stockholm")

# Larmtyper som kan slås av och på: id → (beskrivning, på som standard, bekräftas med upprepade kontroller)
ALERT_KINDS = {
    "vpn_down": ("VPN-anslutning nere", True, True),
    "gateway_down": ("Gateway nere (t.ex. WAN)", True, True),
    "gateway_degraded": ("Paketförlust eller hög fördröjning på gateway", True, False),
    "source_down": ("pfSense eller UniFi svarar inte", True, True),
    "unifi_offline": ("UniFi-switch eller accesspunkt offline", True, True),
    "suricata": ("Suricata-larm", True, False),
    "pfblocker": ("Klient försöker nå blockerade adresser (pfBlockerNG)", True, False),
    "data_anomaly": ("Ovanligt hög dataförbrukning", True, False),
    "new_device": ("Ny enhet i nätverket", True, False),
    "unraid_down": ("Unraid-server svarar inte", False, True),
    "unraid_problem": ("Unraid: disk med fel, array stoppad eller varm disk", False, True),
    "unraid_watch": ("Övervakad container eller virtuell maskin har stannat", True, True),
    "plex_down": ("Plex-server svarar inte", False, True),
}

# namn: (typ, min, max)
SCHEMA = {
    "retention_days": (int, 1, 365),
    "anomaly_factor": (float, 1.5, 20),
    "anomaly_min_gb": (float, 0.1, 1000),
    "suricata_min_priority": (int, 1, 3),
    "pfblocker_threshold": (int, 1, 10000),
    "email_warnings": (bool, None, None),
    "slack_warnings": (bool, None, None),
    "new_device_alerts": (bool, None, None),  # äldre inställning, ersatt av alert_kinds.new_device
    # bekräftelse: kontrollera var N:e sekund, larma efter M misslyckade kontroller i rad
    "confirm_interval": (int, 10, 900),
    "confirm_count": (int, 1, 20),
    # tysta tider
    "quiet_enabled": (bool, None, None),
    "quiet_start": ("time", None, None),
    "quiet_end": ("time", None, None),
    "quiet_days": ("days", None, None),
    "quiet_allow_critical": (bool, None, None),
    "quiet_digest": (bool, None, None),
    # vilka larm som används, och vilka containrar/VM som övervakas per Unraid-server
    "alert_kinds": ("kinds", None, None),
    "watch": ("watch", None, None),
}


class Settings:
    def __init__(self, cfg: Config, persist: bool = True) -> None:
        self.defaults = {
            "retention_days": cfg.retention_days,
            "anomaly_factor": cfg.anomaly_factor,
            "anomaly_min_gb": cfg.anomaly_min_gb,
            "suricata_min_priority": cfg.suricata_min_priority,
            "pfblocker_threshold": cfg.pfblocker_threshold,
            "email_warnings": cfg.email_warnings,
            "slack_warnings": cfg.slack_warnings,
            "new_device_alerts": True,
            "confirm_interval": 60,
            "confirm_count": 3,
            "quiet_enabled": False,
            "quiet_start": "22:00",
            "quiet_end": "07:00",
            "quiet_days": [0, 1, 2, 3, 4, 5, 6],
            "quiet_allow_critical": False,
            "quiet_digest": True,
            "alert_kinds": {k: v[1] for k, v in ALERT_KINDS.items()},
            "watch": {},
        }
        self.path = os.path.join(cfg.data_dir, "settings.json") if persist else None
        self.saved: dict = {}
        self.lock = threading.Lock()
        if self.path and os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as fh:
                    self.saved = self._validate(json.load(fh), strict=False)
            except (OSError, ValueError) as exc:
                log.warning("Kunde inte läsa %s: %s", self.path, exc)

    def get(self, key: str):
        if key == "alert_kinds":
            kinds = dict(self.defaults["alert_kinds"])
            kinds.update(self.saved.get("alert_kinds", {}))
            if "new_device_alerts" in self.saved and "new_device" not in self.saved.get("alert_kinds", {}):
                kinds["new_device"] = self.saved["new_device_alerts"]
            return kinds
        return copy.deepcopy(self.saved.get(key, self.defaults[key]))

    def all(self) -> dict:
        return {k: self.get(k) for k in SCHEMA if k != "new_device_alerts"}

    def kind_enabled(self, kind: str) -> bool:
        return bool(self.get("alert_kinds").get(kind, True))

    def update(self, values: dict) -> dict:
        clean = self._validate(values, strict=True)
        with self.lock:
            self.saved.update(clean)
            self._save()
        return self.all()

    def set_watch(self, server: str, kind: str, name: str, on: bool) -> dict:
        if kind not in ("containers", "vms"):
            raise ValueError("kind måste vara containers eller vms")
        with self.lock:
            watch = copy.deepcopy(self.saved.get("watch", {}))
            entry = watch.setdefault(server, {"containers": [], "vms": []})
            items = set(entry.get(kind, []))
            (items.add if on else items.discard)(name)
            entry[kind] = sorted(items)
            watch[server] = entry
            self.saved["watch"] = watch
            self._save()
        return self.get("watch")

    def watched(self, server: str, kind: str) -> set[str]:
        return set(self.get("watch").get(server, {}).get(kind, []))

    def _save(self) -> None:
        if self.path:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.saved, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)

    # ── tysta tider ──
    def quiet_now(self, now: dt.datetime | None = None) -> tuple[bool, str | None]:
        """(är det tyst nu, sluttid HH:MM)"""
        if not self.get("quiet_enabled"):
            return False, None
        now = now or dt.datetime.now(TZ)
        start, end = _hm(self.get("quiet_start")), _hm(self.get("quiet_end"))
        days = set(self.get("quiet_days"))
        t = now.hour * 60 + now.minute
        if start == end:
            return False, None
        if start < end:
            active = start <= t < end and now.weekday() in days
        else:  # över midnatt: delen efter midnatt hör till föregående dags period
            if t >= start:
                active = now.weekday() in days
            elif t < end:
                active = (now.weekday() - 1) % 7 in days
            else:
                active = False
        return active, (self.get("quiet_end") if active else None)

    @staticmethod
    def _validate(values: dict, strict: bool) -> dict:
        out = {}
        for key, raw in (values or {}).items():
            if key not in SCHEMA:
                if strict:
                    raise ValueError(f"Okänd inställning: {key}")
                continue
            typ, lo, hi = SCHEMA[key]
            try:
                if typ is bool:
                    val = raw if isinstance(raw, bool) else str(raw).lower() in ("1", "true", "ja", "on", "yes")
                elif typ == "time":
                    if not isinstance(raw, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", raw):
                        raise ValueError
                    val = raw
                elif typ == "days":
                    val = sorted({int(d) for d in raw if 0 <= int(d) <= 6})
                elif typ == "kinds":
                    if not isinstance(raw, dict):
                        raise ValueError
                    val = {k: bool(v) for k, v in raw.items() if k in ALERT_KINDS}
                elif typ == "watch":
                    if not isinstance(raw, dict):
                        raise ValueError
                    val = {str(srv): {"containers": sorted({str(x) for x in (e or {}).get("containers", [])}),
                                      "vms": sorted({str(x) for x in (e or {}).get("vms", [])})}
                           for srv, e in raw.items()}
                else:
                    val = typ(raw)
                    if val < lo or val > hi:
                        raise ValueError
            except (TypeError, ValueError):
                if strict:
                    rng = f" (tillåtet {lo}–{hi})" if lo is not None else ""
                    raise ValueError(f"Ogiltigt värde för {key}: {raw!r}{rng}") from None
                continue
            out[key] = val
        return out


def _hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)

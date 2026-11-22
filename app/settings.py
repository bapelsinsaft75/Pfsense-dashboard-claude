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
    "unraid_disk_error": ("Unraid: disk med fel", False, True),
    "unraid_array": ("Unraid: arrayen är stoppad", False, True),
    "unraid_disk_hot": ("Unraid: varm disk", False, True),
    "unraid_warn": ("Unraid: nästan full, eller container stoppad oväntat", False, True),
    "unraid_watch": ("Övervakad container har stannat", True, True),
    "unraid_vm": ("Övervakad virtuell maskin har stannat", True, True),
    "plex_down": ("Plex-server svarar inte", True, True),
    "pihole_down": ("Pi-hole svarar inte", True, True),
    "quiet_digest": ("Sammanfattning efter tysta tider", True, False),
}

# Grupper i larmtabellen
ALERT_GROUPS = [
    ("Nätverk", ["vpn_down", "gateway_down", "gateway_degraded", "source_down", "unifi_offline"]),
    ("Säkerhet", ["suricata", "pfblocker"]),
    ("Klienter", ["new_device", "data_anomaly"]),
    ("Servrar", ["unraid_down", "unraid_disk_error", "unraid_array", "unraid_disk_hot", "unraid_warn", "unraid_watch", "unraid_vm", "plex_down", "pihole_down"]),
    ("Sammanfattningar", ["quiet_digest"]),
]
ROUTE_SEP = "|"
# larmtyper som delats upp: tidigare val för den gamla typen gäller tills något annat valts
KIND_ALIAS = {"unraid_disk_error": "unraid_problem", "unraid_array": "unraid_problem",
              "unraid_disk_hot": "unraid_problem", "unraid_warn": "unraid_problem"}
# profiler: vilka val i webbläsaren en profil kommer ihåg, och vilka delar som kan döljas
PROFILE_KEYS = {"layout", "tileOrder", "partOrder", "period", "chartView2", "topList", "clientsAll", "layoutCollapsed", "urFilters",
                "urTab", "plexTab", "phDomTab", "phClTab", "phQn", "phQf", "hiddenParts"}
PROFILE_LAYOUTS = {"klassisk", "sidomeny", "rutor", "flikar", "overblick"}  # larmtyp|server|container – mer specifika rader går före mindre specifika

# Larmtyper som normalt är kritiska. En ny e-postmottagare får dessa från början.
CRITICAL_KINDS = {"vpn_down", "gateway_down", "source_down", "suricata", "unraid_down", "unraid_disk_error", "unraid_array",
                  "unraid_watch", "unraid_vm", "plex_down", "pihole_down"}
EMAIL_RE = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+")

VALID_KINDS = set(ALERT_KINDS) | set(KIND_ALIAS.values())  # gamla typer behålls i sparade inställningar

# namn: (typ, min, max)
SCHEMA = {
    "retention_days": (int, 1, 365),
    "wan_down_mbit": (int, 0, 100000),
    "wan_up_mbit": (int, 0, 100000),
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
    # e-postmottagare och vilka larmtyper var och en får. None = använd MAIL_TO
    "recipients": ("recipients", None, None),
    # vilka larmtyper som skickas med e-post och till Slack: typ -> {"email": bool, "slack": bool}
    "channels": ("channels", None, None),
    # vem som får vilka larm: "typ", "typ|server" eller "typ|server|container" -> banner, slack, e-postadresser
    "routes": ("routes", None, None),
    # sparade profiler: namn, utseende och val på sidan
    "profiles": ("profiles", None, None),
}


class Settings:
    def __init__(self, cfg: Config, persist: bool = True) -> None:
        self.defaults = {
            "retention_days": cfg.retention_days,
            "wan_down_mbit": cfg.wan_down_mbit,
            "wan_up_mbit": cfg.wan_up_mbit,
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
            "recipients": None,
            "channels": {},
            "routes": {},
            "profiles": [],
        }
        self.mail_to: list[str] = [a.strip() for a in (cfg.mail_to or "").split(",") if a.strip()]
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
        if key == "channels":
            saved = self.saved.get("channels", {})
            warn_slack = self.get("slack_warnings")
            on = self.get("alert_kinds")  # en avstängd larmtyp skickas inte heller, tills man väljer annat
            return {k: {"email": saved.get(k, {}).get("email", bool(on.get(k, True))),
                        "slack": saved.get(k, {}).get("slack", bool(on.get(k, True)) and (k in CRITICAL_KINDS or warn_slack))}
                    for k in ALERT_KINDS}
        if key == "alert_kinds":
            kinds = dict(self.defaults["alert_kinds"])
            kinds.update(self.saved.get("alert_kinds", {}))
            if "new_device_alerts" in self.saved and "new_device" not in self.saved.get("alert_kinds", {}):
                kinds["new_device"] = self.saved["new_device_alerts"]
            return kinds
        return copy.deepcopy(self.saved.get(key, self.defaults[key]))

    def all(self) -> dict:
        return {k: self.get(k) for k in SCHEMA if k != "new_device_alerts"}

    def channel(self, kind: str, channel: str) -> bool:
        """Ska larm av den här typen skickas via kanalen ("email" eller "slack")?"""
        ch = self.get("channels").get(kind)
        if ch is None:  # okänd larmtyp: e-post som vanligt, Slack enligt valet för varningar
            return True if channel == "email" else bool(self.get("slack_warnings"))
        return bool(ch.get(channel, True))

    def route(self, kind: str, source: str | None = None) -> dict:
        """Vart ett larm ska: {"banner", "slack", "emails"}. source kan vara "server" eller "server|container"."""
        routes = self.saved.get("routes") or {}
        parts = [p for p in (source or "").split(ROUTE_SEP) if p]
        for k in (kind, KIND_ALIAS.get(kind)):
            if not k:
                continue
            for n in range(len(parts), -1, -1):
                key = ROUTE_SEP.join([k, *parts[:n]])
                if key in routes:
                    return copy.deepcopy(routes[key])
        own = kind in (self.saved.get("alert_kinds") or {}) or kind in (self.saved.get("channels") or {})
        return self._legacy_route(kind if own or kind not in KIND_ALIAS else KIND_ALIAS[kind], parts[0] if parts else None)

    def _legacy_route(self, kind: str, server: str | None) -> dict:
        """Det som gällde innan tabellen fanns: larmtyp på/av, kanal per typ och mottagare per typ och server."""
        if kind == "quiet_digest":  # tidigare en kryssruta under Aviseringar: e-post och Slack till alla
            on = bool(self.get("quiet_digest"))
            rec = self.get("recipients")
            emails = [r["email"] for r in rec] if rec is not None else list(self.mail_to)
            return {"banner": False, "slack": on, "emails": emails if on else []}
        if kind == "unraid_problem":  # den gamla, sammanslagna typen
            saved_ch = (self.saved.get("channels") or {}).get(kind)
            on = (self.saved.get("alert_kinds") or {}).get(kind, False)
            ch = saved_ch or {"email": on, "slack": on}
            rec = self.get("recipients")
            emails = ([r["email"] for r in rec if kind in r.get("kinds", []) and (server is None or r.get("servers") is None or server in r["servers"])]
                      if rec is not None else (list(self.mail_to) if on else [])) if ch.get("email") else []
            return {"banner": bool(on), "slack": bool(ch.get("slack")), "emails": emails}
        ch = self.get("channels").get(kind) or {"email": True, "slack": bool(self.get("slack_warnings"))}
        emails: list[str] = []
        if ch.get("email", True):
            rec = self.get("recipients")
            if rec is None:
                if kind in CRITICAL_KINDS or self.get("email_warnings"):
                    emails = list(self.mail_to)
            else:
                emails = [r["email"] for r in rec if kind in r["kinds"]
                          and (server is None or r.get("servers") is None or server in r["servers"])]
        return {"banner": self.kind_enabled(kind), "slack": bool(ch.get("slack", True)), "emails": emails}

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
                    val = {k: bool(v) for k, v in raw.items() if k in VALID_KINDS}
                elif typ == "recipients":
                    if raw is None:
                        val = None
                    else:
                        if not isinstance(raw, list) or len(raw) > 20:
                            raise ValueError
                        val, seen = [], set()
                        for r in raw:
                            email = str((r or {}).get("email", "")).strip()
                            if not EMAIL_RE.fullmatch(email):
                                raise ValueError(f"Ogiltig e-postadress: {email or '(tom)'}")
                            if email.lower() in seen:
                                raise ValueError(f"{email} finns redan")
                            seen.add(email.lower())
                            servers = r.get("servers")
                            val.append({"email": email, "name": str(r.get("name") or "").strip()[:60],
                                        "kinds": sorted({k for k in r.get("kinds", []) if k in VALID_KINDS}),
                                        # None = alla servrar, annars en lista med servernamn
                                        "servers": None if servers is None else sorted({str(x) for x in servers})})
                elif typ == "channels":
                    if not isinstance(raw, dict):
                        raise ValueError
                    val = {k: {"email": bool((v or {}).get("email", True)), "slack": bool((v or {}).get("slack", True))}
                           for k, v in raw.items() if k in VALID_KINDS}
                elif typ == "routes":
                    if not isinstance(raw, dict) or len(raw) > 2000:
                        raise ValueError
                    val = {}
                    for k, v in raw.items():
                        k = str(k)[:300]
                        if k.split(ROUTE_SEP)[0] not in VALID_KINDS or not isinstance(v, dict):
                            continue
                        emails = [str(e).strip() for e in (v.get("emails") or []) if EMAIL_RE.fullmatch(str(e).strip())]
                        val[k] = {"banner": bool(v.get("banner")), "slack": bool(v.get("slack")),
                                  "emails": sorted(set(emails), key=str.lower)}
                elif typ == "profiles":
                    if not isinstance(raw, list) or len(raw) > 30:
                        raise ValueError("Högst 30 profiler.")
                    val, ids = [], set()
                    for pr in raw:
                        pid = re.sub(r"[^a-z0-9-]", "", str((pr or {}).get("id", "")).lower())[:40]
                        name = str((pr or {}).get("name", "")).strip()[:40]
                        if not pid or pid in ids:
                            raise ValueError("Ogiltigt profil-id.")
                        if not name:
                            raise ValueError("Profilen måste ha ett namn.")
                        ids.add(pid)
                        layout = pr.get("layout") if pr.get("layout") in PROFILE_LAYOUTS else "klassisk"
                        prefs = {k: str(v)[:8000] for k, v in (pr.get("prefs") or {}).items()
                                 if k in PROFILE_KEYS and v is not None}
                        val.append({"id": pid, "name": name, "layout": layout, "prefs": prefs,
                                    "updated": int(pr.get("updated") or 0)})
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
            except ValueError as exc:
                if strict and str(exc):
                    raise ValueError(str(exc)) from None
                if strict:
                    rng = f" (tillåtet {lo}–{hi})" if lo is not None else ""
                    raise ValueError(f"Ogiltigt värde för {key}: {raw!r}{rng}") from None
                continue
            except TypeError:
                if strict:
                    rng = f" (tillåtet {lo}–{hi})" if lo is not None else ""
                    raise ValueError(f"Ogiltigt värde för {key}: {raw!r}{rng}") from None
                continue
            out[key] = val
        return out


def _hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)

"""Inställningar som kan ändras från webbsidan. Sparas i /data/settings.json.

Värden från miljövariabler används som standard; det som sparas från webbsidan går före.
"""
from __future__ import annotations

import json
import logging
import os
import threading

from .config import Config

log = logging.getLogger("settings")

# namn: (typ, min, max)
SCHEMA = {
    "retention_days": (int, 1, 365),
    "anomaly_factor": (float, 1.5, 20),
    "anomaly_min_gb": (float, 0.1, 1000),
    "suricata_min_priority": (int, 1, 3),
    "pfblocker_threshold": (int, 1, 10000),
    "email_warnings": (bool, None, None),
    "slack_warnings": (bool, None, None),
    "new_device_alerts": (bool, None, None),
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
        return self.saved.get(key, self.defaults[key])

    def all(self) -> dict:
        return {k: self.get(k) for k in SCHEMA}

    def update(self, values: dict) -> dict:
        clean = self._validate(values, strict=True)
        with self.lock:
            self.saved.update(clean)
            if self.path:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(self.saved, fh, indent=2, ensure_ascii=False)
                os.replace(tmp, self.path)
        return self.all()

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
                else:
                    val = typ(raw)
                    if val < lo or val > hi:
                        raise ValueError
            except (TypeError, ValueError):
                if strict:
                    raise ValueError(f"Ogiltigt värde för {key}: {raw!r} (tillåtet {lo}–{hi})") from None
                continue
            out[key] = val
        return out

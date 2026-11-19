"""Inställningar från miljövariabler (sätts i Unraid-mallen)."""
from __future__ import annotations

import ipaddress
import os


def env(key: str, default: str | None = None) -> str | None:
    value = os.environ.get(key)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def env_bool(key: str, default: bool) -> bool:
    value = env(key)
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "ja", "on")


def env_int(key: str, default: int) -> int:
    value = env(key)
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


def env_float(key: str, default: float) -> float:
    value = env(key)
    try:
        return float(value) if value is not None else default
    except ValueError:
        return default


def _networks(raw: str) -> list[ipaddress._BaseNetwork]:
    nets = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            nets.append(ipaddress.ip_network(part, strict=False))
    return nets


class Config:
    def __init__(self) -> None:
        self.data_dir = env("DATA_DIR", "/data")
        self.web_port = env_int("WEB_PORT", 8080)

        # pfSense REST API (pfSense-pkg-RESTAPI v2)
        self.pfsense_url = (env("PFSENSE_URL", "") or "").rstrip("/")
        self.pfsense_api_key = env("PFSENSE_API_KEY", "") or ""
        self.pfsense_verify_ssl = env_bool("PFSENSE_VERIFY_SSL", False)
        self.pfsense_poll = max(3, env_int("PFSENSE_POLL_SECONDS", 10))
        self.pfsense_client_poll = max(10, env_int("PFSENSE_CLIENT_POLL_SECONDS", 60))
        self.wan_interface = (env("WAN_INTERFACE", "wan") or "wan").lower()

        self.lan_subnets = _networks(env("LAN_SUBNETS", "192.168.1.0/24") or "")

        # UniFi Network Application
        self.unifi_url = (env("UNIFI_URL", "") or "").rstrip("/")
        self.unifi_user = env("UNIFI_USER", "") or ""
        self.unifi_pass = env("UNIFI_PASS", "") or ""
        self.unifi_site = env("UNIFI_SITE", "default") or "default"
        self.unifi_verify_ssl = env_bool("UNIFI_VERIFY_SSL", False)
        self.unifi_poll = max(10, env_int("UNIFI_POLL_SECONDS", 30))

        # NetFlow / IPFIX från softflowd (0 = av)
        self.netflow_port = env_int("NETFLOW_PORT", 2055)
        # auto | netflow | states
        self.traffic_source = (env("TRAFFIC_SOURCE", "auto") or "auto").lower()

        # GeoIP: tom = ladda ner DB-IP Lite automatiskt
        self.geoip_path = env("GEOIP_DB_PATH", "") or ""
        self.geoip_auto_download = env_bool("GEOIP_AUTO_DOWNLOAD", True)

        # Hemstad på kartan
        self.home_city = env("HOME_CITY", "Örebro") or "Örebro"
        self.home_lat = env_float("HOME_LAT", 59.2741)
        self.home_lon = env_float("HOME_LON", 15.2066)

        self.retention_days = max(2, env_int("RETENTION_DAYS", 35))
        self.debug_endpoints = env_bool("DEBUG_ENDPOINTS", True)

        demo = (env("DEMO", "auto") or "auto").lower()
        if demo == "auto":
            self.demo = not self.pfsense_url
        else:
            self.demo = demo in ("1", "true", "yes", "ja", "on")

    def is_lan(self, ip: str) -> bool:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(addr in net for net in self.lan_subnets if net.version == addr.version)

    @property
    def unifi_enabled(self) -> bool:
        return bool(self.unifi_url and self.unifi_user and self.unifi_pass)


def is_public(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False

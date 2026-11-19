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

        # Standardvärden; kan ändras på webbsidan (sparas i /data/settings.json)
        self.retention_days = min(365, max(1, env_int("RETENTION_DAYS", 14)))
        self.anomaly_factor = env_float("ALERT_DATA_FACTOR", 3.0)
        self.anomaly_min_gb = env_float("ALERT_DATA_MIN_GB", 2.0)
        self.suricata_min_priority = env_int("ALERT_SURICATA_PRIORITY", 1)
        self.pfblocker_threshold = env_int("ALERT_PFBLOCKER_THRESHOLD", 25)
        self.email_warnings = env_bool("EMAIL_WARNINGS", False)

        # Övervakning av gateways och VPN i pfSense
        self.vpn_poll = max(10, env_int("VPN_POLL_SECONDS", 30))

        # Syslog från pfSense (Suricata, pfBlockerNG, brandväggslogg). 0 = av
        self.syslog_port = env_int("SYSLOG_PORT", 5514)
        self.pfblocker_tracker_prefix = env("PFBLOCKER_TRACKER_PREFIX", "177") or "177"

        # E-post (Proton Mail SMTP-token: smtp.protonmail.ch, port 587)
        self.smtp_host = env("SMTP_HOST", "smtp.protonmail.ch") or ""
        self.smtp_port = env_int("SMTP_PORT", 587)
        self.smtp_user = env("SMTP_USER", "") or ""
        self.smtp_token = env("SMTP_TOKEN", "") or ""
        self.mail_from = env("MAIL_FROM", "") or ""
        self.mail_to = env("MAIL_TO", "") or ""
        self.mail_subject_prefix = env("MAIL_SUBJECT_PREFIX", "[Nätverket hemma]") or ""

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

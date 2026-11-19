"""Klient för UniFi Network Application (fristående, t.ex. Docker på Unraid, eller UniFi OS)."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import Config
from .pfsense import norm_mac, pick, to_int

log = logging.getLogger("unifi")

BANDS = {"ng": "2,4 GHz", "na": "5 GHz", "6e": "6 GHz", "6g": "6 GHz"}

MODELS = {
    "U7PG2": "UAP-AC-Pro", "U7LR": "UAP-AC-LR", "U7LT": "UAP-AC-Lite", "U7NHD": "UAP-nanoHD",
    "UAL6": "U6 Lite", "UALR6": "U6 LR", "UAP6MP": "U6 Pro", "UAE6": "U6 Enterprise", "UAPA6A": "U6+",
    "U6M": "U6 Mesh", "U7PRO": "U7 Pro", "U7PROMAX": "U7 Pro Max", "UKPW": "U6 Mesh",
    "USMINI": "USW-Flex-Mini", "USF5P": "USW-Flex", "USL8LP": "USW-Lite-8-PoE", "USL8LPB": "USW-Lite-8-PoE",
    "USL16LP": "USW-Lite-16-PoE", "USL16LPB": "USW-Lite-16-PoE", "US8P60": "US-8-60W", "US8P150": "US-8-150W",
    "US16P150": "US-16-150W", "US24": "US-24", "US24P250": "US-24-250W", "USL24": "USW-24", "USL24P": "USW-24-PoE",
    "US68P": "USW-Pro-8-PoE", "USW-ULTRA": "USW-Ultra", "USM8P": "USW-Ultra", "US624P": "USW-Pro-24-PoE",
    "USF5P_V2": "USW-Flex", "USWED35": "USW-Flex-2.5G-5", "USWED37": "USW-Flex-2.5G-8",
}


class UniFiError(Exception):
    pass


class UniFi:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.prefix = ""
        self.csrf: str | None = None
        self.logged_in = False
        self.client = httpx.AsyncClient(base_url=cfg.unifi_url, verify=cfg.unifi_verify_ssl, timeout=20)

    async def login(self) -> None:
        creds = {"username": self.cfg.unifi_user, "password": self.cfg.unifi_pass, "remember": True}
        try:
            resp = await self.client.post("/api/login", json=creds)
            if resp.status_code == 404:
                # UniFi OS (UDM, Cloud Key Gen2 m.fl.)
                resp = await self.client.post("/api/auth/login", json=creds)
                if resp.status_code < 400:
                    self.prefix = "/proxy/network"
                    self.csrf = resp.headers.get("x-csrf-token")
        except httpx.HTTPError as exc:
            raise UniFiError(f"Kan inte nå UniFi ({exc.__class__.__name__}): {exc}") from exc
        if resp.status_code in (400, 401, 403):
            raise UniFiError("UniFi nekade inloggningen. Kontrollera användarnamn och lösenord (lokalt konto).")
        if resp.status_code >= 400:
            raise UniFiError(f"UniFi svarade {resp.status_code} vid inloggning")
        self.logged_in = True

    async def get(self, path: str) -> list[dict]:
        if not self.logged_in:
            await self.login()
        headers = {"x-csrf-token": self.csrf} if self.csrf else None
        try:
            resp = await self.client.get(self.prefix + path, headers=headers)
            if resp.status_code == 401:
                await self.login()
                resp = await self.client.get(self.prefix + path, headers=headers)
        except httpx.HTTPError as exc:
            raise UniFiError(f"Kan inte nå UniFi ({exc.__class__.__name__}): {exc}") from exc
        if resp.status_code >= 400:
            raise UniFiError(f"UniFi svarade {resp.status_code} på {path}. Stämmer UNIFI_SITE?")
        body = resp.json()
        return body.get("data", []) if isinstance(body, dict) else body

    async def fetch(self) -> tuple[list[dict], list[dict]]:
        site = self.cfg.unifi_site
        clients = await self.get(f"/api/s/{site}/stat/sta")
        devices = await self.get(f"/api/s/{site}/stat/device")
        return clients, devices

    async def close(self) -> None:
        await self.client.aclose()


def parse_client(row: dict) -> dict | None:
    mac = norm_mac(row.get("mac"))
    if not mac:
        return None
    wired = bool(row.get("is_wired"))
    info: dict[str, Any] = {
        "mac": mac,
        "ip": row.get("ip") or row.get("last_ip"),
        "alias": row.get("name"),
        "hostname": row.get("hostname"),
        "oui": row.get("oui"),
        "kind": "wired" if wired else "wifi",
    }
    if wired:
        info["dev"] = norm_mac(pick(row, "sw_mac", "last_uplink_mac", "uplink_mac"))
        info["port"] = to_int(pick(row, "sw_port", "last_uplink_remote_port"))
    else:
        info["dev"] = norm_mac(pick(row, "ap_mac", "last_uplink_mac"))
        info["band"] = BANDS.get(str(row.get("radio", "")).lower(), row.get("radio_proto") or row.get("radio"))
        info["ssid"] = row.get("essid")
        sig = row.get("signal")
        if isinstance(sig, (int, float)) and sig < 0:
            info["rssi"] = int(sig)
        elif isinstance(row.get("rssi"), (int, float)):
            info["rssi"] = int(row["rssi"]) - 95 if row["rssi"] > 0 else int(row["rssi"])
    return info


def parse_device(row: dict) -> dict | None:
    mac = norm_mac(row.get("mac"))
    dtype = str(row.get("type", "")).lower()
    if not mac or dtype not in ("uap", "usw"):
        return None
    model = str(row.get("model", ""))
    ports = row.get("port_table") or []
    uplink = row.get("uplink") or {}
    own_uplink = to_int(uplink.get("port_idx"))
    if own_uplink is None:
        own_uplink = next((to_int(p.get("port_idx")) for p in ports if p.get("is_uplink")), None)
    return {
        "id": mac,
        "kind": "ap" if dtype == "uap" else "switch",
        "name": row.get("name") or MODELS.get(model, model) or mac,
        "model": MODELS.get(model, model),
        "ports": len(ports) or to_int(row.get("num_port")) or 0,
        "ports_up": sorted(to_int(p.get("port_idx")) for p in ports if p.get("up") and to_int(p.get("port_idx")) is not None),
        "own_uplink": own_uplink,
        "uplink_mac": norm_mac(uplink.get("uplink_mac")),
        "uplink_port": to_int(uplink.get("uplink_remote_port")),
        "online": to_int(row.get("state")) == 1,
    }

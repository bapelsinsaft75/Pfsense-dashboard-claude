"""Status för en Unraid-server via dess GraphQL-API (Unraid 7.x, Settings → Management Access → API Keys).

Fältnamnen i API:et har ändrats mellan versioner. Därför provas flera varianter av varje fråga, och den första
som fungerar används. Vilken variant som valdes syns på /api/debug/unraid.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any

import httpx

log = logging.getLogger("unraid")

QUERIES: dict[str, list[str]] = {
    "system": [
        "{ info { os { hostname uptime } cpu { brand threads } versions { core { unraid } } }"
        " metrics { cpu { percentTotal } memory { total used percentTotal } } }",
        "{ info { os { hostname uptime } cpu { brand threads } versions { unraid } memory { total used free } } }",
        "{ info { os { hostname uptime } cpu { brand threads } } }",
        "{ info { os { hostname uptime } } }",
    ],
    "array": [
        "{ array { state capacity { kilobytes { free used total } }"
        " parities { name device size status temp isSpinning }"
        " disks { name device size status temp fsSize fsFree fsUsed isSpinning }"
        " caches { name device size status temp fsSize fsFree fsUsed isSpinning } } }",
        "{ array { state capacity { kilobytes { free used total } }"
        " parities { name device size status temp } disks { name device size status temp fsSize fsFree fsUsed }"
        " caches { name device size status temp fsSize fsFree fsUsed } } }",
        "{ array { state capacity { kilobytes { free used total } } disks { name size status temp } } }",
    ],
    "parity": [
        "{ parityHistory { date duration speed status errors } }",
    ],
    "docker": [
        "{ docker { containers { names image state status autoStart } } }",
        "{ dockerContainers { names image state status autoStart } }",
        "{ docker { containers { names state status } } }",
    ],
    "vms": [
        "{ vms { domains { name state } } }",
        "{ vms { domain { name state } } }",
    ],
}

DISK_STATUS = {
    "DISK_OK": ("ok", "OK"), "DISK_NP": ("idle", "Saknas"), "DISK_NP_DSBL": ("err", "Saknas, avstängd"),
    "DISK_INVALID": ("err", "Ogiltig"), "DISK_WRONG": ("err", "Fel disk"), "DISK_DSBL": ("err", "Avstängd"),
    "DISK_DSBL_NEW": ("warn", "Ny, avstängd"), "DISK_NEW": ("warn", "Ny"), "DISK_NP_MISSING": ("err", "Saknas"),
}
HOT_C = 50
FULL_PCT = 90


class UnraidError(Exception):
    pass


class Unraid:
    def __init__(self, name: str | None, url: str, key: str, verify: bool) -> None:
        self.configured_name = name
        self.url = url
        self.client = httpx.AsyncClient(base_url=url, verify=verify, timeout=20, follow_redirects=True,
                                        headers={"x-api-key": key, "Content-Type": "application/json",
                                                 "Accept": "application/json"})
        self.chosen: dict[str, int] = {}
        self.errors: dict[str, str] = {}
        self.raw: dict[str, Any] = {}
        self.data: dict[str, Any] = {}
        self.status: dict[str, Any] = {"enabled": True, "ok": False, "error": None, "last": None, "level": None,
                                       "detail": None, "name": name or "Unraid"}

    async def _query(self, q: str) -> dict:
        try:
            resp = await self.client.post("/graphql", json={"query": q})
        except httpx.HTTPError as exc:
            raise UnraidError(f"Kan inte nå Unraid på {self.url} ({exc.__class__.__name__}). "
                              f"Är 'Host access to custom networks' påslaget?") from exc
        if resp.status_code in (401, 403):
            raise UnraidError("Unraid nekade åtkomst. Kontrollera API-nyckeln och att den har läsbehörighet.")
        try:
            body = resp.json()
        except ValueError as exc:
            raise UnraidError(f"Unraid svarade {resp.status_code} utan JSON. Stämmer adressen?") from exc
        if body.get("errors"):
            raise ValueError("; ".join(e.get("message", "?") for e in body["errors"])[:300])
        return body.get("data") or {}

    async def _section(self, name: str) -> dict | None:
        variants = QUERIES[name]
        start = self.chosen.get(name, 0)
        for i in range(start, len(variants)):
            try:
                data = await self._query(variants[i])
            except ValueError as exc:  # GraphQL-fel: prova nästa variant
                self.errors[name] = str(exc)
                continue
            self.chosen[name] = i
            self.errors.pop(name, None)
            self.raw[name] = data
            return data
        return None

    async def poll(self) -> None:
        try:
            sections = {n: await self._section(n) for n in QUERIES}
        except UnraidError as exc:
            self.status.update(ok=False, error=str(exc), level="err")
            log.warning("%s", exc)
            return
        if not any(sections.values()):
            self.status.update(ok=False, level="err",
                               error="Unraid-API:et svarade men inga frågor fungerade. Se /api/debug/unraid.")
            return
        self.data = build(sections)
        level, reasons = health(self.data)
        name = self.configured_name or self.data.get("hostname") or "Unraid"
        self.status.update(ok=True, error=None, last=time.time(), level=level, name=name,
                           detail="; ".join(reasons) if reasons else "Allt ser bra ut")

    async def close(self) -> None:
        await self.client.aclose()


# ── tolkning ──
def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _uptime_seconds(v: Any) -> float | None:
    """API:et ger antingen sekunder eller tidpunkten för senaste start."""
    if v is None:
        return None
    n = _num(v)
    if n is not None:
        return n
    try:
        boot = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return (dt.datetime.now(dt.timezone.utc) - boot.astimezone(dt.timezone.utc)).total_seconds()
    except ValueError:
        return None


def _disk(d: dict, role: str) -> dict:
    raw_status = str(d.get("status") or "")
    level, label = DISK_STATUS.get(raw_status, ("ok" if not raw_status else "warn", raw_status or "OK"))
    temp = _num(d.get("temp"))
    spinning = d.get("isSpinning")
    state = "idle" if spinning is False and level == "ok" else level
    size_kb, fs_size, fs_used = _num(d.get("size")), _num(d.get("fsSize")), _num(d.get("fsUsed"))
    used_pct = fs_used / fs_size * 100 if fs_size and fs_used is not None else None
    if level == "ok" and temp is not None and temp >= HOT_C:
        state, label = "warn", "Varm"
    elif level == "ok" and spinning is False:
        label = "Vilar"
    return {
        "name": d.get("name") or role, "role": role, "device": d.get("device"),
        "status": state, "label": label,
        "size_bytes": (fs_size or size_kb or 0) * 1024 if (fs_size or size_kb) else None,
        "used_pct": used_pct, "temp": temp if spinning is not False else None,
    }


def build(s: dict[str, dict | None]) -> dict:
    out: dict[str, Any] = {}
    info = ((s.get("system") or {}).get("info") or {})
    metrics = (s.get("system") or {}).get("metrics") or {}
    os_ = info.get("os") or {}
    cpu = info.get("cpu") or {}
    versions = info.get("versions") or {}
    out["hostname"] = os_.get("hostname")
    out["uptime"] = _uptime_seconds(os_.get("uptime"))
    out["version"] = (versions.get("core") or {}).get("unraid") if isinstance(versions.get("core"), dict) else versions.get("unraid")
    out["cpu"] = {"brand": cpu.get("brand"), "threads": cpu.get("threads"),
                  "pct": _num((metrics.get("cpu") or {}).get("percentTotal"))}
    mem = metrics.get("memory") or info.get("memory") or {}
    total, used = _num(mem.get("total")), _num(mem.get("used"))
    pct = _num(mem.get("percentTotal")) or (used / total * 100 if total and used is not None else None)
    out["memory"] = {"total": total, "used": used, "pct": pct}

    arr = (s.get("array") or {}).get("array") or {}
    out["array_state"] = str(arr.get("state") or "").upper() or None
    kb = ((arr.get("capacity") or {}).get("kilobytes") or {})
    t, u, f = _num(kb.get("total")), _num(kb.get("used")), _num(kb.get("free"))
    out["capacity"] = {"total": t * 1024 if t else None, "used": u * 1024 if u is not None else None,
                       "free": f * 1024 if f is not None else None, "pct": u / t * 100 if t and u is not None else None}
    disks = [_disk(d, "parity") for d in arr.get("parities") or []]
    disks += [_disk(d, "data") for d in arr.get("disks") or []]
    disks += [_disk(d, "cache") for d in arr.get("caches") or []]
    out["disks"] = [d for d in disks if d["label"] != "Saknas" or d["role"] == "parity"]

    hist = (s.get("parity") or {}).get("parityHistory") or []
    if hist:
        last = sorted(hist, key=lambda h: str(h.get("date") or ""))[-1]
        out["parity_last"] = {"date": last.get("date"), "errors": last.get("errors"), "status": last.get("status"),
                              "duration": last.get("duration")}

    d = s.get("docker") or {}
    containers = (d.get("docker") or {}).get("containers") if "docker" in d else d.get("dockerContainers")
    out["containers"] = sorted((_container(c) for c in containers or []),
                               key=lambda c: ({"bad": 0, "running": 1, "stopped": 2}[c["state"]], c["name"].lower()))
    v = s.get("vms") or {}
    vms = (v.get("vms") or {})
    domains = vms.get("domains") or vms.get("domain") or []
    out["vms"] = sorted(({"name": x.get("name") or "?", "state": str(x.get("state") or "").lower(),
                          "running": str(x.get("state") or "").upper() in ("RUNNING", "IDLE")} for x in domains),
                        key=lambda x: (not x["running"], x["name"].lower()))
    return out


def _container(c: dict) -> dict:
    names = c.get("names") or []
    name = (names[0] if isinstance(names, list) and names else str(names or "?")).lstrip("/")
    state = str(c.get("state") or "").lower()
    running = state in ("running", "up")
    if running:
        st = "running"
    elif c.get("autoStart") and state in ("exited", "dead", "restarting"):
        st = "bad"  # ska vara igång men är det inte
    else:
        st = "stopped"
    return {"name": name, "image": c.get("image"), "state": st, "status": c.get("status"),
            "autostart": c.get("autoStart")}


def health(d: dict) -> tuple[str, list[str]]:
    errs, warns = [], []
    if d.get("array_state") and d["array_state"] != "STARTED":
        errs.append(f"arrayen är {d['array_state'].lower()}")
    for disk in d.get("disks", []):
        if disk["status"] == "err":
            errs.append(f"{disk['name']}: {disk['label'].lower()}")
        elif disk["label"] == "Varm":
            warns.append(f"{disk['name']} är {disk['temp']:.0f} °C")
        if disk["used_pct"] is not None and disk["used_pct"] >= FULL_PCT and disk["role"] == "cache":
            warns.append(f"{disk['name']} är {disk['used_pct']:.0f} % full")
    cap = d.get("capacity") or {}
    if cap.get("pct") is not None and cap["pct"] >= FULL_PCT:
        warns.append(f"arrayen är {cap['pct']:.0f} % full")
    bad = [c["name"] for c in d.get("containers", []) if c["state"] == "bad"]
    if bad:
        warns.append(f"stoppad oväntat: {', '.join(bad)}")
    return ("err" if errs else "warn" if warns else "ok"), errs + warns

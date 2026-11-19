"""Statistik, frågelogg och styrning av Pi-hole v6 via dess REST-API (/api)."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import defaultdict
from typing import Any
from urllib.parse import quote

import httpx

log = logging.getLogger("pihole")

# Status i frågeloggen som betyder att frågan blockerades, och hur de beskrivs på sidan
BLOCKED_STATUS = {
    "GRAVITY": "blocklista", "GRAVITY_CNAME": "blocklista (CNAME)",
    "REGEX": "regex", "REGEX_CNAME": "regex (CNAME)",
    "DENYLIST": "egen blocklista", "DENYLIST_CNAME": "egen blocklista (CNAME)",
    "EXTERNAL_BLOCKED_IP": "uppströms", "EXTERNAL_BLOCKED_NULL": "uppströms", "EXTERNAL_BLOCKED_NXRA": "uppströms",
    "EXTERNAL_BLOCKED_EDE15": "uppströms", "SPECIAL_DOMAIN": "specialdomän", "DBBUSY": "databasen upptagen",
}
ALLOWED_STATUS = {"FORWARDED": "vidare", "CACHE": "cache", "CACHE_STALE": "cache", "RETRIED": "omförsök",
                  "RETRIED_DNSSEC": "omförsök", "IN_PROGRESS": "pågår"}
CACHED = {"CACHE", "CACHE_STALE"}

# Andra nivåns domäner där den registrerbara domänen har tre delar (t.ex. bbc.co.uk)
TWO_PART_TLDS = {"co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au", "co.nz", "co.jp", "com.br",
                 "com.cn", "com.tr", "co.za", "com.mx", "co.in", "com.sg", "com.hk"}
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?!-)[a-z0-9_-]{1,63}(?:\.(?!-)[a-z0-9_-]{1,63})+$")

PERIOD_SECONDS = {"live": 600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}
# stapelbredd i grafen per period
PERIOD_STEP = {"live": 60, "24h": 600, "7d": 3600, "30d": 86400}
PERIOD_TTL = {"live": 4, "24h": 20, "7d": 300, "30d": 600}


class PiHoleError(Exception):
    pass


def base_domain(domain: str) -> str:
    """Den registrerbara domänen: telemetry.roborock.com → roborock.com."""
    parts = domain.lower().strip(".").split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in TWO_PART_TLDS:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def subdomain_regex(domain: str) -> str:
    return r"(\.|^)" + re.escape(base_domain(domain)) + "$"


def rule_label(rule: str) -> str:
    """(\\.|^)roborock\\.com$ → *.roborock.com, annars oförändrad."""
    m = re.fullmatch(r"\(\\\.\|\^\)(.+)\$", rule)
    return "*." + m.group(1).replace("\\.", ".") if m else rule


def clean_domain(raw: str) -> str:
    d = (raw or "").strip().lower()
    d = re.sub(r"^[a-z]+://", "", d).split("/")[0].split(":")[0].strip(".")
    if not DOMAIN_RE.match(d):
        raise PiHoleError(f"”{raw}” är ingen giltig domän.")
    return d


def classify(q: dict) -> tuple[bool, str]:
    """(blockerad, beskrivning) för en fråga i loggen."""
    st = str(q.get("status") or "UNKNOWN").upper()
    if st in BLOCKED_STATUS:
        return True, BLOCKED_STATUS[st]
    if q.get("list_id") is not None and st in ALLOWED_STATUS:
        return False, "egen tillåtlista"
    return False, ALLOWED_STATUS.get(st, st.lower())


def parse_query(q: dict) -> dict:
    blocked, why = classify(q)
    client = q.get("client") or {}
    reply = q.get("reply") or {}
    rt = reply.get("time")
    return {
        "id": q.get("id"), "time": q.get("time"), "domain": q.get("domain") or "",
        "type": q.get("type") or "", "ip": client.get("ip") or "", "client_name": client.get("name") or None,
        "blocked": blocked, "why": why, "cached": str(q.get("status") or "").upper() in CACHED,
        "reply_ms": rt if isinstance(rt, (int, float)) and rt >= 0 else None,
    }


def _version_text(info: dict) -> tuple[str | None, str | None]:
    """(installerad version, ny version om det finns en uppdatering)."""
    v = info.get("version") or info
    local = remote = None
    update = None
    for comp in ("docker", "core", "ftl", "web"):
        c = v.get(comp) or {}
        loc = (c.get("local") or {}) if isinstance(c.get("local"), dict) else {"version": c.get("local")}
        rem = (c.get("remote") or {}) if isinstance(c.get("remote"), dict) else {"version": c.get("remote")}
        lv, rv = loc.get("version"), rem.get("version")
        if comp == "docker" and not lv:
            continue
        if local is None and lv:
            local, remote = lv, rv
        if lv and rv and str(lv).lstrip("v") != str(rv).lstrip("v") and update is None:
            update = f"{'Docker ' if comp == 'docker' else ''}{rv}"
    return local, update


class PiHole:
    def __init__(self, name: str, url: str, password: str, verify: bool) -> None:
        self.name = name
        self.url = url
        self.password = password
        self.client = httpx.AsyncClient(base_url=url, verify=verify, timeout=15)
        self.sid: str | None = None
        self._auth_lock = asyncio.Lock()
        self._auth_failed_at = 0.0
        self.status: dict[str, Any] = {"name": name, "enabled": True, "ok": False, "error": None, "last": None}
        self.summary: dict[str, Any] = {}
        self.blocking: dict[str, Any] = {"state": "unknown", "timer": None, "at": None}
        self.version: dict[str, Any] = {"local": None, "update": None}
        self._version_at = 0.0
        self.rules: list[dict] = []
        self._rules_at = 0.0
        self._period_cache: dict[str, tuple[float, dict]] = {}

    # ── transport ──
    async def _api(self, method: str, path: str, params: dict | None = None, json: Any = None) -> tuple[int, Any]:
        headers = {"X-FTL-SID": self.sid} if self.sid else {}
        r = await self.client.request(method, "/api" + path, params=params, json=json, headers=headers)
        try:
            body = r.json() if r.content else None
        except ValueError:
            body = None
        return r.status_code, body

    async def _login(self) -> None:
        if time.time() - self._auth_failed_at < 60:
            raise PiHoleError("Pi-hole nekade inloggningen. Kontrollera PIHOLE_PASSWORD (app-lösenord).")
        code, body = await self._api("POST", "/auth", json={"password": self.password})
        sess = (body or {}).get("session") or {}
        if code >= 400 or not sess.get("valid"):
            self._auth_failed_at = time.time()
            self.sid = None
            msg = ((body or {}).get("error") or {}).get("message") or sess.get("message") or f"svar {code}"
            raise PiHoleError(f"Pi-hole nekade inloggningen ({msg}). Kontrollera PIHOLE_PASSWORD (app-lösenord).")
        self.sid = sess.get("sid")

    async def call(self, method: str, path: str, params: dict | None = None, json: Any = None) -> Any:
        """Anropa API:t, och logga in (igen) vid behov."""
        try:
            code, body = await self._api(method, path, params, json)
            if code == 401:
                async with self._auth_lock:
                    await self._login()
                code, body = await self._api(method, path, params, json)
        except httpx.HTTPError as exc:
            raise PiHoleError(f"Kan inte nå {self.name} ({exc.__class__.__name__}). Kontrollera adressen {self.url}.") from exc
        if code == 401:
            raise PiHoleError("Pi-hole nekade åtkomst. Kontrollera PIHOLE_PASSWORD (app-lösenord).")
        if code == 403:
            raise PiHoleError("Pi-hole tillåter inte ändringen med app-lösenordet. Slå på webserver.api.app_sudo i Pi-hole.")
        if code >= 400:
            msg = ((body or {}).get("error") or {}).get("message") if isinstance(body, dict) else None
            raise PiHoleError(f"{self.name} svarade {code}{': ' + msg if msg else ''}.")
        return body if body is not None else {}

    async def close(self) -> None:
        if self.sid:
            try:
                await self._api("DELETE", "/auth")
            except Exception:  # noqa: BLE001 – stängs ändå
                pass
        await self.client.aclose()

    # ── läsning ──
    async def poll(self) -> None:
        now = time.time()
        try:
            self.summary = await self.call("GET", "/stats/summary")
            self._set_blocking(await self.call("GET", "/dns/blocking"))
            if now - self._version_at > 3600:
                self._version_at = now
                try:
                    local, update = _version_text(await self.call("GET", "/info/version"))
                    self.version = {"local": local, "update": update}
                except PiHoleError as exc:
                    log.info("Pi-hole-version: %s", exc)
            if now - self._rules_at > 60:
                await self.load_rules()
        except PiHoleError as exc:
            self.status.update(ok=False, error=str(exc))
            log.warning("%s", exc)
            return
        self.status.update(ok=True, error=None, last=now)

    def _set_blocking(self, b: dict) -> None:
        self.blocking = {"state": b.get("blocking") or "unknown", "timer": b.get("timer"), "at": time.time()}

    async def load_rules(self) -> None:
        data = await self.call("GET", "/domains")
        self._rules_at = time.time()
        self.rules = sorted(
            ({"domain": d.get("domain") or "", "type": d.get("type"), "kind": d.get("kind"),
              "comment": d.get("comment") or "", "enabled": bool(d.get("enabled", True)),
              "added": d.get("date_added")} for d in (data.get("domains") or [])),
            key=lambda d: d["added"] or 0, reverse=True)

    async def queries(self, length: int, since: float | None = None) -> list[dict]:
        params: dict[str, Any] = {"length": length}
        if since:
            params.update({"from": int(since), "until": int(time.time()) + 60})
        data = await self.call("GET", "/queries", params=params)
        out = [parse_query(q) for q in data.get("queries") or []]
        out.sort(key=lambda q: q["time"] or 0, reverse=True)
        return out

    async def period(self, period: str) -> dict:
        period = period if period in PERIOD_SECONDS else "24h"
        hit = self._period_cache.get(period)
        if hit and time.time() - hit[0] < PERIOD_TTL[period]:
            return hit[1]
        if period == "live":
            data = await self._period_live()
        elif period == "24h":
            data = await self._period_day()
        else:
            data = await self._period_db(PERIOD_SECONDS[period])
        data["period"] = period
        data["step"] = PERIOD_STEP[period]
        self._period_cache[period] = (time.time(), data)
        return data

    async def _period_live(self) -> dict:
        now = time.time()
        qs = await self.queries(10000, since=now - PERIOD_SECONDS["live"])
        step = PERIOD_STEP["live"]
        bars: dict[int, list[int]] = {int(now // step * step) - i * step: [0, 0] for i in range(10)}
        doms: dict[bool, dict[str, int]] = {True: defaultdict(int), False: defaultdict(int)}
        clients: dict[str, dict] = {}
        for q in qs:
            b = bars.get(int((q["time"] or 0) // step * step))
            if b is not None:
                b[1 if q["blocked"] else 0] += 1
            doms[q["blocked"]][q["domain"]] += 1
            c = clients.setdefault(q["ip"], {"ip": q["ip"], "name": q["client_name"], "total": 0, "blocked": 0})
            c["total"] += 1
            c["blocked"] += q["blocked"]
        total, blocked = len(qs), sum(q["blocked"] for q in qs)
        top = lambda d: [{"domain": k, "count": v} for k, v in sorted(d.items(), key=lambda kv: kv[1], reverse=True)[:10]]  # noqa: E731
        return {"total": total, "blocked": blocked, "cached": sum(q["cached"] for q in qs), "clients_count": len(clients),
                "history": [{"t": t, "allowed": a, "blocked": b} for t, (a, b) in sorted(bars.items())],
                "top_allowed": top(doms[False]), "top_blocked": top(doms[True]), "clients": list(clients.values())}

    async def _period_day(self) -> dict:
        hist, ta, tb, ca, cb = await asyncio.gather(
            self.call("GET", "/history"),
            self.call("GET", "/stats/top_domains", params={"count": 10}),
            self.call("GET", "/stats/top_domains", params={"count": 10, "blocked": "true"}),
            self.call("GET", "/stats/top_clients", params={"count": 500}),
            self.call("GET", "/stats/top_clients", params={"count": 500, "blocked": "true"}),
        )
        q = self.summary.get("queries") or {}
        out = self._compose(hist, ta, tb, ca, cb, PERIOD_STEP["24h"])
        if q.get("total") is not None:
            out.update(total=q.get("total") or 0, blocked=q.get("blocked") or 0, cached=q.get("cached") or 0)
        active = (self.summary.get("clients") or {}).get("active")
        if active is not None:
            out["clients_count"] = active
        return out

    async def _period_db(self, seconds: int) -> dict:
        now = int(time.time())
        rng = {"from": now - seconds, "until": now}
        hist, ta, tb, ca, cb = await asyncio.gather(
            self.call("GET", "/history/database", params=rng),
            self.call("GET", "/stats/database/top_domains", params={**rng, "count": 10}),
            self.call("GET", "/stats/database/top_domains", params={**rng, "count": 10, "blocked": "true"}),
            self.call("GET", "/stats/database/top_clients", params={**rng, "count": 500}),
            self.call("GET", "/stats/database/top_clients", params={**rng, "count": 500, "blocked": "true"}),
        )
        return self._compose(hist, ta, tb, ca, cb, PERIOD_STEP["7d" if seconds <= 7 * 86400 else "30d"])

    @staticmethod
    def _compose(hist: dict, ta: dict, tb: dict, ca: dict, cb: dict, step: int) -> dict:
        bars: dict[int, list[int]] = defaultdict(lambda: [0, 0])
        total = blocked = cached = 0
        for h in hist.get("history") or []:
            t, b = int(h.get("total") or 0), int(h.get("blocked") or 0)
            total += t
            blocked += b
            cached += int(h.get("cached") or 0)
            slot = bars[int((h.get("timestamp") or 0) // step * step)]
            slot[0] += t - b
            slot[1] += b
        clients: dict[str, dict] = {}
        for c in ca.get("clients") or []:
            clients[c.get("ip") or ""] = {"ip": c.get("ip") or "", "name": c.get("name") or None,
                                          "total": int(c.get("count") or 0), "blocked": 0}
        for c in cb.get("clients") or []:
            e = clients.setdefault(c.get("ip") or "", {"ip": c.get("ip") or "", "name": c.get("name") or None,
                                                       "total": 0, "blocked": 0})
            e["blocked"] = int(c.get("count") or 0)
        dom = lambda d: [{"domain": x.get("domain") or "", "count": int(x.get("count") or 0)} for x in d.get("domains") or []]  # noqa: E731
        return {"total": total, "blocked": blocked, "cached": cached, "clients_count": len(clients),
                "history": [{"t": t, "allowed": a, "blocked": b} for t, (a, b) in sorted(bars.items())],
                "top_allowed": dom(ta), "top_blocked": dom(tb), "clients": list(clients.values())}

    # ── styrning ──
    async def set_blocking(self, enabled: bool, seconds: int | None = None) -> dict:
        body = await self.call("POST", "/dns/blocking", json={"blocking": enabled, "timer": seconds if seconds else None})
        self._set_blocking(body)
        self._period_cache.clear()
        return self.blocking

    async def change_domain(self, action: str, domain: str, scope: str = "exact") -> str:
        """Blockera eller avblockera en domän. Returnerar en beskrivning av vad som gjordes."""
        domain = clean_domain(domain)
        await self.load_rules()
        want, other = ("deny", "allow") if action == "block" else ("allow", "deny")
        done = []
        # ta bort en motsatt regel för exakt samma domän (t.ex. egen blockering när man avblockerar)
        for r in [r for r in self.rules if r["type"] == other and r["kind"] == "exact" and r["domain"] == domain]:
            await self.call("DELETE", f"/domains/{other}/exact/{quote(domain, safe='')}")
            done.append(f"tog bort {domain} från {'blocklistan' if other == 'deny' else 'tillåtlistan'}")
        removed_deny = action == "unblock" and done
        if scope == "sub":
            value, kind = subdomain_regex(domain), "regex"
        else:
            value, kind = domain, "exact"
        if not removed_deny or scope == "sub":
            exists = any(r["type"] == want and r["kind"] == kind and r["domain"] == value for r in self.rules)
            if not exists:
                await self.call("POST", f"/domains/{want}/{kind}",
                                json={"domain": value, "comment": "Från nätverksdashboarden", "groups": [0], "enabled": True})
            what = f"*.{base_domain(domain)}" if scope == "sub" else domain
            done.append(f"lade till {what} i {'blocklistan' if want == 'deny' else 'tillåtlistan'}")
        await self.load_rules()
        self._period_cache.clear()
        return "; ".join(done)

    async def remove_rule(self, rtype: str, kind: str, domain: str) -> None:
        if rtype not in ("allow", "deny") or kind not in ("exact", "regex"):
            raise PiHoleError("Okänd regel.")
        await self.call("DELETE", f"/domains/{rtype}/{kind}/{quote(domain, safe='')}")
        await self.load_rules()
        self._period_cache.clear()

    # ── sammanfattning för sidan ──
    def brief(self) -> dict:
        q = self.summary.get("queries") or {}
        g = self.summary.get("gravity") or {}
        timer = self.blocking.get("timer")
        left = None
        if timer and self.blocking.get("at"):
            left = max(0, round(timer - (time.time() - self.blocking["at"])))
        return {**self.status, "blocking": self.blocking.get("state"), "paused_left": left,
                "percent_blocked": q.get("percent_blocked"), "total_24h": q.get("total"),
                "gravity_domains": g.get("domains_being_blocked"), "gravity_updated": g.get("last_update"),
                "version": self.version.get("local"), "update": self.version.get("update")}

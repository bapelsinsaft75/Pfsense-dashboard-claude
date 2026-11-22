"""Webbserver och insamlingsloopar."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time

from datetime import datetime
from zoneinfo import ZoneInfo

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import demo
from .config import Config
from .engine import Engine
from .netflow import NetflowProtocol
from .pfsense import PfSense, PfSenseError
from .pihole import PiHoleError, clean_domain, rule_label
from .syslog import SyslogProtocol
from .unifi import UniFi, UniFiError

STOCKHOLM = ZoneInfo("Europe/Stockholm")


class _StockholmFormatter(logging.Formatter):
    """Loggens tidsstämplar i svensk tid och 24-timmarsformat, oavsett containerns TZ."""

    def formatTime(self, record, datefmt=None):  # noqa: N802
        return datetime.fromtimestamp(record.created, STOCKHOLM).strftime(datefmt or "%Y-%m-%d %H:%M:%S")


_handler = logging.StreamHandler()
_handler.setFormatter(_StockholmFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(), handlers=[_handler])
log = logging.getLogger("main")
STATIC = os.path.join(os.path.dirname(__file__), "static")


# ── en hämtning per källa: används både av looparna och av knappen ”Uppdatera status” ──
async def pfsense_once(engine: Engine, pf: PfSense, clients: bool = True) -> None:
    try:
        if clients:
            leases, arp = await asyncio.gather(pf.leases(), pf.arp())
            engine.update_clients(leases, arp)
        engine.update_interfaces(await pf.interfaces())
        engine.update_states(await pf.states())
        engine.mark("pfsense", True)
    except PfSenseError as exc:
        engine.mark("pfsense", False, str(exc))
        log.warning("%s", exc)
    except Exception as exc:  # noqa: BLE001
        engine.mark("pfsense", False, f"Oväntat fel: {exc}")
        log.exception("pfSense-insamling misslyckades")


_openvpn = {"supported": True}


async def vpn_once(engine: Engine, pf: PfSense) -> None:
    """Gateways (inklusive VPN-gateways), systemstatus och OpenVPN-klienter."""
    try:
        engine.update_gateways(await pf.gateways())
    except PfSenseError as exc:
        log.debug("Gateways: %s", exc)
    except Exception:  # noqa: BLE001
        log.exception("Kunde inte läsa gateways")
    try:
        engine.update_system(await pf.system())
    except Exception as exc:  # noqa: BLE001 – bara för Överblick
        log.debug("Systemstatus: %s", exc)
    if _openvpn["supported"]:
        try:
            engine.update_openvpn(await pf.openvpn_clients())
        except PfSenseError as exc:
            if "404" in str(exc):
                _openvpn["supported"] = False
        except Exception:  # noqa: BLE001
            log.exception("Kunde inte läsa OpenVPN-status")


async def unraid_once(engine: Engine) -> None:
    try:
        await asyncio.gather(*(u.poll() for u in engine.unraids), return_exceptions=True)
        for u in engine.unraids:
            engine.check_unraid(u)
    except Exception:  # noqa: BLE001
        log.exception("Unraid-insamling misslyckades")


async def plex_once(engine: Engine) -> None:
    await asyncio.gather(*(s.poll() for s in engine.plex_servers), return_exceptions=True)
    for s in engine.plex_servers:
        try:
            engine.check_plex(s)
        except Exception:  # noqa: BLE001
            log.exception("Plex-kontroll misslyckades")


async def pihole_once(engine: Engine) -> None:
    await engine.pihole.poll()
    try:
        engine.check_pihole()
    except Exception:  # noqa: BLE001
        log.exception("Pi-hole-kontroll misslyckades")


async def unifi_once(engine: Engine, uf: UniFi) -> None:
    try:
        sta, dev = await uf.fetch()
        engine.update_unifi(sta, dev)
        engine.mark("unifi", True)
    except UniFiError as exc:
        engine.mark("unifi", False, str(exc))
        log.warning("%s", exc)
    except Exception as exc:  # noqa: BLE001
        engine.mark("unifi", False, f"Oväntat fel: {exc}")
        log.exception("UniFi-insamling misslyckades")


async def pfsense_loop(engine: Engine, pf: PfSense) -> None:
    last_clients = 0.0
    while True:
        due = time.time() - last_clients >= engine.cfg.pfsense_client_poll
        await pfsense_once(engine, pf, clients=due)
        if due:
            last_clients = time.time()
        await asyncio.sleep(engine.cfg.pfsense_poll)


async def vpn_loop(engine: Engine, pf: PfSense) -> None:
    while True:
        await vpn_once(engine, pf)
        await asyncio.sleep(min(engine.cfg.vpn_poll, engine.settings.get("confirm_interval")))


async def unraid_loop(engine: Engine) -> None:
    while True:
        await unraid_once(engine)
        await asyncio.sleep(min(engine.cfg.unraid_poll, engine.settings.get("confirm_interval")))


async def plex_loop(engine: Engine) -> None:
    while True:
        await plex_once(engine)
        await asyncio.sleep(engine.cfg.plex_poll)


async def pihole_loop(engine: Engine) -> None:
    while True:
        await pihole_once(engine)
        await asyncio.sleep(min(engine.cfg.pihole_poll, engine.settings.get("confirm_interval")))


async def unifi_loop(engine: Engine, uf: UniFi) -> None:
    while True:
        await unifi_once(engine, uf)
        await asyncio.sleep(engine.cfg.unifi_poll)


async def ticker(engine: Engine) -> None:
    last_flush = last_compact = last_check = time.time()
    last_anomaly = time.time() - 3000  # första kontrollen efter cirka 10 minuter
    while True:
        await asyncio.sleep(2)
        if engine.cfg.demo:
            demo.tick(engine)
        else:
            engine.tick()
        now = time.time()
        if not engine.cfg.demo and now - last_check >= 30:
            engine.check_sources(now)
            last_check = now
        engine.alerts.flush_quiet_digest()
        if not engine.cfg.demo and now - last_anomaly >= 3600:
            await asyncio.to_thread(engine.check_data_anomalies, now)
            last_anomaly = now
        if now - last_flush >= 60:
            await asyncio.to_thread(engine.history.flush)
            last_flush = now
        if now - last_compact >= 3600:
            await asyncio.to_thread(engine.history.compact, engine.settings.get("retention_days"))
            last_compact = now


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = Config()
    engine = Engine(cfg)
    app.state.engine = engine
    tasks: list[asyncio.Task] = []
    closers = []
    transport = None
    if cfg.demo:
        log.info("Startar i demoläge med exempeldata (PFSENSE_URL är inte satt).")
        demo.setup(engine)
        if engine.pihole:
            tasks.append(asyncio.create_task(pihole_loop(engine)))
    else:
        await engine.geo.ensure()
        tasks.append(asyncio.create_task(engine.geo.refresh_loop()))
        pf = PfSense(cfg)
        app.state.pf = pf
        closers.append(pf.close)
        tasks.append(asyncio.create_task(pfsense_loop(engine, pf)))
        tasks.append(asyncio.create_task(vpn_loop(engine, pf)))
        if cfg.unifi_enabled:
            uf = UniFi(cfg)
            app.state.uf = uf
            closers.append(uf.close)
            tasks.append(asyncio.create_task(unifi_loop(engine, uf)))
        if engine.unraids:
            tasks.append(asyncio.create_task(unraid_loop(engine)))
            closers.extend(u.close for u in engine.unraids)
        if engine.pihole:
            tasks.append(asyncio.create_task(pihole_loop(engine)))
            closers.append(engine.pihole.close)
        if engine.plex_servers:
            tasks.append(asyncio.create_task(plex_loop(engine)))
            closers.extend(s.close for s in engine.plex_servers)
        if cfg.netflow_port > 0:
            loop = asyncio.get_running_loop()
            try:
                transport, _ = await loop.create_datagram_endpoint(
                    lambda: NetflowProtocol(engine.nf_parser, engine.on_flows), local_addr=("0.0.0.0", cfg.netflow_port)
                )
                log.info("Tar emot NetFlow/IPFIX på UDP %s", cfg.netflow_port)
            except OSError as exc:
                engine.mark("netflow", False, f"Kan inte lyssna på UDP {cfg.netflow_port}: {exc}")
    syslog_transport = None
    if cfg.syslog_port > 0 and not cfg.demo:
        try:
            syslog_transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
                lambda: SyslogProtocol(engine.on_syslog), local_addr=("0.0.0.0", cfg.syslog_port)
            )
            log.info("Tar emot syslog på UDP %s", cfg.syslog_port)
        except OSError as exc:
            engine.mark("syslog", False, f"Kan inte lyssna på UDP {cfg.syslog_port}: {exc}")
    tasks.append(asyncio.create_task(ticker(engine)))
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        if transport:
            transport.close()
        if syslog_transport:
            syslog_transport.close()
        for close in closers:
            await close()
        engine.history.flush()


app = FastAPI(title="pfSense Nätverkskarta", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.get("/api/summary")
async def summary(period: str = Query("24h", pattern="^(24h|7d|30d)$")):
    engine: Engine = app.state.engine
    return await asyncio.to_thread(engine.summary, period)


PTR_CACHE: dict[str, tuple[str | None, float]] = {}


async def reverse_dns(ips: list[str], limit: int = 60, timeout: float = 2.5) -> dict[str, str | None]:
    """Omvända DNS-uppslag med cache. Uppslag som inte hinner klart visas som tomma."""
    import socket
    now = time.time()
    out, todo = {}, []
    for ip in ips:
        hit = PTR_CACHE.get(ip)
        if hit and now - hit[1] < 86400:
            out[ip] = hit[0]
        elif len(todo) < limit:
            todo.append(ip)

    async def one(ip: str) -> None:
        try:
            name = (await asyncio.wait_for(asyncio.to_thread(socket.gethostbyaddr, ip), timeout))[0]
        except (OSError, asyncio.TimeoutError, UnicodeError):
            name = None
        PTR_CACHE[ip] = (name, time.time())
        out[ip] = name

    if todo:
        await asyncio.gather(*(one(ip) for ip in todo))
    if len(PTR_CACHE) > 20000:
        PTR_CACHE.clear()
    return out


@app.get("/api/country/{cc}")
async def country(cc: str, period: str = Query("24h", pattern="^(24h|7d|30d)$")):
    engine: Engine = app.state.engine
    cc = cc.upper()
    if len(cc) != 2 or not cc.isalpha():
        raise HTTPException(400, "Ogiltig landskod")
    detail = await asyncio.to_thread(engine.country_detail, cc, period)
    if engine.cfg.reverse_dns and not engine.cfg.demo:
        ptr = await reverse_dns([r["ip"] for r in detail["remotes"]])
        for r in detail["remotes"]:
            r["host"] = ptr.get(r["ip"])
    return detail


THUMBS: dict[tuple[int, str], tuple[bytes, str, float]] = {}


@app.get("/api/plex/thumb/{server}")
async def plex_thumb(server: int, path: str = Query(..., max_length=300)):
    engine: Engine = app.state.engine
    if not path.startswith("/library/") or server < 0 or server >= len(engine.plex_servers):
        raise HTTPException(404)
    key = (server, path)
    hit = THUMBS.get(key)
    if not hit or time.time() - hit[2] > 3600:
        got = await engine.plex_servers[server].thumb(path)
        if not got:
            raise HTTPException(404)
        if len(THUMBS) > 300:
            THUMBS.clear()
        hit = THUMBS[key] = (got[0], got[1], time.time())
    return Response(hit[0], media_type=hit[1], headers={"Cache-Control": "max-age=3600"})


def _pihole():
    engine: Engine = app.state.engine
    if not engine.pihole:
        raise HTTPException(404, "Pi-hole är inte konfigurerat (PIHOLE_URL).")
    return engine, engine.pihole


@app.get("/api/pihole")
async def pihole_stats(period: str = Query("24h", pattern="^(live|24h|7d|30d)$")):
    engine, ph = _pihole()
    try:
        data = dict(await ph.period(period))
    except PiHoleError as exc:
        return {"brief": ph.brief(), "error": str(exc), "rules": ph.rules}
    data["clients"] = engine.pihole_named([dict(c) for c in data["clients"]])
    data["clients"].sort(key=lambda c: c["total"], reverse=True)
    return {"brief": ph.brief(), "data": data, "rules": ph.rules}


@app.get("/api/pihole/queries")
async def pihole_queries(n: int = Query(10, ge=1, le=50), show: str = Query("all", pattern="^(all|allowed|blocked)$")):
    engine, ph = _pihole()
    try:
        qs = await ph.queries(n if show == "all" else 1000)
    except PiHoleError as exc:
        raise HTTPException(502, str(exc)) from exc
    if show != "all":
        qs = [q for q in qs if q["blocked"] == (show == "blocked")]
    return {"queries": engine.pihole_named(qs[:n])}


@app.post("/api/pihole/blocking")
async def pihole_blocking(body: dict = Body(...)):
    engine, ph = _pihole()
    enabled = bool(body.get("enabled"))
    minutes = body.get("minutes")
    seconds = None
    if not enabled:
        try:
            seconds = int(round(float(minutes) * 60))
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "Ange hur många minuter blockeringen ska pausas.") from exc
        if not 1 <= seconds <= 7 * 86400:
            raise HTTPException(400, "Pausen måste vara mellan 1 minut och 7 dagar.")
    try:
        state = await ph.set_blocking(enabled, seconds)
    except PiHoleError as exc:
        raise HTTPException(502, str(exc)) from exc
    m = seconds // 60 if seconds else 0
    title = "Pi-hole: blockeringen påslagen igen" if enabled else f"Pi-hole: blockeringen pausad i {m} min"
    engine.alerts.note("pihole_change", title, "Ändrat från dashboarden.")
    return {"brief": ph.brief(), "blocking": state}


@app.post("/api/pihole/domain")
async def pihole_domain(body: dict = Body(...)):
    engine, ph = _pihole()
    action = body.get("action")
    scope = body.get("scope") or "exact"
    if action not in ("block", "unblock") or scope not in ("exact", "sub"):
        raise HTTPException(400, "Okänd åtgärd.")
    try:
        domain = clean_domain(str(body.get("domain") or ""))
    except PiHoleError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        what = await ph.change_domain(action, domain, scope)
    except PiHoleError as exc:
        raise HTTPException(502, str(exc)) from exc
    verb = "blockerade" if action == "block" else "avblockerade"
    engine.alerts.note("pihole_change", f"Pi-hole: {verb} {domain}", f"Från dashboarden: {what}.")
    return {"done": what, "rules": ph.rules}


@app.post("/api/pihole/rule/delete")
async def pihole_rule_delete(body: dict = Body(...)):
    engine, ph = _pihole()
    try:
        await ph.remove_rule(str(body.get("type")), str(body.get("kind")), str(body.get("domain") or ""))
    except PiHoleError as exc:
        raise HTTPException(502, str(exc)) from exc
    lst = "tillåtlistan" if body.get("type") == "allow" else "blocklistan"
    engine.alerts.note("pihole_change", f"Pi-hole: {rule_label(str(body.get('domain') or ''))} borttagen ur {lst}", "Ändrat från dashboarden.")
    return {"rules": ph.rules}


@app.post("/api/plex/{server}/refresh")
async def plex_refresh(server: int, body: dict = Body(default={})):
    engine: Engine = app.state.engine
    if server < 0 or server >= len(engine.plex_servers):
        raise HTTPException(404, "Okänd Plex-server.")
    p = engine.plex_servers[server]
    key = body.get("section")
    key = str(key) if key not in (None, "") else None
    if key and not any(x["key"] == key for x in p.libraries):
        raise HTTPException(404, "Okänt bibliotek.")
    try:
        await p.refresh(key)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    what = next((x["title"] for x in p.libraries if x["key"] == key), None) if key else "alla bibliotek"
    engine.alerts.note("plex_refresh", f"Plex: uppdaterar {what} på {p.name}", "Startat från dashboarden.")
    return {"libraries": p.libraries}


_refresh_lock = asyncio.Lock()


@app.post("/api/refresh")
async def refresh_all():
    """Knappen ”Uppdatera status”: hämta aktuell status från alla källor direkt, i stället för att vänta."""
    engine: Engine = app.state.engine
    if _refresh_lock.locked():
        raise HTTPException(409, "En uppdatering pågår redan.")
    async with _refresh_lock:
        started = time.time()
        jobs, names = [], []
        pf = getattr(app.state, "pf", None)
        uf = getattr(app.state, "uf", None)
        if pf:
            jobs += [pfsense_once(engine, pf), vpn_once(engine, pf)]
            names += ["pfSense", "gateways och VPN"]
        if uf:
            jobs.append(unifi_once(engine, uf)); names.append("UniFi")
        if engine.unraids and not engine.cfg.demo:
            jobs.append(unraid_once(engine)); names.append("Unraid")
        if engine.plex_servers and not engine.cfg.demo:
            jobs.append(plex_once(engine)); names.append("Plex")
        if engine.pihole:
            engine.pihole._version_at = engine.pihole._rules_at = 0
            engine.pihole._period_cache.clear()
            jobs.append(pihole_once(engine)); names.append("Pi-hole")
        results = await asyncio.wait_for(asyncio.gather(*jobs, return_exceptions=True), timeout=40) if jobs else []
        failed = [n for n, r in zip(names, results) if isinstance(r, Exception)]
        if engine.cfg.demo:
            demo.tick(engine)
        return {"sources": names, "failed": failed, "seconds": round(time.time() - started, 1)}


@app.get("/api/profiles")
async def get_profiles():
    engine: Engine = app.state.engine
    return {"profiles": engine.settings.get("profiles")}


@app.put("/api/profiles")
async def put_profiles(body: dict = Body(...)):
    engine: Engine = app.state.engine
    try:
        engine.settings.update({"profiles": body.get("profiles") or []})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"profiles": engine.settings.get("profiles")}


@app.get("/api/overview")
async def overview():
    from .overview import build
    engine: Engine = app.state.engine
    return await asyncio.to_thread(build, engine)


@app.get("/api/top")
async def top(window: str = Query("5m", pattern="^(5m|15m|60m|24h)$"), limit: int = Query(10, ge=1, le=100),
              scope: str = Query("internet", pattern="^(internet|internal|all)$")):
    engine: Engine = app.state.engine
    return await asyncio.to_thread(engine.top, window, limit, scope)


@app.get("/api/flows")
async def flows(window: str = Query("24h", pattern="^(live|60m|24h|7d|30d)$")):
    engine: Engine = app.state.engine
    return await asyncio.to_thread(engine.map_flows, window)


@app.get("/api/series")
async def series(window: str = Query("60m", pattern="^(5m|15m|60m|24h)$"),
                 scope: str = Query("internet", pattern="^(internet|internal|all)$")):
    engine: Engine = app.state.engine
    return await asyncio.to_thread(engine.chart_series, window, scope)


@app.get("/api/health")
async def health():
    engine: Engine = app.state.engine
    return {"ok": True, "mode": "demo" if engine.cfg.demo else "live", "status": engine.status}


@app.post("/api/alerts/{alert_id}/ack")
async def ack_alert(alert_id: int):
    engine: Engine = app.state.engine
    return {"ok": engine.alerts.ack(alert_id)}


@app.post("/api/alerts/ack-all")
async def ack_all():
    engine: Engine = app.state.engine
    return {"ok": True, "count": engine.alerts.ack_all()}


@app.put("/api/clients/{cid}/name")
async def rename_client(cid: str, body: dict = Body(...)):
    engine: Engine = app.state.engine
    name = body.get("name")
    if name is not None and not isinstance(name, str):
        raise HTTPException(400, "Namnet måste vara text")
    await asyncio.to_thread(engine.set_alias, cid, name)
    return {"id": cid, "name": engine.display_name(cid)}


@app.get("/api/settings")
async def get_settings():
    engine: Engine = app.state.engine
    return {"settings": engine.settings.all(), "email": {"enabled": engine.mailer.configured, "to": engine.mailer.to,
                                                         "host": engine.mailer.host},
            "slack": {"enabled": engine.slack.enabled},
            "servers": {"plex": [p.name for p in engine.plex_servers],
                        "pihole": [engine.pihole.name] if engine.pihole else [],
                        "unraid": [u.status.get("name") or u.url for u in engine.unraids]},
            "matrix": engine.alert_matrix()}


@app.put("/api/settings")
async def put_settings(values: dict = Body(...)):
    engine: Engine = app.state.engine
    try:
        before = engine.settings.get("retention_days")
        result = engine.settings.update(values)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if result["retention_days"] < before:
        await asyncio.to_thread(engine.history.flush)
        await asyncio.to_thread(engine.history.compact, result["retention_days"])
    return {"settings": result}


@app.post("/api/test-email")
async def test_email(body: dict | None = Body(None)):
    engine: Engine = app.state.engine
    from .settings import EMAIL_RE
    to = [(body or {}).get("to")] if (body or {}).get("to") else None
    if to and not EMAIL_RE.fullmatch(str(to[0])):
        raise HTTPException(400, "Ogiltig e-postadress")
    if not to:
        rec = engine.settings.get("recipients")
        to = [r["email"] for r in rec] if rec else engine.mailer.to
    try:
        await asyncio.to_thread(
            engine.mailer.send, "Testmejl",
            "Det här är ett testmejl från din pfSense-dashboard. Om du läser det fungerar e-postlarmen.\n", to)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "to": to}


@app.put("/api/unraid/watch")
async def set_watch(body: dict = Body(...)):
    engine: Engine = app.state.engine
    try:
        watch = engine.settings.set_watch(str(body.get("server")), str(body.get("kind")), str(body.get("name")),
                                          bool(body.get("on")))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"watch": watch}


@app.get("/api/alert-kinds")
async def alert_kinds():
    from .settings import ALERT_KINDS
    from .settings import CRITICAL_KINDS
    return [{"id": k, "label": v[0], "default": v[1], "confirmed": v[2], "critical": k in CRITICAL_KINDS}
            for k, v in ALERT_KINDS.items()]


@app.post("/api/test-slack")
async def test_slack():
    engine: Engine = app.state.engine
    try:
        await asyncio.to_thread(
            engine.slack.send, "info", "Testmeddelande från pfSense-dashboarden",
            "Om du ser det här fungerar larmen till Slack.", datetime.now(STOCKHOLM).strftime("%Y-%m-%d %H:%M"))
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@app.get("/api/debug/unraid")
async def debug_unraid():
    engine: Engine = app.state.engine
    if not engine.cfg.debug_endpoints:
        raise HTTPException(404)
    if not engine.unraids:
        return {"configured": False, "hint": "Fyll i UNRAID_URL och UNRAID_API_KEY (och UNRAID2_... för fler)."}
    from .unraid import QUERIES
    return {"configured": True, "servers": [
        {"name": u.status["name"], "url": u.url, "status": u.status,
         "chosen_query": {k: QUERIES[k][i] for k, i in u.chosen.items()}, "errors": u.errors, "raw": u.raw}
        for u in engine.unraids]}


@app.get("/api/debug/netflow")
async def debug_netflow():
    engine: Engine = app.state.engine
    if not engine.cfg.debug_endpoints:
        raise HTTPException(404)
    return {"port": engine.cfg.netflow_port, "history_source": engine.history_source(),
            "lan_subnets": [str(n) for n in engine.cfg.lan_subnets], **engine.nf_parser.stats(),
            "lan_flows_last_minute": len(engine.nf_window)}


@app.get("/api/debug/raw/{name}")
async def debug_raw(name: str):
    engine: Engine = app.state.engine
    if not engine.cfg.debug_endpoints:
        raise HTTPException(404)
    valid = ("states", "arp", "leases", "interfaces", "gateways", "openvpn", "unifi_clients", "unifi_devices")
    name = {"state": "states", "interface": "interfaces", "lease": "leases", "gateway": "gateways"}.get(name, name)
    if name not in valid:
        raise HTTPException(404, f"Okänd källa '{name}'. Giltiga: {', '.join(valid)}")
    return JSONResponse({"name": name, "sample": engine.raw.get(name, [])})


def _version() -> str:
    """Versionen från bygget (git-commit), annars ändringstiden för webbsidans filer."""
    v = os.environ.get("APP_VERSION", "").strip()
    if v:
        return v[:12]
    newest = max(os.path.getmtime(os.path.join(STATIC, f)) for f in ("index.html", "app.js", "style.css", "layout.js"))
    return datetime.fromtimestamp(newest, STOCKHOLM).strftime("%Y%m%d-%H%M")


@app.get("/")
async def index():
    """Sidan med versionsnummer på CSS och JavaScript, så att webbläsaren hämtar nya filer efter en uppdatering."""
    v = _version()
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as fh:
        html = fh.read()
    html = (html.replace('href="style.css"', f'href="style.css?v={v}"')
                .replace('src="app.js"', f'src="app.js?v={v}"')
                .replace('src="layout.js"', f'src="layout.js?v={v}"')
                .replace("{{VERSION}}", v))
    return Response(html, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=STATIC), name="static")


def run() -> None:
    port = int(os.environ.get("WEB_PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning", proxy_headers=False)


if __name__ == "__main__":
    run()

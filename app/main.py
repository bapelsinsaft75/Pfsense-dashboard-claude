"""Webbserver och insamlingsloopar."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import demo
from .config import Config
from .engine import Engine
from .netflow import NetflowProtocol
from .pfsense import PfSense, PfSenseError
from .syslog import SyslogProtocol
from .unifi import UniFi, UniFiError

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")
STATIC = os.path.join(os.path.dirname(__file__), "static")


async def pfsense_loop(engine: Engine, pf: PfSense) -> None:
    cfg = engine.cfg
    last_clients = 0.0
    while True:
        try:
            if time.time() - last_clients >= cfg.pfsense_client_poll:
                leases, arp = await asyncio.gather(pf.leases(), pf.arp())
                engine.update_clients(leases, arp)
                last_clients = time.time()
            ifaces = await pf.interfaces()
            engine.update_interfaces(ifaces)
            states = await pf.states()
            engine.update_states(states)
            engine.mark("pfsense", True)
        except PfSenseError as exc:
            engine.mark("pfsense", False, str(exc))
            log.warning("%s", exc)
        except Exception as exc:  # noqa: BLE001
            engine.mark("pfsense", False, f"Oväntat fel: {exc}")
            log.exception("pfSense-insamling misslyckades")
        await asyncio.sleep(cfg.pfsense_poll)


async def vpn_loop(engine: Engine, pf: PfSense) -> None:
    """Gateways (inklusive VPN-gateways) och OpenVPN-klienter."""
    openvpn_supported = True
    while True:
        try:
            engine.update_gateways(await pf.gateways())
        except PfSenseError as exc:
            log.debug("Gateways: %s", exc)
        except Exception:  # noqa: BLE001
            log.exception("Kunde inte läsa gateways")
        if openvpn_supported:
            try:
                engine.update_openvpn(await pf.openvpn_clients())
            except PfSenseError as exc:
                if "404" in str(exc):
                    openvpn_supported = False
            except Exception:  # noqa: BLE001
                log.exception("Kunde inte läsa OpenVPN-status")
        await asyncio.sleep(engine.cfg.vpn_poll)


async def unifi_loop(engine: Engine, uf: UniFi) -> None:
    while True:
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
    else:
        await engine.geo.ensure()
        tasks.append(asyncio.create_task(engine.geo.refresh_loop()))
        pf = PfSense(cfg)
        closers.append(pf.close)
        tasks.append(asyncio.create_task(pfsense_loop(engine, pf)))
        tasks.append(asyncio.create_task(vpn_loop(engine, pf)))
        if cfg.unifi_enabled:
            uf = UniFi(cfg)
            closers.append(uf.close)
            tasks.append(asyncio.create_task(unifi_loop(engine, uf)))
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


@app.get("/api/settings")
async def get_settings():
    engine: Engine = app.state.engine
    return {"settings": engine.settings.all(), "email": {"enabled": engine.mailer.enabled, "to": engine.mailer.to,
                                                         "host": engine.mailer.host}}


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
async def test_email():
    engine: Engine = app.state.engine
    try:
        await asyncio.to_thread(
            engine.mailer.send, "Testmejl",
            "Det här är ett testmejl från din pfSense-dashboard. Om du läser det fungerar e-postlarmen.\n")
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "to": engine.mailer.to}


@app.get("/api/debug/raw/{name}")
async def debug_raw(name: str):
    engine: Engine = app.state.engine
    if not engine.cfg.debug_endpoints:
        raise HTTPException(404)
    if name not in ("states", "arp", "leases", "interfaces", "gateways", "openvpn", "unifi_clients", "unifi_devices"):
        raise HTTPException(404, "Okänd källa")
    return JSONResponse({"name": name, "sample": engine.raw.get(name, [])})


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC, "index.html"), headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=STATIC), name="static")


def run() -> None:
    port = int(os.environ.get("WEB_PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning", proxy_headers=False)


if __name__ == "__main__":
    run()

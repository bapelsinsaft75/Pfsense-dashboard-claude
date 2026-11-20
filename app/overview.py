"""Överblick: en sida som svarar på ”är något fel?” och ”var sitter flaskhalsen?”.

Varje värde jämförs med något – linjens kapacitet, ett normalvärde eller en gräns – så att
problem kan beskrivas i klartext med orsak, i stället för bara som en röd siffra.
"""
from __future__ import annotations

import statistics
import time
from typing import Any

WARN, ERR = 50, 85  # beläggning i procent: gul från 50 %, röd från 85 %


def _lvl(pct: float | None, warn: float = WARN, err: float = ERR) -> str:
    if pct is None:
        return "ok"
    return "err" if pct >= err else "warn" if pct >= warn else "ok"


def _mbit(bps: float | None) -> float | None:
    return None if bps is None else bps / 1e6


def _fmt(v: float | None, d: int = 0) -> str:
    if v is None:
        return "–"
    s = f"{v:,.{d}f}".replace(",", " ").replace(".", ",")
    return s


def _top(e, idx: int) -> tuple[str, float] | None:
    """Klienten som just nu använder mest nedladdning (idx 0) eller uppladdning (idx 1)."""
    best = max(e.rates.items(), key=lambda kv: kv[1][idx], default=None)
    if not best or best[1][idx] <= 0:
        return None
    return e.display_name(best[0]), best[1][idx]


def _latency(e) -> dict | None:
    gws = [g for g in e.gateways.values() if not g.get("vpn")]
    if not gws:
        return None
    g = gws[0]
    hist = list(e.gw_hist.get(g["name"], []))
    now = time.time()
    day = [h for h in hist if now - h[0] < 86400 and h[1] is not None]
    cur = day[-1] if day else None
    delays = sorted(h[1] for h in day)
    # ”normalt” = fördröjningen när linjen inte är belastad: 20:e percentilen senaste dygnet
    base = delays[int(len(delays) * .2)] if len(delays) >= 10 else None
    hour = [h for h in day if now - h[0] < 3600]
    step = max(1, len(hour) // 60)
    series = [{"t": h[0], "ms": h[1], "up": h[5]} for h in hour[::step]]
    return {"gateway": g["name"], "state": g["state"], "now": cur[1] if cur else None, "jitter": cur[2] if cur else None,
            "loss": cur[3] if cur else None, "base": base, "series": series,
            "median": statistics.median(delays) if delays else None}


def build(e) -> dict[str, Any]:
    s = e.settings
    cap_down, cap_up = s.get("wan_down_mbit") or 0, s.get("wan_up_mbit") or 0
    down_bps, up_bps = e.wan_rate if e.wan_rate else (None, None)
    down_pct = _mbit(down_bps) / cap_down * 100 if cap_down and down_bps is not None else None
    up_pct = _mbit(up_bps) / cap_up * 100 if cap_up and up_bps is not None else None
    lat = _latency(e)
    problems: list[dict] = []
    links: list[dict] = []

    def problem(level, area, title, value, unit, why, goto=None, action=None, pct=None, mark=None, normal=None):
        problems.append({"level": level, "area": area, "title": title, "value": value, "unit": unit, "why": why,
                         "goto": goto, "action": action, "pct": pct, "mark": mark, "normal": normal})

    # ── internet ──
    top_up, top_down = _top(e, 1), _top(e, 0)
    full_up = up_pct is not None and up_pct >= 90
    full_down = down_pct is not None and down_pct >= 90
    if lat and lat["state"] == "down":
        problem("err", "Internet", f"Internet: {lat['gateway']} är nere", "nere", "", "pfSense får inget svar från internetleverantören.", "sec-security", "Visa säkerhet")
    elif lat and lat["now"] is not None:
        base = lat["base"] or lat["median"] or lat["now"]
        high = lat["now"] > max(base * 3, base + 30)
        if lat["loss"] is not None and lat["loss"] >= 2:
            problem("err" if lat["loss"] >= 5 else "warn", "Internet", "Internet: paketförlust", _fmt(lat["loss"], 1), "%",
                    f"{_fmt(lat['loss'], 1)} % av pingarna till {lat['gateway']} försvinner. Normalt är det 0 %.", "sec-security", "Visa gateways",
                    pct=min(100, lat["loss"] * 10), mark=10)
        if high:
            why = "Fördröjningen är mycket högre än normalt."
            if full_up and top_up:
                why = f"Uppladdningen är full ({_fmt(_mbit(up_bps), 1)} av {cap_up} Mbit/s) – {top_up[0]} laddar upp {_fmt(_mbit(top_up[1]), 1)} Mbit/s. Det gör samtal och spel sega för alla."
            elif full_down and top_down:
                why = f"Nedladdningen är full ({_fmt(_mbit(down_bps))} av {cap_down} Mbit/s) – {top_down[0]} laddar ner {_fmt(_mbit(top_down[1]))} Mbit/s."
            problem("err" if lat["now"] > base + 100 else "warn", "Internet", "Internet: hög fördröjning", _fmt(lat["now"]), "ms", why,
                    "sec-chart", "Visa trafiken", pct=min(100, lat["now"] / max(base * 6, 60) * 100), mark=min(100, base / max(base * 6, 60) * 100),
                    normal=f"normalt {_fmt(base)} ms")
    for pct, name, bps, cap, top, arrow in ((up_pct, "uppladdningen", up_bps, cap_up, top_up, "↑"), (down_pct, "nedladdningen", down_bps, cap_down, top_down, "↓")):
        if pct is not None and pct >= 90 and not any(p["area"] == "Internet" and "fördröjning" in p["title"] for p in problems):
            problem("warn", "Internet", f"Internet: {name} är nästan full", _fmt(pct), "%",
                    f"{_fmt(_mbit(bps), 1)} av {cap} Mbit/s används{f' – mest av {top[0]} ({_fmt(_mbit(top[1]), 1)} Mbit/s)' if top else ''}.",
                    "sec-chart", "Visa trafiken", pct=pct, mark=WARN)
    # ── VPN ──
    vpn = e.status.get("vpn") or {}
    if vpn.get("enabled") and not vpn.get("ok"):
        problem("err", "VPN", f"{vpn.get('label') or 'VPN'} är nere", "nere", "", vpn.get("detail") or vpn.get("error") or "VPN-anslutningen är inte uppe.", "sec-security", "Visa VPN")
    # ── pfSense ──
    ps = e.pf_system or {}
    pf = e.status.get("pfsense") or {}
    if pf.get("enabled") and not pf.get("ok") and pf.get("error"):
        problem("err", "pfSense", "pfSense svarar inte", "", "", pf["error"])
    if ps.get("cpu") is not None and ps["cpu"] >= 85:
        problem("warn", "pfSense", "pfSense: hög processorlast", _fmt(ps["cpu"]), "%", "Brandväggen kan bli en flaskhals när processorn är så belastad.", pct=ps["cpu"], mark=WARN)
    # ── UniFi ──
    devs = e.devices
    weak = [u for u in e.unifi_clients.values() if u.get("kind") == "wifi" and u.get("rssi") is not None and u["rssi"] < -75]
    for d in devs.values():
        if not d["online"]:
            problem("err", "UniFi", f"{d['name']} är offline", "offline", "", f"{d['model']} svarar inte. Klienter via den saknar nätverk.", "unifi-card", "Visa UniFi")
            continue
        for r in d.get("radios") or []:
            if r.get("util") is not None and r["util"] >= WARN:
                w = [u for u in weak if u.get("dev") == d["id"]]
                problem("err" if r["util"] >= ERR else "warn", "Wi-Fi", f"Wi-Fi: {d['name']} {r['band'] or ''} är trång".replace("  ", " "), _fmt(r["util"]), "%",
                        f"Kanalen är upptagen {_fmt(r['util'])} % av tiden med {r['clients']} klienter{f', varav {len(w)} med svag signal' if w else ''}. Över ~50 % ger fördröjning för alla på accesspunkten.",
                        "clients-card", "Visa klienter", pct=r["util"], mark=WARN, normal="kanalbeläggning")
    # länkar: internet, upplänkar och radio
    nodes = [{"id": "internet", "label": "Internet", "sub": f"{_fmt(lat['now'])} ms" if lat and lat["now"] is not None else "", "level": "err" if lat and lat["state"] == "down" else "ok"},
             {"id": "pfsense", "label": "pfSense", "sub": f"CPU {_fmt(ps['cpu'])} %" if ps.get("cpu") is not None else "brandvägg", "level": "ok"}]
    wan_pct = max(p for p in (down_pct, up_pct, -1) if p is not None)
    edges = [{"from": "internet", "to": "pfsense", "pct": None if wan_pct < 0 else wan_pct,
              "label": f"↓ {_fmt(_mbit(down_bps))} ↑ {_fmt(_mbit(up_bps), 1)} Mbit/s" if down_bps is not None else "",
              "mbit": (_mbit(down_bps) or 0) + (_mbit(up_bps) or 0)}]
    if up_pct is not None or up_bps is not None:
        links.append({"name": "WAN uppladdning", "pct": up_pct, "mbit": _mbit(up_bps), "cap": cap_up or None,
                      "note": f"{top_up[0]} {_fmt(_mbit(top_up[1]), 1)} Mbit/s" if top_up else "", "goto": "sec-chart"})
        links.append({"name": "WAN nedladdning", "pct": down_pct, "mbit": _mbit(down_bps), "cap": cap_down or None,
                      "note": f"{top_down[0]} {_fmt(_mbit(top_down[1]))} Mbit/s" if top_down else "", "goto": "sec-chart"})
    for d in devs.values():
        n_cl = sum(1 for u in e.unifi_clients.values() if u.get("dev") == d["id"])
        radios = d.get("radios") or []
        worst_radio = max((r["util"] for r in radios if r.get("util") is not None), default=None)
        level = "err" if not d["online"] else _lvl(worst_radio) if d["kind"] == "ap" else "ok"
        sub = f"{n_cl} klienter" + (f" · kanal {_fmt(worst_radio)} %" if worst_radio is not None else "")
        nodes.append({"id": d["id"], "label": d["name"], "sub": sub, "level": level, "kind": d["kind"]})
        parent = d.get("uplink_mac") if d.get("uplink_mac") in devs else "pfsense"
        rate = max(d.get("uplink_rx_bps") or 0, d.get("uplink_tx_bps") or 0) if d.get("uplink_rx_bps") is not None else None
        pct = rate / (d["uplink_speed"] * 1e6) * 100 if rate is not None and d.get("uplink_speed") else None
        edges.append({"from": parent, "to": d["id"], "pct": pct, "mbit": _mbit(rate) or 0,
                      "label": f"{_fmt(pct)} %" if pct is not None else "", "speed": d.get("uplink_speed")})
        pname = devs[parent]["name"] if parent in devs else "pfSense"
        if pct is not None:
            links.append({"name": f"{pname} ↔ {d['name']}", "pct": pct, "mbit": _mbit(rate), "cap": d.get("uplink_speed"),
                          "note": f"{_fmt(_mbit(rate))} av {d['uplink_speed']} Mbit/s", "goto": "unifi-card"})
            if pct >= ERR:
                problem("warn", "Nät", f"Länken {pname} ↔ {d['name']} är nästan full", _fmt(pct), "%",
                        f"{_fmt(_mbit(rate))} av {d['uplink_speed']} Mbit/s. Allt som går genom {d['name']} blir långsammare.", "unifi-card", "Visa UniFi", pct=pct, mark=WARN)
        for r in radios:
            if r.get("util") is not None:
                links.append({"name": f"Wi-Fi {r['band'] or ''} {d['name']}".replace("  ", " "), "pct": r["util"], "mbit": None, "cap": None,
                              "note": f"{r['clients']} klienter" + (f", {len([u for u in weak if u.get('dev') == d['id']])} svaga" if any(u.get('dev') == d['id'] for u in weak) else ""),
                              "goto": "clients-card"})
    links.sort(key=lambda x: x["pct"] if x["pct"] is not None else -1, reverse=True)
    # ── servrar ──
    servers_ok, servers = 0, 0
    for u in e.unraids:
        servers += 1
        st = u.status
        name = st.get("name") or "Unraid"
        if not st.get("ok"):
            if st.get("error"):
                problem("err", "Servrar", f"{name} svarar inte", "", "", st["error"], "unraid-cards", f"Visa {name}")
        elif st.get("level") in ("warn", "err"):
            problem(st["level"], "Servrar", f"{name} behöver uppmärksamhet", "", "", (lambda t: t[:1].upper() + t[1:])(st.get("detail") or ""), "unraid-cards", f"Visa {name}")
        else:
            servers_ok += 1
    for p in e.plex_servers:
        servers += 1
        if p.status.get("ok"):
            servers_ok += 1
        elif p.status.get("error"):
            problem("warn", "Plex", f"{p.name} svarar inte", "", "", p.status["error"], "plex-card", "Visa Plex")
    ph = e.pihole.brief() if e.pihole else None
    if ph:
        if not ph["ok"] and ph.get("error"):
            problem("warn", "DNS", f"{ph['name']} svarar inte", "", "", ph["error"], "pihole-card", "Visa Pi-hole")
        elif ph["ok"] and ph.get("blocking") != "enabled":
            problem("warn", "DNS", f"{ph['name']}: blockeringen är pausad", "", "", "Annonser och spårare blockeras inte just nu.", "pihole-card", "Visa Pi-hole")
    # ── säkerhet ──
    active = [a for a in e.alerts.banner() if not a.get("resolved")]
    sec = [a for a in active if a["kind"] in ("suricata", "pfblocker")]
    if sec:
        problem("warn", "Säkerhet", f"{len(sec)} säkerhetslarm", str(len(sec)), "", "; ".join(a["title"] for a in sec[:3]), "sec-security", "Visa säkerhet")

    order = {"err": 0, "warn": 1}
    problems.sort(key=lambda p: order.get(p["level"], 2))
    # ── små rutor för det som fungerar ──
    lvl_of = lambda area: "err" if any(p["area"] == area and p["level"] == "err" for p in problems) else "warn" if any(p["area"] == area for p in problems) else "ok"  # noqa: E731
    wifi_util = max((r["util"] for d in devs.values() for r in (d.get("radios") or []) if r.get("util") is not None), default=None)
    tiles = [
        {"label": "Internet", "level": lvl_of("Internet"), "value": _fmt(lat["now"]) if lat and lat["now"] is not None else "–", "unit": "ms",
         "sub": f"normalt {_fmt(lat['base'])} ms" if lat and lat.get("base") is not None else (lat["gateway"] if lat else "ingen gateway"), "goto": "sec-security"},
        {"label": "WAN ned", "level": _lvl(down_pct, 90, 98) if down_pct is not None else "ok", "value": _fmt(_mbit(down_bps)), "unit": f"/{cap_down}" if cap_down else "Mbit/s",
         "sub": f"{_fmt(down_pct)} % av linjen" if down_pct is not None else "ange linjens hastighet", "goto": "sec-chart"},
        {"label": "WAN upp", "level": _lvl(up_pct, 90, 98) if up_pct is not None else "ok", "value": _fmt(_mbit(up_bps), 1), "unit": f"/{cap_up}" if cap_up else "Mbit/s",
         "sub": f"{_fmt(up_pct)} % av linjen" if up_pct is not None else "ange linjens hastighet", "goto": "sec-chart"},
        {"label": "VPN", "level": lvl_of("VPN") if vpn.get("enabled") else "off", "value": "Uppe" if vpn.get("ok") else "Nere" if vpn.get("enabled") else "–", "unit": "",
         "sub": (vpn.get("label") or "VPN") if vpn.get("enabled") else "ingen VPN", "goto": "sec-security"},
        {"label": "pfSense", "level": lvl_of("pfSense"), "value": _fmt(ps.get("cpu")), "unit": "% CPU",
         "sub": f"{len(e.conns)} anslutningar", "goto": "sec-map"},
        {"label": "DNS", "level": lvl_of("DNS") if ph else "off", "value": _fmt(ph.get("percent_blocked")) if ph and ph.get("percent_blocked") is not None else "–", "unit": "% block",
         "sub": ph["name"] if ph else "ingen Pi-hole", "goto": "pihole-card"},
        {"label": "Wi-Fi", "level": lvl_of("Wi-Fi") if devs else "off", "value": _fmt(wifi_util) if wifi_util is not None else "–", "unit": "% kanal",
         "sub": f"{len(weak)} svag signal" if weak else f"{sum(1 for u in e.unifi_clients.values() if u.get('kind') == 'wifi')} trådlösa", "goto": "unifi-card"},
        {"label": "Servrar", "level": "err" if any(p["area"] in ("Servrar", "Plex") and p["level"] == "err" for p in problems) else "warn" if any(p["area"] in ("Servrar", "Plex") for p in problems) else "ok",
         "value": f"{servers_ok}", "unit": f"/{servers}" if servers else "", "sub": "alla OK" if servers and servers_ok == servers else "Unraid och Plex", "goto": "unraid-cards"},
        {"label": "Säkerhet", "level": lvl_of("Säkerhet"), "value": str(len(sec)), "unit": "", "sub": "aktiva larm", "goto": "sec-security"},
    ]
    n = len(problems)
    verdict = {"level": problems[0]["level"] if problems else "ok",
               "text": "Allt fungerar" if not n else f"{n} {'sak behöver' if n == 1 else 'saker behöver'} uppmärksamhet",
               "sub": "Inga problem hittades." if not n else "Allt annat fungerar."}
    return {"generated": time.time(), "verdict": verdict, "problems": problems, "tiles": tiles, "latency": lat,
            "map": {"nodes": nodes, "edges": edges}, "links": links[:7],
            "capacity": {"down": cap_down, "up": cap_up}}

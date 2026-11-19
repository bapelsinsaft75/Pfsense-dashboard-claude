(function () {
"use strict";

const REFRESH_MS = 5000;
const ICONS = {
  laptop: "M4 6h16v10H4z M2 19h20", phone: "M8 3h8v18H8z M11 18h2", tablet: "M6 3h12v18H6z M11 18h2",
  tv: "M3 5h18v12H3z M8 21h8", server: "M4 4h16v6H4z M4 14h16v6H4z M8 7h.01 M8 17h.01",
  console: "M7 8h10a4 4 0 0 1 0 9l-2-2H9l-2 2a4 4 0 0 1 0-9z M9 11v3 M7.5 12.5h3 M15.5 12h.01",
  speaker: "M7 3h10v18H7z M9 14a3 3 0 1 0 6 0a3 3 0 1 0-6 0 M12 7h.01",
  iot: "M12 3a6 6 0 0 1 3.5 10.9V17h-7v-3.1A6 6 0 0 1 12 3z M9.5 20h5",
  robot: "M4 12a8 8 0 1 0 16 0a8 8 0 1 0-16 0 M10 12a2 2 0 1 0 4 0a2 2 0 1 0-4 0",
  printer: "M7 8V3h10v5 M4 8h16v8H4z M7 13h10v8H7z", desktop: "M3 4h18v12H3z M9 20h6 M12 16v4",
  wifi: "M2.5 9a14 14 0 0 1 19 0 M5.5 12.3a9.5 9.5 0 0 1 13 0 M8.7 15.6a5 5 0 0 1 6.6 0 M12 19h.01",
  cable: "M7 3h10v8H7z M10 11v4h4v-4 M12 15v6 M9.5 6v2 M12 6v2 M14.5 6v2",
  switch: "M2 8h20v8H2z M5.5 12h.01 M8.5 12h.01 M11.5 12h.01 M14.5 12h.01 M18 12h1",
  ap: "M3 11a9 4 0 1 0 18 0a9 4 0 1 0-18 0 M12 11h.01 M3 11v2a9 4 0 0 0 18 0v-2",
};
const HOUSE = "M0-8.5-8-1.5h2.3V7h4.2V2.2h3V7h4.2V-1.5H8z";
const PERIOD_LABEL = { "24h": "senaste 24 timmarna", "7d": "senaste 7 dagarna", "30d": "senaste 30 dagarna" };

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const icon = (t) => `<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="${ICONS[t] || ICONS.desktop}"/></svg>`;
const fmt = (n, d = 1) => Number(n || 0).toLocaleString("sv-SE", { maximumFractionDigits: d });
function fmtBytes(b) {
  if (b >= 1e12) return fmt(b / 1e12, 2) + " TB";
  if (b >= 1e9) return fmt(b / 1e9, 1) + " GB";
  if (b >= 1e6) return fmt(b / 1e6, 0) + " MB";
  if (b >= 1e3) return fmt(b / 1e3, 0) + " kB";
  return fmt(b, 0) + " B";
}
function bytesParts(b) {
  const s = fmtBytes(b).split(" ");
  return `${s[0]}<small>${s[1]}</small>`;
}
function fmtRate(bps) {
  const m = bps / 1e6;
  return m >= 10 ? fmt(m, 0) : m >= 1 ? fmt(m, 1) : fmt(m, 2);
}
const regionNames = (() => { try { return new Intl.DisplayNames(["sv"], { type: "region" }); } catch (e) { return null; } })();
const countryName = (cc) => { try { return (regionNames && regionNames.of(cc)) || cc; } catch (e) { return cc; } };
const clock = (t) => new Date(t * 1000).toLocaleTimeString("sv-SE", { timeZone: "Europe/Stockholm" });
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const state = { period: "24h", data: null, mapFilter: null, devF: null, connF: "", sortKey: "bytes", sortDir: -1 };
let coords = {};
let byId = {};

/* ── karta ── */
class MapView {
  constructor(svgEl) {
    this.svg = d3.select(svgEl);
    const vb = svgEl.viewBox.baseVal;
    this.w = vb.width; this.h = vb.height;
    const s = this.svg;
    this.gS = s.append("path").attr("class", "sphere");
    this.gG = s.append("path").attr("class", "grat");
    this.gL = s.append("g"); this.gB = s.append("g"); this.gF = s.append("g"); this.gE = s.append("g");
    this.gH = s.append("g").attr("class", "home");
    this.zoom = "world";
    this.flows = [];
    this.home = { name: "Örebro", lon: 15.2066, lat: 59.2741 };
    this.makeProj();
  }
  makeProj() {
    this.p = d3.geoEqualEarth();
    if (this.zoom === "europe") this.p.fitExtent([[10, 10], [this.w - 10, this.h - 10]], { type: "MultiPoint", coordinates: [[-11, 36], [34, 71], [30, 36], [-11, 66]] });
    else this.p.fitExtent([[6, 6], [this.w - 6, this.h - 6]], { type: "Sphere" });
    this.path = d3.geoPath(this.p);
  }
  setWorld(world) {
    this.gL.selectAll("path").data(topojson.feature(world, world.objects.countries).features).join("path").attr("class", "land");
    this.render();
  }
  arc(f) {
    const a = [this.home.lon, this.home.lat], b = coords[f.cc];
    const [s, t] = f.dir === "out" ? [a, b] : [b, a];
    const p0 = this.p(s), p1 = this.p(t);
    const dx = p1[0] - p0[0], dy = p1[1] - p0[1], d = Math.hypot(dx, dy) || 1;
    const k = Math.min(0.32, 14 / d + 0.14) * (f.dir === "in" ? 0.6 : 1);
    const nx = -dy / d, ny = dx / d, mx = (p0[0] + p1[0]) / 2, my = (p0[1] + p1[1]) / 2;
    let cx = mx + nx * d * k, cy = my + ny * d * k;
    if (cy > my) { cx = mx - nx * d * k; cy = my - ny * d * k; }
    return `M${p0[0]},${p0[1]}Q${cx},${cy} ${p1[0]},${p1[1]}`;
  }
  render() {
    const P = this.path;
    this.gS.attr("d", P({ type: "Sphere" }));
    this.gG.attr("d", P(d3.geoGraticule10()));
    this.gL.selectAll("path").attr("d", P);
    const flows = this.flows.filter((f) => coords[f.cc]);
    const max = d3.max(flows, (f) => f.bytes) || 1;
    const sw = (f) => Math.max(1, Math.min(4.2, 1 + Math.sqrt(f.bytes / max) * 3.2));
    const show = (f) => !this.filter || f.clients.includes(this.filter);
    const col = (f) => (f.dir === "out" ? "var(--out)" : "var(--in)");
    const tip = (f) => `${countryName(f.cc)} · ${f.dir === "out" ? "startad inifrån" : "startad utifrån"}\n${fmtBytes(f.bytes)} · ${f.conns} anslutningar${f.active ? ` (${f.active} aktiva nu)` : ""}\n${f.clients.slice(0, 8).map((c) => (byId[c] ? byId[c].name : c)).join(", ")}`;
    const key = (f) => f.cc + f.dir;
    this.gB.selectAll("path").data(flows, key).join("path").attr("class", (f) => "flow" + (show(f) ? "" : " dim"))
      .attr("d", (f) => this.arc(f)).attr("stroke", col).attr("stroke-width", sw).attr("opacity", 0.28);
    this.gF.selectAll("path").data(flows, key).join((en) => en.append("path").call((p) => p.append("title")))
      .attr("class", (f) => "flow dash" + (show(f) ? "" : " dim")).attr("d", (f) => this.arc(f)).attr("stroke", col).attr("stroke-width", sw)
      .select("title").text(tip);
    const pts = d3.groups(flows, (f) => f.cc).map(([cc, fs]) => ({ cc, fs }));
    const labels = this.zoom === "europe" || pts.length <= 14;
    this.gE.selectAll("g").data(pts, (d) => d.cc).join((en) => { const g = en.append("g").attr("class", "endpoint"); g.append("circle"); g.append("text"); g.append("title"); return g; })
      .each((d, i, n) => {
        const g = d3.select(n[i]), [x, y] = this.p(coords[d.cc]);
        const any = d.fs.some(show), both = d.fs.length > 1, hasOut = d.fs.some((f) => f.dir === "out");
        g.attr("transform", `translate(${x},${y})`).classed("dim", !any);
        g.select("circle").attr("r", 3.2).attr("fill", hasOut ? "var(--out)" : "var(--in)").attr("stroke", both ? "var(--in)" : "none").attr("stroke-width", both ? 1.6 : 0);
        g.select("text").attr("x", 6).attr("y", 3).text(labels && d.cc !== "SE" ? countryName(d.cc) : "");
        g.select("title").text(d.fs.map(tip).join("\n\n"));
      });
    const [hx, hy] = this.p([this.home.lon, this.home.lat]);
    if (this.gH.selectAll("*").empty()) {
      this.gH.append("circle").attr("class", "home-pulse").attr("r", 9);
      this.gH.append("path").attr("d", HOUSE).attr("transform", "scale(1.1)");
      this.gH.append("text").attr("text-anchor", "end").attr("x", -13).attr("y", -13);
    }
    this.gH.attr("transform", `translate(${hx},${hy})`).select("text").text(this.home.name);
  }
}

/* ── rendering ── */
function renderStatus(d) {
  const names = { pfsense: "pfSense", unifi: "UniFi", netflow: "NetFlow", geoip: "GeoIP" };
  const chips = [], problems = [];
  for (const [k, label] of Object.entries(names)) {
    const s = d.status[k] || {};
    const enabled = k === "geoip" ? true : s.enabled;
    let cls = "off", title = "Inte konfigurerat";
    if (enabled && s.ok) { cls = "ok"; title = s.last ? `Senast ${clock(s.last)}` : "OK"; }
    else if (enabled && s.error) { cls = "err"; title = s.error; problems.push([label, s.error]); }
    else if (enabled) { cls = ""; title = "Väntar på data"; }
    chips.push(`<span class="chip ${cls}" title="${esc(title)}">${label}</span>`);
  }
  $("chips").innerHTML = chips.join("");
  $("problems").innerHTML = problems.map(([l, e]) => `<div class="problem"><b>${esc(l)}</b>${esc(e)}</div>`).join("");
  $("demo").hidden = d.mode !== "demo";
  const src = d.traffic_source === "netflow" ? "NetFlow" : "pfSense-tillstånd";
  $("sub").textContent = `pfSense · ${d.home.name} · data från ${src} · uppdaterad ${clock(d.generated)}`;
  if (d.status.geoip && d.status.geoip.source) $("geo-src").textContent = `${d.status.geoip.source}${d.status.geoip.updated ? ", " + d.status.geoip.updated : ""}`;
}

function renderKpis(d) {
  const k = d.kpi;
  $("k-total").innerHTML = bytesParts(k.total_bytes);
  $("k-total-s").textContent = PERIOD_LABEL[d.period];
  $("k-now").innerHTML = `${fmt((k.now_down_bps + k.now_up_bps) / 1e6, 0)}<small>Mbit/s</small>`;
  $("k-now-s").textContent = `${fmt(k.now_down_bps / 1e6, 0)} ned · ${fmt(k.now_up_bps / 1e6, 0)} upp · ${k.active_conns} anslutningar`;
  $("k-cl").textContent = k.clients_online;
  const parts = [];
  if (k.wifi || k.wired) parts.push(`${k.wifi} trådlöst`, `${k.wired} kabel`);
  if (k.unknown_link && (k.wifi || k.wired)) parts.push(`${k.unknown_link} okänd`);
  parts.push(`${k.clients_total - k.clients_online} inaktiva`);
  $("k-cl-s").textContent = parts.join(" · ");
  const cs = new Set(d.flows.map((f) => f.cc)), ci = new Set(d.flows.filter((f) => f.dir === "in").map((f) => f.cc));
  $("k-ctry").textContent = cs.size;
  $("k-ctry-s").textContent = `${ci.size} med anslutningar startade utifrån`;
}

function renderCountries(d) {
  const by = d3.rollups(d.flows, (v) => ({ o: d3.sum(v.filter((f) => f.dir === "out"), (f) => f.bytes), i: d3.sum(v.filter((f) => f.dir === "in"), (f) => f.bytes), n: d3.sum(v, (f) => f.conns), hasIn: v.some((f) => f.dir === "in") }), (f) => f.cc)
    .sort((a, b) => (b[1].o + b[1].i) - (a[1].o + a[1].i));
  const all = d3.sum(by, (x) => x[1].o + x[1].i) || 1;
  $("ctrylist").innerHTML = by.length ? by.slice(0, 9).map(([cc, v]) => {
    const t = v.o + v.i, share = t / all * 100;
    return `<div class="crow"><span class="f">${esc(countryName(cc))}</span><span class="num">${fmtBytes(t)}</span><div class="split"><div class="o" style="width:${t ? share * v.o / t : 0}%"></div><div class="i" style="width:${v.hasIn ? Math.max(1.2, t ? share * v.i / t : 0) : 0}%"></div></div><span class="m">${fmt(v.n, 0)} anslutningar${v.hasIn ? " · varav utifrån" : ""}</span><span class="m num">${fmt(share, 0)} %</span></div>`;
  }).join("") : `<div class="empty">Ingen trafik registrerad än.</div>`;
}

function renderChart(d) {
  const W = 760, H = 220, m = { l: 40, r: 8, t: 14, b: 24 };
  const data = d.series;
  const el = $("chart");
  el.innerHTML = "";
  if (!data.length) return;
  const x = d3.scaleTime([new Date(data[0].t * 1000), new Date(data[data.length - 1].t * 1000)], [m.l, W - m.r]);
  const maxV = d3.max(data, (p) => Math.max(p.down_bps, p.up_bps)) / 1e6 || 1;
  const y = d3.scaleLinear([0, maxV * 1.1], [H - m.b, m.t]).nice(4);
  const s = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("role", "img").attr("aria-label", "Bandbredd över tid")
    .style("width", "100%").style("height", "auto").style("display", "block");
  const yt = y.ticks(4);
  s.append("g").selectAll("line").data(yt).join("line").attr("class", "gl").attr("x1", m.l).attr("x2", W - m.r).attr("y1", y).attr("y2", y);
  s.append("g").selectAll("text").data(yt).join("text").attr("x", m.l - 6).attr("y", (v) => y(v) + 3.5).attr("text-anchor", "end").text((v) => fmt(v, 0));
  const tz = { timeZone: "Europe/Stockholm" };
  const tf = d.period === "24h" ? (t) => t.toLocaleTimeString("sv-SE", { ...tz, hour: "2-digit", minute: "2-digit" })
    : (t) => t.toLocaleDateString("sv-SE", { ...tz, day: "numeric", month: "short" });
  s.append("g").selectAll("text").data(x.ticks(d.period === "24h" ? 8 : 7)).join("text").attr("x", x).attr("y", H - 6).attr("text-anchor", "middle").text(tf);
  const cv = d3.curveMonotoneX, X = (p) => x(new Date(p.t * 1000));
  s.append("path").attr("d", d3.area().x(X).y0(y(0)).y1((p) => y(p.down_bps / 1e6)).curve(cv)(data)).attr("fill", "var(--accent)").attr("fill-opacity", 0.12);
  s.append("path").attr("d", d3.line().x(X).y((p) => y(p.down_bps / 1e6)).curve(cv)(data)).attr("fill", "none").attr("stroke", "var(--accent)").attr("stroke-width", 2);
  s.append("path").attr("d", d3.line().x(X).y((p) => y(p.up_bps / 1e6)).curve(cv)(data)).attr("fill", "none").attr("stroke", "var(--up)").attr("stroke-width", 1.6);
  const pk = data.reduce((a, b) => (b.down_bps > a.down_bps ? b : a));
  if (pk.down_bps > 0) {
    const px = X(pk), py = y(pk.down_bps / 1e6), left = px > W / 2;
    s.append("circle").attr("cx", px).attr("cy", py).attr("r", 4).attr("fill", "var(--accent)").attr("stroke", "var(--card)").attr("stroke-width", 2);
    const when = new Date(pk.t * 1000);
    const label = d.period === "24h" ? when.toLocaleTimeString("sv-SE", { ...tz, hour: "2-digit", minute: "2-digit" }) : when.toLocaleString("sv-SE", { ...tz, day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
    s.append("text").attr("x", left ? px - 8 : px + 8).attr("y", Math.max(12, py - 8)).attr("text-anchor", left ? "end" : "start")
      .attr("style", "fill:var(--fg);font-weight:500").text(`Topp ${fmt(pk.down_bps / 1e6, 0)} Mbit/s ${label}`);
  }
  $("chart-s").textContent = `Mbit/s, klienter mot internet, ${PERIOD_LABEL[d.period]}`;
}

function renderTop(d) {
  const top = d.clients.filter((c) => c.bytes > 0).slice(0, 7);
  const mx = top.length ? top[0].bytes : 1;
  $("topc-s").textContent = PERIOD_LABEL[d.period];
  $("topc").innerHTML = top.length ? top.map((c) => `<div class="r pickable" data-id="${esc(c.id)}">${icon(c.type)}<span class="n">${esc(c.name)}</span><span class="num">${fmtBytes(c.bytes)}</span><div class="t"><div style="width:${c.bytes / mx * 100}%"></div></div></div>`).join("")
    : `<div class="empty">Ingen data än. Den första mätningen kommer inom några minuter.</div>`;
}

function renderDevices(d) {
  const card = $("unifi-card");
  if (!d.devices.length) {
    const st = d.status.unifi || {};
    $("devs").innerHTML = `<div class="empty">${st.enabled ? (st.error ? "UniFi svarar inte. Se felmeddelandet ovan." : "Väntar på data från UniFi …") : "UniFi är inte konfigurerat. Fyll i UNIFI_URL, UNIFI_USER och UNIFI_PASS i Unraid-mallen för att se switchar, accesspunkter och om klienter är trådlösa eller trådbundna."}</div>`;
    $("portkey").hidden = true;
    return;
  }
  $("portkey").hidden = false;
  card.hidden = false;
  $("devs").innerHTML = d.devices.map((dv) => {
    const gb = d3.sum(dv.clients, (id) => (byId[id] ? byId[id].bytes : 0));
    let body;
    if (dv.kind === "switch") {
      const ports = Array.from({ length: dv.ports }, (_, i) => {
        const n = String(i + 1);
        const cls = dv.used[n] ? "on" : dv.infra[n] ? "inf" : dv.unknown.includes(n) ? "unk" : "";
        const t = dv.used[n] || dv.infra[n] || (dv.unknown.includes(n) ? "aktiv, okänd enhet" : "ledig");
        return `<i class="${cls}" title="Port ${n}: ${esc(t)}"></i>`;
      }).join("");
      const inUse = Object.keys(dv.used).length + Object.keys(dv.infra).length + dv.unknown.length;
      body = `<div class="ports" aria-label="Portar">${ports}</div><div class="meta">${inUse} av ${dv.ports} portar används${dv.uplink ? ` · upplänk port ${dv.uplink}` : ""}</div>`;
    } else {
      const bands = Object.entries(dv.bands).sort().map(([b, n]) => `${n} på ${esc(b)}`).join(" · ") || "inga klienter";
      body = `<div class="meta">${bands}${dv.ssids.length ? ` · SSID ${dv.ssids.map(esc).join(", ")}` : ""}</div>${dv.uplink ? `<div class="meta">Ansluten till ${esc(dv.uplink)}</div>` : ""}`;
    }
    return `<button class="dev" type="button" data-dev="${esc(dv.id)}" aria-pressed="${state.devF === dv.id}"><div class="hd">${icon(dv.kind)}<div><b>${esc(dv.name)}</b><span>${esc(dv.model)}${dv.online ? "" : ' · <span class="offline">offline</span>'}</span></div></div><div class="num">${dv.clients.length} klienter <span class="muted">· ${fmtBytes(gb)}</span></div>${body}</button>`;
  }).join("");
}

const sigLvl = (r) => (r >= -55 ? 4 : r >= -62 ? 3 : r >= -70 ? 2 : 1);
const sig = (r) => `<span class="sig" title="${r} dBm">${[3, 5, 8, 11].map((h, i) => `<i class="${i < sigLvl(r) ? "on" : ""}" style="height:${h}px"></i>`).join("")}</span>`;
function connHTML(c) {
  const k = c.conn;
  if (!k) return `<span class="muted">–</span>`;
  if (k.kind === "wifi") return `<span class="conn">${icon("wifi")}<span>Wi-Fi ${esc(k.band || "")}</span>${k.rssi != null ? sig(k.rssi) + `<small>${k.rssi} dBm</small>` : ""}</span>`;
  return `<span class="conn">${icon("cable")}<span>${esc(k.dev_name || "Kabel")}</span>${k.port ? `<small>port ${k.port}</small>` : ""}</span>`;
}

function renderTable(d) {
  const q = $("search").value.trim().toLowerCase();
  const ipn = (ip) => (ip && ip.includes(".") ? ip.split(".").reduce((a, x) => a * 256 + +x, 0) : 0);
  const val = (c) => state.sortKey === "conn" ? (c.conn ? (c.conn.kind === "wifi" ? "0" + (c.conn.band || "") + String(200 + (c.conn.rssi || 0)).padStart(3, "0") : "1" + (c.conn.dev_name || "") + String(c.conn.port || 0).padStart(3, "0")) : "2")
    : state.sortKey === "now" ? c.down_bps + c.up_bps : state.sortKey === "ip" ? ipn(c.ip) : state.sortKey === "name" ? c.name.toLowerCase() : c.bytes;
  const dev = state.devF;
  const rows = d.clients.filter((c) => (!state.connF || (c.conn && c.conn.kind === state.connF)) && (!dev || (c.conn && c.conn.dev === dev))
    && (!q || `${c.name} ${c.ip || ""} ${c.mac || ""}`.toLowerCase().includes(q)))
    .sort((a, b) => { const x = val(a), y = val(b); return (x > y ? 1 : x < y ? -1 : 0) * state.sortDir; });
  document.querySelector("#table tbody").innerHTML = rows.length ? rows.map((c) => `<tr class="pickable${state.mapFilter === c.id ? " sel" : ""}" data-id="${esc(c.id)}"><td><span class="who">${icon(c.type)}${esc(c.name)}</span></td><td class="ip">${esc(c.ip || "–")}</td><td>${connHTML(c)}</td><td>${esc((c.mac || "–").toUpperCase())}</td><td><span class="pill${c.online ? "" : " idle"}">${c.online ? "Online" : "Inaktiv"}</span></td><td class="r-right num">${c.online && (c.down_bps + c.up_bps) > 0 ? fmtRate(c.down_bps + c.up_bps) + " Mbit/s" : "–"}</td><td class="r-right num">${fmtBytes(c.bytes)}</td></tr>`).join("")
    : `<tr><td colspan="7" class="muted">Inga klienter matchar filtret.</td></tr>`;
  document.querySelectorAll("#table th button").forEach((b) => {
    const base = b.textContent.replace(/ [↑↓]$/, "");
    b.textContent = b.dataset.sort === state.sortKey ? `${base} ${state.sortDir < 0 ? "↓" : "↑"}` : base;
  });
}

function renderMapFilter() {
  const c = state.mapFilter && byId[state.mapFilter];
  $("mapfilter").innerHTML = c ? `<b>Visar endast ${esc(c.name)}</b> <button type="button" id="clearfilter">Visa alla</button>` : "";
}

function render() {
  const d = state.data;
  if (!d) return;
  byId = Object.fromEntries(d.clients.map((c) => [c.id, c]));
  if (state.mapFilter && !byId[state.mapFilter]) state.mapFilter = null;
  renderStatus(d); renderKpis(d); renderCountries(d); renderChart(d); renderTop(d); renderDevices(d); renderTable(d); renderMapFilter();
  map.home = d.home;
  map.flows = d.flows;
  map.filter = state.mapFilter;
  map.render();
}

async function load() {
  try {
    const r = await fetch(`api/summary?period=${state.period}`, { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    state.data = await r.json();
    render();
  } catch (e) {
    $("problems").innerHTML = `<div class="problem"><b>Dashboard</b>Får inget svar från servern (${esc(e.message)}). Försöker igen …</div>`;
  }
}

/* ── händelser ── */
function setPressed(group, btn) { [...group.querySelectorAll("button")].forEach((x) => x.setAttribute("aria-pressed", x === btn)); }
$("period").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; state.period = b.dataset.p; setPressed(e.currentTarget, b); load(); });
$("zoom").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; setPressed(e.currentTarget, b); map.zoom = b.dataset.z; map.makeProj(); map.render(); });
$("connf").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; state.connF = b.dataset.c; setPressed(e.currentTarget, b); render(); });
$("devs").addEventListener("click", (e) => { const b = e.target.closest("[data-dev]"); if (!b) return; state.devF = state.devF === b.dataset.dev ? null : b.dataset.dev; render(); });
$("search").addEventListener("input", () => state.data && renderTable(state.data));
document.querySelector("#table thead").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  const k = b.dataset.sort;
  state.sortDir = state.sortKey === k ? -state.sortDir : (k === "name" || k === "ip" || k === "conn" ? 1 : -1);
  state.sortKey = k; render();
});
document.addEventListener("click", (e) => {
  if (e.target.closest("#clearfilter")) { state.mapFilter = null; render(); return; }
  const r = e.target.closest("[data-id]");
  if (r && (r.closest("#table") || r.closest("#topc"))) { state.mapFilter = state.mapFilter === r.dataset.id ? null : r.dataset.id; render(); }
});

const map = new MapView(document.querySelector("#map svg"));
Promise.all([fetch("countries-110m.json").then((r) => r.json()), fetch("country-coords.json").then((r) => r.json())])
  .then(([world, cc]) => { coords = cc; map.setWorld(world); render(); })
  .catch(() => { $("problems").innerHTML += `<div class="problem"><b>Karta</b>Kartdata kunde inte laddas.</div>`; });
load();
setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS);
if (reduceMotion) document.documentElement.classList.add("reduce");
})();

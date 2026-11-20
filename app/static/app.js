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
const PERIOD_LABEL = { live: "just nu", "24h": "senaste 24 timmarna", "7d": "senaste 7 dagarna", "30d": "senaste 30 dagarna" };

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

const state = { period: (() => { try { const p = localStorage.getItem("period"); return ["live", "24h", "7d", "30d"].includes(p) ? p : "24h"; } catch (e) { return "24h"; } })(), data: null, mapFilter: null, devF: null, connF: "", sortKey: "bytes", sortDir: -1,
  clAll: (() => { try { return localStorage.getItem("clientsAll") === "1"; } catch (e) { return false; } })() };
let coords = {};
let byId = {};

/* ── karta ── */
class MapView {
  constructor(svgEl) {
    this.svg = d3.select(svgEl);
    const vb = svgEl.viewBox.baseVal;
    this.w = vb.width; this.h = vb.height;
    const s = this.svg.append("g").attr("class", "maproot");
    this.root = s;
    this.k = 1;
    this.gS = s.append("path").attr("class", "sphere");
    this.gG = s.append("path").attr("class", "grat");
    this.gL = s.append("g"); this.gB = s.append("g"); this.gF = s.append("g"); this.gE = s.append("g");
    this.gH = s.append("g").attr("class", "home");
    this.zoom = "world";
    this.flows = [];
    this.home = { name: "Örebro", lon: 15.2066, lat: 59.2741 };
    this.makeProj();
    // zoom med knapparna, dra med musen; på mobil flyttar ett finger kartan bara när man har zoomat in
    this.zb = d3.zoom().scaleExtent([1, 16]).translateExtent([[0, 0], [this.w, this.h]])
      .filter((e) => !e.button && e.type !== "wheel" && e.type !== "dblclick"
        && (e.type !== "touchstart" || this.k > 1 || (e.touches && e.touches.length > 1)))
      .on("zoom", (e) => {
        this.k = e.transform.k;
        this.root.attr("transform", e.transform);
        this.applyK();
        this.svg.classed("zoomed", this.k > 1.001).style("touch-action", this.k > 1.001 ? "none" : "pan-y");
        const lvl = document.getElementById("map-zlevel");
        if (lvl) lvl.textContent = `${Math.round(this.k * 10) / 10}×`;
        const out = document.querySelector('[data-mz="out"]'), rs = document.querySelector('[data-mz="reset"]');
        if (out) out.disabled = this.k <= 1.001;
        if (rs) rs.disabled = this.k <= 1.001 && Math.abs(e.transform.x) < 0.5 && Math.abs(e.transform.y) < 0.5;
      });
    this.svg.call(this.zb).style("touch-action", "pan-y");
  }
  zoomBy(f) { this.svg.transition().duration(reduceMotion ? 0 : 250).call(this.zb.scaleBy, f, [this.w / 2, this.h / 2]); }
  zoomReset(anim = true) { this.svg.transition().duration(anim && !reduceMotion ? 300 : 0).call(this.zb.transform, d3.zoomIdentity); }
  applyK() {
    const inv = 1 / this.k;
    this.gE.selectAll("g.endpoint").attr("transform", function () { return `translate(${this.__x},${this.__y}) scale(${inv})`; });
    if (this.hx != null) this.gH.attr("transform", `translate(${this.hx},${this.hy}) scale(${inv})`);
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
    const tip = (f) => this.live
      ? `${countryName(f.cc)} · ${f.dir === "out" ? "startad inifrån" : "startad utifrån"}\n${fmtMbit(f.bps)} Mbit/s just nu · ${f.conns} ${f.conns === 1 ? "anslutning" : "anslutningar"} med trafik\n${(f.client_bps || []).map((c) => `${c.name} (${fmtMbit(c.bps)} Mbit/s)`).join(", ")}`
      : `${countryName(f.cc)} · ${f.dir === "out" ? "startad inifrån" : "startad utifrån"}\n${fmtBytes(f.bytes)} · ${f.conns} anslutningar${f.active ? ` (${f.active} aktiva nu)` : ""}\n${f.clients.slice(0, 8).map((c) => (byId[c] ? byId[c].name : c)).join(", ")}`;
    const key = (f) => f.cc + f.dir;
    this.gB.selectAll("path").data(flows, key).join("path").attr("class", (f) => "flow" + (show(f) ? "" : " dim"))
      .attr("d", (f) => this.arc(f)).attr("stroke", col).attr("stroke-width", sw).attr("opacity", 0.28);
    this.gF.selectAll("path").data(flows, key).join((en) => en.append("path").call((p) => p.append("title")))
      .attr("class", (f) => "flow dash" + (show(f) ? "" : " dim")).attr("d", (f) => this.arc(f)).attr("stroke", col).attr("stroke-width", sw)
      .select("title").text(tip);
    const pts = d3.groups(flows, (f) => f.cc).map(([cc, fs]) => ({ cc, fs }));
    const labels = this.zoom === "europe" || this.k >= 2 || pts.length <= 14;
    this.gE.selectAll("g").data(pts, (d) => d.cc).join((en) => { const g = en.append("g").attr("class", "endpoint"); g.append("circle"); g.append("text"); g.append("title"); return g; })
      .each((d, i, n) => {
        const g = d3.select(n[i]), [x, y] = this.p(coords[d.cc]);
        n[i].__x = x; n[i].__y = y;
        const any = d.fs.some(show), both = d.fs.length > 1, hasOut = d.fs.some((f) => f.dir === "out");
        g.attr("transform", `translate(${x},${y}) scale(${1 / this.k})`).classed("dim", !any);
        g.select("circle").attr("r", 3.2).attr("fill", hasOut ? "var(--out)" : "var(--in)").attr("stroke", both ? "var(--in)" : "none").attr("stroke-width", both ? 1.6 : 0);
        g.select("text").attr("x", 6).attr("y", 3).text(labels && d.cc !== "SE" ? countryName(d.cc) + (this.live ? ` · ${fmtMbit(d3.sum(d.fs, (f) => f.bps))} Mbit/s` : "") : "");
        g.classed("livept", !!this.live);
        g.select("title").text(d.fs.map(tip).join("\n\n") + "\n\nKlicka för detaljer");
        g.attr("data-cc", d.cc).style("cursor", "pointer");
      });
    const [hx, hy] = this.p([this.home.lon, this.home.lat]);
    if (this.gH.selectAll("*").empty()) {
      this.gH.append("circle").attr("class", "home-pulse").attr("r", 9);
      this.gH.append("path").attr("d", HOUSE).attr("transform", "scale(1.1)");
      this.gH.append("text").attr("text-anchor", "end").attr("x", -13).attr("y", -13);
    }
    this.hx = hx; this.hy = hy;
    this.gH.attr("transform", `translate(${hx},${hy}) scale(${1 / this.k})`).select("text").text(this.home.name);
  }
}

/* ── larm ── */
const seenAlerts = new Set();
let firstAlertRender = true;
function ago(t) {
  const m = Math.round((Date.now() / 1000 - t) / 60);
  if (m < 1) return "nyss";
  if (m < 60) return `för ${m} min sedan`;
  const h = Math.round(m / 60);
  if (h < 24) return `för ${h} h sedan`;
  return new Date(t * 1000).toLocaleString("sv-SE", { timeZone: "Europe/Stockholm", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}
const LVL = { critical: "Kritiskt", warning: "Varning", info: "Info" };
function renderAlerts(d) {
  const list = d.alerts || [];
  const html = list.map((a) => {
    const resolved = !!a.resolved;
    const isNew = !firstAlertRender && !seenAlerts.has(a.id);
    const meta = resolved ? `Löst ${ago(a.resolved)} · började ${ago(a.created)}` : `${ago(a.created)}${a.count > 1 ? ` · ${a.count} gånger` : ""}${a.emailed ? " · mejlat" : ""}`;
    return `<div class="alert ${resolved ? "resolved" : a.level}${isNew ? " new" : ""}" role="${a.level === "critical" && !resolved ? "alert" : "status"}">
      <span class="lvl">${resolved ? "Löst" : LVL[a.level] || a.level}</span>
      <div class="txt"><b>${esc(a.title)}</b><span>${esc(a.message || "")} · ${meta}</span></div>
      <div class="acts"><button type="button" class="btn" data-ack="${a.id}">${resolved ? "OK" : "Stäng"}</button></div></div>`;
  }).join("") + (list.length > 1 ? `<div class="alert info" style="background:var(--card);padding-block:6px"><span></span><span></span><div class="acts"><button type="button" class="btn" data-ack="all">Stäng alla</button></div></div>` : "");
  $("alerts").innerHTML = html;
  list.forEach((a) => seenAlerts.add(a.id));
  firstAlertRender = false;
  const active = list.filter((a) => !a.resolved).length;
  document.title = (active ? `(${active}) ` : "") + "pfSense Nätverkskarta";
}

function renderSecurity(d) {
  const sec = d.security || {};
  const t = sec.totals || {};
  const parts = [];
  const gws = (sec.gateways || []).concat((sec.vpn_clients || []).map((v) => ({ name: v.name, state: v.up ? "up" : "down", status: v.status, vpn: true })));
  if (gws.length) {
    parts.push(`<div><h3>Gateways och VPN</h3><div class="gws">${gws.map((g) => `<span class="gw ${g.state}" title="${esc(g.status || "")}">${esc(g.name)}${g.vpn ? " <small>VPN</small>" : ""}${g.delay ? ` <small>${esc(g.delay)}</small>` : ""}</span>`).join("")}</div></div>`);
  }
  parts.push(`<div class="secnums"><div><b class="num">${fmt(t.suricata || 0, 0)}</b><span>Suricata-larm</span></div><div><b class="num">${fmt(t.pfblockerng || 0, 0)}</b><span>pfBlockerNG-blockeringar</span></div><div><b class="num">${fmt(t.firewall || 0, 0)}</b><span>Övriga blockeringar</span></div></div>`);
  if ((sec.suricata || []).length) {
    parts.push(`<div><h3>Suricata</h3><div class="slist">${sec.suricata.map((x) => `<div class="r"><span class="prio p${x.prio}">P${x.prio}</span><span class="n" title="${esc(x.label)}">${esc(x.label)}</span><span class="num">${fmt(x.count, 0)}</span></div>`).join("")}</div></div>`);
  }
  if ((sec.pfb_clients || []).length) {
    parts.push(`<div><h3>Klienter som stoppats av pfBlockerNG</h3><div class="slist">${sec.pfb_clients.map((x) => `<div class="r"><span></span><span class="n">${esc(x.name)}</span><span class="num">${fmt(x.count, 0)}</span></div>`).join("")}</div></div>`);
  }
  if ((sec.blocked_countries || []).length) {
    parts.push(`<div><h3>Blockerade försök utifrån, per land</h3><div class="slist">${sec.blocked_countries.map((x) => `<div class="r"><span></span><span class="n">${esc(countryName(x.cc))}</span><span class="num">${fmt(x.count, 0)}</span></div>`).join("")}</div></div>`);
  }
  if (!t.suricata && !t.pfblockerng && !t.firewall && !(d.status.syslog || {}).ok) {
    parts.push(`<p class="muted" style="margin:0">Ingen syslog från pfSense än. Slå på fjärrloggning i pfSense (Status → System Logs → Settings) och skicka till dashboardens IP, port 5514. I Suricata: slå på "Send Alerts to System Log".</p>`);
  }
  $("security").innerHTML = parts.join("");
}

function renderEvents(d) {
  const ev = d.events || [];
  $("events").innerHTML = ev.length ? ev.map((e) => `<div class="ev ${e.level}"><i></i><span class="t">${esc(e.title)}${e.resolved && e.level !== "info" ? ' <span class="ok">· löst</span>' : ""}</span><span class="w">${ago(e.updated)}</span><span class="m">${esc(e.message || "")}</span></div>`).join("")
    : `<div class="empty">Inga larm. Här visas allt som har hänt, även larm du har stängt.</div>`;
}

/* ── Unraid (en eller flera servrar) ── */
let urFilter = {};
try { urFilter = JSON.parse(localStorage.getItem("urFilters") || "{}") || {}; } catch (e) { urFilter = {}; }
const urF = (name) => (urFilter[name] = urFilter[name] || { docker: "", vm: "" });
const GB = 1024 ** 3;
function fmtSize(b) { if (b == null) return "–"; return b >= 1e12 ? fmt(b / 1e12, 1) + " TB" : fmt(b / 1e9, 0) + " GB"; }
function fmtUptime(sec) {
  if (sec == null) return null;
  const d = Math.floor(sec / 86400), h = Math.floor(sec / 3600) % 24, m = Math.floor(sec / 60) % 60;
  return d ? `${d} ${d === 1 ? "dag" : "dagar"} ${h} h` : h ? `${h} h ${m} min` : `${m} min`;
}
function dockerStatus(c) {
  const st = c.status || "";
  const unit = { second: "s", seconds: "s", minute: "min", minutes: "min", hour: "h", hours: "h", day: "dagar", days: "dagar", week: "veckor", weeks: "veckor", month: "mån", months: "mån" };
  const up = st.match(/^Up\s+(?:About\s+)?(an?|\d+)\s+(\w+)/i);
  if (up) { const n = /^an?$/i.test(up[1]) ? 1 : up[1]; return `körs · ${n} ${unit[up[2].toLowerCase()] || up[2]}`; }
  const ex = st.match(/^Exited\s+\((\d+)\)\s+(?:About\s+)?(an?|\d+)\s+(\w+)\s+ago/i);
  const when = ex ? ` · för ${/^an?$/i.test(ex[2]) ? 1 : ex[2]} ${unit[ex[3].toLowerCase()] || ex[3]} sedan` : "";
  if (c.state === "bad") return `stoppad oväntat${when}`;
  if (c.state === "running") return "körs";
  return `stoppad${when}`;
}
const cpuHistory = {};
const VM_STATE = { running: "körs", idle: "körs", paused: "pausad", shutoff: "avstängd", shutdown: "stängs av", crashed: "kraschad", pmsuspended: "viloläge" };
const segF = (kind, value) => `<span class="seg small" role="group" aria-label="Filtrera">${[["", "Alla"], ["running", "Körs"], ["stopped", "Stoppade"]].map(([v, l]) => `<button type="button" data-kind="${kind}" data-f="${v}" aria-pressed="${v === value}">${l}</button>`).join("")}</span>`;

const BELL = '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4z M10 20a2 2 0 0 0 4 0"/></svg>';
const bell = (kind, item, watched) => `<button type="button" class="bell${watched ? " on" : ""}" data-watch="${kind}" data-item="${esc(item)}" aria-pressed="${watched}" title="${watched ? "Övervakas: larmar om den stannar. Klicka för att sluta övervaka." : "Klicka för att övervaka och få larm om den stannar"}">${BELL}</button>`;
function unraidCard(u, i) {
  const st = u.status, name = st.name || u.hostname || `Unraid ${i + 1}`, f = urF(name);
  const w = u.watch || { containers: [], vms: [] };
  const sub = [u.version ? `Unraid ${u.version}` : null, u.uptime != null ? `drifttid ${fmtUptime(u.uptime)}` : null, "uppdateras var 30:e sekund"].filter(Boolean).join(" · ");
  const err = !st.ok && st.error ? `<div class="problem"><b>${esc(name)}</b>${esc(st.error)}</div>` : "";
  if (!st.ok && !u.disks) {
    return `<div class="urcard" id="unraid-card-${i}" data-name="${esc(name)}"><p class="cmetaline">${esc(sub)}</p>${err || '<div class="empty">Väntar på svar från servern …</div>'}</div>`;
  }
  const cap = u.capacity || {}, mem = u.memory || {}, cpu = u.cpu || {};
  const hist = (cpuHistory[name] = cpuHistory[name] || []);
  if (cpu.pct != null && (!hist.t || hist.t !== st.last)) { hist.push(cpu.pct); hist.t = st.last; if (hist.length > 30) hist.shift(); }
  const spark = hist.length > 1 ? `<svg class="spark" viewBox="0 0 120 28" preserveAspectRatio="none" aria-hidden="true"><path d="${hist.map((v, j) => `${j ? "L" : "M"}${(j / (hist.length - 1) * 120).toFixed(1)} ${(26 - v / 100 * 24).toFixed(1)}`).join(" ")}" fill="none" stroke="#7fb0d8" stroke-width="1.5"/></svg>` : "";
  const started = u.array_state === "STARTED";
  const par = u.parity_last;
  const parTxt = par ? `senaste paritetskontroll ${esc(new Date(par.date).toLocaleDateString("sv-SE", { timeZone: "Europe/Stockholm", day: "numeric", month: "short" }))}, ${par.errors ?? "?"} fel` : "";
  const parBad = (u.disks || []).some((x) => x.role === "parity" && x.status === "err");
  const roleName = (x) => x.role === "parity" ? (x.name === "parity" ? "Parity" : x.name.replace(/^parity/, "Parity ")) : x.role === "cache" ? (x.name === "cache" ? "Cache" : x.name) : x.name.replace(/^disk(\d+)$/, "Disk $1");
  const disks = (u.disks || []).map((x) => {
    const pill = x.status === "ok" ? "" : x.status === "idle" ? " idle" : x.status === "warn" ? " warn" : " err";
    return `<tr><td>${esc(roleName(x))}</td><td class="muted">${esc(x.device || "–")}</td><td><span class="pill${pill}">${esc(x.label)}</span></td><td>${x.used_pct != null ? `<span class="dbar"><i class="${x.used_pct >= 90 ? "hot" : ""}" style="width:${x.used_pct}%"></i></span>${fmt(x.used_pct, 0)} %` : '<span class="muted">–</span>'}</td><td class="r-right num">${fmtSize(x.size_bytes)}</td><td class="r-right num${x.temp >= 50 ? " hot" : ""}">${x.temp != null ? fmt(x.temp, 0) + " °C" : '<span class="muted">–</span>'}</td></tr>`;
  }).join("") || `<tr><td colspan="6" class="muted">Inga diskar i svaret från Unraid.</td></tr>`;
  const cs = u.containers || [];
  const cf = cs.filter((c) => !f.docker || (f.docker === "running" ? c.state === "running" : c.state !== "running"));
  const ctrs = cf.length ? cf.map((c) => `<div class="ctr"><i class="${c.state}"></i><span title="${esc(c.image || "")}">${esc(c.name)}</span><small class="${c.state === "bad" ? "bad" : ""}">${esc(dockerStatus(c))}</small>${bell("containers", c.name, w.containers.includes(c.name))}</div>`).join("")
    : `<div class="empty">${cs.length ? "Inga containrar matchar filtret." : "Inga containrar i svaret från Unraid."}</div>`;
  const vms = u.vms || [];
  const vf = vms.filter((v) => !f.vm || (f.vm === "running" ? v.running : !v.running));
  const vmHtml = !vms.length ? "" : `<div class="urhead vmhead"><h3>Virtuella maskiner · ${vms.filter((v) => v.running).length} av ${vms.length} körs${w.vms.length ? ` · ${w.vms.length} övervakas` : ""}</h3>${segF("vm", f.vm)}</div>
    <div class="ctrs">${vf.length ? vf.map((v) => `<div class="ctr"><i class="${v.running ? "running" : v.state === "crashed" ? "bad" : "stopped"}"></i><span>${esc(v.name)}</span><small>${esc(VM_STATE[v.state] || v.state || "")}</small>${bell("vms", v.name, w.vms.includes(v.name))}</div>`).join("") : '<div class="empty">Inga virtuella maskiner matchar filtret.</div>'}</div>`;
  return `<div class="urcard" id="unraid-card-${i}" data-name="${esc(name)}">
    <p class="cmetaline">${esc(sub)}</p>${err}
    <div class="urtop">
      <div class="urtile"><span class="l">Array</span><span class="v${started ? "" : " bad"}">${started ? "Startad" : esc((u.array_state || "Okänd").toLowerCase())}</span><span class="s">${parBad ? '<span class="pill err">Parity ogiltig</span>' : '<span class="pill">Parity giltig</span>'}${parTxt ? " · " + parTxt : ""}</span></div>
      <div class="urtile"><span class="l">Lagring</span><span class="v num">${cap.used != null ? fmt(cap.used / 1e12, 1) : "–"}<small>av ${cap.total != null ? fmt(cap.total / 1e12, 0) : "–"} TB</small></span><div class="meter"><i class="${cap.pct >= 90 ? "hot" : ""}" style="width:${cap.pct || 0}%"></i></div><span class="s">${cap.free != null ? fmt(cap.free / 1e12, 1) + " TB ledigt" : ""}${cap.pct != null ? " · " + fmt(cap.pct, 0) + " %" : ""}</span></div>
      <div class="urtile"><span class="l">Processor</span><span class="v num">${cpu.pct != null ? fmt(cpu.pct, 0) : "–"}<small>%</small></span>${spark}<span class="s">${esc([cpu.brand, cpu.threads ? cpu.threads + " trådar" : null].filter(Boolean).join(" · "))}</span></div>
      <div class="urtile"><span class="l">Minne</span><span class="v num">${mem.used != null ? fmt(mem.used / GB, 1) : "–"}<small>av ${mem.total != null ? fmt(mem.total / GB, 0) : "–"} GB</small></span><div class="meter"><i style="width:${mem.pct || 0}%"></i></div><span class="s">${mem.pct != null ? fmt(mem.pct, 0) + " %" : ""}</span></div>
    </div>
    <div class="urgrid">
      <div class="urcol"><h3>Diskar</h3><div class="tablebox"><table><thead><tr><th>Disk</th><th>Enhet</th><th>Status</th><th>Använt</th><th class="r-right">Storlek</th><th class="r-right">Temp</th></tr></thead><tbody>${disks}</tbody></table></div></div>
      <div class="urcol"><div class="urhead"><h3>Docker · ${cs.filter((c) => c.state === "running").length} av ${cs.length} körs${w.containers.length ? ` · ${w.containers.length} övervakas` : ""}</h3>${segF("docker", f.docker)}</div><div class="ctrs">${ctrs}</div>${vmHtml}</div>
    </div></div>`;
}
/* flikar: Översikt (när det finns flera servrar) och en flik per server */
const tabStore = (k, def) => { try { return localStorage.getItem(k) ?? def; } catch (e) { return def; } };
const tabSave = (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* ingen lagring */ } };
let urTab = tabStore("urTab", "overview");
let plexTab = tabStore("plexTab", "overview");
const LVL_CLS = (lv) => (lv === "ok" ? "ok" : lv === "warn" ? "warn" : lv === "err" ? "err" : "off");
function cardTabs(title, attr, tabs, cur) {
  return `<div class="ctabs" role="tablist" aria-label="${esc(title)}"><span class="ctitle">${esc(title)}</span>${tabs.map((t) =>
    `<button type="button" role="tab" ${attr}="${esc(t.id)}" aria-selected="${t.id === cur}">${t.dot ? `<i class="cdot ${t.dot}"></i>` : ""}${esc(t.label)}${t.badge != null ? `<span class="cbadge">${esc(t.badge)}</span>` : ""}</button>`).join("")}</div>`;
}
const urName = (u, i) => u.status.name || u.hostname || `Unraid ${i + 1}`;
const urLevel = (u) => (!u.status.ok ? (u.status.error ? "err" : "off") : LVL_CLS(u.status.level));
function urOverview(u, i) {
  const name = urName(u, i), lv = urLevel(u), cap = u.capacity || {}, mem = u.memory || {}, cpu = u.cpu || {};
  const hot = (u.disks || []).filter((x) => x.temp != null).sort((a, b) => b.temp - a.temp)[0];
  const cs = u.containers || [], vms = u.vms || [];
  const run = cs.filter((c) => c.state === "running").length, bad = cs.filter((c) => c.state === "bad").length;
  const vrun = vms.filter((v) => v.running).length;
  const roleName = (x) => x.role === "parity" ? "Parity" : x.role === "cache" ? (x.name === "cache" ? "Cache" : x.name) : x.name.replace(/^disk(\d+)$/, "Disk $1");
  const meter = (label, pct, val, hotc) => `<div class="ovm"><span class="l">${label}</span><div class="meter"><i class="${hotc ? "hot" : ""}" style="width:${Math.max(0, Math.min(100, pct || 0))}%"></i></div><span class="v">${val}</span></div>`;
  const prob = !u.status.ok ? `<span class="ovprob err">${esc(u.status.error || "Väntar på svar")}</span>`
    : lv === "ok" ? '<span class="ovprob ok">✓ Allt ser bra ut</span>' : `<span class="ovprob ${lv}">⚠ ${esc(u.status.detail || "")}</span>`;
  const sub = [u.version ? `Unraid ${u.version}` : null, u.uptime != null ? `drifttid ${fmtUptime(u.uptime)}` : null].filter(Boolean).join(" · ");
  return `<button type="button" class="ovbox ${lv}" data-urtab="${esc(name)}" title="Visa ${esc(name)}">
    <span class="ovh"><b><i class="cdot ${lv}"></i>${esc(name)}</b><span>${esc(sub)}</span></span>${prob}
    ${u.status.ok || u.disks ? `<span class="ovmeters">
      ${meter("Lagring", cap.pct, `${cap.used != null ? fmt(cap.used / 1e12, 1) : "–"} <small>av ${cap.total != null ? fmt(cap.total / 1e12, 0) : "–"} TB · ${fmt(cap.pct || 0, 0)} %</small>`, cap.pct >= 90)}
      ${meter("Processor", cpu.pct, `${cpu.pct != null ? fmt(cpu.pct, 0) : "–"} % <small>${cpu.threads ? "· " + cpu.threads + " trådar" : ""}</small>`)}
      ${meter("Minne", mem.pct, `${mem.used != null ? fmt(mem.used / GB, 1) : "–"} <small>av ${mem.total != null ? fmt(mem.total / GB, 0) : "–"} GB · ${fmt(mem.pct || 0, 0)} %</small>`)}
      ${hot ? meter("Varmast", hot.temp / 60 * 100, `${fmt(hot.temp, 0)} °C <small>· ${esc(roleName(hot))}</small>`, hot.temp >= 50) : ""}
    </span>
    <span class="ovcounts"><span><b>${u.array_state === "STARTED" ? "Startad" : esc((u.array_state || "Okänd").toLowerCase())}</b>array</span>
      <span class="${bad ? "bad" : ""}"><b>${run}<small>av ${cs.length}</small></b>dockers körs${bad ? ` · ${bad} stoppad oväntat` : ""}</span>
      <span><b>${vms.length ? `${vrun}<small>av ${vms.length}</small>` : "–"}</b>${vms.length ? "VM körs" : "inga VM"}</span></span>` : ""}
    <span class="ovlink">Visa ${esc(name)} →</span></button>`;
}
function renderUnraids(d) {
  const list = d.unraids || [];
  const box = $("unraid-cards");
  if (!list.length) { box.innerHTML = ""; return; }
  const names = list.map(urName), multi = list.length > 1;
  let cur = urTab;
  if ((cur === "overview" && !multi) || (cur !== "overview" && !names.includes(cur))) cur = multi ? "overview" : names[0];
  const tabs = [...(multi ? [{ id: "overview", label: "Översikt" }] : []), ...list.map((u, i) => ({ id: names[i], label: names[i], dot: urLevel(u) }))];
  const idx = names.indexOf(cur);
  box.innerHTML = `<section class="card urbox" id="unraid-box"><div class="chead">${cardTabs("Unraid", "data-urtab", tabs, cur)}<span class="cmeta">uppdateras var 30:e sekund</span></div>
    ${cur === "overview" ? `<div class="ovgrid">${list.map(urOverview).join("")}</div>` : unraidCard(list[idx], idx)}</section>`;
}
window.unraidSelect = (i) => {
  const list = (state.data && state.data.unraids) || [];
  if (!list[i]) return;
  const name = urName(list[i], i);
  if (urTab !== name) { urTab = name; tabSave("urTab", name); renderUnraids(state.data); }
};
$("unraid-cards").addEventListener("click", async (e) => {
  const tb = e.target.closest("[data-urtab]");
  if (tb) { urTab = tb.dataset.urtab; tabSave("urTab", urTab); renderUnraids(state.data); return; }
  const wb = e.target.closest("[data-watch]");
  if (wb) {
    const server = wb.closest(".urcard").dataset.name, on = wb.getAttribute("aria-pressed") !== "true";
    wb.disabled = true;
    try {
      const r = await fetch("api/unraid/watch", { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ server, kind: wb.dataset.watch, name: wb.dataset.item, on }) });
      if (!r.ok) throw new Error(r.status);
    } catch (err) { $("problems").innerHTML = `<div class="problem"><b>Övervakning</b>Kunde inte spara (${esc(err.message)}).</div>`; }
    load();
    return;
  }
  const b = e.target.closest("[data-kind]"); if (!b) return;
  const name = b.closest(".urcard").dataset.name;
  urF(name)[b.dataset.kind] = b.dataset.f;
  try { localStorage.setItem("urFilters", JSON.stringify(urFilter)); } catch (err) { /* ingen lagring */ }
  if (state.data) renderUnraids(state.data);
});

/* ── Plex ── */
const msToTime = (ms) => {
  if (ms == null) return "–";
  const t = Math.floor(ms / 1000), h = Math.floor(t / 3600), m = Math.floor(t / 60) % 60, s = t % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
};
const POSTER_BG = ["#2f5d3a,#0f2416", "#5a3a7a,#1b1030", "#7a4a1d,#2a1406", "#1f4a6e,#0a1a2a", "#6e1f3a,#2a0a16", "#3a5a6e,#0f1a24"];
function initials(t) { return (t || "?").replace(/^(The|En|Ett|Den|Det) /i, "").split(/[\s:–-]+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase(); }
const LIB_TYPE = { movie: "Filmer", show: "Serier", artist: "Musik", photo: "Foton" };
function libCount(l) {
  const n = (v) => (v == null ? "–" : fmt(v, 0));
  if (l.type === "show") return `${n(l.count)}<small>serier${l.count2 != null ? ` · ${n(l.count2)} avsnitt` : ""}</small>`;
  if (l.type === "artist") return `${n(l.count)}<small>artister${l.count2 != null ? ` · ${n(l.count2)} låtar` : ""}</small>`;
  if (l.type === "movie") return `${n(l.count)}<small>filmer</small>`;
  return `${n(l.count)}<small>objekt</small>`;
}
function streamHTML(x) {
  const bg = POSTER_BG[[...x.title].reduce((a, c) => a + c.charCodeAt(0), 0) % POSTER_BG.length];
  const art = x.thumb ? `<img src="api/plex/thumb/${x.server}?path=${encodeURIComponent(x.thumb)}" alt="" loading="lazy" onerror="this.remove()">` : "";
  const dec = x.decision === "transcode"
    ? `<span class="tag tc">Omkodning${x.video && x.target ? ` ${esc(x.video.split(" · ")[0])} → ${esc(x.target)}` : ""}${x.hw ? " (HW)" : ""}</span>`
    : `<span class="tag dp">${x.decision === "stream" ? "Direktström" : "Direktuppspelning"}</span>`;
  const where = x.local ? "" : `<span class="tag remote">Utanför hemmet${x.country ? " · " + esc(countryName(x.country)) : ""}</span>`;
  const quality = [x.decision !== "transcode" && x.video ? x.video : null, x.audio].filter(Boolean).map((q) => `<span class="tag">${esc(q)}</span>`).join("");
  const pct = x.duration ? Math.min(100, (x.offset || 0) / x.duration * 100) : 0;
  const st = x.state === "paused" ? `<span class="state pause">❚❚ Pausad · ${msToTime(x.offset)} / ${msToTime(x.duration)}</span>`
    : x.state === "buffering" ? `<span class="state">Buffrar · ${msToTime(x.offset)} / ${msToTime(x.duration)}</span>`
    : `<span class="state play">▶ ${msToTime(x.offset)} / ${msToTime(x.duration)}</span>`;
  return `<article class="stream"><div class="poster" style="background:linear-gradient(160deg,${bg})"><span>${x.type === "track" ? "♪" : esc(initials(x.title))}</span>${art}</div>
    <div class="sinfo"><div class="stitle"><b title="${esc(x.title)}">${esc(x.title)}</b><span>${esc(x.label)}</span></div>
    <div class="ssub">${esc(x.subtitle || "")}</div>
    <div class="swho"><span class="u">${esc(x.user)}</span><span>${esc([x.device, x.product].filter(Boolean).join(" · "))}</span></div>
    <div class="tags">${dec}${where}${quality}${x.bandwidth_kbps ? `<span class="tag num">${fmt(x.bandwidth_kbps / 1000, 1)} Mbit/s</span>` : ""}</div>
    <div class="prog"><div class="bar">${x.buffer ? `<em style="width:${Math.min(100, x.buffer)}%"></em>` : ""}<i style="width:${pct}%"></i></div>${st}</div></div></article>`;
}
function plexTotals(ss) {
  return { streams: ss.length, direct: ss.filter((x) => x.decision !== "transcode").length, transcode: ss.filter((x) => x.decision === "transcode").length,
    remote: ss.filter((x) => !x.local).length, mbit: ss.reduce((a, x) => a + (x.bandwidth_kbps || 0), 0) / 1000 };
}
const plexLevel = (s) => (s.ok ? "ok" : s.error ? "err" : "off");
function plexServerPane(s, ss) {
  const t = plexTotals(ss), libs = s.libraries || [];
  const libHtml = libs.length ? libs.map((l) => `<div class="plib${l.refreshing ? " scan" : ""}">
      <span class="plt"><span><span class="pty">${esc(LIB_TYPE[l.type] || l.type || "")}</span><b>${esc(l.title)}</b></span>
      ${l.refreshing ? '<button type="button" class="btn" disabled><i class="spin"></i>Skannar …</button>' : `<button type="button" class="btn px" data-pxref="${s.index}" data-sec="${esc(l.key)}" title="Be Plex skanna ${esc(l.title)} efter nya och borttagna filer">⟳ Uppdatera</button>`}</span>
      <span class="pv num">${libCount(l)}</span>
      ${l.refreshing && l.progress != null ? `<div class="meter"><i style="width:${Math.min(100, l.progress)}%;background:var(--plex)"></i></div>` : ""}
      <span class="ps">${l.refreshing ? (l.progress != null ? `skannar · ${fmt(l.progress, 0)} %` : "skannar …") : l.scanned ? `senast skannad ${phDate(l.scanned)}` : ""}</span></div>`).join("")
    : `<div class="empty">${s.ok ? "Hämtar biblioteken …" : "Inga bibliotek att visa."}</div>`;
  return `<p class="cmetaline">${s.version ? "Plex Media Server " + esc(String(s.version).split("-")[0]) + " · " : ""}${s.ok ? "svarar" : "svarar inte"}</p>
    ${!s.ok && s.error ? `<div class="problem"><b>${esc(s.name)}</b>${esc(s.error)}</div>` : ""}
    <div class="psec"><h3>Bibliotek ${libs.length ? `<button type="button" class="btn px" data-pxref="${s.index}" data-sec="">⟳ Uppdatera alla</button>` : ""}</h3><div class="plibs">${libHtml}</div></div>
    <div class="psec"><h3>Strömmar just nu <small>${t.streams} ${t.streams === 1 ? "ström" : "strömmar"} · ${t.transcode} omkodning · ${fmt(t.mbit, 1)} Mbit/s${t.remote ? ` · ${t.remote} utanför hemmet` : ""}</small></h3>
      <div class="streams">${ss.length ? ss.map(streamHTML).join("") : '<div class="empty">Inga strömmar på den här servern just nu.</div>'}</div></div>`;
}
// Översikt: som tidigare – totalt för alla servrar och alla strömmar
function plexAll(p) {
  const t = plexTotals(p.sessions);
  return `${p.servers.filter((x) => !x.ok && x.error).map((x) => `<div class="problem"><b>${esc(x.name)}</b>${esc(x.error)}</div>`).join("")}
    <div class="psec"><h3>Strömmar just nu <small>${t.streams} ${t.streams === 1 ? "ström" : "strömmar"} totalt · ${t.direct} direkt · ${t.transcode} omkodning · ${fmt(t.mbit, 1)} Mbit/s${t.remote ? ` · ${t.remote} utanför hemmet` : ""}</small></h3>
      <div class="streams">${p.sessions.length ? p.sessions.map(streamHTML).join("") : '<div class="empty">Inga strömmar från Plex just nu.</div>'}</div></div>`;
}
function renderPlex(d) {
  const p = d.plex || { servers: [], sessions: [] };
  const card = $("plex-card");
  card.hidden = !p.servers.length;
  if (!p.servers.length) return;
  const multi = p.servers.length > 1;
  let cur = plexTab;
  if ((cur === "overview" && !multi) || (cur !== "overview" && !p.servers.some((s) => String(s.index) === cur))) cur = multi ? "overview" : String(p.servers[0].index);
  const by = (s) => p.sessions.filter((x) => x.server === s.index);
  const tabs = [...(multi ? [{ id: "overview", label: "Översikt" }] : []), ...p.servers.map((s) => ({ id: String(s.index), label: s.name, dot: plexLevel(s), badge: by(s).length }))];
  $("plex-tabs").innerHTML = cardTabs("Plex", "data-pxtab", tabs, cur);
  const s = p.servers.find((x) => String(x.index) === cur);
  $("plex-pane").innerHTML = cur === "overview" ? plexAll(p) : plexServerPane(s, by(s));
}
window.plexSelect = (i) => { plexTab = String(i); tabSave("plexTab", plexTab); if (state.data) renderPlex(state.data); };
$("plex-card").addEventListener("click", async (e) => {
  const tb = e.target.closest("[data-pxtab]");
  if (tb) { plexTab = tb.dataset.pxtab; tabSave("plexTab", plexTab); renderPlex(state.data); return; }
  const rb = e.target.closest("[data-pxref]");
  if (!rb) return;
  rb.disabled = true;
  try {
    const r = await fetch(`api/plex/${rb.dataset.pxref}/refresh`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ section: rb.dataset.sec || null }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || r.status);
  } catch (err) {
    $("plex-err").innerHTML = `<div class="problem"><b>Plex</b>${esc(err.message)}</div>`;
  }
  load();
});

/* ── land: vilka adresser och klienter ── */
const PORTS = {
  20: "FTP", 21: "FTP", 22: "SSH", 25: "SMTP", 53: "DNS", 80: "HTTP", 110: "POP3", 123: "NTP", 143: "IMAP", 443: "HTTPS",
  465: "SMTP", 587: "SMTP", 853: "DNS-över-TLS", 993: "IMAP", 995: "POP3", 1194: "OpenVPN", 1883: "MQTT", 3074: "Xbox Live",
  3389: "RDP", 3478: "STUN", 3479: "STUN", 3480: "STUN", 5060: "SIP", 5222: "XMPP", 5223: "Apple push", 5228: "Google push",
  5353: "mDNS", 8080: "HTTP", 8443: "HTTPS", 8883: "MQTT", 9339: "spel", 19302: "Google Meet", 25565: "Minecraft",
  27015: "Steam", 32400: "Plex", 51413: "BitTorrent", 51820: "WireGuard",
};
async function openCountry(cc) {
  const dlg = $("country");
  $("cd-title").textContent = countryName(cc);
  const per = state.period === "live" ? "24h" : state.period;
  $("cd-sub").textContent = `Hämtar ${PERIOD_LABEL[per]} …`;
  $("cd-clients").innerHTML = ""; $("cd-remotes").innerHTML = "";
  if (dlg.showModal) { if (!dlg.open) dlg.showModal(); } else dlg.setAttribute("open", "");
  try {
    const r = await fetch(`api/country/${encodeURIComponent(cc)}?period=${per}`, { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    const d = await r.json();
    const active = d.remotes.reduce((n, x) => n + x.active, 0);
    $("cd-sub").textContent = `${d.remotes.length} adresser · ${d.clients.length} klienter · ${active} aktiva anslutningar just nu · ${PERIOD_LABEL[d.period]}`;
    const dirPill = (dir) => `<span class="dirpill ${dir}">${dir === "out" ? "inifrån" : "utifrån"}</span>`;
    $("cd-clients").innerHTML = d.clients.length ? `<table><thead><tr><th>Klient</th><th>IP-adress</th><th class="r-right">Startad inifrån</th><th class="r-right">Startad utifrån</th><th class="r-right">Anslutningar</th></tr></thead><tbody>${
      d.clients.map((c) => `<tr class="pickable" data-client="${esc(c.id)}"><td>${esc(c.name)}</td><td class="ip">${esc(c.ip || "–")}</td><td class="r-right num">${c.out ? fmtBytes(c.out) : "–"}</td><td class="r-right num">${c.in ? fmtBytes(c.in) : "–"}</td><td class="r-right num">${fmt(c.conns, 0)}</td></tr>`).join("")
    }</tbody></table>` : `<p class="muted">Ingen klient har pratat med ${esc(countryName(cc))} under perioden.</p>`;
    $("cd-remotes").innerHTML = d.remotes.length ? `<table><thead><tr><th>Adress</th><th>Värdnamn</th><th>Organisation</th><th>Port</th><th>Riktning</th><th>Klient</th><th class="r-right">Data</th><th class="r-right">Anslutn.</th><th>Senast</th></tr></thead><tbody>${
      d.remotes.map((x) => `<tr><td class="ip">${esc(x.ip)}${x.active ? ' <span class="live" title="Aktiv anslutning just nu"></span>' : ""}</td><td class="wrapcell">${esc(x.host || "–")}</td><td class="wrapcell">${esc(x.org || "–")}</td><td class="num">${x.ports.map((p) => esc(p) + (PORTS[p] ? ` <small class="muted">${PORTS[p]}</small>` : "")).join(", ") || "–"}</td><td>${dirPill(x.dir)}</td><td class="wrapcell">${x.clients.map((c) => esc(c.name)).join(", ")}</td><td class="r-right num">${x.bytes ? fmtBytes(x.bytes) : "–"}</td><td class="r-right num">${fmt(x.conns, 0)}</td><td class="muted">${x.active ? "nu" : ago(x.last)}</td></tr>`).join("")
    }</tbody></table>` : `<p class="muted">Inga adresser sparade än. Adresser sparas från och med den här versionen av dashboarden.</p>`;
  } catch (e) {
    $("cd-sub").textContent = `Kunde inte hämta detaljer (${e.message}).`;
  }
}
$("ctrylist").addEventListener("click", (e) => { const r = e.target.closest("[data-cc]"); if (r) openCountry(r.dataset.cc); });
$("map").addEventListener("click", (e) => { const g = e.target.closest("[data-cc]"); if (g) openCountry(g.getAttribute("data-cc")); });
$("cd-close").addEventListener("click", () => $("country").close());
$("cd-clients").addEventListener("click", (e) => {
  const r = e.target.closest("[data-client]"); if (!r) return;
  state.mapFilter = r.dataset.client; $("country").close(); render();
});

/* ── inställningar ── */
const DAYS = ["Mån", "Tis", "Ons", "Tor", "Fre", "Lör", "Sön"];
// Egna väljare för timme och minut, så att tiden alltid visas i 24-timmarsformat oavsett webbläsarens språk
function timePicker(id) {
  const box = $(id);
  const opt = (n) => `<option value="${String(n).padStart(2, "0")}">${String(n).padStart(2, "0")}</option>`;
  box.innerHTML = `<select aria-label="Timme">${Array.from({ length: 24 }, (_, i) => opt(i)).join("")}</select><span>:</span><select aria-label="Minut">${Array.from({ length: 12 }, (_, i) => opt(i * 5)).join("")}</select>`;
}
function setTime(id, hm) {
  const [h, m] = (hm || "00:00").split(":");
  const [sh, sm] = $(id).querySelectorAll("select");
  sh.value = h;
  if (![...sm.options].some((o) => o.value === m)) sm.insertAdjacentHTML("beforeend", `<option value="${m}">${m}</option>`);
  sm.value = m;
}
function getTime(id) { const [sh, sm] = $(id).querySelectorAll("select"); return `${sh.value}:${sm.value}`; }
timePicker("s-qstart"); timePicker("s-qend");
let alertKinds = [];
function confirmHint() {
  const i = +$("s-interval").value || 0, n = +$("s-count").value || 0;
  const total = Math.max(0, (n - 1) * i);
  $("s-confirm-hint").textContent = n <= 1 ? "Larmar direkt vid första misslyckade kontrollen."
    : `Ett fel måste bestå i ungefär ${total >= 60 ? fmt(total / 60, 1) + " min" : total + " s"} (${n} kontroller med ${i} s mellanrum) innan det larmar. En lyckad kontroll börjar om räkningen.`;
}
$("s-interval").addEventListener("input", confirmHint);
$("s-count").addEventListener("input", confirmHint);
document.querySelector(".stabs").addEventListener("click", (e) => {
  const b = e.target.closest("[data-tab]"); if (!b) return;
  showTab(b.dataset.tab);
});
let recips = [], mailConfigured = false, serverList = [], nextRid = 1;
let mtxRows = [], mtxState = {}, mtxOpen = new Set();
const newRid = () => "r" + nextRid++;
function syncRecips() {
  $("s-recips").querySelectorAll(".recip").forEach((row) => {
    const r = recips.find((x) => x.rid === row.dataset.rid);
    if (r) { r.email = row.querySelector(".r-email").value.trim(); r.name = row.querySelector(".r-name").value.trim(); }
  });
}
function renderRecips() {
  $("s-recips").innerHTML = recips.length ? recips.map((r) => `<div class="recip" data-rid="${r.rid}">
      <div class="rtop"><input type="email" class="r-email" value="${esc(r.email)}" placeholder="namn@exempel.se" aria-label="E-postadress" required>
        <input type="text" class="r-name" value="${esc(r.name || "")}" placeholder="Namn (valfritt)" aria-label="Namn">
        <button type="button" class="btn" data-rtest="${r.rid}" ${mailConfigured ? "" : "disabled"}>Testa</button>
        <button type="button" class="btn" data-rdel="${r.rid}" aria-label="Ta bort mottagare">Ta bort</button></div></div>`).join("")
    : '<p class="muted">Inga mottagare. Lägg till en för att få larm via e-post.</p>';
}
function addRecip() {
  syncRecips();
  const rid = newRid();
  recips.push({ rid, email: "", name: "" });
  // en ny adress får de kritiska larmen från början
  for (const row of mtxRows) if (row.critical) for (const k of [row.key, ...row.children.map((c) => c.key)]) mtxState[k].emails.add(rid);
  renderRecips();
  const rows = $("s-recips").querySelectorAll(".r-email"); rows[rows.length - 1].focus();
}
$("s-add-recip").addEventListener("click", addRecip);
$("s-recips").addEventListener("click", async (e) => {
  const b = e.target.closest("button"); if (!b) return;
  syncRecips();
  if (b.dataset.rdel) {
    recips = recips.filter((r) => r.rid !== b.dataset.rdel);
    for (const st of Object.values(mtxState)) st.emails.delete(b.dataset.rdel);
    renderRecips(); return;
  }
  if (b.dataset.rtest) {
    const to = (recips.find((r) => r.rid === b.dataset.rtest) || {}).email;
    if (!to) return;
    $("s-msg").textContent = `Skickar testmejl till ${to} …`; $("s-msg").className = "formmsg";
    try {
      const r = await fetch("api/test-email", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ to }) });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || r.status);
      $("s-msg").textContent = `Testmejl skickat till ${to}.`; $("s-msg").className = "formmsg ok";
    } catch (err) { $("s-msg").textContent = err.message; $("s-msg").className = "formmsg err"; }
  }
});

/* ── larmtabellen: banner, Slack och en kolumn per e-postadress ── */
function mtxCols() {
  return [{ id: "banner", label: "Banner" }, { id: "slack", label: "Slack" },
    ...recips.map((r) => ({ id: r.rid, label: r.name || (r.email ? r.email.split("@")[0] : "ny adress"), title: r.email || "Ny adress, fyll i under Aviseringar", mail: true }))];
}
const mtxGet = (key, col) => { const st = mtxState[key]; return col === "banner" || col === "slack" ? st[col] : st.emails.has(col); };
function mtxSet(key, col, on) {
  const st = mtxState[key];
  if (col === "banner" || col === "slack") st[col] = on; else if (on) st.emails.add(col); else st.emails.delete(col);
}
function renderMatrix() {
  const cols = mtxCols();
  let html = `<thead><tr><th>Larm</th>${cols.map((c) => `<th class="${c.mail ? "mail" : ""}" title="${esc(c.title || "")}">${esc(c.label)}</th>`).join("")}<th><button type="button" class="linkbtn" data-mtxadd>+ e-post</button></th></tr></thead><tbody>`;
  let group = null;
  const box = (key, col, label, ind) => {
    const on = ind ? ind.all : mtxGet(key, col);
    return `<td class="c"><input type="checkbox" data-mk="${esc(key)}" data-mc="${col}" ${on ? "checked" : ""} ${ind && ind.some && !ind.all ? "data-ind" : ""} aria-label="${esc(label)}"></td>`;
  };
  for (const row of mtxRows) {
    if (row.group !== group) { group = row.group; html += `<tr class="mgrp"><td colspan="${cols.length + 2}">${esc(group)}</td></tr>`; }
    const kids = row.children;
    const open = mtxOpen.has(row.key);
    const canOpen = kids.length || row.watch;
    html += `<tr class="mrow${open ? " open" : ""}"><td>${canOpen ? `<button type="button" class="mchev" data-mopen="${esc(row.key)}" aria-expanded="${open}">${open ? "▾" : "▸"}</button>` : '<span class="mchev"></span>'}${esc(row.label)}${row.confirmed ? ' <span class="muted" title="Bekräftas med upprepade kontroller">↻</span>' : ""}${row.critical ? ' <span class="kcrit">kritiskt</span>' : ""}${kids.length ? ` <span class="muted">· ${kids.length}</span>` : ""}</td>${cols.map((c) => {
      if (!kids.length) return box(row.key, c.id, `${c.label}: ${row.label}`);
      const v = kids.map((k) => mtxGet(k.key, c.id));
      return box(row.key, c.id, `${c.label}: ${row.label}`, { all: v.every(Boolean), some: v.some(Boolean) });
    }).join("")}<td></td></tr>`;
    if (!open) continue;
    for (const k of kids) {
      html += `<tr class="msub"><td><span class="mtree">└</span>${esc(k.label)}${k.item ? ` <button type="button" class="linkbtn mstop" data-unwatch="${esc(k.key)}" title="Sluta bevaka ${esc(k.item)}">✕</button>` : ""}</td>${cols.map((c) => box(k.key, c.id, `${c.label}: ${k.label}`)).join("")}<td></td></tr>`;
    }
    if (row.watch) {
      const opts = Object.entries(row.available || {}).flatMap(([srv, items]) => items.map((it) => `<option value="${esc(srv)}|${esc(it)}">${esc(srv)} · ${esc(it)}</option>`));
      html += `<tr class="msub"><td colspan="${cols.length + 2}"><span class="mtree">└</span>${opts.length
        ? `<select class="mwatch" data-addwatch="${esc(row.key)}" aria-label="Börja bevaka"><option value="">🔔 + bevaka ${row.watch === "vms" ? "en virtuell maskin" : "en container"} …</option>${opts.join("")}</select>`
        : `<span class="muted">Inga fler ${row.watch === "vms" ? "virtuella maskiner" : "containrar"} att bevaka.</span>`}</td></tr>`;
    }
  }
  $("s-mtx").innerHTML = html + "</tbody>";
  $("s-mtx").querySelectorAll("[data-ind]").forEach((x) => { x.indeterminate = true; });
}
$("s-mtx").addEventListener("change", async (e) => {
  const t = e.target;
  if (t.matches("[data-mk]")) {
    const key = t.dataset.mk, col = t.dataset.mc;
    const row = mtxRows.find((r) => r.key === key);
    if (row && row.children.length) {
      for (const k of [row.key, ...row.children.map((c) => c.key)]) mtxSet(k, col, t.checked);
    } else {
      mtxSet(key, col, t.checked);
      const parent = mtxRows.find((r) => r.children.some((c) => c.key === key));
      if (parent) mtxSet(parent.key, col, parent.children.every((c) => mtxGet(c.key, col)));
    }
    renderMatrix();
    return;
  }
  if (t.matches("[data-addwatch]") && t.value) {
    const row = mtxRows.find((r) => r.key === t.dataset.addwatch);
    const [server, ...rest] = t.value.split("|"); const item = rest.join("|");
    try {
      const r = await fetch("api/unraid/watch", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ server, kind: row.watch, name: item, on: true }) });
      if (!r.ok) throw new Error(r.status);
    } catch (err) { $("s-msg").textContent = `Kunde inte börja bevaka ${item}.`; $("s-msg").className = "formmsg err"; return; }
    const key = `${row.key}|${server}|${item}`;
    const p = mtxState[row.key];
    mtxState[key] = { banner: p.banner, slack: p.slack, emails: new Set(p.emails) };
    row.children.push({ key, label: `${server} · ${item}`, source: `${server}|${item}`, server, item });
    row.children.sort((a, b) => a.label.localeCompare(b.label, "sv"));
    row.available[server] = (row.available[server] || []).filter((x) => x !== item);
    renderMatrix();
  }
});
$("s-mtx").addEventListener("click", async (e) => {
  const o = e.target.closest("[data-mopen]");
  if (o) { const k = o.dataset.mopen; if (mtxOpen.has(k)) mtxOpen.delete(k); else mtxOpen.add(k); renderMatrix(); return; }
  if (e.target.closest("[data-mtxadd]")) { showTab("notify"); addRecip(); return; }
  const u = e.target.closest("[data-unwatch]");
  if (u) {
    const key = u.dataset.unwatch;
    const row = mtxRows.find((r) => r.children.some((c) => c.key === key));
    const k = row.children.find((c) => c.key === key);
    try {
      const r = await fetch("api/unraid/watch", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ server: k.server, kind: row.watch, name: k.item, on: false }) });
      if (!r.ok) throw new Error(r.status);
    } catch (err) { $("s-msg").textContent = `Kunde inte sluta bevaka ${k.item}.`; $("s-msg").className = "formmsg err"; return; }
    row.children = row.children.filter((c) => c.key !== key);
    delete mtxState[key];
    (row.available[k.server] = row.available[k.server] || []).push(k.item);
    row.available[k.server].sort();
    renderMatrix();
  }
});
function showTab(tab) {
  document.querySelectorAll(".stabs [data-tab]").forEach((x) => x.setAttribute("aria-selected", x.dataset.tab === tab));
  document.querySelectorAll(".spane").forEach((p) => { p.hidden = p.dataset.pane !== tab; });
  if (tab === "alerts") { syncRecips(); renderMatrix(); }
}
function readRoutes() {
  syncRecips();
  const mail = Object.fromEntries(recips.filter((r) => r.email).map((r) => [r.rid, r.email]));
  const routes = {}, alertKindsOut = {}, channels = {};
  for (const row of mtxRows) {
    for (const k of [row, ...row.children]) {
      const st = mtxState[k.key];
      routes[k.key] = { banner: st.banner, slack: st.slack, emails: [...st.emails].map((rid) => mail[rid]).filter(Boolean) };
    }
    const all = [row, ...row.children].map((k) => routes[k.key]);
    alertKindsOut[row.kind] = all.some((x) => x.banner);
    channels[row.kind] = { email: all.some((x) => x.emails.length), slack: all.some((x) => x.slack) };
  }
  const recipients = recips.filter((r) => r.email || r.name).map((r) => ({ email: r.email, name: r.name,
    kinds: mtxRows.filter((row) => [row, ...row.children].some((k) => mtxState[k.key].emails.has(r.rid))).map((row) => row.kind), servers: null }));
  return { routes, alert_kinds: alertKindsOut, channels, recipients };
}
async function openSettings() {
  const dlg = $("settings");
  $("s-msg").textContent = ""; $("s-msg").className = "formmsg";
  try {
    const [r, rk] = await Promise.all([fetch("api/settings", { cache: "no-store" }), fetch("api/alert-kinds", { cache: "no-store" })]);
    const j = await r.json();
    alertKinds = await rk.json();
    const st = j.settings;
    $("s-retention").value = st.retention_days; $("s-factor").value = st.anomaly_factor; $("s-mingb").value = st.anomaly_min_gb;
    $("s-prio").value = String(st.suricata_min_priority); $("s-pfb").value = st.pfblocker_threshold;
    $("s-interval").value = st.confirm_interval; $("s-count").value = st.confirm_count; confirmHint();
    $("s-quiet").checked = !!st.quiet_enabled; setTime("s-qstart", st.quiet_start); setTime("s-qend", st.quiet_end);
    $("s-qdays").innerHTML = DAYS.map((d, i) => `<label class="day"><input type="checkbox" value="${i}" ${st.quiet_days.includes(i) ? "checked" : ""}><span>${d}</span></label>`).join("");
    $("s-qcrit").checked = !!st.quiet_allow_critical; $("s-qdigest").checked = !!st.quiet_digest;

    mailConfigured = j.email.enabled;
    serverList = [...(j.servers?.plex || []).map((n) => ({ name: n, type: "Plex" })), ...(j.servers?.unraid || []).map((n) => ({ name: n, type: "Unraid" })), ...(j.servers?.pihole || []).map((n) => ({ name: n, type: "Pi-hole" }))];
    recips = (st.recipients ? st.recipients.map((r) => ({ email: r.email, name: r.name || "" })) : j.email.to.map((email) => ({ email, name: "" })))
      .map((r) => ({ ...r, rid: newRid() }));
    const ridOf = Object.fromEntries(recips.map((r) => [r.email.toLowerCase(), r.rid]));
    mtxRows = j.matrix || [];
    mtxState = {};
    for (const row of mtxRows) for (const k of [row, ...row.children]) {
      mtxState[k.key] = { banner: !!k.route.banner, slack: !!k.route.slack, emails: new Set((k.route.emails || []).map((e) => ridOf[e.toLowerCase()]).filter(Boolean)) };
    }
    renderRecips();
    renderMatrix();
    $("s-slack").innerHTML = j.slack.enabled ? "Larm skickas till Slack via webhooken i Unraid-mallen."
      : "Slack är inte konfigurerat. Fyll i SLACK_WEBHOOK_URL i Unraid-mallen.";
    $("s-test-slack").disabled = !j.slack.enabled;
    $("s-mail").innerHTML = j.email.enabled ? `E-post skickas via <b>${esc(j.email.host)}</b>.`
      : "E-post är inte konfigurerat. Fyll i SMTP_USER och SMTP_TOKEN i Unraid-mallen. Mottagarna lägger du till här.";
  } catch (e) { $("s-msg").textContent = "Kunde inte läsa inställningarna."; $("s-msg").className = "formmsg err"; }
  if (dlg.showModal) dlg.showModal(); else dlg.setAttribute("open", "");
}
$("open-settings").addEventListener("click", openSettings);
$("s-cancel").addEventListener("click", () => $("settings").close());
$("settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const rt = readRoutes();
  const body = {
    retention_days: +$("s-retention").value, anomaly_factor: +$("s-factor").value, anomaly_min_gb: +$("s-mingb").value,
    suricata_min_priority: +$("s-prio").value, pfblocker_threshold: +$("s-pfb").value,
    confirm_interval: +$("s-interval").value, confirm_count: +$("s-count").value, alert_kinds: rt.alert_kinds,
    quiet_enabled: $("s-quiet").checked, quiet_start: getTime("s-qstart"), quiet_end: getTime("s-qend"),
    quiet_days: [...$("s-qdays").querySelectorAll("input:checked")].map((c) => +c.value),
    quiet_allow_critical: $("s-qcrit").checked, quiet_digest: $("s-qdigest").checked,
    channels: rt.channels,
    recipients: rt.recipients,
    routes: rt.routes,
  };
  try {
    const r = await fetch("api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || r.status);
    $("s-msg").textContent = "Sparat."; $("s-msg").className = "formmsg ok";
    setTimeout(() => $("settings").close(), 700);
    load();
  } catch (err) { $("s-msg").textContent = `Kunde inte spara: ${err.message}`; $("s-msg").className = "formmsg err"; }
});
$("s-test-slack").addEventListener("click", async () => {
  $("s-msg").textContent = "Skickar …"; $("s-msg").className = "formmsg";
  try {
    const r = await fetch("api/test-slack", { method: "POST" });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || r.status);
    $("s-msg").textContent = "Testmeddelande skickat till Slack."; $("s-msg").className = "formmsg ok";
  } catch (err) { $("s-msg").textContent = err.message; $("s-msg").className = "formmsg err"; }
});
$("alerts").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-ack]"); if (!b) return;
  b.disabled = true;
  await fetch(b.dataset.ack === "all" ? "api/alerts/ack-all" : `api/alerts/${b.dataset.ack}/ack`, { method: "POST" }).catch(() => {});
  load();
});

/* ── rendering ── */
function renderStatus(d) {
  const names = { pfsense: "pfSense", vpn: "VPN", unifi: "UniFi", netflow: "NetFlow", syslog: "Syslog", geoip: "GeoIP", email: "E-post", slack: "Slack" };
  const chips = [], problems = [];
  for (const [k, baseLabel] of Object.entries(names)) {
    const s = d.status[k] || {};
    const label = k === "vpn" ? (s.label || baseLabel) : baseLabel;
    if (k === "vpn" && !s.enabled && !(d.status.pfsense || {}).ok) continue;
    const enabled = k === "geoip" ? true : s.enabled;
    let cls = "off", title = "Inte konfigurerat";
    if (k === "slack" && !enabled) continue;
    if ((k === "email" || k === "slack") && enabled && !s.ok && !s.error) { cls = "ok"; title = "Konfigurerat"; }
    else if (enabled && s.ok) { cls = "ok"; title = (s.detail ? s.detail + " · " : "") + (s.last ? `Senast ${clock(s.last)}` : "OK"); }
    else if (k === "vpn" && !enabled) { cls = "off"; title = "Ingen OpenVPN-klient hittad i pfSense"; }
    else if (enabled && s.error) { cls = "err"; title = s.error; if (k !== "vpn") problems.push([label, s.error]); }
    else if (enabled) { cls = ""; title = "Väntar på data"; }
    chips.push(`<span class="chip ${cls}" title="${esc(title)}">${label}</span>`);
  }
  // Plex-servrar direkt efter VPN
  const plexChips = ((d.plex || {}).servers || []).map((p) => {
    const cls = p.ok ? "ok" : p.error ? "err" : "";
    const tip = p.ok ? `${p.name}${p.version ? " · v" + p.version : ""} · ${p.streams} ${p.streams === 1 ? "ström" : "strömmar"}` : (p.error || "Väntar på svar");
    return `<button type="button" class="chip plex ${cls}" data-goto="plex-card" data-pxserver="${p.index ?? ""}" title="${esc(tip)} · Klicka för att se servern">${esc(p.name)}${p.ok ? ` <small>${p.streams}</small>` : ""}</button>`;
  });
  const unraidChip = (d.unraids || []).map((x, i) => {
    const u = x.status, name = u.name || `Unraid ${i + 1}`;
    if (!u.ok && u.error) problems.push([name, u.error]);
    return `<button type="button" class="chip unraid ${u.ok ? (u.level === "ok" ? "ok" : u.level) : (u.error ? "err" : "")}" data-goto="unraid-card-${i}" title="${esc(name + " · " + (u.ok ? u.detail : (u.error || "Väntar på svar")) + " · Klicka för detaljer")}">${esc(name)}</button>`;
  });
  const vpnAt = chips.findIndex((c) => c.includes(">VPN<"));
  chips.splice(vpnAt >= 0 ? vpnAt + 1 : 1, 0, ...unraidChip, ...plexChips, phChip(d));
  if (d.quiet && d.quiet.active) chips.unshift(`<span class="chip quiet" title="Inga mejl eller Slack-meddelanden skickas under tysta tider. Larmen syns ändå här.">Tyst till ${esc(d.quiet.until)}</span>`);
  $("chips").innerHTML = chips.join("");
  $("problems").innerHTML = problems.map(([l, e]) => `<div class="problem"><b>${esc(l)}</b>${esc(e)}</div>`).join("");
  $("demo").hidden = d.mode !== "demo";
  const src = d.traffic_source === "netflow" ? "NetFlow" : "pfSense-tillstånd";
  $("sub").textContent = `pfSense · ${d.home.name} · data från ${src} · uppdaterad ${clock(d.generated)}`;
  if (d.status.geoip && d.status.geoip.source) $("geo-src").textContent = `${d.status.geoip.source}${d.status.geoip.updated ? ", " + d.status.geoip.updated : ""}`;
}

function renderKpis(d) {
  const k = d.kpi;
  $("k-now").innerHTML = `${fmt((k.now_down_bps + k.now_up_bps) / 1e6, 0)}<small>Mbit/s</small>`;
  $("k-now-s").textContent = `${fmt(k.now_down_bps / 1e6, 0)} ned · ${fmt(k.now_up_bps / 1e6, 0)} upp · ${k.active_conns} anslutningar`;
  const live = state.period === "live", ld = live && mapData && mapData.window === "live" ? mapData : null;
  const perTxt = live ? `<i class="livedot"></i>just nu` : esc(PER_HEAD[state.period]);
  for (const id of ["per-total", "per-cl", "per-ctry", "per-map", "per-clist"]) { $(id).innerHTML = perTxt; $(id).classList.toggle("now", live); }
  if (live) {
    if (!ld) { for (const id of ["k-total", "k-cl", "k-ctry"]) $(id).textContent = "…"; for (const id of ["k-total-s", "k-cl-s", "k-ctry-s"]) $(id).textContent = ""; }
    else {
      $("k-total").innerHTML = bytesParts(ld.minute_up + ld.minute_down);
      $("k-total-s").textContent = "senaste minuten";
      $("k-cl").textContent = ld.clients;
      $("k-cl-s").textContent = `med trafik · av ${k.clients_online} anslutna`;
      const cs = new Set(ld.flows.map((f) => f.cc)), ci = new Set(ld.flows.filter((f) => f.dir === "in").map((f) => f.cc));
      $("k-ctry").textContent = cs.size;
      $("k-ctry-s").textContent = `med trafik · ${ci.size} startade utifrån`;
    }
  } else {
    $("k-total").innerHTML = bytesParts(k.total_bytes);
    $("k-total-s").textContent = PERIOD_LABEL[d.period];
  }
  if (live) return;
  $("k-cl").textContent = k.clients_online;
  const parts = [];
  const link = (status, conn, text, tip) => `<button type="button" class="kpilink" data-status="${status}" data-conn="${conn}" title="${tip}">${text}</button>`;
  if (k.wifi || k.wired) parts.push(link("online", "wifi", `${k.wifi} trådlöst`, "Visa trådlösa klienter"), link("online", "wired", `${k.wired} kabel`, "Visa trådbundna klienter"));
  if (k.unknown_link && (k.wifi || k.wired)) parts.push(link("online", "unknown", `${k.unknown_link} okänd`, "Online i pfSense men okända för UniFi. Klicka för att se vilka."));
  parts.push(link("inactive", "", `${k.clients_total - k.clients_online} inaktiva`, "Visa inaktiva klienter"));
  $("k-cl-s").innerHTML = parts.join(" · ");
  const cs = new Set(d.flows.map((f) => f.cc)), ci = new Set(d.flows.filter((f) => f.dir === "in").map((f) => f.cc));
  $("k-ctry").textContent = cs.size;
  $("k-ctry-s").textContent = `${ci.size} med anslutningar startade utifrån`;
}

/* ── kartans period: just nu eller 1 h – 30 d ── */
const PER_HEAD = { "24h": "senaste 24 h", "7d": "senaste 7 dagarna", "30d": "senaste 30 dagarna" };
let mapData = null;
async function loadMap() {
  try {
    const r = await fetch(`api/flows?window=${state.period}`, { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    mapData = await r.json();
    renderMapWin();
  } catch (e) { /* kartan visar senaste svaret */ }
}
function renderMapWin() {
  const live = mapData && mapData.window === "live";
  const lb = $("livebar");
  lb.hidden = !live;
  if (live) {
    const ago = mapData.measured ? Math.max(0, Math.round(Date.now() / 1000 - mapData.measured)) : null;
    const n = new Set(mapData.flows.map((f) => f.cc)).size;
    lb.innerHTML = `<span><b class="num">${n}</b>${n === 1 ? "land" : "länder"} med trafik</span><span><b class="num">${mapData.live_conns}</b>anslutningar med trafik · av ${fmt(mapData.open_conns, 0)} öppna</span><span><b class="num">${fmtMbit(mapData.total_bps)}<small>Mbit/s</small></b>totalt just nu</span>${ago != null ? `<span><b>${ago} s</b>sedan senaste mätning</span>` : ""}`;
  }
  $("ctry-s").textContent = live ? "Mbit/s" : "andel av data";
  if (state.data) renderKpis(state.data);
  renderCountries();
  map.flows = mapData ? mapData.flows : (state.data ? state.data.flows : []);
  map.live = live;
  map.filter = state.mapFilter;
  map.render();
}

function renderCountries() {
  const live = mapData && mapData.window === "live";
  const flows = mapData ? mapData.flows : (state.data ? state.data.flows : []);
  if (live) {
    const byL = d3.rollups(flows, (v) => ({ o: d3.sum(v.filter((f) => f.dir === "out"), (f) => f.bps), i: d3.sum(v.filter((f) => f.dir === "in"), (f) => f.bps), n: d3.sum(v, (f) => f.conns), hasIn: v.some((f) => f.dir === "in") }), (f) => f.cc)
      .sort((a, b) => (b[1].o + b[1].i) - (a[1].o + a[1].i));
    const allL = d3.sum(byL, (x) => x[1].o + x[1].i) || 1;
    $("ctrylist").innerHTML = byL.length ? byL.slice(0, 9).map(([cc, v]) => {
      const t = v.o + v.i, share = t / allL * 100;
      return `<div class="crow pickable" data-cc="${esc(cc)}" title="Visa vilka adresser och klienter som pratar med ${esc(countryName(cc))}"><span class="f"><i class="livedot"></i>${esc(countryName(cc))}</span><span class="num">${fmtMbit(t)} Mbit/s</span><div class="split"><div class="o" style="width:${t ? share * v.o / t : 0}%"></div><div class="i" style="width:${v.hasIn ? Math.max(1.2, t ? share * v.i / t : 0) : 0}%"></div></div><span class="m">${v.n} ${v.n === 1 ? "anslutning" : "anslutningar"} med trafik${v.hasIn ? " · varav utifrån" : ""}</span><span class="m num">${fmt(share, 0)} %</span></div>`;
    }).join("") : `<div class="empty">Ingen anslutning har haft trafik den senaste minuten.</div>`;
    return;
  }
  const by = d3.rollups(flows, (v) => ({ o: d3.sum(v.filter((f) => f.dir === "out"), (f) => f.bytes), i: d3.sum(v.filter((f) => f.dir === "in"), (f) => f.bytes), n: d3.sum(v, (f) => f.conns), hasIn: v.some((f) => f.dir === "in") }), (f) => f.cc)
    .sort((a, b) => (b[1].o + b[1].i) - (a[1].o + a[1].i));
  const all = d3.sum(by, (x) => x[1].o + x[1].i) || 1;
  $("ctrylist").innerHTML = by.length ? by.slice(0, 9).map(([cc, v]) => {
    const t = v.o + v.i, share = t / all * 100;
    return `<div class="crow pickable" data-cc="${esc(cc)}" title="Visa vilka adresser och klienter som pratar med ${esc(countryName(cc))}"><span class="f">${esc(countryName(cc))}</span><span class="num">${fmtBytes(t)}</span><div class="split"><div class="o" style="width:${t ? share * v.o / t : 0}%"></div><div class="i" style="width:${v.hasIn ? Math.max(1.2, t ? share * v.i / t : 0) : 0}%"></div></div><span class="m">${fmt(v.n, 0)} anslutningar${v.hasIn ? " · varav utifrån" : ""}</span><span class="m num">${fmt(share, 0)} %</span></div>`;
  }).join("") : `<div class="empty">Ingen trafik registrerad än.</div>`;
}

/* ── över tid: bandbredd och datamängd, var för sig med egen period och källa ── */
const charts = (() => {
  const def = { bw: { win: "60m", scope: "internet" }, data: { win: "24h", scope: "internet" } };
  try {
    const saved = JSON.parse(localStorage.getItem("chartView2") || "null");
    if (saved) return { bw: { ...def.bw, ...saved.bw }, data: { ...def.data, ...saved.data } };
    const old = JSON.parse(localStorage.getItem("chartView") || "{}");
    if (old.win) def[old.mode === "data" ? "data" : "bw"] = { win: old.win, scope: old.scope || "internet" };
  } catch (e) { /* ingen lagring */ }
  return def;
})();
const chartData = { bw: null, data: null };
const STEP_LABEL = { 30: "30 s", 60: "1 min", 1800: "30 min" };
function loadChart() { loadChartOne("bw"); loadChartOne("data"); }
async function loadChartOne(mode) {
  const C = charts[mode];
  try {
    const r = await fetch(`api/series?window=${C.win}&scope=${C.scope}`, { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    chartData[mode] = await r.json();
    renderChart(mode);
  } catch (e) { $(`${mode}-chart`).innerHTML = `<div class="empty">Kunde inte hämta grafen (${esc(e.message)}).</div>`; }
}
function renderChart(mode) {
  if (!mode) { renderChart("bw"); renderChart("data"); return; }
  const C = charts[mode];
  document.querySelectorAll(`#${mode}-win [data-cwin]`).forEach((b) => b.setAttribute("aria-pressed", b.dataset.cwin === C.win));
  const cd = chartData[mode];
  const el = $(`${mode}-chart`);
  if (!cd || cd.window !== C.win || cd.scope !== C.scope) return;
  if (cd.unifi === false && C.scope !== "internet") { C.scope = "internet"; loadChartOne(mode); return; }
  renderScopeSeg(`${mode}-scope`, C.scope, cd.unifi);
  const all = (cd.scope || "internet") === "all";
  const bw = mode === "bw";
  // det pågående tidssteget är inte klart: i kurvan skulle det se ut som ett plötsligt fall
  const nowS = Date.now() / 1000;
  // första och sista tidssteget ligger bara delvis inom perioden och skulle se ut som fall i kurvan
  let pts = cd.points;
  if (bw && pts.length > 4) {
    pts = pts.slice(pts[0].t < nowS - cd.seconds ? 1 : 0);
    if (pts[pts.length - 1].t + cd.step > nowS) pts = pts.slice(0, -1);
  }
  const tz = { timeZone: "Europe/Stockholm" };
  const hm = (t) => new Date(t * 1000).toLocaleTimeString("sv-SE", { ...tz, hour: "2-digit", minute: "2-digit" });
  const hms = (t) => new Date(t * 1000).toLocaleTimeString("sv-SE", { ...tz, hour: "2-digit", minute: "2-digit", second: cd.step < 60 ? "2-digit" : undefined });
  const sumD = cd.total_down, sumU = cd.total_up;
  const pk = pts.reduce((a, p) => (p.down_bps > a.down_bps ? p : a), pts[0] || { down_bps: 0, t: 0 });
  const sumAll = sumD + sumU, sumInet = cd.total_internet || 0, sumLan = Math.max(0, sumAll - sumInet);
  const pkAll = pts.reduce((a, p) => (p.down_bps + p.up_bps > a.down_bps + a.up_bps ? p : a), pts[0] || { down_bps: 0, up_bps: 0, t: 0 });
  if (all) {
    $(`${mode}-stats`).innerHTML = bw
      ? `<span><b class="num">${fmtMbit(sumAll * 8 / cd.seconds)}<small>Mbit/s</small></b>snitt totalt</span><span><b class="num">${fmtMbit(sumInet * 8 / cd.seconds)}<small>Mbit/s</small></b>internet · ${fmt(sumAll ? sumInet / sumAll * 100 : 0, 0)} %</span><span><b class="num">${fmtMbit(sumLan * 8 / cd.seconds)}<small>Mbit/s</small></b>internt · ${fmt(sumAll ? sumLan / sumAll * 100 : 0, 0)} %</span><span><b class="num">${fmtMbit(pkAll.down_bps + pkAll.up_bps)}<small>Mbit/s</small></b>topp${pkAll.t ? ` kl ${hm(pkAll.t)}` : ""}</span>`
      : `<span><b class="num">${bytesParts(sumAll)}</b>totalt</span><span><b class="num">${bytesParts(sumInet)}</b>internet · ${fmt(sumAll ? sumInet / sumAll * 100 : 0, 0)} %</span><span><b class="num">${bytesParts(sumLan)}</b>internt · ${fmt(sumAll ? sumLan / sumAll * 100 : 0, 0)} %</span><span><b>${STEP_LABEL[cd.step] || cd.step + " s"}</b>per stapel</span>`;
  } else $(`${mode}-stats`).innerHTML = bw
    ? `<span><b class="num">${fmtMbit(sumD * 8 / cd.seconds)}<small>Mbit/s</small></b>snitt ned</span><span><b class="num">${fmtMbit(sumU * 8 / cd.seconds)}<small>Mbit/s</small></b>snitt upp</span><span><b class="num">${fmtMbit(pk.down_bps)}<small>Mbit/s</small></b>topp ned${pk.down_bps ? ` kl ${hm(pk.t)}` : ""}</span><span><b>${STEP_LABEL[cd.step] || cd.step + " s"}</b>per punkt</span>`
    : `<span><b class="num">${bytesParts(sumD + sumU)}</b>totalt</span><span><b class="num">${bytesParts(sumD)}</b>ned</span><span><b class="num">${bytesParts(sumU)}</b>upp</span><span><b>${STEP_LABEL[cd.step] || cd.step + " s"}</b>per stapel</span>`;
  const per = bw ? "Mbit/s" : (cd.step >= 1800 ? "per halvtimme" : cd.step >= 60 ? "per minut" : "per 30 sekunder");
  $(`${mode}-legend`).innerHTML = all
    ? `<span><i class="blk" style="background:var(--accent)"></i>Mot internet (pfSense)</span><span><i class="blk" style="background:var(--lan)"></i>Internt (UniFi minus internet)</span><span>· ${per}, ned och upp tillsammans</span>`
    : `<span><i class="${bw ? "" : "blk"}" style="background:var(--accent)"></i>Nedladdning</span><span><i class="${bw ? "" : "blk"}" style="background:var(--up)"></i>Uppladdning</span><span>· ${per}, ${cd.scope === "internal" ? "trafik inne i nätverket" : "klienter mot internet"}</span>`;
  el.innerHTML = "";
  if (!pts.length || (sumD + sumU) === 0) {
    el.innerHTML = `<div class="empty">${cd.seconds > 3600 ? "Ingen data än." : `Ingen trafik registrerad de senaste ${WIN_LABEL[cd.window]}. Korta perioder fylls på medan dashboarden körs.`}</div>`;
    return;
  }
  const W = 760, H = 200, m = { l: 44, r: 10, t: 24, b: 24 };
  const s = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("role", "img")
    .attr("aria-label", bw ? "Bandbredd över tid" : "Datamängd över tid").style("width", "100%").style("height", "auto").style("display", "block");
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t + (bw ? 0 : cd.step);
  const x = d3.scaleLinear([t0, t1], [m.l, W - m.r]);
  const unit = bw ? 1e6 : (Math.max(...pts.map((p) => p.down + p.up)) >= 1e9 ? 1e9 : 1e6);
  const val = (p, k) => (bw ? p[k + "_bps"] : p[k]) / unit;
  const inetV = (p) => (bw ? p.internet * 8 / cd.step : p.internet) / unit;
  const totV = (p) => val(p, "down") + val(p, "up");
  const maxV = d3.max(pts, (p) => (bw && !all ? Math.max(val(p, "down"), val(p, "up")) : totV(p))) || 1;
  const y = d3.scaleLinear([0, maxV * 1.1], [H - m.b, m.t]).nice(4);
  y.ticks(4).forEach((v) => {
    s.append("line").attr("class", "gl").attr("x1", m.l).attr("x2", W - m.r).attr("y1", y(v)).attr("y2", y(v));
    s.append("text").attr("x", m.l - 6).attr("y", y(v) + 3.5).attr("text-anchor", "end").text(fmt(v, v < 10 ? 1 : 0));
  });
  s.append("text").attr("x", 2).attr("y", 10).text(bw ? "Mbit/s" : unit === 1e9 ? "GB" : "MB");
  const tickEvery = { 300: 60, 900: 180, 3600: 600, 86400: 10800 }[cd.seconds] || cd.seconds / 6;
  for (let t = Math.ceil(t0 / tickEvery) * tickEvery; t <= t1; t += tickEvery) {
    if (x(t) < m.l + 16 || x(t) > W - m.r - 16) continue;
    s.append("text").attr("x", x(t)).attr("y", H - 6).attr("text-anchor", "middle").text(hm(t));
  }
  if (all) {
    if (bw) {
      const cv = d3.curveMonotoneX, X = (p) => x(p.t);
      s.append("path").attr("d", d3.area().x(X).y0((p) => y(inetV(p))).y1((p) => y(totV(p))).curve(cv)(pts)).attr("fill", "var(--lan)").attr("fill-opacity", 0.55);
      s.append("path").attr("d", d3.area().x(X).y0(y(0)).y1((p) => y(inetV(p))).curve(cv)(pts)).attr("fill", "var(--accent)").attr("fill-opacity", 0.7);
      s.append("path").attr("d", d3.line().x(X).y((p) => y(totV(p))).curve(cv)(pts)).attr("fill", "none").attr("stroke", "var(--lan)").attr("stroke-width", 1.6);
    } else {
      const bwid = Math.max(1, (x(t0 + cd.step) - x(t0)) - 2);
      pts.forEach((p) => {
        const a = inetV(p), b = totV(p);
        s.append("rect").attr("x", x(p.t) + 1).attr("width", bwid).attr("y", y(a)).attr("height", Math.max(0, y(0) - y(a))).attr("fill", "var(--accent)").attr("fill-opacity", 0.9);
        s.append("rect").attr("x", x(p.t) + 1).attr("width", bwid).attr("y", y(b)).attr("height", Math.max(0, y(a) - y(b))).attr("fill", "var(--lan)").attr("fill-opacity", 0.85);
      });
    }
  } else if (bw) {
    const cv = d3.curveMonotoneX, X = (p) => x(p.t);
    s.append("path").attr("d", d3.area().x(X).y0(y(0)).y1((p) => y(val(p, "down"))).curve(cv)(pts)).attr("fill", "var(--accent)").attr("fill-opacity", 0.12);
    s.append("path").attr("d", d3.line().x(X).y((p) => y(val(p, "down"))).curve(cv)(pts)).attr("fill", "none").attr("stroke", "var(--accent)").attr("stroke-width", 2);
    s.append("path").attr("d", d3.line().x(X).y((p) => y(val(p, "up"))).curve(cv)(pts)).attr("fill", "none").attr("stroke", "var(--up)").attr("stroke-width", 1.6);
    if (pk.down_bps > 0) {
      const px = x(pk.t), py = y(pk.down_bps / 1e6), left = px > W / 2;
      s.append("circle").attr("cx", px).attr("cy", py).attr("r", 4).attr("fill", "var(--accent)").attr("stroke", "var(--card)").attr("stroke-width", 2);
      s.append("text").attr("x", left ? px - 8 : px + 8).attr("y", Math.max(12, py - 8)).attr("text-anchor", left ? "end" : "start")
        .attr("style", "fill:var(--fg);font-weight:500").text(`Topp ${fmtMbit(pk.down_bps)} Mbit/s kl ${hm(pk.t)}`);
    }
  } else {
    const bwid = Math.max(1, (x(t0 + cd.step) - x(t0)) - 2);
    pts.forEach((p) => {
      const d = val(p, "down"), u = val(p, "up");
      s.append("rect").attr("x", x(p.t) + 1).attr("width", bwid).attr("y", y(d)).attr("height", Math.max(0, y(0) - y(d))).attr("fill", "var(--accent)").attr("fill-opacity", 0.85);
      s.append("rect").attr("x", x(p.t) + 1).attr("width", bwid).attr("y", y(d + u)).attr("height", Math.max(0, y(d) - y(d + u))).attr("fill", "var(--up)");
    });
  }
  // värde vid muspekaren
  const guide = s.append("line").attr("y1", m.t).attr("y2", H - m.b).attr("stroke", "var(--muted)").attr("stroke-dasharray", "3 3").style("display", "none");
  const tip = s.append("g").style("display", "none");
  tip.append("rect").attr("width", 160).attr("height", 46).attr("fill", "var(--track)").attr("stroke", "#2a3d52");
  const tt = [0, 1, 2].map((i) => tip.append("text").attr("x", 8).attr("y", 15 + i * 13));
  s.append("rect").attr("x", m.l).attr("y", m.t).attr("width", W - m.l - m.r).attr("height", H - m.t - m.b).attr("fill", "transparent")
    .on("mousemove", (ev) => {
      const [mx] = d3.pointer(ev);
      const tt0 = x.invert(mx);
      const p = pts.reduce((a, q) => (Math.abs((bw ? q.t : q.t + cd.step / 2) - tt0) < Math.abs((bw ? a.t : a.t + cd.step / 2) - tt0) ? q : a), pts[0]);
      const gx = bw ? x(p.t) : x(p.t + cd.step / 2);
      guide.attr("x1", gx).attr("x2", gx).style("display", null);
      tip.style("display", null).attr("transform", `translate(${gx + 170 > W ? gx - 168 : gx + 8},${m.t + 2})`);
      tt[0].attr("style", "fill:var(--fg)").text(bw ? hms(p.t) : `${hm(p.t)}–${hm(p.t + cd.step)}`);
      if (all) {
        const lan = Math.max(0, p.down + p.up - p.internet);
        tt[1].attr("style", "fill:var(--accent)").text(bw ? `internet ${fmtMbit(p.internet * 8 / cd.step)} Mbit/s` : `internet ${fmtBytes(p.internet)}`);
        tt[2].attr("style", "fill:var(--lan)").text(bw ? `internt ${fmtMbit(lan * 8 / cd.step)} Mbit/s` : `internt ${fmtBytes(lan)}`);
      } else {
        tt[1].attr("style", "fill:var(--accent)").text(bw ? `ned ${fmtMbit(p.down_bps)} Mbit/s` : `ned ${fmtBytes(p.down)}`);
        tt[2].attr("style", "fill:var(--up)").text(bw ? `upp ${fmtMbit(p.up_bps)} Mbit/s` : `upp ${fmtBytes(p.up)}`);
      }
    })
    .on("mouseleave", () => { guide.style("display", "none"); tip.style("display", "none"); });
}
for (const mode of ["bw", "data"]) {
  $(`${mode}-scope`).addEventListener("click", (e) => {
    const b = e.target.closest("[data-scope]"); if (!b || b.disabled) return;
    charts[mode].scope = b.dataset.scope; saveChart(); renderScopeSeg(`${mode}-scope`, charts[mode].scope, chartData[mode] ? chartData[mode].unifi : true); loadChartOne(mode);
  });
  $(`${mode}-win`).addEventListener("click", (e) => {
    const b = e.target.closest("[data-cwin]"); if (!b) return;
    charts[mode].win = b.dataset.cwin; saveChart(); renderChart(mode); loadChartOne(mode);
  });
}
function saveChart() { try { localStorage.setItem("chartView2", JSON.stringify(charts)); } catch (e) { /* ingen lagring */ } }

/* ── trafikval: internet, internt eller allt ── */
const SCOPES = {
  internet: { label: "Internet", path: "M12 3a9 9 0 1 0 0 18a9 9 0 0 0 0-18z M3 12h18 M12 3c3 3 3 15 0 18 M12 3c-3 3-3 15 0 18",
    tip: "Trafik mellan nätverket och internet. Från pfSense, med land." },
  internal: { label: "Internt", path: "M4 5h16v14H4z M8 19v-4h8v4 M9 15v-3h6v3 M8 9h1 M11 9h2 M15 9h1",
    tip: "Trafik inne i nätverket, t.ex. Plex hemma och filkopiering till LillaNasen och Tower. UniFis totala trafik minus internet." },
  all: { label: "Allt", path: "M12 3l9 5l-9 5l-9-5z M3 13l9 5l9-5 M3 17.5l9 5l9-5",
    tip: "Klientens totala trafik enligt UniFi, uppdelad i internet och internt." },
};
function renderScopeSeg(id, current, unifi) {
  $(id).innerHTML = Object.entries(SCOPES).map(([k, sc]) => {
    const off = k !== "internet" && unifi === false;
    return `<button type="button" data-scope="${k}" aria-pressed="${k === current}" aria-label="${sc.label}" ${off ? "disabled" : ""}><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="${sc.path}"/></svg><em class="tip" role="tooltip"><b>${sc.label}</b>${off ? "Kräver UniFi. Fyll i UNIFI_URL, UNIFI_USER och UNIFI_PASS i Unraid-mallen." : sc.tip}</em></button>`;
  }).join("");
}
const SCOPE_WORD = { internet: "mot internet", internal: "internt", all: "totalt" };

/* ── topplista: mest bandbredd och mest data ── */
function linkIcon(x) {
  const c = x.conn;
  if (!c) return `<span class="link unk" title="Okänd anslutning: UniFi känner inte till klienten">?</span>`;
  if (c.kind === "wifi") {
    const tip = ["Trådlös", c.band ? `Wi-Fi ${c.band}` : "Wi-Fi", c.rssi != null ? `${c.rssi} dBm` : null].filter(Boolean).join(" · ");
    return `<span class="link wifi" title="${esc(tip)}" aria-label="${esc(tip)}">${icon("wifi")}</span>`;
  }
  const tip = ["Trådbunden", c.dev ? `${c.dev}${c.port ? ", port " + c.port : ""}` : null].filter(Boolean).join(" · ");
  return `<span class="link wired" title="${esc(tip)}" aria-label="${esc(tip)}">${icon("cable")}</span>`;
}
const topState = (() => { try { return { mode: "bw", win: "5m", scope: "internet", ...JSON.parse(localStorage.getItem("topList") || "{}"), more: false }; } catch (e) { return { mode: "bw", win: "5m", scope: "internet", more: false }; } })();
let topData = null;
const WIN_LABEL = { "5m": "5 min", "15m": "15 min", "60m": "60 min", "24h": "24 h" };
function fmtMbit(bps) { const m = bps / 1e6; return m >= 100 ? fmt(m, 0) : m >= 10 ? fmt(m, 0) : m >= 1 ? fmt(m, 1) : fmt(m, 2); }
function sparkPath(vals) {
  if (!vals || vals.length < 2) return "";
  const mx = Math.max(...vals) || 1;
  return vals.map((v, i) => `${i ? "L" : "M"}${(i / (vals.length - 1) * 72).toFixed(1)} ${(16 - v / mx * 14).toFixed(1)}`).join(" ");
}
async function loadTop() {
  try {
    const r = await fetch(`api/top?window=${topState.win}&scope=${topState.scope}&limit=${topState.more ? 25 : 10}`, { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    topData = await r.json();
    renderTop();
  } catch (e) { $("topc").innerHTML = `<div class="empty">Kunde inte hämta topplistan (${esc(e.message)}).</div>`; }
}
function renderTop() {
  document.querySelectorAll("[data-tmode]").forEach((b) => b.setAttribute("aria-selected", b.dataset.tmode === topState.mode));
  document.querySelectorAll("[data-twin]").forEach((b) => b.setAttribute("aria-pressed", b.dataset.twin === topState.win));
  const t = topData;
  if (!t) return;
  if (t.unifi === false && topState.scope !== "internet") { topState.scope = "internet"; loadTop(); return; }
  renderScopeSeg("topscope", topState.scope, t.unifi);
  const bw = topState.mode === "bw";
  const sc = t.scope || "internet", all = sc === "all";
  const items = t.items.slice().sort((a, b) => (bw ? b.avg_bps - a.avg_bps : b.bytes - a.bytes)).filter((x) => x.bytes > 0);
  const label = t.window === "24h" ? "senaste 24 timmarna" : `senaste ${WIN_LABEL[t.window]}`;
  const keyAll = `<span class="key"><span><i style="background:var(--accent)"></i>internet</span><span><i style="background:var(--lan)"></i>internt</span></span>`;
  $("topc-s").innerHTML = bw
    ? `<span>${SCOPES[sc].label} · snitt ${label} · <b>${fmtMbit(t.total_bps)} Mbit/s</b></span>${all ? keyAll : `<span class="key"><span><i style="background:var(--accent)"></i>ned</span><span><i style="background:var(--up)"></i>upp</span></span>`}`
    : `<span>${SCOPES[sc].label} · ${label} · <b>${fmtBytes(t.total_bytes)}</b></span>${all ? keyAll : `<span class="key"><span><i style="background:${sc === "internal" ? "var(--lan)" : "linear-gradient(90deg,#3a5874,#7fb0d8)"}"></i>andel av totalen</span></span>`}`;
  if (!items.length) {
    $("topc").innerHTML = `<div class="empty">${t.window === "24h" ? "Ingen data än." : `Ingen ${sc === "internal" ? "intern " : ""}trafik registrerad de senaste ${WIN_LABEL[t.window]}. Korta perioder fylls på medan dashboarden körs.`}</div>`;
    $("topc-more").hidden = true;
    return;
  }
  const mx = bw ? Math.max(...items.map((x) => x.avg_bps)) || 1 : t.total_bytes || 1;
  $("topc").innerHTML = items.map((x, i) => {
    const connTxt = x.conn ? (x.conn.kind === "wifi" ? `Wi-Fi ${x.conn.band || ""}` : `kabel${x.conn.dev ? " · " + x.conn.dev : ""}${x.conn.port ? " port " + x.conn.port : ""}`) : "";
    const inetShare = x.bytes ? Math.min(1, x.internet / x.bytes) : 0;
    if (bw) {
      const dw = all ? x.avg_bps / mx * 100 * inetShare : x.avg_down_bps / mx * 100;
      const uw = all ? x.avg_bps / mx * 100 * (1 - inetShare) : x.avg_up_bps / mx * 100;
      return `<div class="tr bwrow pickable" data-id="${esc(x.id)}"><span class="n" title="${esc(x.ip || "")}">${linkIcon(x)}<span class="nm">${esc(x.name)}</span></span><svg class="sp" viewBox="0 0 72 18" preserveAspectRatio="none" aria-hidden="true"><path d="${sparkPath(x.spark)}" fill="none" stroke="var(--accent)" stroke-width="1.4"/></svg><span class="v num">${fmtMbit(x.avg_bps)}<small>Mbit/s</small></span>
        <div class="bar"><span class="d" style="width:${dw}%"></span><span class="${all ? "l" : "u"}" style="width:${uw}%"></span></div>
        <span class="sub"><span>${all ? `${fmt(inetShare * 100, 0)} % internet · ${fmt((1 - inetShare) * 100, 0)} % internt` : `${fmtMbit(x.avg_down_bps)} ned · ${fmtMbit(x.avg_up_bps)} upp`}</span><span>topp ${fmtMbit(x.peak_bps)} Mbit/s</span>${sc === "internal" && connTxt ? `<span>${esc(connTxt)}</span>` : ""}</span></div>`;
    }
    const share = x.bytes / mx * 100;
    return `<div class="tr datarow pickable" data-id="${esc(x.id)}"><span class="n">${linkIcon(x)}<span class="nm">${esc(x.name)}</span>${x.ip ? `<small>${esc(x.ip)}</small>` : ""}</span><span class="v num">${fmtBytes(x.bytes)}</span>
      <div class="bar">${all ? `<span class="d" style="width:${share * inetShare}%"></span><span class="l" style="width:${share * (1 - inetShare)}%"></span>` : `<span class="${sc === "internal" ? "l" : "t"}" style="width:${share}%"></span>`}</div>
      <span class="sub"><span>${fmt(share, 0)} % · ${fmtBytes(x.down)} ned · ${fmtBytes(x.up)} upp</span>${all ? `<span>${fmtBytes(x.internet)} internet · ${fmtBytes(Math.max(0, x.bytes - x.internet))} internt</span>` : ""}${sc === "internal" ? (connTxt ? `<span>${esc(connTxt)}</span>` : "") : x.country ? `<span>mest till ${esc(countryName(x.country))}</span>` : ""}</span></div>`;
  }).join("");
  $("topc-more").hidden = !(t.count > items.length || topState.more);
  $("topc-more").textContent = topState.more ? "Visa färre" : `Visa fler (${t.count})`;
}
document.querySelector("[data-tmode]").closest(".toptabs").addEventListener("click", (e) => {
  const b = e.target.closest("[data-tmode]"); if (!b) return;
  topState.mode = b.dataset.tmode; saveTop(); renderTop();
});
$("topwin").addEventListener("click", (e) => {
  const b = e.target.closest("[data-twin]"); if (!b) return;
  topState.win = b.dataset.twin; saveTop(); renderTop(); loadTop();
});
$("topc-more").addEventListener("click", () => { topState.more = !topState.more; loadTop(); });
$("topscope").addEventListener("click", (e) => {
  const b = e.target.closest("[data-scope]"); if (!b || b.disabled) return;
  topState.scope = b.dataset.scope; saveTop(); loadTop();
});
function saveTop() { try { localStorage.setItem("topList", JSON.stringify({ mode: topState.mode, win: topState.win, scope: topState.scope })); } catch (e) { /* ingen lagring */ } }

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
  if (!k) {
    const random = c.mac && /^.[26ae]/i.test(c.mac);
    return `<span class="muted" title="UniFi känner inte till klienten">Okänd${random ? ' <small title="Lokalt administrerad MAC-adress: mobiler och datorer slumpar den för integritetens skull">· slumpad MAC</small>' : ""}</span>`;
  }
  if (k.kind === "wifi") return `<span class="conn">${icon("wifi")}<span>Wi-Fi ${esc(k.band || "")}</span>${k.rssi != null ? sig(k.rssi) + `<small>${k.rssi} dBm</small>` : ""}</span>`;
  return `<span class="conn">${icon("cable")}<span>${esc(k.dev_name || "Kabel")}</span>${k.port ? `<small>port ${k.port}</small>` : ""}</span>`;
}

function phDns(cid) {
  const cl = ph.resp && ph.resp.data ? ph.resp.data.clients : null;
  if (!cl) return { total: -1, blocked: 0 };
  if (!ph.byCid || ph.byCidFor !== cl) { ph.byCid = new Map(cl.filter((c) => c.cid).map((c) => [c.cid, c])); ph.byCidFor = cl; }
  return ph.byCid.get(cid) || { total: 0, blocked: 0 };
}
function phCell(cid) {
  const x = phDns(cid);
  if (x.total < 0) return `<td class="r-right muted">…</td>`;
  if (!x.total) return `<td class="r-right muted">–</td>`;
  return `<td class="r-right num">${fmt(x.total, 0)} · <span class="blkt">${fmt(x.blocked, 0)} (${fmt(x.blocked / x.total * 100, 0)} %)</span></td>`;
}
function renderTable(d) {
  const editingInput = document.activeElement && document.activeElement.id === "rename-input";
  const caret = editingInput ? document.activeElement.selectionStart : null;
  const q = $("search").value.trim().toLowerCase();
  const ipn = (ip) => (ip && ip.includes(".") ? ip.split(".").reduce((a, x) => a * 256 + +x, 0) : 0);
  const val = (c) => state.sortKey === "conn" ? (c.conn ? (c.conn.kind === "wifi" ? "0" + (c.conn.band || "") + String(200 + (c.conn.rssi || 0)).padStart(3, "0") : "1" + (c.conn.dev_name || "") + String(c.conn.port || 0).padStart(3, "0")) : "2")
    : state.sortKey === "now" ? c.down_bps + c.up_bps : state.sortKey === "dns" ? phDns(c.id).total : state.sortKey === "ip" ? ipn(c.ip) : state.sortKey === "name" ? c.name.toLowerCase() : c.bytes;
  const dev = state.devF;
  const connOk = (c) => !state.connF || (state.connF === "unknown" ? !c.conn : c.conn && c.conn.kind === state.connF);
  const statusOk = (c) => !state.statusF || (state.statusF === "online" ? c.online : !c.online);
  const rows = d.clients.filter((c) => connOk(c) && statusOk(c) && (!dev || (c.conn && c.conn.dev === dev))
    && (!q || `${c.name} ${c.ip || ""} ${c.mac || ""}`.toLowerCase().includes(q)))
    .sort((a, b) => { const x = val(a), y = val(b); return (x > y ? 1 : x < y ? -1 : 0) * state.sortDir; });
  const LIMIT = 10;
  const showAll = state.clAll || rows.length <= LIMIT + 2;
  const shown = showAll ? rows : rows.slice(0, LIMIT);
  document.querySelector("#table tbody").innerHTML = rows.length ? shown.map((c) => `<tr class="pickable${state.mapFilter === c.id ? " sel" : ""}" data-id="${esc(c.id)}"><td>${nameCell(c)}</td><td class="ip">${esc(c.ip || "–")}</td><td>${connHTML(c)}</td><td>${esc((c.mac || "–").toUpperCase())}</td><td><span class="pill${c.online ? "" : " idle"}">${c.online ? "Online" : "Inaktiv"}</span></td><td class="r-right num">${c.online && (c.down_bps + c.up_bps) > 0 ? fmtRate(c.down_bps + c.up_bps) + " Mbit/s" : "–"}</td><td class="r-right num">${fmtBytes(c.bytes)}</td>${phEnabled() ? phCell(c.id) : ""}</tr>`).join("")
    : `<tr><td colspan="${phEnabled() ? 8 : 7}" class="muted">Inga klienter matchar filtret.</td></tr>`;
  const of = rows.length === d.clients.length ? "" : ` (av ${d.clients.length})`;
  $("filter-count").textContent = shown.length < rows.length ? `visar ${shown.length} av ${rows.length}${of}` : `${rows.length} klienter${of}`;
  const canToggle = rows.length > LIMIT + 2;
  $("cl-toggle").hidden = !canToggle;
  $("cl-more").hidden = !canToggle;
  $("cl-box").classList.toggle("clfade", canToggle && !state.clAll);
  if (canToggle) {
    $("cl-toggle").textContent = state.clAll ? `Visa ${LIMIT} ▴` : `Visa alla ${rows.length} ▾`;
    $("cl-more").textContent = state.clAll ? `▴ Visa bara ${LIMIT}` : `▾ Visa alla ${rows.length} klienter`;
    $("cl-toggle").setAttribute("aria-expanded", !!state.clAll);
    $("cl-more").setAttribute("aria-expanded", !!state.clAll);
  }
  $("unknown-hint").hidden = state.connF !== "unknown";
  if (editingInput) { const i = $("rename-input"); if (i) { i.focus(); if (caret != null) i.setSelectionRange(caret, caret); } }
  document.querySelectorAll("#table th button").forEach((b) => {
    const base = b.textContent.replace(/ [↑↓]$/, "");
    b.textContent = b.dataset.sort === state.sortKey ? `${base} ${state.sortDir < 0 ? "↓" : "↑"}` : base;
  });
}

function toggleClients(e) {
  state.clAll = !state.clAll;
  try { localStorage.setItem("clientsAll", state.clAll ? "1" : "0"); } catch (err) { /* ingen lagring */ }
  renderTable(state.data);
  if (!state.clAll && e && e.currentTarget.id === "cl-more") $("clients-card").scrollIntoView({ block: "nearest" });
}
$("cl-toggle").addEventListener("click", toggleClients);
$("cl-more").addEventListener("click", toggleClients);

const PENCIL = '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20h4L19 9l-4-4L4 16z M14 6l4 4"/></svg>';
function nameCell(c) {
  if (state.editing === c.id) {
    return `<span class="rename"><input type="text" id="rename-input" maxlength="64" value="${esc(state.editValue ?? c.name)}" placeholder="${esc(c.default_name)}" aria-label="Nytt namn för ${esc(c.name)}"><button type="button" class="btn primary" data-save="${esc(c.id)}">Spara</button><button type="button" class="btn" data-cancel>Avbryt</button></span>`;
  }
  const orig = c.custom_name && c.default_name !== c.name ? ` <small class="muted" title="Namn från nätverket">(${esc(c.default_name)})</small>` : "";
  return `<span class="who">${icon(c.type)}<span>${esc(c.name)}${orig}</span><button type="button" class="edit" data-edit="${esc(c.id)}" title="Byt namn" aria-label="Byt namn på ${esc(c.name)}">${PENCIL}</button></span>`;
}
async function saveName(id) {
  const input = $("rename-input");
  const name = input ? input.value.trim() : "";
  try {
    const r = await fetch(`api/clients/${encodeURIComponent(id)}/name`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
    if (!r.ok) throw new Error(r.status);
  } catch (e) {
    $("problems").innerHTML = `<div class="problem"><b>Namn</b>Kunde inte spara namnet (${esc(e.message)}).</div>`;
  }
  state.editing = null; state.editValue = null;
  load();
}
document.querySelector("#table tbody").addEventListener("click", (e) => {
  const ed = e.target.closest("[data-edit]");
  if (ed) {
    e.stopPropagation();
    state.editing = ed.dataset.edit; state.editValue = null; renderTable(state.data);
    const i = $("rename-input"); if (i) { i.focus(); i.select(); }
    return;
  }
  const sv = e.target.closest("[data-save]");
  if (sv) { e.stopPropagation(); saveName(sv.dataset.save); return; }
  if (e.target.closest("[data-cancel]")) { e.stopPropagation(); state.editing = null; state.editValue = null; renderTable(state.data); return; }
  if (e.target.closest(".rename")) e.stopPropagation();
});
document.querySelector("#table tbody").addEventListener("input", (e) => { if (e.target.id === "rename-input") state.editValue = e.target.value; });
document.querySelector("#table tbody").addEventListener("keydown", (e) => {
  if (e.target.id !== "rename-input") return;
  if (e.key === "Enter") { e.preventDefault(); saveName(state.editing); }
  if (e.key === "Escape") { state.editing = null; state.editValue = null; renderTable(state.data); }
});

function renderMapFilter() {
  const c = state.mapFilter && byId[state.mapFilter];
  $("mapfilter").innerHTML = c ? `<b>Visar endast ${esc(c.name)}</b> <button type="button" id="clearfilter">Visa alla</button>` : "";
}

function render() {
  const d = state.data;
  if (!d) return;
  byId = Object.fromEntries(d.clients.map((c) => [c.id, c]));
  if (state.mapFilter && !byId[state.mapFilter]) state.mapFilter = null;
  renderAlerts(d); renderSecurity(d); renderEvents(d); renderPlex(d); renderUnraids(d);
  renderStatus(d); renderKpis(d); renderCountries(); renderDevices(d); renderTable(d); renderMapFilter();
  map.home = d.home;
  map.flows = mapData ? mapData.flows : d.flows;
  map.live = mapData && mapData.window === "live";
  map.filter = state.mapFilter;
  map.render();
  if (window.onDashRender) window.onDashRender(d);
}
window.dashState = state;
window.dashRender = () => render();

async function load() {
  try {
    const r = await fetch(`api/summary?period=${state.period === "live" ? "24h" : state.period}`, { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    state.data = await r.json();
    render();
    loadTop();
    loadChart();
    loadMap();
    loadPihole();
  } catch (e) {
    $("problems").innerHTML = `<div class="problem"><b>Dashboard</b>Får inget svar från servern (${esc(e.message)}). Försöker igen …</div>`;
  }
}

/* ── händelser ── */
function setPressed(group, btn) { [...group.querySelectorAll("button")].forEach((x) => x.setAttribute("aria-pressed", x === btn)); }
$("period").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  state.period = b.dataset.p; setPressed(e.currentTarget, b);
  try { localStorage.setItem("period", state.period); } catch (err) { /* ingen lagring */ }
  mapData = null; renderMapWin(); load();
});
setPressed($("period"), $("period").querySelector(`[data-p="${state.period}"]`));
$("zoom").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; setPressed(e.currentTarget, b); map.zoom = b.dataset.z; map.makeProj(); map.zoomReset(false); map.render(); });
$("map-zoom").addEventListener("click", (e) => {
  const b = e.target.closest("[data-mz]"); if (!b) return;
  if (b.dataset.mz === "in") map.zoomBy(1.6);
  else if (b.dataset.mz === "out") map.zoomBy(1 / 1.6);
  else map.zoomReset();
  // etiketterna visas när man har zoomat in tillräckligt
  setTimeout(() => map.render(), reduceMotion ? 0 : 320);
});
$("connf").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; state.connF = b.dataset.c; setPressed(e.currentTarget, b); render(); });
$("statusf").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; state.statusF = b.dataset.s; setPressed(e.currentTarget, b); render(); });
function scrollToCard(id) {
  if (window.layoutGoto && window.layoutGoto(id)) return;
  const um = id.match(/^unraid-card-(\d+)$/);
  if (um) { window.unraidSelect(+um[1]); id = "unraid-box"; }
  const el = $(id);
  if (!el || el.hidden) return;
  const top = el.getBoundingClientRect().top + window.scrollY - $("alerts").offsetHeight - 12;
  window.scrollTo({ top, behavior: reduceMotion ? "auto" : "smooth" });
  el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash");
}
$("chips").addEventListener("click", (e) => {
  const b = e.target.closest("[data-goto]"); if (!b) return;
  if (b.dataset.pxserver) window.plexSelect(b.dataset.pxserver);
  scrollToCard(b.dataset.goto);
});
function setClientFilter(status, conn) {
  state.statusF = status; state.connF = conn; state.devF = null;
  setPressed($("statusf"), $("statusf").querySelector(`[data-s="${status}"]`));
  setPressed($("connf"), $("connf").querySelector(`[data-c="${conn}"]`));
  render();
  scrollToCard("clients-card");
}
$("k-cl-s").addEventListener("click", (e) => { const b = e.target.closest(".kpilink"); if (b) setClientFilter(b.dataset.status, b.dataset.conn); });
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

/* ── Pi-hole: statistik, frågelogg och styrning ── */
const PH_TWO = new Set(["co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au", "co.nz", "co.jp", "com.br", "com.cn", "com.tr", "co.za", "com.mx", "co.in", "com.sg", "com.hk"]);
const phBase = (d) => { const p = d.toLowerCase().split("."); return p.length >= 3 && PH_TWO.has(p.slice(-2).join(".")) ? p.slice(-3).join(".") : p.slice(-2).join("."); };
const phStore = (k, def) => { try { const v = localStorage.getItem(k); return v == null ? def : v; } catch (e) { return def; } };
const phSave = (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* ingen lagring */ } };
const ph = { resp: null, fetchedAt: 0, domTab: phStore("phDomTab", "blocked"), clTab: phStore("phClTab", "total"),
  qn: +phStore("phQn", "10"), qf: phStore("phQf", "all"), frozen: false, hover: false, queries: null, qerr: null, dlg: null, busy: false };
const phEnabled = () => !!(state.data && state.data.pihole);
function phLeft() {
  const b = ph.resp && ph.resp.brief;
  if (!b || b.paused_left == null) return null;
  return Math.max(0, Math.round(b.paused_left - (Date.now() - ph.fetchedAt) / 1000));
}
const mmss = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
function phChip(d) {
  const b = d.pihole;
  if (!b) return "";
  let left = phLeft();
  if (left == null && b.paused_left != null) left = b.paused_left;
  const paused = b.ok && b.blocking !== "enabled";
  const cls = !b.ok ? (b.error ? "err" : "") : paused ? "warn" : "ok";
  const small = !b.ok ? "" : paused ? ` <small data-phleft>${left != null ? "pausad " + mmss(left) : "av"}</small>` : b.percent_blocked != null ? ` <small>${fmt(b.percent_blocked, 0)} %</small>` : "";
  const tip = !b.ok ? (b.error || "Väntar på svar") : paused ? "Blockeringen är pausad" : `${fmt(b.percent_blocked, 1)} % av DNS-frågorna blockerade senaste 24 h`;
  return `<button type="button" class="chip pihole ${cls}" data-goto="pihole-card" title="${esc(tip)} · Klicka för att se Pi-hole">${esc(b.name)}${small}</button>`;
}
async function loadPihole() {
  const card = $("pihole-card");
  card.hidden = !phEnabled();
  document.querySelectorAll("#table .phcol").forEach((x) => { x.hidden = !phEnabled(); });
  if (!phEnabled()) return;
  try {
    const r = await fetch(`api/pihole?period=${state.period}`, { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    ph.resp = await r.json();
    ph.fetchedAt = Date.now();
  } catch (e) {
    ph.resp = { brief: state.data.pihole, error: `Får inget svar från dashboarden (${e.message}).`, rules: ph.resp ? ph.resp.rules : [] };
  }
  renderPihole();
  if (state.data) renderTable(state.data);
  loadQueries();
}
async function loadQueries(force) {
  if (!phEnabled() || (!force && (ph.frozen || ph.hover))) return;
  try {
    const r = await fetch(`api/pihole/queries?n=${ph.qn}&show=${ph.qf}`, { cache: "no-store" });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || r.status);
    ph.queries = j.queries; ph.qerr = null;
  } catch (e) { ph.qerr = String(e.message || e); }
  if (!ph.hover || force) renderQueries();
}
function phStatusHTML(b) {
  const left = phLeft();
  const parts = [];
  if (!b.ok) parts.push(`<span class="pill err">${b.error ? "Svarar inte" : "Väntar på svar"}</span>`);
  else if (b.blocking === "enabled") {
    parts.push(`<span class="pill">● Blockering på</span>`);
  } else {
    parts.push(`<span class="pill warn">❚❚ ${left != null ? `Pausad · <span data-phleft>${mmss(left)}</span> kvar` : "Blockering av"}</span>`);
  }
  if (b.version) parts.push(`<span class="muted">${esc(b.version)}</span>`);
  if (b.update) parts.push(`<span class="pill warn" title="Ny version finns">uppdatering finns: ${esc(b.update)}</span>`);
  if (b.ok && b.blocking === "enabled") {
    parts.push(`<details class="phpause"><summary class="btn warn">❚❚ Pausa blockering ▾</summary><div class="phmenu">
      <span class="h">Pausa all blockering i</span>
      <span class="phgrid">${[1, 5, 15, 60].map((m) => `<button type="button" class="btn" data-pause="${m}">${m} min</button>`).join("")}</span>
      <span class="h">Egen tid</span>
      <span class="phinp"><input type="number" id="ph-custom" min="1" max="10080" step="1" value="90" aria-label="Antal minuter"><span class="muted">minuter</span><button type="button" class="btn warn" data-pause="custom">Pausa</button></span>
      <span class="muted small">Blockeringen slås på igen automatiskt när tiden gått ut, även om dashboarden stängs.</span></div></details>`);
  } else if (b.ok) {
    parts.push(`<button type="button" class="btn okb" data-resume>▶ Slå på igen nu</button>`);
  }
  return parts.join("");
}
function renderPihole() {
  const r = ph.resp;
  if (!r) return;
  const b = r.brief || {};
  const live = state.period === "live";
  $("per-ph").innerHTML = live ? `<i class="livedot"></i>just nu` : esc(PER_HEAD[state.period]);
  $("per-ph").classList.toggle("now", live);
  const open = document.querySelector(".phpause[open]");
  const custom = $("ph-custom") ? $("ph-custom").value : null;
  if (!open) $("ph-stat").innerHTML = phStatusHTML(b);
  else if (custom != null) $("ph-custom").value = custom;
  const err = r.error || (!b.ok && b.error);
  $("ph-err").innerHTML = err ? `<div class="problem"><b>${esc(b.name || "Pi-hole")}</b>${esc(err)}</div>` : "";
  const d = r.data;
  if (!d) { $("ph-kp").innerHTML = ""; $("ph-chart").innerHTML = ""; $("ph-doms").innerHTML = ""; $("ph-cls").innerHTML = ""; renderRules(); return; }
  const pct = d.total ? d.blocked / d.total * 100 : 0;
  const upd = b.gravity_updated ? new Date(b.gravity_updated * 1000) : null;
  $("ph-kp").innerHTML = `
    <div><div class="l">DNS-frågor</div><div class="v num">${fmt(d.total, 0)}</div><div class="s">från ${fmt(d.clients_count, 0)} klienter${live ? " · senaste 10 min" : ""}</div></div>
    <div class="blk"><div class="l">Blockerade</div><div class="v num">${fmt(d.blocked, 0)}<small>${fmt(pct, 1)} %</small></div><div class="s">av alla frågor</div></div>
    <div><div class="l">Svar från cache</div><div class="v num">${fmt(d.total ? d.cached / d.total * 100 : 0, 0)}<small>%</small></div><div class="s">resten vidare till uppströms-DNS</div></div>
    <div><div class="l">Blocklistan</div><div class="v num">${b.gravity_domains != null ? `${fmt(b.gravity_domains / 1e6, 2)}<small>milj. domäner</small>` : "–"}</div><div class="s">${upd ? "uppdaterad " + phDate(b.gravity_updated) : ""}</div></div>`;
  renderPhChart(d);
  renderPhLists(d);
  renderRules();
}
function phDate(t) {
  const dt = new Date(t * 1000), now = new Date();
  const opt = { timeZone: "Europe/Stockholm" };
  const day = (x) => x.toLocaleDateString("sv-SE", opt);
  const hm = dt.toLocaleTimeString("sv-SE", { ...opt, hour: "2-digit", minute: "2-digit" });
  if (day(dt) === day(now)) return `i dag ${hm}`;
  if (day(dt) === day(new Date(now - 86400000))) return `i går ${hm}`;
  return `${dt.toLocaleDateString("sv-SE", { ...opt, day: "numeric", month: "short" })} ${hm}`;
}
function renderPhChart(d) {
  const h = d.history || [];
  if (!h.length) { $("ph-chart").innerHTML = `<div class="empty">Ingen historik än.</div>`; return; }
  const max = Math.max(1, ...h.map((x) => x.allowed + x.blocked));
  const w = 1000 / h.length, gap = Math.min(2, w * 0.25);
  const step = d.step;
  const lbl = (t) => step >= 86400 ? new Date(t * 1000).toLocaleDateString("sv-SE", { timeZone: "Europe/Stockholm", day: "numeric", month: "short" })
    : new Date(t * 1000).toLocaleTimeString("sv-SE", { timeZone: "Europe/Stockholm", hour: "2-digit", minute: "2-digit" }) + (step >= 3600 ? " " + new Date(t * 1000).toLocaleDateString("sv-SE", { timeZone: "Europe/Stockholm", day: "numeric", month: "short" }) : "");
  const bars = h.map((x, i) => {
    const ta = x.allowed / max * 110, tb = x.blocked / max * 110;
    return `<g><title>${lbl(x.t)}: ${fmt(x.allowed + x.blocked, 0)} frågor, ${fmt(x.blocked, 0)} blockerade</title><rect class="a" x="${(i * w).toFixed(2)}" y="${(120 - ta - tb).toFixed(1)}" width="${(w - gap).toFixed(2)}" height="${ta.toFixed(1)}"/><rect class="b" x="${(i * w).toFixed(2)}" y="${(120 - tb).toFixed(1)}" width="${(w - gap).toFixed(2)}" height="${tb.toFixed(1)}"/></g>`;
  }).join("");
  const per = { 60: "en stapel per minut", 600: "en stapel per 10 minuter", 3600: "en stapel per timme", 86400: "en stapel per dag" }[step] || "";
  $("ph-chart").innerHTML = `<svg viewBox="0 0 1000 120" preserveAspectRatio="none" role="img" aria-label="DNS-frågor över tid">${bars}</svg>
    <div class="phlegend"><span><i class="a"></i>Tillåtna</span><span><i class="b"></i>Blockerade</span><span>· ${per} · ${step >= 86400 ? lbl(h[0].t) : step >= 600 ? phDate(h[0].t) : clock(h[0].t)} – ${step >= 86400 ? lbl(h[h.length - 1].t) : step >= 600 ? phDate(h[h.length - 1].t) : clock(h[h.length - 1].t)}</span></div>`;
}
function renderPhLists(d) {
  const blockedTab = ph.domTab === "blocked";
  const doms = blockedTab ? d.top_blocked : d.top_allowed;
  const dmax = Math.max(1, ...doms.map((x) => x.count));
  const act = (dom, blocked) => `<button type="button" class="btn sm ${blocked ? "okb" : "red"} rowact" data-phact="${blocked ? "unblock" : "block"}" data-dom="${esc(dom)}">${blocked ? "Avblockera" : "Blockera"}</button>`;
  $("ph-doms").innerHTML = doms.length ? doms.map((x) => `<div class="phrow"><span class="n" title="${esc(x.domain)}">${esc(x.domain)}</span><span class="c num">${fmt(x.count, 0)}</span>${act(x.domain, blockedTab)}<div class="bar"><i class="${blockedTab ? "b" : "a"}" style="width:${x.count / dmax * 100}%"></i></div></div>`).join("")
    : `<div class="empty">Inga ${blockedTab ? "blockerade" : "tillåtna"} domäner under perioden.</div>`;
  const key = ph.clTab === "blocked" ? "blocked" : "total";
  const cls = [...d.clients].sort((a, b) => b[key] - a[key]).filter((c) => c[key] > 0).slice(0, 8);
  const cmax = Math.max(1, ...cls.map((c) => c.total));
  $("ph-cls").innerHTML = cls.length ? cls.map((c) => `<div class="phrow cl"><span class="n">${esc(c.name || c.ip)} <span class="muted">· ${esc(c.ip)}</span></span><span class="c num">${fmt(c.total, 0)} <span class="blkt">· ${fmt(c.blocked, 0)}</span></span><div class="bar two"><i class="a" style="width:${(c.total - c.blocked) / cmax * 100}%"></i><i class="b" style="width:${c.blocked / cmax * 100}%"></i></div></div>`).join("")
    : `<div class="empty">Inga klienter under perioden.</div>`;
}
function ruleName(r) {
  const m = r.kind === "regex" && r.domain.match(/^\(\\\.\|\^\)(.+)\$$/);
  return m ? "*." + m[1].replace(/\\\./g, ".") : r.domain;
}
function renderRules() {
  const rules = (ph.resp && ph.resp.rules) || [];
  document.querySelector("#ph-rules tbody").innerHTML = rules.length ? rules.map((r) => `<tr><td class="wrapcell">${esc(ruleName(r))}${r.enabled ? "" : ' <small class="muted">(avstängd)</small>'}</td><td><span class="kind ${r.type === "allow" ? "allow" : "deny"}">${r.type === "allow" ? "tillåten" : "blockerad"}${r.kind === "regex" ? " · regex" : ""}</span></td><td class="muted">${r.added ? phDate(r.added) : "–"}${r.comment === "Från nätverksdashboarden" ? " · från dashboarden" : ""}</td><td class="r-right"><button type="button" class="btn sm" data-rmrule data-type="${esc(r.type)}" data-kind="${esc(r.kind)}" data-dom="${esc(r.domain)}">Ta bort</button></td></tr>`).join("")
    : `<tr><td colspan="4" class="muted">Inga egna regler i Pi-hole.</td></tr>`;
}
function renderQueries() {
  const tb = document.querySelector("#ph-q tbody");
  $("ph-qstate").innerHTML = ph.frozen ? "fryst" : `<i class="livedot"></i>uppdateras var 5:e sekund`;
  $("ph-qstate").classList.toggle("now", !ph.frozen);
  $("ph-freeze").textContent = ph.frozen ? "▶ Fortsätt" : "❚❚ Frys listan";
  $("ph-freeze").setAttribute("aria-pressed", ph.frozen);
  if (ph.qerr && !ph.queries) { tb.innerHTML = `<tr><td colspan="7" class="muted">${esc(ph.qerr)}</td></tr>`; return; }
  const qs = ph.queries || [];
  tb.innerHTML = qs.length ? qs.map((q) => `<tr class="${q.blocked ? "blk" : ""}"><td class="muted num">${clock(q.time)}</td><td class="dom wrapcell">${esc(q.domain)}</td><td>${esc(q.name || q.ip)}</td><td class="muted">${esc(q.type)}</td><td><span class="st ${q.blocked ? "b" : q.cached ? "c" : "f"}">${q.blocked ? "Blockerad" : "Tillåten"} · ${esc(q.why)}</span></td><td class="r-right num muted">${q.reply_ms != null ? fmt(q.reply_ms, q.reply_ms < 1 ? 1 : 0) + " ms" : "–"}</td><td class="r-right"><button type="button" class="btn sm ${q.blocked ? "okb" : "red"} rowact" data-phact="${q.blocked ? "unblock" : "block"}" data-dom="${esc(q.domain)}">${q.blocked ? "Avblockera" : "Blockera"}</button></td></tr>`).join("")
    : `<tr><td colspan="7" class="muted">Inga frågor att visa.</td></tr>`;
}
function phOpen(action, domain) {
  domain = (domain || "").trim().toLowerCase();
  if (!domain) { $("ph-domain").focus(); return; }
  ph.dlg = { action, domain };
  const un = action === "unblock";
  $("ph-dlg-title").textContent = `${un ? "Avblockera" : "Blockera"} ${domain}?`;
  $("ph-dlg-exact").textContent = `${domain} ${un ? "läggs till i Pi-holes tillåtlista (eller tas bort ur din egen blocklista)" : "läggs till i Pi-holes blocklista"}`;
  $("ph-dlg-sub").textContent = `*.${phBase(domain)} – som regex-regel`;
  document.querySelector('#ph-dlg input[value="exact"]').checked = true;
  $("ph-dlg-ok").textContent = un ? "Avblockera" : "Blockera";
  $("ph-dlg-ok").className = `btn ${un ? "okb" : "red"}`;
  $("ph-dlg-err").hidden = true;
  const dlg = $("ph-dlg");
  if (dlg.showModal) dlg.showModal(); else dlg.setAttribute("open", "");
}
async function phPost(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || `fel ${r.status}`);
  return j;
}
$("ph-dlg-cancel").addEventListener("click", () => $("ph-dlg").close());
$("ph-dlg-ok").addEventListener("click", async () => {
  if (!ph.dlg || ph.busy) return;
  ph.busy = true;
  const scope = document.querySelector('#ph-dlg input[name="phscope"]:checked').value;
  try {
    await phPost("api/pihole/domain", { action: ph.dlg.action, domain: ph.dlg.domain, scope });
    $("ph-dlg").close();
    $("ph-domain").value = "";
    load();
  } catch (e) {
    $("ph-dlg-err").textContent = e.message; $("ph-dlg-err").hidden = false;
  } finally { ph.busy = false; }
});
$("pihole-card").addEventListener("click", async (e) => {
  const t = e.target;
  const a = t.closest("[data-phact]");
  if (a) { phOpen(a.dataset.phact, a.dataset.dom); return; }
  const add = t.closest("#ph-add [data-act]");
  if (add) { phOpen(add.dataset.act, $("ph-domain").value); return; }
  const p = t.closest("[data-pause]");
  if (p) {
    const min = p.dataset.pause === "custom" ? +$("ph-custom").value : +p.dataset.pause;
    if (!(min >= 1)) { $("ph-custom").focus(); return; }
    try { await phPost("api/pihole/blocking", { enabled: false, minutes: min }); } catch (err) { $("ph-err").innerHTML = `<div class="problem"><b>Pi-hole</b>${esc(err.message)}</div>`; return; }
    const det = t.closest("details"); if (det) det.open = false;
    load(); return;
  }
  if (t.closest("[data-resume]")) {
    try { await phPost("api/pihole/blocking", { enabled: true }); } catch (err) { $("ph-err").innerHTML = `<div class="problem"><b>Pi-hole</b>${esc(err.message)}</div>`; return; }
    load(); return;
  }
  const rm = t.closest("[data-rmrule]");
  if (rm) {
    if (!confirm(`Ta bort ${rm.closest("tr").cells[0].textContent} ur Pi-holes ${rm.dataset.type === "allow" ? "tillåtlista" : "blocklista"}?`)) return;
    try { await phPost("api/pihole/rule/delete", { type: rm.dataset.type, kind: rm.dataset.kind, domain: rm.dataset.dom }); } catch (err) { $("ph-err").innerHTML = `<div class="problem"><b>Pi-hole</b>${esc(err.message)}</div>`; return; }
    load(); return;
  }
  const seg = t.closest("#ph-domtab button, #ph-cltab button, #ph-qn button, #ph-qf button");
  if (seg) {
    const g = seg.parentElement.id, v = seg.dataset.v;
    setPressed(seg.parentElement, seg);
    if (g === "ph-domtab") { ph.domTab = v; phSave("phDomTab", v); renderPihole(); }
    if (g === "ph-cltab") { ph.clTab = v; phSave("phClTab", v); renderPihole(); }
    if (g === "ph-qn") { ph.qn = +v; phSave("phQn", v); loadQueries(true); }
    if (g === "ph-qf") { ph.qf = v; phSave("phQf", v); loadQueries(true); }
    return;
  }
  if (t.closest("#ph-freeze")) { ph.frozen = !ph.frozen; renderQueries(); if (!ph.frozen) loadQueries(true); }
});
$("ph-add").addEventListener("submit", (e) => { e.preventDefault(); phOpen("block", $("ph-domain").value); });
const phq = $("ph-q");
phq.addEventListener("mouseenter", () => { ph.hover = true; });
phq.addEventListener("mouseleave", () => { ph.hover = false; if (!ph.frozen) loadQueries(); });
for (const [id, v] of [["ph-domtab", ph.domTab], ["ph-cltab", ph.clTab], ["ph-qn", String(ph.qn)], ["ph-qf", ph.qf]]) {
  const b = $(id).querySelector(`[data-v="${v}"]`); if (b) setPressed($(id), b);
}
setInterval(() => {
  const left = phLeft();
  if (left == null) return;
  document.querySelectorAll("[data-phleft]").forEach((x) => { x.textContent = x.closest(".chip") ? "pausad " + mmss(left) : mmss(left); });
  if (left === 0 && Date.now() - ph.fetchedAt > 3000) load();
}, 1000);

const map = new MapView(document.querySelector("#map svg"));
Promise.all([fetch("countries-110m.json").then((r) => r.json()), fetch("country-coords.json").then((r) => r.json())])
  .then(([world, cc]) => { coords = cc; map.setWorld(world); render(); })
  .catch(() => { $("problems").innerHTML += `<div class="problem"><b>Karta</b>Kartdata kunde inte laddas.</div>`; });
load();
setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS);
if (reduceMotion) document.documentElement.classList.add("reduce");
})();

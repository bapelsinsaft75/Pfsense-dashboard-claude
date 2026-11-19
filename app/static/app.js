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
        g.select("title").text(d.fs.map(tip).join("\n\n") + "\n\nKlicka för detaljer");
        g.attr("data-cc", d.cc).style("cursor", "pointer");
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
  $("events").innerHTML = ev.length ? ev.map((e) => `<div class="ev ${e.level}"><i></i><span class="t">${esc(e.title)}${e.resolved ? ' <span class="ok">· löst</span>' : ""}</span><span class="w">${ago(e.updated)}</span><span class="m">${esc(e.message || "")}</span></div>`).join("")
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
    return `<section class="card urcard" id="unraid-card-${i}" data-name="${esc(name)}"><h2>Unraid · ${esc(name)} <small>${esc(sub)}</small></h2>${err || '<div class="empty">Väntar på svar från servern …</div>'}</section>`;
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
  return `<section class="card urcard" id="unraid-card-${i}" data-name="${esc(name)}">
    <h2>Unraid · ${esc(name)} <small>${esc(sub)}</small></h2>${err}
    <div class="urtop">
      <div class="urtile"><span class="l">Array</span><span class="v${started ? "" : " bad"}">${started ? "Startad" : esc((u.array_state || "Okänd").toLowerCase())}</span><span class="s">${parBad ? '<span class="pill err">Parity ogiltig</span>' : '<span class="pill">Parity giltig</span>'}${parTxt ? " · " + parTxt : ""}</span></div>
      <div class="urtile"><span class="l">Lagring</span><span class="v num">${cap.used != null ? fmt(cap.used / 1e12, 1) : "–"}<small>av ${cap.total != null ? fmt(cap.total / 1e12, 0) : "–"} TB</small></span><div class="meter"><i class="${cap.pct >= 90 ? "hot" : ""}" style="width:${cap.pct || 0}%"></i></div><span class="s">${cap.free != null ? fmt(cap.free / 1e12, 1) + " TB ledigt" : ""}${cap.pct != null ? " · " + fmt(cap.pct, 0) + " %" : ""}</span></div>
      <div class="urtile"><span class="l">Processor</span><span class="v num">${cpu.pct != null ? fmt(cpu.pct, 0) : "–"}<small>%</small></span>${spark}<span class="s">${esc([cpu.brand, cpu.threads ? cpu.threads + " trådar" : null].filter(Boolean).join(" · "))}</span></div>
      <div class="urtile"><span class="l">Minne</span><span class="v num">${mem.used != null ? fmt(mem.used / GB, 1) : "–"}<small>av ${mem.total != null ? fmt(mem.total / GB, 0) : "–"} GB</small></span><div class="meter"><i style="width:${mem.pct || 0}%"></i></div><span class="s">${mem.pct != null ? fmt(mem.pct, 0) + " %" : ""}</span></div>
    </div>
    <div class="urgrid">
      <div class="urcol"><h3>Diskar</h3><div class="tablebox"><table><thead><tr><th>Disk</th><th>Enhet</th><th>Status</th><th>Använt</th><th class="r-right">Storlek</th><th class="r-right">Temp</th></tr></thead><tbody>${disks}</tbody></table></div></div>
      <div class="urcol"><div class="urhead"><h3>Docker · ${cs.filter((c) => c.state === "running").length} av ${cs.length} körs${w.containers.length ? ` · ${w.containers.length} övervakas` : ""}</h3>${segF("docker", f.docker)}</div><div class="ctrs">${ctrs}</div>${vmHtml}</div>
    </div></section>`;
}
function renderUnraids(d) {
  const list = d.unraids || [];
  $("unraid-cards").innerHTML = list.map(unraidCard).join("");
}
$("unraid-cards").addEventListener("click", async (e) => {
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
function renderPlex(d) {
  const p = d.plex || { servers: [], sessions: [] };
  const card = $("plex-card");
  card.hidden = !p.servers.length;
  if (!p.servers.length) return;
  const t = p.totals;
  $("plex-sum").innerHTML = `<div><b class="num">${t.streams}</b><span>${t.streams === 1 ? "ström" : "strömmar"} totalt</span></div>
    <div><b class="num">${t.direct}</b><span>direktuppspelning</span></div><div><b class="num">${t.transcode}</b><span>omkodning</span></div>
    <div><b class="num">${fmt(t.bandwidth_kbps / 1000, 1)}</b><span>Mbit/s totalt</span></div><div><b class="num">${t.remote}</b><span>utanför hemmet</span></div>`;
  const down = p.servers.filter((s) => !s.ok && s.error);
  $("plex-streams").innerHTML = p.sessions.length ? p.sessions.map((x, i) => {
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
  }).join("") : `<div class="empty">Inga strömmar från Plex just nu.</div>`;
  $("plex-err").innerHTML = down.map((s) => `<div class="problem"><b>${esc(s.name)}</b>${esc(s.error)}</div>`).join("");
}

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
  $("cd-sub").textContent = `Hämtar ${PERIOD_LABEL[state.period]} …`;
  $("cd-clients").innerHTML = ""; $("cd-remotes").innerHTML = "";
  if (dlg.showModal) { if (!dlg.open) dlg.showModal(); } else dlg.setAttribute("open", "");
  try {
    const r = await fetch(`api/country/${encodeURIComponent(cc)}?period=${state.period}`, { cache: "no-store" });
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
  document.querySelectorAll(".stabs [data-tab]").forEach((x) => x.setAttribute("aria-selected", x === b));
  document.querySelectorAll(".spane").forEach((p) => { p.hidden = p.dataset.pane !== b.dataset.tab; });
});
let recips = [], mailConfigured = false, serverList = [];
function readRecips() {
  return [...$("s-recips").querySelectorAll(".recip")].map((row) => ({
    email: row.querySelector(".r-email").value.trim(), name: row.querySelector(".r-name").value.trim(),
    kinds: [...row.querySelectorAll("[data-rkind]:checked")].map((c) => c.dataset.rkind),
    servers: !serverList.length || row.querySelector(".r-allsrv").checked ? null
      : [...row.querySelectorAll("[data-rsrv]:checked")].map((c) => c.dataset.rsrv),
  }));
}
function renderRecips() {
  $("s-recips").innerHTML = recips.length ? recips.map((r, i) => {
    const n = r.kinds.length;
    return `<div class="recip" data-i="${i}">
      <div class="rtop"><input type="email" class="r-email" value="${esc(r.email)}" placeholder="namn@exempel.se" aria-label="E-postadress" required>
        <input type="text" class="r-name" value="${esc(r.name || "")}" placeholder="Namn (valfritt)" aria-label="Namn">
        <button type="button" class="btn" data-rtest="${i}" ${mailConfigured ? "" : "disabled"}>Testa</button>
        <button type="button" class="btn" data-rdel="${i}" aria-label="Ta bort mottagare">Ta bort</button></div>
      <details ${r.open ? "open" : ""}><summary>Larm som skickas hit: <b>${n} av ${alertKinds.length}</b></summary>
        <div class="rquick"><button type="button" class="linkbtn" data-rall="${i}">Alla</button><button type="button" class="linkbtn" data-rcrit="${i}">Bara kritiska</button><button type="button" class="linkbtn" data-rnone="${i}">Inga</button></div>
        <div class="rkinds">${alertKinds.map((k) => `<label class="check"><input type="checkbox" data-rkind="${k.id}" ${r.kinds.includes(k.id) ? "checked" : ""}> ${esc(k.label)}</label>`).join("")}</div>
        ${serverList.length ? `<div class="rsrv"><div><b>Servrar</b> <span class="muted">· gäller larm om Plex och Unraid</span></div>
          <label class="check"><input type="checkbox" class="r-allsrv" ${r.servers == null ? "checked" : ""}> Alla servrar</label>
          <div class="rsrvlist">${serverList.map((sv) => `<label class="check"><input type="checkbox" data-rsrv="${esc(sv.name)}" ${r.servers == null || r.servers.includes(sv.name) ? "checked" : ""} ${r.servers == null ? "disabled" : ""}> ${esc(sv.name)} <span class="muted">${sv.type}</span></label>`).join("")}</div></div>` : ""}
      </details></div>`;
  }).join("") : '<p class="muted">Inga mottagare. Lägg till en för att få larm via e-post.</p>';
}
function syncRecips() {
  const opened = [...$("s-recips").querySelectorAll(".recip details")].map((d) => d.open);
  recips = readRecips().map((r, i) => ({ ...r, open: opened[i] }));
}
$("s-add-recip").addEventListener("click", () => {
  syncRecips();
  recips.push({ email: "", name: "", kinds: alertKinds.filter((k) => k.critical).map((k) => k.id), open: true });
  renderRecips();
  const rows = $("s-recips").querySelectorAll(".r-email"); rows[rows.length - 1].focus();
});
$("s-recips").addEventListener("change", (e) => {
  if (e.target.matches(".r-allsrv")) {
    e.target.closest(".recip").querySelectorAll("[data-rsrv]").forEach((c) => { c.disabled = e.target.checked; if (e.target.checked) c.checked = true; });
    return;
  }
  if (!e.target.matches("[data-rkind]")) return;
  const row = e.target.closest(".recip");
  row.querySelector("summary b").textContent = `${row.querySelectorAll("[data-rkind]:checked").length} av ${alertKinds.length}`;
});
$("s-recips").addEventListener("click", async (e) => {
  const b = e.target.closest("button"); if (!b) return;
  const i = +(b.dataset.rdel ?? b.dataset.rall ?? b.dataset.rcrit ?? b.dataset.rnone ?? b.dataset.rtest ?? -1);
  if (i < 0) return;
  syncRecips();
  if (b.dataset.rdel != null) { recips.splice(i, 1); renderRecips(); return; }
  if (b.dataset.rall != null) recips[i].kinds = alertKinds.map((k) => k.id);
  if (b.dataset.rcrit != null) recips[i].kinds = alertKinds.filter((k) => k.critical).map((k) => k.id);
  if (b.dataset.rnone != null) recips[i].kinds = [];
  if (b.dataset.rtest != null) {
    const to = recips[i].email;
    $("s-msg").textContent = `Skickar testmejl till ${to} …`; $("s-msg").className = "formmsg";
    try {
      const r = await fetch("api/test-email", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ to }) });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || r.status);
      $("s-msg").textContent = `Testmejl skickat till ${to}.`; $("s-msg").className = "formmsg ok";
    } catch (err) { $("s-msg").textContent = err.message; $("s-msg").className = "formmsg err"; }
    return;
  }
  recips[i].open = true;
  renderRecips();
});
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
    $("s-kinds").innerHTML = alertKinds.map((k) => `<label class="check"><input type="checkbox" data-kind="${k.id}" ${st.alert_kinds[k.id] ? "checked" : ""}> ${esc(k.label)}${k.confirmed ? ' <span class="muted" title="Bekräftas med upprepade kontroller">↻</span>' : ""}</label>`).join("");
    $("s-quiet").checked = !!st.quiet_enabled; setTime("s-qstart", st.quiet_start); setTime("s-qend", st.quiet_end);
    $("s-qdays").innerHTML = DAYS.map((d, i) => `<label class="day"><input type="checkbox" value="${i}" ${st.quiet_days.includes(i) ? "checked" : ""}><span>${d}</span></label>`).join("");
    $("s-qcrit").checked = !!st.quiet_allow_critical; $("s-qdigest").checked = !!st.quiet_digest;
    $("s-slackwarn").checked = !!st.slack_warnings;
    mailConfigured = j.email.enabled;
    serverList = [...(j.servers?.plex || []).map((n) => ({ name: n, type: "Plex" })), ...(j.servers?.unraid || []).map((n) => ({ name: n, type: "Unraid" }))];
    recips = st.recipients ? st.recipients.map((r) => ({ ...r }))
      : j.email.to.map((email) => ({ email, name: "", kinds: alertKinds.filter((k) => k.critical || (st.email_warnings && st.alert_kinds[k.id])).map((k) => k.id) }));
    renderRecips();
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
  const kinds = {};
  $("s-kinds").querySelectorAll("[data-kind]").forEach((c) => { kinds[c.dataset.kind] = c.checked; });
  const body = {
    retention_days: +$("s-retention").value, anomaly_factor: +$("s-factor").value, anomaly_min_gb: +$("s-mingb").value,
    suricata_min_priority: +$("s-prio").value, pfblocker_threshold: +$("s-pfb").value,
    confirm_interval: +$("s-interval").value, confirm_count: +$("s-count").value, alert_kinds: kinds,
    quiet_enabled: $("s-quiet").checked, quiet_start: getTime("s-qstart"), quiet_end: getTime("s-qend"),
    quiet_days: [...$("s-qdays").querySelectorAll("input:checked")].map((c) => +c.value),
    quiet_allow_critical: $("s-qcrit").checked, quiet_digest: $("s-qdigest").checked,
    slack_warnings: $("s-slackwarn").checked,
    recipients: readRecips(),
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
    return `<button type="button" class="chip plex ${cls}" data-goto="plex-card" title="${esc(tip)} · Klicka för att se strömmarna">${esc(p.name)}${p.ok ? ` <small>${p.streams}</small>` : ""}</button>`;
  });
  const unraidChip = (d.unraids || []).map((x, i) => {
    const u = x.status, name = u.name || `Unraid ${i + 1}`;
    if (!u.ok && u.error) problems.push([name, u.error]);
    return `<button type="button" class="chip unraid ${u.ok ? (u.level === "ok" ? "ok" : u.level) : (u.error ? "err" : "")}" data-goto="unraid-card-${i}" title="${esc(name + " · " + (u.ok ? u.detail : (u.error || "Väntar på svar")) + " · Klicka för detaljer")}">${esc(name)}</button>`;
  });
  const vpnAt = chips.findIndex((c) => c.includes(">VPN<"));
  chips.splice(vpnAt >= 0 ? vpnAt + 1 : 1, 0, ...unraidChip, ...plexChips);
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
  $("k-total").innerHTML = bytesParts(k.total_bytes);
  $("k-total-s").textContent = PERIOD_LABEL[d.period];
  $("k-now").innerHTML = `${fmt((k.now_down_bps + k.now_up_bps) / 1e6, 0)}<small>Mbit/s</small>`;
  $("k-now-s").textContent = `${fmt(k.now_down_bps / 1e6, 0)} ned · ${fmt(k.now_up_bps / 1e6, 0)} upp · ${k.active_conns} anslutningar`;
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

function renderCountries(d) {
  const by = d3.rollups(d.flows, (v) => ({ o: d3.sum(v.filter((f) => f.dir === "out"), (f) => f.bytes), i: d3.sum(v.filter((f) => f.dir === "in"), (f) => f.bytes), n: d3.sum(v, (f) => f.conns), hasIn: v.some((f) => f.dir === "in") }), (f) => f.cc)
    .sort((a, b) => (b[1].o + b[1].i) - (a[1].o + a[1].i));
  const all = d3.sum(by, (x) => x[1].o + x[1].i) || 1;
  $("ctrylist").innerHTML = by.length ? by.slice(0, 9).map(([cc, v]) => {
    const t = v.o + v.i, share = t / all * 100;
    return `<div class="crow pickable" data-cc="${esc(cc)}" title="Visa vilka adresser och klienter som pratar med ${esc(countryName(cc))}"><span class="f">${esc(countryName(cc))}</span><span class="num">${fmtBytes(t)}</span><div class="split"><div class="o" style="width:${t ? share * v.o / t : 0}%"></div><div class="i" style="width:${v.hasIn ? Math.max(1.2, t ? share * v.i / t : 0) : 0}%"></div></div><span class="m">${fmt(v.n, 0)} anslutningar${v.hasIn ? " · varav utifrån" : ""}</span><span class="m num">${fmt(share, 0)} %</span></div>`;
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
  if (!k) {
    const random = c.mac && /^.[26ae]/i.test(c.mac);
    return `<span class="muted" title="UniFi känner inte till klienten">Okänd${random ? ' <small title="Lokalt administrerad MAC-adress: mobiler och datorer slumpar den för integritetens skull">· slumpad MAC</small>' : ""}</span>`;
  }
  if (k.kind === "wifi") return `<span class="conn">${icon("wifi")}<span>Wi-Fi ${esc(k.band || "")}</span>${k.rssi != null ? sig(k.rssi) + `<small>${k.rssi} dBm</small>` : ""}</span>`;
  return `<span class="conn">${icon("cable")}<span>${esc(k.dev_name || "Kabel")}</span>${k.port ? `<small>port ${k.port}</small>` : ""}</span>`;
}

function renderTable(d) {
  const editingInput = document.activeElement && document.activeElement.id === "rename-input";
  const caret = editingInput ? document.activeElement.selectionStart : null;
  const q = $("search").value.trim().toLowerCase();
  const ipn = (ip) => (ip && ip.includes(".") ? ip.split(".").reduce((a, x) => a * 256 + +x, 0) : 0);
  const val = (c) => state.sortKey === "conn" ? (c.conn ? (c.conn.kind === "wifi" ? "0" + (c.conn.band || "") + String(200 + (c.conn.rssi || 0)).padStart(3, "0") : "1" + (c.conn.dev_name || "") + String(c.conn.port || 0).padStart(3, "0")) : "2")
    : state.sortKey === "now" ? c.down_bps + c.up_bps : state.sortKey === "ip" ? ipn(c.ip) : state.sortKey === "name" ? c.name.toLowerCase() : c.bytes;
  const dev = state.devF;
  const connOk = (c) => !state.connF || (state.connF === "unknown" ? !c.conn : c.conn && c.conn.kind === state.connF);
  const statusOk = (c) => !state.statusF || (state.statusF === "online" ? c.online : !c.online);
  const rows = d.clients.filter((c) => connOk(c) && statusOk(c) && (!dev || (c.conn && c.conn.dev === dev))
    && (!q || `${c.name} ${c.ip || ""} ${c.mac || ""}`.toLowerCase().includes(q)))
    .sort((a, b) => { const x = val(a), y = val(b); return (x > y ? 1 : x < y ? -1 : 0) * state.sortDir; });
  document.querySelector("#table tbody").innerHTML = rows.length ? rows.map((c) => `<tr class="pickable${state.mapFilter === c.id ? " sel" : ""}" data-id="${esc(c.id)}"><td>${nameCell(c)}</td><td class="ip">${esc(c.ip || "–")}</td><td>${connHTML(c)}</td><td>${esc((c.mac || "–").toUpperCase())}</td><td><span class="pill${c.online ? "" : " idle"}">${c.online ? "Online" : "Inaktiv"}</span></td><td class="r-right num">${c.online && (c.down_bps + c.up_bps) > 0 ? fmtRate(c.down_bps + c.up_bps) + " Mbit/s" : "–"}</td><td class="r-right num">${fmtBytes(c.bytes)}</td></tr>`).join("")
    : `<tr><td colspan="7" class="muted">Inga klienter matchar filtret.</td></tr>`;
  $("filter-count").textContent = rows.length === d.clients.length ? `${rows.length} klienter` : `${rows.length} av ${d.clients.length} klienter`;
  $("unknown-hint").hidden = state.connF !== "unknown";
  if (editingInput) { const i = $("rename-input"); if (i) { i.focus(); if (caret != null) i.setSelectionRange(caret, caret); } }
  document.querySelectorAll("#table th button").forEach((b) => {
    const base = b.textContent.replace(/ [↑↓]$/, "");
    b.textContent = b.dataset.sort === state.sortKey ? `${base} ${state.sortDir < 0 ? "↓" : "↑"}` : base;
  });
}

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
  renderStatus(d); renderKpis(d); renderCountries(d); renderChart(d); renderTop(d); renderDevices(d); renderTable(d); renderMapFilter();
  map.home = d.home;
  map.flows = d.flows;
  map.filter = state.mapFilter;
  map.render();
  if (window.onDashRender) window.onDashRender(d);
}
window.dashState = state;
window.dashRender = () => render();

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
$("statusf").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; state.statusF = b.dataset.s; setPressed(e.currentTarget, b); render(); });
function scrollToCard(id) {
  if (window.layoutGoto && window.layoutGoto(id)) return;
  const el = $(id);
  if (!el || el.hidden) return;
  const top = el.getBoundingClientRect().top + window.scrollY - $("alerts").offsetHeight - 12;
  window.scrollTo({ top, behavior: reduceMotion ? "auto" : "smooth" });
  el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash");
}
$("chips").addEventListener("click", (e) => { const b = e.target.closest("[data-goto]"); if (b) scrollToCard(b.dataset.goto); });
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

const map = new MapView(document.querySelector("#map svg"));
Promise.all([fetch("countries-110m.json").then((r) => r.json()), fetch("country-coords.json").then((r) => r.json())])
  .then(([world, cc]) => { coords = cc; map.setWorld(world); render(); })
  .catch(() => { $("problems").innerHTML += `<div class="problem"><b>Karta</b>Kartdata kunde inte laddas.</div>`; });
load();
setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS);
if (reduceMotion) document.documentElement.classList.add("reduce");
})();

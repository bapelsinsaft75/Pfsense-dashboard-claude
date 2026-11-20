/* Utseenden: Klassisk, Sidomeny, Statusrutor och Flikar och rader.
 * Samma delar av sidan (karta, klienter, servrar ...) flyttas till rätt plats för valt utseende.
 * Valet sparas i webbläsaren och kan styras med ?layout=klassisk|sidomeny|rutor|flikar i adressen.
 */
(function () {
"use strict";

const LAYOUTS = {
  klassisk: "Klassisk",
  sidomeny: "Sidomeny",
  rutor: "Statusrutor",
  flikar: "Flikar och rader",
  overblick: "Överblick",
};
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n, d = 1) => Number(n || 0).toLocaleString("sv-SE", { maximumFractionDigits: d });
const store = {
  get(k, def) { try { const v = localStorage.getItem(k); return v == null ? def : JSON.parse(v); } catch (e) { return def; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* ingen lagring */ } },
};
const ICON = {
  overview: "M3 3h8v8H3z M13 3h8v5h-8z M13 10h8v11h-8z M3 13h8v8H3z",
  network: "M12 3a9 9 0 1 0 0 18a9 9 0 0 0 0-18z M3 12h18 M12 3c3 3 3 15 0 18 M12 3c-3 3-3 15 0 18",
  clients: "M4 6h16v10H4z M2 19h20",
  unifi: "M2 8h20v8H2z M6 12h.01 M10 12h.01 M14 12h.01",
  security: "M12 3l8 3v6c0 5-3.5 8-8 9c-4.5-1-8-4-8-9V6z",
  server: "M4 4h16v6H4z M4 14h16v6H4z M8 7h.01 M8 17h.01",
  plex: "M8 5l11 7l-11 7z",
  events: "M6 16V11a6 6 0 0 1 12 0v5l2 2H4z M10 20a2 2 0 0 0 4 0",
  internet: "M5 12a7 7 0 0 1 14 0 M8 12a4 4 0 0 1 8 0 M12 12v7",
  pihole: "M3 4h18l-7 8v6l-4 2v-8z",
  vpn: "M6 11V8a6 6 0 0 1 12 0v3 M5 11h14v10H5z M12 15v2",
};
const icon = (k) => `<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="${ICON[k] || ICON.overview}"/></svg>`;

// Delarna av sidan, i den ordning de har i Klassisk.
const el = {};
const ORDER = ["head", "demo", "problems", "kpis", "map", "chart", "pihole", "security", "unifi", "clients", "unraid", "plex", "foot"];
function grab() {
  const wrap = document.querySelector(".wrap");
  Object.assign(el, {
    wrap, alerts: $("alerts"), head: wrap.querySelector(".head"), demo: $("demo"), problems: $("problems"),
    kpis: $("sec-kpis"), map: $("sec-map"), chart: $("sec-chart"), security: $("sec-security"),
    unifi: $("unifi-card"), clients: $("clients-card"), unraid: $("unraid-cards"), plex: $("plex-card"), pihole: $("pihole-card"), ovw: $("ovw"),
    foot: wrap.querySelector(".foot"),
  });
  el.store = document.createElement("div");
  el.store.hidden = true;
  el.store.id = "layout-store";
  document.body.appendChild(el.store);
}

// Sidor/sektioner som de andra utseendena bygger på
const SECTIONS = [
  { id: "oversikt", title: "Översikt", icon: "overview", parts: ["kpis"] },
  { id: "natverk", title: "Nätverk", icon: "network", parts: ["map", "chart"] },
  { id: "klienter", title: "Klienter", icon: "clients", parts: ["clients"] },
  { id: "unifi", title: "UniFi", icon: "unifi", parts: ["unifi"] },
  { id: "sakerhet", title: "Säkerhet", icon: "security", parts: ["security"] },
  { id: "pihole", title: "Pi-hole", icon: "pihole", parts: ["pihole"] },
  { id: "servrar", title: "Servrar", icon: "server", parts: ["unraid"] },
  { id: "plex", title: "Plex", icon: "plex", parts: ["plex"] },
];
// Mål för statusmarkeringarnas klick (data-goto) och kortens id
const GOTO = { "clients-card": "klienter", "plex-card": "plex", "unifi-card": "unifi", "pihole-card": "pihole" };

let layout = "klassisk";
let page = "oversikt";        // sidomeny
let drawer = null;            // rutor: öppen ruta
let unraidFilter = null;      // visa bara en Unraid-server (index)
let built = {};

/* ── sammanfattningar ── */
function summary(d) {
  const k = d.kpi, sec = d.security || {}, t = sec.totals || {};
  const alerts = (d.alerts || []).filter((a) => !a.resolved);
  const crit = alerts.filter((a) => a.level === "critical").length;
  const unraids = d.unraids || [];
  const plex = d.plex || { servers: [], totals: {} };
  const ph = d.pihole;
  const vpn = d.status.vpn || {};
  const urLevel = (u) => (!u.status.ok ? (u.status.error ? "err" : "off") : u.status.level === "ok" ? "ok" : u.status.level);
  const worst = (levels) => (levels.includes("err") ? "err" : levels.includes("warn") ? "warn" : levels.length ? "ok" : "off");
  const plexLevel = !plex.servers.length ? "off" : worst(plex.servers.map((p) => (p.ok ? "ok" : p.error ? "err" : "off")));
  const secLevel = alerts.some((a) => ["suricata", "pfblocker"].includes(a.kind)) ? "warn" : "ok";
  const mbit = (k.now_down_bps + k.now_up_bps) / 1e6;
  const countries = new Set(d.flows.map((f) => f.cc)).size;
  return {
    alerts, crit,
    sections: {
      oversikt: { level: crit ? "err" : alerts.length ? "warn" : "ok", badge: alerts.length || "", text: `${fmt(mbit, 0)} Mbit/s nu · ${k.clients_online} klienter online${vpn.enabled ? ` · VPN ${vpn.ok ? "ansluten" : "nere"}` : ""}` },
      natverk: { level: "ok", badge: countries ? `${countries} länder` : "", text: `${countries} länder · ${fmt(k.now_down_bps / 1e6, 0)} ned · ${fmt(k.now_up_bps / 1e6, 0)} upp · ${k.active_conns} anslutningar` },
      klienter: { level: "ok", badge: k.clients_online, text: `${k.clients_online} online${k.wifi || k.wired ? ` · ${k.wifi} trådlöst · ${k.wired} kabel` : ""}${k.unknown_link && (k.wifi || k.wired) ? ` · ${k.unknown_link} okända` : ""} · ${k.clients_total - k.clients_online} inaktiva` },
      unifi: { level: d.status.unifi.enabled ? (d.status.unifi.ok ? "ok" : "err") : "off", badge: d.devices.length || "", text: d.devices.length ? `${d.devices.length} enheter · ${d.devices.filter((x) => !x.online).length} offline` : "Inte konfigurerat" },
      sakerhet: { level: secLevel, badge: t.suricata || "", text: `${fmt(t.suricata || 0, 0)} Suricata-larm · ${fmt(t.pfblockerng || 0, 0)} pfBlockerNG-blockeringar senaste dygnet` },
      servrar: { level: worst(unraids.map(urLevel)), badge: unraids.length || "", text: unraids.length ? unraids.map((u) => `${u.status.name}: ${u.status.ok ? (u.status.level === "ok" ? "OK" : u.status.detail) : (u.status.error ? "svarar inte" : "väntar")}`).join(" · ") : "Inga Unraid-servrar" },
      pihole: !ph ? { level: "off", badge: "", text: "Inte konfigurerat" }
        : !ph.ok ? { level: "err", badge: "!", text: ph.error || "Väntar på svar" }
        : ph.blocking !== "enabled" ? { level: "warn", badge: "paus", text: "Blockeringen är pausad" }
        : { level: "ok", badge: ph.percent_blocked != null ? `${fmt(ph.percent_blocked, 0)} %` : "", text: `${fmt(ph.total_24h || 0, 0)} DNS-frågor · ${fmt(ph.percent_blocked || 0, 1)} % blockerade senaste dygnet` },
      plex: { level: plexLevel, badge: plex.servers.length ? (plex.totals.streams ?? 0) : "", text: plex.servers.length ? `${plex.totals.streams} strömmar · ${plex.totals.transcode} omkodning · ${fmt((plex.totals.bandwidth_kbps || 0) / 1000, 1)} Mbit/s` : "Inte konfigurerat" },
    },
    unraids: unraids.map((u, i) => ({ i, name: u.status.name || `Unraid ${i + 1}`, level: urLevel(u), u })),
    vpn, mbit, countries, k, t, plex, ph,
  };
}

/* ── gemensamt: placera delar ── */
/* delar som är dolda i den här webbläsaren (sparas med profilen) */
const PART_LABEL = { kpis: "Nyckeltal", map: "Karta och länder", chart: "Över tid och topplista", pihole: "Pi-hole",
  security: "Säkerhet och händelser", unifi: "UniFi-enheter", clients: "Klienter", unraid: "Unraid", plex: "Plex" };
let hidden = new Set(store.get("hiddenParts", []));
const ALWAYS = new Set(["head", "demo", "problems", "foot", "ovw"]);
const shown = (sectionId) => { const s = SECTIONS.find((x) => x.id === sectionId); return !s || s.parts.some((p) => !hidden.has(p)); };
function put(container, names) {
  for (const n of names) if (el[n] && (container === el.store || ALWAYS.has(n) || !hidden.has(n))) container.appendChild(el[n]);
}
function stash(names) { put(el.store, names); }
function clearWrap() {
  el.store.appendChild(el.alerts);
  stash(ORDER);
  stash(["ovw"]);
  if (el.ovw) el.ovw.hidden = true;
  el.wrap.innerHTML = "";
}
function inbox(target) {
  // larmen visas som en ruta i sidan i stället för som banner överst
  el.alerts.classList.add("inbox");
  target.appendChild(el.alerts);
}

/* ── Klassisk ── */
function buildKlassisk() {
  clearWrap();
  el.alerts.classList.remove("inbox");
  el.wrap.parentNode.insertBefore(el.alerts, el.wrap);
  put(el.wrap, ORDER);
}

/* ── Överblick: är något fel och var sitter flaskhalsen – sedan allt annat ── */
function buildOverblick() {
  clearWrap();
  el.alerts.classList.remove("inbox");
  el.wrap.parentNode.insertBefore(el.alerts, el.wrap);
  put(el.wrap, ["head", "demo", "problems", "ovw"]);
  el.ovw.hidden = false;
  document.body.dataset.layout = "overblick";
  put(el.wrap, ORDER.filter((n) => !["head", "demo", "problems"].includes(n)));
  if (window.loadOverview) window.loadOverview();
}

/* ── Flikar och rader ── */
function buildFlikar() {
  clearWrap();
  const bar = document.createElement("nav");
  bar.className = "ltabs";
  bar.setAttribute("aria-label", "Delar");
  bar.innerHTML = SECTIONS.filter((s) => shown(s.id)).map((s) => `<a href="#rad-${s.id}" data-sec="${s.id}">${s.title}<span class="ldot" data-dot="${s.id}"></span></a>`).join("");
  put(el.wrap, ["head"]);
  el.wrap.appendChild(bar);
  put(el.wrap, ["demo", "problems"]);
  const ib = document.createElement("section");
  ib.className = "lsec inboxsec open";
  ib.id = "rad-atgard";
  ib.innerHTML = `<div class="lsh" role="button" tabindex="0"><span class="chev">▸</span><b>Behöver åtgärd</b><span class="lsum" data-sum="atgard"></span></div><div class="lsb"></div>`;
  el.wrap.appendChild(ib);
  inbox(ib.querySelector(".lsb"));
  const collapsed = store.get("layoutCollapsed", {});
  for (const s of SECTIONS.filter((x) => shown(x.id))) {
    const sec = document.createElement("section");
    sec.className = "lsec" + (collapsed[s.id] ? "" : " open");
    sec.id = `rad-${s.id}`;
    sec.innerHTML = `<div class="lsh" role="button" tabindex="0" aria-expanded="${!collapsed[s.id]}"><span class="chev">▸</span><b>${s.title}</b><span class="lsum" data-sum="${s.id}"></span></div><div class="lsb"></div>`;
    put(sec.querySelector(".lsb"), s.parts);
    el.wrap.appendChild(sec);
  }
  put(el.wrap, ["foot"]);
  if (!built.flikarListeners) {
    built.flikarListeners = true;
    el.wrap.addEventListener("click", flikarClick);
    el.wrap.addEventListener("keydown", (e) => {
      if (layout === "flikar" && (e.key === "Enter" || e.key === " ") && e.target.classList.contains("lsh")) { e.preventDefault(); toggleRow(e.target.closest(".lsec")); }
    });
  }
}
function toggleRow(sec, open) {
  const want = open ?? !sec.classList.contains("open");
  sec.classList.toggle("open", want);
  sec.querySelector(".lsh").setAttribute("aria-expanded", want);
  const id = sec.id.replace(/^rad-/, "");
  if (id !== "atgard") { const c = store.get("layoutCollapsed", {}); c[id] = !want; store.set("layoutCollapsed", c); }
  if (want) window.dashRender && window.dashRender();
}
function flikarClick(e) {
  if (layout !== "flikar") return;
  const h = e.target.closest(".lsh");
  if (h && !e.target.closest("button, a, input, select")) { toggleRow(h.parentElement); return; }
  const a = e.target.closest(".ltabs a");
  if (a) { e.preventDefault(); gotoRow(a.dataset.sec); }
}
function gotoRow(id) {
  const sec = $(`rad-${id}`);
  if (!sec) return false;
  toggleRow(sec, true);
  const top = sec.getBoundingClientRect().top + window.scrollY - 56;
  window.scrollTo({ top, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  return true;
}

/* ── Sidomeny ── */
function buildSidomeny() {
  clearWrap();
  const side = document.createElement("aside");
  side.className = "lside";
  side.setAttribute("aria-label", "Meny");
  const main = document.createElement("div");
  main.className = "lmain";
  el.wrap.appendChild(side);
  el.wrap.appendChild(main);
  put(main, ["head", "demo", "problems"]);
  const pg = document.createElement("div");
  pg.className = "lpage";
  pg.id = "lpage";
  main.appendChild(pg);
  put(main, ["foot"]);
  side.addEventListener("click", (e) => {
    const a = e.target.closest("[data-page]"); if (!a) return;
    e.preventDefault();
    setPage(a.dataset.page, a.dataset.ur != null ? +a.dataset.ur : null);
  });
  const h = location.hash.match(/^#sida-([a-z]+)(?:-(\d+))?$/);
  if (h) { page = h[1]; unraidFilter = h[2] != null ? +h[2] : null; }
  showPage();
}
function setPage(p, ur = null) {
  page = p; unraidFilter = ur;
  try { history.replaceState(null, "", `#sida-${p}${ur != null ? "-" + ur : ""}`); } catch (e) { /* ignoreras */ }
  showPage();
  window.scrollTo({ top: 0 });
}
function showPage() {
  const pg = $("lpage");
  if (!pg) return;
  stash(SECTIONS.flatMap((s) => s.parts));
  pg.innerHTML = "";
  const s = SECTIONS.find((x) => x.id === page) || SECTIONS[0];
  const head = document.createElement("h2");
  head.className = "lpagetitle";
  head.textContent = s.id === "servrar" && unraidFilter != null ? (lastSum?.unraids[unraidFilter]?.name || s.title) : s.title;
  pg.appendChild(head);
  if (s.id === "oversikt") inbox(pg); else stashAlerts();
  put(pg, s.parts);
  applyUnraidFilter();
  window.dashRender && window.dashRender();
}
function stashAlerts() { el.store.appendChild(el.alerts); }

/* ── Statusrutor ── */
function buildRutor() {
  clearWrap();
  put(el.wrap, ["head", "demo", "problems"]);
  const ib = document.createElement("div");
  ib.className = "linbox";
  el.wrap.appendChild(ib);
  inbox(ib);
  const bar = document.createElement("div");
  bar.className = "ltbar";
  bar.id = "ltbar";
  el.wrap.appendChild(bar);
  const grid = document.createElement("div");
  grid.className = "ltiles";
  grid.id = "ltiles";
  el.wrap.appendChild(grid);
  bar.addEventListener("click", tileBarClick);
  grid.addEventListener("pointerdown", tileDown);
  grid.addEventListener("keydown", (e) => {
    const mv = e.target.closest("[data-mv]");
    if (mv && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); e.stopPropagation(); moveTile(mv.closest(".ltile").dataset.key, +mv.dataset.mv); }
  });

  put(el.wrap, ["foot"]);
  const dr = document.createElement("div");
  dr.className = "ldrawer-wrap";
  dr.id = "ldrawer";
  dr.hidden = true;
  dr.innerHTML = `<div class="lshade"></div><aside class="ldrawer" role="dialog" aria-modal="true" aria-labelledby="ldrawer-title"><div class="ldh"><h2 id="ldrawer-title"></h2><button type="button" class="btn" id="ldrawer-close">Stäng ✕</button></div><div class="ldb" id="ldrawer-body"></div></aside>`;
  document.body.appendChild(dr);
  built.drawer = dr;
  dr.querySelector(".lshade").addEventListener("click", closeDrawer);
  $("ldrawer-close").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", escClose);
  grid.addEventListener("click", (e) => {
    const mv = e.target.closest("[data-mv]");
    if (mv) { e.preventDefault(); moveTile(mv.closest(".ltile").dataset.key, +mv.dataset.mv); return; }
    if (tileEdit) return;
    const t = e.target.closest("[data-tile]"); if (!t) return;
    openDrawer(t.dataset.tile, t.dataset.ur != null ? +t.dataset.ur : null);
  });
}
function escClose(e) { if (e.key === "Escape" && drawer) closeDrawer(); }
function openDrawer(sectionId, ur = null) {
  const s = SECTIONS.find((x) => x.id === sectionId);
  if (!s) return false;
  drawer = sectionId; unraidFilter = ur;
  const body = $("ldrawer-body");
  stash(SECTIONS.flatMap((x) => x.parts));
  put(body, s.parts);
  $("ldrawer-title").textContent = s.id === "servrar" && ur != null ? (lastSum?.unraids[ur]?.name || s.title) : s.title;
  built.drawer.hidden = false;
  document.body.classList.add("ldrawer-open");
  applyUnraidFilter();
  window.dashRender && window.dashRender();
  $("ldrawer-close").focus();
  return true;
}
function closeDrawer() {
  drawer = null; unraidFilter = null;
  stash(SECTIONS.flatMap((x) => x.parts));
  if (built.drawer) built.drawer.hidden = true;
  document.body.classList.remove("ldrawer-open");
}
/* ── egen ordning på rutorna: dra och släpp, eller pilar ── */
let tileEdit = false, tileDrag = null, tileKeys = [];
const tileKey = (t) => t.key || (t.ur != null ? `ur:${t.title}` : t.tile);
function saveOrder(keys) { store.set("tileOrder", keys); }
function moveTile(key, dir) {
  const keys = [...tileKeys], i = keys.indexOf(key), j = i + dir;
  if (i < 0 || j < 0 || j >= keys.length) return;
  [keys[i], keys[j]] = [keys[j], keys[i]];
  saveOrder(keys);
  if (lastSum) renderTiles(lastSum);
  const b = document.querySelector(`.ltile[data-key="${CSS.escape(key)}"] [data-mv="${dir}"]`); if (b) b.focus();
}
function tileBarClick(e) {
  const b = e.target.closest("button"); if (!b) return;
  if (b.dataset.act === "edit") tileEdit = !tileEdit;
  if (b.dataset.act === "reset") { store.set("tileOrder", null); }
  if (lastSum) renderTiles(lastSum);
}
// dra med mus eller finger (pekarhändelser – fungerar även på mobil)
let tileStart = null;
function tileTarget(x, y) {
  const el2 = document.elementFromPoint(x, y);
  const t = el2 && el2.closest(".ltile");
  if (!t || t.dataset.key === tileDrag) return null;
  const r = t.getBoundingClientRect();
  return { t, right: x > r.left + r.width / 2 };
}
function tileDown(e) {
  if (!tileEdit || e.button > 0 || e.target.closest("[data-mv]")) return;
  const t = e.target.closest(".ltile"); if (!t) return;
  tileStart = { key: t.dataset.key, x: e.clientX, y: e.clientY, el: t };
  window.addEventListener("pointermove", tileMove);
  window.addEventListener("pointerup", tileUp, { once: true });
  window.addEventListener("pointercancel", tileUp, { once: true });
}
function tileMove(e) {
  if (!tileStart) return;
  if (!tileDrag) {
    if (Math.hypot(e.clientX - tileStart.x, e.clientY - tileStart.y) < 6) return;
    tileDrag = tileStart.key;
    tileStart.el.classList.add("dragging");
  }
  e.preventDefault();
  tileStart.el.style.transform = `translate(${e.clientX - tileStart.x}px,${e.clientY - tileStart.y}px)`;
  document.querySelectorAll(".dropl,.dropr").forEach((x) => x.classList.remove("dropl", "dropr"));
  const hit = tileTarget(e.clientX, e.clientY);
  if (hit) hit.t.classList.add(hit.right ? "dropr" : "dropl");
}
function tileUp(e) {
  window.removeEventListener("pointermove", tileMove);
  const st = tileStart; tileStart = null;
  if (!st || !tileDrag) { tileDrag = null; return; }
  st.el.style.transform = "";
  const hit = e.type === "pointerup" ? tileTarget(e.clientX, e.clientY) : null;
  const dragged = tileDrag; tileDrag = null;
  if (hit) {
    const keys = tileKeys.filter((k) => k !== dragged);
    let i = keys.indexOf(hit.t.dataset.key);
    if (i >= 0) { if (hit.right) i += 1; keys.splice(i, 0, dragged); saveOrder(keys); }
  }
  if (lastSum) renderTiles(lastSum);
}
function renderTiles(S) {
  const grid = $("ltiles");
  if (!grid || tileDrag) return;
  const k = S.k;
  const tiles = [
    { tile: "natverk", title: "Internet", icon: "internet", level: "ok", big: `${fmt(S.mbit, 0)}<small>Mbit/s</small>`, sub: `${fmt(k.now_down_bps / 1e6, 0)} ned · ${fmt(k.now_up_bps / 1e6, 0)} upp · ${S.countries} länder` },
  ];
  if (S.vpn.enabled) tiles.push({ tile: "sakerhet", key: "vpn", title: "VPN", icon: "vpn", level: S.vpn.ok ? "ok" : "err", big: S.vpn.ok ? "Ansluten" : "Nere", sub: (S.vpn.detail || "").split(";")[0] });
  tiles.push(
    { tile: "klienter", title: "Klienter", icon: "clients", level: "ok", big: `${k.clients_online}<small>online</small>`, sub: S.sections.klienter.text.replace(/^\d+ online · /, "") },
    { tile: "sakerhet", title: "Säkerhet", icon: "security", level: S.sections.sakerhet.level, big: `${fmt(S.t.suricata || 0, 0)}<small>larm</small>`, sub: `${fmt(S.t.pfblockerng || 0, 0)} blockeringar senaste dygnet` },
  );
  for (const u of S.unraids) {
    const cap = u.u.capacity || {};
    tiles.push({ tile: "servrar", ur: u.i, title: u.name, icon: "server", level: u.level,
      big: cap.pct != null ? `${fmt(cap.pct, 0)}<small>% fullt</small>` : "–",
      sub: u.u.status.ok ? (u.level === "ok" ? `${(u.u.containers || []).filter((c) => c.state === "running").length} av ${(u.u.containers || []).length} containrar körs` : u.u.status.detail) : (u.u.status.error || "väntar på svar") });
  }
  if (S.plex.servers.length) tiles.push({ tile: "plex", title: "Plex", icon: "plex", level: S.sections.plex.level, big: `${S.plex.totals.streams}<small>strömmar</small>`, sub: `${S.plex.totals.transcode} omkodning · ${S.plex.totals.remote} utanför hemmet` });
  if (S.ph) tiles.push({ tile: "pihole", title: "Pi-hole", icon: "pihole", level: S.sections.pihole.level, big: S.sections.pihole.level === "ok" ? `${fmt(S.ph.percent_blocked || 0, 0)}<small>% blockerat</small>` : S.sections.pihole.level === "warn" ? "Pausad" : "Nere", sub: S.sections.pihole.text });
  if (S.sections.unifi.level !== "off") tiles.push({ tile: "unifi", title: "UniFi", icon: "unifi", level: S.sections.unifi.level, big: `${S.sections.unifi.badge || 0}<small>enheter</small>`, sub: S.sections.unifi.text });
  for (let i = tiles.length - 1; i >= 0; i--) if (!shown(tiles[i].tile)) tiles.splice(i, 1);
  const rank = { err: 0, warn: 1, ok: 2, off: 3 };
  const order = store.get("tileOrder", null);
  if (Array.isArray(order) && order.length) {
    // egen ordning; rutor som inte fanns när ordningen sparades hamnar sist
    const pos = (t) => { const i = order.indexOf(tileKey(t)); return i < 0 ? 1000 + rank[t.level] : i; };
    tiles.sort((a, b) => pos(a) - pos(b));
  } else {
    tiles.sort((a, b) => rank[a.level] - rank[b.level]);
  }
  tileKeys = tiles.map(tileKey);
  const custom = Array.isArray(order) && order.length;
  const bar = $("ltbar");
  if (bar) bar.innerHTML = tileEdit
    ? `<span class="lthint">Dra rutorna dit du vill ha dem, eller använd pilarna. Ordningen sparas i den här webbläsaren.</span><button type="button" class="btn" data-act="reset" ${custom ? "" : "disabled"}>Återställ (problem först)</button><button type="button" class="btn primary" data-act="edit">Klar</button>`
    : `<span class="lthint">${custom ? "Egen ordning" : "Problem visas först"}</span><button type="button" class="btn" data-act="edit">↕ Ändra ordning</button>`;
  grid.classList.toggle("editing", tileEdit);
  grid.innerHTML = tiles.map((t, i) => `<button type="button" class="ltile ${t.level}${drawer === t.tile && (t.ur ?? null) === unraidFilter ? " open" : ""}" data-tile="${t.tile}" data-key="${esc(tileKey(t))}" ${t.ur != null ? `data-ur="${t.ur}"` : ""} ${tileEdit ? 'aria-roledescription="flyttbar ruta"' : ""}>
      <span class="lth">${icon(t.icon)}<b>${esc(t.title)}</b><span class="ldot ${t.level}"></span>${tileEdit ? `<span class="ltmv"><span role="button" tabindex="0" data-mv="-1" aria-label="Flytta ${esc(t.title)} bakåt" ${i === 0 ? 'aria-disabled="true"' : ""}>◀</span><span role="button" tabindex="0" data-mv="1" aria-label="Flytta ${esc(t.title)} framåt" ${i === tiles.length - 1 ? 'aria-disabled="true"' : ""}>▶</span></span>` : ""}</span>
      <span class="ltbig num">${t.big}</span><span class="ltsub">${esc(t.sub || "")}</span></button>`).join("");
}

/* ── Unraid: visa bara vald server ── */
function applyUnraidFilter() {
  if (unraidFilter != null && window.unraidSelect) window.unraidSelect(unraidFilter);
}

/* ── uppdatering vid varje ny data ── */
let lastSum = null;
function onRender(d) {
  const S = (lastSum = summary(d));
  const title = S.alerts.length ? `(${S.alerts.length}) ` : "";
  document.title = title + "pfSense Nätverkskarta";
  if (layout === "flikar") {
    for (const [id, x] of Object.entries(S.sections)) {
      const sum = document.querySelector(`[data-sum="${id}"]`);
      if (sum) sum.innerHTML = `<span class="ldot ${x.level}"></span>${esc(x.text)}`;
      const dot = document.querySelector(`[data-dot="${id}"]`);
      if (dot) dot.className = `ldot ${x.level === "ok" || x.level === "off" ? "" : x.level}`;
    }
    const pr = $("rad-pihole"); if (pr) pr.hidden = !S.ph;
    const tb = document.querySelector('.ltabs [data-sec="pihole"]'); if (tb) tb.hidden = !S.ph;
    const ib = $("rad-atgard");
    if (ib) {
      ib.hidden = !(d.alerts || []).length;
      ib.querySelector('[data-sum="atgard"]').textContent = `${S.alerts.length} aktiva larm${S.crit ? `, varav ${S.crit} kritiska` : ""}`;
    }
  } else if (layout === "sidomeny") {
    const side = document.querySelector(".lside");
    if (side) {
      const link = (id, label, ic, x, ur = null) => {
        const on = page === id && (ur ?? null) === unraidFilter;
        const lv = x.level === "ok" || x.level === "off" ? "" : x.level;
        return `<a href="#sida-${id}${ur != null ? "-" + ur : ""}" data-page="${id}" ${ur != null ? `data-ur="${ur}"` : ""} class="${on ? "on" : ""}" ${on ? 'aria-current="page"' : ""}>${ic}<span>${esc(label)}</span><span class="lbadge ${lv}">${esc(x.badge ?? "")}</span></a>`;
      };
      const sec = S.sections;
      side.innerHTML = `<div class="lbrand">Nätverket hemma</div>
        ${link("oversikt", "Översikt", icon("overview"), sec.oversikt)}
        <div class="lgrp">Nätverk</div>
        ${shown("natverk") ? link("natverk", "Karta och trafik", icon("network"), sec.natverk) : ""}
        ${shown("klienter") ? link("klienter", "Klienter", icon("clients"), sec.klienter) : ""}
        ${sec.unifi.level !== "off" && shown("unifi") ? link("unifi", "UniFi", icon("unifi"), sec.unifi) : ""}
        ${shown("sakerhet") ? link("sakerhet", "Säkerhet och VPN", icon("security"), sec.sakerhet) : ""}
        ${S.ph && shown("pihole") ? link("pihole", "Pi-hole", icon("pihole"), sec.pihole) : ""}
        ${(S.unraids.length && shown("servrar")) || (S.plex.servers.length && shown("plex")) ? '<div class="lgrp">Servrar</div>' : ""}
        ${shown("servrar") ? S.unraids.map((u) => link("servrar", u.name, `<span class="ldot ${u.level}"></span>`, { level: u.level, badge: u.level === "ok" ? "" : u.level === "err" ? "!" : "1" }, u.i)).join("") : ""}
        ${S.plex.servers.length && shown("plex") ? link("plex", "Plex", icon("plex"), sec.plex) : ""}`;
    }
  } else if (layout === "rutor") {
    renderTiles(S);
  }
}

/* ── klick på statusmarkeringar (Unraid, Plex) ── */
function goto(id) {
  if (layout === "klassisk" || layout === "overblick") return false;
  const m = id.match(/^unraid-card-(\d+)$/);
  const target = m ? "servrar" : GOTO[id];
  if (!target) return false;
  if (layout === "flikar") return gotoRow(target);
  if (layout === "sidomeny") { setPage(target, m ? +m[1] : null); return true; }
  if (layout === "rutor") return openDrawer(target, m ? +m[1] : null);
  return false;
}

/* ── välj utseende ── */
function apply(name) {
  if (!LAYOUTS[name]) name = "klassisk";
  if (layout === "rutor") { closeDrawer(); document.removeEventListener("keydown", escClose); if (built.drawer) built.drawer.remove(); built.drawer = null; }
  layout = name;
  document.body.dataset.layout = name;
  ({ klassisk: buildKlassisk, sidomeny: buildSidomeny, rutor: buildRutor, flikar: buildFlikar, overblick: buildOverblick })[name]();
  if (window.dashState && window.dashState.data) { window.dashRender(); }
  const pick = $("s-layout");
  if (pick) pick.querySelectorAll("input").forEach((i) => { i.checked = i.value === name; });
}

grab();
window.onDashRender = onRender;
window.layoutGoto = goto;
const fromUrl = new URLSearchParams(location.search).get("layout");
apply(LAYOUTS[fromUrl] ? fromUrl : store.get("layout", "klassisk"));

const pick = $("s-layout");
if (pick) {
  pick.innerHTML = Object.entries(LAYOUTS).map(([v, l]) => `<label class="lchoice"><input type="radio" name="layout" value="${v}" ${v === layout ? "checked" : ""}><span><b>${l}</b><small>${{
    klassisk: "Allt på en sida, som tidigare.",
    sidomeny: "Meny till vänster, en sak i taget.",
    rutor: "En ruta per system, detaljer i en panel.",
    flikar: "En sida med flikar och delar som kan fällas ihop.",
    overblick: "Det viktigaste först: är något fel, och var sitter flaskhalsen.",
  }[v]}</small></span></label>`).join("");
  pick.addEventListener("change", (e) => {
    if (e.target.name !== "layout") return;
    store.set("layout", e.target.value);
    apply(e.target.value);
  });
}

/* ── profiler: namn, utseende, rutornas ordning, val på sidan och dolda delar ── */
const PKEYS = ["layout", "tileOrder", "period", "chartView2", "topList", "clientsAll", "layoutCollapsed", "urFilters",
  "urTab", "plexTab", "phDomTab", "phClTab", "phQn", "phQf", "hiddenParts"];
let profiles = [];
const ls = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) { /* ingen lagring */ } },
};
const activeId = () => ls.get("activeProfile");
function snapshot() {
  const prefs = {};
  for (const k of PKEYS) { const v = ls.get(k); if (v != null) prefs[k] = v; }
  prefs.layout = JSON.stringify(layout);
  return { layout, prefs, updated: Math.floor(Date.now() / 1000) };
}
const slug = (name) => (name.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "profil").slice(0, 30);
async function saveProfiles(list) {
  const r = await fetch("api/profiles", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ profiles: list }) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || r.status);
  profiles = j.profiles;
  renderPicker(); renderProfileSettings();
}
async function loadProfiles() {
  try { const r = await fetch("api/profiles", { cache: "no-store" }); profiles = (await r.json()).profiles || []; } catch (e) { profiles = []; }
  renderPicker(); renderProfileSettings();
}
function useProfile(id) {
  const p = profiles.find((x) => x.id === id); if (!p) return;
  for (const k of PKEYS) ls.set(k, p.prefs[k] ?? null);
  ls.set("layout", JSON.stringify(p.layout));
  ls.set("activeProfile", p.id);
  const u = new URL(location.href); u.searchParams.delete("layout"); u.hash = "";
  location.replace(u.toString());
}
async function addProfile(name) {
  name = (name || "").trim();
  if (!name) return "Skriv ett namn på profilen.";
  if (profiles.some((p) => p.name.toLowerCase() === name.toLowerCase())) return `Det finns redan en profil som heter ${name}.`;
  let id = slug(name), n = 2;
  while (profiles.some((p) => p.id === id)) id = `${slug(name)}-${n++}`;
  await saveProfiles([...profiles, { id, name, ...snapshot() }]);
  ls.set("activeProfile", id);
  renderPicker(); renderProfileSettings();
  return null;
}
async function updateProfile(id) { await saveProfiles(profiles.map((p) => (p.id === id ? { ...p, ...snapshot() } : p))); }
async function renameProfile(id, name) {
  name = (name || "").trim(); if (!name) return;
  await saveProfiles(profiles.map((p) => (p.id === id ? { ...p, name } : p)));
}
async function deleteProfile(id) {
  await saveProfiles(profiles.filter((p) => p.id !== id));
  if (activeId() === id) ls.set("activeProfile", null);
  renderPicker();
}
function renderPicker() {
  const box = $("profpick"); if (!box) return;
  const act = profiles.find((p) => p.id === activeId());
  const open = box.querySelector(".profmenu") && !box.querySelector(".profmenu").hidden;
  box.innerHTML = `<button type="button" class="btn profbtn" aria-haspopup="true" aria-expanded="${open}" title="Byt profil">Profil: <b>${esc(act ? act.name : "ingen")}</b> ▾</button>
    <div class="profmenu" role="menu" ${open ? "" : "hidden"}>
      ${profiles.length ? profiles.map((p) => `<button type="button" role="menuitemradio" aria-checked="${act && act.id === p.id}" data-puse="${esc(p.id)}"><span>${act && act.id === p.id ? "●" : ""}</span>${esc(p.name)}<small>${esc(LAYOUTS[p.layout] || "")}</small></button>`).join("") : '<p class="muted">Inga profiler än.</p>'}
      <hr>
      ${act ? `<button type="button" data-pupd="${esc(act.id)}"><span>↻</span>Spara ändringar i ${esc(act.name)}</button>` : ""}
      <form class="profnew"><input type="text" maxlength="40" placeholder="Namn på ny profil" aria-label="Namn på ny profil"><button type="submit" class="btn">Spara</button></form>
      <span class="proferr" hidden></span>
      <button type="button" data-pmanage><span>⚙</span>Hantera profiler …</button>
    </div>`;
  placeMenu();
}
// öppna menyn åt det håll där den får plats (vänster- eller högerkant)
function placeMenu() {
  const box = $("profpick"), m = box && box.querySelector(".profmenu");
  if (!m || m.hidden) return;
  const r = box.getBoundingClientRect(), w = Math.min(280, window.innerWidth - 32);
  m.classList.toggle("left", r.right - w < 16);
}
window.addEventListener("resize", placeMenu);
function renderProfileSettings() {
  const list = $("s-profiles"); if (!list) return;
  const act = activeId();
  list.innerHTML = profiles.length ? profiles.map((p) => `<div class="profrow" data-pid="${esc(p.id)}">
      <input type="text" maxlength="40" value="${esc(p.name)}" aria-label="Namn på profilen" data-pname>
      <span class="muted">${esc(LAYOUTS[p.layout] || p.layout)}${act === p.id ? " · används nu" : ""}</span>
      <button type="button" class="btn" data-puse="${esc(p.id)}">Använd</button>
      <button type="button" class="btn" data-pupd="${esc(p.id)}" title="Ersätt profilen med hur sidan ser ut nu">Spara nuvarande</button>
      <button type="button" class="btn" data-pdel="${esc(p.id)}">Ta bort</button></div>`).join("") : '<p class="muted">Inga profiler än. Ställ in sidan som du vill ha den och spara den som en profil.</p>';
  const parts = $("s-parts");
  if (parts) parts.innerHTML = Object.entries(PART_LABEL).map(([k, l]) => `<label class="check"><input type="checkbox" data-part="${k}" ${hidden.has(k) ? "" : "checked"}> ${l}</label>`).join("");
}
async function profileAction(e) {
  const t = e.target;
  const use = t.closest("[data-puse]"); if (use) { useProfile(use.dataset.puse); return; }
  const upd = t.closest("[data-pupd]");
  if (upd) { try { await updateProfile(upd.dataset.pupd); flash(upd, "Sparat"); } catch (err) { alert(err.message); } return; }
  const del = t.closest("[data-pdel]");
  if (del) { const p = profiles.find((x) => x.id === del.dataset.pdel); if (p && confirm(`Ta bort profilen ${p.name}?`)) await deleteProfile(p.id); return; }
  if (t.closest("[data-pmanage]")) { $("profpick").querySelector(".profmenu").hidden = true; $("open-settings").click(); return; }
}
function flash(btn, text) { const old = btn.innerHTML; btn.innerHTML = `<span>✓</span>${text}`; setTimeout(() => { btn.innerHTML = old; }, 1200); }
const profBox = $("profpick");
if (profBox) {
  profBox.addEventListener("click", (e) => {
    if (e.target.closest(".profbtn")) { const m = profBox.querySelector(".profmenu"); m.hidden = !m.hidden; placeMenu(); profBox.querySelector(".profbtn").setAttribute("aria-expanded", !m.hidden); if (!m.hidden) { const f = m.querySelector("button,input"); if (f) f.focus(); } return; }
    profileAction(e);
  });
  profBox.addEventListener("submit", async (e) => {
    e.preventDefault();
    const inp = e.target.querySelector("input"), err = profBox.querySelector(".proferr");
    try { const msg = await addProfile(inp.value); if (msg) { err.textContent = msg; err.hidden = false; } }
    catch (x) { err.textContent = x.message; err.hidden = false; }
  });
  document.addEventListener("click", (e) => { const m = profBox.querySelector(".profmenu"); if (m && !m.hidden && !profBox.contains(e.target)) { m.hidden = true; profBox.querySelector(".profbtn").setAttribute("aria-expanded", "false"); } });
  document.addEventListener("keydown", (e) => { const m = profBox.querySelector(".profmenu"); if (e.key === "Escape" && m && !m.hidden) { m.hidden = true; profBox.querySelector(".profbtn").focus(); } });
}
const sp = $("s-profiles");
if (sp) {
  sp.addEventListener("click", profileAction);
  sp.addEventListener("change", (e) => { const n = e.target.closest("[data-pname]"); if (n) renameProfile(n.closest(".profrow").dataset.pid, n.value).catch((x) => alert(x.message)); });
}
const sadd = $("s-prof-add");
if (sadd) sadd.addEventListener("click", async () => {
  const inp = $("s-prof-new");
  try { const msg = await addProfile(inp.value); if (msg) alert(msg); else inp.value = ""; } catch (x) { alert(x.message); }
});
const sparts = $("s-parts");
if (sparts) sparts.addEventListener("change", (e) => {
  const c = e.target.closest("[data-part]"); if (!c) return;
  if (c.checked) hidden.delete(c.dataset.part); else hidden.add(c.dataset.part);
  store.set("hiddenParts", [...hidden]);
  apply(layout);
});
const og = $("open-settings");
if (og) og.addEventListener("click", () => renderProfileSettings());
loadProfiles();
})();

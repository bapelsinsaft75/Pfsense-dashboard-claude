/* Egna vyer inne i appen (sidhuvud och meny ligger kvar):
 *   /plex, /plex/<bibliotek>   – Plex-bibliotek med informationsruta och "Synka nu"
 *   /requests                  – önskningar steg för steg
 *   /senaste/filmer|serier     – senast tillagt i Plex
 * Appen visar en tom behållare (#fd-local-root) på dessa adresser; här fylls den.
 */
(function () {
  "use strict";

  /* ---------- hjälpfunktioner ---------- */
  function token() { try { return localStorage.getItem("access_token"); } catch (e) { return null; } }

  async function refresh() {
    var rt = null;
    try { rt = localStorage.getItem("refresh_token"); } catch (e) {}
    if (!rt) return false;
    var r = await fetch("/api/v1/auth/refresh", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: rt }) });
    if (!r.ok) return false;
    var d = await r.json();
    localStorage.setItem("access_token", d.access_token);
    localStorage.setItem("refresh_token", d.refresh_token);
    return true;
  }

  async function api(path, opts, retried) {
    opts = opts || {};
    var h = { "Authorization": "Bearer " + (token() || "") };
    var r = await fetch(path, Object.assign({}, opts, { headers: h }));
    if (r.status === 401 && !retried && await refresh()) return api(path, opts, true);
    if (r.status === 401) { location.replace("/plex-login?next=" + encodeURIComponent(location.pathname)); throw new Error("401"); }
    var d = await r.json().catch(function () { return null; });
    if (!r.ok) throw new Error((d && d.detail) || "Fel " + r.status);
    return d;
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function fmtTime(ts) {
    if (!ts) return "aldrig";
    return new Date(ts * 1000).toLocaleString("sv-SE", { dateStyle: "short", timeStyle: "short" });
  }
  function fmtDay(ts) {
    if (!ts) return "";
    return new Date(ts * 1000).toLocaleDateString("sv-SE", { day: "numeric", month: "short" });
  }
  function fmtDate(s) {
    if (!s) return "";
    var d = new Date(/Z|[+-]\d\d:?\d\d$/.test(s) ? s : String(s).replace(" ", "T") + "Z");
    return isNaN(d) ? s : d.toLocaleDateString("sv-SE", { year: "numeric", month: "short", day: "numeric" });
  }

  /* Navigera inom appen utan att ladda om (menyn ligger kvar) */
  function go(href) {
    history.pushState({}, "", href);
    window.dispatchEvent(new PopStateEvent("popstate", { state: {} }));
  }

  /* ---------- informationsruta (Plex-titel) ---------- */
  function chip(label, value, url, cls) {
    var n = el(url ? "a" : "span", "d-chip " + (cls || ""));
    if (url) { n.href = url; n.target = "_blank"; n.rel = "noopener"; }
    n.appendChild(el("b", null, label));
    n.appendChild(el("span", null, value));
    return n;
  }

  function ensureDialog(root) {
    var dlg = root.querySelector("dialog.detail");
    if (dlg) return dlg;
    dlg = el("dialog", "detail");
    dlg.setAttribute("aria-labelledby", "d-title");
    dlg.appendChild(el("div", "d-hero"));
    var close = el("button", "d-close", "✕");
    close.type = "button"; close.setAttribute("aria-label", "Stäng");
    close.addEventListener("click", function () { dlg.close(); });
    dlg.appendChild(close);
    dlg.appendChild(el("div", "d-body"));
    dlg.addEventListener("click", function (e) { if (e.target === dlg) dlg.close(); });
    root.appendChild(dlg);
    return dlg;
  }

  async function openDetail(root, ratingKey, title) {
    var dlg = ensureDialog(root);
    var body = dlg.querySelector(".d-body");
    var hero = dlg.querySelector(".d-hero");
    hero.style.backgroundImage = "";
    body.innerHTML = "";
    body.appendChild(el("p", "msg", "Laddar " + (title || "") + " …"));
    if (!dlg.open) dlg.showModal();
    var d;
    try { d = await api("/api/local/plex/items/" + encodeURIComponent(ratingKey)); }
    catch (e) { body.innerHTML = ""; body.appendChild(el("p", "msg error", e.message)); return; }
    body.innerHTML = "";
    if (d.backdrop_url) hero.style.backgroundImage = "url('" + d.backdrop_url.replace(/'/g, "%27") + "')";

    var top = el("div", "d-top");
    var poster = el("div", "d-poster");
    if (d.poster_url) { var img = el("img"); img.src = d.poster_url; img.alt = ""; poster.appendChild(img); }
    top.appendChild(poster);
    var head = el("div", "d-head");
    var h = el("h2", null, d.title); h.id = "d-title"; head.appendChild(h);
    if (d.original_title && d.original_title !== d.title) head.appendChild(el("p", "d-orig", d.original_title));
    var meta = [d.year, d.duration, d.content_rating,
      d.type === "show" && d.seasons.length ? d.seasons.length + (d.seasons.length === 1 ? " säsong" : " säsonger") : null,
      d.type === "show" && d.episodes ? d.episodes + " avsnitt" : null].filter(Boolean).join(" · ");
    head.appendChild(el("p", "d-meta", meta));

    var chips = el("div", "d-chips");
    var r = d.ratings || {};
    if (r.tmdb && r.tmdb.rating) chips.appendChild(chip("TMDB", "★ " + r.tmdb.rating, r.tmdb.url, "tmdb"));
    if (r.imdb && r.imdb.rating) chips.appendChild(chip("IMDb", "★ " + r.imdb.rating, r.imdb.url, "imdb"));
    else if (r.imdb && r.imdb.url) chips.appendChild(chip("IMDb", "Visa", r.imdb.url, "imdb"));
    if (r.rotten_tomatoes && r.rotten_tomatoes.critic != null) chips.appendChild(chip("RT", r.rotten_tomatoes.critic + "%", null, "rt"));
    if (d.watched) chips.appendChild(el("span", "d-chip seen", "✓ Sedd"));
    else if (d.in_progress && d.progress_pct) chips.appendChild(el("span", "d-chip prog", "Påbörjad " + d.progress_pct + "%"));
    else if (d.viewed_episodes) chips.appendChild(el("span", "d-chip prog", d.viewed_episodes + "/" + d.episodes + " avsnitt sedda"));
    head.appendChild(chips);

    var actions = el("div", "d-actions");
    if (d.plex_url) {
      var play = el("a", "btn primary d-btn", "▶ Watch on Plex");
      play.href = d.plex_url; play.target = "_blank"; play.rel = "noopener"; actions.appendChild(play);
    }
    if (d.film_url) {
      var fl = el("a", "btn d-btn", d.type === "show" ? "Öppna seriesida" : "Öppna filmsida");
      fl.href = d.film_url;
      fl.addEventListener("click", function (e) { e.preventDefault(); dlg.close(); go(d.film_url); });
      actions.appendChild(fl);
    }
    head.appendChild(actions);
    top.appendChild(head);
    body.appendChild(top);

    if (d.tagline) body.appendChild(el("p", "d-tagline", d.tagline));
    if (d.summary) body.appendChild(el("p", "d-summary", d.summary));
    var facts = el("dl", "d-facts");
    [["Genre", (d.genres || []).join(", ")], ["Regi", (d.directors || []).join(", ")], ["Manus", (d.writers || []).join(", ")],
     ["Studio", d.studio], ["Premiär", d.released], ["Fil", d.video]].forEach(function (f) {
      if (!f[1]) return;
      facts.appendChild(el("dt", null, f[0])); facts.appendChild(el("dd", null, f[1]));
    });
    if (facts.children.length) body.appendChild(facts);

    if (d.seasons && d.seasons.length) {
      body.appendChild(el("h3", null, "Säsonger"));
      var ul = el("ul", "d-seasons");
      d.seasons.forEach(function (sn) {
        var li = el("li");
        li.appendChild(el("span", "d-sname", sn.title));
        var bar = el("span", "d-bar"); var fill = el("span");
        fill.style.width = (sn.episodes ? Math.round(100 * sn.viewed / sn.episodes) : 0) + "%";
        bar.appendChild(fill); li.appendChild(bar);
        li.appendChild(el("span", "small", sn.viewed + "/" + (sn.episodes || "?")));
        ul.appendChild(li);
      });
      body.appendChild(ul);
    }
    if (d.cast && d.cast.length) {
      body.appendChild(el("h3", null, "Skådespelare"));
      var cast = el("ul", "d-cast");
      d.cast.forEach(function (c) {
        var li = el("li");
        var ph = el("span", "d-face");
        if (c.photo) { var im = el("img"); im.src = c.photo; im.alt = ""; im.loading = "lazy"; ph.appendChild(im); }
        else ph.textContent = (c.name || "?").charAt(0);
        li.appendChild(ph);
        li.appendChild(el("span", "d-cname", c.name));
        if (c.role) li.appendChild(el("span", "small", c.role));
        cast.appendChild(li);
      });
      body.appendChild(cast);
    }
  }

  /* ---------- kort (affisch + titel) ---------- */
  function card(it, onClick, extra) {
    var li = el("li", "pcard");
    var a = el("a");
    a.href = "#" + it.rating_key;
    a.addEventListener("click", function (e) { e.preventDefault(); onClick(it); });
    var pw = el("div", "pposter");
    if (it.poster_url) {
      var img = el("img"); img.src = it.poster_url; img.alt = ""; img.loading = "lazy"; img.decoding = "async";
      pw.appendChild(img);
    } else {
      pw.appendChild(el("span", "pfallback", it.title));
    }
    if (it.watched) pw.appendChild(el("span", "pseen", "✓ Sedd"));
    else if (it.viewed_episodes) pw.appendChild(el("span", "pseen pprog", it.viewed_episodes + "/" + (it.episodes || "?") + " avsnitt"));
    if (it.rating) pw.appendChild(el("span", "prating", "★ " + it.rating));
    if (extra && extra.badge) pw.appendChild(el("span", "pnew", extra.badge));
    a.appendChild(pw);
    a.appendChild(el("span", "ptitle", it.title));
    (extra && extra.lines || []).forEach(function (ln) { if (ln) a.appendChild(el("span", ln[0], ln[1])); });
    li.appendChild(a);
    return li;
  }

  /* ---------- Plex-bibliotek ---------- */
  async function plexView(root, key) {
    root.classList.remove("fd-narrow");
    var head = el("div", "req-head");
    var h1 = el("h1", null, "Plex");
    head.appendChild(h1);
    var syncbox = el("div", "syncbox"); syncbox.hidden = true;
    var syncinfo = el("span", "small"); syncinfo.setAttribute("role", "status"); syncinfo.setAttribute("aria-live", "polite");
    var syncbtn = el("button", "btn small-btn", "Synka nu"); syncbtn.type = "button";
    syncbox.appendChild(syncinfo); syncbox.appendChild(syncbtn);
    head.appendChild(syncbox);
    var tabs = el("nav", "libtabs"); tabs.setAttribute("aria-label", "Plex-bibliotek");
    var lead = el("p", "lead");
    var toolbar = el("div", "toolbar"); toolbar.hidden = true;
    var q = el("input", "input"); q.type = "search"; q.placeholder = "Sök i biblioteket"; q.setAttribute("aria-label", "Sök i biblioteket");
    var sort = el("select", "input"); sort.setAttribute("aria-label", "Sortering");
    [["title", "Titel A–Ö"], ["added", "Senast tillagda"], ["year", "Nyast först"], ["rating", "Högst betyg"],
     ["unwatched", "Osedda först"]].forEach(function (o) { var op = el("option", null, o[1]); op.value = o[0]; sort.appendChild(op); });
    toolbar.appendChild(q); toolbar.appendChild(sort);
    var libs = el("div", "libcards");
    var grid = el("ul", "pgrid");
    var msg = el("p", "msg"); msg.setAttribute("role", "status");
    [head, tabs, lead, toolbar, libs, grid, msg].forEach(function (n) { root.appendChild(n); });

    function kindWord(t) { return t === "show" ? "serier" : "filmer"; }

    function renderTabs(sections) {
      tabs.innerHTML = "";
      var all = el("a", "tab" + (key ? "" : " active"), "Alla bibliotek");
      all.href = "/plex";
      tabs.appendChild(all);
      sections.forEach(function (s) {
        var a = el("a", "tab" + (s.key === key ? " active" : ""), s.title);
        a.href = "/plex/" + encodeURIComponent(s.key);
        if (s.key === key) a.setAttribute("aria-current", "page");
        tabs.appendChild(a);
      });
    }
    function renderLibs(sections) {
      libs.innerHTML = "";
      lead.textContent = sections.length ? "Dina bibliotek i Plex."
        : "Inga bibliotek hittades. Biblioteken synkas en stund efter inloggningen – ladda om sidan om en minut.";
      sections.forEach(function (s) {
        var a = el("a", "libcard");
        a.href = "/plex/" + encodeURIComponent(s.key);
        a.appendChild(el("span", "libname", s.title));
        a.appendChild(el("span", "small", (s.count || 0) + " " + kindWord(s.type)));
        libs.appendChild(a);
      });
    }

    var timer = null;
    async function showSync(afterDone) {
      if (!root.isConnected) return;
      var st = await api("/api/local/plex/sync-status");
      syncbox.hidden = false;
      if (st.running) {
        syncinfo.textContent = "Synkar biblioteken …";
        syncbtn.disabled = true;
        clearTimeout(timer);
        timer = setTimeout(function () { showSync(true); }, 3000);
        return;
      }
      syncbtn.disabled = false;
      syncinfo.textContent = (st.error ? "Senaste synken misslyckades. " : "") + "Senast synkad " + fmtTime(st.last_sync) +
        " · synkas var " + st.interval_hours + ":e timme";
      syncbtn.title = st.can_sync_library ? "Hämta biblioteken från Plex nu" : "Uppdatera din sett-historik från Plex";
      if (afterDone) {
        var secs = await api("/api/local/plex/sections");
        renderTabs(secs); renderLibs(secs);
      }
    }
    syncbtn.addEventListener("click", async function () {
      syncbtn.disabled = true;
      syncinfo.textContent = "Startar synk …";
      try { await api("/api/local/plex/sync", { method: "POST" }); }
      catch (e) { syncbtn.disabled = false; syncinfo.textContent = "Kunde inte starta synken."; return; }
      setTimeout(function () { showSync(true); }, 1000);
    });

    var sections = await api("/api/local/plex/sections");
    renderTabs(sections);
    if (!key) {
      document.title = "Plex | Filmförslag";
      renderLibs(sections);
      showSync(false).catch(function () {});
      return;
    }
    msg.textContent = "Laddar …";
    var data = await api("/api/local/plex/sections/" + encodeURIComponent(key) + "/items");
    document.title = data.title + " | Plex | Filmförslag";
    h1.textContent = data.title;
    toolbar.hidden = false;
    try { var saved = sessionStorage.getItem("fd-plex-sort"); if (saved) sort.value = saved; } catch (e) {}

    function renderGrid() {
      var term = q.value.trim().toLowerCase();
      var items = data.items.filter(function (i) { return !term || i.title.toLowerCase().indexOf(term) !== -1; });
      var sv = sort.value;
      items.sort(function (a, b) {
        if (sv === "added") return (b.added_at || 0) - (a.added_at || 0);
        if (sv === "year") return (b.year || 0) - (a.year || 0);
        if (sv === "rating") return (b.rating || 0) - (a.rating || 0);
        if (sv === "unwatched") return (a.watched - b.watched) || a.title.localeCompare(b.title, "sv");
        return a.title.localeCompare(b.title, "sv");
      });
      grid.innerHTML = "";
      var frag = document.createDocumentFragment();
      items.forEach(function (it) {
        var meta = [it.year, it.type === "show" && it.seasons ? it.seasons + (it.seasons === 1 ? " säsong" : " säsonger") : null]
          .filter(Boolean).join(" · ");
        frag.appendChild(card(it, function (x) { openDetail(root, x.rating_key, x.title); }, { lines: [["small", meta]] }));
      });
      grid.appendChild(frag);
      msg.textContent = items.length ? "" : "Inga träffar.";
      lead.textContent = data.items.length + " " + kindWord(data.type) + (term ? " · " + items.length + " träffar" : "");
    }
    q.addEventListener("input", renderGrid);
    sort.addEventListener("change", function () { try { sessionStorage.setItem("fd-plex-sort", sort.value); } catch (e) {} renderGrid(); });
    renderGrid();
    var rk = location.hash.slice(1);
    var hit = rk && data.items.filter(function (i) { return i.rating_key === rk; })[0];
    if (hit) openDetail(root, hit.rating_key, hit.title);
  }

  /* ---------- Senaste (från Plex) ---------- */
  async function latestView(root, kindParam) {
    root.classList.remove("fd-narrow");
    var episodes = /^serier|^avsnitt|^shows/.test(kindParam || "");
    var tabs = el("nav", "libtabs"); tabs.setAttribute("aria-label", "Senaste");
    [["/senaste/filmer", "Senaste filmer", !episodes], ["/senaste/serier", "Senaste serier", episodes]].forEach(function (t) {
      var a = el("a", "tab" + (t[2] ? " active" : ""), t[1]); a.href = t[0];
      if (t[2]) a.setAttribute("aria-current", "page");
      tabs.appendChild(a);
    });
    var h1 = el("h1", null, episodes ? "Senaste serier" : "Senaste filmer");
    var lead = el("p", "lead", "Hämtar från Plex …");
    var grid = el("ul", "pgrid");
    var msg = el("p", "msg"); msg.setAttribute("role", "status");
    [tabs, h1, lead, grid, msg].forEach(function (n) { root.appendChild(n); });
    document.title = (episodes ? "Senaste serier" : "Senaste filmer") + " | Filmförslag";

    var d = await api("/api/local/plex/latest?kind=" + (episodes ? "episodes" : "movies"));
    lead.textContent = episodes
      ? "De " + d.items.length + " senast tillagda avsnitten i din Plex."
      : "De " + d.items.length + " senast tillagda filmerna från " + d.year + " i din Plex.";
    if (!d.items.length) msg.textContent = episodes ? "Inga avsnitt hittades." : "Inga filmer från " + d.year + " hittades i Plex.";
    var frag = document.createDocumentFragment();
    d.items.forEach(function (it) {
      var lines = episodes
        ? [["ep-code", it.episode_code], ["ep-title", it.episode_title], ["small", "Tillagd " + fmtDay(it.added_at)]]
        : [["small", [it.year, "tillagd " + fmtDay(it.added_at)].filter(Boolean).join(" · ")]];
      frag.appendChild(card(it, function (x) {
        openDetail(root, episodes ? (x.show_rating_key || x.rating_key) : x.rating_key, x.title);
      }, { lines: lines }));
    });
    grid.appendChild(frag);
  }

  /* ---------- Requests ---------- */
  var ICON = { done: "✓", current: "●", todo: "○", failed: "✕" };

  async function requestsView(root) {
    root.classList.add("fd-narrow");
    document.title = "Requests | Filmförslag";
    var head = el("div", "req-head");
    head.appendChild(el("h1", null, "Requests"));
    var reload = el("button", "btn small-btn", "Uppdatera"); reload.type = "button";
    head.appendChild(reload);
    var lead = el("p", "lead", "Dina önskningar och hur långt de har kommit: Plex-watchlist, Seerr, nedladdning och Plex.");
    var scopeNav = el("nav", "libtabs"); scopeNav.hidden = true; scopeNav.setAttribute("aria-label", "Visa");
    var note = el("p", "msg"); note.setAttribute("role", "status");
    var list = el("ul", "rq");
    var empty = el("p", "msg");
    empty.innerHTML = "Du har inga önskningar än. Öppna en film eller serie som inte finns i Plex och tryck på <b>Request movie</b> eller <b>Request show</b>.";
    empty.hidden = true;
    [head, lead, scopeNav, note, list, empty].forEach(function (n) { root.appendChild(n); });

    var scope = new URLSearchParams(location.search).get("scope");

    function render(data) {
      list.innerHTML = "";
      note.textContent = data.note || "";
      empty.hidden = data.items.length > 0;
      data.items.forEach(function (it) {
        var li = el("li", "rq-item");
        var img = el("img", "rq-poster");
        img.src = it.poster_url || "/favicon.svg?v=3"; img.alt = ""; img.loading = "lazy";
        li.appendChild(img);
        var body = el("div", "rq-body");
        var top = el("div", "rq-top");
        var title = el(it.url ? "a" : "span", "rq-title", it.title + (it.year ? " (" + it.year + ")" : ""));
        if (it.url) title.href = it.url;
        top.appendChild(title);
        top.appendChild(el("span", "rq-kind", it.media_type === "tv" ? "Serie" : "Film"));
        body.appendChild(top);
        body.appendChild(el("div", "small", "Önskad " + fmtDate(it.created_at) +
          (data.scope === "all" && it.requested_by ? " av " + it.requested_by : "")));
        var steps = el("ol", "rq-steps");
        it.stages.forEach(function (s) {
          var st = el("li", "rq-step " + s.state);
          st.appendChild(el("span", "rq-dot", ICON[s.state] || "○"));
          st.appendChild(el("span", "rq-label", s.label));
          steps.appendChild(st);
        });
        body.appendChild(steps);
        if (it.progress != null && it.progress < 100) {
          var bar = el("div", "rq-bar"); var fill = el("span");
          fill.style.width = it.progress + "%"; bar.appendChild(fill); body.appendChild(bar);
        }
        var foot = el("div", "rq-foot");
        foot.appendChild(el("span", "rq-summary", it.summary));
        if (it.seerr_url) { var a = el("a", "rq-link", "Öppna i Seerr"); a.href = it.seerr_url; a.target = "_blank"; a.rel = "noopener"; foot.appendChild(a); }
        if (it.source === "local" && (it.mine || data.is_admin)) {
          var del = el("button", "btn small-btn", "Ta bort"); del.type = "button";
          del.addEventListener("click", async function () { await api("/api/local/requests/" + it.local_id, { method: "DELETE" }); load(); });
          foot.appendChild(del);
        }
        body.appendChild(foot);
        li.appendChild(body);
        list.appendChild(li);
      });
    }

    async function load() {
      note.textContent = "Hämtar status …";
      var data = await api("/api/local/requests/view?scope=" + encodeURIComponent(scope || "all"));
      if (data.is_admin) {
        scopeNav.hidden = false;
        scopeNav.innerHTML = "";
        [["all", "Alla"], ["mine", "Mina"]].forEach(function (o) {
          var t = el("a", "tab" + (o[0] === data.scope ? " active" : ""), o[1]);
          t.href = "/requests?scope=" + o[0];
          if (o[0] === data.scope) t.setAttribute("aria-current", "page");
          scopeNav.appendChild(t);
        });
        lead.textContent = data.scope === "all"
          ? "Alla användares önskningar och hur långt de har kommit: Plex-watchlist, Seerr, nedladdning och Plex."
          : "Dina önskningar och hur långt de har kommit: Plex-watchlist, Seerr, nedladdning och Plex.";
      }
      render(data);
    }
    reload.addEventListener("click", function () { load().catch(function () {}); });
    await load();
  }

  /* ---------- router ---------- */
  var rendered = null;

  var VIEW_PATH = /^\/(plex(\/[^/]+)?|requests|senaste\/[^/]+)\/?$/;

  /* Appen visar sin "sidan finns inte"-sida (med sidhuvud) på våra adresser.
     Vi gömmer dess innehåll och lägger vyn i samma <main>. */
  function ensureRoot() {
    var root = document.getElementById("fd-local-root");
    if (!VIEW_PATH.test(location.pathname)) {
      if (root) root.remove();
      return null;
    }
    var mains = Array.prototype.filter.call(document.querySelectorAll("main"), function (m) { return !m.closest("dialog"); });
    var main = mains[0];
    if (!main) return null;
    if (root && root.parentElement === main) return root;
    if (root) root.remove();
    Array.prototype.forEach.call(main.children, function (c) { c.style.display = "none"; });
    root = el("div", "fd-local-root");
    root.id = "fd-local-root";
    main.appendChild(root);
    return root;
  }

  function route() {
    var root = ensureRoot();
    if (!root) { rendered = null; return; }
    // Håll appens "sidan finns inte"-innehåll dolt även om den ritar om
    Array.prototype.forEach.call(root.parentElement.children, function (c) {
      if (c !== root && c.style.display !== "none") c.style.display = "none";
    });
    var key = location.pathname + location.search;
    if (root.getAttribute("data-rendered") === key) return;
    root.setAttribute("data-rendered", key);
    root.innerHTML = "";
    if (!token()) { location.replace("/plex-login?next=" + encodeURIComponent(location.pathname + location.search)); return; }
    var p = location.pathname.replace(/\/+$/, "");
    var m;
    var job;
    if (p === "/plex") job = plexView(root, null);
    else if ((m = /^\/plex\/([^/]+)$/.exec(p))) job = plexView(root, decodeURIComponent(m[1]));
    else if (p === "/requests") job = requestsView(root);
    else if ((m = /^\/senaste\/([^/]+)$/.exec(p))) job = latestView(root, m[1]);
    else return;
    Promise.resolve(job).catch(function (e) {
      if (e && e.message === "401") return;
      root.appendChild(el("p", "msg error", (e && e.message) || "Något gick fel."));
    });
  }

  // Länkar inne i vyerna: navigera i appen så att menyn ligger kvar
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest(".fd-local-root a[href^='/']");
    if (!a || a.target || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    go(a.getAttribute("href"));
  });

  window.addEventListener("popstate", function () { setTimeout(route, 0); });
  new MutationObserver(function () { route(); }).observe(document.documentElement, { childList: true, subtree: true });
  setInterval(route, 500);
  window.fdLocalGo = go;
})();

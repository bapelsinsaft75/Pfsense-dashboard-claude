/* Lokala tillägg till Film Dilemma-kopian:
 * - Inloggning/registrering går via Plex (/plex-login)
 * - Filmsidor visar "Spela på Plex" eller "Önska film"
 * - Länk till önskningar i menyn
 */
(function () {
  "use strict";
  var cfg = null;
  var lastPath = null;
  var statusCache = {};

  function token() {
    try { return localStorage.getItem("access_token"); } catch (e) { return null; }
  }

  async function api(path, opts) {
    opts = opts || {};
    var headers = Object.assign({ "Accept": "application/json" }, opts.headers || {});
    var t = token();
    if (t) headers["Authorization"] = "Bearer " + t;
    if (opts.body && typeof opts.body !== "string") {
      headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    var r = await fetch(path, Object.assign({}, opts, { headers: headers }));
    if (r.status === 401 && t && !opts._retried) {
      if (await refresh()) return api(path, Object.assign({}, opts, { _retried: true }));
    }
    var data = null;
    try { data = await r.json(); } catch (e) {}
    return { ok: r.ok, status: r.status, data: data };
  }

  async function refresh() {
    var rt = null;
    try { rt = localStorage.getItem("refresh_token"); } catch (e) {}
    if (!rt) return false;
    var r = await fetch("/api/v1/auth/refresh", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: rt })
    });
    if (!r.ok) return false;
    var d = await r.json();
    try {
      localStorage.setItem("access_token", d.access_token);
      localStorage.setItem("refresh_token", d.refresh_token);
    } catch (e) {}
    return true;
  }

  function loginUrl() {
    var next = location.pathname + location.search;
    if (/^\/(ka\/)?(login|register)/.test(location.pathname)) next = "/";
    return "/plex-login?next=" + encodeURIComponent(next);
  }

  function el(tag, attrs, children) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "class") n.className = attrs[k];
      else if (k === "text") n.textContent = attrs[k];
      else if (k.indexOf("on") === 0) n.addEventListener(k.slice(2), attrs[k]);
      else n.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { if (c) n.appendChild(c); });
    return n;
  }

  /* ---------- Inloggning via Plex ---------- */
  function handleAuthRoutes() {
    var p = location.pathname;
    if (/^\/(ka\/)?(login|register|reset-password|verify-email)\/?$/.test(p)) {
      var next = "/";
      try {
        var q = new URLSearchParams(location.search);
        next = q.get("redirect") || q.get("next") || q.get("from") || "/";
      } catch (e) {}
      location.replace("/plex-login?next=" + encodeURIComponent(next));
      return true;
    }
    if (cfg && cfg.require_login && !token()) {
      location.replace(loginUrl());
      return true;
    }
    return false;
  }

  /* Inloggningsrutan (Sign in / Create account) byts mot en Plex-knapp */
  function patchAuthDialog() {
    var dlg = document.querySelector('[data-testid="auth-dialog"]');
    if (!dlg || dlg.querySelector("#fd-plex-auth")) return;
    dlg.classList.add("fd-plex-auth-dlg");
    var panel = dlg.querySelector('[role="tabpanel"]');
    var tabs = dlg.querySelector('[role="tablist"]');
    var anchor = tabs || panel;
    if (!anchor || !anchor.parentElement) return;
    var block = el("div", { id: "fd-plex-auth", class: "fd-plex-auth" }, [
      el("p", { class: "fd-plex-note", text: "Logga in med ditt Plex-konto. Du kommer in om du har tillgång till Plex-servern." }),
      el("a", { class: "fd-plex-btn fd-plex-primary fd-plex-wide", href: loginUrl(), text: "Logga in med Plex" })
    ]);
    anchor.parentElement.insertBefore(block, anchor);
  }

  var cleanupQueued = false;
  function scheduleCleanup() {
    if (cleanupQueued) return;
    cleanupQueued = true;
    requestAnimationFrame(function () { cleanupQueued = false; cleanup(); });
  }
  new MutationObserver(function () { patchAuthDialog(); scheduleCleanup(); hideServicePicker(); })
    .observe(document.documentElement, { childList: true, subtree: true });

  /* ---------- Dilemma solver: välj om den letar i Plex eller även i TMDB ---------- */
  function getScope() {
    var m = /(?:^|; )fd_dilemma_scope=(plex|all)/.exec(document.cookie);
    return m ? m[1] : null;
  }
  function setScope(v) {
    document.cookie = "fd_dilemma_scope=" + v + "; path=/; max-age=31536000; SameSite=Lax";
  }

  function addDilemmaScope() {
    if (!/^\/(ka\/)?dilemma(\/|$)/.test(location.pathname)) {
      var old = document.getElementById("fd-dilemma-scope");
      if (old) old.remove();
      return;
    }
    if (document.getElementById("fd-dilemma-scope") || !cfg) return;
    var heads = Array.prototype.slice.call(document.querySelectorAll("main h1, main h2"));
    var head = heads.filter(function (h) { return visible(h) && /dilemma/i.test(h.textContent || ""); })[0];
    if (!head) return;
    var current = getScope() || (cfg.plex_library_count ? "plex" : "all");
    var group = el("div", { id: "fd-dilemma-scope", class: "fd-scope", role: "radiogroup", "aria-label": "Var ska den leta?" });
    group.appendChild(el("span", { class: "fd-scope-label", text: "Leta bland" }));
    [["plex", "Mina filmer i Plex"], ["all", "Plex + TMDB"]].forEach(function (o) {
      var b = el("button", { type: "button", role: "radio", class: "fd-scope-opt", "aria-checked": String(o[0] === current), text: o[1] });
      if (o[0] === "plex" && !cfg.plex_library_count) { b.disabled = true; b.title = "Plex-biblioteket är inte synkat än"; }
      b.addEventListener("click", function () {
        setScope(o[0]);
        group.querySelectorAll(".fd-scope-opt").forEach(function (x) { x.setAttribute("aria-checked", String(x === b)); });
      });
      group.appendChild(b);
    });
    if (!getScope()) setScope(current);
    head.insertAdjacentElement("afterend", group);
  }

  /* ---------- Plex-ruta på filmsidor ---------- */
  function movieIdFromPath() {
    var m = /^\/(?:ka\/)?movies\/(\d+)/.exec(location.pathname);
    return m ? Number(m[1]) : null;
  }

  function visible(n) {
    return n && n.getClientRects().length > 0 && getComputedStyle(n).visibility !== "hidden";
  }

  function findAnchor() {
    // Sidan har både mobil- och datorlayout; välj den som syns just nu
    var metas = document.querySelectorAll(".fd-meta-item");
    for (var i = 0; i < metas.length; i++) {
      if (visible(metas[i]) && metas[i].parentElement) return metas[i].parentElement;
    }
    var h1s = document.querySelectorAll("h1");
    for (var j = 0; j < h1s.length; j++) {
      if (visible(h1s[j])) return h1s[j].parentElement;
    }
    return null;
  }

  /* Bygger Plex-rutan: "Watch on Plex" om titeln finns i Plex, annars "Request movie/show" */
  function buildPlexBox(mid, st, id, extraClass) {
    var isTv = st.media_type === "tv";
    var children = [];
    if (st.on_plex) {
      children.push(el("a", {
        class: "fd-plex-btn fd-plex-primary", href: st.plex_url || "#", target: "_blank", rel: "noopener"
      }, [el("span", { class: "fd-plex-icon", text: "▶" }), el("span", { text: "Watch on Plex" })]));
      children.push(el("span", { class: "fd-plex-badge fd-plex-src-plex", text: "Finns i Plex" }));
      if (st.watched) children.push(el("span", { class: "fd-plex-badge fd-plex-seen", text: "✓ Sedd" }));
    } else if (!st.logged_in) {
      children.push(el("span", { class: "fd-plex-badge fd-plex-src-tmdb", text: "Endast TMDB" }));
      children.push(el("a", { class: "fd-plex-btn", href: loginUrl(), text: "Logga in med Plex för att önska" }));
    } else if (st.request_status && st.request_status !== "failed") {
      var label = {
        pending: "Önskad – väntar på att läggas till",
        sent: "Önskad – laddas ner",
        available: "Tillagd i Plex"
      }[st.request_status] || "Önskad";
      children.push(el("span", { class: "fd-plex-badge fd-plex-src-tmdb", text: "Endast TMDB" }));
      children.push(el("span", { class: "fd-plex-badge fd-plex-wish", text: "★ " + label }));
      children.push(el("a", { class: "fd-plex-link", href: "/onskningar", text: "Mina önskningar" }));
    } else {
      var reqLabel = isTv ? "Request show" : "Request movie";
      var btn = el("button", { class: "fd-plex-btn fd-plex-primary", type: "button" },
        [el("span", { class: "fd-plex-icon", text: "+" }), el("span", { text: reqLabel })]);
      btn.addEventListener("click", async function () {
        btn.disabled = true;
        btn.lastChild.textContent = "Skickar …";
        var r = await api("/api/local/requests", { method: "POST", body: { movie_id: mid } });
        delete statusCache[mid];
        if (!r.ok) {
          btn.disabled = false;
          btn.lastChild.textContent = reqLabel;
          alert((r.data && r.data.detail) || "Det gick inte att skicka önskan.");
        }
        refreshBox(true);
      });
      children.push(btn);
      children.push(el("span", { class: "fd-plex-badge fd-plex-src-tmdb", text: "Endast TMDB" }));
      if (st.request_status === "failed") {
        children.push(el("span", { class: "fd-plex-note fd-plex-err", text: "Förra försöket misslyckades." }));
      }
    }
    return el("div", { id: id, class: "fd-plex-box " + (extraClass || ""), "data-mid": String(mid),
                       "data-state": JSON.stringify(st) }, children);
  }

  function upToDate(node, mid, st) {
    return node && node.isConnected && visible(node) && node.getAttribute("data-mid") === String(mid) &&
      node.getAttribute("data-state") === JSON.stringify(st);
  }

  function renderBox(mid, st) {
    var old = document.getElementById("fd-plex-box");
    if (upToDate(old, mid, st)) return;
    if (old) old.remove();
    if (!st || !st.plex_configured) return;
    var anchor = findAnchor();
    if (!anchor) return;
    anchor.insertAdjacentElement("afterend", buildPlexBox(mid, st, "fd-plex-box"));
  }

  /* Dilemma solver: byt streamingknappen (Apple TV/Amazon m.fl.) mot Plex-knappen */
  function dilemmaPick() {
    var titles = document.querySelectorAll('[data-testid="pick-title"] a[href^="/movies/"]');
    for (var i = 0; i < titles.length; i++) {
      if (visible(titles[i])) {
        var m = /\/movies\/(\d+)/.exec(titles[i].getAttribute("href"));
        if (m) return Number(m[1]);
      }
    }
    return null;
  }

  function renderDilemma(mid, st) {
    var block = null;
    var blocks = document.querySelectorAll('[data-testid="pick-buttons-block"]');
    for (var i = 0; i < blocks.length; i++) if (visible(blocks[i]) || blocks[i].querySelector('[data-testid^="watch-"]')) { block = blocks[i]; break; }
    // Göm originalets streaming-/affiliateknappar i kortet
    if (block) {
      block.querySelectorAll('[data-testid^="watch-"], a[target="_blank"]').forEach(function (n) {
        if (!n.closest(".fd-plex-box")) n.style.display = "none";
      });
    }
    if (!st || !st.plex_configured) return;
    var old = document.getElementById("fd-dilemma-plex");
    if (upToDate(old, mid, st)) return;
    if (old) old.remove();
    var box = buildPlexBox(mid, st, "fd-dilemma-plex", "fd-plex-compact");
    if (block) {
      block.style.display = "";
      block.insertBefore(box, block.firstChild);
    } else {
      var reasons = document.querySelector('[data-testid="dilemma-reasons"]');
      var title = document.querySelector('[data-testid="pick-title"]');
      var anchor = reasons || (title && title.parentElement && title.parentElement.parentElement);
      if (anchor) anchor.insertAdjacentElement("afterend", box);
    }
  }

  /* Dilemma solver: dölj tjänstväljaren ("Any service") */
  function hideServicePicker() {
    if (!/^\/(ka\/)?dilemma(\/|$)/.test(location.pathname)) return;
    document.querySelectorAll('main button[aria-haspopup="true"]').forEach(function (b) {
      if (b.closest(".fd-plex-box, #fd-dilemma-scope")) return;
      if (/service/i.test(b.textContent || "") || b.querySelector("img")) {
        var wrap = b.parentElement && b.parentElement.childElementCount === 1 ? b.parentElement : b;
        hide(wrap);
      }
    });
  }

  async function refreshBox(force) {
    var onDilemma = /^\/(ka\/)?dilemma(\/|$)/.test(location.pathname);
    var mid = onDilemma ? dilemmaPick() : movieIdFromPath();
    if (!mid) {
      var old = document.getElementById("fd-plex-box");
      if (old) old.remove();
      return;
    }
    if (force || !statusCache[mid]) {
      var r = await api("/api/local/movies/" + mid + "/status");
      if (!r.ok) return;
      statusCache[mid] = r.data;
    }
    if (onDilemma) {
      if (dilemmaPick() === mid) renderDilemma(mid, statusCache[mid]);
    } else if (movieIdFromPath() === mid) {
      renderBox(mid, statusCache[mid]);
    }
  }

  /* ---------- Menylänkar: Plex (med bibliotek) och Önskningar ---------- */
  var plexSections = null;
  var sectionsLoading = false;

  function loadSections() {
    if (plexSections || sectionsLoading) return;
    sectionsLoading = true;
    api("/api/local/plex/sections").then(function (r) {
      sectionsLoading = false;
      if (r.ok && Array.isArray(r.data)) { plexSections = r.data; fillPlexMenu(); }
    }).catch(function () { sectionsLoading = false; });
  }

  function fillPlexMenu() {
    var menu = document.getElementById("fd-plex-menu");
    if (!menu) return;
    menu.innerHTML = "";
    if (!plexSections) {
      menu.appendChild(el("span", { class: "fd-plex-empty", text: "Laddar …" }));
      return;
    }
    plexSections.forEach(function (s) {
      var a = el("a", { href: "/plex/" + encodeURIComponent(s.key), role: "menuitem" },
        [el("span", { text: s.title }), el("span", { class: "fd-plex-count", text: String(s.count || 0) })]);
      menu.appendChild(a);
    });
    if (!plexSections.length) menu.appendChild(el("span", { class: "fd-plex-empty", text: "Inga bibliotek än" }));
    menu.appendChild(el("a", { href: "/plex", role: "menuitem", class: "fd-plex-all", text: "Alla bibliotek" }));
    menu.appendChild(el("a", { href: "/onskningar", role: "menuitem", text: "Önskningar" }));
  }

  function makeNavItem(lists, id, href, text) {
    var a = lists.cloneNode(false);
    a.id = id;
    a.setAttribute("href", href);
    a.textContent = text;
    a.removeAttribute("aria-current");
    a.addEventListener("click", function (e) { e.preventDefault(); location.href = href; }, true);
    return a;
  }

  function addNavLink() {
    if (!token() || document.getElementById("fd-nav-wishes")) return;
    var links = Array.prototype.slice.call(document.querySelectorAll("header a, nav a"));
    var lists = links.filter(function (a) { return a.getAttribute("href") === "/lists"; })[0];
    if (!lists || !lists.parentElement) return;
    var inLi = lists.parentElement.tagName === "LI";
    var container = inLi ? lists.parentElement : lists;

    function wrap(node) {
      if (!inLi) return node;
      var li = container.cloneNode(false);
      li.appendChild(node);
      return li;
    }

    // Plex med rullgardin
    var plexLink = makeNavItem(lists, "fd-nav-plex", "/plex", "Plex");
    plexLink.setAttribute("aria-haspopup", "true");
    plexLink.setAttribute("aria-expanded", "false");
    plexLink.appendChild(el("span", { class: "fd-plex-caret", "aria-hidden": "true", text: "▾" }));
    var holder = el("span", { class: "fd-plex-nav" }, [plexLink, el("div", { id: "fd-plex-menu", class: "fd-plex-menu", role: "menu" })]);
    var open = function () { holder.classList.add("open"); plexLink.setAttribute("aria-expanded", "true"); loadSections(); };
    var close = function () { holder.classList.remove("open"); plexLink.setAttribute("aria-expanded", "false"); };
    holder.addEventListener("mouseenter", open);
    holder.addEventListener("mouseleave", close);
    plexLink.addEventListener("focus", open);
    holder.addEventListener("focusout", function (e) { if (!holder.contains(e.relatedTarget)) close(); });
    holder.addEventListener("keydown", function (e) { if (e.key === "Escape") { close(); plexLink.focus(); } });

    var parent = container.parentElement;
    parent.insertBefore(wrap(holder), container.nextSibling);
    holder.id = "fd-nav-wishes";   // markerar att menyn redan är tillagd

    // "Famous Fans" bygger på Film Dilemmas egen data och är tom här – dölj den för att få plats
    links.forEach(function (a) {
      if (/^\s*famous fans\s*$/i.test(a.textContent || "")) {
        var item = a.parentElement && a.parentElement.tagName === "LI" ? a.parentElement : a;
        item.style.display = "none";
      }
    });
    fillPlexMenu();
    loadSections();
  }

  /* ---------- Bort med allt som inte har med TMDB eller Plex att göra ---------- */
  var HIDE_HEADINGS = /^(awards and festivals|from the critics'? polls|film dilemma'?s picks|one of a kind|reader reviews|comments|recommended by|famous fans|reviews from critics|critics)$/i;
  var AFFILIATE_TEXT = /amazon associate|may earn from|affiliate disclosure/i;

  function hide(n) { if (n && n.style.display !== "none") n.style.display = "none"; }

  function sectionOf(h) {
    // Närmaste omslutande sektion, men aldrig hela <main>
    var n = h.closest("section");
    if (n && n.tagName !== "MAIN" && !n.contains(document.querySelector("main h1"))) return n;
    n = h.parentElement;
    return n && n.tagName !== "MAIN" ? n : h;
  }

  function cleanup() {
    // Meny: Coffee och Discover
    document.querySelectorAll("header button, header a").forEach(function (b) {
      var t = (b.textContent || b.getAttribute("aria-label") || "").trim();
      if (/^coffee$|buy me a coffee/i.test(t) || b.getAttribute("href") === "/discover") {
        hide(b.parentElement && b.parentElement.tagName === "LI" ? b.parentElement : b);
      }
    });
    // Sidfot: behåll bara TMDB-erkännandet
    var footer = document.querySelector("footer");
    if (footer && !footer.dataset.fdClean) {
      var tmdb = footer.querySelector('a[href*="themoviedb.org"]');
      if (tmdb) {
        var keep = tmdb.closest("p") || tmdb;
        var n = keep;
        while (n && n !== footer) {
          Array.prototype.forEach.call(n.parentElement.children, function (sib) { if (sib !== n) hide(sib); });
          n = n.parentElement;
        }
        footer.dataset.fdClean = "1";
      }
    }
    // Sektioner som bygger på Film Dilemmas egen data
    document.querySelectorAll("main h2, main h3").forEach(function (h) {
      if (HIDE_HEADINGS.test((h.textContent || "").trim())) hide(sectionOf(h));
    });
    // Flik "Shop / Merch" på filmsidan
    document.querySelectorAll('[role="tab"]').forEach(function (t) {
      if (/^\s*shop/i.test(t.textContent || "")) hide(t);
    });
    // Startsidan: originalets AI-funktion och rundtur
    document.querySelectorAll("main button, main a").forEach(function (b) {
      if (/^\s*(describe your dilemma|watch the tour|take the tour)\s*$/i.test(b.textContent || "")) hide(b);
    });
    // Originalets eget betyg ("Film Dilemma: 6/10")
    document.querySelectorAll("main span").forEach(function (sp) {
      if (/^\s*Film Dilemma:\s*\d/.test(sp.textContent || "") && sp.textContent.length < 40) hide(sp);
    });
    // Affiliate-texter
    document.querySelectorAll("main p, main span, [role=dialog] p").forEach(function (p) {
      if (p.children.length < 3 && AFFILIATE_TEXT.test(p.textContent || "")) hide(p);
    });
    document.querySelectorAll("main span, main p").forEach(function (sp) {
      if (sp.childElementCount === 0 && /^Affiliate link ·\s*/.test(sp.textContent || "")) {
        sp.textContent = sp.textContent.replace(/^Affiliate link ·\s*/, "");
      }
    });
  }

  function tick() {
    if (location.pathname !== lastPath) {
      lastPath = location.pathname;
      if (handleAuthRoutes()) return;
    }
    refreshBox(false);
    addNavLink();
    addDilemmaScope();
    hideServicePicker();
    cleanup();
  }

  ["pushState", "replaceState"].forEach(function (fn) {
    var orig = history[fn];
    history[fn] = function () {
      var r = orig.apply(this, arguments);
      setTimeout(tick, 0);
      return r;
    };
  });
  window.addEventListener("popstate", function () { setTimeout(tick, 0); });
  window.addEventListener("storage", function (e) {
    if (e.key === "access_token") { statusCache = {}; }
  });

  handleAuthRoutes();
  fetch("/api/local/config", { headers: token() ? { Authorization: "Bearer " + token() } : {} })
    .then(function (r) { return r.json(); })
    .then(function (c) { cfg = c; handleAuthRoutes(); })
    .catch(function () {});
  setInterval(tick, 800);
})();

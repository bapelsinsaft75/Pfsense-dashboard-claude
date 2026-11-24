/* ======================================================================
 * WARNING: THIS FILE RUNS ON OTHER PEOPLE'S WEBSITES.
 * Film critics pasted our snippet into their sites; this script executes in
 * their pages. If it throws or moves, THEY see it, and we hear about it from
 * an angry partner, not from a test.
 *  - Behaviour changes MUST be opt-in (a new data-* attribute on the snippet).
 *    Never change the default placement, width or theme for existing partners.
 *  - Guarded by frontend/worker/partner-widget.test.mjs (golden placement
 *    snapshots on real partner layouts in frontend/worker/fixtures/partner-widget/)
 *    and frontend/scripts/require-widget.cjs, which blocks `npm run deploy`.
 *  - When a snapshot fails, do NOT regenerate fixtures or expectations to make
 *    it pass. Fix the widget, or update the one expectation deliberately, in the
 *    same commit, with the reason.
 * ====================================================================== */
/*! Film Dilemma partner widget - filmdilemma.com/partner-widget.js
 * Paste once, site-wide (footer):
 * <script async src="https://filmdilemma.com/partner-widget.js" data-partner="YOUR-SLUG"></script>
 * Optional: data-theme="dark" or data-theme="light" pins the widget's theme.
 * With NO data-theme (or data-theme="auto") the theme is DETECTED from the
 * host page's own background and then TRACKED, so a site with a real
 * light/dark toggle flips the widget with it -- see resolveTheme/watchTheme
 * below. An anchor
 * <div data-filmdilemma-widget></div> controls placement (wins over everything);
 * <div data-filmdilemma-widget="off"></div> on a page keeps the widget off that page;
 * optional data-target="<css selector>" on this tag puts it right after that element;
 * data-placement="wix-body" on this tag (opt-in, off by default) puts it under a Wix Blog post body, above tags/share;
 * without one, data-after-paragraph="N" on this script tag (positive integer)
 * puts the widget right after the Nth real-text direct-child <p> of the post's
 * text container (empty <p>&nbsp;</p> are skipped), so no template edit is
 * needed; fewer than N paragraphs falls through to the default (default: right
 * after the main <article>, else inside the best content container, else end
 * of <body> - see pickMainRoot, added after the 2026-08-31 GVN sidebar
 * report: magazine themes often tag sidebar/related teaser cards with
 * <article> too, sometimes ahead of the real post in DOM order, sometimes
 * with no <article> around the real post at all). Creates one 0-height iframe at
 * /embed/auto, which resolves the page same-origin (see
 * app/routers/partners.py + src/pages/EmbedAuto.js) and renders the
 * existing per-movie widget. Dedupe: if the snippet is pasted more than
 * once (or a CMS re-runs footer scripts), only the first execution
 * creates a widget - later ones see the existing iframe and no-op. A page
 * that instead carries an OLD per-post embed snippet pasted straight into
 * the post body (checked once, at load - won't catch one injected later by
 * other JS) never gets a second widget either: this script widens that
 * legacy iframe in place instead (see fixLegacyWidth) so partners who add
 * the site-wide script don't have to touch their old posts. A partner who
 * ONLY has the per-post snippet, with no site-wide script anywhere on the
 * page, cannot be healed this way - no script of ours runs there. Never
 * throws or shifts layout.
 *
 * Headline (2026-10-02): the page's own headline (og:title, else the first h1
 * inside the picked main root, else document.title; trimmed, 200 chars max) is
 * read ONCE from the DOM the reader's browser already loaded and sent to OUR
 * server as &h= on the /embed/auto src, so a post that is clearly about one film
 * (trailer, first reactions, interview) can show the widget even without a
 * mapped review URL. No request to the partner's server is ever made.
 *
 * Width: the iframe never assumes a fixed content-column width (partner
 * CMSes vary wildly - a hardcoded max-width looks like a floating box next
 * to a much wider or narrower column, see the 2026-08-30 Every Movie Has a
 * Lesson report). Instead it measures a "width source" element - the
 * majority-paragraph text container inside <article> when one exists
 * (falls back to <article> itself, then the actual insertion parent) - and
 * mirrors that element's rendered width, refreshed via ResizeObserver
 * (debounced window resize as fallback for older browsers) so it tracks
 * late layout shifts (lazy images, font swap, injected ads). 680px is only
 * the last-resort default when no width source is available at all (e.g.
 * appended straight to <body> on a page with no <article>). */
(function(){"use strict";try{
// currentScript is null (some optimizer plugins execute the fetched body via
// eval/Function, outside any script element's own execution) or present but
// stripped of our data-* (execution via a freshly injected <script> with no
// attributes copied over) when a page's asset optimizer (e.g. FlyingPress
// "delay/execute on interaction") rewrites the snippet instead of loading it
// normally - see the 2026-08-31 GVN report. Either way the ORIGINAL tag the
// partner pasted is still sitting in the DOM (untouched, just never natively
// executed), still carrying data-partner - so when currentScript can't tell
// us the slug, find that original tag instead. currentScript WITH
// data-partner always wins first (multiple normal pastes, or normal
// loading), so nothing changes for a page that never gets rewritten.
var s=document.currentScript;
if(!s||!s.getAttribute("data-partner")){
  s=document.querySelector('script[data-partner][src*="partner-widget"], script[data-partner][data-src*="partner-widget"]');
}
if(!s)return;
var slug=(s.getAttribute("data-partner")||"").trim();if(!slug)return;
var afterP=parseInt(s.getAttribute("data-after-paragraph"),10);if(!(afterP>0))afterP=0;
// Three states, not two. An explicit "dark"/"light" PINS the theme and is
// never re-evaluated - Don has data-theme="dark" today and his widget must
// behave exactly as it always has. Absent or "auto" means detect-and-track
// (GVN's site has a real dark-mode toggle, so a static attribute cannot be
// right on both of its states). Anything else - a typo, a templating
// leftover - falls back to "light", which is what it already resolved to
// before this file could detect anything.
var themeAttr=s.getAttribute("data-theme");
var pinnedTheme=(themeAttr==="dark"||themeAttr==="light")?themeAttr:null;
var autoTheme=(themeAttr===null||themeAttr==="auto");
var origin="https://filmdilemma.com";
var DEFAULT_MAX=680;
// The host-colour correction of a wrongly pinned dark theme is limited to the
// one partner whose snippet is known to be wrong, so no other partner's render
// can change.
var HOST_FIX_SLUG="every-movie-has-a-lesson";
// --- host-page theme detection (auto mode only) ---------------------------
// Reads the host's EFFECTIVE background rather than trusting a class name:
// every CMS names its dark class something different ("dark", "night-mode",
// "theme--dark", a data-bs-theme attribute), while the rendered background is
// the one signal every dark theme necessarily changes.
function parseRgb(value){
  try{
    var m=/^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)(?:[\s,/]+([\d.%]+))?\s*\)$/i.exec(value||"");
    if(!m)return null;
    var a=m[4]===undefined?1:(String(m[4]).indexOf("%")>-1?parseFloat(m[4])/100:parseFloat(m[4]));
    return [parseFloat(m[1]),parseFloat(m[2]),parseFloat(m[3]),isNaN(a)?1:a];
  }catch(e){return null}
}
// WCAG relative luminance, same formula the widget itself uses for its accent
// contrast (see buildAccent in src/pages/EmbedSimilar.js).
function luminance(rgb){
  var c=[rgb[0]/255,rgb[1]/255,rgb[2]/255].map(function(v){
    return v<=0.03928?v/12.92:Math.pow((v+0.055)/1.055,2.4);
  });
  return 0.2126*c[0]+0.7152*c[1]+0.0722*c[2];
}
// Walks up from the element the widget lands next to until it finds a
// background that is actually painted (alpha > 0.5). A transparent chain all
// the way to <html> is INCONCLUSIVE, and inconclusive resolves to "light" -
// exactly what every attribute-less snippet already renders today, so the new
// detection can only ever change a page it is confident about.
function textTheme(el){
  // The host's own text colour at `el` (walking up to the nearest element whose
  // computed colour is readable): text is always rendered over the TRUE ground,
  // so dark text means a light ground and light text a dark one. Null when no
  // readable colour is found or it is mid-grey.
  try{
    var node=el;
    while(node&&node.nodeType===1){
      var fg=parseRgb(window.getComputedStyle(node).color);
      if(fg&&fg[3]>0.5){
        var lum=luminance(fg);
        return lum<0.35?"light":(lum>0.65?"dark":null);
      }
      node=node.parentElement;
    }
  }catch(e){}
  return null;
}
function resolveTheme(startEl){
  try{
    var start=startEl||document.body||document.documentElement;
    var byText=textTheme(start);
    var node=start;
    while(node&&node.nodeType===1){
      var rgb=parseRgb(window.getComputedStyle(node).backgroundColor);
      if(rgb&&rgb[3]>0.5){
        var byBg=luminance(rgb)<0.5?"dark":"light";
        // A painted ancestor that CONTRADICTS the text colour is not the ground
        // the text sits on (GVN dark mode, 2026-10-31: body dark, the post
        // column white through a pseudo-element, its text dark). Trust the text.
        return (byText&&byText!==byBg)?byText:byBg;
      }
      node=node.parentElement;
    }
    // Nothing painted anywhere up the tree (a gradient or image background, or
    // a canvas page). The host's TEXT colour is the next honest signal: dark
    // text means a light page, light text a dark one. Only then the OS
    // preference, which describes the reader's machine, not this page (2026-10-31:
    // a dark-mode reader on GVN's white page got light-grey text on white).
    if(byText)return byText;
    if(window.matchMedia&&window.matchMedia("(prefers-color-scheme: dark)").matches)return "dark";
  }catch(e){}
  return "light";
}
// The ground DIRECTLY behind the frame (2026-10-31, GVN dark mode): the page
// chrome was dark, the post column behind the widget white, and the ancestor
// walk met the dark page background first because Elementor paints the white
// through a sibling overlay, not an ancestor. elementsFromPoint at the frame's
// own position sees whatever is painted there, overlays included. Fixed or
// sticky elements (a header the frame scrolled under) are skipped. Returns
// null when the frame is off screen or the browser lacks the API; callers then
// fall back to the ancestor walk.
function groundThemeAt(frame){
  try{
    if(!document.elementsFromPoint||!frame||!frame.getBoundingClientRect)return null;
    var r=frame.getBoundingClientRect();
    if(!r||r.width<10)return null;
    var x=Math.min(window.innerWidth-2,Math.max(1,r.left+r.width/2));
    var y=r.top+1;
    if(y<0||y>window.innerHeight-1)return null;
    var stack=document.elementsFromPoint(x,y)||[],i,el,cs,rgb;
    for(i=0;i<stack.length;i++){
      el=stack[i];
      if(el===frame||(frame.contains&&frame.contains(el)))continue;
      cs=window.getComputedStyle(el);
      if(cs.position==="fixed"||cs.position==="sticky")continue;
      rgb=parseRgb(cs.backgroundColor);
      if(rgb&&rgb[3]>0.5)return luminance(rgb)<0.5?"dark":"light";
    }
  }catch(e){}
  return null;
}
// Host colour check used by both the loader (pinned theme) and the legacy-iframe
// path. See the comment where the loader applies it.
function paintedHostTheme(startEl){
  try{
    var node=startEl||document.body||document.documentElement;
    while(node&&node.nodeType===1){
      var cs=window.getComputedStyle(node);
      var rgb=parseRgb(cs.backgroundColor);
      if(rgb&&rgb[3]>0.5)return luminance(rgb)<0.5?"dark":"light";
      if(cs.backgroundImage&&cs.backgroundImage!=="none")return null;
      node=node.parentElement;
    }
    var scheme=window.getComputedStyle(document.documentElement).colorScheme||"";
    return scheme.indexOf("dark")>-1?null:"light";
  }catch(e){return null}
}
// Deepest element inside `article` holding most of its <p> tags - usually
// the actual text column (e.g. WordPress .entry-content); on block-based
// builders (Squarespace) this is one text block among many, not the whole
// post, so callers must not assume it is safe to insert *into*.
function findTextContainer(article){
  try{
    var ps=article.querySelectorAll("p");
    if(ps.length<2)return null;
    var nodes=[],counts=[],i,parent,idx;
    for(i=0;i<ps.length;i++){
      parent=ps[i].parentElement;if(!parent)continue;
      idx=nodes.indexOf(parent);
      if(idx===-1){nodes.push(parent);counts.push(1)}else{counts[idx]++}
    }
    if(!nodes.length)return null;
    var best=0;
    for(i=1;i<counts.length;i++){if(counts[i]>counts[best])best=i}
    if(counts[best]<ps.length*0.6)return null;
    return nodes[best];
  }catch(e){return null}
}
// Sum of a container's own <p> text (not innerText - a sidebar teaser card's
// heading/link text can run to 100+ chars with zero actual paragraphs, see
// the 2026-08-31 GVN report below).
function pMass(el){
  try{
    var ps=el.querySelectorAll("p"),sum=0,i;
    for(i=0;i<ps.length;i++)sum+=(ps[i].textContent||"").length;
    return sum;
  }catch(e){return 0}
}
var MAIN_ROOT_FLOOR=400;
// Explicit allowlist, not any div: a wrapping row/section that contains BOTH
// the real article and its sidebar would outscore the real column on p-mass
// alone and put the widget right back where it started.
var CONTENT_SELECTORS=['[itemprop="articleBody"]','.entry-content','.post-content','.elementor-widget-theme-post-content','.article-content','main'];
// Picks the element the widget should anchor to, replacing a naive first-
// <article> pick that breaks on magazine themes: GVN (geekvibesnation.com,
// SmartMag+Elementor) marks its sidebar "highlights" teaser cards with
// <article class="l-post..."> too, ahead of the real post in DOM order, and
// - worse - the real post there isn't wrapped in an <article> at all, just a
// plain Elementor column div. A single <article> (every fixture below, and
// every partner site seen before GVN) is trusted unchanged - no page that
// already works today can regress. Multiple candidates are scored by p-mass
// and must clear a floor to win, so zero-paragraph teaser stubs can't.
function pickMainRoot(){
  try{
    var articles=document.querySelectorAll("article");
    if(articles.length===1)return articles[0];
    var i,score,best=null,bestScore=0;
    if(articles.length>1){
      for(i=0;i<articles.length;i++){
        score=pMass(articles[i]);
        if(score>bestScore){bestScore=score;best=articles[i]}
      }
      if(best&&bestScore>=MAIN_ROOT_FLOOR)return best;
    }
    // Tier 2: no <article> is a real candidate - fall back to a named
    // content-container class/attribute, same p-mass scoring.
    var best2=null,bestScore2=0,j,list,el;
    for(j=0;j<CONTENT_SELECTORS.length;j++){
      list=document.querySelectorAll(CONTENT_SELECTORS[j]);
      for(i=0;i<list.length;i++){
        el=list[i];score=pMass(el);
        if(score>bestScore2){bestScore2=score;best2=el}
      }
    }
    if(best2&&bestScore2>=MAIN_ROOT_FLOOR)return best2;
  }catch(e){}
  return null;
}
// Blocks that close a review post (score box, author box). When the text
// column is followed by more content inside the article (GVN's 2026-10-31
// redesign: review box and author box inside the post body, then comments and
// a related-posts aside, all inside one <article>), the widget used to land
// after the whole <article>, at the bottom of the page under the related grid.
// GVN asked for it "under the review score or the author box": the LAST such
// block in document order that sits inside the main root and not in an aside
// or the comments becomes the anchor. Pages without these blocks (every other
// fixture) keep the previous behaviour exactly.
var POST_TAIL_SELECTORS=['.saboxplugin-wrap','.author-box','.post-author-box','.entry-author','.review-box','.wp-review-box','.rating-box'];
function findPostTail(root){
  try{
    var best=null,i,j,list,el;
    for(j=0;j<POST_TAIL_SELECTORS.length;j++){
      list=root.querySelectorAll(POST_TAIL_SELECTORS[j]);
      for(i=0;i<list.length;i++){
        el=list[i];
        if(el.closest&&el.closest("aside,#comments,.comments-area,header,footer,nav"))continue;
        if(!best||(el.compareDocumentPosition(best)&Node.DOCUMENT_POSITION_PRECEDING))best=el;
      }
    }
    return best;
  }catch(e){return null}
}
// True only if real content (not just scripts/styles) follows `container`
// on the way up to `root` - i.e. it is unsafe to append inside `container`
// because it would land mid-article, ahead of later blocks.
function hasRealContentAfter(container,root){
  try{
    var SKIP=/^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE)$/;
    var node=container;
    while(node&&node!==root){
      var sib=node.nextElementSibling;
      while(sib){
        if(!SKIP.test(sib.tagName)){
          if(sib.querySelector&&sib.querySelector("p,img,figure,h1,h2,h3,h4,li,blockquote"))return true;
          if((sib.textContent||"").trim().length>40)return true;
        }
        sib=sib.nextElementSibling;
      }
      node=node.parentElement;
    }
    return false;
  }catch(e){return true}
}
function measureWidth(el){
  try{var w=el.getBoundingClientRect().width;if(w>0)return Math.round(w)}catch(e){}
  return 0;
}
// Width of the column the TEXT actually sits in (2026-10-07, Film Focus Online
// on Wix mobile): the width source's border box can be wider than its text.
// Wix wraps every paragraph in its own div with 18px side padding, so
// mirroring the box made the widget stick out 18px past the paragraphs on both
// sides (and, on a phone, past the screen edge). The evidence is the real
// paragraphs: up to 12 <p> with 60+ characters of text, their smallest inset
// from the box on each side. Only a SYMMETRIC inset (sides within 2px of each
// other) that is at most 25% of the box in total is removed, so the iframe,
// which is centred in its parent, stays centred on the text column; a lopsided
// or large inset (an indented layout, a narrow max-width column in a wide box)
// changes nothing. With fewer than 2 such paragraphs the box's own padding and
// border is the evidence instead. Zero inset (WordPress, Squarespace text
// blocks, ...) returns exactly measureWidth.
function measureTextWidth(el){
  var w=measureWidth(el);
  if(!w)return 0;
  try{
    var r=el.getBoundingClientRect(),minL=1e9,minR=1e9,n=0,ps=el.querySelectorAll("p"),i,pr,t,inset=0,l,rr,cs;
    for(i=0;i<ps.length&&n<12;i++){
      t=(ps[i].textContent||"").replace(/\s+/g," ");
      if(t.length<60)continue;
      pr=ps[i].getBoundingClientRect();
      if(!(pr.width>0))continue;
      n++;
      minL=Math.min(minL,pr.left-r.left);
      minR=Math.min(minR,r.right-pr.right);
    }
    if(n>=2){
      if(Math.abs(minL-minR)<=2)inset=Math.min(minL,minR)*2;
    }else{
      cs=window.getComputedStyle(el);
      l=(parseFloat(cs.paddingLeft)||0)+(parseFloat(cs.borderLeftWidth)||0);
      rr=(parseFloat(cs.paddingRight)||0)+(parseFloat(cs.borderRightWidth)||0);
      inset=Math.min(l,rr)*2;
    }
    if(inset>0&&inset<=w*0.25)return Math.round(w-inset);
  }catch(e){}
  return w;
}
// A legacy per-post snippet (pasted into the post body before the site-wide
// script existed) hardcodes style="...;max-width:680px" on its own iframe.
// Once the site-wide script also runs on the page we can heal that iframe in
// place: same width-source logic as a fresh widget (findTextContainer on the
// nearest enclosing <article>, falling back to the article itself), applied
// to someone else's markup instead of ours. If the snippet's own wrapper
// element (not the iframe) is the one carrying an inline max-width, that
// gets corrected too. No enclosing <article> means no safe measurement, so
// the iframe is left exactly as pasted rather than guessed at.
function fixLegacyWidth(f){
  try{
    var article=f.parentElement;
    while(article&&article.tagName!=="ARTICLE")article=article.parentElement;
    if(!article)return;
    var widthSource=findTextContainer(article)||article;
    // Squarespace multi-column row: a legacy iframe pasted into one column
    // (col.sqs-col-6 etc) sharing its row with a sibling column has a real
    // containing block far narrower than the article's text column, so
    // measuring the text column there is inert - use the column instead.
    var col=f.parentElement;
    while(col&&col!==article){
      if(col.className&&/\bsqs-col-\d+\b/.test(col.className)){
        if(col.parentElement&&col.parentElement.children.length>1)widthSource=col;
        break;
      }
      col=col.parentElement;
    }
    var wrapper=null,node=f.parentElement;
    while(node&&node!==article){
      if(node.style&&node.style.maxWidth){wrapper=node;break}
      node=node.parentElement;
    }
    var apply=function(){
      var w=measureWidth(widthSource);if(!w)return;
      f.style.maxWidth=w+"px";
      if(wrapper)wrapper.style.maxWidth=w+"px";
    };
    apply();
    // Don's legacy iframe hardcodes theme=dark on a white page (2026-10-03), and
    // this path returned before any theme check. Dark -> light only, and only
    // when the host clearly paints light; a src rewrite reloads that iframe once.
    try{
      var cur=f.getAttribute("src")||"";
      if(slug===HOST_FIX_SLUG&&/[?&]theme=dark(&|$)/.test(cur)&&/\/embed\//.test(cur)&&paintedHostTheme(f.parentElement)==="light"){
        f.setAttribute("src",cur.replace(/([?&])theme=dark(&|$)/,"$1theme=light$2")+(/[?&]bg=/.test(cur)?"":"&bg=transparent"));
      }
    }catch(e){}
    if(window.ResizeObserver){
      try{new ResizeObserver(apply).observe(widthSource)}catch(e){}
    }else{
      var t=null;
      window.addEventListener("resize",function(){
        if(t)clearTimeout(t);
        t=setTimeout(apply,150);
      });
    }
  }catch(e){}
}
// Old per-post snippets pasted on partner sites carry a FOLLOWED link, and we
// can't edit those posts. Google's link spam policy (2026-10-02) names
// keyword-rich links distributed through widgets, so this script nofollows
// them on every page it runs on. It runs FIRST in run() (before any early
// return) and touches only our old snippets' signatures: a filmdilemma.com
// link whose trimmed, lowercased text contains "powered by film dilemma" or
// ENDS WITH "on film dilemma" (card, taste) or "- film dilemma" (box office).
// A link whose whole text is exactly "film dilemma" is never matched: it is a
// partner's own editorial link, or a new snippet that is already nofollow.
// Only the rel attribute changes - no text or layout. The change is made by
// JS, which Google executes when rendering.
function nofollowOldLinks(){try{
  var as=document.querySelectorAll('a[href*="filmdilemma.com"]'),i,j,a,t,rel,hit,ends=["powered by film dilemma","on film dilemma","- film dilemma"];
  for(i=0;i<as.length;i++){
    a=as[i];
    t=(a.textContent||"").replace(/^\s+|\s+$/g,"").toLowerCase();
    if(t==="film dilemma")continue;
    hit=t.indexOf("powered by film dilemma")>-1;
    for(j=0;!hit&&j<ends.length;j++){
      hit=t.length>=ends[j].length&&t.lastIndexOf(ends[j])===t.length-ends[j].length;
    }
    if(!hit)continue;
    rel=(a.getAttribute("rel")||"").split(/\s+/).filter(Boolean);
    if(rel.indexOf("nofollow")===-1){rel.push("nofollow");a.setAttribute("rel",rel.join(" "))}
  }
}catch(e){}}
// The page headline, for matching news posts to one film server-side. Own
// try/catch and ""-default: a throw here (encodeURIComponent raises URIError on
// a lone surrogate, which a 200-char cut can create) must never take the URL
// match down with it. `root` is the picked main root when the caller has one;
// an explicit-anchor page never ran pickMainRoot, so it is asked for lazily,
// and only when og:title is missing.
function readHeadline(root){
  var h="";
  try{
    var blank=function(v){return !v||!v.replace(/\s+/g,"")};
    var m=document.querySelector('meta[property="og:title"]')||document.querySelector('meta[name="og:title"]');
    h=(m&&m.getAttribute("content"))||"";
    if(blank(h)){
      var r=root||pickMainRoot();
      var h1=r&&r.querySelector?r.querySelector("h1"):null;
      h=h1?(h1.textContent||""):"";
    }
    if(blank(h))h=document.title||"";
    h=h.replace(/\s+/g," ").replace(/^\s+|\s+$/g,"");
    if(h.length>200){
      h=h.slice(0,200);
      var c=h.charCodeAt(199);
      if(c>=0xD800&&c<=0xDBFF)h=h.slice(0,199);
      h=h.replace(/\s+$/,"");
    }
    encodeURIComponent(h);
  }catch(e){h=""}
  return h;
}
// Title candidates for the block on pages without a mapped review
// (docs/2026-10-31_growth-plan/09-widget-empty-state.md). Short phrases only,
// never the page text: italics and cites, quoted phrases, "Title (Year)",
// Capitalised Phrases of 2 to 6 words. At most 40, 80 chars each. Own
// try/catch: a throw here must never take the widget down.
// Only an article page may get the "Films in this article" view. Home, about,
// listing and testimonial pages name films in sidebars and quotes, and the
// block must show the partner's latest reviews there instead (Outside the
// Spotlight, 2026-11-04). og:type decides when the site sets it (Wix, Yoast,
// Rank Math); without it only the bare home page is ruled out.
function isArticlePage(pageUrl){
  try{
    var og=document.querySelector('meta[property="og:type"]');
    var t=og&&(og.getAttribute("content")||"").trim().toLowerCase();
    if(t)return t==="article";
    return !!new window.URL(pageUrl,window.location.href).pathname.replace(/\/+$/,"");
  }catch(e){return true}
}
function readCandidates(root){
  var out=[],seen={},r=root||pickMainRoot()||document.body;
  function push(t,src,y){try{
    t=(t||"").replace(/\s+/g," ").replace(/^[\s"'\u201c\u201d\u2018\u2019\u00ab\u00bb(]+|[\s"'\u201c\u201d\u2018\u2019\u00ab\u00bb),.:;!?]+$/g,"");
    if(t.length<2||t.length>80)return;
    var k=t.toLowerCase()+"|"+(y||"");
    if(seen[k]||out.length>=40)return;
    seen[k]=1;var c={text:t,source:src};if(y)c.year=y;out.push(c);
  }catch(e){}}
  try{
    var yearAt=/^(.{2,80}?)\s*\(((?:19|20)\d{2})\)/,i,t,m;
    var ems=r.querySelectorAll("i,em,cite"),after,ay;
    for(i=0;i<ems.length&&i<120;i++){
      t=(ems[i].textContent||"");m=t.match(yearAt);
      if(m){push(m[1],"year",parseInt(m[2],10));continue}
      // "<i>Heat</i> (1995)": the year sits right after the italics.
      after=ems[i].nextSibling;ay=after&&after.nodeType===3?(after.textContent||"").match(/^\s*\(((?:19|20)\d{2})\)/):null;
      if(ay)push(t,"year",parseInt(ay[1],10));
      push(t,"em");
    }
    var ps=r.querySelectorAll("p,li,h2,h3,blockquote"),txt,q;
    var reQuote=/[\u201c"]([^\u201d"]{2,80})[\u201d"]/g,reYear=/([A-Z][^.!?\n()]{1,78}?)\s*\(((?:19|20)\d{2})\)/g;
    var reCaps=/\b([A-Z][\w'\u2019:-]+(?:\s+(?:of|the|a|an|and|in|on|to|for|[A-Z][\w'\u2019:-]+)){1,5})\b/g;
    for(i=0;i<ps.length&&i<200&&out.length<40;i++){
      txt=(ps[i].textContent||"");
      reQuote.lastIndex=0;while((q=reQuote.exec(txt))&&out.length<40)push(q[1],"quote");
      // "Michael Mann's Heat (1995)": the phrase before a year may start with a
      // possessive or a sentence start, so the last 1 to 4 words are tried too.
      reYear.lastIndex=0;while((q=reYear.exec(txt))&&out.length<40){
        var yr=parseInt(q[2],10),words=q[1].replace(/\s+/g," ").trim().split(" "),k;
        push(q[1],"year",yr);
        for(k=1;k<=4&&k<words.length&&out.length<40;k++)push(words.slice(words.length-k).join(" "),"year",yr);
      }
    }
    for(i=0;i<ps.length&&i<200&&out.length<40;i++){
      txt=(ps[i].textContent||"");
      reCaps.lastIndex=0;while((q=reCaps.exec(txt))&&out.length<40)push(q[1],"caps");
    }
  }catch(e){}
  return out;
}
// Wait for Wix's React before touching its post (2026-11-17, Outside the
// Spotlight: "the widget is not displaying"). Measured on the live site with a
// real Chromium: a live Wix page (Thunderbolt) is server-rendered, then React
// HYDRATES it ~2.5-4 s after the page loads. Any node we insert into the post
// before that makes hydration fail, React throws away the whole server-rendered
// blog area and renders it again (4 of 4 loads), our iframe goes with it and has
// to load again from zero: the widget appeared only 6-10 s after the reader
// reached it. Inserted after React has claimed the post body (its DOM node then
// carries a __reactFiber key), nothing is thrown away (4 of 4 loads). The same
// fault, measured the same night, hit Film Focus Online (Wix, default placement:
// 2 to 4 frame loads, the widget vanished after showing). So on ANY live Wix page
// (window.viewerModel and window.thunderboltVersion, both set before any snippet
// runs) the frame waits until React owns the element it goes into (wixReactTarget:
// the anchor's parent for an "after" placement, the anchor itself for an append),
// for at most WIX_REACT_WAIT_MS; after that it inserts as before (and
// keepAttached below still puts a discarded frame back). Without moveBefore,
// run() is retried every 100 ms (waitForWixReact). Static pages and every non-Wix
// partner never wait.
var WIX_REACT_WAIT_MS=10000,wixWaitStart=0,wixSettled=false;
function hasReactFiber(el){
  try{var k=Object.keys(el),i;for(i=0;i<k.length;i++){if(k[i].indexOf("__reactFiber")===0||k[i].indexOf("__reactInternalInstance")===0)return true}}catch(e){}
  return false;
}
// True when run() must come back later (it has already scheduled that).
function waitForWixReact(body){
  try{
    if(!body)return false;
    if(hasReactFiber(body)){
      if(wixSettled)return false;
      wixSettled=true;setTimeout(run,100);return true;
    }
    if(!wixWaitStart)wixWaitStart=Date.now();
    if(Date.now()-wixWaitStart>=WIX_REACT_WAIT_MS)return false;
    setTimeout(run,100);return true;
  }catch(e){return false}
}
function run(){try{
nofollowOldLinks();
// The partner's own off switch for one page (2026-11-05, Hollywood in Toto):
// <div data-filmdilemma-widget="off"></div> anywhere in a post keeps the
// site-wide widget off that page. Opt-in: no marker, no change.
if(document.querySelector('[data-filmdilemma-widget="off"]'))return;
if(document.querySelector('iframe[src*="/embed/auto"]'))return;
var canon=document.querySelector('link[rel="canonical"]');
var pageUrl=(canon&&canon.href)||window.location.href;
if(!canon){var p;try{p=new window.URL(pageUrl).pathname.replace(/\/+$/,"")}catch(e){return}if(!p)return}
var legacyEmbed=document.querySelector('iframe[src*="/embed/movies/"]');
if(legacyEmbed){fixLegacyWidth(legacyEmbed);return}
// Where the browser can MOVE an iframe without reloading it (Element.moveBefore,
// Chrome 133+), the frame does not wait idle for Wix's React: it starts loading
// at once, parked at the end of <body> (outside Wix's React root, height 0,
// hidden), and is moved into the post the moment React owns it (unparkForWix
// below). Measured on Outside the Spotlight: visible ~1-2 s after reaching the
// end of the post instead of 3-4.5 s. Other browsers keep the plain wait.
var canMoveFrame=false;try{canMoveFrame=typeof window.Element==="function"&&typeof window.Element.prototype.moveBefore==="function"}catch(e){}
// The element React must own before the frame may go in, or null when this is
// not a live Wix page or the placement is the end-of-<body> fallback.
function wixReactTarget(){
  try{
    if(!window.viewerModel||!window.thunderboltVersion)return null;
    var pl=resolvePlacement();
    if(!pl.real||!pl.anchor)return null;
    var t=pl.after?pl.anchor.parentElement:pl.anchor;
    return t&&t!==document.body&&t!==document.documentElement?t:null;
  }catch(e){return null}
}
var wixTarget0=wixReactTarget();
var parkForWix=false;
if(canMoveFrame){
  try{parkForWix=!!(wixTarget0&&!hasReactFiber(wixTarget0)&&document.body)}catch(e){parkForWix=false}
}else if(waitForWixReact(wixTarget0))return;

// Wix Blog post: <article data-hook="post"> holds data-hook="post-description"
// (the review body) followed by data-hook="post-footer" (tags, share). Wix class
// names are hashed and unstable but data-hook is stable. Wix wraps every
// paragraph in its own div, so findTextContainer finds nothing there and the
// default would land the widget after the whole <article>, below tags and share.
// Returns the body section, or null when the hooks are absent (any non-Wix
// site), which leaves placement exactly as before.
function findWixPostBody(){
  // Opt-in only: without data-placement="wix-body" on the script tag this is never
  // used, so every existing partner (e.g. Film Focus Online) places exactly as before.
  if((s.getAttribute("data-placement")||"").trim()!=="wix-body")return null;
  try{
    var post=document.querySelector('article[data-hook="post"]');
    var body=post?post.querySelector('[data-hook="post-description"]'):null;
    return body&&body.parentElement?body:null;
  }catch(e){return null}
}
// Optional data-target="<css selector>" on the script tag; invalid or missing
// selector returns null.
function findTarget(){
  try{
    var sel=(s.getAttribute("data-target")||"").trim();
    return sel?document.querySelector(sel):null;
  }catch(e){return null}
}
// Where the widget goes (the placement rules in the header comment). A function
// rather than inline code so it can be asked AGAIN when the host re-renders its
// page and throws our iframe away (see keepAttached below). `real` is false only
// for the end-of-<body> last resort.
function resolvePlacement(){
var explicitAnchor=document.querySelector("[data-filmdilemma-widget]");
var anchor,after,widthSource,mainRoot=null,real=true,postTail=null;
var targetEl=explicitAnchor?null:findTarget();
if(explicitAnchor){
  anchor=explicitAnchor;after=false;widthSource=explicitAnchor;
}else if(targetEl){
  // data-target="<css selector>" on the script tag: right after that element.
  anchor=targetEl;after=true;widthSource=targetEl;
}else{
  var article=pickMainRoot();
  mainRoot=article;
  var isArticleTag=!!article&&article.tagName==="ARTICLE";
  var textContainer=article?findTextContainer(article):null;
  // data-after-paragraph: Nth direct-child <p> with real text (WordPress emits
  // empty <p>&nbsp;</p>); fewer than N falls through to the placement below.
  if(afterP&&textContainer){
    var kids=textContainer.children,n=0,k;
    for(k=0;k<kids.length;k++){
      if(kids[k].tagName==="P"&&(kids[k].textContent||"").replace(/\s+/g,"")&&++n===afterP){
        anchor=kids[k];after=true;widthSource=textContainer;break;
      }
    }
  }
  var wixBody=anchor?null:findWixPostBody();
  if(anchor){
  }else if(wixBody){
    // Wix Blog: directly under the review text, above tags and share row.
    anchor=wixBody;after=true;widthSource=wixBody;
  }else if(textContainer&&!hasRealContentAfter(textContainer,article)){
    anchor=textContainer;after=false;widthSource=textContainer;
  }else if(article&&isArticleTag&&textContainer&&(postTail=findPostTail(article))){
    // A real <article> whose text column is followed by more blocks: sit right
    // under the review score or the author box, inside the post column, not
    // after the whole article. Tier-2 roots (no <article>) keep appending
    // inside their container as before, so no live partner page moves.
    anchor=postTail;after=true;widthSource=textContainer;
  }else if(article&&isArticleTag){
    anchor=article;after=true;widthSource=textContainer||article;
  }else if(article){
    // Tier-2 root is some CMS's generic container, not a real <article> - it
    // may be one flex/grid child in a multi-column row (GVN's two-column
    // Elementor layout), so inserting "afterend" would add a third child and
    // break that row. Appending inside it instead never touches the row.
    anchor=article;after=false;widthSource=textContainer||article;
  }else{
    anchor=document.body;after=false;widthSource=null;real=false;
  }
}
return {anchor:anchor,after:after,widthSource:widthSource,mainRoot:mainRoot,real:real};
}
var placement=resolvePlacement();
var anchor=placement.anchor,after=placement.after,widthSource=placement.widthSource,mainRoot=placement.mainRoot;
if(!anchor)return;

// Detected from where the widget actually sits (the anchor, not <body>): on a
// page whose article panel is dark inside a light chrome, the panel is the
// ground the widget will be read against.
var themeSource=widthSource||anchor;
// A pinned data-theme is a human's guess and can be wrong: Don's snippet pins
// "dark" on a WHITE Squarespace page, which rendered a dark box on a light site
// (2026-10-03). So a pin is only honoured while the host does not clearly
// contradict it. paintedHostTheme() is deliberately stricter than
// resolveTheme(): it answers only when a real background is painted somewhere
// up the tree, or when nothing is painted and the page has not opted into a
// dark color-scheme (then the canvas is the browser's white). A background
// IMAGE anywhere on the way up, or a missing answer, keeps the pin untouched.
// Only a contradicted pin is changed: it is replaced by the theme the host
// really shows, and since that theme was read from the host the widget may drop
// its own ground (bg=transparent). A pin that agrees with the page, and every
// unpinned snippet (GVN), never reaches this code's effect.
var hostMatched=false;
if(pinnedTheme&&slug===HOST_FIX_SLUG){
  var hostTheme=paintedHostTheme(themeSource);
  if(hostTheme&&hostTheme!==pinnedTheme){pinnedTheme=hostTheme;hostMatched=true}
}
var theme=pinnedTheme||(autoTheme?resolveTheme(themeSource):"light");

var f=document.createElement("iframe");
// `&bg=transparent` ONLY in auto mode. It tells the widget to drop its own
// page-level background and let the host's show through, which is right only
// when the theme is known to MATCH that background - and a detected theme was
// derived from it, so it necessarily does. A pinned data-theme is a human's
// guess that can be wrong (Don's snippet says dark on a white Squarespace
// page), and there the widget must keep painting its own ground or its light
// text would land on white. So: detected => transparent, pinned => unchanged.
var BG=((autoTheme&&!pinnedTheme)||hostMatched)?"&bg=transparent":"";
// Read once; part of embedSrc so a theme flip (which rebuilds the src) keeps it.
var headline=readHeadline(mainRoot);
var HEADLINE_PARAM=headline?"&h="+encodeURIComponent(headline):"";
// Interface language (docs/2026-10-31_georgian-i18n): data-lang="ka" on this
// tag, or a host page whose own <html lang> is Georgian, loads the Georgian
// widget under /ka. Every other page (every current partner) keeps the
// unchanged /embed/auto address. An unknown data-lang value means English.
var WIDGET_LANGS={ka:1};
function widgetLang(){
  var v=(s.getAttribute("data-lang")||"").trim().toLowerCase();
  if(v)return WIDGET_LANGS[v]?v:"";
  var h=(document.documentElement.getAttribute("lang")||"").trim().toLowerCase().split("-")[0];
  return WIDGET_LANGS[h]?h:"";
}
var LANG_PREFIX=widgetLang()?"/"+widgetLang():"";
function embedSrc(t){
  return origin+LANG_PREFIX+"/embed/auto?partner="+encodeURIComponent(slug)+"&url="+encodeURIComponent(pageUrl)+"&theme="+t+BG+HEADLINE_PARAM;
}
f.src=embedSrc(theme);
// Loads with the page, never lazily (2026-11-18): with loading="lazy" Chrome
// started the frame only when the reader got near it, so on Every Movie Has a
// Lesson and MN Movie Man it showed 2 to 3.5 s AFTER the reader reached it.
f.loading="eager";f.title="Film Dilemma";f.setAttribute("scrolling","no");f.setAttribute("frameborder","0");
var initialMax=(widthSource&&measureTextWidth(widthSource))||DEFAULT_MAX;
f.style.cssText="display:block;width:100%;height:0;max-width:"+initialMax+"px;margin:0 auto;border:0;overflow:hidden;box-sizing:border-box;visibility:hidden";
// A frame with nothing to show (height 0: no match, a home page, still loading)
// is hidden from screen readers and the keyboard too, not only from the eye
// (2026-11-05: Geek Vibes Nation reported an accessibility issue from the empty
// frame on their home page). It turns visible the moment it gets a height.
var parked=false,pendingH=0;
// While parked, the frame is shown OVER its real spot (absolute, at body level,
// outside Wix's React tree) and the same space is reserved by a bottom margin
// on the anchor (React does not patch style attributes when it hydrates), so a
// reader sees the widget the moment it is ready, however late Wix hydrates.
// 2026-11-18: a reader check on Film Focus Online saw the post hydrate only at
// ~11 s in some phone loads; the frame then moved in at the 10 s cutoff, was
// thrown away by React and reloaded (visible at 12 s). The overlay ends that.
var ovAnchor=null,ovOrigMargin=null,ovTimer=0;
function overlayAnchor(){
  if(ovAnchor&&document.documentElement.contains(ovAnchor))return ovAnchor;
  ovAnchor=null;ovOrigMargin=null;
  try{var np=resolvePlacement();if(np.real&&np.after&&np.anchor){ovAnchor=np.anchor;}}catch(e){}
  return ovAnchor;
}
function placeOverlay(){
  if(!parked)return;
  var a=overlayAnchor();
  var h=pendingH||0;
  if(!a||!h){f.style.height="0";f.style.visibility="hidden";return}
  try{
    if(ovOrigMargin===null){ovOrigMargin=a.style.marginBottom||"";ovBaseMB=parseFloat(getComputedStyle(a).marginBottom)||0}
    a.style.marginBottom=(ovBaseMB+h)+"px";
    var par=a.parentElement||a,pr=par.getBoundingClientRect(),pcs=getComputedStyle(par);
    var cl=pr.left+(parseFloat(pcs.paddingLeft)||0)+(parseFloat(pcs.borderLeftWidth)||0);
    var cw=par.clientWidth-(parseFloat(pcs.paddingLeft)||0)-(parseFloat(pcs.paddingRight)||0);
    var mw=parseFloat(f.style.maxWidth)||cw,w=Math.min(cw,mw);
    if(f.style.position!=="absolute"){f.style.position="absolute";f.style.left="0px";f.style.top="0px";f.style.zIndex="1";f.style.margin="0"}
    var ar=a.getBoundingClientRect();
    // Origin of the frame's containing block, from where it sits now.
    var fr=f.getBoundingClientRect();
    var ox=fr.left-(parseFloat(f.style.left)||0),oy=fr.top-(parseFloat(f.style.top)||0);
    f.style.width=w+"px";
    f.style.left=(cl+(cw-w)/2-ox)+"px";
    f.style.top=(ar.bottom+ovBaseMB-oy)+"px";
    f.style.height=h+"px";f.style.visibility="visible";f.removeAttribute("aria-hidden");f.removeAttribute("tabindex");
  }catch(e){}
}
var ovBaseMB=0;
function endOverlay(){
  if(ovTimer){clearInterval(ovTimer);ovTimer=0}
  try{if(ovAnchor&&ovOrigMargin!==null){if(ovOrigMargin)ovAnchor.style.marginBottom=ovOrigMargin;else ovAnchor.style.removeProperty("margin-bottom")}}catch(e){}
  ovAnchor=null;ovOrigMargin=null;
  f.style.position="";f.style.zIndex="";f.style.left="";f.style.top="";f.style.width="100%";f.style.margin="0 auto";
}
function setFrameHeight(h){
  // Parked at the end of <body> (see parkForWix): shown as an overlay over its
  // real spot (placeOverlay), never under the site footer.
  if(parked){pendingH=h;placeOverlay();return}
  f.style.height=h+"px";
  if(h>0){f.style.visibility="visible";f.removeAttribute("aria-hidden");f.removeAttribute("tabindex")}
  else{f.style.visibility="hidden";f.setAttribute("aria-hidden","true");f.setAttribute("tabindex","-1")}
}
f.setAttribute("aria-hidden","true");f.setAttribute("tabindex","-1");
function insertFrame(a,aft){if(aft){a.insertAdjacentElement("afterend",f)}else{a.appendChild(f)}}
// Moves the parked frame into the post once Wix's React owns the post body (or
// after WIX_REACT_WAIT_MS). moveBefore keeps the loaded frame as it is; if it
// throws, a normal insert (one reload) is the fallback.
// It moves in only once the target is hydrated AND has not changed for
// WIX_QUIET_MS (Wix can re-render the post right after hydrating it). The
// overlay shows the widget meanwhile, so waiting costs the reader nothing; if
// the post never hydrates within WIX_UNPARK_MAX_MS the frame simply stays an
// overlay (moving it into a not-yet-hydrated post is what got it thrown away).
var WIX_QUIET_MS=1000,WIX_UNPARK_MAX_MS=30000;
function unparkForWix(){
  var parkStart=Date.now(),quietSince=0,obsTarget=null,mo=null;
  ovTimer=setInterval(placeOverlay,200);
  var iv=setInterval(function(){try{
    var t=wixReactTarget();
    if(!(t&&hasReactFiber(t))){
      if(Date.now()-parkStart>WIX_UNPARK_MAX_MS){clearInterval(iv);if(mo)mo.disconnect()}
      return;
    }
    if(t!==obsTarget){
      if(mo)mo.disconnect();
      obsTarget=t;quietSince=Date.now();
      try{mo=new MutationObserver(function(){quietSince=Date.now()});mo.observe(t,{childList:true,subtree:true})}catch(e){mo=null}
    }
    if(Date.now()-quietSince<WIX_QUIET_MS&&Date.now()-parkStart<WIX_UNPARK_MAX_MS)return;
    clearInterval(iv);
    if(mo)mo.disconnect();
    setTimeout(function(){
      endOverlay();
      try{
        var np=resolvePlacement();
        if(!np.anchor||!np.real)np={anchor:anchor,after:after,widthSource:widthSource,real:true};
        var par=np.after?np.anchor.parentNode:np.anchor,ref=np.after?np.anchor.nextSibling:null,moved=false;
        try{par.moveBefore(f,ref);moved=true}catch(e){}
        if(!moved)insertFrame(np.anchor,np.after);
        widthSource=np.widthSource;themeSource=widthSource||np.anchor;inRealSpot=np.real;
        applyWidth();bindWidth();
      }catch(e){}
      parked=false;
      if(pendingH)setFrameHeight(pendingH);
      if(autoTheme&&!pinnedTheme){try{applyTheme(detectTheme())}catch(e){}}
    },50);
  }catch(e){clearInterval(iv)}},50);
}
if(parkForWix){
  // Loads now even though the end of <body> is far below the reader.
  f.loading="eager";
  parked=true;
  document.body.appendChild(f);
  unparkForWix();
}else{
  insertFrame(anchor,after);
}
var inRealSpot=placement.real,askedCandidates=false,blockShut=false,pageCap=0;
// Auto mode: now that the frame is in the page, the ground behind it is the
// best evidence. A different answer replaces the src before the first load
// finishes (no visible flip); later, the frame coming into view re-checks it.
function detectTheme(){var g=groundThemeAt(f);return g||resolveTheme(themeSource)}
if(autoTheme&&!pinnedTheme){
  try{var g0=groundThemeAt(f);if(g0&&g0!==theme){theme=g0;f.src=embedSrc(theme)}}catch(e){}
  try{
    if(window.IntersectionObserver){
      var io=new IntersectionObserver(function(entries){
        for(var i=0;i<entries.length;i++){if(entries[i].isIntersecting){try{applyTheme(detectTheme())}catch(e){}}}
      });
      io.observe(f);
    }
  }catch(e){}
}

var widthObs=null,widthTimer=null,widthListener=false;
function applyWidth(){if(!widthSource)return;var w=measureTextWidth(widthSource);if(w&&pageCap&&w>pageCap)w=pageCap;if(w)f.style.maxWidth=w+"px"}
function bindWidth(){
  if(!widthSource)return;
  if(window.ResizeObserver){
    try{if(widthObs)widthObs.disconnect();widthObs=new ResizeObserver(applyWidth);widthObs.observe(widthSource)}catch(e){}
  }else if(!widthListener){
    widthListener=true;
    window.addEventListener("resize",function(){
      if(widthTimer)clearTimeout(widthTimer);
      widthTimer=setTimeout(applyWidth,150);
    });
  }
}
bindWidth();

// Client-rendered hosts (Wix hydrates its page 1-3 s after load, longer on a
// phone) discard DOM nodes they did not render, our iframe included: on Film
// Focus Online's Wix site the widget vanished whenever the snippet ran before
// that pass (2026-10-07). So if the iframe leaves the document within the first
// 30 s, it is put back, at a freshly resolved placement, but only at a REAL one
// (article / content container / explicit anchor), never the end-of-<body>
// fallback, which a half-built page would produce. At most 8 times; the
// observer only schedules a one-timer-at-a-time check, and never acts on a page
// whose iframe stays put (every other partner today).
var WATCH_MS=30000,MAX_REINSERTS=8,FALLBACK_AFTER_MS=4000,watchStart=Date.now(),watchTimer=null,watchPoll=null,watchObs=null,reinserts=0,detachedAt=0;
function stopWatching(){
  try{if(watchObs)watchObs.disconnect()}catch(e){}
  if(watchPoll){clearInterval(watchPoll);watchPoll=null}
}
function reattachIfDetached(){
  try{
    if(Date.now()-watchStart>WATCH_MS||reinserts>=MAX_REINSERTS){stopWatching();return}
    if(document.documentElement.contains(f)){detachedAt=0;return}
    if(!detachedAt)detachedAt=Date.now();
    var np=resolvePlacement();
    if(!np.anchor)return;
    // No real spot yet: the page may still be building, so wait. A frame
    // gone for FALLBACK_AFTER_MS with still no real spot goes where the first
    // placement would have put it (end of the page): Wix's mobile home
    // rebuilds its whole main container and the block was lost for good
    // (Outside the Spotlight, 2026-11-04).
    if(!np.real&&Date.now()-detachedAt<FALLBACK_AFTER_MS)return;
    reinserts++;
    widthSource=np.widthSource;themeSource=widthSource||np.anchor;
    applyWidth();
    insertFrame(np.anchor,np.after);
    inRealSpot=np.real;
    bindWidth();
  }catch(e){}
}
function keepAttached(){
  try{
    if(window.MutationObserver&&document.documentElement){
      watchObs=new window.MutationObserver(function(){
        if(watchTimer)return;
        watchTimer=setTimeout(function(){watchTimer=null;reattachIfDetached()},300);
      });
      watchObs.observe(document.documentElement,{childList:true,subtree:true});
    }
    // Safety net for a last mutation that fired while the page was still half
    // built: a once-a-second look until the window closes.
    watchPoll=setInterval(reattachIfDetached,1000);
  }catch(e){}
}
keepAttached();
// --- theme tracking (auto mode only) -------------------------------------
// A pinned data-theme is never re-evaluated, so nothing below is even wired up
// for Don's snippet or any other explicit one.
//
// A flip REPLACES the iframe src (the theme is a query param the embed reads on
// load), which reloads the frame. Two things keep that from shifting the host's
// layout, which this file promises never to do: the frame keeps its current
// pixel height across the swap (it is never reset to 0), and the transient
// `height: 0` that /embed/auto posts while it re-resolves is ignored until a
// real height arrives. `swapping` is time-bounded so a frame that never comes
// back cannot silently disable height updates forever.
var swapping=false,swapTimer=null;
function applyTheme(next){
  // Not while parked: the overlay floats over content that is still moving, so
  // the ground behind it is not the post's (2026-11-18, Film Focus Online phone:
  // light, dark, light = 3 frame loads). The move re-checks once (unparkForWix).
  if(parked)return;
  if(next===theme)return;
  theme=next;
  swapping=true;
  if(swapTimer)clearTimeout(swapTimer);
  swapTimer=setTimeout(function(){swapping=false},8000);
  try{f.src=embedSrc(next)}catch(e){}
}
function watchTheme(){
  try{
    var t=null;
    var recheck=function(){
      if(t)clearTimeout(t);
      // Debounced: a theme toggle typically rewrites a class on <html> AND
      // <body> AND restyles half the page, which is many mutation records for
      // one user action.
      t=setTimeout(function(){try{applyTheme(detectTheme())}catch(e){}},150);
    };
    if(window.MutationObserver){
      var obs=new window.MutationObserver(recheck);
      // Attributes only, and only the ones a theme switch plausibly touches -
      // observing subtree/childList on a whole document would fire on every ad,
      // lazy image and comment widget the host loads.
      var opts={attributes:true,attributeFilter:["class","style","data-theme","data-mode","data-color-mode","data-bs-theme","color-scheme"]};
      if(document.documentElement)obs.observe(document.documentElement,opts);
      if(document.body)obs.observe(document.body,opts);
    }
    // A host with no toggle of its own that simply follows the OS changes its
    // background through a media query, with no attribute mutation at all.
    if(window.matchMedia){
      var mq=window.matchMedia("(prefers-color-scheme: dark)");
      if(mq.addEventListener)mq.addEventListener("change",recheck);
      else if(mq.addListener)mq.addListener(recheck);
    }
  }catch(e){}
}
if(autoTheme&&!pinnedTheme)watchTheme();

window.addEventListener("message",function(e){try{
if(e.origin!==origin||!e.data||!e.data.fdEmbed||e.source!==f.contentWindow)return;
// The iframe found no mapped review and the partner allows the block on such
// pages: hand it the title candidates (short phrases, see readCandidates).
// The block shows only on article pages and only in a real spot inside the
// post. Elsewhere it sat under the footer over Outside the Spotlight's home
// video, or left an empty gap on his About page (owner, 2026-11-04).
// noSpot tells the frame to show nothing at all. A partner whose block may show
// on every page (anyPage, widget_options.empty_pages_scope "all": Every Movie
// Has a Lesson, 2026-11-05) still needs the real spot; pages that are not
// posts get no candidates, so they show the latest reviews.
// On such a page the spot is often the whole-width main box (Every Movie Has a
// Lesson's home), so the block is held to a 1000px column, centred.
if(e.data.needCandidates){askedCandidates=true;var art=isArticlePage(pageUrl);blockShut=!(inRealSpot&&(art||e.data.anyPage===true));if(!blockShut&&!art){pageCap=1000;applyWidth()}try{f.contentWindow.postMessage(blockShut?{fdEmbed:true,candidates:[],noSpot:true}:{fdEmbed:true,candidates:art?readCandidates(mainRoot):[]},origin)}catch(err){}return}
// A block that lost its real spot (Wix phones rebuild the page) stays shut.
if(askedCandidates&&(blockShut||!inRealSpot)){setFrameHeight(0);return}
var h=e.data.height||0;
if(!h&&swapping)return;
if(h)swapping=false;
setFrameHeight(h);
}catch(err){}});
}catch(err){}}
if(document.readyState==="loading"){document.addEventListener("DOMContentLoaded",run)}else{run()}
}catch(err){}})();

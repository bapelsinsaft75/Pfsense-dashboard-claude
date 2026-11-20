#!/usr/bin/env python3
"""Bevakar en avtalssida på Mina sidor. Om knappen "Avbryt uppsägning" dyker upp
trycker skriptet på den och bekräftar.

All konfiguration sker via miljövariabler (se .env.exempel).
  python bevaka.py              # bevaka i loop
  python bevaka.py --en-gang    # kontrollera en gång
  TORRKORNING=1                 # hitta knappen men klicka inte
"""
import argparse
import base64
import collections
import html
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout


def env(namn: str, standard: str = "") -> str:
    return os.environ.get(namn, standard).strip()


URL = env("AVTAL_URL")
ANVANDARE = env("K2A_ANVANDARNAMN")
LOSENORD = env("K2A_LOSENORD")
INTERVALL = int(env("INTERVALL_SEK", "300"))
TORR = env("TORRKORNING", "0").lower() in ("1", "true", "ja", "yes")
NTFY_URL = env("NTFY_URL")
AVBRYT_TEXT = env("AVBRYT_TEXT", r"avbryt\s+uppsägning")
BEKRAFTA_TEXT = env("BEKRAFTA_TEXT", r"^\s*(ja|bekräfta|ok)\s*$")
BEKRAFTA_FRAGA = env("BEKRAFTA_FRAGA", r"vill du ta bort.*uppsägning")
UPPSAGD_TEXT = env("UPPSAGD_TEXT", r"pågående\s+uppsägning")
START_URL = env("START_URL", "https://minasidor.k2a.se/mypages")
LISTA_URL = env("LISTA_URL", "https://minasidor.k2a.se/mypages/contract/")
VISA_TEXT = env("VISA_TEXT", r"^\s*visa\s*$")
AVTAL_MENY_TEXT = env("AVTAL_MENY_TEXT", r"^\s*mina\s+avtal\s*$")
AVTAL_NAMN = env("AVTAL_NAMN")  # texten under Mina avtal, t.ex. Klaffgatan 2
# Valfria CSS-selektorer om automatiken inte hittar inloggningsfälten
SEL_ANVANDARE = env("SEL_ANVANDARE")
SEL_LOSENORD = env("SEL_LOSENORD")
SEL_SKICKA = env("SEL_SKICKA")
WEBB_PORT = int(env("WEBB_PORT", "8080"))
WEBB_LOSENORD = env("WEBB_LOSENORD")
BILDER = Path(env("SKARMDUMP_MAPP", str(Path(__file__).resolve().parent / "skarmdumpar")))


LOGG = collections.deque(maxlen=300)
LAGE = {"status": "startar", "senast": None, "nasta": None, "antal": 0, "start": datetime.now()}
KONTROLLERA_NU = threading.Event()


def logg(msg: str) -> None:
    rad = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    LOGG.append(rad)
    print(rad, flush=True)


def notis(msg: str) -> None:
    logg(msg)
    if NTFY_URL:
        try:
            urllib.request.urlopen(urllib.request.Request(
                NTFY_URL, data=msg.encode("utf-8"), method="POST"), timeout=10)
        except Exception as e:  # en notis får aldrig stoppa bevakningen
            logg(f"Kunde inte skicka notis: {e}")


def skarmdump(sida, namn: str) -> None:
    try:
        BILDER.mkdir(parents=True, exist_ok=True)
        sida.screenshot(path=str(BILDER / f"{datetime.now():%Y%m%d-%H%M%S}-{namn}.png"), full_page=True)
    except Exception as e:
        logg(f"Skärmdump misslyckades: {e}")


def klickbar(omrade, monster: str):
    """Första synliga knapp/länk vars text matchar mönstret."""
    rx = re.compile(monster, re.I)
    kandidater = omrade.get_by_role("button", name=rx).or_(omrade.get_by_role("link", name=rx))
    for i in range(kandidater.count()):
        el = kandidater.nth(i)
        if el.is_visible():
            return el
    # Reserv: element som bara ser ut som knappar (t.ex. <div> med klickhändelse).
    text = omrade.get_by_text(rx)
    for i in range(text.count()):
        el = text.nth(i)
        if el.is_visible() and len(el.inner_text().strip()) < 40:
            return el
    return None


def synlig(loc):
    for i in range(loc.count()):
        if loc.nth(i).is_visible():
            return loc.nth(i)
    return None


def losenordsfalt(sida):
    return synlig(sida.locator(SEL_LOSENORD or "input[type=password]"))


TEXTFALT = "input:not([type]), input[type=text], input[type=email], input[type=tel]"
LOSENORDSFLIK_TEXT = env("LOSENORDSFLIK_TEXT", r"^(?!.*(glömt|nytt|återställ)).*(lösenord|användarnamn|e-?post)")
NASTA_TEXT = env("NASTA_TEXT", r"^\s*(nästa|fortsätt|gå vidare|logga in)\s*$")


COOKIE_TEXT = env("COOKIE_TEXT", r"^\s*(avvisa alla|neka alla|endast nödvändiga|tillåt alla|acceptera alla)\s*$")


def stang_cookies(sida) -> None:
    """Stänger cookie-rutan (den täcker annars inloggningsformuläret). Väljer helst 'Avvisa alla'."""
    for monster in (r"^\s*(avvisa alla|neka alla|endast nödvändiga)\s*$", COOKIE_TEXT):
        knapp = klickbar(sida, monster)
        if knapp is not None:
            try:
                logg(f"Stänger cookie-rutan ({knapp.inner_text().strip()!r})")
                knapp.click(timeout=5_000)
                sida.wait_for_timeout(1000)
            except Exception as e:
                logg(f"Kunde inte stänga cookie-rutan: {e}")
            return


def pa_inloggningssida(sida) -> bool:
    return bool(re.search(r"sign-?in|login|logga-?in", sida.url, re.I))


def skicka_formular(sida, falt) -> None:
    if SEL_SKICKA:
        knapp = synlig(sida.locator(SEL_SKICKA))
    else:
        knapp = synlig(falt.locator("xpath=ancestor::form[1]//*[@type='submit']")) or klickbar(sida, NASTA_TEXT)
    if knapp is not None:
        knapp.click()
    else:
        falt.press("Enter")
    try:
        sida.wait_for_load_state("networkidle", timeout=30_000)
    except PWTimeout:
        pass
    sida.wait_for_timeout(2500)


def logga_in(sida) -> bool:
    """Loggar in. Klarar BankID-flik först och tvåstegsinloggning. True om inloggning gjordes."""
    if losenordsfalt(sida) is None and not pa_inloggningssida(sida):
        return False
    if not (ANVANDARE and LOSENORD):
        raise RuntimeError("Inloggning krävs men K2A_ANVANDARNAMN/K2A_LOSENORD saknas")
    logg(f"Loggar in på {sida.url} …")
    stang_cookies(sida)

    # 1. Om bara BankID syns: klicka på fliken/länken för lösenordsinloggning.
    if losenordsfalt(sida) is None and synlig(sida.locator(SEL_ANVANDARE or TEXTFALT)) is None:
        flik = klickbar(sida, LOSENORDSFLIK_TEXT)
        if flik is not None:
            logg(f"Klickar på {flik.inner_text().strip()!r} för att visa inloggningsfälten")
            flik.click()
            sida.wait_for_timeout(2000)

    pw = losenordsfalt(sida)
    anv = synlig(sida.locator(SEL_ANVANDARE)) if SEL_ANVANDARE else None
    if anv is None:
        if pw is not None:
            anv = synlig(pw.locator(f"xpath=preceding::input[not(@type) or @type='text' "
                                    f"or @type='email' or @type='tel'][1]"))
        if anv is None:
            anv = synlig(sida.locator(TEXTFALT))
    if anv is None and pw is None:
        skarmdump(sida, "inloggningssida")
        raise RuntimeError("Hittade inga inloggningsfält på inloggningssidan – se skärmdumpen")

    if anv is not None:
        anv.fill(ANVANDARE)
    if pw is None:
        # 2. Tvåstegsinloggning: användarnamn först, sedan lösenord.
        logg("Skickar användarnamn, väntar på lösenordsfältet")
        skicka_formular(sida, anv)
        try:
            sida.locator(SEL_LOSENORD or "input[type=password]").first.wait_for(state="visible", timeout=15_000)
        except PWTimeout:
            skarmdump(sida, "inget-losenordsfalt")
            raise RuntimeError("Lösenordsfältet dök inte upp – se skärmdumpen")
        pw = losenordsfalt(sida)
    pw.fill(LOSENORD)
    skicka_formular(sida, pw)

    if losenordsfalt(sida) is not None:
        skarmdump(sida, "inloggning-misslyckades")
        raise RuntimeError("Inloggningen verkar ha misslyckats (lösenordsfältet syns fortfarande)")
    logg(f"Inloggad, står på {sida.url}")
    return True


AVTAL_MONSTER = re.compile(rf"{AVBRYT_TEXT}|{UPPSAGD_TEXT}|uppsägningstid|ladda ner avtalet", re.I)
SENAST_FUNNEN = {"url": None}


def vanta_pa(sida, timeout_ms: int = 30_000, extra: str = "") -> str:
    """Väntar tills inloggningsformulär, avtalsinnehåll eller (extra) syns."""
    pw = sida.locator(SEL_LOSENORD or "input[type=password]")
    avtal = sida.get_by_text(AVTAL_MONSTER)
    ovrigt = sida.get_by_text(re.compile(extra, re.I)) if extra else None
    slut = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < slut:
        if synlig(pw) is not None:
            return "inloggning"
        if pa_inloggningssida(sida) and time.monotonic() > slut - timeout_ms / 1000 + 5:
            return "inloggning"
        if synlig(avtal) is not None:
            sida.wait_for_timeout(1500)  # låt knapparna ritas klart
            return "avtal"
        if ovrigt is not None and synlig(ovrigt) is not None:
            sida.wait_for_timeout(1000)
            return "extra"
        sida.wait_for_timeout(500)
    return "okant"


def ga_till(sida, url: str, extra: str = "") -> str:
    sida.goto(url, wait_until="domcontentloaded", timeout=60_000)
    try:
        sida.wait_for_load_state("networkidle", timeout=20_000)
    except PWTimeout:
        pass
    lage = vanta_pa(sida, extra=extra)
    stang_cookies(sida)
    if lage == "inloggning" or (lage == "okant" and pa_inloggningssida(sida)):
        logga_in(sida)
        sida.goto(url, wait_until="domcontentloaded", timeout=60_000)
        lage = vanta_pa(sida, extra=extra)
        if lage == "inloggning":
            raise RuntimeError("Fortfarande på inloggningssidan efter inloggning")
    return lage


def prova_klicka_avtal(sida) -> bool:
    """Klickar på avtalet i menyn (t.ex. 'Klaffgatan 2'). True om avtalssidan visas."""
    if AVTAL_NAMN:
        kandidater = sida.get_by_text(re.compile(re.escape(AVTAL_NAMN), re.I))
    else:
        kandidater = sida.locator("a[href*='/contract/']")
    for i in range(kandidater.count()):
        el = kandidater.nth(i)
        if not el.is_visible():
            continue
        try:
            el.click(timeout=5_000)
        except Exception:
            continue
        if vanta_pa(sida, 15_000) == "avtal":
            return True
    return False


def prova_avtalslistan(sida) -> bool:
    """Går till avtalslistan och klickar på Visa (vid flera avtal: det som innehåller AVTAL_NAMN)."""
    logg(f"Öppnar avtalslistan {LISTA_URL} …")
    if ga_till(sida, LISTA_URL, extra=VISA_TEXT) == "avtal":
        return True
    rx = re.compile(VISA_TEXT, re.I)
    antal = sida.get_by_role("button", name=rx).or_(sida.get_by_role("link", name=rx)).count()
    if antal == 0:
        antal = sida.get_by_text(rx).count()
    logg(f"Hittade {antal} Visa-knapp(ar)")
    for i in range(antal):
        if i > 0:
            ga_till(sida, LISTA_URL, extra=VISA_TEXT)
        knappar = sida.get_by_role("button", name=rx).or_(sida.get_by_role("link", name=rx))
        if knappar.count() == 0:
            knappar = sida.get_by_text(rx)
        if i >= knappar.count():
            break
        try:
            knappar.nth(i).click(timeout=5_000)
        except Exception as e:
            logg(f"Kunde inte klicka på Visa: {e}")
            continue
        if vanta_pa(sida, 20_000) == "avtal":
            if not AVTAL_NAMN or AVTAL_NAMN.lower() in sida.inner_text("body").lower():
                return True
            logg(f"Avtal {i + 1} är inte {AVTAL_NAMN!r}, provar nästa")
    return False


def oppna_avtal(sida) -> None:
    for url in dict.fromkeys(u for u in (SENAST_FUNNEN["url"], URL) if u):
        if ga_till(sida, url) == "avtal":
            return
        logg(f"Avtalet syntes inte på {url} (hamnade på {sida.url})")

    # Via avtalslistan: /mypages/contract/ -> Visa
    if LISTA_URL and prova_avtalslistan(sida):
        SENAST_FUNNEN["url"] = sida.url
        logg(f"Avtalet hittat via listan: {sida.url}")
        return

    # Reserv via menyn: Mina avtal -> <AVTAL_NAMN>
    logg(f"Letar upp avtalet via menyn på {START_URL} …")
    ga_till(sida, START_URL, extra=AVTAL_MENY_TEXT)
    for forsok in range(3):
        if prova_klicka_avtal(sida):
            SENAST_FUNNEN["url"] = sida.url
            logg(f"Avtalet hittat: {sida.url}")
            return
        meny = klickbar(sida, AVTAL_MENY_TEXT)
        if meny is None:
            break
        logg("Klickar på 'Mina avtal'")
        meny.click()
        sida.wait_for_timeout(1500)
    skarmdump(sida, "hittar-inte-avtalet")
    raise RuntimeError(f"Hittade inte avtalssidan (står på {sida.url})")


def spara_senaste(sida) -> None:
    """Sparar senaste vyn så att den syns på statussidan (för felsökning)."""
    try:
        BILDER.mkdir(parents=True, exist_ok=True)
        sida.screenshot(path=str(BILDER / "senaste.png"), full_page=True)
        (BILDER / "senaste.html").write_text(sida.content(), encoding="utf-8")
    except Exception as e:
        logg(f"Kunde inte spara senaste vyn: {e}")


def kontrollera(sida) -> str:
    oppna_avtal(sida)
    spara_senaste(sida)
    logg(f"På sidan: {sida.url}  titel: {sida.title()!r}")

    knapp = klickbar(sida, AVBRYT_TEXT)
    if knapp is None:
        if sida.get_by_text(re.compile(UPPSAGD_TEXT, re.I)).count():
            skarmdump(sida, "text-men-ingen-knapp")
            return "text-men-ingen-knapp"
        return "ingen-knapp"

    if TORR:
        skarmdump(sida, "torr-hittad")
        return "hittad-torr"

    skarmdump(sida, "fore-klick")
    knapp.click()
    logg("Klickade 'Avbryt uppsägning', väntar på bekräftelse …")

    # Bekräftelsen: "Vill du ta bort den här uppsägningen?"  [Nej] [Ja]
    try:
        sida.get_by_text(re.compile(BEKRAFTA_FRAGA, re.I)).first.wait_for(state="visible", timeout=10_000)
    except PWTimeout:
        logg("Bekräftelsefrågan syntes inte (kan ha varit en webbläsardialog)")
    sida.wait_for_timeout(800)
    dialog = sida.get_by_role("dialog")
    omrade = dialog.first if synlig(dialog) is not None else sida
    bekrafta = klickbar(omrade, BEKRAFTA_TEXT) or klickbar(sida, BEKRAFTA_TEXT)
    if bekrafta is not None:
        logg(f"Bekräftar med knappen {bekrafta.inner_text().strip()!r}")
        skarmdump(sida, "bekraftelse")
        bekrafta.click()
        try:
            sida.wait_for_load_state("networkidle", timeout=20_000)
        except PWTimeout:
            pass
    else:
        skarmdump(sida, "ingen-ja-knapp")
        logg("Hittade ingen Ja-knapp")
    sida.wait_for_timeout(2000)
    skarmdump(sida, "efter-bekraftelse")

    oppna_avtal(sida)
    if klickbar(sida, AVBRYT_TEXT) is None:
        return "klar"
    return "oklart"


STATUSTEXT = {
    "startar": ("Startar", "gra"),
    "ingen-knapp": ("Ingen uppsägning att avbryta", "gron"),
    "hittad-torr": ("Knappen finns – torrkörning, klickade inte", "gul"),
    "klar": ("Uppsägningen avbröts", "gron"),
    "oklart": ("Klickade, men knappen finns kvar", "rod"),
    "fel": ("Fel vid senaste kontroll", "rod"),
    "text-men-ingen-knapp": ("Uppsägning pågår men knappen hittades inte", "rod"),
}

SIDA = """<!doctype html><html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30"><title>K2A-bevakare</title><style>
:root{--bg:#f6f7f9;--kort:#fff;--text:#1d2330;--dov:#667085;--kant:#e3e6eb;
--gron:#1a7f37;--gul:#9a6700;--rod:#cf222e;--gra:#667085}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--kort:#181b21;--text:#e6e8eb;
--dov:#9aa1ad;--kant:#2a2f37;--gron:#3fb950;--gul:#d29922;--rod:#f85149}}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,sans-serif}
main{max-width:860px;margin:0 auto;padding:24px 16px}
h1{font-size:20px;margin:0 0 16px}.kort{background:var(--kort);border:1px solid var(--kant);
border-radius:10px;padding:16px;margin-bottom:16px}
.status{font-size:22px;font-weight:600}.gron{color:var(--gron)}.gul{color:var(--gul)}
.rod{color:var(--rod)}.gra{color:var(--gra)}
dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 16px;margin:12px 0 0}
dt{color:var(--dov)}dd{margin:0}
pre{margin:0;max-height:360px;overflow:auto;font:12px/1.5 ui-monospace,monospace;white-space:pre-wrap}
button{font:inherit;padding:8px 14px;border-radius:8px;border:1px solid var(--kant);
background:var(--text);color:var(--bg);cursor:pointer}
ul{margin:0;padding-left:18px}a{color:inherit}.tag{font-size:12px;padding:2px 8px;
border-radius:99px;border:1px solid currentColor;margin-left:8px;vertical-align:middle}
</style></head><body><main>
<h1>K2A-bevakare{torr}</h1>
<div class="kort"><div class="status {klass}">{statustext}</div><dl>
<dt>Senaste kontroll</dt><dd>{senast}</dd><dt>Nästa kontroll</dt><dd>{nasta}</dd>
<dt>Antal kontroller</dt><dd>{antal}</dd><dt>Intervall</dt><dd>{intervall} s</dd>
<dt>Igång sedan</dt><dd>{start}</dd></dl>
<form method="post" action="/kontrollera" style="margin-top:14px">
<button>Kontrollera nu</button></form></div>
<div class="kort"><strong>Senaste vy</strong> – <a href="/bild/senaste.png">skärmdump</a> · <a href="/senaste.html">HTML</a>
<a href="/bild/senaste.png"><img src="/bild/senaste.png" alt="" style="display:block;width:100%;margin-top:10px;border:1px solid var(--kant);border-radius:6px"></a></div>
<div class="kort"><strong>Skärmdumpar</strong><ul>{bilder}</ul></div>
<div class="kort"><strong>Logg</strong><pre>{logg}</pre></div>
</main></body></html>"""


def tid(t):
    return t.strftime("%Y-%m-%d %H:%M:%S") if t else "–"


class Webb(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def behorig(self) -> bool:
        if not WEBB_LOSENORD:
            return True
        h = self.headers.get("Authorization", "")
        try:
            ok = h.startswith("Basic ") and base64.b64decode(h[6:]).decode().split(":", 1)[1] == WEBB_LOSENORD
        except Exception:
            ok = False
        if not ok:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="K2A-bevakare"')
            self.end_headers()
        return ok

    def svara(self, kod, typ, data: bytes):
        self.send_response(kod)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if not self.behorig():
            return
        if self.path == "/kontrollera":
            KONTROLLERA_NU.set()
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def do_GET(self):
        if not self.behorig():
            return
        if self.path == "/api/status":
            d = {k: (tid(v) if isinstance(v, datetime) else v) for k, v in LAGE.items()}
            return self.svara(200, "application/json", json.dumps(d, ensure_ascii=False).encode())
        if self.path.startswith("/bild/"):
            namn = Path(self.path[6:]).name
            fil = BILDER / namn
            if namn.endswith(".png") and fil.is_file():
                return self.svara(200, "image/png", fil.read_bytes())
            return self.svara(404, "text/plain", b"saknas")
        if self.path == "/senaste.html":
            fil = BILDER / "senaste.html"
            if fil.is_file():
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Disposition", 'attachment; filename="senaste.html"')
                self.end_headers()
                self.wfile.write(fil.read_bytes())
                return
            return self.svara(404, "text/plain", b"saknas")
        if self.path != "/":
            return self.svara(404, "text/plain", b"saknas")

        text, klass = STATUSTEXT.get(LAGE["status"], (LAGE["status"], "gra"))
        bilder = sorted((b for b in BILDER.glob("*.png") if b.name != "senaste.png"), reverse=True)[:15] if BILDER.exists() else []
        sida = SIDA
        for k, v in {
            "{torr}": '<span class="tag gul">torrkörning</span>' if TORR else "",
            "{klass}": klass, "{statustext}": html.escape(text),
            "{senast}": tid(LAGE["senast"]), "{nasta}": tid(LAGE["nasta"]),
            "{antal}": str(LAGE["antal"]), "{intervall}": str(INTERVALL), "{start}": tid(LAGE["start"]),
            "{bilder}": "".join(f'<li><a href="/bild/{html.escape(b.name)}">{html.escape(b.name)}</a></li>'
                                for b in bilder) or "<li>Inga ännu</li>",
            "{logg}": html.escape("\n".join(reversed(LOGG))),
        }.items():
            sida = sida.replace(k, v)
        self.svara(200, "text/html; charset=utf-8", sida.encode())


def starta_webb() -> None:
    srv = ThreadingHTTPServer(("0.0.0.0", WEBB_PORT), Webb)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    logg(f"Webbgränssnitt på port {WEBB_PORT}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--en-gang", action="store_true", help="kontrollera en gång och avsluta")
    ap.add_argument("--visa", action="store_true", help="visa webbläsarfönstret (ej i Docker)")
    a = ap.parse_args()

    if not URL and not AVTAL_NAMN:
        print("Ange AVTAL_URL eller AVTAL_NAMN", file=sys.stderr)
        return 2
    if not a.en_gang:
        starta_webb()
    logg(f"Startar bevakning var {INTERVALL}:e sekund{' (TORRKÖRNING)' if TORR else ''}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not a.visa, executable_path=env("CHROMIUM_SOKVAG") or None)
        ctx = browser.new_context(locale="sv-SE")  # cookies ligger kvar mellan varven
        sida = ctx.new_page()
        sida.on("dialog", lambda d: (logg(f"Webbläsardialog: {d.message!r} – accepterar"), d.accept()))

        senaste = None
        fel_i_rad = 0
        while True:
            try:
                status = kontrollera(sida)
                fel_i_rad = 0
            except Exception as e:
                status = "fel"
                fel_i_rad += 1
                logg(f"Fel: {e}")
                skarmdump(sida, "fel")
                if fel_i_rad == 3:
                    notis(f"Bevakaren har fått fel 3 gånger i rad: {e}")

            LAGE.update(status=status, senast=datetime.now(), antal=LAGE["antal"] + 1,
                        nasta=datetime.now() + timedelta(seconds=INTERVALL))
            logg(f"Status: {status}")
            if status != senaste:
                if status == "hittad-torr":
                    notis("TORRKÖRNING: knappen 'Avbryt uppsägning' finns – klickade inte.")
                elif status == "klar":
                    notis("Uppsägningen är avbruten – knappen är borta.")
                elif status == "text-men-ingen-knapp":
                    notis("Sidan säger att en uppsägning pågår men knappen hittades inte. Kolla skärmdumparna!")
                elif status == "oklart":
                    notis("Tryckte på 'Avbryt uppsägning' men knappen finns kvar. Kolla skärmdumparna!")
            senaste = status

            if a.en_gang:
                break
            if KONTROLLERA_NU.wait(INTERVALL):
                logg("Manuell kontroll begärd")
            KONTROLLERA_NU.clear()
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

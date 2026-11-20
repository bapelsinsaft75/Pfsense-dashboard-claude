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
BEKRAFTA_TEXT = env("BEKRAFTA_TEXT", r"^\s*(bekräfta|ja|ok|avbryt uppsägning)\b")
UPPSAGD_TEXT = env("UPPSAGD_TEXT", r"pågående\s+uppsägning")
LOGGA_IN_TEXT = env("LOGGA_IN_TEXT", r"^\s*logga\s*in\s*$")
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


def logga_in(sida) -> bool:
    """Fyller i inloggningsformuläret om det visas. True om inloggning gjordes."""
    pw = losenordsfalt(sida)
    if pw is None:
        return False
    if not (ANVANDARE and LOSENORD):
        raise RuntimeError("Inloggning krävs men K2A_ANVANDARNAMN/K2A_LOSENORD saknas")

    if SEL_ANVANDARE:
        anv = synlig(sida.locator(SEL_ANVANDARE))
    else:
        # Sista synliga text-/e-postfältet före lösenordsfältet i samma formulär
        anv = synlig(pw.locator(
            "xpath=preceding::input[not(@type) or @type='text' or @type='email' or @type='tel'][1]"))
    if anv is None:
        raise RuntimeError("Hittade inget användarnamnsfält – sätt SEL_ANVANDARE")

    logg("Loggar in …")
    anv.fill(ANVANDARE)
    pw.fill(LOSENORD)
    knapp = synlig(sida.locator(SEL_SKICKA)) if SEL_SKICKA else (
        klickbar(sida, LOGGA_IN_TEXT) or synlig(sida.locator("button[type=submit], input[type=submit]")))
    if knapp is not None:
        knapp.click()
    else:
        pw.press("Enter")
    sida.wait_for_load_state("networkidle", timeout=60_000)
    sida.wait_for_timeout(1500)

    if losenordsfalt(sida) is not None:
        skarmdump(sida, "inloggning-misslyckades")
        raise RuntimeError("Inloggningen verkar ha misslyckats (lösenordsfältet syns fortfarande)")
    return True


def oppna_avtal(sida) -> None:
    sida.goto(URL, wait_until="networkidle", timeout=60_000)
    if logga_in(sida):
        # Efter inloggning hamnar man inte alltid på avtalssidan – gå dit igen.
        sida.goto(URL, wait_until="networkidle", timeout=60_000)
        if losenordsfalt(sida) is not None:
            raise RuntimeError("Fortfarande på inloggningssidan efter inloggning")
    # Vänta tills avtalsinnehållet har ritats upp av JavaScript (max 30 s).
    try:
        sida.get_by_text(re.compile(rf"{AVBRYT_TEXT}|{UPPSAGD_TEXT}|uppsägningstid|avtal fr\.?o\.?m", re.I)) \
            .first.wait_for(state="visible", timeout=30_000)
    except PWTimeout:
        logg(f"Avtalsinnehållet syntes inte inom 30 s (adress nu: {sida.url})")
    sida.wait_for_timeout(2000)


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

    # Bekräftelsen kan vara en webbläsardialog (hanteras automatiskt) eller en modal.
    try:
        sida.get_by_role("dialog").first.wait_for(state="visible", timeout=8_000)
        omrade = sida.get_by_role("dialog").first
    except PWTimeout:
        omrade = sida
    sida.wait_for_timeout(1000)

    bekrafta = klickbar(omrade, BEKRAFTA_TEXT)
    if bekrafta is not None:
        logg(f"Bekräftar med knappen {bekrafta.inner_text().strip()!r}")
        bekrafta.click()
        sida.wait_for_load_state("networkidle")
    else:
        logg("Ingen bekräfta-knapp syntes (kan ha varit en webbläsardialog)")
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

    if not URL:
        print("AVTAL_URL saknas", file=sys.stderr)
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

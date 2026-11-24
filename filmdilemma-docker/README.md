# Film Dilemma – spegling i Docker

Kopia av frontenden på https://filmdilemma.com (hämtad 2026-11-24), med ägarens tillstånd.
Allt i `site/` serveras av nginx i en container på **192.168.2.50**.

## Vad som ingår och inte

| Del | Status |
|---|---|
| HTML, 191 JS-filer, 115 CSS-filer, typsnitt, ikoner, manifest, sitemap | Kopierat (343 filer, 12 MB) |
| SPA-routing (`/movies`, `/lists/...`, `/ka/...` m.m.) | Fungerar, alla sökvägar går till `index.html` |
| Filmer, listor, sök, konton (49 000+ filmer) | **Kopieras inte.** Hämtas live från `https://api.filmdilemma.com` |
| Filmaffischer | Hämtas live från `image.tmdb.org` |
| Cloudflare-skript (`/cdn-cgi/...`, `/__fd/pass`) | Borttaget eller svarar 404 eftersom det bara fungerar bakom Cloudflare |

För en kopia som klarar sig helt utan originalet behövs ägarens API-källkod och en dump av databasen.
Kör ni det API:t själva pekar ni ut det med `API_UPSTREAM` (se nedan).

## Starta

```bash
cp .env.example .env      # justera PARENT_IF (nätverkskortet på värden) och GATEWAY
docker compose up -d --build
```

Öppna sedan http://192.168.2.50

Containern kopplas till värdens **befintliga macvlan-nätverk** (standard `br0`) och får 192.168.2.50.
- Kolla nätverkets namn med `docker network ls` och sätt `DOCKER_NETWORK` i `.env` om det inte heter `br0`.
- 192.168.2.50 får inte användas av något annat, och ska helst ligga utanför DHCP-poolen
  (eller reserveras i pfSense).
- Docker-värden själv kan inte nå containern via macvlan. Testa från en annan dator.

Vill du hellre köra containern bakom värdens IP: byt `networks:`-blocket mot `ports: ["80:80"]`.

## API-lägen

1. **Standard (direkt):** webbläsaren anropar `https://api.filmdilemma.com`.
   Kräver att API:t tillåter CORS från `http://192.168.2.50`. Annars visas
   "Having trouble connecting". Be ägaren lägga till originet i API:ts CORS-lista.
2. **Via proxy:** sätt `API_URL: ""` i `docker-compose.yml` och `API_UPSTREAM` till ett API
   (t.ex. ett eget på `http://192.168.2.51:8000`). Appen anropar då `/api/...` på
   192.168.2.50 och nginx skickar vidare.

Byt läge genom att bygga om containern: `docker compose up -d --force-recreate`.

## Uppdatera kopian

Originalet byggs om ibland (nya filnamn med hash). Hämta då om innehållet i `site/`.

## Borttaget jämfört med originalet

Google Analytics, Microsoft Clarity och Cloudflare Web Analytics är borttagna ur `index.html`,
så ingen besöksdata skickas till ägarens konton. Appens egna anrop är tysta när `window.gtag` saknas.

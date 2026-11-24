# Film Dilemma – fristående kopia med Plex

Kopia av Film Dilemmas gränssnitt (med ägarens tillstånd) som körs helt lokalt i en container.
Inget anrop går till filmdilemma.com. Filmdata kommer från **TMDB** och inloggning sker med **Plex**.

## Vad som finns

| Del | Hur det fungerar här |
|---|---|
| Gränssnittet | Originalets frontend, oförändrad förutom adresser (`web/`) |
| Filmer, personer, sök, filter | Eget API (`app/`) med data importerad från TMDB till SQLite |
| Listor | Skapas automatiskt: "Best movies about …" (TMDB-nyckelord), filmserier, bästa per genre och årtionde |
| Inloggning | **Endast Plex.** Bara du och de du delat Plex-servern med kommer in. Du blir admin. |
| Finns i Plex | Filmsidan visar **▶ Spela på Plex** om filmen finns i ditt bibliotek |
| Sett-historik | Filmer som setts i Plex markeras som sedda (per användare) och kan filtreras bort |
| Önskningar | **Önska film** på filmer som saknas → Overseerr/Jellyseerr eller Radarr, annars en lista på `/onskningar` |
| Betyg, bevakningslista, sedda | Sparas lokalt per användare |
| Spel, chatt, recensioner från partners, "Famous fans" m.m. | Finns inte (tomma sektioner) |

Bilder (affischer, porträtt) laddas direkt från `image.tmdb.org`.

## Du behöver

1. **TMDB-token** – skapa ett gratis konto på themoviedb.org → *Settings → API* → kopiera
   **API Read Access Token** (den långa som börjar med `eyJ`).
2. **Plex-token** för ditt admin-konto – se Plex guide
   [Finding an authentication token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)
   (öppna valfri film i Plex Web → *⋯ → Get Info → View XML*, värdet efter `X-Plex-Token=` i adressen).
3. **Plex-serverns adress** sett från containern, t.ex. `http://192.168.2.10:32400`.
4. Valfritt: Overseerr/Jellyseerr (URL + API-nyckel) eller Radarr (URL + API-nyckel) för önskningar.

## Installera på Unraid

Kör i Unraids terminal (`>_` uppe till höger).

**1. Hämta koden**

```bash
mkdir -p /mnt/user/appdata && cd /mnt/user/appdata
git clone -b claude/cool-ramanujan-mp5fff https://github.com/bapelsinsaft75/Pfsense-dashboard-claude.git filmdilemma-src
```

Finns mappen redan: `cd /mnt/user/appdata/filmdilemma-src && git pull`

**2. Fyll i inställningar**

```bash
cd /mnt/user/appdata/filmdilemma-src/filmdilemma-docker
cp .env.example .env
nano .env
```

Fyll minst i `TMDB_TOKEN`, `PLEX_URL` och `PLEX_TOKEN`. Spara med Ctrl+O, Enter, Ctrl+X.

**3. Bygg och starta**

```bash
docker build -t filmdilemma-local .
docker rm -f filmdilemma 2>/dev/null
docker run -d --name filmdilemma --restart unless-stopped \
  --network br0 --ip 192.168.2.50 \
  --env-file .env \
  -v /mnt/user/appdata/filmdilemma:/data \
  filmdilemma-local
```

**4. Följ första importen**

```bash
docker logs -f filmdilemma
```

Första starten importerar filmer från TMDB (`MAX_MOVIES`, standard 20 000 ≈ 15 min).
Sidan går att använda under tiden och fylls på efterhand. Avsluta loggvisningen med Ctrl+C.

Öppna sedan **http://192.168.2.50** från en annan dator eller mobilen och tryck **Sign in** → **Logga in med Plex**.
Unraid-servern själv når inte adresser på `br0` om inte *Settings → Docker → Host access to custom networks* är på.

### Alternativ: docker compose

```bash
docker compose up -d --build
```

Compose-filen kopplar in containern på det befintliga nätverket `br0` (`DOCKER_NETWORK` i `.env`) och
sparar data i `/mnt/user/appdata/filmdilemma`.

## Underhåll

| Vad | Kommando |
|---|---|
| Uppdatera koden | `cd /mnt/user/appdata/filmdilemma-src && git pull`, sedan steg 3 igen |
| Importera fler/nyare filmer | `docker exec filmdilemma python -m app.tmdb_import --max 49000` |
| Uppdatera befintliga filmer | `docker exec filmdilemma python -m app.tmdb_import --refresh` |
| Synka Plex direkt | Knappen **Synka Plex nu** på `/onskningar` (admin), annars var `PLEX_SYNC_MINUTES` |
| Börja om från noll | Stoppa containern och ta bort `/mnt/user/appdata/filmdilemma` |

All data (filmer, användare, betyg, önskningar) ligger i `/mnt/user/appdata/filmdilemma/filmdilemma.db`.

## Inställningar (`.env`)

| Variabel | Standard | Beskrivning |
|---|---|---|
| `PUBLIC_URL` | `http://192.168.2.50` | Adressen du surfar till |
| `TMDB_TOKEN` | – | TMDB API Read Access Token (krävs) |
| `MAX_MOVIES` | `20000` | Antal filmer vid första importen |
| `WATCH_REGION` | `SE` | Land för streamingtjänster |
| `PLEX_URL` / `PLEX_TOKEN` | – | Din Plex-server och admin-token (krävs för inloggning) |
| `PLEX_SYNC_MINUTES` | `30` | Hur ofta bibliotek och sett-historik synkas |
| `REQUIRE_LOGIN` | `false` | `true` = hela sidan kräver Plex-inloggning |
| `OVERSEERR_URL` / `OVERSEERR_API_KEY` | – | Skicka önskningar till Overseerr eller Jellyseerr |
| `RADARR_URL` / `RADARR_API_KEY` | – | ... eller direkt till Radarr |
| `RADARR_ROOT_FOLDER` / `RADARR_QUALITY_PROFILE` | första i Radarr | Var och i vilken kvalitet Radarr laddar ner |

## Struktur

```
filmdilemma-docker/
├── Dockerfile            en container: Python (Starlette) serverar frontend + API på port 80
├── docker-compose.yml
├── .env.example
├── app/
│   ├── main.py           API och webbserver
│   ├── search.py         filtrering/sortering av filmer
│   ├── serializers.py    JSON-format som frontenden förväntar sig
│   ├── tmdb_import.py    import från TMDB
│   ├── plex.py           Plex-inloggning, synk, önskningar
│   ├── auth.py           tokens
│   ├── db.py             SQLite-schema
│   └── local/            Plex-inloggningssidan, önskningssidan och tillägget på filmsidorna
└── web/                  originalets byggda frontend
```

Analysskript (Google Analytics, Clarity, Cloudflare) är borttagna.

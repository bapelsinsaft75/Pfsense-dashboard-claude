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
| Dilemma solver | Föreslår filmer och serier utifrån känsla, era och språk. Välj **Mina filmer i Plex** eller **Plex + TMDB**. Finns förslaget i Plex visas **Watch on Plex**, annars **Request movie/show** |
| Plex-koppling | Fungerar med både nya och äldre Plex-agenter (TMDB-, IMDb- och TVDB-id). Filmer och serier i Plex som saknas i databasen hämtas automatiskt från TMDB |
| Plex-menyn | **Plex** i menyn listar dina bibliotek (filmer och serier). Klick på en titel öppnar en informationsruta med handling, skådespelare, säsonger, sett-status och betyg från TMDB och IMDb |
| Request via Plex-watchlist | Standard (`REQUEST_MODE=watchlist`): **Request movie/show** lägger titeln på den inloggades **egen Plex-watchlist**. Seerr:s *Plex Watchlist Sync* önskar den sedan i användarens namn. Kräver att användarna har behörigheten *Auto-Request* i Seerr |
| Seerr | Med `REQUEST_MODE=seerr` skickas önskningar direkt till Seerr **i den inloggades namn** (matchas på Plex-id, e-post eller Plex-användarnamn). Finns personen inte i Seerr skickas ingen önskan. Status läses tillbaka: väntar på godkännande, godkänd, nekad, tillgänglig |
| Requests | Under användarmenyn (**Profile → Requests**). Visar varje önskan steg för steg: skickad till Seerr → godkänd/nekad → laddas ner (med förlopp) → tillgänglig i Seerr → uppe i Plex. Användare ser sina egna, admin ser allas |
| Önskningar | **Önska film** på filmer som saknas → Overseerr/Jellyseerr eller Radarr, annars en lista på `/onskningar` |
| Betyg, bevakningslista, sedda | Sparas lokalt per användare |
| Allt som inte rör TMDB eller Plex | Borttaget: Coffee, Discover, Famous Fans, spel, recensioner, kommentarer, shop, affiliate-länkar, cookie-ruta och sidfotens länkar (TMDB-erkännandet är kvar, det kräver TMDB) |

Bilder (affischer, porträtt) laddas direkt från `image.tmdb.org`.

## Du behöver

1. **TMDB-token** – skapa ett gratis konto på themoviedb.org → *Settings → API* → kopiera
   **API Read Access Token** (den långa som börjar med `eyJ`).
2. **Plex-token** för ditt admin-konto – se Plex guide
   [Finding an authentication token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)
   (öppna valfri film i Plex Web → *⋯ → Get Info → View XML*, värdet efter `X-Plex-Token=` i adressen).
3. **Plex-serverns adress** sett från containern, t.ex. `http://192.168.2.10:32400`.
4. Valfritt: [Seerr](https://seerr.dev) (URL + API-nyckel) eller Radarr (URL + API-nyckel) för önskningar.
   Overseerr och Jellyseerr fungerar också (samma API).

## Installera på Unraid (med Force update)

Imagen byggs automatiskt av GitHub Actions vid varje ändring och publiceras som
`ghcr.io/bapelsinsaft75/filmdilemma:latest`. Unraid hämtar den därifrån, så uppdateringar görs med
**Force update** i Docker-fliken.

**1. Logga in mot GitHubs register (en gång, om paketet är privat)**

Skapa en token på GitHub: *Settings → Developer settings → Personal access tokens → Tokens (classic)*
med behörigheten `read:packages`. Kör sedan i Unraids terminal:

```bash
docker login ghcr.io -u bapelsinsaft75
```

Klistra in token som lösenord. Inloggningen sparas och används av Force update.

**2. Lägg in mallen**

```bash
wget -O /boot/config/plugins/dockerMan/templates-user/my-filmdilemma.xml \
  https://raw.githubusercontent.com/bapelsinsaft75/Pfsense-dashboard-claude/claude/cool-ramanujan-mp5fff/filmdilemma-docker/unraid/my-filmdilemma.xml
```

Om repot är privat fungerar inte `wget`; kopiera då `unraid/my-filmdilemma.xml` från din klon:
`cp /mnt/user/appdata/filmdilemma-src/filmdilemma-docker/unraid/my-filmdilemma.xml /boot/config/plugins/dockerMan/templates-user/`

**3. Skapa containern**

Docker-fliken → **Add Container** → välj mallen **filmdilemma** → fyll i `TMDB_TOKEN`, `PLEX_URL` och
`PLEX_TOKEN` → **Apply**. Nätverk `br0` och IP `192.168.2.50` är redan ifyllda.

Har du en gammal container som heter `filmdilemma` (från `docker run`), ta bort den först:
`docker rm -f filmdilemma`. Databasen i `/mnt/user/appdata/filmdilemma` ligger kvar.

**4. Uppdatera i framtiden**

Docker-fliken → klicka på containern → **Force update**. (Med *Advanced view* på syns även
"update ready" när en ny image finns.)

Första starten importerar filmer från TMDB (`MAX_MOVIES`, standard 20 000 ≈ 15 min). Följ den via
containerns **Logs**. Öppna sedan **http://192.168.2.50** från en annan dator eller mobilen och tryck
**Sign in** → **Logga in med Plex**. Unraid-servern själv når inte adresser på `br0` om inte
*Settings → Docker → Host access to custom networks* är på.

### Alternativ: docker compose

```bash
cp .env.example .env && nano .env
docker compose pull && docker compose up -d
```

### Bygga själv i stället

```bash
docker build -t filmdilemma-local .
```

## Underhåll

| Vad | Hur |
|---|---|
| Uppdatera | **Force update** i Docker-fliken. En pågående import fortsätter där den slutade |
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
| `OMDB_API_KEY` | – | Valfri. IMDb-betyg tas från Plex; den här gratisnyckeln (omdbapi.com) används när Plex saknar dem |
| `REQUEST_MODE` | `watchlist` | `watchlist` = lägg på användarens Plex-watchlist, `seerr` = skicka direkt till Seerr |
| `SEERR_URL` / `SEERR_API_KEY` | – | Skicka önskningar till Seerr (även Overseerr/Jellyseerr). Gamla namnen `OVERSEERR_URL`/`OVERSEERR_API_KEY` fungerar också |
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

# pfSense Nätverkskarta

Grafisk dashboard för pfSense som körs som Docker-container på Unraid (testad mot Unraid 7.2.x).

- Karta med hus-ikon i hemstaden (standard: Örebro) och linjer till de länder klienterna pratar med.
  **Grön** linje = anslutningen startades inifrån nätverket, **lila** = startades utifrån.
- Vilka klienter som använder mest bandbredd just nu och mest data (24 timmar, 7 dagar, 30 dagar).
- Lista över alla klienter med namn, IP och MAC.
- Med UniFi: om klienten är trådlös eller trådbunden, switch och port, Wi-Fi-band och signal,
  samt en vy över switchar och accesspunkter.

Utan inställningar startar containern i **demoläge** med exempeldata.

## Hur data hämtas

| Vad | Källa |
| --- | --- |
| Klienter, IP, MAC, namn | pfSense REST API: DHCP-leases och ARP-tabellen |
| Vem som startade anslutningen, aktiva anslutningar | pfSense REST API: tillståndstabellen (firewall states) |
| Bandbredd och datamängd per klient | NetFlow/IPFIX från **softflowd** (rekommenderas), annars räknare i tillståndstabellen |
| Bandbredd på WAN | pfSense REST API: gränssnittsstatus |
| Land för en IP-adress | Lokal GeoIP-databas (DB-IP Lite laddas ner automatiskt varje månad) |
| Trådlöst/kabel, switchport, Wi-Fi | UniFi Network Application |

## 1. Förbered pfSense

1. **REST API** (paketet `pfSense-pkg-RESTAPI`, v2):
   skapa en API-nyckel under *System → REST API → Keys*. Användaren som äger nyckeln behöver
   läsrätt till status, diagnostik och brandväggstillstånd.
2. **softflowd** (rekommenderas): installera under *System → Package Manager*. Under *Services → softflowd*:
   - Interface: **LAN** (och övriga lokala nät/VLAN). Inte WAN, då syns bara routerns publika adress.
   - Host: dashboardens IP-adress, Port: `2055`
   - NetFlow-version: **9** (stöder även IPv6) eller 10 (IPFIX). Version 5 fungerar för IPv4.
   - Flow tracking level: Full
   - Om det finns en inställning för maximal livslängd eller aktiv timeout: sätt den till cirka 60 sekunder,
     så att långa anslutningar (strömning, nedladdningar) rapporteras löpande.

## 2. Förbered UniFi (valfritt)

Skapa ett lokalt konto i UniFi Network (*Inställningar → Admins & Users*) med enbart visningsrätt,
t.ex. `dashboard`. Kontot måste vara lokalt, inte ett UI.com-konto.

## 3. Installera på Unraid

Imagen byggs automatiskt av GitHub Actions och publiceras som
`ghcr.io/bapelsinsaft75/pfsense-dashboard-claude:latest`.

> Om repot är privat blir även imagen privat. Gör paketet publikt under GitHub → Packages →
> *pfsense-dashboard-claude* → Package settings, eller logga in på Unraid med
> `docker login ghcr.io` (användarnamn + en token med `read:packages`).

**Med mallen** (enklast): kopiera `unraid/pfsense-dashboard.xml` till
`/boot/config/plugins/dockerMan/templates-user/my-pfsense-dashboard.xml` på Unraid-servern,
gå till *Docker → Add Container* och välj mallen *pfsense-dashboard*.

**Manuellt**: *Docker → Add Container*, Repository `ghcr.io/bapelsinsaft75/pfsense-dashboard-claude:latest`
och lägg till variablerna i tabellen nedan.

### Nätverk

Ge containern **en egen IP på `br0`** (*Network Type: Custom: br0*, *Fixed IP address*), precis som UniFi.
Då når den UniFi-containern direkt och softflowd kan skicka till den IP-adressen på port 2055.
Webbsidan finns på `http://<containerns IP>:8080`.

Kör du hellre containern i *bridge*-läge med Unraids IP behöver Unraid kunna prata med containrar på br0:
slå på *Settings → Docker → Host access to custom networks* (Docker måste stoppas medan du ändrar).

### Inställningar

| Variabel | Exempel | Beskrivning |
| --- | --- | --- |
| `PFSENSE_URL` | `https://192.168.1.1` | Tomt = demoläge |
| `PFSENSE_API_KEY` | | REST API-nyckel |
| `LAN_SUBNETS` | `192.168.1.0/24` | Lokala nät, kommaseparerade |
| `UNIFI_URL` | `https://192.168.1.5:8443` | UniFi-containerns adress |
| `UNIFI_USER` / `UNIFI_PASS` | | Lokalt UniFi-konto |
| `UNIFI_SITE` | `default` | |
| `NETFLOW_PORT` | `2055` | `0` stänger av NetFlow-mottagaren |
| `TRAFFIC_SOURCE` | `auto` | `auto`, `netflow` eller `states` |
| `HOME_CITY`, `HOME_LAT`, `HOME_LON` | `Örebro`, `59.2741`, `15.2066` | Hus-ikonen på kartan |
| `WAN_INTERFACE` | `wan` | WAN-gränssnittets namn i pfSense |
| `RETENTION_DAYS` | `35` | Hur länge historiken sparas |
| `PFSENSE_VERIFY_SSL`, `UNIFI_VERIFY_SSL` | `false` | `false` för självsignerade certifikat |
| `GEOIP_DB_PATH` | | Egen MMDB-fil, t.ex. MaxMind GeoLite2-Country |
| `DEBUG_ENDPOINTS` | `true` | Felsökningssidor, se nedan |

Historik och GeoIP-databas sparas i `/data` (mappa till `/mnt/user/appdata/pfsense-dashboard`).

## Felsökning

- Statusmarkeringarna uppe till höger visar om pfSense, UniFi, NetFlow och GeoIP fungerar.
  Håll muspekaren över en markering för att se felet.
- Rådata från källorna (de första raderna) visas på
  `/api/debug/raw/states`, `/api/debug/raw/arp`, `/api/debug/raw/leases`, `/api/debug/raw/interfaces`,
  `/api/debug/raw/unifi_clients` och `/api/debug/raw/unifi_devices`.
  Bra att titta på om något ser fel ut, eftersom fältnamnen i pfSense REST API skiljer sig något mellan versioner.
- `/api/health` används av Dockers hälsokontroll.

Dashboarden har ingen inloggning och är tänkt att bara nås från det lokala nätet.

## Utveckling

```sh
pip install -r requirements.txt pytest
python -m pytest -q
DATA_DIR=./data python -m app.main     # demoläge på http://localhost:8080
docker build -t pfsense-dashboard .
```

`mockups/` innehåller de visuella förslagen som dashboarden bygger på (förslag D).

## Licenser för data

- Kartdata: Natural Earth via [world-atlas](https://github.com/topojson/world-atlas) (ISC).
- Landskoordinater: [world-countries](https://github.com/mledoze/countries) (ODbL).
- GeoIP: [DB-IP Lite](https://db-ip.com/db/lite.php), CC BY 4.0.
- Typsnitt: Barlow Condensed, JetBrains Mono och Public Sans (SIL Open Font License).
- d3 och topojson-client (ISC).

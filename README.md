# pfSense Nätverkskarta

Grafisk dashboard för pfSense som körs som Docker-container på Unraid (testad mot Unraid 7.2.x).

- Karta med hus-ikon i hemstaden (standard: Örebro) och linjer till de länder klienterna pratar med.
  **Grön** linje = anslutningen startades inifrån nätverket, **lila** = startades utifrån.
- Vilka klienter som använder mest bandbredd just nu och mest data (24 timmar, 7 dagar, 30 dagar).
- Lista över alla klienter med namn, IP och MAC. Du kan ge klienterna egna namn direkt i listan (pennan vid namnet).
- Klientlistan kan filtreras på status (online, inaktiva) och anslutning (trådlöst, kabel, okänd).
- Status för Unraid-servern: array, diskar med temperatur, processor, minne, Docker-containrar och
  virtuella maskiner (med filter för körs/stoppade).
- Status för en eller flera Plex-servrar, och vilka som strömmar just nu och vad.
- Klicka på ett land, på kartan eller i listan, för att se vilka klienter och vilka externa adresser
  (med värdnamn och organisation) som kommunicerar med landet.
- Med UniFi: om klienten är trådlös eller trådbunden, switch och port, Wi-Fi-band och signal,
  samt en vy över switchar och accesspunkter.
- **Larm** som en banner överst på sidan, och **e-post** (t.ex. Proton Mail med SMTP-token) och **Slack** vid viktiga händelser.
- Säkerhetsvy med Suricata-larm, pfBlockerNG-blockeringar och status för gateways och VPN.
- Historik äldre än 14 dagar raderas. Antalet dagar går att ändra under *Inställningar* på webbsidan.

Utan inställningar startar containern i **demoläge** med exempeldata.

## Hur data hämtas

| Vad | Källa |
| --- | --- |
| Klienter, IP, MAC, namn | pfSense REST API: DHCP-leases och ARP-tabellen |
| Vem som startade anslutningen, aktiva anslutningar | pfSense REST API: tillståndstabellen (firewall states) |
| Bandbredd och datamängd per klient | NetFlow/IPFIX från **softflowd** (rekommenderas), annars räknare i tillståndstabellen |
| Bandbredd på WAN | pfSense REST API: gränssnittsstatus |
| Land för en IP-adress | Lokal GeoIP-databas (DB-IP Lite laddas ner automatiskt varje månad) |
| Organisation för en IP-adress | DB-IP Lite ASN (laddas ner automatiskt) |
| Värdnamn för en extern adress | Omvänt DNS-uppslag, när du öppnar ett land |
| Trådlöst/kabel, switchport, Wi-Fi | UniFi Network Application |
| OpenVPN-klienter och gateways (WAN) | pfSense REST API: OpenVPN-status och gateway-status |
| Suricata-larm, pfBlockerNG och brandväggsblockeringar | Syslog från pfSense till containern (UDP 5514) |

## Larm

| Larm | Nivå | Mejlas / Slack |
| --- | --- | --- |
| OpenVPN-klient inte ansluten, eller gateway nere (två kontroller i rad, var 30:e sekund) | Kritiskt | Ja, och när den är uppe igen |
| pfSense svarar inte på 5 minuter | Kritiskt | Ja, och när det fungerar igen |
| Suricata-larm med prioritet 1 (nivån går att ändra) | Kritiskt | Ja |
| En klient har använt minst 3 gånger mer data än normalt senaste 24 timmarna, och minst 2 GB | Varning | Om du valt det |
| En klient försöker nå adresser som pfBlockerNG blockerar (25 gånger på 10 minuter) | Varning | Om du valt det |
| Hög paketförlust eller fördröjning på en gateway | Varning | Om du valt det |
| En UniFi-switch eller accesspunkt är offline, eller UniFi svarar inte | Varning | Om du valt det |
| Ny enhet ansluter till nätverket | Varning | Om du valt det |

Under *Inställningar* (kugghjulet):

- **Larm → Bekräfta innan larm:** hur ofta en kontroll görs och hur många misslyckade kontroller i rad som krävs
  innan det larmar, t.ex. var 60:e sekund och 3 gånger. Gäller larm märkta ↻ (VPN, gateway, pfSense, UniFi,
  Unraid, Plex och övervakade containrar/VM).
- **Larm → Larmtyper:** slå av och på varje typ av larm. Larmen för Unraid-server och Plex är avstängda från början.
- **Aviseringar → Tysta tider:** inga mejl eller Slack-meddelanden mellan två klockslag på valda veckodagar.
  Larmen syns ändå i bannern. Valfritt: släpp igenom kritiska larm, och få en sammanfattning när tiden är slut.
- **Aviseringar → E-postmottagare:** lägg till flera adresser och välj vilka larmtyper var och en ska få,
  t.ex. att larm om virtuella maskiner bara går till en viss adress. `MAIL_TO` blir första mottagare tills du
  sparar listan här. Varje rad har en knapp för testmejl. Under *Servrar* väljer du vilka Plex- och
  Unraid-servrar mottagaren ska få larm om (från början alla).
- **Övervaka containrar och virtuella maskiner:** klicka på klockan vid en container eller VM i Unraid-sektionen.
  Stannar den blir det ett kritiskt larm (efter bekräftelsen ovan).

"Normalt" är medianen av klientens dataförbrukning per dygn de senaste två veckorna, och kräver minst 4 dygns historik.
Gränserna ändras under *Inställningar* (kugghjulet). Samma larm mejlas inte igen förrän det har lösts.
Stängda larm syns kvar under *Händelser*.

## 1. Förbered pfSense

1. **REST API** (paketet `pfSense-pkg-RESTAPI`, v2):
   skapa en API-nyckel under *System → REST API → Keys*. Användaren som äger nyckeln behöver
   läsrätt till status, diagnostik och brandväggstillstånd.
2. **softflowd** (rekommenderas): installera under *System → Package Manager*. Under *Services → softflowd*:
   - Interface: **LAN** (och övriga lokala nät/VLAN). Inte WAN, då syns bara routerns publika adress.
   - Host: dashboardens IP-adress (t.ex. `192.168.2.30`), Port: `2055`
   - NetFlow-version: **9** (stöder även IPv6) eller 10 (IPFIX). Version 5 fungerar för IPv4.
   - Flow tracking level: Full
   - Om det finns en inställning för maximal livslängd eller aktiv timeout: sätt den till cirka 60 sekunder,
     så att långa anslutningar (strömning, nedladdningar) rapporteras löpande.

3. **Fjärrloggning** (för Suricata och pfBlockerNG): *Status → System Logs → Settings → Remote Logging Options*
   - Kryssa i *Enable Remote Logging*, Remote log servers: `<dashboardens IP>:5514` (t.ex. `192.168.2.30:5514`)
   - Remote Syslog Contents: *Firewall Events* och *System Events* (eller *Everything*)
4. **Suricata**: under *Services → Suricata → (interface) → Interface Settings*, kryssa i
   **Send Alerts to System Log**. Gör det för varje interface där Suricata körs.
5. **pfBlockerNG** loggar sina blockeringar i brandväggsloggen. Se till att *Logging* är påslaget för
   IP-listorna (standard). Dashboarden känner igen pfBlockerNG-regler på att deras tracker-ID börjar med `177`
   (ändras med `PFBLOCKER_TRACKER_PREFIX` om dina ser annorlunda ut). DNSBL-blockeringar syns inte i
   brandväggsloggen och räknas därför inte.
6. **VPN**: OpenVPN-klienter (*VPN → OpenVPN → Clients*) övervakas automatiskt var 30:e sekund via REST API,
   även om de inte är en gateway. Om klienten inte är ansluten (t.ex. `RECONNECTING` eller `down`) två kontroller
   i rad blir det ett kritiskt larm och ett mejl, och ett nytt mejl när den är ansluten igen.
   Gateways (t.ex. WAN) övervakas också, om *Gateway Monitoring* är påslaget i *System → Routing → Gateways*.

## 2. Förbered UniFi (valfritt)

Skapa ett lokalt konto i UniFi Network (*Inställningar → Admins & Users*) med enbart visningsrätt,
t.ex. `dashboard`. Kontot måste vara lokalt, inte ett UI.com-konto.

## 3. E-post via Proton Mail (valfritt)

1. Logga in på Proton Mail på webben: *Inställningar → Alla inställningar → IMAP/SMTP → SMTP-tokens*.
2. Skapa en token, välj adressen som ska skicka mejlen och kopiera token direkt (den visas bara en gång).
   Proton erbjuder SMTP-tokens på betalda planer, och adressen behöver vara på din egen domän.
3. Fyll i `SMTP_USER` (adressen), `SMTP_TOKEN` (token) och `MAIL_TO` (vem som ska få larmen) i Unraid-mallen.
   `SMTP_HOST` är `smtp.protonmail.ch` och `SMTP_PORT` `587` som standard.
4. Öppna *Inställningar* på webbsidan och tryck *Skicka testmejl*.

Fungerar även med andra SMTP-tjänster: ändra `SMTP_HOST` och `SMTP_PORT`.

## 4. Slack (valfritt)

1. Gå till https://api.slack.com/apps → *Create New App* → *From scratch*. Välj namn (t.ex. *Nätverket hemma*) och din arbetsyta.
2. Under *Incoming Webhooks*: slå på *Activate Incoming Webhooks* och tryck *Add New Webhook to Workspace*.
3. Välj kanalen som larmen ska skickas till och kopiera webhook-adressen (`https://hooks.slack.com/services/...`).
4. Fyll i `SLACK_WEBHOOK_URL` i Unraid-mallen och tryck *Testa Slack* under *Inställningar* på webbsidan.

Kritiska larm skickas alltid, och ett nytt meddelande när de är lösta. Varningar skickas också som standard;
det stängs av under *Inställningar*. Webhook-adressen fungerar som ett lösenord: den som har den kan skriva i kanalen.

## 5. Plex (valfritt)

Fyll i `PLEX1_NAME`, `PLEX1_URL` och `PLEX1_TOKEN` (och `PLEX2_...` för nästa server, upp till fyra).
Token hittar du genom att öppna Plex i webbläsaren, välja *⋯ → Hämta info → Visa XML* på en film och kopiera
värdet efter `X-Plex-Token=` i adressfältet. Strömmarna från alla servrar visas tillsammans längst ner på sidan,
och varje server får en egen statusmarkering. Omslagsbilderna hämtas via dashboarden, så webbläsaren behöver
inte nå Plex-servrarna själv.

## 6. Unraid-status (valfritt)

1. Skapa en API-nyckel med läsbehörighet under *Settings → Management Access → API Keys*.
2. Fyll i `UNRAID_URL` (t.ex. `http://192.168.2.10`), `UNRAID_API_KEY` och gärna `UNRAID_NAME`.
   Fler servrar: `UNRAID2_URL`, `UNRAID2_API_KEY`, `UNRAID2_NAME` och så vidare, upp till fyra.
   Varje server får en egen statusmarkering och en egen sektion.
3. Kör dashboarden på samma Unraid-server med egen IP på br0? Slå då på
   *Settings → Docker → Host access to custom networks* (Docker måste stoppas medan du ändrar), annars kan
   containern inte nå servern den körs på.

Unraid-API:ets fältnamn har ändrats mellan versioner. Dashboarden provar flera varianter av varje fråga;
vad som fungerade, och felen för det som inte gjorde det, syns på `/api/debug/unraid`.

## 7. Installera på Unraid

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
Då når den UniFi-containern direkt, och pfSense kan skicka NetFlow till port 2055 och syslog till port 5514
på den IP-adressen.
Webbsidan finns på `http://<containerns IP>:8080`.

Kör du hellre containern i *bridge*-läge med Unraids IP behöver Unraid kunna prata med containrar på br0:
slå på *Settings → Docker → Host access to custom networks* (Docker måste stoppas medan du ändrar).

### Inställningar

| Variabel | Exempel | Beskrivning |
| --- | --- | --- |
| `PFSENSE_URL` | `https://192.168.2.1` | Tomt = demoläge |
| `PFSENSE_API_KEY` | | REST API-nyckel |
| `LAN_SUBNETS` | `192.168.2.0/24` | Lokala nät, kommaseparerade |
| `UNIFI_URL` | `https://192.168.2.9:8443` | UniFi-containerns adress |
| `UNIFI_USER` / `UNIFI_PASS` | | Lokalt UniFi-konto |
| `UNIFI_SITE` | `default` | |
| `NETFLOW_PORT` | `2055` | `0` stänger av NetFlow-mottagaren |
| `TRAFFIC_SOURCE` | `auto` | `auto`, `netflow` eller `states` |
| `HOME_CITY`, `HOME_LAT`, `HOME_LON` | `Örebro`, `59.2741`, `15.2066` | Hus-ikonen på kartan |
| `WAN_INTERFACE` | `wan` | WAN-gränssnittets namn i pfSense |
| `RETENTION_DAYS` | `14` | Hur länge historiken sparas (standardvärde, kan ändras på webbsidan) |
| `SYSLOG_PORT` | `5514` | Syslog från pfSense, `0` stänger av |
| `SMTP_HOST`, `SMTP_PORT` | `smtp.protonmail.ch`, `587` | E-postserver |
| `SMTP_USER`, `SMTP_TOKEN` | | Proton-adress och SMTP-token |
| `MAIL_TO`, `MAIL_FROM` | | Mottagare (kommaseparerade) och avsändare (standard: `SMTP_USER`) |
| `EMAIL_WARNINGS` | `false` | Mejla även varningar (kan ändras på webbsidan) |
| `SLACK_WEBHOOK_URL` | | Incoming Webhook från Slack |
| `SLACK_WARNINGS` | `true` | Skicka även varningar till Slack (kan ändras på webbsidan) |
| `UNRAID_URL`, `UNRAID_API_KEY` | `http://192.168.2.10` | Unraid-servern att övervaka |
| `UNRAID_NAME` | `LillaNasen` | Visningsnamn, tomt = serverns namn |
| `UNRAID2_URL`, `UNRAID2_API_KEY`, `UNRAID2_NAME` | `http://192.168.2.11`, …, `Tower` | Nästa Unraid-server (upp till `UNRAID4_...`) |
| `PLEX1_NAME`, `PLEX1_URL`, `PLEX1_TOKEN` | `Peters Plex`, `http://192.168.2.56:32400` | Plex-server 1 (upp till `PLEX4_...`) |
| `PLEX_VERIFY_SSL` | `false` | `false` för Plex egna certifikat |
| `PUBLIC_URL` | `http://192.168.2.30:8080` | Dashboardens adress, för länkar i Slack och e-post |
| `PFBLOCKER_TRACKER_PREFIX` | `177` | Hur pfBlockerNG-regler känns igen i brandväggsloggen |
| `PFSENSE_VERIFY_SSL`, `UNIFI_VERIFY_SSL` | `false` | `false` för självsignerade certifikat |
| `GEOIP_DB_PATH` | | Egen MMDB-fil, t.ex. MaxMind GeoLite2-Country |
| `GEOIP_ASN_PATH` | | Egen ASN-databas, t.ex. MaxMind GeoLite2-ASN |
| `REVERSE_DNS` | `true` | Slå upp värdnamn för externa adresser |
| `DEBUG_ENDPOINTS` | `true` | Felsökningssidor, se nedan |

Historik, larm, egna klientnamn, inställningar från webbsidan (`settings.json`) och GeoIP-databaser sparas i `/data`
(mappa till `/mnt/user/appdata/pfsense-dashboard`). Det du sparar under *Inställningar* på webbsidan går före
miljövariablerna.

## Felsökning

- Statusmarkeringarna uppe till höger visar om pfSense, UniFi, NetFlow och GeoIP fungerar.
  Håll muspekaren över en markering för att se felet.
- Rådata från källorna (de första raderna) visas på
  `/api/debug/raw/states`, `/api/debug/raw/arp`, `/api/debug/raw/leases`, `/api/debug/raw/interfaces`,
  `/api/debug/raw/gateways`, `/api/debug/raw/openvpn`, `/api/debug/netflow` (mottagna NetFlow-paket),
  `/api/debug/raw/unifi_clients` och `/api/debug/raw/unifi_devices`.
  Bra att titta på om något ser fel ut, eftersom fältnamnen i pfSense REST API skiljer sig något mellan versioner.
- `/api/health` används av Dockers hälsokontroll.

Dashboarden har ingen inloggning och är tänkt att bara nås från det lokala nätet. Den som når sidan kan
också ändra inställningarna och stänga larm.

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

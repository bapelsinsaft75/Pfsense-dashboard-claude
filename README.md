# Pfsense-dashboard-claude

Grafisk dashboard för pfSense som körs som Docker-container på Unraid.

## Status: visuella förslag

`mockups/index.html` innehåller fyra förslag (exempeldata):

- **A – Kontrollrum**: mörk driftvy, allt på en skärm.
- **B – Översikt**: ljus rapportvy med perioder, 24 h-graf och sökbar klientlista.
- **C – Glob**: snurrbar jordglob centrerad på Örebro.
- **D – Översikt, mörk**: layouten från B i färgerna och typsnitten från A (vald riktning). Visar även UniFi-data: trådlöst eller kabel, switch och port, Wi-Fi-band och signal.

Gemensamt: karta med hus-ikon i Örebro och linjer till länder
(grön = anslutningen startades inifrån, lila = startades utifrån),
toppklienter efter bandbredd och datamängd, samt lista över klienter med IP/MAC.

Visa lokalt (kartdatan hämtas med fetch och kräver en webbserver):

```sh
cd mockups && python3 -m http.server 8080
```

Kartdata: Natural Earth via [world-atlas](https://github.com/topojson/world-atlas) (ISC-licens, se `mockups/countries-110m.LICENSE`).

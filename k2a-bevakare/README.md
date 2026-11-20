# K2A-bevakare

Loggar in på Mina sidor och kontrollerar avtalssidan med jämna mellanrum. Om knappen
**Avbryt uppsägning** dyker upp trycker den på knappen och bekräftar. Status visas på en webbsida.

## Kom igång

```bash
cp .env.exempel .env      # fyll i AVTAL_URL, K2A_ANVANDARNAMN, K2A_LOSENORD
docker compose up -d --build
```

Öppna `http://<server>:8080` (eller den port du satt i `WEBB_PORT`).

1. Låt `TORRKORNING=1` stå kvar första gången. Kontrollera på statussidan att inloggningen
   fungerar och att skärmdumparna visar rätt sida.
2. Sätt `TORRKORNING=0` och kör `docker compose up -d` för att den ska klicka på riktigt.

Följ loggen med `docker compose logs -f`.

## Statusar

| Status | Betydelse |
|---|---|
| Ingen uppsägning att avbryta | Inloggad, knappen finns inte |
| Knappen finns – torrkörning | Skulle ha klickat |
| Uppsägningen avbröts | Klickade, bekräftade, och knappen är borta |
| Klickade, men knappen finns kvar | Kontrollera skärmdumparna |
| Fel | t.ex. misslyckad inloggning, se loggen |

## Om något inte hittas

Fält och knappar hittas via sin text eller typ. Om sidan ser annorlunda ut kan du
ange egna selektorer i `.env`, se `.env.exempel` (`SEL_ANVANDARE`, `AVBRYT_TEXT` m.fl.).

Push-notiser till mobilen: installera appen ntfy, välj ett svårgissat ämne och sätt
`NTFY_URL=https://ntfy.sh/<ämne>`.

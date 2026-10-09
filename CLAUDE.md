# zendesk-cli

Python-CLI (`zd`) för Zendesk med personens egen webbinloggning i stället för
API-nyckel. Byggt som `e37-cli`: `src/`-layout, bara standardbiblioteket, argparse.

## Regler

- Inga tredjepartsberoenden. `urllib`, `json` och en egen minimal WebSocket-klient
  (`browser.py`) räcker.
- Inloggning sker bara i en riktig webbläsare som personen själv skriver i. Ingen kod
  fyller i lösenord. Zendesks inloggningssida svarar 403 på HTTP-anrop som inte kommer
  från en webbläsare, och de flesta loggar in via Microsoft eller Google.
- Webbläsaren används bara vid `zd login`. Allt annat är HTTPS med cookies från
  nyckelringen.
- Cookies är en inloggning. De ligger bara i nyckelringen (`keychain.py`), skrivs aldrig
  ut, loggas aldrig och hamnar aldrig i ett felmeddelande. `account list` visar
  användare och datum, inte värden.
- Ingen standardinstans i koden. `session.open_account` väljer NAMN, `ZD_ACCOUNT`
  eller den enda sparade.
- Allt som skriver: torrkörning som standard och `--apply` för att skicka. Efter
  sändning läses posten tillbaka och jämförs. Avviker något stoppar körningen.
- Språk: utskrifter till användaren på svenska. Kod, kommentarer och `--help` på
  engelska.
- Dokumentera endpoints och beteenden i `docs/` när de blir kända, särskilt de privata
  som bara webben använder.

## Struktur

- `src/zd/browser.py`: startar Chrome/Edge med en egen profil per instans och DevTools,
  läser cookies via `Storage.getCookies` tills `users/me` säger att sessionen är
  inloggad, och stänger sedan webbläsaren.
- `src/zd/session.py`: HTTP med cookies, CSRF-token för skrivningar
  (`authenticity_token` från `users/me`), paginering, 429-väntan, och roterade cookies
  som skrivs tillbaka till nyckelringen.
- `src/zd/keychain.py`: från e37-cli. Delar upp poster över 2000 tecken (Credential
  Manager tar max 2560 byte).
- `src/zd/cli.py`: kommandona.
- `src/zd/skill/SKILL.md`: Claude-skillen. Ändras ett kommando, ändra skillen i samma
  commit.

Versionen står bara i `src/zd/__init__.py`.

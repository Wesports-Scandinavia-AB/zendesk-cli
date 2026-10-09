# zendesk-cli

Zendesk från terminalen, inloggad som dig själv. Ingen API-nyckel.

`zd login` öppnar ett webbläsarfönster en gång. Du loggar in som vanligt (Microsoft,
Google, lösenord och 2FA) och stänger fönstret när du ser Zendesk. Sessionen sparas då i
din nyckelring. Därefter går allt över vanlig HTTPS mot samma adresser som Zendesks eget
webbgränssnitt anropar, med exakt dina behörigheter. Webbläsaren behöver inte vara
öppen.

## Installera

```
pipx install git+https://github.com/Wesports-Scandinavia-AB/zendesk-cli
zd skill install        # lär Claude använda zd
```

Kräver Python 3.9+ och Chrome eller Edge. Inga tredjepartsberoenden.

## Använd

```
zd login vartex                    logga in på vartex.zendesk.com (fönster öppnas)
zd login wesports --subdomain wesportshelp
zd account list                    sparade inloggningar (aldrig cookies)
zd whoami
zd logout vartex [--forget-browser]

zd tickets [--mine] [--query 'status:open tags:retur'] [--view ID] [--limit N]
zd views                           dina vyer med antal
zd ticket show 123456              hela tråden, interna anteckningar inräknade
zd ticket reply 123456 --internal --text 'Kollat med lagret.'
zd ticket reply 123456 --file svar.txt --status solved --apply
zd ticket set 123456 --status pending --add-tag vantar_lager --apply

zd list macros --find retur        också triggers, automations, views, groups, brands,
zd show macros ID                  ticket-fields, ticket-forms, user-fields, sla-policies,
zd update macros ID --data @m.json dynamic-content, webhooks

zd hc articles --locale sv --find retur [--brand addnature]
zd hc article ID [--locale sv] [--html]
zd hc update ID --locale sv --file a.html --apply

zd api GET /api/v2/whatever.json   vilken adress som helst som webben använder
zd api PUT /api/v2/... --data @body.json --apply
```

Alla kommandon tar `--json` där det är meningsfullt, och `-a NAMN` när fler än en
inloggning är sparad.

**Allt som skriver är en torrkörning** tills du lägger till `--apply`. Då skickas
ändringen, posten läses tillbaka och jämförs, och något som inte blev som väntat
stoppar med ett fel.

## Session och säkerhet

- Cookies sparas i nyckelringen: Credential Manager (`zendesk-cli:<namn>`) på Windows,
  login-nyckelringen på macOS. Aldrig i en fil.
- Den som har cookies **är** du i Zendesk tills sessionen går ut. Dela dem aldrig.
  `zd logout` avslutar sessionen hos Zendesk också.
- Inloggningsfönstret är en helt vanlig Chrome, utan DevTools eller automatisering;
  annars stoppar Zendesks Cloudflare inloggningen. Sessionen läses ur profilen först
  när fönstret är stängt.
- Webbläsarprofilen ligger per instans i `%LOCALAPPDATA%\zendesk-cli\profiles\<namn>`
  och minns SSO-inloggningen, så nästa `zd login` tar ett par klick.
- Zendesk förnyar sessionen när den används. `zd` sparar de förnyade cookies, så en
  inloggning som används håller lika länge som i webbläsaren. Hur länge en oanvänd
  session lever bestäms av kontots inställning i Admin Center (Säkerhet → Sessioner).
- För CI: `ZD_SESSION` med samma JSON som nyckelringsposten går före nyckelringen.

---
name: zendesk
description: Läs och ändra i Zendesk som den inloggade personen själv med zendesk-cli (`zd`) - ärenden, vyer, svar och interna anteckningar, makron, triggers, Help Center-artiklar, och vilken endpoint som helst som Zendesks webb använder. Ingen API-nyckel. Use when the user asks about Zendesk tickets, wants to read a ticket thread, reply or add an internal note, change status or tags, look at or change macros/triggers/views, read or edit a Help Center article, or log in to Zendesk from the terminal - e.g. "vad står i ärende 123456", "lägg en intern anteckning", "vilka makron handlar om retur", "uppdatera artikeln om returer".
---

# zd: Zendesk som dig själv

`zd` använder personens egen Zendesk-session (cookies i OS-nyckelringen), inte en
API-nyckel. Den ser och får göra exakt det personen ser och får göra i webben.

## Innan du börjar

- `zd account list` visar sparade inloggningar. Är listan tom, eller säger ett kommando
  att sessionen gått ut: kör `zd login NAMN` i bakgrunden (`run_in_background`) och be
  personen logga in i fönstret som öppnas. Personen stänger fönstret när
  Zendesk syns; då sparas sessionen och kommandot blir klart. Be aldrig om lösenord eller cookies i chatten.
- Fler än en inloggning: välj med `-a NAMN`.
- `--json` ger rådata. Använd det när du ska räkna, filtrera eller citera fält.

## Läsa

```
zd tickets [--mine] [--query 'status:open tags:retur'] [--view ID] [--limit N]
zd views
zd ticket show ID              tråden med interna anteckningar ([INTERN])
zd list KIND [--find TEXT]     macros, triggers, automations, views, groups, brands,
zd show KIND ID                ticket-fields, ticket-forms, user-fields, sla-policies,
                               dynamic-content, webhooks
zd hc articles [--locale sv] [--find TEXT] [--brand SUBDOMÄN]
zd hc article ID [--locale sv] [--html]
zd api GET /api/v2/...         allt annat; --all KEY följer pagineringen
```

## Skriva: alltid torrt först

Varje skrivande kommando visar vad som skulle skickas och gör ingenting utan `--apply`.

1. Kör utan `--apply` och visa personen utskriften.
2. Ett **publikt svar** (`ticket reply` utan `--internal`) går till kunden. Skicka det
   bara på personens uttryckliga ok för exakt den texten.
3. Kör samma kommando med `--apply`. Zendesk läses tillbaka och jämförs. Ett fel efter
   sändning betyder att något ändrades men inte blev som väntat, till exempel att en
   trigger ändrade tillbaka. Säg det, försök inte igen blint.

```
zd ticket reply ID (--text T | --file F) [--internal] [--html] [--status S] [--apply]
zd ticket set ID [--status S] [--priority P] [--assignee me|ID|EMAIL] [--group ID]
                 [--add-tag T] [--remove-tag T] [--apply]
zd update KIND ID --data @fil.json [--apply]      bara ändrade fält skickas
zd hc update ID --locale sv [--file a.html] [--title T] [--draft yes|no] [--apply]
zd api POST|PUT|PATCH|DELETE PATH --data @body.json [--apply]
```

Ändra en konfiguration: `zd show macros ID > m.json`, redigera, `zd update macros ID
--data @m.json` (torrt), sedan `--apply`.

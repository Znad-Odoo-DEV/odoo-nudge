# Nudge WhatsApp & Telegram

Ask [Nudge](../nudge/README.md) from your phone, on WhatsApp (Business Cloud API) or Telegram. Questions are
answered in the chat; a change is never made there: Nudge prepares it and answers with a link, and the person
says yes inside Odoo.

Community and Enterprise. For Odoo 17, 18 and 19. No new Python package: plain
`requests` for the two APIs. Needs Nudge and Nudge: MCP Server, which is installed with it and stays switched off (it reuses its log of requests, its origin and
its pending step).

## What a person gets

- **A question is answered**: "كم فاتورة متأخرة" or "how many overdue invoices" gets a count; "show me the
  contract of Raghad" gets a few lines and an **Open in Odoo** link. Answers are plain text, at most 1500
  characters, compact lists instead of tables. Any Arabic dialect and English.
- **A change is prepared, not made**: "اعمل عرض سعر لراغد" or "create a task Call Ebrahim" is prepared as a pending
  step, exactly the card of the panel, and the answer carries the link that opens it. The person reads it and
  presses **Yes, make this change** in Odoo (or No). Nothing is created from the chat. The step lives 24 hours.
- **Voice notes** are written out by the provider's transcription when it can (the server needs `ffmpeg` to turn
  the Ogg of the phone into WAV); otherwise Nudge asks for text. **Pictures and files** of a document go through
  *Document to draft record*, in prepare mode: the same link, the same yes.
- **Private mode**: only what needs no AI answers: remembered answers, common requests understood by rules
  (counts, sums, finding, a contact or a task to prepare), the search of Nudge. Voice notes and pictures are
  refused with "send text". The budgets and the limit of answers at the same time apply as in the panel.

## Linking a phone

Each person links their own phone: Preferences, **Nudge Messaging**, *Get a Linking Code* (six digits, once, ten
minutes), and sends the code to the number on WhatsApp or to the bot on Telegram. The phone is then bound to that
person: one phone per person and channel, one person per phone. The messages of that phone run as that person,
with their rights and companies. Unlink in the same page (or send *unlink* from the phone); a manager sees all
links under Nudge, Linked Phones. Links are archived, never deleted.

A phone nobody linked gets one polite line and nothing else: its question is not kept, answered or counted as a
request.

## Switching it on

Off until an administrator turns it on, in Settings, Nudge, **Messaging assistants**:

| Setting | Meaning |
|---|---|
| Messaging Assistants | The two addresses answer. Off: 403 to everyone. |
| WhatsApp phone number id, verify token | From the Meta app. The verify token is any word you choose; give Meta the same. |
| WhatsApp app secret, access token | The app secret signs every message; the access token sends the answers. Kept, never shown again. |
| Telegram bot token, webhook secret | From BotFather; the secret is any word of letters, digits, `_` and `-`. Kept, never shown again. **Register the Webhook** tells Telegram the address. |

Addresses (shown in the settings): `https://odoo.example.com/nudge_messaging/whatsapp` and
`https://odoo.example.com/nudge_messaging/telegram`. On a server with several databases add `?db=<name>`. The
routes avoid the words `mcp` and `sse` on purpose: some proxies rewrite those paths.

## Safety

- **Proof first.** WhatsApp: `X-Hub-Signature-256` is the HMAC-SHA256 of the raw body with the app secret.
  Telegram: `X-Telegram-Bot-Api-Secret-Token` equals the webhook secret. Compared in constant time, before the
  body is parsed or anything is read. Wrong or missing: 403, nothing said. Without the secret set nothing is
  accepted. Body over 1 MB: 413.
- **Idempotent**: a message id is taken once (a repeat from Meta or Telegram is answered 200 and ignored).
- **Rate limit** per sender and channel: 20 messages in 10 minutes by default (`nudge_messaging.rate_messages`,
  `nudge_messaging.rate_minutes`); the first message over the limit gets one line, the rest silence.
- **sudo** is used only to find the person a phone is linked to and to write the log of messages. Everything
  after runs as that person.
- **The keys are never shown, logged or sent back.** A failed call to Telegram logs the kind of failure only
  (its address holds the token).
- **Read-only from the chat.** The tools offered to the AI are the ones that look and summarise, and the ones that
  only prepare (`prepare_create`, `prepare_write`, `call_method`, `document_to_record`). Results are framed as
  data. A group chat of Telegram is ignored.
- **Audit**: every message is a Request in *What Nudge Did*, with the origin `WhatsApp` or `Telegram`; every
  prepared change is a step with the same origin.

## The line of answers

An answer that needs the AI provider, a voice note or a file takes a place in the same line as the answers of the
panel (`nudge.slot`, limits of Nudge and of the provider), and gives it back at the end, on error or on Stop. When
none is free the message stays in the queue, not answered and not counted as a try: the scheduled action takes it
up again when a place frees (a few seconds later), and it never goes before a question waiting in the panel. A
change prepared for the person's yes holds no place while it waits. Messages answered by rules need no place.

## Tests

```
odoo-bin -d <db> -i nudge_messaging --test-tags /nudge_messaging --stop-after-init
```

No test reaches a network: both APIs are replaced.

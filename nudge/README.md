# Nudge

An AI companion that lives inside the Odoo web client.
[العربية](README.ar.md)

Press a shortcut from any screen, or hold it and talk. Nudge looks at what you are looking at,
answers in your language, and points at the button or the field to use. It knows the database it
is installed on: its custom modules, the fields they add, how each app is configured.

In Agent mode it does the work for you: it looks up records, creates and changes them, presses
buttons, with your rights, and asks you before each change.

Long work is done in the background: leave it to Nudge, go on with something else, and be told
when it is done or when your answer is needed.

What is left is listed under [What is not there yet](#what-is-not-there-yet). A short guide for the people who use it:
[docs/user-guide.md](doc/user-guide.md), [بالعربية](doc/user-guide.ar.md).

## What Nudge does

| | |
|---|---|
| **Sees your screen** | The record you are on, its fields and their values, unsaved changes included, its buttons and tabs. Only when you send a message, and only what you are allowed to see. |
| **Knows Odoo** | The official documentation of Odoo 18 comes with the module: every app of Community and Enterprise, and the developer documentation (ORM, fields, views, security, automations, QWeb, the JavaScript framework), 1067 pages searched by Postgres. For how standard Odoo works, or how something is done in code, Nudge reads the documentation before answering, and gives the link of the page. Nothing is fetched from outside. |
| **Counts and sums** | In Agent mode: how many, how much, which most, per what. Grouped by any field, a date by day, week, month, quarter or year; sum, average, minimum, maximum of any number. Through the ORM, as the person: what they cannot open is not counted. |
| **Computes with Python** | Off by default; an administrator switches it on. The agent runs Python in the sandbox of Odoo (the one of the server actions), as the person: every record passes their rights. The code reads only: it runs in a savepoint rolled back when it ends, so what it writes is discarded, and a change still goes through the yes of the person. No import, no sudo, no cursor, no private attribute, no model of the system. Every run is in the log. |
| **SQL for administrators** | Off by default. The group Settings, and only those allowed in every company, may ask the agent for one SELECT: a join or a report no other tool gives. No change, no lock, no comment, one statement; the tables of Odoo they may read, never users, parameters or providers; 50 rows, 10 seconds, in a savepoint rolled back when it ends. Every query is in the log. |
| **Points at things** | A question asked with the screen shared asks first: on the screen, or in writing (each time, or once for all in the preferences). On the screen, its pointer goes to the button, field or tab to use and presses it, for show; a box with a short text opens, and the rest of the screen is blurred and dimmed, the element clear. Step by step: the next step comes when you have done the current one. A field hidden in a closed tab is reached through the tab. |
| **Takes you there** | "Take me to the quotations" opens the screen, among the menus you can open. |
| **Knows this database** | Which modules are custom and what they are for, which fields they add to standard screens, how deliveries, approvals and other options are set. Read again when modules change. |
| **Listens** | Hold the shortcut and ask, or press the microphone. In Arabic and in English, names of buttons in the middle of an Arabic sentence included. |
| **Types what you dictate** | Click in a text field, press the dictation shortcut, talk: the words go where your cursor is. Or tell Nudge what to write in which field. Nothing is saved until you save. |
| **Reads its answers** | To those who ask for it, when the device has a voice for the language. Off unless turned on. |
| **Does the work** | In Agent mode: "Create a quotation for Acme with 3 office chairs, then confirm it." Nudge finds the customer and the product, shows what it is about to create, and waits for your yes. Then the same for the confirmation. |
| **Works while you are away** | Press the clock instead of Send: the request is worked on by the server while you do something else. A message tells you when Nudge needs your yes, and when it is done, from any screen. |
| **Follows your words** | Business notes written by your managers ("we call quotations offers") open every conversation. |
| **Speaks your language** | Arabic and English, right to left, whatever the language of the interface. |
| **Stays where you put it** | Drag the avatar anywhere; pick its colour in the preferences. |
| **Stays within budget** | Tokens and cost per person and per day, per company and per month, set by the administrator. Every call to the provider, speech included, is in the ledger. |

## Install

Requirements: Odoo 18 (Community or Enterprise). No extra Python package, unless you use the
Anthropic provider (`pip install -r requirements.txt`).

1. Add this folder to the `addons_path` of Odoo.
2. Update the apps list and install **Nudge**.

## Security

Nudge acts as the person, never beyond them: every tool runs with their rights, their
record rules and their companies; the models that hold how Odoo works and the fields
that hold secrets are closed whatever the rights. What leaves the database, who may do
what, the switches an administrator holds, what is written down, and the known
trade-offs are in [docs/security.md](doc/security.md).

## Try it for free

1. Get a Gemini API key at <https://aistudio.google.com/apikey>.
2. In Odoo: **Nudge ‣ Configuration ‣ AI Providers ‣ Google Gemini (free tier)**.
3. Paste the key in **API Key**, save, press **Test Connection**.
4. Open any record and press **Ctrl + Shift + Space**.

If the model is refused, or the provider says it is temporarily unavailable, press
**Fetch Models** and pick another one from the list with **Use**. Free models are often busy:
on 2026-09-29 `gemini-3.8-flash` answered "high demand" while `gemini-3.5-flash-lite` answered
in about a second.

> **Free tiers and your data.** On the Gemini free tier, Google uses what you send to improve its
> products. With screen sharing on, what you send includes the record on your screen. Use a free
> tier with demo data. For real customer data, use a paid tier, or Ollama, which runs on your own
> server.

Other providers are already listed under AI Providers: Groq, OpenRouter (models ending in
`:free`), Ollama, Anthropic Claude. Set the key, press **Fetch Models**, pick a model.

### Ollama: a model on your own server

For data that must not leave the company, run the model yourself:

1. Install [Ollama](https://ollama.com) on a server with a GPU that Odoo can reach. Odoo.sh
   cannot host it: use a separate server on the same network, or over a VPN.
2. Pull a model: `ollama pull qwen3:14b` (good in Arabic), or `llama3.3`, `gemma3`, `mistral`.
   A model of 14–32B parameters on a card with 16–24 GB of memory answers well; 7–8B models
   fit 8 GB but are weaker at planning and at using the tools; a server without a GPU works,
   slowly.
3. **Nudge ‣ Configuration ‣ AI Providers ‣ Ollama (local)**: set the address of the server
   in **Base URL** (`http://your-server:11434/v1`; `localhost` only when Ollama runs on the Odoo
   server itself), leave **Requires an API Key** unticked, press **Fetch Models**, pick one with
   **Use**, then **Test Connection**.

Nothing goes out: questions, screens, files and what the agent reads stay on that server.
Speech stays with the browser, as Ollama does not turn speech into text.

### Before any provider

Without a provider, the panel offers a search instead of a conversation: the screens the
person can open, the settings of the apps (for administrators) and the official documentation,
by a few words; the pointer shows the way to a screen or a setting found. No AI is asked, and
nothing leaves the database. The conversation takes over the moment a provider is ready.

### Voice and providers

Chrome, Edge and Safari turn speech into text themselves. For the other browsers, and when the
administrator prefers it, the AI provider does it. A provider says how under **Voice** on its
form:

| Provider | Speech to Text | Tried |
|---|---|---|
| Google Gemini | A model that listens, `gemini-3.1-flash-lite` | Yes, in Arabic and English |
| Groq, OpenAI | Transcription service, `whisper-large-v3` | No: written from their documentation |
| Anthropic Claude, Ollama, OpenRouter | Not available | |

Conversations can go to one provider and speech to another: Nudge uses the first provider that
is ready and can listen.

## Using Nudge

- **Call it**: `Ctrl + Shift + Space`, or the round avatar at the bottom of the screen, or the
  icon in the top bar. The shortcut can be changed in the preferences of the panel (gear icon).
- **Talk to it**: hold `Ctrl + Shift + Space`, ask, let go. Or press the microphone in the panel;
  it stops when you stop talking. The button next to it says which language you speak
  (`ع` / `EN`): press it to change.
- **Dictate**: click in a text field of Odoo, press `Ctrl + Alt + D`, talk, press it again. Or
  hold it while you talk. Dates, numbers and fields where a record is picked are not dictated.
- **Have it write**: "Write in the customer reference: order by phone, 12 March."

### Agent mode

For the people of the group **Nudge / Agent User**. Under the message box, choose **Agent**; or
say "Nudge, do it" after it explained how.

- **Ask for what you want done**: "Create a contact for Sara Khalil at Acme", "Set the customer
  reference of this quotation to PO-7788", "Confirm this order", "Which of my quotations are
  still waiting?"
- **Read what Nudge shows before it changes anything**: what it is about to create, the fields
  as they are and as they will be, or the button it is about to press and on which record. A
  warning sign marks what is not taken back with a click: posting, validating, cancelling,
  anything about money or stock.
- **Say yes or no.** On a yes, the change is made as you, with your rights: the record says it
  was created or changed by you. On a no, nothing happens and Nudge asks what you want instead.
- **One change at a time.** A request of several changes asks you several times.
- Writing a new message instead of answering leaves the question undecided: nothing is done.

What Nudge can do is what you can do: a record you cannot open is not found, a button you do not
have on the form cannot be pressed. Some things it never does, whoever asks: it never deletes,
and it never touches users, access rights, companies, or the settings of Odoo.

### In the background

In Agent mode, the clock next to Send leaves the request to the server. Close the panel, go to
another screen: the work goes on, and Odoo tells you from wherever you are.

- **Nudge needs your answer**: press **Show me**, read what it is about to do, say yes or no.
  Then it goes on, in the background again.
- **Nudge is done**: a message with what it did; the number on the avatar says how many requests
  ended since you last looked.
- What is still worked on is shown at the end of its conversation, with what Nudge is doing, and
  a **Stop** button. What was done stays done.
- A request is worked on by pieces of at most 90 seconds, taken up one after the other, 12 at
  most, and 60 steps at most. Past that, Nudge tells what was done and what is left.
- While Nudge works on a conversation in the background, that conversation is its own: write in
  another one, or stop it first.
- **Ask about the screen**: "What am I looking at?", "What should I do next?", "Why can't I save?"
- **Ask to be shown**: "Show me how to confirm this order", "Where do I change the salesperson?"
- **Ask to go somewhere**: "Take me to the warehouses."
- **Ask about this database**: "What is the maintenance module here?", "Which fields did we add
  to the sales order?"
- **The eye** above the message box tells what Nudge sees, and closes it.

## Privacy

- Nudge looks at the screen when you send a message. Nothing is collected in the background.
- What it is shown is what your screen shows: nothing you are not allowed to read.
- Values of fields whose name looks like a secret (password, token, key) are hidden before leaving.
- Each person can close the eye. An administrator can turn screen sharing off for everybody:
  **Settings ‣ Nudge ‣ Privacy**.
- The microphone is open while you hold the shortcut, or from the press on the button to the end
  of what you say. Never otherwise. No recording is kept, in Odoo or in its logs.
- Who turns your voice into text is chosen by the administrator, in the same place: the browser
  (Chrome, Edge and Safari send the sound to the servers of their maker), the AI provider, or
  the browser when it can and the provider otherwise. Voice can be turned off for everybody.
- With the recording go the words written on your screen, labels of buttons and fields, so that
  they are recognized. Never what the records hold.
- In Agent mode, what Nudge reads of the records to do its work goes to the AI provider, as
  your questions do. Fields that hold secrets are hidden, and the models that hold how Odoo
  itself works are closed, whatever the rights of the person. An administrator can turn Agent
  mode off: **Settings ‣ Nudge ‣ Agent**.
- The profile of the database holds structure and configuration, never business records. Its
  parts about access rights and automation are told to managers only, and a part about a model
  only to those who can read that model.

## Who can use Nudge

| Group | Can |
|---|---|
| Nudge / User | Talk to Nudge. Sees only their own conversations. |
| Nudge / Agent User | Can also ask Nudge to do the work, in Agent mode. Reads the log of what Nudge did for them. |
| Nudge / Manager | Sees every conversation and its cost. Reads the provider list. Reads and corrects the database profile. Writes business notes. |
| Settings (Administrator) | Creates providers and sets API keys. |

The administrator is a Manager after installation. Give access to others from
**Settings ‣ Users**, or to everybody at once with **Settings ‣ Nudge ‣ Nudge for All Internal Users**.

## For managers

- **Nudge ‣ What Nudge Knows ‣ Database Profile**: what was read from the database, version by
  version, and what changed. Any piece can be corrected; corrections survive a refresh.
- **Nudge ‣ What Nudge Knows ‣ Business Notes**: how your business names things and works.
- **Nudge ‣ Conversations**: who asked what, tokens and cost.
- **Nudge ‣ Usage**: every call to an AI provider, conversation or speech, by person and by
  day, with tokens and cost. **Settings ‣ Nudge ‣ Budgets** sets what may be spent: tokens or
  cost, per person and per day, per company and per month; 0 is no limit. Past a limit, Nudge
  says it must wait until tomorrow, or until next month.
- **Nudge ‣ What Nudge Did ‣ Requests**: what people asked Nudge to do, with every step it took,
  the model that was used, tokens and cost. A request worked on in the background says so, and
  what Nudge was doing when it was last heard of; a manager can stop it.
- **Nudge ‣ What Nudge Did ‣ Changes**: every record created or changed, every button pressed:
  for whom, on which records, what was written, what the person was shown, and what they
  answered. Nobody can change or delete this log, administrators included, and a conversation
  in which Nudge acted can be archived but not deleted.

## API keys

Keys are stored in the database, readable by administrators only, and never sent to the browser.
They are stored as plain text, which is how Odoo stores its own secrets: protect database backups
accordingly. The Anthropic provider can instead read `ANTHROPIC_API_KEY` from the environment of
the server.

## Tests

```bash
# Python tests and browser tours
odoo-bin -d <test database> -u nudge --test-enable --test-tags /nudge --stop-after-init

# JavaScript unit tests
odoo-bin -d <test database> --test-enable --stop-after-init \
    --test-tags "/web:WebSuite.test_unit_desktop[@nudge]"
```

Tests never call a real provider. Manual checks: [docs/manual-test.md](doc/manual-test.md).

On Windows with Git Bash, prefix the commands with `MSYS_NO_PATHCONV=1`, otherwise `/nudge` is
rewritten into a file path.

## What is not there yet

- Acting without asking: every change waits for your yes. There is no mode that skips it.
- Deleting, printing reports, sending emails: Nudge archives or cancels instead of deleting,
  and a window opened by a button is opened on your screen for you to go on from there.
- A scanned PDF holds no text: Nudge asks for a photo of it, which a provider that sees images
  can read.
- Screens other than forms, lists and kanbans are seen by their name only.
- A queue of jobs: the background work runs in the scheduled actions of Odoo, one request at a
  time per cron thread.

## Known limits

- One answer at a time per user. An answer is cut after 90 seconds.
- In Agent mode the model is called 8 times at most between two of your answers, and a request
  takes 25 steps at most. Past that, Nudge tells what was done and what is left.
- Agent mode was tried with `gemini-3.5-flash-lite`, on Sales and Contacts, in English and in
  Arabic, in the foreground and in the background. A small model gets there, with a mistake
  corrected on the way now and then; it does not always say what it is about to do before it
  asks, and a customer it does not find, it may offer to create. Other apps were not tried.
- The messages of the background reach the browser through the bus of Odoo, which needs the
  websocket of Odoo to work: behind a proxy, `/websocket` must be passed through. Without it,
  what ended is seen when the panel is opened, not before.
- On Windows, the scheduled actions run in the same server as the pages: a piece of work in the
  background takes a thread while it lasts.
- A busy provider is asked again twice, after 1 and 3 seconds, before an error is shown.
- A conversation holds 120 messages, 240 when Nudge acted in it; then a new one must be started.
- Each answer being streamed occupies one Odoo worker until it ends.
- Free, small models make language mistakes, in Arabic especially, and sometimes point at
  less than they could. A stronger model answers better.
- Tested against the real Gemini API. The Anthropic provider has not been tried against the real API.
- Voice through the AI provider was tried with real recordings, in Arabic and English. Voice
  through the browser is tested with a stand-in for the microphone: it has not been tried with
  someone talking.
- A browser listens to one language at a time: English words in an Arabic sentence come out in
  Arabic letters. The AI provider writes them in Latin letters.
- A recording sent to the AI provider lasts 60 seconds at most, and its words come once you are
  done, not while you talk.
- Browsers give the microphone to pages served over https, or by the computer itself.
- Answers are read by the voices of the device. Without a voice for Arabic, answers in Arabic
  are not read: Windows offers one under Settings ‣ Time & language ‣ Speech.
- Cost is counted in the prices set on the provider; a provider without prices costs 0, and
  only tokens can then be limited. Budgets are checked before a call, not during it: the last
  answer before the limit can go over it by one answer.
- Prompt injection is tested against what the tools refuse and how content is framed
  (`tests/test_injection.py`). What a model does with an injected instruction that stays
  within its rights (say, writing a text the note told it to write) is up to the model, and
  the person who says yes.
- Dark mode is prepared but only takes effect on Odoo Enterprise; it has not been tested.
- The blur behind a guided step needs `backdrop-filter`; on a browser without it, the rest
  of the screen is only dimmed.
- The documentation is searched by English words: a question in Arabic is turned into
  English words by the model before the search, which costs one call and may miss.

The documentation of Odoo included in `data/odoo_docs.jsonl.gz` is © Odoo S.A., licensed
[CC BY-SA 4.0](https://github.com/odoo/documentation), read into text by
`scripts/build_odoo_docs.py`. Run the script again to take a newer documentation.
- Installed and tested on Odoo.sh (Linux, several workers, behind its proxy): the server
  tests pass there; the browser features were tried there by hand.

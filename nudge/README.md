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
[doc/user-guide.md](doc/user-guide.md), [بالعربية](doc/user-guide.ar.md).

## What Nudge does

| | |
|---|---|
| **Sees your screen** | The record you are on, its fields and their values, unsaved changes included, its buttons and tabs. Only when you send a message, and only what you are allowed to see. |
| **Knows Odoo** | The official documentation of Odoo comes with the module: every app of Community and Enterprise, and the developer documentation (ORM, fields, views, security, automations, QWeb, the JavaScript framework), 1067 pages searched by Postgres. For how standard Odoo works, or how something is done in code, Nudge reads the documentation before answering, and gives the link of the page. Nothing is fetched from outside. |
| **Counts and sums** | In Agent mode: how many, how much, which most, per what. Grouped by any field, a date by day, week, month, quarter or year; sum, average, minimum, maximum of any number. Through the ORM, as the person: what they cannot open is not counted. |
| **Computes with Python** | Off by default; an administrator switches it on. The agent runs Python in the sandbox of Odoo (the one of the server actions), as the person: every record passes their rights. The code reads only: it runs in a savepoint rolled back when it ends, so what it writes is discarded, and a change still goes through the yes of the person. No import, no sudo, no cursor, no private attribute, no model of the system. Every run is in the log. |
| **SQL for administrators** | Off by default. The group Settings, and only those allowed in every company, may ask the agent for one SELECT: a join or a report no other tool gives. No change, no lock, no comment, one statement; the tables of Odoo they may read, never users, parameters or providers; 50 rows, 10 seconds, in a savepoint rolled back when it ends. Every query is in the log. |
| **Points at things** | A question asked with the screen shared asks first: on the screen, or in writing (each time, or once for all in the preferences). On the screen, its pointer goes to the button, field or tab to use and presses it, for show; a box with a short text opens, and the rest of the screen is blurred and dimmed, the element clear. Step by step: the next step comes when you have done the current one. A field hidden in a closed tab is reached through the tab. |
| **Finds products** | `/product` and a name, a description, a code or a size, in any trade: the names, codes, barcodes, descriptions, variants, categories and supplier names of the products the person may see; sizes read with their units and compared across units, a misspelt word still found by its letters. Cards with the picture, the price, the cost and the stock where the person may see them, and why each came. Needs no AI; a picture with it is described by the model. |
| **Commands: where to look** | A word after a slash says where to look; typing `/` lists those open to the person. Without any AI, before a provider too: `/screen`, `/setting`, `/doc`, `/file`, `/find` (any record by name or reference), `/product`, `/stock`, `/rules` (Developer mode), `/sql`, `/help`, each in English and in Arabic. With the AI, a few tools and a short brief only, without the rest of the conversation: `/count` for a number, `/explain` for a field, a term or the screen. |
| **Many records, one yes** | "Confirm all the draft quotations of Raghad": one plan, one card. Tried first in a savepoint and undone, so the card says how many records go through and why the others do not; yes to all, yes to those that go through, or no. Each record in a savepoint of its own; beyond 20 records, in the background by pieces. 200 records per plan by default, set by the administrator, 1000 at most. Undo writes back what was written, unless a record was changed since; what a button did is not taken back, and the card says so. |
| **Works by itself** | Scheduled tasks, asked for in words: "every morning at 8, the overdue invoices", "when a purchase order above 50,000 is created, compare its prices". Shown on a card first. Run as their owner, in the background, reading only: a change they find needed waits for the owner's yes. The records are counted first, so nothing is spent when nothing matches or nothing is new. Answers in the top bar, the task's conversation and by email. 5 tasks per person by default, set by the administrator. |
| **Morning digest** | One click in the preferences ("Set up my digest") makes a daily task per person, at the hour and in the time zone they choose. The numbers are counted by Odoo with their own rights, only for the apps installed and the groups they belong to: sales (new leads, quotations, orders to deliver, the week against the week before), purchases, accounting (overdue invoices, payments, bank lines), inventory, projects, people, and an overview first for managers. The AI, when there is one, only writes a few sentences around the numbers in their language; without it, or in private mode, the same numbers arrive alone with the same "Show me" links. Top bar and email like any task; a preview button reads today's now. |
| **Website assistant** (companion module `nudge_website`) | A chat bubble on the website and in the portal: visitors with no login are answered only from the published products, the company files marked public and the published pages; customers who are logged in also about their own orders and invoices, with their own rights. Rate limit per visitor, a daily budget, private mode obeyed, no AI needed for products and files, and unanswered questions reach the Business Notes. |
| **@Nudge in the chatter** | With Nudge: Discuss installed from Apps, "@Nudge summarise this" in the chatter of any record or in a channel: answered as an internal note, as the person who asked, from the fields every reader sees, the records it links to and the last messages, nothing looked up elsewhere, nothing changed. |
| **Without an AI provider** | These work before any key is set up, and with private mode on: remembered answers, explanations of the screen, rule tasks, private mode itself, recorded tours, the health check, the compliance checklists, the first setup, the import matcher, common requests, search by meaning, and, new in release 2, the first-week coach, undoing a change, the check before an upgrade, shared recipes and the marketplace, recurring questions, My Nudge Home, unusual figures, warnings before saving and the debugger. See [Works without an AI provider](#works-without-an-ai-provider). |
| **Your Odoo in Claude Desktop** | With Nudge: MCP Server, Claude Desktop, ChatGPT and other MCP assistants look up records and the documentation as the person who holds the key, and prepare changes that are made only after the assistant confirms with a token. Off until an administrator turns it on. |
| **Your Odoo on WhatsApp and Telegram** (companion module `nudge_messaging`) | Link your phone with a one-time code from your preferences, then ask from WhatsApp or Telegram: questions are answered in the chat with an Open in Odoo link, and a change is only prepared (a quotation, a task, a photographed bill): the reply carries a link and you say yes inside Odoo. Voice notes when the provider can listen, private mode and budgets obeyed, every message a request in What Nudge Did. Installs Nudge: MCP Server too (switched off); voice notes need `ffmpeg` on the server. Off until an administrator turns it on; Odoo 18 for now. |
| **Document to draft record** | Attach a PDF, a picture, an Excel or CSV file, or paste an email, and ask for a vendor bill, a customer invoice, a quotation, a purchase order, a lead or a contact. The AI reads the file once; Odoo finds the contact, products, taxes and currency and computes the totals again. The card shows the whole draft and its differences; a yes creates it as a draft, never posted, with the file attached. Undo archives the record, or cancels the draft, and never deletes. A scanned PDF is refused with a word about it; a picture needs a provider that can read images (another provider copies it out first when the chosen one cannot). |
| **Summary of a record's history** | "Summarise this record" in the panel, or `@Nudge summarize` in the chatter (Nudge: Discuss). It reads the chatter, activities, files and the records around it, as the person, and answers with a dated timeline, open points, money, last contact and next step, with Show me links. Kept 15 minutes, unless the chatter changes. |
| **Draft a reply** | On a record that has a customer's message, **Draft a reply** (tone Formal, Friendly or Short) or `@Nudge draft a reply` in the chatter. The reply is written in the customer's language from the messages the customer can read, the record and the company files, and opens in the email window (Nudge: Emails) with the signature of the person. They read it, change it and press Send: Nudge never sends it. |
| **The AI over a selected list** | Select records in a list: **Do this for the N selected**. Classify into a field of values, summarise into a text field, translate, or fill a field from the others. Nudge tries a sample of five, shows what it proposes and what the rest costs; one yes sends the others to the background in batches, **Stop** keeps what went through, and one undo takes it all back. |
| **Website and portal assistant** | With Nudge: Website Assistant, a bubble on the website answers visitors from the published products, the files marked "Public on the Website" and the published pages, and logged-in customers also about their own orders and invoices. A limit per visitor, a daily budget, a hand-over to Live Chat or the contact form. |
| **Browser speech in private mode** | The company switch **Allow Browser Speech in Private Mode** (off by default) lets the browser dictate and read aloud when no provider on your own server listens. Chrome and Edge may send the sound to their maker; Nudge cannot prevent it. |
| **Translate a module** | An administrator picks an installed module and a language (**Settings ‣ Nudge ‣ Translations**). Nudge lists the untranslated terms, asks the provider in batches that keep placeholders and the words of the company glossary, and shows a grid to accept or reject each term before anything is saved. |
| **Charts in the chat** | Bars, a line or a pie drawn from the figures the person may read, asked for in words of any dialect or chosen by the AI, with **Open as report** and **Download PNG**. |
| **Designed reports** | In Developer mode, the printed quotation, invoice, delivery slip and payslip take the company identity, as an extension view of the standard template shown with its difference before saving and undone in one step. |
| **Approval flows from a sentence** | "A purchase order above 50,000 needs the finance manager" (or the same in Arabic) becomes, after one yes in Developer mode, the fields, the rule that stops the confirmation, the buttons and the activity for the approvers. Works on Community; with Studio installed its approval rules are used instead. |
| **Website Builder** (companion module `nudge_builder`) | Six questions, three directions of the home page to compare, then unpublished pages made of Odoo's own snippets, with 12 sector templates in Arabic and English, an optional shop part and a Site Check. Nothing is published or deleted until you say so. Needs Website; Odoo 18 for now. |
| **Mail agent** (companion module `nudge_mail`, Nudge: Emails) | Sorts the mail of a shared mailbox into proposals a manager approves (a lead, a ticket, a vendor bill, a reply); a rejection teaches a rule that is used afterwards without AI. Off per company until switched on. |
| **Report in any shape** | A chart, a pivot or a list asked for in words of any dialect, shown on a card and, on yes, kept in **Nudge ‣ My Reports**, a menu only its owner sees. The period moves with the day (this month, last 30 days). |
| **Explain this report** | The Explain button on a saved report writes short sentences from its numbers, with no AI: the total, who drives it, the movers against the previous period, rows that changed sign, empty columns. Read with the rights of the person who asks. *Add a summary* (only with a provider that private mode and the budgets allow) asks for one paragraph from those sentences alone, in the background; a number it adds that is not in the sentences is flagged. |
| **Meeting audio summary** | Attach or record a meeting (m4a, mp3, ogg, wav, webm, up to 10 MB) and ask for the summary. Nudge writes the summary, decisions and action items; tasks, activities, calendar events and notes are offered as drafts, each with its own yes. The recording is forgotten after one hour and the transcript is kept nowhere. Needs a provider that accepts audio, or the words the browser heard while recording when that is allowed. |
| **Module scaffold** | In Developer mode, a complete Odoo module for 17, 18 or 19 from a described need: a spec card first, checked in memory after the yes, downloaded as a ZIP. Nothing is written on the server or installed. |
| **Mobile-first panel** | Under 600 px, with `?mobile=1` or in the Odoo app, the panel is a full-screen sheet with a hold-to-talk button and up to five quick tiles (clock, photo of a bill, my tasks, ask about this record, record a note) that the company can switch off in Settings. A tile of an app that is not installed is not shown. |
| **Goes away when asked** | Nudge: On / Off in the person's own menu: the avatar, the button and the shortcuts, for them only. |
| **Takes you there** | "Take me to the quotations" opens the screen, among the menus you can open. |
| **Knows this database** | Which modules are custom and what they are for, which fields they add to standard screens, how deliveries, approvals and other options are set. Read again when modules change. |
| **Listens** | Hold the shortcut and ask, or press the microphone. In Arabic and in English, names of buttons in the middle of an Arabic sentence included. |
| **Types what you dictate** | Click in a text field, press the dictation shortcut, talk: the words go where your cursor is. Or tell Nudge what to write in which field. Nothing is saved until you save. |
| **Reads its answers** | To those who ask for it, when the device has a voice for the language. Off unless turned on. |
| **Does the work** | In Agent mode: "Create a quotation for Raghad with 3 office chairs, then confirm it." Nudge finds the customer and the product, shows what it is about to create, and waits for your yes. Then the same for the confirmation. |
| **Works while you are away** | Press the clock instead of Send: the request is worked on by the server while you do something else. A message tells you when Nudge needs your yes, and when it is done, from any screen. |
| **Follows your words** | Business notes written by your managers ("we call quotations offers") open every conversation. |
| **Speaks your language** | Arabic and English, right to left, whatever the language of the interface. |
| **Stays where you put it** | Drag the avatar anywhere; pick its colour in the preferences. |
| **Stays within budget** | Tokens and cost per person and per day, per company and per month, set by the administrator. Every call to the provider, speech included, is in the ledger. |

## Works without an AI provider

These do not call an AI provider, so they work before a key is set up, when the provider fails,
and in private mode. Nothing is spent, and every change still waits for a yes on the usual card.

| Feature | What the person does, and what happens |
|---|---|
| **Answer memory** | Press the thumbs-up on an explanation. A manager approves it in **Nudge ‣ What Nudge Knows ‣ Remembered Answers**. From then on the same question of someone else, who may see what it is about, is answered at once, marked as a remembered answer, with a button **Ask the AI instead**. The usage report shows it with zero tokens and zero cost. An answer that changed data or quoted values of the database is never kept. With no provider, the best remembered answer above the floor is offered above the search. **Settings ‣ Nudge ‣ Answer Memory** switches it off and sets **Confidence Required** (0.75 by default). |
| **Explainers** | `/why` (`/لماذا`): why this form cannot be saved: the required fields, including the conditional ones, the refused values, the last message, locked fields, missing rights, the company. `/field customer` (`/حقل`): what a field is, in words. `/here` (`/هنا`): what this screen is, its state and its buttons. Each line is shown with the pointer. They are read from Odoo's own metadata and the form as it is, with what was typed, and they are offered in place of the error when there is no provider or it fails. |
| **Rule tasks** | **Nudge ‣ What Nudge Did ‣ Scheduled Tasks**, **Done By: Rule (no AI)**. Pick the record type (a model you may read), a filter, **Measure** (Count, Sum or Average of a number field), an optional grouping, and when to report: **Always**, **Above a Threshold**, **Below a Threshold** or **Changed Since the Last Run**. It runs read-only as its owner and answers in the top bar and by email, with a link that opens the filtered list. It counts in the same limit of tasks per person. |
| **Private mode** | **Settings ‣ Nudge ‣ Privacy ‣ Private Mode: Never Send Data to an External AI Provider**, per company, and the tick **Runs on Our Own Server** on a provider (Ollama on your own machine, for instance). The server refuses every call to a provider that is not ticked, before anything leaves: answers, pictures, voice, connection tests and the embeddings of Nudge for Odoo AI. Providers on your own server are preferred, and a lock shows in the panel. Quick search, slash commands, tours and remembered answers keep working. Voice typing by the browser still sends the sound to the maker of the browser: choose the AI provider as the engine of the voice. |
| **Recorded tours** | A Nudge manager chooses **Record a tour** in the user menu, goes through the task by clicking, and each step is named by its menu, button, field or tab, never by its place on the page. Anyone replays it with the pointer: `/tour create a quotation` (`/جولة`), or from the search of the panel. A step this database does not have is skipped with a note. Tours are edited under **Nudge ‣ Configuration ‣ Tours**. Eight come ready: Create a quotation, Confirm a sales order, Create a contact, Create a product, Receive a purchase order, Validate a delivery, Register a vendor bill, Register a payment; each shows only where its app is installed. The AI may show a stored tour instead of improvising steps. |
| **Health check** | A Nudge manager types `/health` (`/فحص`), or opens **Nudge ‣ Health Check ‣ Check the Database**. Eleven small checks run as that person and change nothing: duplicate contacts, duplicate products, product data, accounting setup, taxes, negative stock, users and rights, records of the wrong company, scheduled actions that fail, the email queue, orphan files. The report gives each finding a severity, a count and a **Show me** button, and says what changed since the last report. It also runs every week as the manager who asked; the last ten reports of a company are kept, under **Reports**. |
| **Compliance checklist** | **Nudge ‣ Health Check ‣ Compliance** lists the checklists for the country of the company, each with a **Run**: United Arab Emirates, Saudi Arabia, Qatar, Kuwait, Bahrain, Oman and Egypt. Deterministic checks, no AI, nothing changed: VAT taxes at the rate of the country and on the products, tax numbers (TRN, VAT) on the company and the customers, the invoice layout, e-invoicing modules and fields (ZATCA, ETA), Arabic, WPS readiness and employee fields. Each finding has a severity, an explanation with the official reference in English and Arabic, and **Show me**; results are kept with the health reports. It is a checklist, not legal advice: confirm with the authority (it says so on the screen, in every pack and on every finding). A country is a JSON file (`data/compliance`, or `data/nudge_compliance` in any module), refused at load with a clear error if it names an unknown check. |
| **First setup** | On a database nobody has set up (one company with the default name, no chart of accounts, few users) an administrator is offered **Set up this database with me**, also under **Settings ‣ Nudge ‣ First Setup** and by sentence in English or any Arabic dialect. Nudge asks one thing at a time (company, country, currency, languages, time zone, logo, apps for the kind of business, other companies, warehouses, users with a role, fiscal year, number formats) and shows a card with the exact changes before each; the country card names the fiscal localisation module (`l10n_ae`, `l10n_sa`, `l10n_eg`...) that Odoo loads its chart of accounts with. Users get an invitation email only if you ask; what was made is archived by Undo, never deleted; the outgoing mail server is only pointed at. With no provider the answers are read by rules. |
| **Import matcher** | Attach a CSV or Excel file and type `/import` (`/استيراد`), or press **Check a CSV or Excel file, without AI**. The columns are matched to the fields of a model by name, label, usual names, spelling and what the values look like. Correct the matching on the card; Nudge tries the file with the dry run of Odoo's import and lists the errors by row and column; nothing is saved. Then it hands you a corrected CSV for the standard Import screen. |
| **Common requests** | Open a screen, find a record, filter a list, count and sum, create a contact, product, quotation, purchase order, task, lead, ticket or time off, change one field, press a button: understood by rules in Arabic, any dialect (Modern Standard, Gulf, Egyptian, Iraqi, Levantine, Maghrebi, Sudanese, Yemeni), Arabizi and English. Usage rows are labelled **Understood Without AI**, with zero tokens. A name that fits several records is a choice, never a guess. **Settings ‣ Nudge ‣ Common Requests ‣ Try to understand common requests without AI first**, on by default. |
| **Search by meaning** | **Settings ‣ Nudge ‣ Local Search Model ‣ Download** fetches multilingual-e5-small (about 135 MB, into the filestore) to run on the CPU of the server; it needs `onnxruntime` and `tokenizers`. The documentation, the company files and the remembered answers are then found by meaning as well as by words, so a question in Arabic finds an English page. It does not search the records of other models (partners, products...), and it answers nothing: it only finds. Nothing leaves the server. |
| **Your first week** | A card in the panel shows a new person (first 14 days, or put in training by a manager) how many of the key screens of their role they opened and the tours still to take; **Nudge ‣ Training** shows the managers who is where. Only the screens opened are kept. A company switch in Settings turns the coach off. |
| **Who changed this, and undo it** | `/changes` (or "who changed this price?" in any dialect) lists the tracked changes of a record, newest first. **Restore the old value** puts one field back, with your rights, after a yes; nothing is deleted. A state, a posted entry or a done stock move is never put back, and the answer says why. The restore tools themselves are in Agent mode. |
| **Check before an upgrade** | **Nudge ‣ Health Check ‣ Before an Upgrade**: an administrator picks the series (17 to 18, 18 to 19, 19 to 20) and gets a read-only report of what will break or needs a decision: Studio objects and the logic they hide, renamed or removed fields, `attrs`/`states`, custom modules without a migration. Some entries say "confirm on staging"; the 20.0 list holds generic checks only. |
| **Shared recipes** | A manager exports tours, rule tasks, presets, command words and approved answers as one checksummed file (**Nudge ‣ Configuration ‣ Recipes**) and imports it, after a review step, on another database. |
| **Recipe marketplace** | **Nudge ‣ Configuration ‣ Recipes ‣ Marketplace** lists the recipes of public `index.json` indexes (format in `doc/recipes-index.md`), HTTPS only, each download checked against the checksum of the index. It is not an AI call: the only thing the index sees is your server's address. The default source is off and no index is fetched by itself until an administrator enables a source and, for the weekly refresh, ticks it. |
| **Questions that keep coming back** | Questions asked are counted by meaning, in any dialect, with no AI and no person kept. Every week the managers are offered the ones asked often that no remembered answer or tour covers: keep an answer, record a tour or ignore (a short message on the bus, three lines at most). |
| **My Nudge Home** | The first screen of the Nudge app, for every employee: the numbers of the apps they may use (sales, purchases, accounting, inventory, projects, people), each with its trend against last week and **Show me**, counted as the person every five minutes. Community and Enterprise. |
| **Unusual figures** | The health check and a daily task kind find payslips, bills, invoices, stock moves, expenses, discounts, purchase prices and attendance far from the median of their group, with robust statistics. The sensitivity is in **Settings ‣ Nudge ‣ Unusual Figures**; the comparison is told in words, with **Show me**. |
| **Warnings before saving** | A small strip under the title of a form warns, without blocking, about duplicate contacts and products, tax-number and phone formats, email syntax, a price under cost, a bill number already used, a deadline in the past, and the fields the company requires. Each warning is switched per company in Settings and muted per person; **Ask Nudge about this** is the only part that uses AI. On by default: duplicates, the tax-number format, a bill number already used and the required fields. |
| **Why did it fail?** | In Developer mode, `/debug` reads an automation, a server action or a scheduled action: its definition, its logged errors, the missing fields and models and the rights. With a provider Nudge proposes the fix as a card showing the difference on the code; the original is kept and put back on request. |
| **Charts without AI** | A chart asked for in words of any dialect ("sales by month as a chart") is understood by rules, drawn from the figures the person may read and opened as a report, with no provider. |

## Setup wizard

Settings > Nudge > First Setup > *Choose a provider and see the cost* (or Nudge > Configuration > Setup Wizard; system administrators only) opens a six-step wizard: users and mode, provider, key and test, monthly estimate, budgets, enable. It is reachable before any provider exists, and "Rules only" goes through to the end with no provider. The key typed there is saved on the provider record (readable by system administrators only), is not stored in the wizard's table, and is never logged. The estimate is people x messages per day x working days x model calls per message x the measured prompt of the mode (Talk 10.6k, Agent 22.9k, Developer 36.0k tokens) at the prices of `data/nudge_price_table.json`, whose date is shown; the prices are an estimate and can be edited in the wizard or in that file. Budgets are written to the same settings as Settings > Nudge > Budgets.

## Install

Requirements: Odoo 18 (Community or Enterprise); Nudge exists for Odoo 17 and 18 too, each on its
own branch (the 18 branch has no Nudge for Odoo AI; the 17 branch has neither it nor Nudge for Studio and the two
spreadsheet companions). No Python package is required. Some are optional (see [Optional packages](#optional-packages)): `anthropic` for the
Anthropic provider, `onnxruntime`, `tokenizers` and `numpy` for the search by meaning, `phonenumbers` for the phone check, and `ffmpeg` for audio. For the
Anthropic provider: Claude needs the `anthropic` Python package on the server (`pip install anthropic`;
on Odoo.sh, a line `anthropic` in the `requirements.txt` at the root of the repository). Nudge installs
without it; **Test Connection** on the provider says when it is missing, and what to do.

1. Add this folder to the `addons_path` of Odoo.
2. Update the apps list and install **Nudge**.

## Optional packages

Nothing below is required to install Nudge, and none is declared as an `external_dependencies`: without a
package the feature that needs it says what is missing and the rest works.

| Package | Enables | Without it |
|---|---|---|
| `anthropic` | The Claude (Anthropic) provider | **Test Connection** on the provider says it is missing |
| `onnxruntime`, `tokenizers`, `numpy` | Search by meaning on the server's own CPU | Search falls back to words |
| `phonenumbers` (with Odoo's phone validation) | The phone-format warning before saving | The check says it cannot tell and does nothing |
| `ffmpeg` (a program on the server) | Voice notes and meeting audio in formats other than WAV | The audio is refused with the reason |

## What is on and what is off

- **On after install**: remembered answers (nothing is served until a manager approves one, and a use costs zero tokens), common requests understood by rules before the AI, warnings before saving (duplicates, tax-number format, a bill number already used, the required fields), the new-user coach for the first 14 days (a company switch turns it off), and the weekly count of recurring questions (no AI, a short message to the managers).
- **Off until someone turns it on**: the weekly health check (until a company names the manager who runs it), the Mail agent, WhatsApp and Telegram, the MCP server, private-mode speech, the Website Builder (a module of its own), the default marketplace source and the weekly refresh of any source.
- **Only when someone asks**: unusual figures, the compliance checklists, the check before an upgrade, the module scaffold, meeting audio (the recording is forgotten after one hour).
- **No schedule calls an AI provider on its own**: scheduled work is what people created (a task, a digest) or runs on the server without a provider (the search index, the recurring questions, the marketplace).

## Editions

- **Community and Enterprise**: Nudge, Nudge: Emails, Nudge: Discuss, Nudge: Spreadsheets, Nudge: MCP Server,
  Nudge WhatsApp & Telegram and Nudge: Website Assistant. Everything in the table above, except what is marked Enterprise below, works on
  Community: the pointer, Agent mode, Developer mode, the commands, the tasks, the spreadsheets.
- **Enterprise only**: Nudge for Studio (`web_studio`), Nudge for Odoo AI (`ai`) and Nudge: Spreadsheets
  in Documents (`documents_spreadsheet`), since the apps they bridge are Enterprise; and dark
  mode, which Community does not have.
- **Companion**: Nudge: MCP Server (`nudge_mcp`, Community and Enterprise) lets Claude Desktop, ChatGPT and other
  MCP assistants use the tools of Nudge with the rights of the person whose key they hold; see its README.
- **Companion**: Nudge: Website Assistant (`nudge_website`, Community and Enterprise, needs Website and Portal) puts a
  chat bubble on the website and in the portal; see its README. Both companions are off until an administrator
  turns them on, and exist for Odoo 17, 18 and 19.
- **Companion**: Nudge: Website Builder (`nudge_builder`, Community and Enterprise, needs Website) interviews the person and builds
  unpublished pages from 12 sector templates; see its README. Nudge WhatsApp & Telegram (`nudge_messaging`) answers from
  linked phones and installs Nudge: MCP Server with it (switched off); see its README. Both are for Odoo 18 for now.

## Security

Nudge acts as the person, never beyond them: every tool runs with their rights, their
record rules and their companies; the models that hold how Odoo works and the fields
that hold secrets are closed whatever the rights. What leaves the database, who may do
what, the switches an administrator holds, what is written down, and the known
trade-offs are in [doc/security.md](doc/security.md).

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

### A free model for the questions, a stronger one for the work

**Settings ‣ Nudge ‣ Agent Provider / Developer Provider**: the default provider answers Talk, the
commands and @Nudge; the provider of the work answers Agent mode, the scheduled tasks and
Developer mode (which falls back on the Agent's). Typically Ollama on your own machine for the
questions, Gemini or Claude for what Nudge does and builds.

A free tier counts the tokens of each minute, and a request of Agent or Developer mode reads tens
of thousands of them a round (the instructions, the tools, what was read). When the provider says
how long to wait, half a minute or less, Nudge waits and asks again by itself; past that, the
person is told to try again in a minute. A team that works with Nudge all day needs a paid key.

A small model (the essential tools) reads short instructions, and every question goes with what
Nudge found for it by itself: the screens whose names hold its words, in Arabic or English, the
pages of the documentation about them, the passages of the company files. It is told the language
of its answer, and to say "I'm not sure" rather than invent. Asked to be shown, a model that wrote
the steps has them pointed at: the buttons, fields and tabs its answer names are matched against
the screen. Asked to be taken somewhere and taken nowhere, the person is offered the screens Nudge
found, with the pointer and Open.

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

Nudge knows Ollama when it answers: Fetch Models shows which models use tools, see pictures, and
how much context they take; the essential tools are chosen for a small model; Test Connection
reads the context the server gives the model and says when it is too small, and **Prepare for
Nudge** makes a copy of the model with enough context (Ollama gives 4096 tokens on a card under
24 GB, and cuts what does not fit without a word). When Ollama is stopped or a model is missing,
Nudge says what to run. The whole guide, with a reverse proxy for Odoo.sh: [doc/ollama.md](doc/ollama.md)
([العربية](doc/ollama.ar.md)).

Nothing goes out: questions, screens, files and what the agent reads stay on that server.
Speech stays with the browser, as Ollama does not turn speech into text.

### Before any provider

Without a provider, the panel offers a search instead of a conversation: the screens the
person can open, the settings of the apps (for administrators) and the official documentation,
by a few words; the pointer shows the way to a screen or a setting found. No AI is asked, and
nothing leaves the database. The conversation takes over the moment a provider is ready.

### Search by meaning, on your own server (optional)

An administrator may turn on a small model that runs on the CPU of the Odoo server, with no AI
provider: Settings > Nudge > Local Search Model > Download. It needs the Python packages
`onnxruntime` and `tokenizers` (`pip install onnxruntime tokenizers`; on Odoo.sh, in
`requirements.txt`); torch is not needed. The model, multilingual-e5-small (int8, MIT licence), is
downloaded from https://huggingface.co/Xenova/multilingual-e5-small into the filestore of the
database (about 135 MB, checked by size and SHA-256), never into the module. A scheduled action
then indexes the documentation, the company files and the remembered answers, a few minutes at a
time, and goes on where it stopped. The searches then re-rank their keyword hits by meaning, and
find a page when the question shares no word with it (a question in Arabic about an English page).
Without the packages or the model nothing changes. Nothing leaves the server, so private mode does
not stop it. The records of other models (partners, products...) are not searched by meaning.

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

- **Ask for what you want done**: "Create a contact for Raghad at your company", "Set the customer
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
have on the form cannot be pressed. It never deletes, whoever asks. Users, groups, access rights,
companies and the settings of Odoo are closed by default; an administrator can switch on **Nudge for
Administrators** (Settings ‣ Nudge), and from then on those changes go through the same yes-cards,
for administrators, one at a time.

### Common requests without AI

Before the AI provider is asked, Agent mode tries to understand the usual requests by rules: open a
screen, find a record, filter the list on screen, count and sum, create a contact, a product, a
quotation, a purchase order, a task, a lead, a ticket or a time off, change one field of a record,
press a button of the record on screen. It reads Modern Standard Arabic, the Gulf, Egyptian, Iraqi,
Levantine, Maghrebi, Sudanese and Yemeni dialects, Arabizi and English, with Arabic-Indic or Latin
digits and dates such as today, last month or a month name. No token is spent, and every change
still waits for a yes on the same card, with the same rights, log and undo. A name that fits several
records is a choice, never a guess. What is not understood goes to the AI as before; with no
provider, or in private mode, Nudge says what it understood and what it can do without AI. The
setting “Try to understand common requests without AI first” (on by default) turns it off, and the
usage report counts these requests with no tokens. The words are in `tools/noai_commands/lexicon/`,
one file per group of dialects, easy to extend.

Typing fast does not send the sentence to the AI. A word the lexicon does not know is read as the word it
was meant to be: a letter more, less, swapped or changed for a near one (`opne`, `invocies`, `موضف`; one letter
for a word of 4 to 7 letters, two from 8), or Arabic written in Latin letters and digits (`3omala2`, `a3mel`,
`mntgat`: 3, 5, 6, 7, 8, 9 and the pairs sh, kh, gh stand for Arabic letters, matched on the consonants of
the lexicon). Numbers, dates, addresses, names in quotes, capitalised names and the value a field is set to
are never touched. A correction always costs confidence, so a typo never makes a sentence surer than the same
sentence typed right; a sentence that changes something and needed a slip corrected, or a word that may mean
two things, stays under the threshold and is only offered. The parse carries `corrections` (what was read as
what) and, under the threshold, `candidates`: the sentences it may have meant, each with the corrected text, for
a “Did you mean” offer. A parse between 0.4 and 0.7 is offered the same way and is never acted on.
### The prompt library

Requests worth asking again are kept as prompts (Nudge ‣ Prompts). A prompt has a title and a text in
English and in Arabic, the mode it is sent in, the screens (models) and the apps it fits, and its
variables: write `{{customer}}` in the text and Nudge asks for it before sending. A variable is a
text, a number, a date, a yes/no, a choice or a record (picked with the rights of the person, so only
what they may read is offered). A prompt is private, for a team (a group), or for the company; only a
Nudge manager shares one. 50 ready prompts come with Nudge (talk, agent, developer, industries, roles,
websites) and are archived, never deleted, when you do not want them.

- **Chips.** Above the message box Nudge suggests the prompts of the model of the screen you are on,
  then the general ones. A prompt whose apps are not installed, or whose mode you may not use, is not
  suggested. A chip with variables, or from a colleague or a recipe, opens a small form that shows the
  text first; you send it, nothing is sent by itself.
- **Keep as a prompt.** The bookmark button next to the paperclip keeps what you typed, or your last
  message, and proposes the dates, numbers and names in it as variables. For a prompt shared with others
  every one of them becomes a variable: no data of a record is kept in a shared prompt.
- **Safe by construction.** The text of a prompt is the message of the person who chooses it: it is never
  put into the system prompt, never run on import, and the values are checked and cannot add
  placeholders of their own. The filled text goes through the same cards, log and undo as anything typed.
- **No provider.** Before a provider is set up, the panel offers the starters that the rules of Nudge
  understand (marked “answered without AI”); one the rules cannot read is not offered.
- **Sharing.** Prompts are an item kind (`prompt`) of the recipes: export them, import them, publish them
  in an index (kind `prompts`). An import shows each text first, brings it in as a company prompt and
  never replaces a newer version. A recipe item of a kind this Nudge does not know is skipped with a
  warning; the rest of the file is imported.

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
- **Nudge ‣ What Nudge Knows ‣ Business Notes**: how your business names things and works. A note (or an approved lesson) belongs to a company, or to all of them when the company is empty, and may be limited to some groups: a conversation is told only the notes of its current company and of the groups of its person. The notes sent hold at most 3,000 characters; a note left out by the limit is marked in the list (column Not Sent) and the log says so, so that nothing is dropped unseen.
- **Nudge ‣ Conversations**: who asked what, tokens and cost.
- **Nudge ‣ Usage**: every call to an AI provider, conversation or speech, by person and by
  day, with tokens and cost. **Settings ‣ Nudge ‣ Budgets** sets what may be spent: tokens or
  cost, per person and per day, per company and per month; 0 is no limit. Past a limit, Nudge
  says it must wait until tomorrow, or until next month.
- **Settings ‣ Nudge ‣ Budgets ‣ Answers at the Same Time**: one answer holds one worker of the
  server while it streams, so the answers to everybody together are capped; the default cap is
  the workers of the server less two. Past it, Nudge asks the person to try again in a moment.
  Work in the background does not count.
- **Nudge ‣ What Nudge Did ‣ Requests**: what people asked Nudge to do, with every step it took,
  the model that was used, tokens and cost. A request worked on in the background says so, and
  what Nudge was doing when it was last heard of; a manager can stop it.
- **Nudge ‣ What Nudge Did ‣ Changes**: every record created or changed, every button pressed:
  for whom, on which records, what was written, what the person was shown, and what they
  answered. This log cannot be changed or deleted from the interface, and a conversation in
  which Nudge acted can be archived but not deleted.

## API keys

Keys are stored in the database, readable by administrators only, and never sent to the browser.
They are stored as plain text, which is how Odoo stores its own secrets: protect database backups
accordingly. The Anthropic provider can instead read `ANTHROPIC_API_KEY` from the environment of
the server.

## Fixed

- A field added after the name, or after another field of the title block, now shows in a group right below the title, with its label.
- In Talk mode with the screen shared, Nudge reads the total of an order or invoice: the totals box and the first 50 lines.
- A list asked for in a scheduled task that runs in the background is answered in the message, as a table with an **Open the list** link, not on a screen that is not there.
- A plan on more than 20 records in the background saves its progress batch by batch, goes on after a worker died, and ends where the person stopped it, keeping what went through.

## Uninstall

Approval flows, automations, tours and reports that Nudge made for a company stay by design: they belong to
the company (list them under Settings ‣ Technical ‣ External Identifiers, module `nudge_custom`). The menus of
saved reports go with the Nudge menu; their window actions, the search vectors and the model downloaded for
the search by meaning (about 135 MB in the filestore) are not removed with the module, and can be deleted by hand.

## Tests

```bash
# Python tests and browser tours
odoo-bin -d <test database> -u nudge --test-enable --test-tags /nudge --stop-after-init

# JavaScript unit tests
odoo-bin -d <test database> --test-enable --stop-after-init \
    --test-tags "/web:WebSuite.test_unit_desktop[@nudge]"
```

Tests never call a real provider. Manual checks: [doc/manual-test.md](doc/manual-test.md).

On Windows with Git Bash, prefix the commands with `MSYS_NO_PATHCONV=1`, otherwise `/nudge` is
rewritten into a file path.

## What is not there yet

- Acting without asking: every change waits for your yes. There is no mode that skips it.
- Deleting and sending emails by itself: Nudge archives or cancels instead of deleting, and a window opened by a
  button is opened on your screen for you to go on from there. (Designed printed reports and the Mail agent exist now; a mail is never sent without a yes.)
- A scanned PDF holds no text: Nudge asks for a photo of it, which a provider that sees images
  can read.
- Screens other than forms, lists and kanbans are seen by their name only.
- A queue of jobs: the background work runs in the scheduled actions of Odoo, one request at a
  time per cron thread.

## Known limits

- One answer at a time per user. An answer is cut after 90 seconds.
- A provider that answers "busy" (limit reached, overloaded) is left alone for the time it asks,
  at most a minute, by every request of the same server: the next questions are told at once
  "The provider is busy, try again in N s". Waits and retries never go past the deadline of the
  answer, and Claude's SDK does not retry on its own.
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
- A provider that takes long per call (a free tier, with the many tools of Developer mode)
  may not finish in 90 seconds: Nudge says it ran out of time and goes on when asked, and no
  round is started with less time left than it would take. A lower reasoning effort on the
  provider, or the clock (the background has its own time), helps.
- A conversation holds 120 messages, 240 when Nudge acted in it; then a new one must be started.
- Each answer being streamed occupies one Odoo worker until it ends.
- Free, small models make language mistakes, in Arabic especially, and sometimes point at
  less than they could. A stronger model answers better.
- Tested against the real Gemini and Anthropic APIs. With Anthropic, a key that is not scoped to a
  workspace (a user key) needs the **Workspace ID** on the provider form; Test Connection says so.
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
- What Developer mode builds (rules, server actions, email templates, scheduled actions, fields,
  views, reports) is registered under the xml-id module `nudge_custom`, so it stays when Nudge is
  uninstalled: list it under Settings ‣ Technical ‣ External Identifiers, filtered on the module
  `nudge_custom`, or, while Nudge is there, with `/rules` and the model.
- Dark mode is prepared but only takes effect on Odoo Enterprise; it has not been tested.
- The blur behind a guided step needs `backdrop-filter`; on a browser without it, the rest
  of the screen is only dimmed.
- The documentation is searched by English words: a question in Arabic is turned into
  English words by the model before the search, which costs one call and may miss.

The documentation of Odoo included in `data/odoo_docs.jsonl.gz` is © Odoo S.A., licensed
[CC BY-SA 4.0](https://github.com/odoo/documentation), read into text by
`scripts/build_odoo_docs.py`. Run the script again to take a newer documentation; the first line of
the file says which branch of the documentation it was built from.
- Installed and tested on Odoo.sh (Linux, several workers, behind its proxy): the server
  tests pass there; the browser features were tried there by hand.

## Compliance dates and the signed pack feed

Packs that carry a date (`deadline`, `source`, `reviewed` on a check; `reviewed` on a pack) are counted down by a daily job: the accounting managers of each company (the Nudge managers when Accounting is not installed) are told 90, 30 and 7 days before, once for each threshold, as a notification and, where Discuss is installed, a message in the inbox. **Nudge, Health Check, Compliance Deadlines** lists what was told. The dates come from the authorities and are marked "confirm with the authority"; they are information, not legal advice.

The feed of updates is off on a new database (no address, no key). In **Settings, Nudge, Compliance Packs** an administrator sets the HTTPS address and the Ed25519 public key of the publisher. Once a day Nudge fetches that one file (never in private mode), checks the signature, validates each pack like a bundled one and lists the changes per country in **Nudge, Health Check, Compliance Pack Updates**. Only a system administrator approves, declines or rolls back an update; a pack is data (no code, no HTML). The feed is `{"format": 1, "payload": "<json text>", "signature": "<base64>"}`; sign it with `python -I scripts/sign_pack_feed.py --key <private.pem> --payload feed.json --out signed.json`. The private key never belongs on the server or in the repository.


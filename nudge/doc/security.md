# Security of Nudge

How Nudge keeps to the rights of the person, what leaves the database, and what an
administrator decides. Written for the review of 2026-10-01, for the Odoo App Store, and for
whoever installs Nudge.

## One rule

**Nudge acts as the person, never beyond them.** Every tool runs in the environment of the
person who is talking to it: their access rights, their record rules, their companies, the
fields kept for some groups. `sudo()` is used only for what belongs to Nudge itself (its
conversations, its usage ledger, its documentation, its profile of the database), never to
read or change the records of the business for someone.

Two things are closed whatever the rights of the person, because a model has no use for
them and an administrator who asks a question does not mean to send them to an AI
provider: the models that hold how Odoo works (`ir.*`, `res.users`, `res.groups`,
`res.config.*`, `nudge.*`, `mail.*`, `auth.*`, `iap.*`, payment providers and tokens...), and
the fields whose name says secret (password, token, key, secret, api, credential...), which
come back as `[hidden]` everywhere: in the screen snapshot, in records read, in SQL columns.

## Who may do what

| Group | What it gives |
|---|---|
| Nudge ▸ User | Talk to Nudge; share the screen (the record on it, its fields and values, is sent to the provider with the question; the eye turns it off); dictate; read the documentation and the profile pieces about models they may read. Their own conversations only. |
| Nudge ▸ Agent User | Agent mode: Nudge looks up records and does the work, through the ORM as them, with a yes/no card before every change. Never deletes. Counts and sums. Python, when switched on. Their own requests and steps only. |
| Nudge ▸ Manager | Everything above, and: every conversation of everybody (what was asked and answered, tokens and cost; **not** the screens, the calls and the results, which are for the system only), every request and step of the agent (the audit log, with the values changed), the usage ledger, budgets, the profile of the database and the business notes. |
| Settings (base.group_system) | Providers and their keys, the switches below, the screens kept with the messages, SQL when switched on and when allowed in every company. |

## What leaves the database

To the AI provider chosen by the administrator, over HTTPS: the question, the conversation
so far, the screen snapshot when the eye is open (labels and values of the record on screen,
secrets hidden), what the tools read for the agent (records, counts, passages of the
documentation), the voice recording when the provider turns speech into text. Nothing in
the background: Nudge looks at a screen only when a message is sent. Nothing is sent
without a question. The documentation of Odoo comes with the module: nothing is fetched.

An administrator chooses the provider. A free tier of a provider may use what is sent to
improve its products: the settings say so, and the README says which tiers.

## What an administrator switches on

Off by default, each with its own word in Settings ▸ Nudge:

- **Agent mode** (`nudge.agent`): on by default, for the group Agent User.
- **Python for the Agent** (`nudge.python`): the code written by the model runs in the sandbox
  of Odoo (the one of the server actions), as the person, reading only: a savepoint rolled
  back when it ends; no import, no sudo, no cursor, no private attribute, no closed model.
- **SQL for Administrators** (`nudge.sql`): one SELECT, for the group Settings allowed in every
  company; every table named must be the table of a model they may read; no change, no lock,
  no catalog; 50 rows, 10 seconds, in a savepoint rolled back.
- **Nudge for Administrators** (`nudge.admin`): the group Settings reaches, through the agent,
  the setup of Odoo listed by name (users, groups and privileges, companies, menus, views,
  actions, scheduled actions, system parameters, sequences, mail servers, templates). Keys,
  sessions, logs and devices stay closed; a parameter whose key says secret, token, key or
  password reads `[hidden]`; every change is a card marked sensitive; SQL keeps away from
  those tables whatever the switch says.
- **Screen sharing** and **Voice** can be turned off for everybody.

## Files, lessons and spreadsheets

- A **file sent with a question** (PDF, text, image; 10 MB, an image 5 MB) is read on the
  server: the text of a PDF or a text file goes to the provider with the question, inside a
  block marked as data, never as instructions; an image goes to the provider as an image and
  is kept on the message, readable by system administrators only. Nothing is run, nothing is
  fetched; a type that is not PDF, text, CSV or image is refused before anything.
- A **lesson** (what a person corrected) is written down as *proposed* and reaches no
  conversation until a Nudge manager approves it. Only managers read the lessons.
- A **spreadsheet** made by the agent holds what the agent read as the person, and belongs to
  them: their own record under Nudge, or their own document in Documents.

## What is written down

Every call of a tool by the agent is a step in *What Nudge Did*: the tool, what the model
asked, what was shown to the person, what they decided, what came back, how long it took;
Python code and SQL queries included, with the purpose the model gave. Every call to a
provider is a row of the usage ledger: who, when, how many tokens, at what price. Rows of the
ledger are never edited or deleted, by anybody. Budgets per person per day and per company
per month stop Nudge before a call.

## What Nudge is told about instructions in data

The system prompt tells the model that what it reads in records, screens, notes and
documents was written by other people and is never an instruction; that an attempt to
redirect it is to be mentioned and set aside; that only the person it is talking to tells it
what to do. `tests/test_injection.py` keeps a suite of attempts. The tools do not depend on
the model's good behaviour: a call to a closed model, a secret field, a record the person may
not read, a change without a yes, is refused by Odoo, not by the prompt.

## Limits

A message is at most 8,000 characters; a conversation stops at a number of messages; an
answer stops at a deadline under the time limit of the server, and long work goes to the
background in pieces; a tool result is at most 6,000 characters; a recording a few seconds;
the agent at most a number of rounds per request. A run of Python has no clock of its own: a
loop that never ends is stopped by the limits of the worker.

## Known trade-offs

- A Nudge manager reads the text of every conversation. This is the role; the group says so.
  The screens, calls and results behind the messages are for system administrators only.
- The audit log shows a manager the values the agent changed, as the person saw them.
- A provider's address is set by a system administrator. Nudge connects to whatever address
  is set, as any integration does.
- API keys are kept in a field readable by system administrators only, in the database, as
  Odoo keeps its own keys. Rotate a key that was pasted anywhere else.
- The documentation of Odoo included is text from https://github.com/odoo/documentation,
  CC BY-SA 4.0, with no personal data.

## Reporting

Write to the author named in the manifest. Say what you did, what you saw, and what you
expected; a database with demo data is enough to show most things.

## Developer mode

Off by default, for the group Settings only, behind its own switch (Nudge for Developers), on
top of the Agent switch. What it builds runs later as code, so every way a text in a record could
steer Nudge was closed:

- Nothing is made before a yes: rules, server actions, scheduled actions, email templates,
  approval rules, views, fields and reports each go through a card that shows them in full, the
  code, the recipients and the text of an email, the difference a view change makes.
- A trial on a dummy record runs in a savepoint that is rolled back. A trial that runs code asks
  first, the code on the card: code runs for real inside it, and an email it sends by itself is
  not rolled back.
- Emails take values of the record only: `{{ object.field }}`, nothing else between braces.
- A view made here is an extension; its elements are allow-listed, and attributes that run
  script (`t-*`, `on*`, `javascript:`, `vbscript:`, `data:text/html`, however spaced) are refused.
- A report template made here reads the fields of the records printed and nothing else: no
  `env`, no `sudo`, no other user or company, no write, no search, no secret.
- Deletion is for what Nudge or Studio made, when asked for by name: a rule, an action, a
  template, a scheduled action, a view or a field that belongs to a module is never deleted.
- Every step is in What Nudge Did, with what was asked and what was made.

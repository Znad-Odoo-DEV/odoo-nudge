# Privacy

What Nudge sends, to whom, and what it keeps. For the person who installs it and for the
people who use it.

## Nothing leaves without a question

Nudge sends nothing on its own. When a person sends a message, and only then, the following
goes to the AI provider the administrator chose, over HTTPS:

- the question and the conversation so far;
- the screen, when the eye is open: the labels and values of the record on screen, with
  passwords, keys, tokens and bank numbers replaced by `[hidden]`;
- what the agent reads to answer, as the person: records, counts and sums, passages of the
  Odoo documentation, the profile of the database (names of models and modules, how much
  each is used, never the content of records);
- a file the person attached: the text of a PDF or a text file, or the image;
- a recording, when speech is turned into text by the provider rather than by the browser;
- to advise on the setup: the settings of the apps (their values for an administrator), what
  their Configuration menus hold, what a module registered and, for an administrator only, the
  code of a module, with the secrets written in it replaced by `***`;
- in Developer mode, for an administrator: what runs on a model (automation rules, server
  actions and their code, email templates, scheduled actions) and the definition of its screens,
  so that Nudge builds on them.

The search offered before a provider is set up asks no provider: it reads the menus, the
settings and the documentation held in this database, and nothing leaves it.

## What is never sent

Passwords, API keys, tokens, the keys of the providers, the values of secret system
parameters, the records a person may not read, the records of another company they may not
reach. Nudge has no telemetry: nothing goes to the author of the module, ever.

## Who sees what is kept

| Kept | Who reads it |
|---|---|
| The conversation (questions, answers) | The person; Nudge managers |
| The screen, tool calls and results behind a message, an image sent | System administrators |
| What the agent did (steps, values changed) | The person; Nudge managers |
| Tokens and cost per call | Nudge managers |
| Lessons waiting for approval | Nudge managers |
| Spreadsheets made by Nudge | The person (and administrators) |

## Choosing a provider

The provider receives what is listed above. A **free tier** may use it to improve its
products: use a paid tier, or a model running on your own server (Ollama, any
OpenAI-compatible server), for business data. With a local model nothing leaves your network.

## Deleting

Deleting a conversation deletes its messages, screens and images. A conversation in which the
Agent acted holds the log of what it did, its runs and their steps, which nobody can change or
delete, a manager neither: it is archived, and deleting it is refused with that reason. Archiving a user keeps
their conversations for the managers; delete them as you would any record. The usage ledger
is kept for the budgets and the bills and cannot be edited from the interface; it only loses
the link to the deleted conversation.

### Retention

Settings, Nudge, Privacy: "Delete Conversations Older Than (Days)". Every day Nudge deletes,
200 at a time, the conversations whose last activity is older than that number of days, the
same way as above; one in which the Agent acted is archived instead, its log kept. A
conversation that is answering, or has a step waiting for a yes, is left. 0, the default,
never deletes anything.

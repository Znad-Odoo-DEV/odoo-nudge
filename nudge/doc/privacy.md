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
- a recording, when speech is turned into text by the provider rather than by the browser.

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

Deleting a conversation deletes its messages, screens and images. Archiving a user keeps
their conversations for the managers; delete them as you would any record. The usage ledger
is kept for the budgets and the bills and is never edited.

# Nudge: Website Assistant

A small chat bubble on the public website and in the portal, from the Nudge family. Installed from Apps by an
administrator who wants it, with Nudge, Website and Portal; Website Sale and Live Chat are optional. Community and
Enterprise. English and Arabic.

## What it answers

| Who asks | Answered from |
| --- | --- |
| A visitor with no login | The **published products** of the shop (the price the website shows, with taxes as the shop is set, the availability when the shop shows it, the link of the page), the **company files marked "Public on the Website"** (a new tick on Nudge's Company Files) and the **text of the published pages**. Nothing else is ever read. |
| A customer logged in | The same, and their **own orders, invoices, deliveries and tickets**, read with their own rights and the record rules of the portal (never with the rights of the system), with the link of each in the portal: "where is my order", "how much do I owe". |

Anything else is refused politely, with the contact link, and the AI is not even asked. The model has no tool to
call: the facts are found by rules, framed as `<data>` (an instruction found in a file or a page is never
followed), and the model, when there is one, only words them in the language of the visitor. No record is created
by an answer.

## Without an AI provider, or in private mode

Products and files are still found and shown, by rules alone. Private mode of Nudge is obeyed: nothing leaves
the server. The AI is also left out for the rest of the day once the daily budget of the company is spent.

## Protection

* A limit per visitor (default 20 messages in 10 minutes), counted on a hash of the address, never on the address.
* A daily budget of tokens per company for the AI on visitors (default 50,000; 0 is never the AI).
* The files and pages the visitors read are those marked public, in a record rule of their own; the rest of Nudge
  (conversations, tasks, notes, memory, usage) has no access for the public or the portal.
* Published pages are read as a public file by a scheduled action every six hours, or at the click of a button in
  the settings; an unpublished page is put away, not deleted.

## Talk to a person

"Talk to a person" opens Live Chat when it is installed and an operator is available, otherwise the contact form.
When the visitor leaves a name and an email, a lead is made (the only record the assistant creates, at the visitor's
request); the conversation goes with it only if the visitor ticks the box.

## For the company

* Settings, Nudge, Website Assistant: turn it on for a website, the greeting, the languages, the daily budget and the
  limit per visitor.
* Nudge, Website Assistant: the questions asked (unanswered first) and the usage of the AI on visitors, which is
  the usage of Nudge filtered on the origin "Website Assistant".
* Visitor Questions: a question the assistant could not answer is only listed there, for a manager to read and add a
  file (public, if visitors may read it). It never becomes a Business Note or a lesson of Nudge.
* Only pages that are published and visible to everyone are read (not signed-in only, password or group pages).
* The AI answers at most N visitors at once (Settings; default: workers less three, at least one); past it the answer
  is by rules alone, with the contact link. Provider timeout 15 s.
* Visitor lines (question, answer, hashed address) are archived, never deleted, after N days (Settings; default 30; 0 keeps them).
* Rate limits count the address Odoo reports: behind a reverse proxy set `proxy_mode = True`, or all visitors share one address.

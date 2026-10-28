# Nudge Agents (for administrators)

## What an agent is

An agent is a colleague with a role, such as "Sales assistant" or "Accounting expert". It has a name and a face,
its own instructions, the skills it knows, the tools it may use, the companies it works in and the people who may
talk to it. People reach it in the Nudge panel (and, once published on a channel, in Discuss and other places).

An agent is not a new user with its own rights. It is Nudge, speaking in a certain way, with less to do.

Open it from Nudge, Configuration, Agents. Four templates (Sales assistant, HR helper, Accounting expert,
Inventory expert) are there to copy and adapt.

## The security rules, in plain words

1. **It works under the rights of the person who talks to it.** What the person may not read, the agent cannot
   read. A tool is offered only if that person may already use it. Giving an agent a tool never gives it to a
   person.
2. **It cannot weaken Nudge.** Your instructions are added on top of the rules of Nudge. They cannot switch off
   the safety rules, the confirmation of changes or the check of rights.
3. **It stays in its companies.** A record of a company outside the agent's list is not found, even if the person
   may see it elsewhere.
4. **Changes wait for a yes.** Anything that changes data is shown on a card first, as always.
5. **Developer tools are never available** to an agent, and developer mode is refused with one.
6. **The audience is explicit.** Only the people and groups you name, in the companies you name, can use it.
   Use *Access preview* to see, for any person, what the agent would let them see and do.
7. **A service binding is special.** It lets the agent answer people who have no Odoo account (website,
   WhatsApp, Telegram), under a restricted service user that reads only the models you list. Only an
   administrator can publish it. Keep the list short, and name a person for escalation. See *Public channels*
   below.
8. **Budgets and masking.** A monthly cost budget and a daily token budget per person can be set; over a budget the
   agent answers only what needs no AI. The budgets are checked at each question, after each yes or no to a card,
   and before each piece of work in the background. "Mask personal data" hides personal data from the provider.
9. **What others read holds no records.** In a Discuss channel or group, everything the agent posts is read by
   the members, and by guests who have no account. There it answers from knowledge only: the Odoo documentation,
   its skills, and the approved answers kept for no group. Only a direct chat with the agent alone is answered with
   the records of the person who writes.
10. **An agent of several companies belongs to all of them.** It is changed, evaluated, published, archived and
    given channels only by a manager who works in every one of its companies. Its list of changes shows the
    differences to them only.
11. **No scheduled tasks.** A scheduled task runs later as plain Nudge, outside the agent, so in this version an
    agent is never given *Prepare a task*.
12. **A public channel goes down when what it says changes.** When anybody but an administrator changes the
    instructions, tools, off-limits topics or limits of the agent, a skill the agent names (any published skill
    when it names none), or an approved answer of the memory meant for everyone (when the agent uses the memory),
    its channels other than Discuss go back to unpublished, for an administrator to publish again. The list of
    changes of the agent says why.

## Building an agent

- **Persona**: name, picture, one-line role, instructions (English, for the model), greeting, languages, and
  optionally its own AI provider.
- **Knowledge**: the skills it may use (none ticked: Nudge picks among the published skills), whether it uses the
  approved memory, and the Odoo documentation.
- **Abilities**: companies, groups and people, and the tools. The list shows each tool with a badge: *Read* or
  *Writes on a card*. Tick nothing and the agent has the reading tools only.
- **Channels**: where the agent is available. A red line marks a service binding.
- **Guardrails**: masking and budgets.
- **Test**: the evaluation questions, the score and the *Try it* button.
- **Activity**: answers, share answered without AI, cost this month, the conversations, and the list of changes
  made to the agent (who changed what, and when).

## Testing and publishing

1. **Try it** opens the Nudge panel as that agent, in *rehearsal*. The header shows the agent and a "Rehearsal"
   badge. Tools that change data only describe what they would do; nothing is written, and the cost is not counted
   as real use in the month of the agent (it still counts in the tokens of your day, AI calls made by a tool
   included). *New chat* stays with the agent; the cross at the end of the header goes back to plain Nudge.
   A rehearsal undoes what a tool wrote in the database. It cannot hold back what leaves the database while the
   tool runs: a webhook of an automation, a mail sent by an outside hook, a call to another system. Rehearse
   with tools that do not set those off, or on a test database.
2. **Evaluation**: add questions in the Test tab. For each, choose what to expect: the answer *contains* a text,
   is answered *without AI*, or is simply *answered*. **Run evaluation** asks them all in rehearsal, records the
   answers and sets the score (the share that passed). When all pass, the agent is *Tested*. The answers are
   given with the rights of the one who runs the evaluation, so they are shown to that person only; the others
   see whether each question passed.
3. **Publish** is offered once the agent is draft or tested, and works only if every question passed, there is at
   least one question, and the score is 80 or more. Run the evaluation again after each change to the instructions
   or knowledge.
4. **Archive** takes the agent away from everyone at once (and out of any Discuss channel). *Back to Draft*
   brings it back for changes.

## Public channels (website, WhatsApp, Telegram)

A person with an Odoo account who writes on a channel is answered as that person, as everywhere. Somebody without
one (a website visitor, an unknown phone number) is answered through a **service binding**:

- The answer runs as the **service user** of the binding, never as an administrator. That user is a **portal
  user** (so the record rules and field groups of the portal apply: no unpublished product, no cost price) and
  holds the group *Agent Service User* and nothing else of Nudge; a binding whose user was given more is refused at
  the next question.
- It has a **cost cap**: a service channel is published only when its agent has a monthly budget and tokens per
  person per day, and the day is counted per visitor (their session or number), not for the service user they
  share. On the website the company's daily budget for visitors (*Website Assistant Tokens per Day*) counts the
  agents too; past it the plain assistant answers without AI. Visitors take the places of the website's AI (and
  two places on WhatsApp and Telegram, `nudge_messaging.max_public_agents`): the others wait.
- It only asks questions: no change, no card, no Agent mode.
- It reads **only the models listed** on the binding (*Readable Models*), with the reading tools of the agent.
  Every model and every field a question would read is checked against the list before anything is read; a field
  that points to a record of another model, or a path through one ("partner_id.city"), is refused, and so are
  archived records (`active`), properties and json fields, and any field the service user's groups do not read.
  A model with a website publication (`is_published`) is read only where it is published. Some models are
  never readable whatever the list says: users, contacts, companies, employees and all of HR, calendars, website
  visitors, pricelists, pay, leave, accounting, payments, messages, the configuration and Nudge's own records.
- With an **empty list** it reads no record at all: it answers from its knowledge, its skills and the Odoo
  documentation.
- No approved answer of the memory is given (they were written for the people of the company: an agent given a
  service channel has *Use Memory* turned off), no company file is named, and nothing is said of how the database
  is set up. What went wrong inside is never shown to a visitor: a fixed sentence is.
- Each visitor has their own conversation on the channel, kept for a day.

## Guardrails of a channel

- **Office hours** (on the channel): such as `08:00-18:00`, or `08:00-12:00, 16:00-20:00`, and **days** such as
  `sun-thu` or `mon-fri`, in the time zone of the company. Outside them the channel answers with a fixed sentence
  that gives the hours, without AI.
- **Off-limits topics** (on the agent, Guardrails tab): one topic per line, in any language. A question that holds
  every word of a line (plurals and Arabic prefixes included: "salaries" catches "salary of Ahmad", "الرواتب"
  catches "راتب") is declined with a fixed sentence, without AI, everywhere the agent answers. It is a matter of
  tone, **not a security boundary**: a question in other words passes it. What may be read is decided by the
  person's rights, or on a service channel by its readable models; never by this list.
- **Hand to a person**: name an employee in *Escalate To* on the channel. The agent is then offered "hand to a
  person": a To Do for that person, on a new lead when CRM is installed (on the lead the website's form just made,
  never a second one; otherwise on the contact of the person who asked, or on an archived contact made for the
  visitor), with a summary of what is needed. A person is handed over only when their message asks for a person,
  and once a text of a record was read in the conversation the To Do carries their own words, not the agent's. A
  visitor without an account is handed over only when they agreed to it for that agent (the website's *Talk to a
  person*), at most once a day; otherwise the agent is told nothing was handed over.
- **Front desk**: when a channel has several agents, the question goes to the agent whose role and skills fit it,
  chosen without AI. Tick *Front Desk Default* on the channel of the agent that answers when none fits.

## Outside the Nudge panel

In Discuss, on the website and on a phone there are no buttons and no modes: the agent is told so, and never offers
to switch to Agent or Developer mode there. It serves a remembered answer only for the same question (the same
words that matter), and never offers a choice of close answers.

## Budget notice

When an agent spends its monthly budget, its creator (or else the Nudge managers of its companies) gets a To Do on
the agent's contact, once a month. Until the next month it answers without AI.

## Good habits

- Write instructions as a short job description: who it helps, what it focuses on, what it declines.
- Start with the fewest tools, add one at a time, and check with Access preview as a person of the audience.
- Include in the evaluation a question the agent must decline, and one in each language it serves.
- On a public channel, list only what a visitor may see (a published product catalogue, the countries you ship
  to), and name a person to escalate to.

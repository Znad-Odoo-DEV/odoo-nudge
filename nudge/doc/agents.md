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
   administrator can publish it. Keep the list short, and name a person for escalation.
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
    instructions, tools or limits of the agent, or a skill the agent names, its channels other than Discuss go
    back to unpublished, for an administrator to publish again. Not yet (60.1): a change of an approved answer of
    the memory, or of a published skill when the agent names none; review those channels after such a change.

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

## Good habits

- Write instructions as a short job description: who it helps, what it focuses on, what it declines.
- Start with the fewest tools, add one at a time, and check with Access preview as a person of the audience.
- Include in the evaluation a question the agent must decline, and one in each language it serves.

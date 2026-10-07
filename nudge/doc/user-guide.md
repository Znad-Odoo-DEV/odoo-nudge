# Nudge — user guide

[العربية](user-guide.ar.md)

Nudge is the small round face at the corner of every Odoo screen. It answers questions about
Odoo and about your own database, shows you where to click, listens to you, types what you
dictate, and, if your administrator allows it, does the work for you.

## Call Nudge

- Press **Ctrl + Shift + Space** from any screen, or click the round face, or the icon in the
  top bar.
- Type your question and press Enter. Shift + Enter makes a new line. Escape closes the panel.
- Nudge answers in the language you write in, whatever the language of your screen.
- **+** starts a new conversation; the clock icon shows your earlier ones.

## Ask about what you see

When the eye above the message box is open, Nudge sees the screen you are on: the record, its
fields, what you typed and did not save yet, its buttons and tabs.

- "What am I looking at?"
- "What should I do next?"
- "Why can't I save?"

Close the eye and Nudge sees nothing of your screen. The eye is remembered.

## Be shown, not told

- "Show me how to confirm this order."
- "Where do I change the salesperson?"
- "Take me to the warehouses."

With your screen shared, every question asks first how you want the answer: **On the
screen, with the pointer**, or **In writing**. Choose once for all under the preferences
("When I ask with my screen shared") if you prefer not to be asked. In Agent mode the card
offers **Do it for me** as well: the work, rather than an explanation; **On the screen** there
explains with the pointer and changes nothing. On the screen, Nudge's
pointer, the arrow with eyes,
goes to the button or the field and presses it, for show. A box with a short text opens next to it, and the rest of the screen is blurred and
dimmed: only this step is clear. The press is a demonstration: do it yourself, and the next
step comes on its own. **Back**, **Next** and the cross are always there. If the field is in a
closed tab, Nudge shows the tab first. Ask how to open a screen ("How do I open the attendance
report?") and the pointer walks you through the menus: the apps, then each menu down to the
screen, from wherever you are; if you are already in the app, it starts at its menus.

## Ask how Odoo works

- "How do quotation templates work?"
- "What is a reordering rule?"
- "How do I set up a payslip batch?"
- "How do I write a computed field?" or "What is a record rule?" (the developer documentation)

Nudge has the official documentation of Odoo 17 at hand, every app. For a question about
standard Odoo it reads the documentation before answering, and gives you the link of the page
at the end. What your own database changes comes first: Nudge says when a custom module
does things differently.

## Ask for numbers

In Agent mode:

- "How many quotations per salesperson this month?"
- "Total invoiced per customer this year, the ten largest."
- "Average hours per project in September."

Nudge counts, sums and averages through Odoo, with your rights: what you cannot open is
not counted. The answer gives the numbers as a short table.

When your administrator has switched Python on, the agent can also compute what a count
cannot give: a calculation per record, a comparison of two lists, dates. It still reads with
your rights and changes nothing by itself: a change is shown to you first, as always.

Administrators of the system, when SQL is switched on, can ask the agent for a query across
tables: "the invoices of September with their lines and products". Reading only, fifty rows.

## A senior at your side

Nudge answers as a senior Odoo implementer, functional consultant, developer, accountant, HR,
sales or inventory person, whichever the question needs, and says what a senior would: the right
way, the trade-off, the thing you did not ask about. Ask "Is this database fit for a contracting
company?" (or trading, shipping, manufacturing, retail, services, real estate) and it checks what
such a business runs in Odoo against what is installed, in use and custom here, and proposes what is
missing with the page of the official documentation for each. What it has no reference for, it
says it does not know.

## Nudge learns from your corrections

Correct it ("no, our salaries are in our own payroll module", "we call quotations offers") and
Nudge writes the lesson down. A manager of Nudge reads it under What Nudge Knows ‣ Business Notes,
approves or refuses it; once approved, every conversation starts with it. Nothing is learnt
without that approval.

## Ask about your database

Nudge knows how this database is set up: which modules are custom, which fields were added,
how deliveries, approvals and other options are set.

- "What does the maintenance module do here?"
- "Which fields did we add to sales orders?"

It also knows where the work is. Every day it counts, for each kind of record, how many there
are and how many changed lately. When a topic is covered twice, say payroll by a standard app
and by your own module, it answers from the one your people actually use, says which one it
read, and looks in the other before saying something does not exist. You never have to name
the module. The figures it gives are counted as you: what you cannot open is not counted.

If your managers wrote business notes ("we call quotations offers"), Nudge follows them.

## Send a file with the question

The paperclip next to the message box takes a PDF, a text or CSV file, or an image. Nudge reads
the text of a PDF on the server and gives it to the AI with your question; an image is shown to
the AI when the provider can look at one. A scanned PDF holds no text: Nudge says so and asks for
a photo or the numbers.

- A supplier's invoice: "Find the bill of this" opens the bill, the purchase order or the RFQ it
  belongs to, whatever you call it; Nudge says what it matched on.
- A list of materials: "What do I have in stock of these?" (Agent mode) finds every likely product
  for each line, the names need not match the catalogue, with the quantity per warehouse, and lets
  you choose.

What a file holds is read as data, never as instructions.

## Talk instead of typing

- Hold **Ctrl + Shift + Space**, ask, let go. Or press the microphone in the panel: it stops
  when you stop talking.
- The button next to the microphone says which language you speak: **ع** or **EN**. Press it
  to change. You can also set it in the preferences (gear icon).
- The first time, your browser asks whether Odoo may use the microphone: allow it.

## Dictate in a field

1. Click in a text field of Odoo.
2. Press **Ctrl + Alt + D** and talk. Or hold it while you talk.
3. Press it again, or the tick in the small bubble, when you are done.

The words go where your cursor is. Dates, amounts and fields where you pick a record are not
dictated. You can also tell Nudge: "Write in the notes: the customer called this morning."

## Have the answers read to you

In the preferences (gear icon), tick **Read the answers to me**. The loudspeaker in the header
of the panel stops the reading. Without a voice for Arabic on your device, Arabic answers are
written only; Windows offers one under Settings ‣ Time & language ‣ Speech.

## For administrators: the setup of Odoo

Off by default. When an administrator switches on **Nudge for Administrators** (Settings ‣
Nudge), administrators can ask the agent about users, groups and privileges, companies, menus,
views, actions, scheduled actions and system parameters, and change them, one confirmation at a
time, never a deletion. Passwords, keys and tokens are never shown, whoever asks.

## Let Nudge do the work (Agent mode)

If you see **Talk** and **Agent** under the messages, you may ask Nudge to do things.

1. Press **Agent**, or say "Nudge, do it" after it explained how.
2. Ask: "Create a quotation for Acme with 3 office chairs, then confirm it."
3. Nudge finds what it needs and shows you what it is about to do: the fields as they are and
   as they will be, or the button it is about to press, and on which record.
4. Press **Yes, do it** or **No**. Nothing changes before your yes. On a no, it asks what you
   want instead.

Every change is made as you, with your rights. Nudge never deletes. A warning sign on a card
means the action is not taken back with a click: read twice.

## Reports and comparisons as spreadsheets

Ask for a comparison or a report ("compare this month's salaries with last month's", "stock of
product X per warehouse") and Nudge asks first: **in the chat**, or **as a spreadsheet, with a
chart**. Choose the spreadsheet and the agent reads the figures, builds a formatted table with
totals and a chart, and opens it in Odoo's spreadsheet editor; it is yours to change and to
download as .xlsx, and it stays under Nudge ‣ Spreadsheets. The question comes only for
comparisons and reports, and only when the Spreadsheet module is installed.

## Leave the work to Nudge

In Agent mode, press the **clock** instead of Send. Close the panel, go to another screen.

- A message tells you when Nudge needs your answer: press **Show me**, read, say yes or no.
- Another one tells you when it is done. The number on the round face says how many requests
  ended since you last looked.
- Open the conversation to see what Nudge is doing, and to **Stop** it. What was done stays.

## Make it yours

In the preferences (gear icon): the shortcuts, the colour of Nudge, the language you speak.
Drag the round face to put Nudge where you want it on the screen; **Put it back in the corner**
undoes it.

## When something is off

- "Nudge needs an AI provider": ask your administrator.
- "The AI provider's limit is reached": wait a minute and try again.
- "You used all of your Nudge tokens for today": your administrator set a budget.
- Nudge does not see your screen: open the eye, or ask your administrator, who may have turned
  screen sharing off.
- The microphone is blocked: allow it in the address bar of your browser.
- An answer is wrong or points at the wrong thing: Nudge is an assistant, not an authority.
  Check before you act, and tell your managers; they can correct what Nudge knows about the
  database under **Nudge ‣ What Nudge Knows**.

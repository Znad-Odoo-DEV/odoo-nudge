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

## Before a provider is set up

Nudge is useful from the first minute. Until an administrator sets an AI provider up, the panel
offers a search: type a few words and it lists the screens you can open, the settings of your
apps (if you are an administrator) and the pages of the official Odoo documentation. Click a
screen or a setting and the pointer takes you there. Nothing is sent anywhere.

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

## Set Odoo up for your business

Ask how to set something up, for any app and any business: "I have a store on every project:
how do I move materials between them, issue them to the project, and receive them?", "What is
the best setup of payroll and attendance for us?". Nudge first asks what it needs to know about
your business, then reads how this database is set: the settings of the app (their values, for
an administrator), what its Configuration menu holds (warehouses, routes, operation types,
journals...) and your custom modules (their code too, for an administrator). It answers with a
setup for this database, built on what you already have, with the pages of the official
documentation. It only suggests: ask it to **apply** it and it changes the settings, one
confirmation at a time (an administrator, with the setup open to administrators); ask it to
**show** you and its pointer takes you to each setting on the screen.

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

## Find a product: /product

Type `/product` (or `/منتج`) and what you look for: a name, a description, a code, a size.
"/product bolt 8 mm stainless", "/product red t-shirt XL", "/product laptop 16 GB",
"/منتج برغي 8 ملم". Nudge looks in every product you may see, in any trade: the names, the
codes and barcodes, the descriptions, the variants (size, colour), the categories and the names
the suppliers give them. A size is read with its unit and compared in any unit (12.7 mm finds the
1/2 inch pipe), a product of another size comes after, and a word misspelt still finds what it
meant. You get cards: the picture, the price, the cost and the stock per warehouse, each where you
may see it, and why the product came (the code, the size, the words). **Open** opens it.

Without `/product`, a question about products ("do we have stainless bolts?", "what is the
price of…") does the same through the conversation. Attach a picture with `/product` and Nudge
describes what it shows and searches with that, when the provider can look at pictures. The
search itself needs no AI: it works before any provider is set up, from the search box.

## Tell Nudge where to look: the commands

Start a message with a word after a slash and Nudge looks there only. Type `/` and the list of
the commands open to you comes up; the arrows and Enter pick one. `/help` (or `/مساعدة`) lists
them with an example each.

Answered at once, **without any AI** (no token spent, and they work before any provider is set
up, from the search box):

| Command | What it finds |
|---|---|
| `/screen` `/شاشة` | A screen you can open, by its name: the pointer shows the way, **Open** goes there. |
| `/setting` `/إعداد` | A setting of the apps (administrators): the pointer shows it. |
| `/doc` `/توثيق` | Pages of the official Odoo documentation, with their links. |
| `/file` `/ملف` | Passages of the company files, with the file and the page. |
| `/find` `/سجل` | A record by its name, number or reference, in any app: "/find S00042". Put its kind first to narrow it: "/find invoice: INV/2026/00042". |
| `/product` `/منتج` | Products like a name, a description, a code or a size. |
| `/stock` `/مخزون` | The same, for products kept in stock, with the quantity in each warehouse. |
| `/rules` `/قواعد` | What runs by itself on a kind of record: automation rules, server actions, scheduled actions, email templates (Developer mode). Without a name, the screen in front. |
| `/sql` | One SELECT, read only (administrators, when SQL is switched on). |

Asked of the AI **with a few tools only** and a short brief, without the rest of the conversation:
a fraction of the tokens of a question asked in full.

| Command | What it answers |
|---|---|
| `/count` `/كم` | A number about your records: "/count invoices unpaid this month" (Agent users). |
| `/explain` `/اشرح` | A field, a term or this screen, explained in a few words. |

Nothing found is said at once, without any AI.

## Nudge: On / Off

Your own menu (your name at the top right) has **Nudge: On**. Press it and Nudge goes away for
you: the avatar, the button of the top bar and the shortcuts, on every page, until you press
**Nudge: Off** there. Nobody else is affected, and nothing is lost.

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

## Check a file before you import it

Importing an Excel or CSV file and getting errors? Attach the file to your question ("why does
this not import into contacts?") and Nudge tries it the way the Test button of the Import screen
does, without saving anything: every row is checked, and each problem comes back with its row
and its column, explained in plain words with the way to fix it in the file. When you are on the
Import screen itself and share your screen, Nudge sees the errors shown there too. Excel files
(.xlsx) and CSV files are also read as text, sheet by sheet, for any other question about them.

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

## For administrators: build with Nudge (Developer mode)

Off by default. When an administrator switches on **Nudge for Developers** (Settings ‣ Nudge),
administrators get a third mode, **Dev**, where Nudge builds as a senior Odoo developer would:
automation rules, server actions, emails sent on an event, scheduled actions, buttons on a form,
new fields, changes to a screen, PDF reports. It asks what it needs to know, reads what already runs on the
model, explains what it is about to make, and shows everything on the card: the trigger, the full
code, the recipients and the text of an email, the difference a change makes to a screen. Nothing
is made before you say yes. A rule can be tried first on a dummy record that is discarded
afterwards; a scheduled action can be run once the same way. What Nudge made is switched off with
a change, and deleted only when you ask for it by name; a view or a field of a module is never
touched. Every step is in What Nudge Did.

### Emails that look designed

With the companion **Nudge: Emails** (it installs itself where Discuss is), ask for an email
("when a quotation is confirmed, email the customer, the salesperson in copy") and Nudge asks
which style you want: **classic** (a coloured band and a clean table), **modern** (a large
coloured header and rounded cards), **minimal** (quiet, a thin line of colour) or **formal** (a
letter with the logo on top). It proposes the title, the text, the values of the record in a
table, its lines, and a box for what the reader must answer; the logo and the name of your
company close every email unless you say otherwise. Arabic text makes the email right to left.
The card shows the email as it will be read, for a real record. Once you said yes, Nudge offers
to send you a test, to your own address only.

Then the email goes the way you asked: by itself on an event, or from a button. With a button,
pressing it opens a window with the email ready for the record on screen: read it, change it,
attach a file, and **Send** does what the button it goes with does (approve, confirm, validate)
and sends the email with a real Cc. Closing the window does neither. The original button can be
hidden behind the new one, fields can be required before Send, and the work can be done
**Without the Email** when you allow it. These buttons are listed under Nudge ‣ Configuration ‣
Emails with Review, where one is switched off or deleted with its button.

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
- "The AI provider took too long to answer": ask again; when it happens often, your
  administrator can set a lower reasoning effort on the provider, or a faster model.
- "Ollama does not answer" or "Ollama has no model": your administrator runs the AI on a machine
  of the company with Ollama; it must be started, or the model pulled. See doc/ollama.md.
- "Nudge ran out of time before it was done": it read what it needed and the time was up. Say
  "go on" and it continues from there, or press the clock and leave the work to it.
- "You used all of your Nudge tokens for today": your administrator set a budget.
- Nudge does not see your screen: open the eye, or ask your administrator, who may have turned
  screen sharing off.
- The microphone is blocked: allow it in the address bar of your browser.
- An answer is wrong or points at the wrong thing: Nudge is an assistant, not an authority.
  Check before you act, and tell your managers; they can correct what Nudge knows about the
  database under **Nudge ‣ What Nudge Knows**.

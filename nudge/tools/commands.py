"""The commands of the message box: a word after a slash says where to look.

A direct command is answered by the server without any AI: screens,
settings, pages of the documentation, records, company files, products,
what runs by itself, a query. It works before any provider is set up, and
costs no token. A focused command goes to the model with the tools of its
kind only and a short brief instead of everything Nudge knows: a fraction of
the tokens of a question asked in full.
"""
import re

from ..llm import arabic
from . import base

DIRECT = "direct"
FOCUSED = "focused"


class Command:
    def __init__(self, name, words, kind=DIRECT):
        self.name = name
        # What is typed for it, in English and in Arabic
        self.words = words
        self.kind = kind


COMMANDS = (
    Command("help", ("/help", "/مساعدة")),
    Command("screen", ("/screen", "/شاشة")),
    Command("setting", ("/setting", "/إعداد")),
    Command("doc", ("/doc", "/توثيق")),
    Command("file", ("/file", "/ملف")),
    Command("find", ("/find", "/سجل")),
    Command("product", ("/product", "/منتج")),
    Command("stock", ("/stock", "/مخزون")),
    Command("rules", ("/rules", "/قواعد")),
    # Why an automation, a server action or a scheduled action failed, no AI (tools/debugger.py)
    Command("debug", ("/debug", "/فشل")),
    Command("tour", ("/tour", "/جولة")),
    # The health of the database, found by small checks, no AI (tools/health.py)
    Command("health", ("/health", "/فحص")),
    # A CSV or Excel file matched to the fields of a model and tried, no AI (tools/import_match.py)
    Command("import", ("/import", "/استيراد")),
    Command("sql", ("/sql",)),
    # The screen explained from what Odoo knows, no AI (tools/explainer.py)
    Command("why", ("/why", "/لماذا")),
    Command("field", ("/field", "/حقل")),
    Command("here", ("/here", "/هنا")),
    Command("count", ("/count", "/كم"), FOCUSED),
    Command("explain", ("/explain", "/اشرح"), FOCUSED),
)
# Read the screen: they need no words after them, and are run at once when chosen
SCREEN_COMMANDS = ("why", "field", "here")
BY_NAME = {command.name: command for command in COMMANDS}
BY_WORD = {word: command for command in COMMANDS for word in command.words}

# What a focused command may reach, and in which mode: reading only
FOCUS = {
    "count": {
        "mode": base.MODE_AGENT,
        "tools": ("aggregate_records", "search_records", "locate_data", "describe_model"),
    },
    "explain": {
        "mode": base.MODE_TALK,
        "tools": ("search_odoo_documentation", "describe_model", "search_company_files", "locate_data"),
    },
    # @Nudge in the chatter of a record or in a channel: what it may know is
    # given with the question; the documentation is the only thing it looks up
    "mention": {
        "mode": base.MODE_TALK,
        "tools": ("search_odoo_documentation",),
    },
    # The history of a record summarised, and a reply to its customer drafted
    # (tools/history.py, models/nudge_reply.py): all they may know comes with
    # the question as data
    "history": {"mode": base.MODE_TALK, "tools": ()},
    "reply": {"mode": base.MODE_TALK, "tools": ()},
}
# Focused by the code of a companion module, never typed
INTERNAL_FOCUS = ("mention", "history", "reply")
# Focused questions a small model answers from what Nudge found for them
GROUNDED_FOCUS = ("mention", "explain")
# Times the model is called for a focused command: look, look again, answer
FOCUS_ROUNDS = 4


def fold(text):
    """:return: ``text`` as words compared, whatever the spelling: lower case,
    no marks, one form of alif, ta marbuta and alif maqsura, and "al" taken
    off the start of an Arabic word: "عروض الأسعار" and "عروض اسعار" meet"""
    text = arabic.fold(text)
    words = []
    for word in re.findall(r"\w+", text):
        if word.startswith("\u0627\u0644") and len(word) > 4:
            word = word[2:]
        words.append(word)
    return " ".join(words)


# Particles written onto an Arabic word: "هالعرض", "بالفاتورة", "والمخزون"
ARABIC_PARTICLES = ("\u0647\u0627\u0644", "\u0628\u0627\u0644", "\u0648\u0627\u0644",
                    "\u0641\u0627\u0644", "\u0643\u0627\u0644", "\u0644\u0644")
# Long vowels: a broken plural keeps the root without them ("عرض", "عروض")
ARABIC_VOWELS = str.maketrans("", "", "\u0627\u0648\u064a")


def root_of(word):
    """:return: a folded word with what is written onto it taken off, and,
    for Arabic, its long vowels: "هالعروض" and "عرض" meet, as do "اسعار"
    and "سعر"; an English plural meets its singular: "quotations", "quotation" """
    for particle in ARABIC_PARTICLES:
        if word.startswith(particle) and len(word) - len(particle) >= 3:
            word = word[len(particle):]
            break
    if word and "\u0600" <= word[0] <= "\u06ff":
        stripped = word.translate(ARABIC_VOWELS)
        return stripped if len(stripped) >= 2 else word
    if len(word) > 4 and word.isascii():
        if word.endswith("ies"):
            return word[:-3] + "y"
        if word.endswith(("ses", "xes", "ches", "shes")):
            return word[:-2]
        if word.endswith("s") and not word.endswith(("ss", "us", "is")):
            return word[:-1]
    return word


# "Take me to the vendor bills", "خدني على الفواتير", "افتحلي العروض"
GO_TO = re.compile(
    r"(\b(take me|bring me|go to|get me to|navigate to|open the|open my)\b"
    r"|\u062e\u062f\u0646\u064a|\u062e\u0630\u0646\u064a|\u0648\u062f\u064a\u0646\u064a"
    r"|\u0631\u0648\u062d\u0646\u064a|\u0627\u0641\u062a\u062d|\u0641\u062a\u062d\u0644\u064a)",
    re.IGNORECASE)
ARABIC_LETTER = re.compile("[\u0621-\u064a]")
LATIN_LETTER = re.compile("[A-Za-z]")


def asks_to_go(text):
    """:return: whether the message asks to be taken to a screen"""
    return bool(GO_TO.search(text or ""))


def language_of(text):
    """:return: "Arabic" when at least as many words of ``text`` are written in
    Arabic as in Latin letters, else "English": the language a small model is
    told to answer in. Words, not letters: "شو يعني Incoterm؟" is Arabic"""
    words = re.findall(r"\w+", text or "")
    arabic = sum(1 for word in words if ARABIC_LETTER.search(word))
    latin = sum(1 for word in words if LATIN_LETTER.search(word) and not ARABIC_LETTER.search(word))
    return "Arabic" if arabic and arabic >= latin else "English"


def matches(terms, text):
    """:return: whether every folded term is in the folded ``text``"""
    folded = fold(text)
    return all(term in folded for term in terms)


def parse(text):
    """:return: ``(command, the words after it)``; ``(None, text)`` when the
    message does not start with a command"""
    text = (text or "").strip()
    first, _space, rest = text.partition(" ")
    if "\n" in first:
        first, _newline, more = first.partition("\n")
        rest = f"{more} {rest}".strip()
    command = BY_WORD.get(first.lower())
    if command is None:
        return None, text
    return command, " ".join(rest.split()) if command.name != "sql" else rest.strip()


def is_open(env, name):
    """:return: whether the current user may use the command ``name``"""
    user = env.user
    Conversation = env["nudge.conversation"]
    if name == "setting":
        return user.has_group("base.group_system")
    if name == "product":
        return "product.product" in env
    if name == "stock":
        return "stock.warehouse" in env and "product.product" in env
    if name == "file":
        return bool(env["nudge.source"].search_count([("state", "=", "ready")], limit=1))
    if name in ("rules", "debug"):
        return Conversation._can_develop()
    if name == "health":
        return user.has_group("nudge.group_nudge_manager")
    if name == "sql":
        tool = base.TOOLS.get("run_sql")
        return bool(tool) and tool.is_available(env, base.MODE_AGENT)
    if name == "count":
        return Conversation._can_use_agent()
    return name in BY_NAME


def texts(env):
    """:return: ``{name: (what it does, an example)}`` in the language of the person"""
    return {
        "help": (env._("The commands you can use here"), "/help"),
        "screen": (env._("A screen you can open, by its name: shown with the pointer"), env._("/screen invoices")),
        "setting": (env._("A setting of the apps, by its name: shown with the pointer"), env._("/setting multi currency")),
        "doc": (env._("Pages of the official Odoo documentation"), env._("/doc landed costs")),
        "file": (env._("Passages of the company files, with the file and the page"), env._("/file annual leave")),
        "find": (env._("A record by its name, number or reference, in any app; its kind first narrows it"),
                 env._("/find invoice: INV/2026/00042")),
        "product": (env._("Products like a name, a description, a code or a size"), env._("/product bolt 8 mm")),
        "stock": (env._("The stock of products, by warehouse"), env._("/stock cement")),
        "rules": (env._("What runs by itself on a kind of record: automations, scheduled actions, emails"),
                  env._("/rules sale.order")),
        "debug": (env._("Why an automation, a server action or a scheduled action failed: what the code names that "
                        "does not exist, the rights, the last errors"), env._("/debug payroll cron")),
        "tour": (env._("A recorded tour of a task, shown with the pointer"), env._("/tour create a quotation")),
        "health": (env._("Checks the database for duplicates, missing setup, risky rights and stuck jobs"), "/health"),
        "import": (env._("Match the columns of the CSV or Excel file you attach to the fields of a model, and try it"),
                   env._("/import (with the file attached)")),
        "sql": (env._("One SELECT, read only, at most 50 rows"), "/sql SELECT name FROM res_partner LIMIT 5"),
        "why": (env._("What stops this form from being saved: required fields, refused values, rights, companies"),
                "/why"),
        "field": (env._("What a field is, in words; with no words, the field you were in"),
                  env._("/field customer")),
        "here": (env._("What this screen is: the record, its state, its buttons"), "/here"),
        "count": (env._("A number about your records, worked out by the AI with your data only"),
                  env._("/count invoices unpaid this month")),
        "explain": (env._("A field, a term or this screen, explained by the AI in a few words"),
                    env._("/explain analytic account")),
    }


def listed(env):
    """:return: the commands the current user may use, for the message box"""
    described = texts(env)
    return [
        {
            "name": command.name,
            "words": list(command.words),
            "kind": command.kind,
            "hint": described[command.name][0],
            "example": described[command.name][1],
            "at_once": command.name in SCREEN_COMMANDS,
        }
        for command in COMMANDS if is_open(env, command.name)
    ]

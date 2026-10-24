"""The tools a call is given beyond those every call of its mode carries.

A model on a laptop reads a short list of tools faster, and picks the right
one more often; but what it is not given, it cannot do. Each mode has its
tools (``base.ESSENTIAL_BY_MODE``); the others come in groups. A group is
offered when the question holds one of its words, in Arabic or in English,
or when the model asks for it with more_tools: from then on it is offered
for the rest of the conversation.

NP-03: every model works this way now, with a larger core (``base.CORE_BY_MODE``):
a call carries the core of its mode and the groups its question needs
(:func:`routed`), never the whole list. A tool named in no group stays in every
call, so that nothing registered is out of reach; one the model calls without
being offered still runs when the person may use it, and is offered from then on.
"""
from . import base, commands

GROUPS = {
    "settings": {
        "about": "read, show and change the settings of the apps, as on the Settings screen",
        "tools": ("read_settings", "show_settings", "change_settings", "read_configuration"),
        "words": ("setting", "configur", "set up", "setup", "enable", "activat", "switch on", "turn on", "option",
                  "اعدادات", "ضبط", "فعل", "تفعيل", "خيار"),
    },
    "data": {
        "about": "SQL queries, spreadsheets of figures, Python run on the database, its profile",
        "tools": ("run_sql", "make_spreadsheet", "run_python", "search_database_profile", "aggregate_records",
                  "show_chart", "save_report"),
        "words": ("sql", "query", "spreadsheet", "excel", "sheet", "python", "script", "report", "chart", "graph", "plot", "pivot", "my reports",
                  "استعلام", "جدول", "اكسل", "بايثون", "سكربت", "تقرير", "مخطط", "رسم بياني", "احفظ", "خزن", "محوري"),
    },
    "reports": {
        "about": "a PDF report printed from a record, with the company's header",
        "tools": ("prepare_report", "prepare_report_design"),
        "words": ("pdf", "print", "report", "طباع", "اطبع", "تقرير", "مطبوع"),
    },
    "modules": {
        "about": "what a module does, the code of a custom module, a new module",
        "tools": ("describe_module", "read_module_code", "prepare_module_scaffold"),
        "words": ("module", "addon", "source code", "scaffold", "موديول", "وحده", "مديول"),
    },
    "assessment": {
        "about": "assess the whole database: what is set up, used, missing or wrong",
        "tools": ("assess_database",),
        "words": ("assess", "audit", "health", "evaluat", "قيم", "قيّم", "تقييم", "فحص"),
    },
    "products": {
        "about": "find products by their name, size, colour or a picture of them",
        "tools": ("search_products", "find_products"),
        "words": ("product", "item", "stock", "in stock", "inventory", "منتج", "منتجات", "صنف", "اصناف",
                  "مخزون", "مخزن", "مستودع"),
    },
    "import": {
        "about": "check a file before importing it",
        "tools": ("test_import", "match_import_columns"),
        "words": ("import", "csv", "xlsx", "استيراد", "استورد", "ملف"),
    },
    "meetings": {
        "about": "summarise a recorded meeting and make tasks, activities, events and a note from it",
        "tools": ("summarize_meeting", "meeting_to_records", "search_records", "find_record"),
        "words": ("meeting", "minutes", "recording", "audio", "voice note", "action item", "اجتماع", "محضر",
                  "تسجيل", "تسجيل صوتي", "ملخص", "لخص", "خلاصة", "خلاصه", "الاجتماع", "جلسة", "اجتماعنا"),
    },
    "documents": {
        "about": "make a draft vendor bill, invoice, quotation, purchase order, lead or contact from a PDF, "
                 "picture, Excel file or email",
        "tools": ("document_to_record", "find_record", "search_records"),
        "words": ("invoice", "bill", "receipt", "quotation", "purchase order", "from this file", "from this email",
                  "from this pdf", "enter this", "فاتورة", "فواتير", "عرض سعر", "امر شراء", "ايصال", "ادخل"),
    },
    "records": {
        "about": "find, read, create and change records, press their buttons, plans of many records",
        "tools": ("search_records", "read_record", "find_record", "aggregate_records", "filter_screen",
                  "open_record", "prepare_create", "prepare_write", "call_method", "prepare_plan", "bulk_apply",
                  "show_chart", "save_report"),
        "words": (),
    },
    "history": {
        "about": "what happened with a customer, an order, a project or a ticket: its history summarised",
        "tools": ("summarize_record_history",),
        "words": ("summar", "history", "what happened", "recap", "catch me up", "where are we",
                  "ملخص", "لخص", "تلخيص", "تاريخ", "ايش صار", "شو صار", "وين وصلنا"),
    },
    "setup": {
        "about": "set up a new database step by step: company, country and fiscal localisation, currency, "
                 "languages, apps, warehouses, users, fiscal year",
        "tools": ("read_setup", "prepare_setup_step"),
        "words": ("set up this database", "first setup", "setup assistant", "set up the database", "set this up",
                  "new database", "جهز قاعده", "جهز النظام", "تهيئه", "الاعداد الاولي", "اعداد اولي", "قاعده جديده",
                  "جهزلي", "جهز الداتابيس"),
    },
    "changes": {
        "about": "who changed a record and what, and putting a tracked field back as it was",
        "tools": ("record_changes", "prepare_revert"),
        "words": ("who changed", "who edited", "revert", "restore", "roll back", "rollback", "old value",
                  "previous value", "change back", "put back",
                  "مين غير", "مين عدل", "منو غير", "رجع", "ارجع", "استرجع", "استعاد", "القديم", "السابق", "تراجع"),
    },
    "screen": {
        "about": "show the steps on the person's screen with the pointer, write in a field of it",
        "tools": ("show_on_screen", "write_in_field"),
        "words": (),
    },
    # NP-03: what was in every call and is now brought by its words, as the groups above
    "automations": {
        "about": "build and try automation rules, server actions, scheduled actions",
        "tools": ("prepare_automation", "prepare_server_action", "prepare_scheduled_action", "try_automation"),
        "words": ("automat", "rule", "trigger", "when", "whenever", "if", "server action", "scheduled", "schedule",
                  "cron", "remind", "notify", "alert", "every day", "every morning", "every week", "every month",
                  "each time", "تلقائي", "اوتوماتيك", "اتمته", "قاعده", "عندما", "حين", "تذكير", "ذكر", "نبه",
                  "تنبيه", "اشعار", "كل يوم", "كل صباح", "كل اسبوع", "كل شهر", "كل ما"),
    },
    "emails": {
        "about": "emails sent on an event, designed emails, templates",
        "tools": ("prepare_email_template", "prepare_email_design", "prepare_email_review", "send_test_email"),
        "words": ("email", "e-mail", "mail", "newsletter", "template", "ايميل", "بريد", "رساله", "رسايل", "قالب",
                  "نشره"),
    },
    "screens": {
        "about": "new fields, buttons, changes to a screen",
        "tools": ("read_view", "prepare_view_change", "prepare_button", "prepare_field"),
        "words": ("field", "view", "form", "button", "column", "tab", "screen", "kanban", "mandatory", "required",
                  "readonly", "hide", "invisible", "حقل", "حقول", "شاشه", "زر", "زرار", "عمود", "واجهه", "تبويب",
                  "اجباري", "اخفاء", "اخفي", "نموذج"),
    },
    "approvals": {
        "about": "approval rules and flows",
        "tools": ("prepare_approval_rule", "prepare_approval_flow", "read_approval_rules"),
        "words": ("approv", "validat", "authoriz", "sign off", "who can", "موافق", "اعتماد", "يعتمد", "صلاحي",
                  "توقيع", "يوافق"),
    },
    "debugging": {
        "about": "why a rule or an action failed, and its fix",
        "tools": ("diagnose_failure", "prepare_code_fix"),
        "words": ("error", "fail", "broke", "traceback", "bug", "crash", "exception", "not working",
                  "doesn't work", "does not work", "خطا", "غلط", "مشكله", "فشل", "عطل", "ما بيشتغل", "مش شغال",
                  "ما يشتغل"),
    },
    "website": {
        "about": "build, check and fix a website or a shop",
        "tools": ("start_website_builder", "build_shop", "check_site", "fix_site"),
        "words": ("website", "web site", "site", "web page", "landing", "shop", "ecommerce", "e-commerce",
                  "online store", "موقع", "متجر", "ويب", "صفحه"),
    },
    "bulk": {
        "about": "one change or AI work on many records",
        "tools": ("prepare_plan", "bulk_apply"),
        "words": ("bulk", "mass", "batch", "many records", "all", "every", "each", "جميع", "كافه", "كل",
                  "بالجمله", "دفعه وحده"),
    },
    "tasks": {
        "about": "AI work done again and again",
        "tools": ("prepare_task",),
        "words": ("every day", "every morning", "every week", "every month", "each morning", "daily", "weekly",
                  "monthly", "whenever", "recurring", "كل يوم", "كل صباح", "كل اسبوع", "كل شهر", "يومي", "اسبوعي",
                  "شهري", "دوري"),
    },
    "tours": {
        "about": "a guided tour of an app",
        "tools": ("show_tour",),
        "words": ("tour", "walk me", "walkthrough", "guide me", "show me around", "training", "جوله", "علمني",
                  "تدريب", "دلني"),
    },
    "studio": {
        "about": "point inside Studio",
        "tools": ("show_in_studio",),
        "words": ("studio", "ستوديو", "استوديو"),
    },
}

NP03_GROUPS = frozenset({"automations", "emails", "screens", "approvals", "debugging", "website", "bulk", "tasks",
                         "tours", "studio"})
# NP-03: Developer mode builds. A question of that mode that names none of these is given the
# automations, the work it is most often for, rather than nothing to build with
BUILDERS = frozenset({"automations", "emails", "screens", "reports", "approvals", "debugging", "modules", "website",
                      "studio"})
# A command typed by the person names its group: "/setting", "/import"
COMMAND_GROUPS = {
    "setting": "settings", "product": "products", "stock": "products", "debug": "debugging", "tour": "tours",
    "health": "assessment", "import": "import", "sql": "data", "field": "screens", "rules": "automations",
}


def routed_tools():
    """:return: the tools that come by a group, those a companion module adds included
    (nudge_builder); every other tool is in every call"""
    return {tool for group in GROUPS.values() for tool in group["tools"]}


def _forms(word):
    """:return: how a word of a group is compared: folded, and for Arabic its root"""
    folded = commands.fold(word)
    if " " in folded:
        return folded
    return commands.root_of(folded)


FORMS = {name: tuple(_forms(word) for word in group["words"]) for name, group in GROUPS.items()}


def asked(text):
    """:return: the names of the groups whose words ``text`` holds"""
    folded = commands.fold(text)
    words = folded.split()
    roots = [commands.root_of(word) for word in words]
    found = []
    for name, forms in FORMS.items():
        for form in forms:
            if " " in form:
                hit = form in folded
            elif form.isascii():
                hit = any(word.startswith(form) for word in words)
            else:
                # An Arabic root meets the roots written onto: فعل, تفعيل
                hit = any(form in root for root in roots if len(form) >= 3) or form in roots
            if hit:
                found.append(name)
                break
    return found


# The words that bring a group, not what it is about: left out of a search (those of the groups of
# NP-03, such as shop, field or email, are often what the search is about, and stay in it)
TRIGGERS = {form for name, forms in FORMS.items() if name not in NP03_GROUPS for form in forms if " " not in form}


def tools_of(names):
    """:return: the tools of the groups named"""
    return {tool for name in names if name in GROUPS for tool in GROUPS[name]["tools"]}


def kept(conversation):
    """:return: the groups the model asked for in this conversation"""
    return [name for name in (conversation.sudo().tool_groups or "").split(",") if name in GROUPS]


def offered(conversation, text):
    """:return: the tools a small model is given beyond those of its mode:
    the groups of the question, and those asked for in this conversation"""
    return tools_of(set(asked(text)) | set(kept(conversation)))


def routed(conversation, mode, texts, screen=None, context="", command=None, attachment=False, called=()):
    """NP-03: the tools a call is given beyond the core of its mode, with no AI.

    :param texts: the question, and the one before it: an answer to a question of Nudge ("the sales
        manager") goes on with the work of the question it answers
    :param screen: the snapshot sent with the question: the Import screen brings the import tools
    :param context: the context block of the question: a report asked as a spreadsheet, an
        explanation asked on the screen
    :param command: the command the question was typed with ("/import")
    :param attachment: whether a file came with the question
    :param called: the tools called lately in this conversation: the work goes on with them
    :return: the names of the tools, sorted; each still passes ``Tool.is_available``"""
    names = set(asked("\n".join(text for text in texts if text))) | set(kept(conversation))
    screen = screen if isinstance(screen, dict) else {}
    if screen.get("import"):
        names.add("import")
    if screen.get("studio"):
        names.add("studio")
    # The context block is the first line of the context of the question; a key is written "key":
    context = (context or "").split("\n", 1)[0]
    if '"report":' in context:
        names.add("data")
    if '"explain":' in context:
        names.add("screen")
    if attachment:
        names |= {"documents", "products"}
    if command in COMMAND_GROUPS:
        names.add(COMMAND_GROUPS[command])
    if mode == base.MODE_DEV and not names & BUILDERS:
        names.add("automations")
    return sorted(tools_of(names) | (set(called) & routed_tools()))


def in_mode(tool, mode):
    """:return: whether ``tool`` is one of ``mode``, whoever asks"""
    return mode in tool.modes or (mode == base.MODE_DEV and base.MODE_AGENT in tool.modes)


def largest(mode):
    """:return: the characters of the largest group beyond the tools of
    ``mode``: what the context of a small model keeps room for"""
    core = base.ESSENTIAL_BY_MODE[mode]
    return max((
        sum(len(base.dump(base.TOOLS[tool].definition())) for tool in group["tools"]
            if tool in base.TOOLS and tool not in core and in_mode(base.TOOLS[tool], mode))
        for group in GROUPS.values()), default=0)


@base.register
class MoreTools(base.Tool):
    name = "more_tools"
    modes = (base.MODE_TALK, base.MODE_AGENT, base.MODE_DEV)
    small_models_only = True
    description = (
        "Ask for more tools when none of yours does what the person asked: they are yours from "
        "your next answer on, in this conversation. Groups: "
        + "; ".join(f"{name}: {group['about']}" for name, group in GROUPS.items()) + "."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["groups"],
        "properties": {
            "groups": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "items": {"type": "string", "enum": list(GROUPS)},
            },
        },
    }

    def run(self, env, arguments, screen):
        conversation = env["nudge.conversation"].browse(env.context.get("nudge_conversation_id")).exists()
        if not conversation:
            return base.ToolResult("There is no conversation to give tools to.", is_error=True)
        mode = env.context.get("nudge_mode") or base.MODE_TALK
        given = [name for name in tools_of(arguments["groups"])
                 if name in base.TOOLS and base.TOOLS[name].is_available(env, mode)]
        if not given:
            return base.ToolResult(
                "None of these tools is open to this person, or in this mode.", is_error=True)
        groups = sorted(set(kept(conversation)) | set(arguments["groups"]))
        conversation.sudo().tool_groups = ",".join(groups)
        return base.ToolResult(
            f"These tools are yours from your next answer: {', '.join(sorted(given))}. Call the one "
            "that does what was asked.")

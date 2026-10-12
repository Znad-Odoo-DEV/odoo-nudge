"""The tools a small model is given beyond those of its mode.

A model on a laptop reads a short list of tools faster, and picks the right
one more often; but what it is not given, it cannot do. Each mode has its
tools (``base.ESSENTIAL_BY_MODE``); the others come in groups. A group is
offered when the question holds one of its words, in Arabic or in English,
or when the model asks for it with more_tools: from then on it is offered
for the rest of the conversation.
"""
from . import base, commands

GROUPS = {
    "settings": {
        "about": "read, show and change the settings of the apps, as on the Settings screen",
        "tools": ("read_settings", "show_settings", "change_settings", "read_configuration"),
        "words": ("setting", "configur", "enable", "activat", "switch on", "turn on", "option",
                  "اعدادات", "ضبط", "فعل", "تفعيل", "خيار"),
    },
    "data": {
        "about": "SQL queries, spreadsheets of figures, Python run on the database, its profile",
        "tools": ("run_sql", "make_spreadsheet", "run_python", "search_database_profile", "aggregate_records"),
        "words": ("sql", "query", "spreadsheet", "excel", "sheet", "python", "script", "report", "chart",
                  "استعلام", "جدول", "اكسل", "بايثون", "سكربت", "تقرير", "مخطط", "رسم بياني"),
    },
    "reports": {
        "about": "a PDF report printed from a record, with the company's header",
        "tools": ("prepare_report",),
        "words": ("pdf", "print", "report", "طباع", "اطبع", "تقرير", "مطبوع"),
    },
    "modules": {
        "about": "what a module does, and the code of a custom module",
        "tools": ("describe_module", "read_module_code"),
        "words": ("module", "addon", "source code", "موديول", "وحده", "مديول"),
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
        "tools": ("test_import",),
        "words": ("import", "csv", "xlsx", "استيراد", "استورد", "ملف"),
    },
    "records": {
        "about": "find, read, create and change records, press their buttons, plans of many records",
        "tools": ("search_records", "read_record", "find_record", "aggregate_records", "filter_screen",
                  "open_record", "prepare_create", "prepare_write", "call_method", "prepare_plan"),
        "words": (),
    },
    "screen": {
        "about": "show the steps on the person's screen with the pointer, write in a field of it",
        "tools": ("show_on_screen", "write_in_field"),
        "words": (),
    },
}


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


# The words that bring a group, not what it is about: left out of a search
TRIGGERS = {form for forms in FORMS.values() for form in forms if " " not in form}


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

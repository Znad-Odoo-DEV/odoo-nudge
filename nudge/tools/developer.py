"""Building Odoo, in Developer mode: automation rules, server actions, emails
sent on an event, and taking them away.

Open to an administrator the switch is on for, and only in Developer mode.
Nothing is made before the person has seen, in full, what is about to be
made: the trigger, the code, the recipients, the text of the email. What a
developer reads before writing, the tools read: what exists already, whether
the code compiles.
"""
import ast
import builtins
import difflib
import re
from datetime import timedelta

import pytz
from lxml import etree

from odoo import Command, _
from odoo.exceptions import AccessError
from odoo.tools import plaintext2html
from odoo.tools import safe_eval as safe_eval_module
from odoo.tools.safe_eval import test_python_expr

from odoo import fields as odoo_fields

from . import base, schema
from .records import has_field_access  # noqa: E402
from .actions import VALUES_SCHEMA, read_moment, read_value, read_values, to_write
from .records import (
    FIELD_PATTERN, MODEL_PATTERN, OPERATORS, PATH_PATTERN, Refused, build_domain, cut, format_value, get_model,
    field_label, get_records, is_developing, model_label, refusing, target_field,
)

TRIGGERS = {
    "on_create": "when a record is created",
    "on_create_or_write": "when a record is created or changed",
    "on_unlink": "when a record is deleted",
    "on_archive": "when a record is archived",
    "on_unarchive": "when a record is unarchived",
    "on_time": "some time before or after a date of the record",
    "on_time_created": "some time after a record is created",
    "on_time_updated": "some time after a record was last changed",
}
TIME_TRIGGERS = ("on_time", "on_time_created", "on_time_updated")
DELAY_UNITS = ("minutes", "hour", "day", "month")
ACTION_KINDS = ("code", "update", "email", "activity")
EMAIL_WAYS = ("email", "comment", "note")
ACTIVITY_UNITS = ("days", "weeks", "months")
# The fields an update action writes by itself; anything else is code
UPDATABLE_TYPES = ("char", "text", "html", "integer", "float", "monetary", "boolean", "date", "datetime", "selection")
# What may be deleted: what Nudge builds, never records of the business
DELETABLE = ["base.automation", "ir.actions.server", "mail.template", "ir.cron", "ir.ui.view", "ir.model.fields",
             "ir.actions.report"]
# What undoing leaves as it is, named on its card, at most
MAX_LEFT_SHOWN = 5
# Where what Nudge makes is registered; a view or a field of a module is never deleted
CUSTOM_MODULES = ("nudge_custom", "studio_customization", "__export__")
INTERVAL_UNITS = ("minutes", "hours", "days", "weeks", "months")
# "monday 08:00": the next Monday, at eight in the time of the person
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
WEEKDAY_HOUR = re.compile(r"([01]?[0-9]|2[0-3])(?::([0-5][0-9]))?")
DEFAULT_HOUR = (8, 0)


def next_weekday(env, text, now=None):
    """:param text: "monday", "monday 08:00"
    :return: the next such day at that hour (eight by default), in the time
        of the person, as Odoo keeps it (universal, naive); None when ``text``
        does not start with a day of the week"""
    words = text.lower().replace(",", " ").split()
    if not words or words[0] not in WEEKDAYS:
        return None
    hour, minute = DEFAULT_HOUR
    if len(words) > 1:
        found = WEEKDAY_HOUR.fullmatch(words[1])
        if not found:
            raise Refused(f"{words[1]!r} is not an hour: write it as 08:00.")
        hour, minute = int(found.group(1)), int(found.group(2) or 0)
    zone = pytz.timezone(env.user.tz or env.context.get("tz") or "UTC")
    here = pytz.utc.localize(now or odoo_fields.Datetime.now()).astimezone(zone)
    days = (WEEKDAYS.index(words[0]) - here.weekday()) % 7
    moment = zone.localize(
        (here + timedelta(days=days)).replace(hour=hour, minute=minute, second=0, microsecond=0, tzinfo=None))
    if moment <= here:
        moment = zone.localize((moment + timedelta(days=7)).replace(tzinfo=None))
    return moment.astimezone(pytz.utc).replace(tzinfo=None)


def every_text(env, number, unit):
    """:return: how often, as the card says it: "week" or "3 days" """
    if number == 1:
        return {
            "minutes": _("minute"), "hours": _("hour"), "days": _("day"),
            "weeks": _("week"), "months": _("month"),
        }[unit]
    return {
        "minutes": _("%s minutes", number), "hours": _("%s hours", number),
        "days": _("%s days", number), "weeks": _("%s weeks", number),
        "months": _("%s months", number),
    }[unit]
# A placeholder of an email names a field of the record, nothing else
PLACEHOLDER = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")
FIELD_PLACEHOLDER = re.compile(r"^object(\.[a-z_][a-z0-9_]*)+$")
EMAIL = re.compile(r"^[^@\s<>,]+@[^@\s<>,]+\.[^@\s<>,]+$")
MAX_CODE_CHARS = 6000
MAX_BODY_CHARS = 6000
MAX_ACTIONS = 5
MAX_CONDITIONS = 8
MAX_RECIPIENTS = 8
MAX_LISTED = 25
# Of each kind, given with a question of Developer mode: a small model reads them all
MAX_BUILT_SHOWN = 3

CONDITION_SCHEMA = {
    "type": "array",
    "maxItems": MAX_CONDITIONS,
    "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["field", "operator", "value"],
        "properties": {
            "field": {"type": "string", "pattern": PATH_PATTERN},
            "operator": {"type": "string", "enum": list(OPERATORS)},
            "value": {"type": "string", "maxLength": 200},
        },
    },
}
EMAIL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["subject", "body", "to"],
    "properties": {
        "to": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_RECIPIENTS,
            "items": {"type": "string", "maxLength": 120},
            "description": (
                "Who receives it: fields of the record that hold a contact or an address "
                "(partner_id, user_id.partner_id, email), or addresses."
            ),
        },
        "cc": {
            "type": "array",
            "maxItems": MAX_RECIPIENTS,
            "items": {"type": "string", "maxLength": 120},
            "description": "In copy, the same way.",
        },
        "subject": {"type": "string", "minLength": 1, "maxLength": 200},
        "body": {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_BODY_CHARS,
            "description": (
                "Plain text or HTML, in the language of the people who receive it. "
                "{{ object.field }} puts a value of the record in; nothing else goes between braces."
            ),
        },
        "send_as": {
            "type": "string",
            "enum": list(EMAIL_WAYS),
            "description": "email (default): sent out. comment: posted in the chatter and sent. note: posted as a note.",
        },
    },
}
ACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind"],
    "properties": {
        "kind": {
            "type": "string",
            "enum": list(ACTION_KINDS),
            "description": "code: Python. update: write a field. email: send an email. activity: schedule an activity.",
        },
        "name": {"type": "string", "maxLength": 120},
        "code": {
            "type": "string",
            "maxLength": MAX_CODE_CHARS,
            "description": (
                "For code. Runs with env, model, record, records, Command, datetime, dateutil, time, "
                "log(message), UserError, float_compare. Ends with action = {...} to open something."
            ),
        },
        "field": {"type": "string", "pattern": PATH_PATTERN, "description": "For update: the field written."},
        "value": {
            "type": "string",
            "maxLength": 1000,
            "description": "For update: the value, as a person would type it; true or false for a checkbox.",
        },
        "expression": {
            "type": "string",
            "maxLength": 1000,
            "description": (
                "For update, instead of value, when the value depends on the record: a Python "
                "expression computed on it. A fixed value is a value: archiving is active, false."),
        },
        "template_id": {"type": "integer", "minimum": 1, "description": "For email: an existing template."},
        "email": dict(EMAIL_SCHEMA, description="For email: the template to make, when there is none."),
        "summary": {"type": "string", "maxLength": 200, "description": "For activity."},
        "note": {"type": "string", "maxLength": 2000, "description": "For activity."},
        "days": {"type": "integer", "minimum": 0, "maximum": 365, "description": "For activity: due in."},
        "user_field": {
            "type": "string",
            "pattern": PATH_PATTERN,
            "description": "For activity: the field of the record that holds who does it; the person otherwise.",
        },
    },
}


# ----------------------------------------------------------------- reading

def check_developing(env):
    if not is_developing(env):
        raise Refused("Developer mode is not open to this person, or this is not Developer mode.")


def ir_model(env, model):
    return env["ir.model"].sudo()._get(model._name)


def automations_installed(env):
    if "base.automation" not in env:
        raise Refused(
            "Automation rules are not installed here: the app Automation Rules (base_automation) "
            "is needed. Tell the person; a server action can still be made.")


# What safe_eval refuses, and how it is written instead
FORBIDDEN_TOLD = {
    "STORE_ATTR": ("A field is written with record.write({'active': False}), never record.active = False. "
                   "A fixed value is best an update action instead of code: "
                   '{"kind": "update", "field": "active", "value": false}.'),
    "IMPORT_NAME": "Nothing is imported in an action: datetime, dateutil, time and Command are given.",
    "'return' outside function": ("An action is not a function: no def, no return; write the lines "
                                  "themselves, with record (records for several)."),
}
# What the code of a server action is given when it runs (ir.actions.server,
# base.automation): any other name it reads fails then
CODE_NAMES = frozenset({
    "env", "model", "record", "records", "time", "datetime", "dateutil", "timezone", "float_compare",
    "b64encode", "b64decode", "Command", "UserError", "log", "_logger", "uid", "user", "payload",
})
# How a person is reminded from code, told where code reached for an email
REMINDING = re.compile(r"mail|send|notif|remind", re.IGNORECASE)
REMIND_TOLD = (
    "A person is reminded with an activity: rec.activity_schedule('mail.mail_activity_data_todo', "
    "user_id=rec.user_id.id, summary='…'); the chatter with rec.message_post(body='…').")
# Calls that only read: code made of them alone changes nothing
READING_CALLS = frozenset({
    "search", "search_count", "search_read", "search_fetch", "browse", "exists", "mapped", "filtered",
    "filtered_domain", "sorted", "read", "read_group", "ensure_one", "sudo", "with_context", "with_user",
    "with_company", "ref", "get", "get_param", "fields_get", "today", "now", "utcnow", "context_today",
    "timedelta", "relativedelta", "date", "strftime", "strptime", "isoformat", "float_compare",
    "b64encode", "b64decode", "UserError",
})
# Calls whose first argument is a domain
DOMAIN_CALLS = ("search", "search_count", "search_read", "search_fetch", "filtered_domain")
# Calls that give records of the model they are called on
SAME_RECORDS = frozenset({
    "search", "browse", "exists", "filtered", "filtered_domain", "sorted", "sudo", "with_context",
    "with_user", "with_company",
})


def compile_code(code, env=None, model_name=None):
    """The code of a server action, an automation rule or a scheduled action.
    :param env: to know the models the code names, when given
    :param model_name: the model of the action, which record and records hold
    :return: what is wrong with it, or "" """
    text = (code or "").strip()
    if not text:
        # An action of kind code came with its lines elsewhere, or none: said where they go
        return ("The code is empty: its lines go in code. A field set to a fixed value needs no code: "
                '{"kind": "update", "field": "active", "value": "false"}.')
    error = test_python_expr(text, mode="exec")
    if error:
        error = str(error)
        for opcode, told in FORBIDDEN_TOLD.items():
            if opcode in error:
                return f"{error} {told}"
        return error
    tree = ast.parse(text)
    defined = [node.name for node in tree.body
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    if defined and all(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                       or (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
                       for node in tree.body):
        # def archive_contact(record): ... was made, never called: the button archived nothing
        return (f"The code only defines {', '.join(defined)}, which nothing calls: it does nothing "
                "when it runs. An action is not a function: no def, no return; write the lines "
                "themselves, with record (records for several).")
    unknown = unknown_names(tree)
    if unknown:
        # A small model wrote object and self: the action failed when run
        told = (f"{', '.join(unknown)} is not known when the code runs. It knows: "
                f"{', '.join(sorted(CODE_NAMES))}; the record is record (records for several).")
        if any(REMINDING.search(name) for name in unknown):
            # mail.send(...) was refused, and nothing showed the way to a reminder
            told += f" {REMIND_TOLD}"
        return told
    builtin = set(getattr(safe_eval_module, "_BUILTINS", vars(builtins)))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "env"
                and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str)
                and env is not None and node.slice.value not in env):
            # env['ir.lang'] for res.lang: the rule failed on every new contact
            return f"There is no model {node.slice.value!r} here: describe_model finds the right one."
        if (isinstance(node, ast.Attribute) and node.attr in ("now", "today", "utcnow")
                and isinstance(node.value, ast.Name) and node.value.id == "datetime"):
            return ("datetime is the module here: datetime.datetime.now(), or datetime.date.today().")
        if (isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "action"
                                                 for target in node.targets)
                and not _opens_something(node.value)):
            # action = dict(active='False') archived nothing
            return ("action = {...} only opens a window, with its type: to change the record, write it, "
                    "such as record.write({'active': False}).")
        wrong = _wrong_domain(node)
        if wrong:
            return wrong
    if env is not None and model_name and model_name in env:
        records = _records_named(tree, model_name)
        for node in ast.walk(tree):
            wrong = _wrong_write(env, node, records)
            if wrong:
                return wrong
    changes = (
        node for node in ast.walk(tree)
        if isinstance(node, ast.Raise)
        or (isinstance(node, ast.Call) and _called(node) not in READING_CALLS
            and not (isinstance(node.func, ast.Name) and node.func.id in builtin))
        or (isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "action"
                                                 for target in node.targets))
    )
    if not any(changes):
        # lang = 'ar' was made into an automation rule that changed nothing; a
        # search alone was a rule that read the orders and left them as they were
        return ("The code changes nothing: it calls no method that changes something and writes no "
                "field. Write it, such as record.write({'lang': 'ar_001'}), or use an update action "
                "instead of code.")
    return ""


def _called(call):
    """:return: the name of what ``call`` calls: write for record.write(...)"""
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return call.func.id if isinstance(call.func, ast.Name) else ""


def _wrong_domain(node):
    """:return: what is wrong with the domain written in a search(...), or "" """
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in DOMAIN_CALLS):
        return ""
    domain = node.args[0] if node.args else next(
        (keyword.value for keyword in node.keywords if keyword.arg == "domain"), None)
    if not isinstance(domain, (ast.List, ast.Tuple)):
        return ""
    for condition in domain.elts:
        if isinstance(condition, ast.Constant) and condition.value in ("&", "|", "!"):
            continue
        if isinstance(condition, ast.Constant) or (
                isinstance(condition, (ast.Tuple, ast.List)) and len(condition.elts) != 3):
            # ('date_order <=', value): the search failed every time the action ran
            return (f"The domain of {node.func.attr}() holds {ast.unparse(condition)}, which is not a "
                    "condition: a condition is three parts, (field, operator, value), such as "
                    "('date_order', '<=', value); '&', '|' and '!' join them.")
    return ""


def _records_of(node, records):
    """:return: the model of the records ``node`` holds, when it is known
    :param records: ``{name: model}`` of the names that hold records"""
    while (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
           and node.func.attr in SAME_RECORDS):
        node = node.func.value
    if isinstance(node, ast.Name):
        return records.get(node.id)
    if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "env"
            and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str)):
        return node.slice.value
    return None


def _records_named(tree, model_name):
    """:return: ``{name: model}`` of the names that hold records of the action's
    model: record and records, and what loops over them, unless set otherwise"""
    stored = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            stored[node.id] = stored.get(node.id, 0) + 1
    given = {name: model_name for name in ("record", "records") if name not in stored}
    looped = {}
    for node in ast.walk(tree):
        if (isinstance(node, (ast.For, ast.comprehension)) and isinstance(node.target, ast.Name)
                and stored.get(node.target.id) == 1 and _records_of(node.iter, given) == model_name):
            looped[node.target.id] = model_name
    return dict(given, **looped)


def _wrong_write(env, node, records):
    """:return: what is wrong with the values of a write({...}) on records of a
    known model, or "" """
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "write" and len(node.args) == 1):
        return ""
    model_name = _records_of(node.func.value, records)
    if not model_name or model_name not in env:
        return ""
    written = node.args[0]
    if isinstance(written, ast.Dict):
        pairs = list(zip(written.keys, written.values))
    elif isinstance(written, ast.Call) and isinstance(written.func, ast.Name) and written.func.id == "dict":
        pairs = [(ast.Constant(keyword.arg), keyword.value) for keyword in written.keywords if keyword.arg]
    else:
        return ""
    model = env[model_name]
    for key, value in pairs:
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
            continue
        field = model._fields.get(key.value)
        if field is None:
            close = difflib.get_close_matches(key.value, list(model._fields), n=3)
            return (f"{model_name} has no field {key.value!r}"
                    + (f"; close to it: {', '.join(map(repr, close))}" if close else "")
                    + ". describe_model gives its fields.")
        if field.type == "selection" and isinstance(value, ast.Constant) and value.value not in (False, None, ""):
            keys = [choice[0] for choice in model.fields_get([key.value], ["selection"])[key.value].get("selection") or []]
            if keys and value.value not in keys:
                # record.write({'lang': 'ar'}): Arabic is ar_001, and the rule failed on every contact
                return (f"{key.value} is a choice, and {value.value!r} is not one of its keys: "
                        + ", ".join(map(repr, keys[:MAX_LISTED])) + (", …" if len(keys) > MAX_LISTED else "") + ".")
    return ""


def _opens_something(value):
    """:return: whether what is given to ``action`` names the type of an action"""
    if isinstance(value, ast.Dict):
        return any(isinstance(key, ast.Constant) and key.value == "type" for key in value.keys)
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "dict":
        return any(keyword.arg == "type" for keyword in value.keywords)
    return True


def unknown_names(tree):
    """:return: the names the code reads that it neither sets nor is given"""
    given = set(CODE_NAMES) | set(getattr(safe_eval_module, "_BUILTINS", vars(builtins)))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            given.add(node.id)
        elif isinstance(node, ast.arg):
            given.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            given.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            given.add(node.name)
    return sorted({node.id for node in ast.walk(tree)
                   if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in given})


def in_words(model, conditions, match):
    """:return: the conditions as the person reads them"""
    labels = model.fields_get([condition["field"].split(".")[0] for condition in conditions], ["string"])
    parts = []
    for condition in conditions:
        label = labels.get(condition["field"].split(".")[0], {}).get("string") or condition["field"]
        rest = condition["field"].split(".")[1:]
        if rest:
            label += " › " + " › ".join(rest)
        value = condition.get("value", "")
        parts.append(f"{label} {condition['operator']}" + (f" {value}" if value else ""))
    return (" or " if match == "any" else " and ").join(parts)


def name_words(words):
    """:return: the words a name is matched on, one by one: "contact archive"
    found nothing named "Archive contact" when matched as one phrase"""
    found = [word for word in re.split(r"[\s,;:.!?،؟]+", words or "") if len(word) > 1]
    return found or [(words or "").strip()]


def named(Model, field, words, domain=()):
    """:return: the records named with any of ``words``, those named with most
    of them first"""
    leaves = [(field, "ilike", word) for word in words]
    records = Model.search(list(domain) + ["|"] * (len(leaves) - 1) + leaves, limit=MAX_LISTED * 4)
    folded = [word.lower() for word in words]
    return records.sorted(
        lambda record: -sum(word in (record[field] or "").lower() for word in folded))[:MAX_LISTED]


def built_on(env, model_name, words="", limit=MAX_BUILT_SHOWN):
    """:return: the automation rules, server actions and scheduled actions on
    ``model_name``, one line each with its id, those named with most of
    ``words`` first; at most ``limit`` of each"""
    folded = [word.lower() for word in name_words(words)] if words else []

    def ranked(records, field):
        return records.sorted(lambda record: -sum(word in (record[field] or "").lower() for word in folded))[:limit]

    lines = []
    try:
        if "base.automation" in env:
            rules = env["base.automation"].with_context(active_test=False).search(
                [("model_name", "=", model_name)], limit=MAX_LISTED * 4)
            lines += [f"rule #{rule.id} {rule.name!r}, {TRIGGERS.get(rule.trigger, rule.trigger)}"
                      + ("" if rule.active else " (off)") for rule in ranked(rules, "name")]
        Action = env["ir.actions.server"]
        domain = [("usage", "=", "ir_actions_server"), ("model_name", "=", model_name)]
        if "base_automation_id" in Action._fields:
            domain.append(("base_automation_id", "=", False))
        actions = Action.search(domain, limit=MAX_LISTED * 4)
        lines += [f"server action #{action.id} {action.name!r} ({action.state})" for action in ranked(actions, "name")]
        crons = env["ir.cron"].with_context(active_test=False).search(
            [("model_id.model", "=", model_name)], limit=MAX_LISTED * 4)
        lines += [f"scheduled action #{cron.id} {cron.cron_name!r}, every {cron.interval_number} "
                  f"{cron.interval_type}" + ("" if cron.active else " (off)") for cron in ranked(crons, "cron_name")]
    except AccessError:
        return []
    return lines


@base.register
class ReadAutomations(base.Tool):
    name = "read_automations"
    modes = (base.MODE_DEV,)
    group = "base.group_system"
    description = (
        "What runs by itself on a kind of record here: the automation rules with their trigger, "
        "conditions and actions (code included), the server actions in its Actions menu, and "
        "its email templates. Read it before building on a model: what exists is changed or "
        "extended, not made twice. With model: that model only. With words: whatever is named "
        "with them, on any model."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "model": {"type": "string", "pattern": MODEL_PATTERN},
            "words": {"type": "string", "minLength": 2, "maxLength": 80},
        },
    }

    def is_available(self, env, mode=base.MODE_TALK):
        return super().is_available(env, mode) and env["nudge.conversation"]._can_develop()

    @refusing
    def run(self, env, arguments, screen):
        check_developing(env)
        model_name = arguments.get("model")
        words = arguments.get("words")
        if not model_name and not words:
            raise Refused("Give a model, or words.")
        model = get_model(env, model_name) if model_name else None
        words = None if model is not None else name_words(words)
        lines = []
        if "base.automation" in env:
            Rule = env["base.automation"].with_context(active_test=False)
            rules = (Rule.search([("model_name", "=", model._name)], limit=MAX_LISTED) if model is not None
                     else named(Rule, "name", words))
            lines.append(f"Automation rules: {len(rules)}")
            for rule in rules:
                head = f"- #{rule.id} {rule.name} on {rule.model_name}: {TRIGGERS.get(rule.trigger, rule.trigger)}"
                if rule.trigger_field_ids:
                    head += f", watching {', '.join(rule.trigger_field_ids.mapped('name'))}"
                if rule.filter_domain and rule.filter_domain.strip() not in ("[]", ""):
                    head += f", when {rule.filter_domain}"
                if not rule.active:
                    head += " (off)"
                lines.append(head)
                for action in rule.action_server_ids:
                    lines.append(f"  · {action.name} ({action.state})" + self._detail(action))
        else:
            lines.append("Automation rules: the app Automation Rules is not installed.")
        Action = env["ir.actions.server"]
        domain = [("usage", "=", "ir_actions_server")]
        if "base_automation_id" in Action._fields:
            # The actions of a rule are listed under the rule
            domain.append(("base_automation_id", "=", False))
        actions = (Action.search(domain + [("model_name", "=", model._name)], limit=MAX_LISTED) if model is not None
                   else named(Action, "name", words, domain))
        lines.append(f"Server actions: {len(actions)}")
        for action in actions:
            where = " (in the Actions menu)" if action.binding_model_id else ""
            lines.append(f"- #{action.id} {action.name} on {action.model_name} ({action.state}){where}" + self._detail(action))
        Cron = env["ir.cron"].with_context(active_test=False)
        crons = (Cron.search([("model_id.model", "=", model._name)], limit=MAX_LISTED) if model is not None
                 else named(Cron, "cron_name", words))
        lines.append(f"Scheduled actions: {len(crons)}")
        for cron in crons:
            lines.append(
                f"- #{cron.id} {cron.cron_name} on {cron.model_id.model}: every {cron.interval_number} "
                f"{cron.interval_type}, next {cron.nextcall}, as {cron.user_id.name}"
                + ("" if cron.active else " (off)") + self._detail(cron.ir_actions_server_id))
        if "mail.template" in env:
            Template = env["mail.template"].with_context(active_test=False)
            templates = (Template.search([("model", "=", model._name)], limit=MAX_LISTED) if model is not None
                         else named(Template, "name", words))
            lines.append(f"Email templates: {len(templates)}")
            for template in templates:
                to = ", ".join(filter(None, (template.partner_to, template.email_to)))
                lines.append(f"- #{template.id} {template.name} on {template.model}: subject {template.subject!r}, to {to or '?'}"
                             + (f", cc {template.email_cc}" if template.email_cc else "") + ("" if template.active else " (off)"))
        return base.ToolResult("\n".join(lines))

    @staticmethod
    def _detail(action):
        if action.state == "code":
            return "\n    " + "\n    ".join((action.code or "").strip().splitlines()[:40])
        if action.state == "object_write":
            return f": {action.update_path} ← {action.value!r} ({action.evaluation_type})"
        if action.state == "mail_post":
            return f": template #{action.template_id.id} {action.template_id.name!r}, as {action.mail_post_method}"
        if action.state == "next_activity":
            return f": {action.activity_summary!r}"
        return ""


@base.register
class CheckPythonCode(base.Tool):
    name = "check_python_code"
    modes = (base.MODE_DEV,)
    group = "base.group_system"
    description = (
        "Whether the code of a server action compiles, and what is wrong with it when it does "
        "not, with the line. Check code with it before putting it on a card."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["code"],
        "properties": {"code": {"type": "string", "maxLength": MAX_CODE_CHARS}},
    }

    def is_available(self, env, mode=base.MODE_TALK):
        return super().is_available(env, mode) and env["nudge.conversation"]._can_develop()

    @refusing
    def run(self, env, arguments, screen):
        check_developing(env)
        error = compile_code(arguments["code"], env)
        return base.ToolResult(error or "The code compiles.", is_error=bool(error))


# ---------------------------------------------------------------- building

DESIGN_FIRST = (
    "Nudge: Emails is installed here: every email is a designed one. Make it with "
    "prepare_email_design in the style the person named, classic when they named none (classic: a "
    "coloured band and a clean table; modern: a large coloured header and rounded cards; minimal: "
    "quiet, a thin line of colour; formal: a letter with the logo on top), then give its "
    "template_id here."
)


def designed_emails_here(env):
    """:return: whether emails are made designed, by Nudge: Emails"""
    return "nudge.email.flow" in env


def path_label(model, path):
    """:return: a path of fields as the person reads it: Salesperson › Email"""
    labels = []
    for name in path.split("."):
        field = model._fields.get(name)
        if field is None:
            return path
        labels.append(field_label(field, model.env))
        if field.comodel_name:
            model = model.env[field.comodel_name]
    return " › ".join(labels)


# Where a template names the people it goes to
RECIPIENT_FIELDS = ("partner_to", "email_to", "email_cc")


def addressed(values):
    """:return: the values of a template, made to go to the people it names.

    Odoo 19 makes a template with Default Recipients on, and its own sending
    (the email of an automation rule, the composer of a record) then writes
    to the contact of the record and leaves out To and Copy to as written:
    "to the salesperson" went to the customer."""
    if any(values.get(name) for name in RECIPIENT_FIELDS):
        return dict(values, use_default_to=False)
    return values


def read_recipients(env, model, to, cc=None):
    """:param to: fields of the record that hold a contact or an address, or addresses
    :return: ``(partner_to, email_to, email_cc, lines of the card)``, as the
        placeholders of a template"""
    partner_to, email_to, email_cc, shown_to, shown_cc = [], [], [], [], []

    def recipient(text, into_partners, into_emails, shown):
        # Written as the placeholder itself: "{{ object.partner_id.email }}"
        text = PLACEHOLDER.sub(r"\1", text.strip())
        text = text[len("object."):] if text.startswith("object.") else text
        if EMAIL.match(text):
            into_emails.append(text)
            shown.append(text)
            return
        field = target_field(model, text)
        if field.type == "many2one" and field.comodel_name == "res.partner":
            into_partners.append("{{ object.%s.id }}" % text)
        elif field.type == "many2one" and field.comodel_name == "res.users":
            into_partners.append("{{ object.%s.partner_id.id }}" % text)
        elif field.type == "char":
            into_emails.append("{{ object.%s }}" % text)
        else:
            raise Refused(
                f"{text!r} is neither an address nor a field that holds a contact or an address.")
        shown.append(f"{path_label(model, text)} ({text})")

    for text in to:
        recipient(text, partner_to, email_to, shown_to)
    # A template has no contacts in copy: their addresses go in
    cc_partners = []
    for text in cc or []:
        recipient(text, cc_partners, email_cc, shown_cc)
    email_cc += [placeholder.replace(".id }}", ".email_formatted }}") for placeholder in cc_partners]
    lines = [
        {"label": _("To"), "value": ", ".join(shown_to)},
        {"label": _("Copy to"), "value": ", ".join(shown_cc) or None},
    ]
    return ",".join(partner_to), ",".join(email_to), ",".join(email_cc), lines


def read_email(env, model, email):
    """:return: ``(values of the template, lines of the card)``"""
    partner_to, email_to, email_cc, lines = read_recipients(env, model, email["to"], email.get("cc"))
    for name in ("subject", "body"):
        for placeholder in PLACEHOLDER.findall(email[name]):
            if not FIELD_PLACEHOLDER.match(placeholder):
                raise Refused(
                    f"The placeholder {{{{ {placeholder} }}}} is not a field of the record: only "
                    "{{ object.field }} goes between braces.")
            target_field(model, placeholder[len("object."):])
    body = email["body"].strip()
    body_html = body if "<" in body and ">" in body else str(plaintext2html(body))
    values = {
        "model_id": ir_model(env, model).id,
        "subject": email["subject"].strip(),
        "body_html": body_html,
        "partner_to": partner_to,
        "email_to": email_to,
        "email_cc": email_cc,
    }
    lines += [
        {"label": _("Subject"), "value": values["subject"]},
        {"label": _("Body"), "value": body, "code": True},
    ]
    if email.get("send_as", "email") != "email":
        lines.append({"label": _("Sent as"), "value": email["send_as"]})
    return values, lines


def read_action(env, model, action, position=1):
    """:param action: one action as the model wrote it
    :return: ``(values of the server action, template values or None,
        lines of the card)``"""
    kind = action["kind"]
    name = (action.get("name") or "").strip()
    values = {"model_id": ir_model(env, model).id}
    lines = []
    if kind == "code":
        error = compile_code(action.get("code"), env, model._name)
        if error:
            raise Refused(f"Action {position}: the code does not compile. {error}")
        values.update(state="code", code=action["code"].strip())
        lines.append({"label": _("Code"), "value": values["code"], "code": True})
        name = name or _("Run code")
    elif kind == "update":
        path = action.get("field")
        if not path:
            raise Refused(
                f"Action {position}: an update names the field, such as "
                '{"kind": "update", "field": "active", "value": "false"}; code goes in '
                '{"kind": "code", "code": "..."}.')
        field = target_field(model, path)
        if field.readonly and not field.inverse:
            raise Refused(f"Action {position}: {path} is read only.")
        label = field_label(field, env)
        if (action.get("expression") or "").strip().lower() in ("true", "false"):
            # "false" as an expression failed with NameError when run: it is a value
            action = dict(action, value=action["expression"].strip().lower(), expression="")
        if action.get("expression") and (action.get("value") or "").strip():
            raise Refused(
                f"Action {position}: an update gives a value or an expression, not both. A fixed "
                "value, such as false to archive, is a value.")
        if action.get("expression"):
            error = test_python_expr(action["expression"].strip(), mode="eval")
            if error:
                raise Refused(f"Action {position}: the expression does not compute. {error}")
            unknown = unknown_names(ast.parse(action["expression"].strip(), mode="eval"))
            if unknown:
                raise Refused(
                    f"Action {position}: {', '.join(unknown)} is not known when it runs. It knows: "
                    f"{', '.join(sorted(CODE_NAMES))}; True and False are written with capitals.")
            values.update(state="object_write", update_path=path, evaluation_type="equation",
                          value=action["expression"].strip())
            lines.append({"label": label, "value": _("computed: %s", action["expression"].strip())})
        else:
            if "value" not in action:
                raise Refused(f"Action {position}: an update gives a value, or an expression.")
            if field.type not in UPDATABLE_TYPES:
                raise Refused(
                    f"Action {position}: {path} is a {field.type}; an update writes "
                    f"{', '.join(UPDATABLE_TYPES)} fields. Write the others with code.")
            parsed, shown = read_value(env, field, action["value"])
            values.update(state="object_write", update_path=path, evaluation_type="value")
            if field.type == "boolean":
                values["update_boolean_value"] = "true" if parsed else "false"
            else:
                values["value"] = "" if parsed is False else str(parsed)
            lines.append({"label": label, "value": shown})
        name = name or _("Update %s", label)
    elif kind == "email":
        if "mail.template" not in env:
            raise Refused("Emails need the app Discuss (mail), which is not installed.")
        template = None
        if action.get("template_id"):
            record = get_records(get_model(env, "mail.template"), [action["template_id"]])
            if record.model != model._name:
                raise Refused(f"Action {position}: template #{record.id} is for {record.model}, not {model._name}.")
            values["template_id"] = record.id
            lines.append({"label": _("Template"), "value": record.name})
        elif action.get("email"):
            if designed_emails_here(env):
                raise Refused(f"Action {position}: {DESIGN_FIRST}")
            template, email_lines = read_email(env, model, action["email"])
            template["name"] = name or action["email"]["subject"].strip()[:120]
            lines += email_lines
        else:
            raise Refused(f"Action {position}: an email gives a template_id, or the email to make.")
        send_as = (action.get("email") or {}).get("send_as") or "email"
        values.update(state="mail_post", mail_post_method=send_as, mail_post_autofollow=False)
        name = name or _("Send an email")
        return dict(values, name=name), template, lines
    elif kind == "activity":
        Action = env["ir.actions.server"]
        if "activity_type_id" not in Action._fields:
            raise Refused("Activities need the app Discuss (mail), which is not installed.")
        summary = (action.get("summary") or "").strip()
        if not summary:
            raise Refused(f"Action {position}: an activity has a summary.")
        activity_type = env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        if not activity_type:
            activity_type = env["mail.activity.type"].search([], limit=1)
        values.update(
            state="next_activity",
            activity_type_id=activity_type.id,
            activity_summary=summary,
            activity_note=str(plaintext2html(action["note"].strip())) if action.get("note") else False,
            activity_date_deadline_range=action.get("days") or 0,
            activity_date_deadline_range_type="days",
        )
        lines.append({"label": _("Activity"), "value": summary})
        if action.get("note"):
            lines.append({"label": _("Note"), "value": action["note"].strip()})
        if action.get("days"):
            lines.append({"label": _("Due in"), "value": _("%s day(s)", action["days"])})
        if action.get("user_field"):
            field = target_field(model, action["user_field"])
            if field.type != "many2one" or field.comodel_name != "res.users":
                raise Refused(f"Action {position}: {action['user_field']} does not hold a user.")
            values.update(activity_user_type="generic", activity_user_field_name=action["user_field"])
            lines.append({"label": _("For"), "value": field_label(field, env)})
        else:
            values.update(activity_user_type="specific", activity_user_id=env.uid)
            lines.append({"label": _("For"), "value": env.user.name})
        name = name or _("Schedule: %s", summary)
    return dict(values, name=name), None, lines


def make_action(env, values, template):
    """Create the server action and, when it comes with one, its template."""
    values = dict(values)
    if template:
        values["template_id"] = env["mail.template"].create(addressed(template)).id
    return env["ir.actions.server"].create(values)


class Building(base.Action):
    """A tool of Developer mode that makes something, once the person said yes."""
    group = "base.group_system"
    modes = (base.MODE_DEV,)

    def is_available(self, env, mode=base.MODE_TALK):
        return super().is_available(env, mode) and env["nudge.conversation"]._can_develop()


def read_rule(env, arguments):
    """:param arguments: what the model wrote for prepare_automation
    :return: ``(values of the rule, values of its actions, templates of
        those, lines of the card)``"""
    model = get_model(env, arguments["model"])
    trigger = arguments["trigger"]
    values = {
        "name": arguments["name"].strip(),
        "model_id": ir_model(env, model).id,
        "trigger": trigger,
    }
    lines = [
        {"label": _("Model"), "value": model_label(model)},
        {"label": _("When"), "value": TRIGGERS[trigger]},
    ]
    if arguments.get("fields"):
        if trigger != "on_create_or_write":
            raise Refused("fields go with on_create_or_write only.")
        found = env["ir.model.fields"].sudo().search([
            ("model", "=", model._name), ("name", "in", arguments["fields"])])
        missing = set(arguments["fields"]) - set(found.mapped("name"))
        if missing:
            raise Refused(f"{model._name} has no field {', '.join(sorted(missing))}.")
        values["trigger_field_ids"] = found.ids
        lines.append({"label": _("Only when these change"), "value": ", ".join(found.mapped("field_description"))})
    if arguments.get("conditions"):
        domain = build_domain(model, arguments["conditions"], arguments.get("match", "all"))
        values["filter_domain"] = str(list(domain))
        lines.append({"label": _("Only for"), "value": in_words(model, arguments["conditions"], arguments.get("match", "all"))})
    if trigger in TIME_TRIGGERS:
        delay = arguments.get("delay")
        if not delay:
            raise Refused("A time trigger needs delay: number, unit, and date_field for on_time.")
        values.update(trg_date_range=delay["number"], trg_date_range_type=delay["unit"])
        if trigger == "on_time":
            if not delay.get("date_field"):
                raise Refused("on_time counts from a date field: give date_field.")
            field = env["ir.model.fields"].sudo().search([
                ("model", "=", model._name), ("name", "=", delay["date_field"]),
                ("ttype", "in", ("date", "datetime"))], limit=1)
            if not field:
                raise Refused(f"{model._name} has no date field {delay['date_field']!r}.")
            values["trg_date_id"] = field.id
            lines.append({"label": _("Counted from"), "value": field.field_description})
        when = _("%(number)s %(unit)s before", number=-delay["number"], unit=delay["unit"]) if delay["number"] < 0 \
            else _("%(number)s %(unit)s after", number=delay["number"], unit=delay["unit"])
        lines.append({"label": _("Delay"), "value": when})
    elif arguments.get("delay"):
        raise Refused("delay goes with the time triggers only.")
    actions, templates = [], []
    for position, action in enumerate(arguments["actions"], start=1):
        action_values, template, action_lines = read_action(env, model, action, position)
        action_values["usage"] = "base_automation"
        actions.append(action_values)
        templates.append(template)
        lines.append({"label": _("Action %s", position) + f": {action_values['name']}", "lines": action_lines})
    return values, actions, templates, lines


def make_rule(env, values):
    """:param values: what ``read_rule`` gave, as the step keeps it
    :return: the rule, with its actions"""
    rule_values = dict(values.get("rule") or {})
    if rule_values.get("trigger_field_ids"):
        rule_values["trigger_field_ids"] = [Command.set(rule_values["trigger_field_ids"])]
    rule = env["base.automation"].create(rule_values)
    for action_values, template in zip(values.get("actions") or [], values.get("templates") or []):
        make_action(env, dict(action_values, base_automation_id=rule.id), template)
    return rule


@base.register
class PrepareAutomation(Building):
    name = "prepare_automation"
    kind = "create"
    description = (
        "Make an automation rule: when something happens to a record of a model, one or more "
        "actions run (code, an update of a field, an email, an activity). Nothing is made by "
        "this call: the person is shown the rule in full, the code and the email included, "
        "and says yes or no; you read their answer as the result. Say in a sentence what you "
        "are about to make before calling. For an email, ask first who receives it and who is "
        "in copy, and propose the subject and the body: the template is made with the rule. "
        "Triggers: " + "; ".join(f"{key}: {text}" for key, text in TRIGGERS.items()) + ". "
        "For a change of state or stage, use on_create_or_write with the field in fields and "
        "the value in conditions."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "model", "trigger", "actions"],
        "properties": {
            "name": {"type": "string", "minLength": 2, "maxLength": 120},
            "model": {"type": "string", "pattern": MODEL_PATTERN},
            "trigger": {"type": "string", "enum": list(TRIGGERS)},
            "fields": {
                "type": "array",
                "maxItems": 8,
                "items": {"type": "string", "pattern": r"[a-z_][a-z0-9_]*"},
                "description": "For on_create_or_write: the rule runs only when one of these fields changes.",
            },
            "conditions": dict(CONDITION_SCHEMA, description="The rule runs only for records that match."),
            "match": {"type": "string", "enum": ["all", "any"]},
            "delay": {
                "type": "object",
                "additionalProperties": False,
                "required": ["number", "unit"],
                "properties": {
                    "number": {"type": "integer", "minimum": -3650, "maximum": 3650,
                               "description": "Negative: before the date."},
                    "unit": {"type": "string", "enum": list(DELAY_UNITS)},
                    "date_field": {"type": "string", "pattern": r"[a-z_][a-z0-9_]*",
                                   "description": "For on_time: the date or datetime field counted from."},
                },
                "description": "For the time triggers.",
            },
            "actions": {"type": "array", "minItems": 1, "maxItems": MAX_ACTIONS, "items": ACTION_SCHEMA},
        },
    }

    @refusing
    def run(self, env, arguments, screen):
        check_developing(env)
        automations_installed(env)
        values, actions, templates, lines = read_rule(env, arguments)
        return base.ToolResult(
            "The person is being shown the automation rule, in full.",
            pending={
                "summary": _("Create: automation rule “%s”", values["name"]),
                "res_model": "base.automation",
                "res_ids": [],
                "record_names": values["name"],
                "values": {"rule": values, "actions": actions, "templates": templates},
                "is_sensitive": any(action.get("state") == "code" for action in actions),
                "preview": {"records": [], "lines": lines},
            },
        )

    def execute(self, env, step):
        check_developing(env)
        automations_installed(env)
        values = step.values or {}
        if not (values.get("rule") or {}).get("name"):
            return base.ToolResult("This rule cannot be made.", is_error=True)
        rule = make_rule(env, values)
        return base.ToolResult(
            f"Made: automation rule #{rule.id} {rule.name!r} with {len(rule.action_server_ids)} action(s). "
            "It is in Settings ‣ Technical ‣ Automation Rules.",
            event={"type": "changed", "model": "base.automation", "ids": rule.ids},
            records=("base.automation", rule.ids, [rule.name]),
        )


@base.register
class PrepareServerAction(Building):
    name = "prepare_server_action"
    kind = "create"
    description = (
        "Make a server action on a model: code, an update of a field, an email, or an "
        "activity, run by a button, from the Actions menu of the model's screens, or by an "
        "automation rule later. Nothing is made by this call: the person is shown the action "
        "in full, the code included, and says yes or no; you read their answer as the result. "
        "in_menu puts it in the Actions menu of the lists and forms of the model."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "model", "action"],
        "properties": {
            "name": {"type": "string", "minLength": 2, "maxLength": 120},
            "model": {"type": "string", "pattern": MODEL_PATTERN},
            "action": ACTION_SCHEMA,
            "in_menu": {"type": "boolean"},
        },
    }

    @refusing
    def run(self, env, arguments, screen):
        check_developing(env)
        model = get_model(env, arguments["model"])
        values, template, lines = read_action(env, model, arguments["action"])
        values["name"] = arguments["name"].strip()
        values["usage"] = "ir_actions_server"
        if arguments.get("in_menu"):
            values.update(binding_model_id=ir_model(env, model).id, binding_type="action")
        lines = [{"label": _("Model"), "value": model_label(model)}] + lines + [
            {"label": _("Shown in"), "value": _("the Actions menu of %s", model_label(model))
             if arguments.get("in_menu") else _("nowhere yet: a button or a rule will run it")},
        ]
        return base.ToolResult(
            # A button was asked for with action_id 1 before the action was there
            "The person is being shown the server action, in full. It has no id before their yes: "
            "a button for it is made after, with the id their answer gives.",
            pending={
                "summary": _("Create: server action “%s”", values["name"]),
                "res_model": "ir.actions.server",
                "res_ids": [],
                "record_names": values["name"],
                "values": {"action": values, "template": template},
                "is_sensitive": values.get("state") == "code",
                "preview": {"records": [], "lines": lines},
            },
        )

    def execute(self, env, step):
        check_developing(env)
        values = step.values or {}
        if not (values.get("action") or {}).get("name"):
            return base.ToolResult("This action cannot be made.", is_error=True)
        action = make_action(env, values["action"], values.get("template"))
        where = " It is in the Actions menu." if action.binding_model_id else ""
        told = (f"Made: server action #{action.id} {action.name!r} ({action.state}).{where} "
                "It is in Settings ‣ Technical ‣ Server Actions.")
        button = asks_for_button(env)
        if button:
            # Told it could say what was done, a small model said the button was on the form
            told += (f" Not finished: the button is not on the form yet. Call prepare_button now: "
                     f"action_id {action.id}, model {action.model_name}.")
        else:
            told += (f" Its id is {action.id}: a button that runs it is made with prepare_button, "
                     f"action_id {action.id}.")
        return base.ToolResult(
            told,
            event={"type": "changed", "model": "ir.actions.server", "ids": action.ids},
            records=("ir.actions.server", action.ids, [action.name]),
            unfinished=button,
        )


# A button asked for, in English or in Arabic: "a button", "زر", "بالزر", "زرًا", "أزرار";
# never "زرع" or "وزارة"
BUTTON_WORDS = re.compile(
    r"\bbuttons?\b|(?:^|[\s«\"'(])(?:و|ف)?(?:بال|لل|ال|ب|ل)?(?:زرار|زر|أزرار|ازرار)"
    r"(?=$|[\s.,،؛:؟?!»\"')\u064b\u0651]|ا(?:$|[\s.,،؟?!]))",
    re.IGNORECASE)


def asks_for_button(env):
    """:return: whether the person asked, in their last words of this
    conversation, for a button"""
    conversation_id = env.context.get("nudge_conversation_id")
    if not conversation_id:
        return False
    asked = env["nudge.message"].sudo().search(
        [("conversation_id", "=", conversation_id), ("role", "=", "user")], order="id desc", limit=3)
    return any(BUTTON_WORDS.search(message.content or "") for message in asked)


@base.register
class PrepareEmailTemplate(Building):
    name = "prepare_email_template"
    kind = "create"
    description = (
        "Make an email template on a model, to be sent by hand from its records or by a rule "
        "later. Ask first who receives it and who is in copy; propose the subject and the "
        "body. Nothing is made by this call: the person is shown the template in full and says "
        "yes or no; you read their answer as the result. For an email sent on an event, "
        "prepare_automation with an action of kind email makes the template and the rule in "
        "one card."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "model", "email"],
        "properties": {
            "name": {"type": "string", "minLength": 2, "maxLength": 120},
            "model": {"type": "string", "pattern": MODEL_PATTERN},
            "email": EMAIL_SCHEMA,
        },
    }

    @refusing
    def run(self, env, arguments, screen):
        check_developing(env)
        if "mail.template" not in env:
            raise Refused("Emails need the app Discuss (mail), which is not installed.")
        if designed_emails_here(env):
            raise Refused(DESIGN_FIRST.replace("then give its template_id here", "which makes the template"))
        model = get_model(env, arguments["model"])
        values, lines = read_email(env, model, arguments["email"])
        values["name"] = arguments["name"].strip()
        lines = [{"label": _("Model"), "value": model_label(model)}] + lines
        return base.ToolResult(
            "The person is being shown the email template, in full.",
            pending={
                "summary": _("Create: email template “%s”", values["name"]),
                "res_model": "mail.template",
                "res_ids": [],
                "record_names": values["name"],
                "values": {"template": values},
                "is_sensitive": False,
                "preview": {"records": [], "lines": lines},
            },
        )

    def execute(self, env, step):
        check_developing(env)
        values = (step.values or {}).get("template") or {}
        if not values.get("name"):
            return base.ToolResult("This template cannot be made.", is_error=True)
        template = env["mail.template"].create(addressed(values))
        return base.ToolResult(
            f"Made: email template #{template.id} {template.name!r}. "
            "It is in Settings ‣ Technical ‣ Email Templates.",
            event={"type": "changed", "model": "mail.template", "ids": template.ids},
            records=("mail.template", template.ids, [template.name]),
        )


@base.register
class PrepareDelete(Building):
    name = "prepare_delete"
    kind = "delete"
    description = (
        "Delete automation rules, server actions, email templates, scheduled actions, "
        "extension views made here or fields made here, for good, when the person asked to "
        "delete them by name; to stop one, switch it off with prepare_write on its field "
        "active instead. A view or a field of a module is never deleted; a field goes with "
        "its column and every value in it. Nothing is deleted by this call: the person is "
        "shown what is about to go and says yes or no; you read their answer as the result."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["model", "ids"],
        "properties": {
            "model": {"type": "string", "enum": DELETABLE},
            "ids": {"type": "array", "minItems": 1, "maxItems": 10, "items": {"type": "integer", "minimum": 1}},
        },
    }

    @refusing
    def run(self, env, arguments, screen):
        check_developing(env)
        model = get_model(env, arguments["model"], "unlink")
        records = get_records(model.with_context(active_test=False), arguments["ids"], "unlink")
        self._check_deletable(records)
        names = records.mapped("display_name")
        lines = [{"label": _("Kind"), "value": model_label(model)},
                 {"label": _("Undo"), "value": _("Not possible: deleted for good")}]
        showing = []
        if model._name == "ir.model.fields":
            lines.append({"label": _("Data"), "value": _("The column and every value in it are deleted too")})
            showing = [view.name for field in records for view in self._views_showing(field)]
        elif model._name == "ir.actions.server":
            showing = [view.name for action in records for view in self._views_running(action)]
        if showing:
            lines.append({"label": _("Views"), "value": _("Taken off the screens: %s", ", ".join(showing))})
        return base.ToolResult(
            "The person is being shown what is about to be deleted.",
            pending={
                "summary": _("Delete: %s", ", ".join(names)[:200]),
                "res_model": model._name,
                "res_ids": records.ids,
                "record_names": ", ".join(names)[:500],
                "values": {"ids": records.ids},
                "is_sensitive": True,
                "preview": {
                    "records": [{"id": record.id, "name": record.display_name} for record in records],
                    "lines": lines,
                },
            },
        )

    @staticmethod
    def _check_deletable(records):
        """:raise Refused: for what a module owns, which an update would bring back or break"""
        if records._name in ("base.automation", "ir.actions.server", "mail.template", "ir.cron"):
            xml_ids = records.get_external_id()
            for record in records:
                module = (xml_ids.get(record.id) or "").split(".")[0]
                if module and module not in CUSTOM_MODULES:
                    raise Refused(
                        f"{record.display_name!r} belongs to the module {module}: it is never deleted, "
                        "which would break the module and come back with its next update. Switch it "
                        "off with prepare_write on active instead.")
            if records._name == "ir.actions.server":
                for action in records:
                    PrepareDelete._views_running(action)
        if records._name == "ir.ui.view":
            xml_ids = records.get_external_id()
            for view in records:
                module = (xml_ids.get(view.id) or "").split(".")[0]
                if module in CUSTOM_MODULES:
                    continue
                if not view.inherit_id or module:
                    raise Refused(
                        f"View #{view.id} {view.name!r} belongs to a module: it is never deleted. "
                        "Switch it off with prepare_write on active, or change it by extension.")
        elif records._name == "ir.actions.report":
            xml_ids = records.get_external_id()
            for report in records:
                module = (xml_ids.get(report.id) or "").split(".")[0]
                if module and module not in CUSTOM_MODULES:
                    raise Refused(f"Report #{report.id} {report.name!r} belongs to a module: it is never deleted.")
        elif records._name == "ir.model.fields":
            for field in records:
                if field.state != "manual":
                    raise Refused(
                        f"Field {field.name!r} of {field.model} belongs to a module: it is never deleted.")
                PrepareDelete._views_showing(field)

    @staticmethod
    def _views_showing(field):
        """:return: the extension views made here that show ``field``, which
        go with it
        :raise Refused: when a view of a module shows it"""
        Views = field.env["ir.ui.view"].with_context(active_test=False)
        views = Views.search([("model", "=", field.model), ("arch_db", "like", f'name="{field.name}"')])
        xml_ids = views.get_external_id()
        for view in views:
            module = (xml_ids.get(view.id) or "").split(".")[0]
            if not view.inherit_id or (module and module not in CUSTOM_MODULES):
                raise Refused(
                    f"Field {field.name!r} is shown by view #{view.id} {view.name!r} of a module: "
                    "take it out of the view first, by extension, or keep the field.")
        return views

    @staticmethod
    def _views_running(action):
        """:return: the extension views made here that hold a button running
        ``action``, which go with it
        :raise Refused: when a view of a module holds one"""
        Views = action.env["ir.ui.view"].with_context(active_test=False)
        views = Views.search([("type", "!=", "qweb"), ("arch_db", "like", f'name="{action.id}"')])
        views = views.filtered(lambda view: _buttons_running(view, action)[1])
        xml_ids = views.get_external_id()
        for view in views:
            module = (xml_ids.get(view.id) or "").split(".")[0]
            if not view.inherit_id or (module and module not in CUSTOM_MODULES):
                raise Refused(
                    f"Server action {action.name!r} is run by a button of view #{view.id} {view.name!r} "
                    "of a module: take the button out of the view first, by extension, or keep the action.")
        return views

    def execute(self, env, step):
        check_developing(env)
        if step.res_model not in DELETABLE:
            return base.ToolResult("This cannot be deleted.", is_error=True)
        model = get_model(env, step.res_model, "unlink")
        records = get_records(model.with_context(active_test=False), step.res_ids or [], "unlink")
        names = delete_built(records)
        return base.ToolResult(
            f"Deleted: {model._description} {', '.join(repr(cut(name, 60)) for name in names)}.",
            event={"type": "changed", "model": model._name, "ids": []},
            records=(model._name, [], names),
        )


def delete_built(records):
    """Delete what Nudge or Studio made, with what goes with it: the views that
    show a field, the template of a report, the email templates made for a
    rule and used by nothing else.

    :return: the names of what was deleted
    :raise Refused: for what a module owns"""
    env = records.env
    PrepareDelete._check_deletable(records)
    names = records.mapped("display_name")
    extra = []
    if records._name == "ir.model.fields":
        for field in records:
            extra.append(PrepareDelete._views_showing(field))
    if records._name == "ir.actions.server":
        # The button stayed on the form and ran an action that was gone
        for action in records:
            extra.append(_take_buttons_off(action, PrepareDelete._views_running(action)))
    if records._name == "ir.actions.report":
        Views = env["ir.ui.view"].with_context(active_test=False)
        templates = Views.search([("type", "=", "qweb"), ("key", "in", records.mapped("report_name"))])
        xml_ids = templates.get_external_id()
        extra.append(templates.filtered(lambda view: (xml_ids.get(view.id) or "").split(".")[0] in CUSTOM_MODULES))
    mail_templates = env["mail.template"] if "mail.template" in env else None
    if records._name == "base.automation" and mail_templates is not None:
        actions = records.action_server_ids.filtered(lambda action: action.state == "mail_post")
        used = actions.mapped("template_id")
        others = env["ir.actions.server"].with_context(active_test=False).search(
            [("template_id", "in", used.ids), ("id", "not in", actions.ids)])
        mail_templates = (used - others.mapped("template_id")).filtered(lambda template: not template.get_external_id().get(template.id))
    else:
        mail_templates = None
    # A scheduled action holds its code in a server action, which Odoo keeps when
    # the scheduled action goes: the trial database filled with them
    behind = records.ir_actions_server_id if records._name == "ir.cron" else None
    for views in extra:
        if records._name in ("ir.model.fields", "ir.actions.server"):
            views.unlink()
    records.unlink()
    for views in extra:
        if views.exists():
            views.unlink()
    if behind:
        behind.exists().unlink()
    if mail_templates:
        mail_templates.exists().unlink()
    env.registry.clear_cache("templates")
    return names


def _buttons_running(view, action):
    """:return: ``(the arch of view, its buttons that run action)``"""
    arch = etree.fromstring((view.arch_db or "").encode("utf-8"))
    return arch, arch.xpath(f"//button[@type='action'][@name='{action.id}']")


def _take_buttons_off(action, views):
    """Take the buttons that run ``action`` out of ``views``.
    :return: the views that held nothing else, which go whole"""
    whole = views.browse()
    for view in views:
        # Read and written in the language its terms are written in: the
        # translations of what stays are kept
        arch, buttons = _buttons_running(view.with_context(lang="en_US"), action)
        rest = [node for node in arch.iter(etree.Element)
                if node.tag not in ("data", "xpath", "header") and node not in buttons]
        if not rest:
            # The view prepare_button made: the button, and where it goes
            whole |= view
            continue
        # A view of Studio holds the other changes of the form too: those stay
        for button in buttons:
            button.getparent().remove(button)
        view.with_context(lang="en_US").arch = etree.tostring(arch, encoding="unicode")
    return whole


def built_by(env, runs):
    """:return: ``[(step, records)]`` that the runs made and that still exist,
    the last made first"""
    steps = env["nudge.step"].sudo().search([
        ("run_id", "in", runs.ids), ("state", "=", "done"), ("kind", "=", "create"),
        ("res_model", "in", list(DELETABLE))], order="id desc")
    found = []
    for step in steps:
        if step.res_model not in env or not step.res_ids:
            continue
        records = env[step.res_model].with_context(active_test=False).browse(step.res_ids).exists()
        if not records:
            continue
        try:
            PrepareDelete._check_deletable(records)
        except Refused:
            continue
        found.append((step, records))
    return found


def undo_built(env, runs):
    """Delete what the runs made, the last made first.
    :return: ``(names deleted, [(name kept, why)])``"""
    deleted, kept = [], []
    for step, records in built_by(env, runs):
        records = records.with_env(env).exists()
        if not records:
            continue
        try:
            with env.cr.savepoint():
                deleted += [f"{records._description}: {name}" for name in delete_built(records)]
        except Refused as refusal:
            kept.append((f"{records._description}: {', '.join(records.mapped('display_name'))}", str(refusal)))
    return deleted, kept


def left_as_it_is(env, runs):
    """:return: what the runs did that undoing them leaves as it is, as their
    steps say it: what was changed rather than made (a value written, a button
    pressed, something deleted), and what was made that is not Nudge's to
    delete"""
    undone = {step.id for step, _records in built_by(env, runs)}
    left = []
    for step in env["nudge.step"].sudo().search([
            ("run_id", "in", runs.ids), ("state", "=", "done"),
            ("kind", "in", ["create", "write", "method", "delete"]), ("tool", "!=", "prepare_undo")],
            order="id"):
        if step.id in undone:
            continue
        if step.kind == "create" and not (
                step.res_model in env and step.res_ids
                and env[step.res_model].sudo().with_context(active_test=False).browse(step.res_ids).exists()):
            # Made, and gone since
            continue
        left.append(step.summary or step.tool)
    return left


@base.register
class PrepareUndo(Building):
    name = "prepare_undo"
    kind = "delete"
    description = (
        "Undo what you built in Developer mode: everything made in this conversation (rules, "
        "server actions, scheduled actions, email templates, views, buttons, fields, reports, "
        "approval rules), or in one request of What Nudge Did with run_id, is deleted, the last "
        "made first. What was changed rather than made (a rule switched off, a value written) "
        "is not undone: say so. Nothing is deleted by this call: the person is shown the list "
        "and says yes or no; you read their answer as the result."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "run_id": {"type": "integer", "minimum": 1, "description": "One request of What Nudge Did."},
        },
    }

    def _runs(self, env, run_id):
        Run = env["nudge.run"].sudo()
        if run_id:
            runs = Run.search([("id", "=", run_id), ("user_id", "=", env.uid), ("is_developer", "=", True)])
            if not runs:
                raise Refused("No request of yours in Developer mode has this number.")
            return runs
        conversation_id = env.context.get("nudge_conversation_id")
        if not conversation_id:
            raise Refused("Give the run_id of the request to undo.")
        return Run.search([("conversation_id", "=", conversation_id), ("user_id", "=", env.uid), ("is_developer", "=", True)])

    @refusing
    def run(self, env, arguments, screen):
        check_developing(env)
        runs = self._runs(env, arguments.get("run_id"))
        found = built_by(env, runs)
        if not found:
            raise Refused("Nothing made here still exists: there is nothing to undo.")
        shown = [{"id": index, "name": f"{records._description}: {', '.join(records.mapped('display_name'))}"}
                 for index, (_step, records) in enumerate(found, start=1)]
        lines = [{"label": _("Deleted"), "value": _("For good: it cannot be brought back")}]
        lines += self._left_lines(env, left_as_it_is(env, runs))
        return base.ToolResult(
            "The person is being shown what is about to be deleted.",
            pending={
                "summary": _("Undo what was built: %s item(s)", len(found)),
                "res_model": "nudge.run",
                "res_ids": runs.ids,
                "record_names": ", ".join(item["name"] for item in shown)[:500],
                "values": {"run_ids": runs.ids},
                "is_sensitive": True,
                "preview": {"records": shown, "lines": lines},
            },
        )

    @staticmethod
    def _left_lines(env, left):
        """:return: the line of the card for what undoing leaves as it is; none
        when it leaves nothing"""
        if not left:
            return []
        shown = "; ".join(left[:MAX_LEFT_SHOWN])
        if len(left) > MAX_LEFT_SHOWN:
            shown += " " + _("and %s more", len(left) - MAX_LEFT_SHOWN)
        return [{"label": _("Stays as it is"), "value": shown}]

    def execute(self, env, step):
        check_developing(env)
        runs = env["nudge.run"].sudo().browse((step.values or {}).get("run_ids") or []).exists()
        runs = runs.filtered(lambda run: run.user_id == env.user and run.is_developer)
        left = left_as_it_is(env, runs)
        deleted, kept = undo_built(env, runs)
        told = f"Deleted {len(deleted)} item(s): " + "; ".join(deleted) if deleted else "Nothing was deleted."
        if kept:
            told += " Kept: " + "; ".join(f"{name} ({why})" for name, why in kept)
        if left:
            told += " Left as it is (changed rather than made, or not Nudge's to delete): " + "; ".join(left)
        # The card says what was done: all of it, or all but what it names
        if not deleted:
            done = _("Nothing")
        elif kept:
            done = _("All but what is kept")
        else:
            done = _("All of it")
        lines = [{"label": _("Deleted"), "value": done}]
        if kept:
            lines.append({"label": _("Kept"), "value": "; ".join(name for name, _why in kept)[:500]})
        lines += self._left_lines(env, left)
        return base.ToolResult(
            told,
            event={"type": "changed", "model": "ir.ui.view", "ids": [], "action": {"type": "ir.actions.client", "tag": "reload"}},
            records=("nudge.run", runs.ids, deleted[:10]),
            card_lines=lines,
        )


# ------------------------------------------------------------------ trying

class DryRun(Exception):
    """Raised to leave the savepoint: nothing the trial did is kept."""


def _mail_watch(env):
    Mail = env["mail.mail"].sudo() if "mail.mail" in env else None
    return (Mail.search([], order="id desc", limit=1).id or 0) if Mail is not None else None


def _mails_since(env, last_id):
    if last_id is None:
        return []
    found = []
    for mail in env["mail.mail"].sudo().search([("id", ">", last_id)], order="id"):
        to = ", ".join(filter(None, [mail.email_to] + [
            partner.email or partner.display_name for partner in mail.recipient_ids]))
        found.append(f"{mail.subject!r} to {to or '?'}")
    return found


def _made_since(env):
    """:return: activities and messages in the whole database, counted: what a
    scheduled action made is their difference"""
    if "mail.activity" not in env:
        return (0, 0)
    return (env["mail.activity"].sudo().search_count([]), env["mail.message"].sudo().search_count([]))


def _on_record(env, record):
    """:return: activities and messages of a record, counted"""
    if not record or not record.exists() or "mail.activity" not in env:
        return (0, 0)
    domain = [("res_model", "=", record._name), ("res_id", "=", record.id)]
    activities = env["mail.activity"].sudo().search_count(domain)
    messages = env["mail.message"].sudo().search_count([
        ("model", "=", record._name), ("res_id", "=", record.id), ("message_type", "!=", "notification")])
    return (activities, messages)


# Fields that change on their own, or that cannot be compared
UNWATCHED = {"id", "create_date", "write_date", "create_uid", "write_uid", "display_name",
             "message_ids", "activity_ids", "message_follower_ids", "website_message_ids"}
UNWATCHED_TYPES = ("binary", "image", "one2many", "many2many", "json", "properties", "properties_definition")
MAX_WATCHED = 200


def _watched_fields(model):
    """:return: every plain field of the record, as the code of an action
    writes what it likes"""
    names = []
    for name, field in model._fields.items():
        if name in UNWATCHED or not field.store or field.type in UNWATCHED_TYPES:
            continue
        if not has_field_access(model, field, "read"):
            continue
        names.append(name)
    return names[:MAX_WATCHED]


def _changes(env, model, names, before, after):
    labels = model.fields_get(names, ["string"])
    changed = []
    for name in names:
        was, now = before.get(name), after.get(name)
        if was != now:
            shown = [format_value(env, model._fields[name], value) for value in (was, now)]
            shown = [value.get("name") if isinstance(value, dict) else value for value in shown]
            changed.append(f"{labels.get(name, {}).get('string') or name}: {shown[0]!r} → {shown[1]!r}")
    return changed


# What a trial takes when it is given nothing: what was made last
TRIED_BUILT = {
    "base.automation": ("rule_id", "rule"),
    "ir.actions.server": ("action_id", "server action"),
    "ir.cron": ("cron_id", "scheduled action"),
}


@base.register
class TryAutomation(Building):
    name = "try_automation"
    kind = "code"
    description = (
        "Try before making, or try what exists: a dummy record is created with the values "
        "given, the rule fires on it (or the server action runs on it, or the scheduled action "
        "runs once), and what happened is told: the fields that changed, the emails that would "
        "have gone out, the activities and messages added, the error if it failed. Then "
        "everything is discarded: the dummy record, the rule made for the trial, the emails. "
        "Give one of: rule (what prepare_automation takes), rule_id, action_id, cron_id; given "
        "none, what was made last in this conversation is tried. For a "
        "rule on records, give record: the fields a person would fill in; for "
        "on_create_or_write, on_archive and on_unarchive, give change: what is changed on it "
        "afterwards. Offer it in one sentence before the card of a rule; a rule that failed on "
        "the dummy record is fixed before the card. A trial that runs code is shown to the "
        "person first, the code in full, and runs after their yes: code runs for real inside "
        "the trial, and an email it sends by itself goes out; an email action only queues it."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "rule": {
                "type": "object",
                "description": "A rule not made yet: exactly what prepare_automation takes.",
            },
            "rule_id": {"type": "integer", "minimum": 1},
            "action_id": {"type": "integer", "minimum": 1},
            "cron_id": {"type": "integer", "minimum": 1},
            "record": dict(VALUES_SCHEMA, minItems=0, description="The dummy record: the fields a person would fill in."),
            "change": dict(VALUES_SCHEMA, minItems=0, description="What is changed on it afterwards, which fires the rule."),
        },
    }

    @staticmethod
    def _given(env, arguments):
        """:return: ``(what is tried, what was taken when nothing was named)``;
        what was taken is written in ``arguments``"""
        given = [key for key in ("rule", "rule_id", "action_id", "cron_id") if arguments.get(key)]
        taken = ""
        if not given:
            # "Try it" right after the rule was made: the model named nothing and was refused
            taken = TryAutomation._last_built(env, arguments)
            given = [key for key in ("rule_id", "action_id", "cron_id") if arguments.get(key)]
            if not given:
                raise Refused("Give one of rule, rule_id, action_id or cron_id: nothing made in this "
                              "conversation is there to try.")
        if len(given) != 1:
            raise Refused("Give one of rule, rule_id, action_id or cron_id.")
        if given[0] == "rule":
            # Checked here rather than by the schema of this tool, which
            # would carry the whole of prepare_automation's a second time
            try:
                # Forgiven as prepare_automation forgives it: the same rule passes both
                arguments["rule"] = schema.forgive(arguments["rule"], PrepareAutomation.input_schema)
                schema.validate(arguments["rule"], PrepareAutomation.input_schema, "rule")
            except schema.SchemaError as error:
                raise Refused(f"Invalid rule. {error}") from None
        return given[0], taken

    @staticmethod
    def _last_built(env, arguments):
        """Name in ``arguments`` the rule, server action or scheduled action
        made last in this conversation, when one still exists.
        :return: what was taken, said for the model, or "" """
        conversation_id = env.context.get("nudge_conversation_id")
        if not conversation_id:
            return ""
        steps = env["nudge.step"].sudo().search([
            ("conversation_id", "=", conversation_id), ("state", "=", "done"), ("kind", "=", "create"),
            ("res_model", "in", list(TRIED_BUILT))], order="id desc", limit=20)
        for step in steps:
            if step.res_model not in env or not step.res_ids:
                continue
            records = env[step.res_model].with_context(active_test=False).browse(step.res_ids).exists()
            if records:
                key, kind = TRIED_BUILT[step.res_model]
                arguments[key] = records[:1].id
                return (f"Nothing was named: the {kind} #{records[:1].id} {records[:1].display_name!r}, "
                        "made last in this conversation, is tried.")
        return ""

    @staticmethod
    def _code_of(env, given, arguments):
        """:return: ``(what is tried, the code it runs)``; no code, nothing to ask"""
        def codes(actions):
            found = []
            for action in actions:
                if action.state == "code" and (action.code or "").strip():
                    found.append(action.code.strip())
                if action.state == "multi":
                    found += codes(action.child_ids)
            return found

        if given == "rule":
            rule = arguments["rule"]
            found = [action.get("code", "").strip() for action in rule.get("actions") or []
                     if action.get("kind") == "code" and action.get("code", "").strip()]
            return f"the rule {rule.get('name', '')!r}", found
        if given == "rule_id":
            automations_installed(env)
            rule = get_records(get_model(env, "base.automation").with_context(active_test=False), [arguments["rule_id"]])
            return f"the rule {rule.name!r}", codes(rule.action_server_ids)
        if given == "action_id":
            action = get_records(get_model(env, "ir.actions.server"), [arguments["action_id"]])
            return f"the server action {action.name!r}", codes(action)
        cron = get_records(get_model(env, "ir.cron").with_context(active_test=False), [arguments["cron_id"]])
        return f"the scheduled action {cron.cron_name!r}", codes(cron.ir_actions_server_id)

    @refusing
    def run(self, env, arguments, screen):
        check_developing(env)
        given, taken = self._given(env, arguments)
        what, code = self._code_of(env, given, arguments)
        if not code:
            result = self._trial(env, given, arguments)
            if taken:
                result.content = f"{taken}\n{result.content}"
            return result
        lines = [{"label": _("Tried"), "value": what}]
        lines += [{"label": _("Code"), "value": text, "code": True} for text in code]
        lines.append({"label": _("Note"), "value": _(
            "The code runs for real inside the trial. What it writes is discarded; an email it "
            "sends by itself goes out.")})
        return base.ToolResult(
            " ".join(filter(None, (taken, "The person is being shown the code the trial runs."))),
            pending={
                "summary": _("Try on a dummy record: %s", what),
                "res_model": "base.automation" if given.startswith("rule") else
                ("ir.cron" if given == "cron_id" else "ir.actions.server"),
                "res_ids": [],
                "record_names": what,
                "values": {"arguments": arguments},
                "is_sensitive": True,
                "preview": {"records": [], "lines": lines},
            },
        )

    def execute(self, env, step):
        check_developing(env)
        arguments = (step.values or {}).get("arguments") or {}
        try:
            return self._trial(env, self._given(env, arguments)[0], arguments)
        except Refused as refusal:
            return base.ToolResult(str(refusal), is_error=True)

    def _trial(self, env, given, arguments):
        given = [given]
        report = []
        try:
            with env.cr.savepoint():
                env.registry.clear_cache()
                if given[0] == "cron_id":
                    self._try_cron(env, arguments["cron_id"], report)
                else:
                    self._try_on_record(env, given[0], arguments, report)
                raise DryRun()
        except DryRun:
            pass
        except Refused:
            raise
        except Exception as error:  # noqa: BLE001
            report.append(f"It failed: {type(error).__name__}: {cut(str(error), 500)}")
        finally:
            # What the trial made is gone from the database: gone from the
            # caches too, and the rules in force are read again
            env.invalidate_all()
            env.registry.clear_cache()
            if "base.automation" in env:
                env["base.automation"]._update_registry()
        report.append("Nothing was kept: the dummy record and whatever was made for the trial were discarded.")
        return base.ToolResult("\n".join(report))

    def _try_on_record(self, env, given, arguments, report):
        rule = action = None
        if given == "rule":
            automations_installed(env)
            values, actions, templates, _lines = read_rule(env, arguments["rule"])
            rule = make_rule(env, {"rule": values, "actions": actions, "templates": templates})
            report.append(f"The rule {rule.name!r} was made for the trial.")
        elif given == "rule_id":
            automations_installed(env)
            Rule = get_model(env, "base.automation").with_context(active_test=False)
            rule = get_records(Rule, [arguments["rule_id"]])
            if not rule.active:
                rule.active = True
                report.append(f"The rule {rule.name!r} is off: it was switched on for the trial only.")
        else:
            action = get_records(get_model(env, "ir.actions.server"), [arguments["action_id"]])
        model_name = rule.model_name if rule is not None else action.model_name
        model = get_model(env, model_name, "create", to_change=True)
        # Discarded after: what Odoo fills may be set ("state": "sale" fires "when confirmed")
        values, _shown = read_values(model, arguments.get("record") or [], trial=True)
        change, _shown = read_values(model, arguments.get("change") or [], trial=True)
        if "base.automation" in env:
            # The other rules of the model are off for the trial: what fails is what is tried
            others = env["base.automation"].sudo().search(
                [("model_name", "=", model_name), ("id", "!=", rule.id if rule is not None else 0)])
            if others:
                others.active = False
                report.append(f"The other rules of {model_name} were off for the trial: "
                              + ", ".join(repr(other.name) for other in others) + ".")
        trigger = rule.trigger if rule is not None else ""
        if trigger == "on_unarchive" and "active" in model._fields:
            values.setdefault("active", False)
        name_field = model._fields.get(model._rec_name or "")
        if name_field and name_field.store and name_field.type == "char" and name_field.name not in values:
            # A dummy contact without a name broke the trial on res_partner_check_name
            values[name_field.name] = _("Nudge trial")
        # What the trial writes to fire the rule is not what the actions did
        written = set(change) | ({"active"} if trigger in ("on_archive", "on_unarchive") else set())
        names = _watched_fields(model)
        twins = None
        if rule is not None and trigger in ("on_create", "on_create_or_write") and not change:
            # The rule acts inside create(): what the record would hold without it
            # is read on twins made while it is off; two, for what changes by itself
            rule.active = False
            twins = [model.create(to_write(values, None)).read(names)[0] for _twin in range(2)]
            rule.active = True
        mails_at = _mail_watch(env)
        record = model.create(to_write(values, None))
        report.append(f"A dummy {model._description} was created: {record.display_name!r} (id {record.id}).")
        before = twins[0] if twins else record.read(names)[0]
        # A sequence, a date of creation: what differs between the twins is no change of the rule
        unstable = {name for name in names if twins and twins[0][name] != twins[1][name]}
        counted = _on_record(env, record)
        if rule is None:
            action.with_context(active_model=model._name, active_ids=record.ids, active_id=record.id).run()
            report.append(f"The server action {action.name!r} ran on it.")
        elif trigger == "on_create_or_write":
            if change:
                record.write(to_write(change, None))
                report.append("Then it was changed: " + ", ".join(f"{name}={value!r}" for name, value in change.items()) + ".")
        elif trigger == "on_archive":
            record.write({"active": False})
            report.append("Then it was archived.")
        elif trigger == "on_unarchive":
            record.write({"active": True})
            report.append("Then it was unarchived.")
        elif trigger == "on_unlink":
            record.unlink()
            report.append("Then it was deleted.")
        elif trigger in TIME_TRIGGERS:
            rule._process(record)
            report.append("The actions ran as if the time had come.")
        if record.exists():
            if rule is not None:
                # Judged once the trigger has done its part: a record matches
                # after the change that fires the rule
                matches = bool(rule._filter_post(record))
                report.append("The conditions match the dummy record." if matches
                              else "The conditions do NOT match the dummy record: the rule leaves it alone.")
            after = record.read(names)[0]
            changed = _changes(env, model, [name for name in names if name not in written | unstable],
                               before, after)
            report.append("Changed by the actions: " + "; ".join(changed) if changed
                          else "No field of the dummy record was changed by the actions.")
            activities, messages = _on_record(env, record)
            if activities > counted[0]:
                report.append(f"Activities scheduled on it: {activities - counted[0]}.")
            if messages > counted[1]:
                report.append(f"Messages posted on it: {messages - counted[1]}.")
        mails = _mails_since(env, mails_at)
        report.append(f"Emails that would be sent: {len(mails)}" + (" — " + "; ".join(mails) if mails else "."))

    def _try_cron(self, env, cron_id, report):
        Cron = get_model(env, "ir.cron").with_context(active_test=False)
        cron = get_records(Cron, [cron_id])
        mails_at = _mail_watch(env)
        counted = _made_since(env)
        cron.ir_actions_server_id.with_context(active_model=cron.model_id.model).run()
        report.append(f"The scheduled action {cron.cron_name!r} ran once, as {env.user.name}.")
        activities, messages = (now - then for now, then in zip(_made_since(env), counted))
        # A reminder makes activities: told none, a trial that found nothing looked fine
        report.append(f"Activities it scheduled: {activities}. Messages it posted: {messages}.")
        mails = _mails_since(env, mails_at)
        report.append(f"Emails that would be sent: {len(mails)}" + (" — " + "; ".join(mails) if mails else "."))
        report.append("What its code wrote in log() is in Settings ‣ Technical ‣ Logging.")


# ------------------------------------------------------- scheduled actions

# A reminder, written by Nudge: a small model could not write its code in eight tries
REMIND_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary"],
    "description": (
        "A reminder instead of code: an activity To Do, with summary, for the person of "
        "user_field (user_id when not given) on every record that matches the conditions and, "
        "with older_than_days, whose date_field (create_date when not given) is older."),
    "properties": {
        "summary": {"type": "string", "minLength": 2, "maxLength": 200},
        "conditions": CONDITION_SCHEMA,
        "older_than_days": {"type": "integer", "minimum": 1, "maximum": 3650},
        "date_field": {"type": "string", "pattern": FIELD_PATTERN},
        "user_field": {"type": "string", "pattern": FIELD_PATTERN},
    },
}


def remind_code(env, model, remind):
    """:return: ``(code, lines of the card)``: the code of a reminder, an
    activity To Do on every record that matches, written here
    :raise Refused: for a field that is not there, or not of its kind"""
    if "activity_ids" not in model._fields:
        raise Refused(f"{model._name} has no activities: a reminder is an activity on its records.")
    user_name = remind.get("user_field") or "user_id"
    user_field = model._fields.get(user_name)
    if not (user_field and user_field.type == "many2one" and user_field.comodel_name == "res.users"):
        users = [name for name, field in model._fields.items()
                 if field.type == "many2one" and field.comodel_name == "res.users" and field.store]
        raise Refused(f"user_field is the field of the person reminded, a user of {model._name}: one of "
                      f"{', '.join(users[:MAX_LISTED]) or 'none'}.")
    summary = " ".join(remind["summary"].split())
    domain = list(build_domain(model, remind["conditions"])) if remind.get("conditions") else []
    code = ["# Written by Nudge: an activity To Do for each person, once", f"domain = {domain!r}"]
    lines = [{"label": _("Reminds"), "value": field_label(user_field, env)},
             {"label": _("Summary"), "value": summary}]
    if remind.get("conditions"):
        lines.append({"label": _("Only for"), "value": in_words(model, remind["conditions"], "all")})
    if remind.get("older_than_days"):
        date_name = remind.get("date_field") or "create_date"
        date_field = model._fields.get(date_name)
        if not (date_field and date_field.type in ("date", "datetime") and date_field.store):
            dates = [name for name, field in model._fields.items()
                     if field.type in ("date", "datetime") and field.store]
            raise Refused(f"date_field is a date of {model._name}: one of {', '.join(dates[:MAX_LISTED])}.")
        now = "datetime.date.today()" if date_field.type == "date" else "datetime.datetime.now()"
        days = int(remind["older_than_days"])
        code.append(f"domain += [({date_name!r}, '<', {now} - datetime.timedelta(days={days}))]")
        lines.append({"label": _("Older than"), "value": _(
            "%(days)s days, by %(field)s", days=days, field=field_label(date_field, env))})
    code += [
        "for rec in model.search(domain):",
        # Every run would add the same activity again: once is enough
        f"    if rec.{user_name} and {summary!r} not in rec.activity_ids.mapped('summary'):",
        f"        rec.activity_schedule('mail.mail_activity_data_todo', user_id=rec.{user_name}.id, "
        f"summary={summary!r})",
    ]
    return "\n".join(code), lines


@base.register
class PrepareScheduledAction(Building):
    name = "prepare_scheduled_action"
    kind = "create"
    description = (
        "Make a scheduled action: code that runs by itself every so many minutes, hours, days, "
        "weeks or months, for what happens on a schedule rather than on an event (every "
        "morning, every Monday, at the end of the month). Every Monday is every 1 weeks, with "
        "first_run \"monday\" and the hour asked. The code runs with no record: it "
        "finds its own with model.search(...). Nothing is made by this call: the person is "
        "shown the action in full, the code included, and says yes or no; you read their "
        "answer as the result. A reminder (an activity for the person responsible of the records "
        "older than a number of days) is given as remind, not as code: Nudge writes its code. Check "
        "other code first "
        "with check_python_code. " + REMIND_TOLD
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "model", "every", "first_run"],
        "properties": {
            "name": {"type": "string", "minLength": 2, "maxLength": 120},
            "model": {"type": "string", "pattern": MODEL_PATTERN},
            "code": {"type": "string", "minLength": 1, "maxLength": MAX_CODE_CHARS},
            "remind": REMIND_SCHEMA,
            "every": {
                "type": "object",
                "additionalProperties": False,
                "required": ["number", "unit"],
                "properties": {
                    "number": {"type": "integer", "minimum": 1, "maximum": 1000},
                    "unit": {"type": "string", "enum": list(INTERVAL_UNITS)},
                },
            },
            "first_run": {
                "type": "string",
                "maxLength": 40,
                "description": (
                    "When it first runs: a day of the week and an hour, \"monday 08:00\" for the "
                    "next Monday; a date and time as the person writes it; or \"now\"."),
            },
        },
    }

    @refusing
    def run(self, env, arguments, screen):
        check_developing(env)
        model = get_model(env, arguments["model"])
        code = (arguments.get("code") or "").strip()
        reminder = []
        if arguments.get("remind"):
            if code:
                raise Refused("Give remind or code, not both: remind writes the code itself.")
            code, reminder = remind_code(env, model, arguments["remind"])
        elif not code:
            raise Refused(
                'Give code, or for a reminder remind: {"summary": "Follow up", '
                '"older_than_days": 14, "conditions": [{"field": "...", "operator": "=", '
                '"value": "..."}]}.')
        error = compile_code(code, env, model._name)
        if error:
            raise Refused(f"The code does not compile. {error}")
        first = (arguments.get("first_run") or "").strip()
        if not first or first.lower() == "now":
            when = odoo_fields.Datetime.now()
        else:
            when = next_weekday(env, first) or read_moment(env, first)
        every = arguments["every"]
        values = {
            "name": arguments["name"].strip(),
            "model_id": ir_model(env, model).id,
            "state": "code",
            "code": code,
            "usage": "ir_cron",
            "interval_number": every["number"],
            "interval_type": every["unit"],
            "nextcall": odoo_fields.Datetime.to_string(when),
            "user_id": env.uid,
            "active": True,
        }
        shown_when = odoo_fields.Datetime.context_timestamp(env.user, when).strftime("%Y-%m-%d %H:%M")
        lines = [
            {"label": _("Model"), "value": model_label(model)},
            {"label": _("Every"), "value": every_text(env, every["number"], every["unit"])},
            {"label": _("First run"), "value": shown_when},
            {"label": _("Runs as"), "value": env.user.name},
            *reminder,
            {"label": _("Code"), "value": values["code"], "code": True},
        ]
        return base.ToolResult(
            "The person is being shown the scheduled action, in full.",
            pending={
                "summary": _("Create: scheduled action “%s”", values["name"]),
                "res_model": "ir.cron",
                "res_ids": [],
                "record_names": values["name"],
                "values": {"cron": values},
                "is_sensitive": True,
                "preview": {"records": [], "lines": lines},
            },
        )

    def execute(self, env, step):
        check_developing(env)
        values = (step.values or {}).get("cron") or {}
        if not values.get("name") or values.get("user_id") != env.uid:
            return base.ToolResult("This scheduled action cannot be made.", is_error=True)
        cron = env["ir.cron"].create(values)
        return base.ToolResult(
            f"Made: scheduled action #{cron.id} {cron.cron_name!r}, every {cron.interval_number} "
            f"{cron.interval_type}, first on {cron.nextcall}. It is in Settings ‣ Technical ‣ "
            "Scheduled Actions; try it once with try_automation and its cron_id.",
            event={"type": "changed", "model": "ir.cron", "ids": cron.ids},
            records=("ir.cron", cron.ids, [cron.cron_name]),
        )

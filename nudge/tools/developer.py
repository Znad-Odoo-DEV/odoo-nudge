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
import re
from datetime import timedelta

import pytz

from odoo import Command
from odoo.tools import plaintext2html
from odoo.tools import safe_eval as safe_eval_module
from odoo.tools.safe_eval import test_python_expr

from odoo import fields as odoo_fields

from . import base, schema
from .records import has_field_access  # noqa: E402
from .actions import VALUES_SCHEMA, read_moment, read_value, read_values, to_write
from .records import (
    MODEL_PATTERN, OPERATORS, PATH_PATTERN, Refused, build_domain, cut, format_value, get_model,
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
            "minutes": env._("minute"), "hours": env._("hour"), "days": env._("day"),
            "weeks": env._("week"), "months": env._("month"),
        }[unit]
    return {
        "minutes": env._("%s minutes", number), "hours": env._("%s hours", number),
        "days": env._("%s days", number), "weeks": env._("%s weeks", number),
        "months": env._("%s months", number),
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


# What the code of a server action is given when it runs (ir.actions.server,
# base.automation): any other name it reads fails then
CODE_NAMES = frozenset({
    "env", "model", "record", "records", "time", "datetime", "dateutil", "timezone", "float_compare",
    "b64encode", "b64decode", "Command", "UserError", "log", "_logger", "uid", "user", "payload",
})


def compile_code(code):
    """The code of a server action, an automation rule or a scheduled action.
    :return: what is wrong with it, or "" """
    text = (code or "").strip()
    if not text:
        return "The code is empty."
    error = test_python_expr(text, mode="exec")
    if error:
        return str(error)
    tree = ast.parse(text)
    unknown = unknown_names(tree)
    if unknown:
        # A small model wrote object and self: the action failed when run
        return (f"{', '.join(unknown)} is not known when the code runs. It knows: "
                f"{', '.join(sorted(CODE_NAMES))}; the record is record (records for several).")
    if not any(isinstance(node, (ast.Call, ast.Raise)) or (
            isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(node.ctx, ast.Store))
            for node in ast.walk(tree)):
        # lang = 'ar' was made into an automation rule that changed nothing
        return ("The code changes nothing: it calls no method and writes no field. Write it, such as "
                "record.write({'lang': 'ar_001'}), or use an update action instead of code.")
    return ""


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
        lines = []
        if "base.automation" in env:
            domain = [("model_name", "=", model._name)] if model is not None else [("name", "ilike", words)]
            rules = env["base.automation"].with_context(active_test=False).search(domain, limit=MAX_LISTED)
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
        domain += [("model_name", "=", model._name)] if model is not None else [("name", "ilike", words)]
        actions = Action.search(domain, limit=MAX_LISTED)
        lines.append(f"Server actions: {len(actions)}")
        for action in actions:
            where = " (in the Actions menu)" if action.binding_model_id else ""
            lines.append(f"- #{action.id} {action.name} on {action.model_name} ({action.state}){where}" + self._detail(action))
        domain = [("model_id.model", "=", model._name)] if model is not None else [("cron_name", "ilike", words)]
        crons = env["ir.cron"].with_context(active_test=False).search(domain, limit=MAX_LISTED)
        lines.append(f"Scheduled actions: {len(crons)}")
        for cron in crons:
            lines.append(
                f"- #{cron.id} {cron.cron_name} on {cron.model_id.model}: every {cron.interval_number} "
                f"{cron.interval_type}, next {cron.nextcall}, as {cron.user_id.name}"
                + ("" if cron.active else " (off)") + self._detail(cron.ir_actions_server_id))
        if "mail.template" in env:
            domain = [("model", "=", model._name)] if model is not None else [("name", "ilike", words)]
            templates = env["mail.template"].with_context(active_test=False).search(domain, limit=MAX_LISTED)
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
        error = compile_code(arguments["code"])
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
        {"label": env._("To"), "value": ", ".join(shown_to)},
        {"label": env._("Copy to"), "value": ", ".join(shown_cc) or None},
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
        {"label": env._("Subject"), "value": values["subject"]},
        {"label": env._("Body"), "value": body, "code": True},
    ]
    if email.get("send_as", "email") != "email":
        lines.append({"label": env._("Sent as"), "value": email["send_as"]})
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
        error = compile_code(action.get("code"))
        if error:
            raise Refused(f"Action {position}: the code does not compile. {error}")
        values.update(state="code", code=action["code"].strip())
        lines.append({"label": env._("Code"), "value": values["code"], "code": True})
        name = name or env._("Run code")
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
        if action.get("expression") and (action.get("value") or "").strip():
            raise Refused(
                f"Action {position}: an update gives a value or an expression, not both. A fixed "
                "value, such as false to archive, is a value.")
        if action.get("expression"):
            error = test_python_expr(action["expression"].strip(), mode="eval")
            if error:
                raise Refused(f"Action {position}: the expression does not compute. {error}")
            values.update(state="object_write", update_path=path, evaluation_type="equation",
                          value=action["expression"].strip())
            lines.append({"label": label, "value": env._("computed: %s", action["expression"].strip())})
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
        name = name or env._("Update %s", label)
    elif kind == "email":
        if "mail.template" not in env:
            raise Refused("Emails need the app Discuss (mail), which is not installed.")
        template = None
        if action.get("template_id"):
            record = get_records(get_model(env, "mail.template"), [action["template_id"]])
            if record.model != model._name:
                raise Refused(f"Action {position}: template #{record.id} is for {record.model}, not {model._name}.")
            values["template_id"] = record.id
            lines.append({"label": env._("Template"), "value": record.name})
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
        name = name or env._("Send an email")
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
        lines.append({"label": env._("Activity"), "value": summary})
        if action.get("note"):
            lines.append({"label": env._("Note"), "value": action["note"].strip()})
        if action.get("days"):
            lines.append({"label": env._("Due in"), "value": env._("%s day(s)", action["days"])})
        if action.get("user_field"):
            field = target_field(model, action["user_field"])
            if field.type != "many2one" or field.comodel_name != "res.users":
                raise Refused(f"Action {position}: {action['user_field']} does not hold a user.")
            values.update(activity_user_type="generic", activity_user_field_name=action["user_field"])
            lines.append({"label": env._("For"), "value": field_label(field, env)})
        else:
            values.update(activity_user_type="specific", activity_user_id=env.uid)
            lines.append({"label": env._("For"), "value": env.user.name})
        name = name or env._("Schedule: %s", summary)
    return dict(values, name=name), None, lines


def make_action(env, values, template):
    """Create the server action and, when it comes with one, its template."""
    values = dict(values)
    if template:
        values["template_id"] = env["mail.template"].create(template).id
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
        {"label": env._("Model"), "value": model_label(model)},
        {"label": env._("When"), "value": TRIGGERS[trigger]},
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
        lines.append({"label": env._("Only when these change"), "value": ", ".join(found.mapped("field_description"))})
    if arguments.get("conditions"):
        domain = build_domain(model, arguments["conditions"], arguments.get("match", "all"))
        values["filter_domain"] = str(list(domain))
        lines.append({"label": env._("Only for"), "value": in_words(model, arguments["conditions"], arguments.get("match", "all"))})
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
            lines.append({"label": env._("Counted from"), "value": field.field_description})
        when = env._("%(number)s %(unit)s before", number=-delay["number"], unit=delay["unit"]) if delay["number"] < 0 \
            else env._("%(number)s %(unit)s after", number=delay["number"], unit=delay["unit"])
        lines.append({"label": env._("Delay"), "value": when})
    elif arguments.get("delay"):
        raise Refused("delay goes with the time triggers only.")
    actions, templates = [], []
    for position, action in enumerate(arguments["actions"], start=1):
        action_values, template, action_lines = read_action(env, model, action, position)
        action_values["usage"] = "base_automation"
        actions.append(action_values)
        templates.append(template)
        lines.append({"label": env._("Action %s", position) + f": {action_values['name']}", "lines": action_lines})
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
                "summary": env._("Create: automation rule “%s”", values["name"]),
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
        lines = [{"label": env._("Model"), "value": model_label(model)}] + lines + [
            {"label": env._("Shown in"), "value": env._("the Actions menu of %s", model_label(model))
             if arguments.get("in_menu") else env._("nowhere yet: a button or a rule will run it")},
        ]
        return base.ToolResult(
            "The person is being shown the server action, in full.",
            pending={
                "summary": env._("Create: server action “%s”", values["name"]),
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
        return base.ToolResult(
            f"Made: server action #{action.id} {action.name!r} ({action.state}).{where} "
            "It is in Settings ‣ Technical ‣ Server Actions.",
            event={"type": "changed", "model": "ir.actions.server", "ids": action.ids},
            records=("ir.actions.server", action.ids, [action.name]),
        )


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
        lines = [{"label": env._("Model"), "value": model_label(model)}] + lines
        return base.ToolResult(
            "The person is being shown the email template, in full.",
            pending={
                "summary": env._("Create: email template “%s”", values["name"]),
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
        template = env["mail.template"].create(values)
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
        lines = [{"label": env._("Kind"), "value": model_label(model)},
                 {"label": env._("Undo"), "value": env._("Not possible: deleted for good")}]
        if model._name == "ir.model.fields":
            lines.append({"label": env._("Data"), "value": env._("The column and every value in it are deleted too")})
            showing = [view.name for field in records for view in self._views_showing(field)]
            if showing:
                lines.append({"label": env._("Views"), "value": env._("Taken off the screens: %s", ", ".join(showing))})
        return base.ToolResult(
            "The person is being shown what is about to be deleted.",
            pending={
                "summary": env._("Delete: %s", ", ".join(names)[:200]),
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
    for views in extra:
        if records._name == "ir.model.fields":
            views.unlink()
    records.unlink()
    for views in extra:
        if views.exists():
            views.unlink()
    if mail_templates:
        mail_templates.exists().unlink()
    env.registry.clear_cache("templates")
    return names


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
    :return: ``(names deleted, names kept with the reason)``"""
    deleted, kept = [], []
    for step, records in built_by(env, runs):
        records = records.with_env(env).exists()
        if not records:
            continue
        try:
            with env.cr.savepoint():
                deleted += [f"{records._description}: {name}" for name in delete_built(records)]
        except Refused as refusal:
            kept.append(f"{records._description}: {', '.join(records.mapped('display_name'))} ({refusal})")
    return deleted, kept


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
        return base.ToolResult(
            "The person is being shown what is about to be deleted.",
            pending={
                "summary": env._("Undo what was built: %s item(s)", len(found)),
                "res_model": "nudge.run",
                "res_ids": runs.ids,
                "record_names": ", ".join(item["name"] for item in shown)[:500],
                "values": {"run_ids": runs.ids},
                "is_sensitive": True,
                "preview": {"records": shown, "lines": [
                    {"label": env._("Undo"), "value": env._("Not possible: deleted for good")},
                    {"label": env._("Not undone"), "value": env._("What was changed rather than made")},
                ]},
            },
        )

    def execute(self, env, step):
        check_developing(env)
        runs = env["nudge.run"].sudo().browse((step.values or {}).get("run_ids") or []).exists()
        runs = runs.filtered(lambda run: run.user_id == env.user and run.is_developer)
        deleted, kept = undo_built(env, runs)
        told = f"Deleted {len(deleted)} item(s): " + "; ".join(deleted) if deleted else "Nothing was deleted."
        if kept:
            told += " Kept: " + "; ".join(kept)
        return base.ToolResult(
            told,
            event={"type": "changed", "model": "ir.ui.view", "ids": [], "action": {"type": "ir.actions.client", "tag": "reload"}},
            records=("nudge.run", runs.ids, deleted[:10]),
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
        "Give one of: rule (what prepare_automation takes), rule_id, action_id, cron_id. For a "
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
    def _given(arguments):
        given = [key for key in ("rule", "rule_id", "action_id", "cron_id") if arguments.get(key)]
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
        return given[0]

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
        given = self._given(arguments)
        what, code = self._code_of(env, given, arguments)
        if not code:
            return self._trial(env, given, arguments)
        lines = [{"label": env._("Tried"), "value": what}]
        lines += [{"label": env._("Code"), "value": text, "code": True} for text in code]
        lines.append({"label": env._("Note"), "value": env._(
            "The code runs for real inside the trial. What it writes is discarded; an email it "
            "sends by itself goes out.")})
        return base.ToolResult(
            "The person is being shown the code the trial runs.",
            pending={
                "summary": env._("Try on a dummy record: %s", what),
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
            return self._trial(env, self._given(arguments), arguments)
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
        values, _shown = read_values(model, arguments.get("record") or [])
        change, _shown = read_values(model, arguments.get("change") or [])
        trigger = rule.trigger if rule is not None else ""
        if trigger == "on_unarchive" and "active" in model._fields:
            values.setdefault("active", False)
        name_field = model._fields.get(model._rec_name or "")
        if name_field and name_field.store and name_field.type == "char" and name_field.name not in values:
            # A dummy contact without a name broke the trial on res_partner_check_name
            values[name_field.name] = env._("Nudge trial")
        # What the trial writes to fire the rule is not what the actions did
        written = set(change) | ({"active"} if trigger in ("on_archive", "on_unarchive") else set())
        mails_at = _mail_watch(env)
        record = model.create(to_write(values, None))
        report.append(f"A dummy {model._description} was created: {record.display_name!r} (id {record.id}).")
        names = _watched_fields(model)
        before = record.read(names)[0]
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
            changed = _changes(env, model, [name for name in names if name not in written], before, after)
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
        cron.ir_actions_server_id.with_context(active_model=cron.model_id.model).run()
        report.append(f"The scheduled action {cron.cron_name!r} ran once, as {env.user.name}.")
        mails = _mails_since(env, mails_at)
        report.append(f"Emails that would be sent: {len(mails)}" + (" — " + "; ".join(mails) if mails else "."))
        report.append("What its code wrote in log() is in Settings ‣ Technical ‣ Logging.")


# ------------------------------------------------------- scheduled actions

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
        "answer as the result. Try the code first with run_python."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "model", "code", "every", "first_run"],
        "properties": {
            "name": {"type": "string", "minLength": 2, "maxLength": 120},
            "model": {"type": "string", "pattern": MODEL_PATTERN},
            "code": {"type": "string", "minLength": 1, "maxLength": MAX_CODE_CHARS},
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
        error = compile_code(arguments["code"])
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
            "code": arguments["code"].strip(),
            "usage": "ir_cron",
            "interval_number": every["number"],
            "interval_type": every["unit"],
            "nextcall": odoo_fields.Datetime.to_string(when),
            "user_id": env.uid,
            "active": True,
        }
        shown_when = odoo_fields.Datetime.context_timestamp(env.user, when).strftime("%Y-%m-%d %H:%M")
        lines = [
            {"label": env._("Model"), "value": model_label(model)},
            {"label": env._("Every"), "value": every_text(env, every["number"], every["unit"])},
            {"label": env._("First run"), "value": shown_when},
            {"label": env._("Runs as"), "value": env.user.name},
            {"label": env._("Code"), "value": values["code"], "code": True},
        ]
        return base.ToolResult(
            "The person is being shown the scheduled action, in full.",
            pending={
                "summary": env._("Create: scheduled action “%s”", values["name"]),
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

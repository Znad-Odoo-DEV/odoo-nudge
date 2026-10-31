"""Changing the records of the business, for the person and as the person.

A change is made in two times. The model asks for it: what it asks is
checked, read as the fields expect it, and described. Nothing is done. The
person reads the description and says yes or no. On a yes, the change is
made in their environment, with their rights, the way their own click would.

Nudge never deletes.
"""
import json
import re

import pytz
from lxml import etree

from odoo import Command, fields
from odoo.api import call_kw
from odoo.service.model import get_public_method
from odoo.tools import plaintext2html
from odoo.tools.safe_eval import safe_eval
from odoo.tools.view_validation import get_expression_field_names

from . import base
from .records import (
    has_field_access,
    FIELD_PATTERN,
    ME,
    MODEL_PATTERN,
    Refused,
    close_fields,
    cut,
    describe,
    format_value,
    get_model,
    get_records,
    holds_secret,
    model_label,
    read_choice,
    refusing,
)

MAX_VALUES = 30
MAX_LINES = 20
MAX_RECORDS = 20
MAX_VALUE_CHARS = 4000
MAX_SHOWN_RECORDS = 10

WRITABLE_TYPES = (
    "char", "text", "html", "integer", "float", "monetary", "boolean", "date", "datetime",
    "selection", "many2one", "many2many",
)
# Written by Odoo itself
KEPT_FIELDS = ("id", "create_uid", "create_date", "write_uid", "write_date", "display_name")
TRUE_WORDS = ("true", "yes", "1")
FALSE_WORDS = ("false", "no", "0")
EMPTY_WORDS = ("", "false", "none", "null")

# What cannot be taken back with a click, or moves money or goods: a whole
# word of the method's name, so that compost or postal is not post
SENSITIVE_METHOD = re.compile(
    r"(^|_)(post|validate|cancel|pay|refund|reverse|lock|draft|approve|refuse|reconcile|done|close|"
    r"send|confirm|archive)(_|$)")
# Fields a list of ids would empty of what they hold: added to or taken from only
KEPT_WHOLE = {"res.users": ("group_ids", "groups_id", "company_ids")}
SENSITIVE_MODEL = re.compile(
    r"^(account|payment|stock|hr\.payslip|hr\.expense|res\.users|res\.groups|res\.company|ir)(\.|$)")

VALUES_SCHEMA = {
    "type": "array",
    "minItems": 1,
    "maxItems": MAX_VALUES,
    "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["field", "value"],
        "properties": {
            "field": {"type": "string", "pattern": FIELD_PATTERN},
            "value": {
                "type": "string",
                "maxLength": MAX_VALUE_CHARS,
                "description": (
                    "Always written as text. A number: \"12.5\". Yes or no: \"true\", "
                    "\"false\". A date: \"2026-10-01\"; with a time, in the time of the person: "
                    "\"2026-10-01 14:30\". A choice: its label or its key. A record: its id, "
                    "found with search_records; \"me\" only for the person asking, never someone "
                    "they name. Several records: ids between "
                    "commas, which replace those in the field; \"+12\" adds one and \"-12\" "
                    "takes one out, the others staying (\"+12, +15\"). Text, such as a city or "
                    "a street: the words themselves, never looked for. Empty empties the field."
                ),
            },
        },
    },
}
LINES_SCHEMA = {
    "type": "array",
    "maxItems": MAX_LINES,
    "description": (
        "Lines to add to the record, such as the lines of an order: one entry per line, with "
        "the field of the record that holds the lines and the values of the line."
    ),
    "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["field", "values"],
        "properties": {
            "field": {
                "type": "string",
                "pattern": FIELD_PATTERN,
                "description": "Such as \"order_line\".",
            },
            "values": dict(VALUES_SCHEMA),
        },
    },
}


# ----------------------------------------------------------------------
# What the model wrote, read as the fields expect it
# ----------------------------------------------------------------------

BY_LABEL = "Name the field to the person by its label, never by its technical name."


def field_label(model, field, name):
    """:return: the label of ``field`` as the person reads it, in their language (the technical name when it has
    none)"""
    return (field.get_description(model.env, ["string"]).get("string") or field.string or name) if field else name


def writable_field(model, name, trial=False):
    """:param trial: for a trial that is discarded: a stored field Odoo fills
        (state) may be set, to fire what it fires"""
    field = model._fields.get(name)
    if field is None or not has_field_access(model, field, "write"):
        known = close_fields(model, name, "write")
        hint = f" Close to it: {', '.join(known)}." if known else ""
        raise Refused(
            f"{model._name} has no field {name!r} that this person can change.{hint} "
            "describe_model lists the fields.")
    # The person reads the label of a field, never its technical name: the refusal names it by the label
    label = field_label(model, field, name)
    if name in KEPT_FIELDS or holds_secret(model, field):
        raise Refused(f"The field {label!r} is not one Nudge writes in. {BY_LABEL}")
    if field.type not in WRITABLE_TYPES:
        raise Refused(
            f"{label!r} is a {field.type} field: Nudge does not write in it. "
            f"Show the person where it is instead. {BY_LABEL}")
    if (field.readonly and not (trial and field.store)) or (field.compute and not field.inverse and not field.store):
        # SEC-01: a button is refused only once pressed (an approval rule runs then): what is not known is not offered
        raise Refused(
            f"{label!r} is filled by Odoo: it cannot be written in. Say so and stop: do not offer a button or another way "
            f"to the same result, which may be refused for this person too. {BY_LABEL}")
    return field


def read_id(env, field, text):
    comodel = env[field.comodel_name]
    if text.lower() == ME and field.comodel_name == "res.users":
        return env.user
    if text.lower() == ME and field.comodel_name == "res.partner":
        return env.user.partner_id
    if not text.isdigit():
        raise Refused(
            f"{field.name} points to a record of {field.comodel_name}: give its id, not "
            f"{text!r}. search_records finds it.")
    record = comodel.browse(int(text)).exists()
    if not record or not record.has_access("read"):
        raise Refused(
            f"There is no {field.comodel_name} {text} that this person can read. "
            "search_records finds the right one.")
    return record


def read_moment(env, text):
    moment = fields.Datetime.to_datetime(text)
    # Told in the time of the person, kept in universal time
    zone = pytz.timezone(env.user.tz or env.context.get("tz") or "UTC")
    return zone.localize(moment).astimezone(pytz.utc).replace(tzinfo=None)


def read_value(env, field, raw):
    """:param str raw: the value as the model wrote it
    :return: ``(value, shown)``: what is written in the field, as JSON holds
        it, and what the person is shown of it"""
    text = raw.strip()
    if field.type == "boolean":
        if text.lower() not in TRUE_WORDS + FALSE_WORDS:
            raise Refused(f"{field.name} is yes or no: write \"true\" or \"false\".")
        value = text.lower() in TRUE_WORDS
        return value, value
    if text.lower() in EMPTY_WORDS and field.type not in ("char", "text", "html"):
        return False, None
    if not text:
        return False, None
    try:
        if field.type in ("char", "text"):
            value = " ".join(raw.split()) if field.type == "char" else raw.strip()
            return value, cut(value)
        if field.type == "html":
            # Text, never markup: what a model writes is not trusted to be harmless
            return str(plaintext2html(raw.strip())), cut(raw)
        if field.type == "integer":
            return int(text), int(text)
        if field.type in ("float", "monetary"):
            return float(text), float(text)
        if field.type == "date":
            value = fields.Date.to_string(fields.Date.to_date(text))
            return value, value
        if field.type == "datetime":
            value = fields.Datetime.to_string(read_moment(env, text))
            return value, text
    except (ValueError, TypeError):
        raise Refused(f"{raw!r} cannot be read as the {field.type} that {field.name} holds.") from None
    if field.type == "selection":
        # "ar" and "Arabic" were refused for the language "Arabic / الْعَرَبيّة" (ar_001)
        key = read_choice(text, field, env)
        return key, dict(field._description_selection(env)).get(key, key)
    if field.type == "many2one":
        record = read_id(env, field, text)
        return record.id, cut(record.display_name, 120)
    if field.type == "many2many":
        try:
            items = json.loads(text)
        except ValueError:
            items = text.split(",")
        items = [str(item).strip() for item in (items if isinstance(items, list) else [items])]
        items = [item for item in items if item][:MAX_RECORDS]
        if any(item[:1] in "+-" for item in items):
            # Added to, or taken from: what else the field holds stays
            change = {"add": [], "remove": []}
            shown = []
            for item in items:
                key, sign = ("remove", "−") if item[:1] == "-" else ("add", "+")
                record = read_id(env, field, item.lstrip("+-").strip())
                change[key].append(record.id)
                shown.append(f"{sign} {cut(record.display_name, 60)}")
            return change, ", ".join(shown)
        if field.name in KEPT_WHOLE.get(field.model_name, ()):
            raise Refused(
                f"{field.name} holds every access of the user: never replaced. Add one with "
                "\"+id\", take one out with \"-id\".")
        records = [read_id(env, field, item) for item in items]
        names = ", ".join(cut(record.display_name, 60) for record in records)
        return [record.id for record in records], names
    raise Refused(f"Nudge does not write in {field.name}.")


def read_values(model, entries, trial=False):
    """:param entries: list of ``{"field", "value"}`` written by the model
    :param trial: for a trial that is discarded, see :func:`writable_field`
    :return: ``(values, lines)``: what is written, by field, and what the
        person is shown"""
    values, shown = {}, []
    labels = model.fields_get([entry["field"] for entry in entries], ["string"])
    for entry in entries:
        name = entry["field"]
        field = writable_field(model, name, trial)
        if name in values:
            raise Refused(f"The field {name!r} is given twice.")
        values[name], text = read_value(model.env, field, entry["value"])
        shown.append({
            "field": name,
            "label": labels.get(name, {}).get("string") or name,
            "value": text,
        })
    return values, shown


def read_lines(model, entries):
    """:param entries: list of ``{"field", "values"}``: lines to add
    :return: ``(lines, shown)``: values of the lines by field, and what the
        person is shown"""
    lines, shown = {}, []
    for entry in entries:
        name = entry["field"]
        field = model._fields.get(name)
        if field is None or field.type != "one2many" or not has_field_access(model, field, "write"):
            raise Refused(f"{name!r} is not a field of {model._name} that holds lines.")
        comodel = get_model(model.env, field.comodel_name, "create", to_change=True)
        values, texts = read_values(comodel, entry["values"])
        if field.inverse_name in values:
            raise Refused(f"A line belongs to its record: {field.inverse_name!r} is not given.")
        lines.setdefault(name, []).append(values)
        label = model.fields_get([name], ["string"])[name]["string"]
        shown.append({"field": name, "label": label, "value": None, "lines": texts})
    return lines, shown


def to_write(values, lines):
    """:return: what the ORM is given"""
    written = dict(values)
    for name, entries in (lines or {}).items():
        written[name] = [Command.create(entry) for entry in entries]
    for name, value in values.items():
        if isinstance(value, list):
            written[name] = [Command.set(value)]
        elif isinstance(value, dict) and set(value) <= {"add", "remove"}:
            written[name] = [Command.link(ident) for ident in value.get("add", [])] + [
                Command.unlink(ident) for ident in value.get("remove", [])]
    return written


def is_sensitive(model_name, method=None):
    return bool(SENSITIVE_MODEL.match(model_name) or (method and SENSITIVE_METHOD.search(method)))


def name_records(records):
    return [
        {"id": record.id, "name": cut(record.display_name, 120)}
        for record in records[:MAX_SHOWN_RECORDS]
    ]


def check_screen(screen, model, records):
    """What the person typed and did not save would be lost, or would come
    on top of what Nudge does."""
    if not screen or screen.get("model") != model._name:
        return
    shown = screen.get("record") if isinstance(screen.get("record"), dict) else {}
    if shown.get("unsaved_changes") and shown.get("id") in records.ids:
        raise Refused(
            "The person has changes on this record that they did not save. Ask them to save "
            "or discard them first, then to ask again.")


# ----------------------------------------------------------------------
# Tools
# ----------------------------------------------------------------------

# A contact is a person unless the sentence says company: what says it, and what takes it back ("not companies")
COMPANY_WORDS = re.compile(
    r"\b(?:compan(?:y|ies)|organi[sz]ations?|firms?|corporations?|enterprises?|llc|ltd|inc|gmbh)\b"
    r"|شرك[ةات]|شركه|مؤسس[ةات]|مؤسسه|منشأ[ةات]|منشآت|منشاه|منشاة|مصنع|مصانع",
    re.IGNORECASE)
NOT_COMPANY = re.compile(
    r"(?:\bnot|\bno|\bnever|\binstead of|\brather than|\bwithout|ليس[تا]?|وليس[تا]?|لا|مو|مش|بدون|وبدون)\s+"
    r"(?:(?:a|an|the|any)\s+)?(?:compan|organi|firm|corporation|enterprise|شرك|مؤسس|منشأ|منشآ|منشا)\w*",
    re.IGNORECASE)


def person_unless_company(env, model, entries):
    """:return: ``entries`` of a contact to create, without the mark that makes it a company when the person
    did not say company (“Create contacts Raghad A, B and C” made companies): Odoo's default is a person. Left
    alone outside a conversation, where nothing was said."""
    if model._name != "res.partner":
        return entries
    marks = [entry for entry in entries
             if (entry["field"] == "is_company" and str(entry["value"]).strip().lower() in ("true", "yes", "1"))
             or (entry["field"] == "company_type" and str(entry["value"]).strip().lower() == "company")]
    try:
        conversation_id = int(env.context.get("nudge_conversation_id") or 0)
    except (TypeError, ValueError):
        conversation_id = 0
    if not marks or not conversation_id:
        return entries
    # sudo: what the person said in their own conversation, read to know whether it says company
    asked = env["nudge.message"].sudo().search([
        ("conversation_id", "=", conversation_id), ("role", "=", "user")], order="id desc", limit=20)
    said = NOT_COMPANY.sub(" ", " ".join(asked.mapped("content")))
    if COMPANY_WORDS.search(said):
        return entries
    return [entry for entry in entries if entry not in marks]


@base.register
class PrepareCreate(base.Action):
    name = "prepare_create"
    kind = "create"
    description = (
        "Create a record of the business, as the person: a quotation, a contact, a task, a "
        "record of a custom module. Nothing is created by this call: the person is shown what "
        "you are about to create, and says yes or no. You read their answer as the result. "
        "Find the records you point to with search_records first, and look at describe_model "
        "for the fields that are required. Give only what you know: Odoo fills the rest the "
        "way it does when someone presses New. A contact is a person unless the person said "
        "company: never set is_company (or company_type) for a name that is not clearly one."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["model", "values"],
        "properties": {
            "model": {"type": "string", "pattern": MODEL_PATTERN},
            "values": VALUES_SCHEMA,
            "lines": LINES_SCHEMA,
        },
    }

    @refusing
    def run(self, env, arguments, screen):
        model = get_model(env, arguments["model"], "create", to_change=True)
        values, shown = read_values(model, person_unless_company(env, model, arguments["values"]))
        lines, shown_lines = read_lines(model, arguments.get("lines") or [])
        title = env._("Create: %s", model_label(model))
        return base.ToolResult(
            "The person is being shown what is about to be created.",
            pending={
                "summary": title,
                "res_model": model._name,
                "res_ids": [],
                "values": {"values": values, "lines": lines},
                "is_sensitive": is_sensitive(model._name),
                "preview": {"records": [], "lines": shown + shown_lines},
            },
        )

    def execute(self, env, step):
        model = get_model(env, step.res_model, "create", to_change=True)
        written = to_write(step.values["values"], step.values.get("lines"))
        record = model.create(written)
        state = describe(env, record, [
            name for name in ("name", "state") if name in model._fields
        ])[0]
        return base.ToolResult(
            f"Created: {model._description} {record.display_name!r}, id {record.id}. "
            f"{base.dump(state)}",
            event={"type": "changed", "model": model._name, "ids": record.ids},
            records=(model._name, record.ids, [record.display_name]),
        )


@base.register
class PrepareWrite(base.Action):
    name = "prepare_write"
    kind = "write"
    description = (
        "Change records of the business, as the person: fill or correct fields, add lines. "
        "Nothing is changed by this call: the person is shown the fields as they are and as "
        "they will be, and says yes or no. You read their answer as the result. Find the ids "
        "with search_records first. A field that holds text, such as \"city\" or \"street\", "
        "takes the words as the person said them: only a field that points to a record needs "
        "an id. Unsure of a field: describe_model gives its type. To change a line of an "
        "order, change the line itself, by its own model and id. To archive a record, write "
        "\"false\" in its field \"active\"."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["model", "ids"],
        "properties": {
            "model": {"type": "string", "pattern": MODEL_PATTERN},
            "ids": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_RECORDS,
                "items": {"type": "integer", "minimum": 1},
            },
            "values": dict(VALUES_SCHEMA, minItems=0),
            "lines": LINES_SCHEMA,
        },
    }

    @refusing
    def run(self, env, arguments, screen):
        model = get_model(env, arguments["model"], "write", to_change=True)
        records = get_records(model, arguments["ids"], "write")
        check_screen(screen, model, records)
        values, shown = read_values(model, arguments.get("values") or [])
        lines, shown_lines = read_lines(model, arguments.get("lines") or [])
        if not values and not lines:
            raise Refused("There is nothing to change: give values, or lines to add.")
        if len(records) == 1:
            # As it is, next to as it will be
            before = records.read(list(values))[0]
            for line in shown:
                field = model._fields[line["field"]]
                line["before"] = format_value(env, field, before.get(line["field"]))
                if isinstance(line["before"], dict):
                    line["before"] = line["before"].get("name") or line["before"].get("count")
        title = env._("Change: %s", model_label(model))
        # Imported here: plans are built on the actions of this file
        from .plans import snapshot  # noqa: PLC0415

        # What the fields to write hold now: the yes is refused if someone changed them since
        current = {str(record.id): snapshot(record, list(values)) for record in records}
        return base.ToolResult(
            "The person is being shown what is about to be changed.",
            pending={
                "summary": title,
                "res_model": model._name,
                "res_ids": records.ids,
                "record_names": ", ".join(records[:MAX_SHOWN_RECORDS].mapped("display_name")),
                "values": {"values": values, "lines": lines, "current": current},
                "is_sensitive": is_sensitive(model._name) or values.get("active") is False,
                "preview": {"records": name_records(records), "lines": shown + shown_lines},
            },
        )

    def execute(self, env, step):
        model = get_model(env, step.res_model, "write", to_change=True)
        records = get_records(model, step.res_ids, "write")
        # Imported here: plans are built on the actions of this file
        from .plans import snapshot  # noqa: PLC0415

        names = list(step.values["values"])
        before = {str(record.id): snapshot(record, names) for record in records}
        # Steps made before the snapshot existed have none and run as they always did
        for key, seen in (step.values.get("current") or {}).items():
            if before.get(key) is not None and any(before[key].get(n) != v for n, v in seen.items()):
                record = records.browse(int(key))
                field = next(n for n, v in seen.items() if before[key].get(n) != v)
                raise Refused(env._(
                    "%(record)s was changed since you were shown this: its field %(field)s is no longer "
                    "what it was. Nothing was written; ask again to see it as it is now.",
                    record=record.display_name, field=model._fields[field].string))
        records.write(to_write(step.values["values"], step.values.get("lines")))
        step.values = dict(step.values, undo={model._name: {
            str(record.id): {"before": before[str(record.id)], "after": snapshot(record, names)}
            for record in records
        }})
        names = ", ".join(repr(name) for name in records[:MAX_SHOWN_RECORDS].mapped("display_name"))
        return base.ToolResult(
            f"Changed: {model._description} {names}.",
            event={"type": "changed", "model": model._name, "ids": records.ids},
            records=(model._name, records.ids, records.mapped("display_name")),
        )


def form_buttons(model):
    """:return: the buttons of the form of ``model`` that run a method, as
    this person has them: ``{method: [nodes]}``. A form often has the same
    button twice, each for some states of the record."""
    arch = etree.fromstring(model.get_view(view_type="form")["arch"])
    buttons = {}
    for node in arch.iter("button"):
        if node.get("type") == "object" and node.get("name"):
            # Buttons of the lines of a record belong to the lines
            if not any(parent.tag == "field" for parent in node.iterancestors()):
                buttons.setdefault(node.get("name"), []).append(node)
    return buttons


def shown_button(nodes, record):
    """:return: the button that is there for ``record``, or None"""
    return next((node for node in nodes if not is_hidden(node, record)), None)


def button_label(node):
    return " ".join((node.get("string") or node.get("title") or node.text or "").split())


def record_values(record, names):
    """:return: the values of ``record`` as the expressions of views read them"""
    values = {}
    for name in names:
        field = record._fields.get(name)
        if field is None:
            continue
        value = record[name]
        if field.type == "many2one":
            value = value.id
        elif field.type in ("one2many", "many2many"):
            value = value.ids
        elif field.type == "date":
            value = fields.Date.to_string(value)
        elif field.type == "datetime":
            value = fields.Datetime.to_string(value)
        values[name] = value
    return values


def evaluate(expression, record):
    """:return: what an expression of a view is worth for ``record``
    :raise Exception: when it cannot be told"""
    names = get_expression_field_names(expression)
    # Read as the person: a field they cannot read is one they cannot tell
    values = record_values(record, names | {"id"})
    values.update(context=dict(record.env.context), uid=record.env.uid, active_id=record.id)
    return safe_eval(expression, values)


def is_hidden(node, record):
    """:return: whether a button is not there for ``record``. What cannot be
    told is taken to be hidden."""
    for element in (node, *node.iterancestors()):
        expression = element.get("invisible")
        if not expression:
            continue
        try:
            if evaluate(expression, record):
                return True
        except Exception:  # noqa: BLE001
            return True
    return False


@base.register
class CallMethod(base.Action):
    name = "call_method"
    kind = "method"
    description = (
        "Press a button of the form of a record, as the person: confirm an order, validate a "
        "delivery, post an invoice, or a button of a custom module. Only buttons that the "
        "person has on the form of the record, in the state the record is in, can be pressed; "
        "the snapshot of their screen lists them, with their \"name\". Nothing is done by this "
        "call: the person is shown which button is about to be pressed on which record, and "
        "says yes or no. You read their answer as the result. A button may open a window for "
        "the person to fill: it is opened on their screen, and they go on from there."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["model", "ids", "method"],
        "properties": {
            "model": {"type": "string", "pattern": MODEL_PATTERN},
            "ids": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_RECORDS,
                "items": {"type": "integer", "minimum": 1},
            },
            "method": {
                "type": "string",
                "pattern": r"[a-z][a-z0-9_]{1,80}",
                "description": "The \"name\" of the button, such as \"action_confirm\".",
            },
        },
    }

    def find_button(self, model, records, method):
        """:return: the button that runs ``method``, which every one of
        ``records`` has in the state it is in
        :raise Refused: when there is none"""
        buttons = form_buttons(model)
        nodes = buttons.get(method)
        if not nodes:
            listed = []
            for name, candidates in sorted(buttons.items()):
                button = shown_button(candidates, records[:1])
                if button is not None:
                    listed.append(f"{name} ({button_label(button)})")
            raise Refused(
                f"The form of {model._name} has no button {method!r} for this person. "
                f"Buttons of this record: {', '.join(listed)[:1500] or 'none'}.")
        get_public_method(model, method)
        found = None
        for record in records:
            found = shown_button(nodes, record)
            if found is None:
                state = record.read(["state"])[0].get("state") if "state" in model._fields else None
                told = f" Its state is {state!r}." if state else ""
                raise Refused(
                    f"The button {button_label(nodes[0])!r} is not there for "
                    f"{record.display_name!r}, in the state the record is in.{told}")
        return found

    @refusing
    def run(self, env, arguments, screen):
        model = get_model(env, arguments["model"], "read", to_change=True)
        records = get_records(model, arguments["ids"], "read")
        check_screen(screen, model, records)
        method = arguments["method"]
        node = self.find_button(model, records, method)
        label = button_label(node) or method
        # The card reads what is about to be done, from the view and the
        # records, never from what the model said of it
        lines = [
            {"label": env._("Button"), "value": label},
            {"label": env._("Method"), "value": method},
            {"label": env._("Records"), "value": env._(
                "%(count)s × %(model)s", count=len(records), model=model_label(model))},
        ]
        return base.ToolResult(
            "The person is being shown which button is about to be pressed.",
            pending={
                "summary": env._("Press “%(button)s”: %(model)s", button=label, model=model_label(model)),
                "res_model": model._name,
                "res_ids": records.ids,
                "record_names": ", ".join(records[:MAX_SHOWN_RECORDS].mapped("display_name")),
                "method": method,
                "is_sensitive": is_sensitive(model._name, method),
                "preview": {"records": name_records(records), "lines": lines},
            },
        )

    def execute(self, env, step):
        # Imported here: the module of the web client is loaded after this one is read
        from odoo.addons.web.controllers.utils import clean_action  # noqa: PLC0415

        model = get_model(env, step.res_model, "read", to_change=True)
        records = get_records(model, step.res_ids, "read")
        # The record may have changed since the person was asked
        node = self.find_button(model, records, step.method)
        # What the web client sends with the click: the context of the person,
        # and the one the button adds to it
        context = dict(env.context)
        if node.get("context"):
            try:
                context.update(evaluate(node.get("context"), records[:1]))
            except Exception:  # noqa: BLE001, S110
                pass
        result = call_kw(model, step.method, [records.ids], {"context": context})
        names = ", ".join(repr(name) for name in records[:MAX_SHOWN_RECORDS].mapped("display_name"))
        told = f"Pressed {button_label(node)!r} on {model._description} {names}."
        event = {"type": "changed", "model": model._name, "ids": records.ids}
        if isinstance(result, dict) and result.get("type"):
            action = clean_action(result, env=env)
            event["action"] = action
            told += (
                f" It opened a window on the screen of the person: {action.get('name') or ''}. "
                "They go on from there; you do not see it.")
        elif "state" in model._fields:
            records.invalidate_recordset()
            states = describe(env, records.exists()[:MAX_SHOWN_RECORDS], ["state"])
            told += f" State now: {base.dump(states)}"
        return base.ToolResult(
            told, event=event, records=(model._name, records.ids, records.mapped("display_name")))

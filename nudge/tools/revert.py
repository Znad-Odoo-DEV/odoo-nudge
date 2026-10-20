"""Who changed this, and put it back. No AI is needed to list or to restore.

Odoo keeps the old and the new value of the tracked fields of a record in ``mail.tracking.value``, one
row for each field of each change, under a message of the chatter. Nothing else is recorded: a field that
is not tracked cannot be restored, and the list says so.

* The changes are listed as the person: the messages they may read, and of the values only those of fields
  they may read. The tracking model is for administrators, so it is read with ``sudo`` and filtered by the
  rights of the person on each field before anything is shown.
* A restore is a normal write by the person, after a card that shows exactly what is written: rights,
  record rules and constraints apply. It is logged, and Nudge's own undo can take it back.
* It never touches a state or a stage, a posted journal entry, the lines of a confirmed order or a done
  stock move, and says why.
"""
import re
from datetime import date, datetime

from odoo import fields
from odoo.exceptions import AccessError, MissingError

from . import base
from .actions import check_screen, name_records, writable_field
from .plans import snapshot
from .records import MODEL_PATTERN, Refused, cut, get_model, get_records, is_secret

MAX_CHANGES = 40
MAX_MESSAGES = 60
# The fields that say where a record is in its life: never put back by a click
STATE_FIELDS = frozenset({
    "state", "stage_id", "kanban_state", "status", "invoice_status", "payment_state", "delivery_status",
    "locked", "is_locked", "team_id_stage",
})
# Lines whose order, when confirmed, is no longer theirs to change: the order states that still allow it
ORDER_LINES = {
    "sale.order.line": ("order_id", ("draft", "sent")),
    "purchase.order.line": ("order_id", ("draft", "sent", "to approve")),
}
TEXT_TYPES = ("char", "text")


def is_state_field(field):
    """:return: whether the field says where the record is in its life: a state or a stage"""
    return (
        field.name in STATE_FIELDS
        or field.name.endswith(("_state", "_stage_id"))
        or (field.type == "many2one" and (field.comodel_name or "").endswith((".stage", ".stage.type")))
    )


def locked_reason(env, record):
    """:return: why nothing of ``record`` may be put back (a sentence), or "" when it may"""
    name = record._name
    if name in ("account.move", "account.payment") and "state" in record._fields and record.state != "draft":
        return env._("A posted entry is never changed by Nudge: reverse it in Accounting.")
    if name == "account.move.line" and record.move_id.state != "draft":
        return env._("A posted entry is never changed by Nudge: reverse it in Accounting.")
    if name in ORDER_LINES:
        link, open_states = ORDER_LINES[name]
        if record[link].state not in open_states:
            return env._("The lines of a confirmed order are never changed by Nudge.")
    if (name.startswith("stock.move") or name == "stock.picking") and "state" in record._fields \
            and record.state in ("done", "cancel"):
        return env._("A done stock move is never changed by Nudge: make a return or an adjustment.")
    return ""


def _stamp(value):
    if isinstance(value, datetime):
        return fields.Datetime.to_string(value)
    if isinstance(value, date):
        return fields.Date.to_string(value)
    return value


def _side(tracking, field, which):
    """:return: the old or new value of one tracking row, as it is shown"""
    kind = field.type
    if kind in TEXT_TYPES:
        return getattr(tracking, f"{which}_value_{'text' if kind == 'text' else 'char'}") or ""
    if kind in ("integer",):
        return str(getattr(tracking, f"{which}_value_integer"))
    if kind in ("float", "monetary"):
        return str(getattr(tracking, f"{which}_value_float"))
    if kind == "boolean":
        return "Yes" if getattr(tracking, f"{which}_value_integer") else "No"
    if kind == "date":
        stamp = getattr(tracking, f"{which}_value_datetime")
        return fields.Date.to_string(stamp.date()) if stamp else ""
    if kind == "datetime":
        return _stamp(getattr(tracking, f"{which}_value_datetime")) or ""
    return getattr(tracking, f"{which}_value_char") or ""


def _shown(env, field, tracking, which):
    """:return: the old or new value of a tracking row for the person. The name of a record they may not
    open stays unsaid: the label was written when the change was made, by somebody who could read it."""
    text = _side(tracking, field, which) or "-"
    if field.type == "many2one":
        target = getattr(tracking, f"{which}_value_integer")
        Target = env[field.comodel_name].with_context(active_test=False)
        if target and Target.sudo().browse(target).exists() and not Target.search_count([("id", "=", target)]):
            return env._("(a record you may not open)")
    return text


def old_value(env, field, tracking):
    """:return: ``(value, reason)``: what to write to put the field back as it was, JSON-ready; the reason
    is a sentence when the field cannot be put back"""
    kind = field.type
    if kind in TEXT_TYPES:
        text = tracking.old_value_text if kind == "text" else tracking.old_value_char
        return (text or False), ""
    if kind == "integer":
        return tracking.old_value_integer, ""
    if kind in ("float", "monetary"):
        return tracking.old_value_float, ""
    if kind == "boolean":
        return bool(tracking.old_value_integer), ""
    if kind in ("date", "datetime"):
        stamp = tracking.old_value_datetime
        if not stamp:
            return False, ""
        return (fields.Date.to_string(stamp.date()) if kind == "date" else fields.Datetime.to_string(stamp)), ""
    if kind == "selection":
        label = tracking.old_value_char
        if not label:
            return False, ""
        choices = field._description_selection(env)
        keys = {name for name, _label in choices}
        for name, shown in choices:
            if shown == label:
                return name, ""
        if label in keys:
            return label, ""
        # The label was stored in the language of whoever changed it: tried in English too
        english = dict(field._description_selection(env(context=dict(env.context, lang="en_US"))))
        for name, shown in english.items():
            if shown == label:
                return name, ""
        return None, env._("The old value is not one of the choices of the field any more.")
    if kind == "many2one":
        target = tracking.old_value_integer
        if not target:
            return False, ""
        if not env[field.comodel_name].browse(target).exists():
            return None, env._("The old value, %s, no longer exists.", tracking.old_value_char or target)
        return target, ""
    return None, env._("Only the names were kept for this kind of field: it cannot be restored.")


def changed_fields(env, record):
    """:return: the tracked fields of ``record``, by name"""
    try:
        return set(record._track_get_fields())
    except Exception:  # noqa: BLE001 - a model without tracking
        return set()


def find_changes(env, record, field_name=None, message_id=None, since=None, until=None, limit=MAX_CHANGES):
    """The changes of ``record`` that the person may see, newest first.

    :return: ``[{"change_id", "message_id", "date", "who", "field", "label", "old", "new"}]``, one for each
        field of each change; the values of a field the person may not read are not there at all"""
    model = record
    domain = [("model", "=", record._name), ("res_id", "=", record.id), ("message_type", "=", "notification")]
    if message_id:
        domain.append(("id", "=", int(message_id)))
    if since:
        domain.append(("date", ">=", since))
    if until:
        domain.append(("date", "<=", until))
    # As the person: the record rules of the messages apply
    messages = env["mail.message"].search(domain, order="date desc, id desc", limit=MAX_MESSAGES)
    if not messages:
        return []
    # sudo: mail.tracking.value is for administrators; every row is filtered by the person's right to read
    # its field below, so nothing they may not read is passed on
    rows = env["mail.tracking.value"].sudo().search([("mail_message_id", "in", messages.ids)])
    by_message = {}
    for tracking in rows:
        by_message.setdefault(tracking.mail_message_id.id, []).append(tracking)
    out = []
    for message in messages:
        for tracking in sorted(by_message.get(message.id, []), key=lambda row: row.id):
            if not tracking.field_id or tracking.field_id.model != record._name:
                continue
            field = model._fields.get(tracking.field_id.name)
            if field is None or not model._has_field_access(field, "read") or is_secret(field):
                continue
            if field_name and field.name != field_name:
                continue
            out.append({
                "change_id": tracking.id,
                "message_id": message.id,
                "date": fields.Datetime.to_string(message.date),
                "who": message.author_id.display_name or message.create_uid.display_name or "",
                "field": field.name,
                "label": field.get_description(env, ["string"])["string"],
                "old": cut(_shown(env, field, tracking, "old"), 200),
                "new": cut(_shown(env, field, tracking, "new"), 200),
            })
            if len(out) >= limit:
                return out
    return out


def judge(env, record, change):
    """Whether a change can be put back, as the person.

    :param change: a row of :func:`find_changes`
    :return: ``(value, reason)``: what to write, or why it cannot be (a sentence)"""
    field = record._fields[change["field"]]
    if is_state_field(field):
        return None, env._("A state or a stage is never put back by a click: use the buttons of the record.")
    reason = locked_reason(env, record)
    if reason:
        return None, reason
    try:
        writable_field(record, field.name)
    except Refused:
        return None, env._("You may not change this field.")
    tracking = env["mail.tracking.value"].sudo().browse(change["change_id"])
    return old_value(env, field, tracking)


def still_new(env, record, change):
    """Whether the field holds, now, what that change wrote: if somebody changed it since, putting the old
    value back would undo their work too.

    :param change: a row of :func:`find_changes`"""
    field = record._fields[change["field"]]
    tracking = env["mail.tracking.value"].sudo().browse(change["change_id"])
    value = record[field.name]
    kind = field.type
    if kind == "many2one":
        return (value.id or 0) == (tracking.new_value_integer or 0)
    if kind == "selection":
        label = tracking.new_value_char or ""
        if not value:
            return not label
        english = dict(field._description_selection(env(context=dict(env.context, lang="en_US"))))
        return label in {value, dict(field._description_selection(env)).get(value), english.get(value)}
    if kind in TEXT_TYPES:
        return (value or "").strip() == (_side(tracking, field, "new") or "").strip()
    if kind == "integer":
        return (value or 0) == (tracking.new_value_integer or 0)
    if kind in ("float", "monetary"):
        return abs((value or 0.0) - (tracking.new_value_float or 0.0)) < 1e-6
    if kind == "boolean":
        return bool(value) == bool(tracking.new_value_integer)
    if kind in ("date", "datetime"):
        return (_stamp(value) or "") == (_side(tracking, field, "new") or "")
    return True


def decorate(env, record, changes):
    """The rows of ``changes`` with what can be done about each: ``restorable`` and ``reason``"""
    can_write = record.has_access("write")
    out = []
    for change in changes:
        if not can_write:
            value, reason = None, env._("You may not change this record.")
        else:
            value, reason = judge(env, record, change)
        out.append(dict(change, restorable=not reason, reason=reason))
    return out


def current_text(env, record, name):
    """:return: the value of the field now, as it is shown"""
    field = record._fields[name]
    value = record[name]
    if field.type == "many2one":
        return value.display_name or "-"
    if field.type == "selection":
        return dict(field._description_selection(env)).get(value) or "-"
    if field.type == "boolean":
        return "Yes" if value else "No"
    if field.type in ("date", "datetime"):
        return _stamp(value) or "-"
    return cut(value, 200) if value not in (False, None, "") else "-"


def target_of(env, kind, number):
    """:return: ``(model, id)`` of the record a change or a message of the chatter is about, as the person"""
    number = int(number)
    if kind == "change":
        row = env["mail.tracking.value"].sudo().browse(number).exists()
        number = row.mail_message_id.id
    message = env["mail.message"].search([("id", "=", number)])
    if not message or not message.model or not message.res_id:
        return None, None
    return message.model, message.res_id


# ----------------------------------------------------------------------
# Tools
# ----------------------------------------------------------------------

def _record_of(env, arguments, screen, operation="read"):
    model_name = arguments.get("model")
    record_id = arguments.get("id")
    if not model_name or not record_id:
        shown = (screen or {}).get("record") if isinstance((screen or {}).get("record"), dict) else {}
        model_name = model_name or (screen or {}).get("model")
        record_id = record_id or shown.get("id")
    if not model_name or not record_id:
        raise Refused("Give the model and the id of the record, or open it on the screen first.")
    model = get_model(env, model_name, "read")
    return model, get_records(model, [int(record_id)], "read")


def _refusing(function):
    def run(self, env, arguments, screen):
        try:
            return function(self, env, arguments, screen)
        except Refused as refusal:
            return base.ToolResult(str(refusal), is_error=True)
        except (AccessError, MissingError) as error:
            return base.ToolResult(str(error), is_error=True)
    return run


@base.register
class RecordChanges(base.Tool):
    name = "record_changes"
    kind = "read"
    description = (
        "List who changed a record and what they changed, newest first: date, who, field, old value "
        "-> new value. Only the fields Odoo tracks are in the list, and only those the person may "
        "read. The person sees the list with a button to restore each old value. Use it when they ask "
        "who changed something, when, or from what; give field (its technical name) to look at one "
        "field, message_id for one change. Nothing is changed by this call."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "model": {"type": "string", "pattern": MODEL_PATTERN},
            "id": {"type": "integer", "minimum": 1},
            "field": {"type": "string", "maxLength": 80},
            "message_id": {"type": "integer", "minimum": 1},
            "since": {"type": "string", "maxLength": 20, "description": "YYYY-MM-DD, the changes from this day on."},
            "until": {"type": "string", "maxLength": 20, "description": "YYYY-MM-DD, the changes up to this day."},
        },
    }

    @_refusing
    def run(self, env, arguments, screen):
        model, record = _record_of(env, arguments, screen)
        name = (arguments.get("field") or "").strip() or None
        if name and name not in model._fields:
            raise Refused(f"{model._name} has no field {name!r}. describe_model lists the fields.")
        changes = find_changes(
            env, record, name, arguments.get("message_id"), _day(arguments.get("since")),
            _day(arguments.get("until"), end=True))
        items = decorate(env, record, changes)
        lines = []
        if name and name not in changed_fields(env, record):
            field = model._fields[name]
            label = field.get_description(env, ["string"])["string"]
            lines.append(env._("“%s” is not tracked by Odoo: nothing was recorded about it, so it cannot be restored.",
                               label))
        if not items and not lines:
            lines.append(env._("Nobody changed a tracked field of this record."))
        for item in items:
            can = env._("can be restored") if item["restorable"] else item["reason"]
            lines.append(f"{item['date']} | {item['who']} | {item['label']}: {item['old']} -> {item['new']} | "
                         f"change_id={item['change_id']} message_id={item['message_id']} | {can}")
        if items:
            lines.append(env._("Only tracked fields can be restored."))
        event = {
            "type": "changes", "model": model._name, "id": record.id, "name": cut(record.display_name, 120),
            "items": items, "note": lines[0] if not items else "",
            "can_write": record.has_access("write"),
        }
        return base.ToolResult(
            "\n".join(lines), event=event, records=(model._name, record.ids, [record.display_name]))


def _day(text, end=False):
    if not text:
        return None
    try:
        day = fields.Date.to_date(str(text)[:10])
    except (ValueError, TypeError):
        return None
    if not day:
        return None
    return f"{day} 23:59:59" if end else f"{day} 00:00:00"


@base.register
class PrepareRevert(base.Action):
    name = "prepare_revert"
    kind = "write"
    description = (
        "Put a tracked field of a record back as it was before a change, as the person. Give the "
        "model and id and ONE of: change_id (one field of one change, from record_changes), "
        "message_id (all the tracked fields that change touched), or field (the technical name: its "
        "latest change is undone). Nothing is changed by this call: the person is shown what is "
        "written now and what it will be, and says yes or no. A state, a stage, a posted entry, the "
        "lines of a confirmed order and a done stock move are never restored, and the answer says so."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["model", "id"],
        "properties": {
            "model": {"type": "string", "pattern": MODEL_PATTERN},
            "id": {"type": "integer", "minimum": 1},
            "change_id": {"type": "integer", "minimum": 1},
            "message_id": {"type": "integer", "minimum": 1},
            "field": {"type": "string", "maxLength": 80},
        },
    }

    def _pick(self, env, record, arguments):
        """:return: the rows of the changes asked for"""
        wanted = [key for key in ("change_id", "message_id", "field") if arguments.get(key)]
        if len(wanted) != 1:
            raise Refused("Give exactly one of change_id, message_id or field.")
        name = arguments.get("field")
        if name and name not in record._fields:
            raise Refused(f"{record._name} has no field {name!r}.")
        if name and name not in changed_fields(env, record):
            label = record._fields[name].get_description(env, ["string"])["string"]
            raise Refused(f"{label!r} is not tracked by Odoo: nothing was recorded about it, so it cannot "
                          "be restored. Say so to the person.")
        rows = find_changes(env, record, name, arguments.get("message_id"))
        if arguments.get("change_id"):
            rows = [row for row in find_changes(env, record) if row["change_id"] == arguments["change_id"]]
        elif name:
            rows = rows[:1]
        if not rows:
            raise Refused("There is no such change on this record that this person can see.")
        return rows

    def _plan(self, env, record, rows):
        """:return: ``(write, shown, kept)``: what is written, the lines of the card, the reasons for the rest"""
        write, shown, kept = {}, [], []
        for row in rows:
            value, reason = judge(env, record, row)
            if reason:
                kept.append((row["label"], reason))
                continue
            if row["field"] in write:
                continue
            if not still_new(env, record, row):
                kept.append((row["label"], env._("It was changed since: nothing is put back over the newer value.")))
                continue
            write[row["field"]] = value
            shown.append({"field": row["field"], "label": row["label"],
                          "before": current_text(env, record, row["field"]), "value": row["old"]})
        return write, shown, kept

    @_refusing
    def run(self, env, arguments, screen):
        model = get_model(env, arguments["model"], "write", to_change=True)
        records = get_records(model, [arguments["id"]], "write")
        check_screen(screen, model, records)
        rows = self._pick(env, records, arguments)
        write, shown, kept = self._plan(env, records, rows)
        if not write:
            raise Refused("Nothing can be restored: " + "; ".join(f"{label}: {why}" for label, why in kept))
        lines = list(shown)
        for label, why in kept:
            lines.append({"label": label, "value": env._("Stays as it is: %s", why)})
        return base.ToolResult(
            "The person is being shown what is about to be restored.",
            pending={
                "summary": env._("Restore the old value: %s", model._description),
                "res_model": model._name,
                "res_ids": records.ids,
                "record_names": cut(records.display_name, 200),
                "values": {"restore": write, "tracking_ids": [row["change_id"] for row in rows]},
                "is_sensitive": False,
                "preview": {"records": name_records(records), "lines": lines},
            },
        )

    def execute(self, env, step):
        model = get_model(env, step.res_model, "write", to_change=True)
        records = get_records(model, step.res_ids, "write")
        # The rights and the state of the record are asked again: they may have changed since the card
        wanted = set((step.values or {}).get("tracking_ids") or [])
        rows = [row for row in find_changes(env, records) if row["change_id"] in wanted]
        write, _shown, kept = self._plan(env, records, rows)
        if not write:
            raise Refused("Nothing can be restored any more: " + "; ".join(f"{label}: {why}" for label, why in kept))
        names = list(write)
        before = {str(record.id): snapshot(record, names) for record in records}
        records.write(write)
        step.values = dict(step.values, undo={model._name: {
            str(record.id): {"before": before[str(record.id)], "after": snapshot(record, names)}
            for record in records
        }})
        told = "; ".join(f"{record._fields[name].string}" for name in names for record in records[:1])
        text = f"Restored the old value of {told} on {model._description} {records[0].display_name!r}."
        if kept:
            text += " Left as it is: " + "; ".join(f"{label} ({why})" for label, why in kept)
        return base.ToolResult(
            text,
            event={"type": "changed", "model": model._name, "ids": records.ids},
            records=(model._name, records.ids, records.mapped("display_name")),
        )


RESTORE_COMMAND = re.compile(r"^/(?:restore|استرجاع)\s+(change|message)\s+(\d{1,12})\s*$", re.I)
CHANGES_COMMAND = re.compile(r"^/(?:changes|تغييرات)\s*$", re.I)

"""Several changes, many records, one yes.

A plan is what the person asks for at once: the same change or the same
button on many records ("confirm the draft quotations of Acme"), or a few
steps on the same records ("set the delivery date, then confirm"). It is
tried first, in a savepoint undone at the end: the card says how many records
will go through and why the others will not. The person says yes once, to
all of them or to those that go through. Each record is then done in a
savepoint of its own: one that fails does not stop the others. A plan on many
records is worked on in the background, by pieces.

What a plan wrote can be undone, record by record, unless someone changed
the record since. What a button did cannot: the card says so before the yes.
"""
import datetime
import time

from odoo import _, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from . import base
# Taken from the actions: how a button is called differs between versions of Odoo
from .actions import (
    VALUES_SCHEMA,
    button_label,
    call_kw,
    evaluate,
    form_buttons,
    get_public_method,
    is_sensitive,
    name_records,
    read_values,
    shown_button,
    to_write,
)
from .records import (
    CONDITION_SCHEMA_ITEMS,
    MAX_CONDITIONS,
    MODEL_PATTERN,
    Refused,
    build_domain,
    cut,
    get_model,
    get_records,
    refusing,
)

PLAN_PARAM = "nudge.plan_max_records"
DEFAULT_MAX_RECORDS = 200
# Whatever the administrator sets: past it, a plan is a migration
HARD_MAX_RECORDS = 1000
MAX_STEPS = 5
# Up to this many records, a plan is done at once; beyond, in the background
INLINE_RECORDS = 20
# Records done between two saves of the progress, in the background
CHUNK = 25
MAX_FAILURES_SHOWN = 10
MAX_REASON_CHARS = 200


def now():
    """The clock a piece of work is measured with"""
    return time.monotonic()


def max_records(env):
    """:return: how many records a plan may change, as the administrator set it"""
    raw = env["ir.config_parameter"].sudo().get_param(PLAN_PARAM)
    try:
        value = int(raw) if raw else DEFAULT_MAX_RECORDS
    except ValueError:
        value = DEFAULT_MAX_RECORDS
    return max(1, min(value, HARD_MAX_RECORDS))


def key_of(model_name, record_id):
    return f"{model_name},{record_id}"


def reason_of(error):
    """:return: why a record did not go through, in the words of Odoo"""
    if isinstance(error, (UserError, ValidationError, Refused)):
        text = str(error.args[0] if error.args else error)
    elif isinstance(error, AccessError):
        text = "This person may not do it on this record."
    else:
        text = f"{type(error).__name__}: {error}"
    return cut(" ".join(text.split()), MAX_REASON_CHARS)


def snapshot(record, names):
    """:return: the values of ``names`` on ``record``, as they would be
    written back, in JSON"""
    values = {}
    for name in names:
        field = record._fields[name]
        value = field.convert_to_write(record[name], record)
        if isinstance(value, datetime.datetime):
            value = fields.Datetime.to_string(value)
        elif isinstance(value, datetime.date):
            value = fields.Date.to_string(value)
        elif isinstance(value, (list, tuple)):
            value = [list(command) if isinstance(command, (list, tuple)) else command for command in value]
        values[name] = value
    return values


def total_of(plan):
    return len({key_of(step["model"], record_id) for step in plan for record_id in step["ids"]})


class Rehearsed(Exception):
    """Leaves the savepoint of a rehearsal once it is over."""


@base.register
class PreparePlan(base.Action):
    name = "prepare_plan"
    kind = "write"
    description = (
        "Change many records at once, or do a few steps on the same records, with one yes of "
        "the person: the same fields written on many records, the same button pressed on many "
        "records (\"confirm all the draft quotations of Acme\"), or steps in order (\"set the "
        "delivery date, then confirm\"). Give the records by their ids, or by conditions when "
        "there are more than search_records lists. Nothing is done by this call: the plan is "
        "tried, then undone, and the person is shown how many records will go through and why "
        "the others will not; they say yes to all, yes to those that go through, or no. You "
        "read the outcome as the result. For one record and one change, use prepare_write or "
        "call_method."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "steps"],
        "properties": {
            "title": {
                "type": "string",
                "minLength": 3,
                "maxLength": 160,
                "description": "What the plan does, in the language of the person.",
            },
            "steps": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_STEPS,
                "description": "Done in this order, each on every record before the next.",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["action", "model"],
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["write", "press"],
                            "description": "write: give values. press: give the method of a button.",
                        },
                        "model": {"type": "string", "pattern": MODEL_PATTERN},
                        "ids": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": HARD_MAX_RECORDS,
                            "items": {"type": "integer", "minimum": 1},
                        },
                        "conditions": {
                            "type": "array",
                            "maxItems": MAX_CONDITIONS,
                            "items": CONDITION_SCHEMA_ITEMS,
                            "description": "Instead of ids: the records that match, as search_records reads them.",
                        },
                        "match": {"type": "string", "enum": ["all", "any"]},
                        "values": dict(VALUES_SCHEMA, minItems=0),
                        "method": {
                            "type": "string",
                            "pattern": r"[a-z][a-z0-9_]{1,80}",
                            "description": "The \"name\" of the button, such as \"action_confirm\".",
                        },
                    },
                },
            },
        },
    }

    @staticmethod
    def in_background(values):
        """Whether the yes sends the plan to the background"""
        return total_of(values["plan"]) > INLINE_RECORDS

    # ------------------------------------------------------------------
    # Asked for: read, tried, shown
    # ------------------------------------------------------------------
    def select(self, model, step, limit, operation):
        """:return: the records of a step, which this person may use"""
        if step.get("ids"):
            records = get_records(model, step["ids"], operation)
        elif step.get("conditions"):
            domain = build_domain(model, step["conditions"], step.get("match"))
            records = model.search(domain, limit=limit + 1)
            if len(records) > limit:
                count = model.search_count(domain)
                raise Refused(
                    f"{count} records match: more than the {limit} a plan may change here, as the "
                    "administrator set it. Narrow the conditions, or do it in parts.")
            records = get_records(model, records.ids, operation)
        else:
            raise Refused("Say which records: their ids, or conditions.")
        if not records:
            raise Refused("No record matches these conditions for this person.")
        if len(records) > limit:
            raise Refused(
                f"{len(records)} records: more than the {limit} a plan may change here, as the "
                "administrator set it. Do it in parts.")
        return records

    def read_step(self, env, index, step, limit):
        """:return: the step as it is done, with the records it is done on"""
        if step["action"] == "write":
            model = get_model(env, step["model"], "write", to_change=True)
            records = self.select(model, step, limit, "write")
            values, shown = read_values(model, step.get("values") or [])
            if not values:
                raise Refused(f"Step {index}: nothing to write. Give values.")
            label = _("Change %(fields)s on %(count)s × %(model)s", fields="; ".join(
                f"{line['label']}: {line['value']}" for line in shown), count=len(records),
                model=model._description)
            return {"action": "write", "model": model._name, "ids": records.ids,
                    "values": values, "label": label}, records
        model = get_model(env, step["model"], "read", to_change=True)
        records = self.select(model, step, limit, "read")
        method = step.get("method")
        if not method:
            raise Refused(f"Step {index}: give the method of the button to press.")
        nodes = form_buttons(model).get(method)
        if not nodes:
            raise Refused(f"Step {index}: the form of {model._name} has no button {method!r} for this person.")
        get_public_method(model, method)
        label = _("Press “%(button)s” on %(count)s × %(model)s", button=button_label(nodes[0]) or method,
                      count=len(records), model=model._description)
        return {"action": "press", "model": model._name, "ids": records.ids, "method": method,
                "label": label}, records

    def rehearse(self, env, plan):
        """Do the plan, then undo it.
        :return: ``{key: reason}`` of the records that would not go through"""
        failing = {}
        try:
            with env.cr.savepoint():
                for step in plan:
                    model = env[step["model"]]
                    nodes = form_buttons(model).get(step["method"]) if step["action"] == "press" else None
                    for record in model.browse(step["ids"]):
                        key = key_of(model._name, record.id)
                        if key in failing:
                            continue
                        try:
                            with env.cr.savepoint():
                                if step["action"] == "write":
                                    record.write(to_write(step["values"], None))
                                # A button is not pressed in a rehearsal: what it does may
                                # reach beyond the database. It must be there, in the state
                                # the record is in once the steps before are done.
                                elif shown_button(nodes, record) is None:
                                    raise Refused(_("The button is not there in the state this record is in."))
                        except Exception as error:  # noqa: BLE001
                            failing[key] = reason_of(error)
                raise Rehearsed()
        except Rehearsed:
            pass
        env.invalidate_all()
        return failing

    @refusing
    def run(self, env, arguments, screen):
        limit = max_records(env)
        plan, first = [], None
        for index, step in enumerate(arguments["steps"], start=1):
            read, records = self.read_step(env, index, step, limit)
            plan.append(read)
            first = first or records
        total = total_of(plan)
        if total > limit:
            raise Refused(
                f"{total} records in all: more than the {limit} a plan may change here. Do it in parts.")
        failing = self.rehearse(env, plan)
        names = dict(self.names_of(env, failing))
        shown_failing = [{"name": names.get(key, key), "reason": reason}
                         for key, reason in list(failing.items())[:MAX_FAILURES_SHOWN]]
        presses = [step for step in plan if step["action"] == "press"]
        sensitive = any(is_sensitive(step["model"], step.get("method")) for step in plan) or any(
            step["values"].get("active") is False for step in plan if step["action"] == "write")
        told = f"The person is being shown the plan: {total} records, {total - len(failing)} would go through."
        if failing:
            told += " These would not: " + "; ".join(
                f"{entry['name']}: {entry['reason']}" for entry in shown_failing)
        return base.ToolResult(
            told,
            pending={
                "summary": arguments["title"],
                "res_model": plan[0]["model"],
                "res_ids": plan[0]["ids"][:50],
                "record_names": ", ".join(first[:10].mapped("display_name"))[:500],
                "values": {"plan": plan, "failing": failing},
                "is_sensitive": bool(sensitive or presses),
                "preview": {
                    "records": name_records(first),
                    "lines": [{"label": f"{index}.", "value": step["label"]}
                              for index, step in enumerate(plan, start=1)],
                    "plan": {
                        "total": total,
                        "passing": total - len(failing),
                        "failing": shown_failing,
                        "more_failing": max(0, len(failing) - len(shown_failing)),
                        # What a button did is not taken back by Undo
                        "undoable": not presses,
                        "background": total > INLINE_RECORDS,
                    },
                },
            },
        )

    @staticmethod
    def names_of(env, keys):
        for key in keys:
            model_name, record_id = key.split(",")
            try:
                yield key, env[model_name].browse(int(record_id)).display_name
            except Exception:  # noqa: BLE001
                yield key, key

    # ------------------------------------------------------------------
    # Said yes to: done, record by record
    # ------------------------------------------------------------------
    def execute(self, env, step, deadline=None, save=None, stop=None):
        """Do the plan of ``step``, from where it was left.

        :param deadline: :func:`now` past which the work stops, to
            go on in the next piece; None does it all
        :param save: called with the progress every few records, in the
            background, where it is committed
        :param stop: called after each save; true when the person stopped the
            request, and the work is left where it is
        :return: :class:`ToolResult`, or None when there is more to do
        """
        values = dict(step.values or {})
        plan = values["plan"]
        skipped = set(values.get("failing") or {}) if values.get("choice") == "passing" else set()
        progress = dict(values.get("progress") or {"step": 0, "offset": 0, "done": 0, "failed": {}, "undo": {}})
        failed, undo = progress["failed"], progress["undo"]
        windows = progress.get("windows", 0)
        counted = 0

        def keep():
            progress["windows"] = windows
            step.values = dict(values, progress=progress)

        for index in range(progress["step"], len(plan)):
            planned = plan[index]
            writing = planned["action"] == "write"
            model = get_model(env, planned["model"], "write" if writing else "read", to_change=True)
            nodes = None if writing else form_buttons(model).get(planned["method"])
            ids = planned["ids"]
            while progress["offset"] < len(ids):
                if deadline is not None and now() > deadline:
                    keep()
                    return None
                record_id = ids[progress["offset"]]
                progress["offset"] += 1
                key = key_of(model._name, record_id)
                if key in failed or key in skipped:
                    continue
                record = model.browse(record_id).exists()
                try:
                    if not record:
                        raise Refused(_("This record no longer exists."))
                    with env.cr.savepoint():
                        if writing:
                            get_records(model, record.ids, "write")
                            names = list(planned["values"])
                            before = snapshot(record, names)
                            record.write(to_write(planned["values"], None))
                            entry = undo.setdefault(model._name, {}).setdefault(str(record.id), {"before": before})
                            entry["after"] = snapshot(record, names)
                        else:
                            windows += self.press(env, model, record, nodes, planned["method"])
                    progress["done"] += 1
                except Exception as error:  # noqa: BLE001
                    failed[key] = reason_of(error)
                counted += 1
                if save and counted % CHUNK == 0:
                    keep()
                    save()
                    if stop and stop():
                        return None
            progress["step"], progress["offset"] = index + 1, 0
        keep()
        return self.outcome(env, plan, progress, skipped, windows)

    def press(self, env, model, record, nodes, method):
        """Press the button on one record, as the person would.
        :return: 1 when it opened a window, which a plan does not show"""
        get_records(model, record.ids, "read")
        node = shown_button(nodes or [], record)
        if node is None:
            raise Refused(_("The button is not there in the state this record is in."))
        context = dict(env.context)
        if node.get("context"):
            try:
                context.update(evaluate(node.get("context"), record))
            except Exception:  # noqa: BLE001, S110
                pass
        result = call_kw(model, method, [record.ids], {"context": context})
        return 1 if isinstance(result, dict) and result.get("type") else 0

    def outcome(self, env, plan, progress, skipped, windows):
        failed = progress["failed"]
        done_records = total_of(plan) - len(failed) - len(skipped)
        names = dict(self.names_of(env, list(failed)[:MAX_FAILURES_SHOWN]))
        told = f"Done: {done_records} records went through."
        if failed:
            told += f" {len(failed)} did not: " + "; ".join(
                f"{names.get(key, key)}: {reason}" for key, reason in list(failed.items())[:MAX_FAILURES_SHOWN])
        if skipped:
            told += f" {len(skipped)} were left out, as the person chose."
        if windows:
            told += (f" On {windows} records the button opened a window, which a plan does not "
                     "show: the person opens those records to go on.")
        first = plan[0]
        return base.ToolResult(
            told,
            is_error=not done_records,
            event={"type": "changed", "model": first["model"], "ids": first["ids"][:200]},
            records=(first["model"], first["ids"], []),
        )


# ----------------------------------------------------------------------
# Taken back
# ----------------------------------------------------------------------

def undo_changes(env, undo):
    """Write back what a change wrote, as the person, record by record;
    a record changed since is left as it is.

    :param undo: ``{model: {id: {"before", "after"}}}``
    :return: (records written back, ``[(name, why)]`` of those left)
    """
    restored, kept = 0, []
    for model_name, entries in undo.items():
        if any(entry.get("created") for entry in entries.values()):
            # Made from a document: taken back by removing the draft nobody changed
            from .document_record import undo_created  # noqa: PLC0415

            for record_id, entry in entries.items():
                removed, why = undo_created(env, model_name, record_id, entry)
                if removed:
                    restored += 1
                else:
                    kept.append((key_of(model_name, record_id), why))
            continue
        try:
            model = get_model(env, model_name, "write", to_change=True)
        except Refused as refusal:
            kept.extend((key_of(model_name, record_id), str(refusal)) for record_id in entries)
            continue
        for record_id, entry in entries.items():
            record = model.browse(int(record_id)).exists()
            if not record:
                kept.append((key_of(model_name, record_id), _("It no longer exists.")))
                continue
            if snapshot(record, list(entry["after"])) != entry["after"]:
                kept.append((record.display_name, _("It was changed since.")))
                continue
            try:
                with env.cr.savepoint():
                    get_records(model, record.ids, "write")
                    record.write(entry["before"])
                restored += 1
            except Exception as error:  # noqa: BLE001
                kept.append((record.display_name, reason_of(error)))
    return restored, kept

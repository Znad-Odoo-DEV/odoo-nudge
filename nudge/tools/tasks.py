"""Asking Nudge to do something again and again: a scheduled task.

The model describes the task; the person reads it on a card and says yes.
The task then runs as them, in the background, and only reads: what it finds
worth changing waits for them as a card.
"""
from odoo import _
from . import base
from .records import (
    CONDITION_SCHEMA_ITEMS,
    MAX_CONDITIONS,
    MODEL_PATTERN,
    Refused,
    build_domain,
    get_model,
    refusing,
)

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


@base.register
class PrepareTask(base.Action):
    name = "prepare_task"
    kind = "create"
    description = (
        "Make a task Nudge does by itself, again and again, as the person: on a schedule "
        "(\"every morning at 8, send me the overdue invoices\") or when records appear (\"when a "
        "purchase order above 50,000 is created, compare its prices with the last ones\"). Write "
        "the instruction as you will read it each time, complete and alone: it is all you will "
        "have. Give a model and conditions whenever the task is about records: they are counted "
        "first, and when none matches, or none is new, the AI is not asked and nothing is "
        "spent. A task only reads, and tells the person who asked: what it finds worth changing "
        "waits as a card for them. What Odoo must do for others on a schedule (remind the "
        "salespeople, write to customers, change records) is a scheduled action, built in "
        "Developer mode with prepare_scheduled_action. Nothing is made by this call: the person "
        "reads the task and says yes or no."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "instruction", "kind"],
        "properties": {
            "name": {"type": "string", "minLength": 3, "maxLength": 80,
                     "description": "Short, in the language of the person."},
            "instruction": {"type": "string", "minLength": 10, "maxLength": 2000},
            "kind": {"type": "string", "enum": ["schedule", "watch"],
                     "description": "schedule: at set times. watch: when records appear."},
            "every": {"type": "integer", "minimum": 1, "maximum": 31},
            "unit": {"type": "string", "enum": ["hours", "days", "weeks", "months"]},
            "hour": {"type": "integer", "minimum": 0, "maximum": 23,
                     "description": "The hour of the day, in the time of the person."},
            "weekday": {"type": "string", "enum": list(WEEKDAYS)},
            "model": {"type": "string", "pattern": MODEL_PATTERN},
            "conditions": {"type": "array", "maxItems": MAX_CONDITIONS, "items": CONDITION_SCHEMA_ITEMS},
            "match": {"type": "string", "enum": ["all", "any"]},
            "email": {"type": "boolean", "description": "Send the answer by email too."},
        },
    }

    def is_available(self, env, mode=base.MODE_TALK):
        return super().is_available(env, mode) and env["nudge.task"]._max_per_user() > 0

    @refusing
    def run(self, env, arguments, screen):
        Task = env["nudge.task"]
        mine = Task.with_context(active_test=False).search_count([("user_id", "=", env.uid)])
        limit = Task._max_per_user()
        if mine >= limit:
            raise Refused(
                f"The person has {mine} tasks: the limit set by the administrator is {limit}. "
                "They delete one first, from My Scheduled Tasks.")
        kind = arguments["kind"]
        values = {
            "name": arguments["name"].strip(),
            "instruction": arguments["instruction"].strip(),
            "kind": kind,
            "interval_number": arguments.get("every") or 1,
            "interval_type": arguments.get("unit") or "days",
            "hour": arguments.get("hour", 8),
            "weekday": str(WEEKDAYS.index(arguments["weekday"])) if arguments.get("weekday") else False,
            "send_email": bool(arguments.get("email")) and "mail.mail" in env,
        }
        labels = dict(Task._fields["interval_type"]._description_selection(env))
        lines = [
            {"label": _("Name"), "value": values["name"]},
            {"label": _("Each time"), "value": values["instruction"]},
        ]
        model_name = arguments.get("model")
        if kind == "watch" and not model_name:
            raise Refused("A task that watches records needs the model of those records.")
        if model_name:
            model = get_model(env, model_name)
            conditions = arguments.get("conditions") or []
            # Read now as the person: a condition they cannot use is refused here
            build_domain(model, conditions, arguments.get("match"))
            values.update(model_name=model._name, conditions=conditions, match=arguments.get("match") or "all")
            watched = model._description + (
                " — " + "; ".join(f"{c['field']} {c['operator']} {c['value']}" for c in conditions)
                if conditions else "")
            lines.append({
                "label": _("Watches") if kind == "watch" else _("Only when there is"),
                "value": watched,
            })
        if kind == "schedule":
            when = _("Every %(number)s %(unit)s", number=values["interval_number"],
                         unit=labels.get(values["interval_type"], values["interval_type"]))
            if values["interval_type"] != "hours":
                when += _(", at %s:00", values["hour"])
            if values["weekday"]:
                when += ", " + dict(Task._fields["weekday"]._description_selection(env))[values["weekday"]]
            lines.append({"label": _("When"), "value": when})
        else:
            lines.append({"label": _("When"), "value": _("Within 10 minutes of a new record")})
        lines.append({"label": _("Email"), "value": values["send_email"]})
        lines.append({"label": _("It may"), "value": _(
            "Read, as you. A change it finds needed waits for your yes.")})
        return base.ToolResult(
            "The person is being shown the task.",
            pending={
                "summary": _("Scheduled task: %s", values["name"]),
                "res_model": "nudge.task",
                "res_ids": [],
                "values": values,
                "is_sensitive": False,
                "preview": {"records": [], "lines": lines},
            },
        )

    def execute(self, env, step):
        task = env["nudge.task"].create(dict(step.values))
        return base.ToolResult(
            f"The task {task.name!r} is made, id {task.id}. Next run: {task.next_run} UTC. The "
            "person finds it in Nudge, Preferences, My Scheduled Tasks.",
            records=("nudge.task", task.ids, [task.name]),
        )

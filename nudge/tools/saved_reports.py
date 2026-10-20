"""Saving a report: a figure the person asked for, kept to open again.

The definition (model, conditions, period, groups, measures, shape) is read as
the person, shown on a card in words, and on their yes becomes a window action
with the groups and measures in its context, a menu under Nudge / My Reports
that only they see, and, if they want, a tile on their home page and a
spreadsheet. The action is plain Odoo: when it is opened, Odoo itself checks
the rights of whoever opens it. Nothing here calls an AI beyond the model that
filled the arguments, and the same path serves the no-AI sentence.
"""
import datetime
import json
from importlib import import_module

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tools.safe_eval import datetime as safe_datetime, safe_eval, time as safe_time

from . import base
from .analytics import GROUP_PATTERN, MAX_GROUPS, MEASURE_PATTERN, NUMERIC_TYPES, TooBig, grouped
from .records import (
    CONDITION_SCHEMA_ITEMS, MAX_CONDITIONS, MODEL_PATTERN, Refused, build_domain, get_fields, get_model, is_secret,
    model_label, refusing,
)

VIEWS = ("bar", "line", "pie", "pivot", "list")
PERIODS = ("this_month", "last_month", "this_year", "last_year", "last_7_days", "last_30_days")
MAX_MEASURES = 3
DATE_FIELDS = ("date_order", "invoice_date", "date", "create_date")
TODAY = 'context_today().strftime("%Y-%m-%d")'


# ----------------------------------------------------------------------
# Periods that move with the day: kept as the expression Odoo evaluates when the report is opened
# ----------------------------------------------------------------------
def _day(expression):
    return f'({expression}).strftime("%Y-%m-%d")'


PERIOD_BOUNDS = {
    "this_month": (_day("context_today() + relativedelta(day=1)"), _day("context_today() + relativedelta(months=1, day=1)")),
    "last_month": (_day("context_today() + relativedelta(months=-1, day=1)"), _day("context_today() + relativedelta(day=1)")),
    "this_year": (_day("context_today() + relativedelta(month=1, day=1)"),
                  _day("context_today() + relativedelta(years=1, month=1, day=1)")),
    "last_year": (_day("context_today() + relativedelta(years=-1, month=1, day=1)"),
                  _day("context_today() + relativedelta(month=1, day=1)")),
    "last_7_days": (_day("context_today() - relativedelta(days=6)"), _day("context_today() + relativedelta(days=1)")),
    "last_30_days": (_day("context_today() - relativedelta(days=29)"), _day("context_today() + relativedelta(days=1)")),
}


def period_terms(preset, field):
    """:return: the domain terms (as Python text) of a period on a date field"""
    start, end = PERIOD_BOUNDS[preset]
    return [f'("{field}", ">=", {start})', f'("{field}", "<", {end})']


def period_words(env, preset):
    return {
        "this_month": env._("this month"), "last_month": env._("last month"), "this_year": env._("this year"),
        "last_year": env._("last year"), "last_7_days": env._("the last 7 days"),
        "last_30_days": env._("the last 30 days"),
    }.get(preset, "")


def evaluate(env, text, today=None):
    """:return: the domain (or context) a text of an action means today, as Odoo evaluates it"""
    today = today or fields.Date.context_today(env["res.users"])
    return safe_eval(text or "[]", {
        "context_today": lambda: today, "relativedelta": relativedelta, "datetime": safe_datetime, "uid": env.uid,
        "time": safe_time,
    })


# ----------------------------------------------------------------------
# The definition: checked as the person, and written the way it is kept
# ----------------------------------------------------------------------
def _groups(model, specs):
    out = []
    for spec in specs or []:
        name, _separator, interval = spec.partition(":")
        field = get_fields(model, [name])[name]
        if is_secret(field):
            raise Refused(f"Nothing can be grouped by {name}.")
        if interval and field.type not in ("date", "datetime"):
            raise Refused(f"{name} is not a date: it cannot be grouped by {interval}.")
        if field.type in ("date", "datetime") and not interval:
            spec = f"{name}:month"
        out.append(spec)
    return out


def _measures(model, specs):
    out = []
    for spec in specs or ["count"]:
        if spec != "count":
            name, _separator, aggregate = spec.partition(":")
            field = get_fields(model, [name])[name]
            if is_secret(field):
                raise Refused(f"Nothing can be summed on {name}.")
            if field.type not in NUMERIC_TYPES:
                raise Refused(f"{name} is not a number: it cannot be {aggregate}med. Use count.")
        if spec not in out:
            out.append(spec)
    return out


def normalise(env, arguments):
    """Check what the person asked to keep, as them: the model they may read, fields that
    exist, a shape that fits. :raises Refused: when it cannot be kept.

    :return: ``(definition, model)``, the definition being plain data"""
    model = get_model(env, arguments["model"])
    conditions = list(arguments.get("conditions") or [])
    match = arguments.get("match") or "all"
    domain = build_domain(model, conditions, match)
    view = arguments.get("view") or "bar"
    if view not in VIEWS:
        raise Refused(f"A report is one of: {', '.join(VIEWS)}.")
    groups = _groups(model, arguments.get("group_by"))
    if len(groups) > MAX_GROUPS:
        raise Refused(f"At most {MAX_GROUPS} groups.")
    measures = _measures(model, arguments.get("measures"))
    if len(measures) > MAX_MEASURES:
        raise Refused(f"At most {MAX_MEASURES} measures.")
    if view != "list" and not groups:
        raise Refused("A chart or a pivot needs something to group by: the salesperson, the month, the customer.")
    if view in ("bar", "line", "pie") and len(groups) > 2:
        raise Refused("A chart takes one group, or two for several series.")
    if view == "pie" and (len(groups) > 1 or len(measures) > 1):
        view = "bar"
    period = arguments.get("period") or None
    period_field = None
    if period:
        if period not in PERIODS:
            raise Refused(f"A period is one of: {', '.join(PERIODS)}.")
        period_field = arguments.get("period_field") or next(
            (name for name in DATE_FIELDS if name in model._fields), None)
        if not period_field or period_field not in model._fields or model._fields[period_field].type not in (
                "date", "datetime"):
            raise Refused(f"{model._name} has no date to cut by {period}: name the date field.")
    definition = {
        "model": model._name,
        "conditions": conditions,
        "match": match,
        "period": period,
        "period_field": period_field,
        "group_by": groups,
        "measures": measures,
        "view": view,
        "name": (arguments.get("name") or "").strip()[:80],
    }
    # Read once, as the person: a field they cannot use, or a bad value, is refused here
    try:
        model.search_count(evaluate(env, domain_text(definition, domain)), limit=1)
    except Refused:
        raise
    except Exception as error:  # noqa: BLE001 - whatever Odoo says is the refusal
        raise Refused(str(error).strip().splitlines()[0][:200] if str(error).strip() else "It cannot be read.") from error
    return definition, model


def domain_text(definition, domain):
    """:return: the domain of the action: the conditions as they are, then the period as expressions"""
    static = domain.to_list() if hasattr(domain, "to_list") else list(domain)
    static = json.loads(json.dumps(static, default=str))
    terms = period_terms(definition["period"], definition["period_field"]) if definition.get("period") else []
    text = repr(static)
    if terms:
        text = (text[:-1] + ", " if static else "[") + ", ".join(terms) + "]"
    return text


def context_of(definition):
    """:return: the context of the action, with the groups and measures the views read"""
    groups, view = definition["group_by"], definition["view"]
    measures = [m.partition(":")[0] for m in definition["measures"] if m != "count"]
    if view == "pivot":
        context = {"pivot_row_groupby": groups[:1], "pivot_column_groupby": groups[1:2],
                   "pivot_measures": measures or ["__count"]}
    elif view == "list":
        context = {"group_by": groups}
    else:
        context = {"graph_mode": view, "graph_groupbys": groups}
        if measures:
            context["graph_measure"] = measures[0]
    return context


def view_mode_of(definition):
    return {"pivot": "pivot,list,form", "list": "list,form"}.get(definition["view"], "graph,list,form")


def action_values(env, definition):
    """:return: what the window action of a definition holds"""
    model = env[definition["model"]]
    domain = build_domain(model, definition["conditions"], definition["match"])
    return {
        "res_model": model._name,
        "domain": domain_text(definition, domain),
        "context": repr(context_of(definition)),
        "view_mode": view_mode_of(definition),
    }


# ----------------------------------------------------------------------
# In words
# ----------------------------------------------------------------------
def measure_words(env, model, spec):
    if spec == "count":
        return env._("the number of records")
    name, _separator, aggregate = spec.partition(":")
    label = model._fields[name]._description_string(env)
    return {
        "sum": env._("the total of %s"), "avg": env._("the average of %s"),
        "min": env._("the smallest %s"), "max": env._("the largest %s"),
    }[aggregate] % label


def group_words(env, model, spec):
    name, _separator, interval = spec.partition(":")
    label = model._fields[name]._description_string(env)
    if not interval:
        return label
    words = {"day": env._("day"), "week": env._("week"), "month": env._("month"), "quarter": env._("quarter"),
             "year": env._("year")}
    return f"{label} ({words.get(interval, interval)})"


def shape_words(env, view):
    return {
        "bar": env._("a bar chart"), "line": env._("a line chart"), "pie": env._("a pie chart"),
        "pivot": env._("a pivot table"), "list": env._("a list"),
    }[view]


def filter_words(env, model, definition):
    parts = [f"{c['field']} {c['operator']} {c['value']}" for c in definition["conditions"]]
    if definition.get("period"):
        parts.append(period_words(env, definition["period"]))
    return "; ".join(parts) or env._("Everything you may read")


def title_of(env, model, definition):
    """:return: the name given, or one built from what it shows"""
    if definition.get("name"):
        return definition["name"]
    groups = " / ".join(group_words(env, model, spec) for spec in definition["group_by"])
    first = definition["measures"][0]
    what = model._description if first == "count" else model._fields[first.partition(":")[0]]._description_string(env)
    return env._("%(what)s by %(groups)s", what=what, groups=groups) if groups else what


def sentence(env, definition):
    """:return: the definition in one sentence, in the language of the reader"""
    model = env[definition["model"]]
    text = env._("%(measures)s of %(model)s", measures=", ".join(
        measure_words(env, model, spec) for spec in definition["measures"]), model=model_label(model))
    if definition["group_by"]:
        text += " " + env._("by %s", " / ".join(group_words(env, model, spec) for spec in definition["group_by"]))
    if definition["conditions"] or definition.get("period"):
        text += ", " + filter_words(env, model, definition)
    return env._("%(what)s, as %(shape)s", what=text, shape=shape_words(env, definition["view"]))


def card_lines(env, model, definition, home, spreadsheet):
    yes, no = env._("Yes"), env._("No")
    return [
        {"label": env._("Name"), "value": title_of(env, model, definition)},
        {"label": env._("Shows"), "value": ", ".join(
            measure_words(env, model, spec) for spec in definition["measures"]) + " — " + model_label(model)},
        {"label": env._("Cut by"), "value": " / ".join(
            group_words(env, model, spec) for spec in definition["group_by"]) or env._("Nothing: a plain list")},
        {"label": env._("Only"), "value": filter_words(env, model, definition)},
        {"label": env._("Shown as"), "value": shape_words(env, definition["view"])},
        {"label": env._("Kept in"), "value": env._("Nudge ▸ My Reports (only you see it)")},
        {"label": env._("On my home page"), "value": yes if home else no},
        {"label": env._("As a spreadsheet"), "value": yes if spreadsheet else no},
    ]


# ----------------------------------------------------------------------
# The figures, for a tile or a spreadsheet: read through the action, as the person
# ----------------------------------------------------------------------
def numbers(env, definition, domain_source, limit=12):
    """:return: ``{"total", "measure", "labels", "values", "currency"}`` of the first measure,
    counted by Odoo as the person"""
    model = env[definition["model"]]
    domain = evaluate(env, domain_source)
    spec = definition["measures"][0]
    aggregate = "__count" if spec == "count" else spec
    [(total,)] = grouped(env, model, domain, [], [aggregate])
    labels, values = [], []
    groups = definition["group_by"][:1]
    if groups:
        dated = model._fields[groups[0].partition(":")[0]].type in ("date", "datetime")
        # The newest periods: the last ones asked for, shown oldest first
        rows = grouped(env, model, domain, groups, [aggregate], limit,
                       f"{groups[0]} desc" if dated else f"{aggregate} DESC")
        if dated:
            rows = list(reversed(rows))
        for group, value in rows:
            labels.append(_label(env, group, groups[0]))
            values.append(round(value or 0, 2))
    return {"total": total or 0, "measure": measure_words(env, model, spec), "labels": labels, "values": values}


def _label(env, value, spec):
    from .charts import _date_label  # noqa: PLC0415 - the chart tool writes dates for people
    if value is None or value is False:
        return env._("Undefined")
    if hasattr(value, "display_name"):
        return value.display_name
    if isinstance(value, (datetime.date, datetime.datetime)):
        return _date_label(env, value.isoformat(), spec.partition(":")[2] or "day")
    return str(value)


def spreadsheet_from(env, definition, title, data):
    """Keep the figures as a spreadsheet with a chart, where the companion is installed.

    :return: the action that opens it, or None"""
    if "nudge.spreadsheet" not in env:
        return None
    workbook = import_module("odoo.addons.nudge_spreadsheet.tools.workbook")
    sheets = import_module("odoo.addons.nudge_spreadsheet.tools.spreadsheet")
    model = env[definition["model"]]
    columns = [{"label": group_words(env, model, definition["group_by"][0]) if definition["group_by"] else title,
                "type": "text"}, {"label": data["measure"], "type": "number"}]
    sheet = {"name": title[:31], "columns": columns, "rows": [[label, value] for label, value in zip(
        data["labels"], data["values"])], "totals": True}
    Spreadsheet = env["nudge.spreadsheet"]
    book = workbook.build_workbook(Spreadsheet._empty_spreadsheet_data(), [sheets.with_chart(sheet)])
    action, _where = Spreadsheet._store_for_nudge(title[:80], book)
    return action


# ----------------------------------------------------------------------
# The tool
# ----------------------------------------------------------------------
@base.register
class SaveReport(base.Action):
    name = "save_report"
    kind = "create"
    description = (
        "Keep a report the person asked for, to open again: sales of the month per salesperson as a "
        "chart, invoices per month and customer as a pivot, a list of unpaid bills. Use it when they "
        "say save, keep, add to my reports (احفظ، خزّن، سجّل). Give the model, the conditions, a "
        "period that moves with the day (this_month, last_year, last_30_days...), what to group by "
        "(a date takes an interval: \"date_order:month\"), the measures, and the shape: bar, line, "
        "pie, pivot or list. Nothing is made by this call: the person reads the definition in words "
        "and says yes or no. On yes the report is kept under Nudge / My Reports, as a window action "
        "Odoo checks again for their rights each time it opens; with home it is a tile on their "
        "home page, with spreadsheet it is also a spreadsheet with a chart."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["model", "view"],
        "properties": {
            "model": {"type": "string", "pattern": MODEL_PATTERN},
            "conditions": {"type": "array", "maxItems": MAX_CONDITIONS, "items": CONDITION_SCHEMA_ITEMS},
            "match": {"type": "string", "enum": ["all", "any"]},
            "period": {"type": "string", "enum": list(PERIODS)},
            "period_field": {"type": "string", "description": "The date the period cuts; the order date by default."},
            "group_by": {"type": "array", "maxItems": MAX_GROUPS, "items": {"type": "string", "pattern": GROUP_PATTERN}},
            "measures": {"type": "array", "maxItems": MAX_MEASURES,
                         "items": {"type": "string", "pattern": MEASURE_PATTERN},
                         "description": "\"count\", or a number field with sum, avg, min or max."},
            "view": {"type": "string", "enum": list(VIEWS)},
            "name": {"type": "string", "maxLength": 80, "description": "In the language of the person."},
            "home": {"type": "boolean", "description": "Also a tile on the home page."},
            "spreadsheet": {"type": "boolean", "description": "Also a spreadsheet with a chart."},
        },
    }

    @refusing
    def run(self, env, arguments, screen):
        if "nudge.report" not in env:
            raise Refused("Reports cannot be kept here.")
        definition, model = normalise(env, arguments)
        home, sheet = bool(arguments.get("home")), bool(arguments.get("spreadsheet")) and "nudge.spreadsheet" in env
        title = title_of(env, model, definition)
        definition["name"] = title
        return base.ToolResult(
            "The person is being shown the report to keep: " + sentence(env, definition),
            pending={
                "summary": env._("Save the report: %s", title),
                "res_model": "nudge.report",
                "res_ids": [],
                "values": {"definition": definition, "home": home, "spreadsheet": sheet},
                "is_sensitive": False,
                "preview": {"records": [], "lines": card_lines(env, model, definition, home, sheet)},
            },
        )

    def execute(self, env, step):
        values = step.values
        report = env["nudge.report"].create_from_definition(values["definition"], home=values.get("home"))
        extra = ""
        if values.get("spreadsheet"):
            action = report.action_spreadsheet()
            extra = " It is also a spreadsheet, under Nudge ▸ Spreadsheets." if action else ""
        return base.ToolResult(
            f"The report {report.name!r} is kept under Nudge ▸ My Reports.{extra}",
            event={"type": "open_action", "action": report.action_open_report(), "text": report.name},
            records=("nudge.report", report.ids, [report.name]),
        )


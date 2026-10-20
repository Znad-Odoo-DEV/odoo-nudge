"""A chart in the chat: the figures of ``aggregate_records`` drawn.

The rows come from the same aggregate, as the person: what they cannot open
in Odoo is not drawn for them. The tool does not draw anything itself: it sends
the browser the labels, the series, the currency and the action that opens the
same figures as a graph view of Odoo.
"""
from odoo import _
import datetime
import json

from odoo.tools.misc import format_date

from . import base
from .analytics import GROUP_PATTERN, MAX_ROWS, MEASURE_PATTERN, AggregateRecords
from .records import (
    MAX_CONDITIONS, MODEL_PATTERN, OPERATORS, PATH_PATTERN, Refused, build_domain, get_model,
)

CHART_TYPES = ("bar", "line", "pie")
MAX_SLICES = 12
DEFAULT_ROWS = 12


def _date_label(env, text, interval):
    """:return: a date group written for the person, in their language"""
    try:
        day = datetime.date.fromisoformat(str(text)[:10])
    except ValueError:
        return str(text)
    if interval == "year":
        return str(day.year)
    if interval == "quarter":
        return f"Q{(day.month - 1) // 3 + 1} {day.year}"
    if interval == "month":
        return format_date(env, day, date_format="MMM yyyy")
    return format_date(env, day)


def _label(env, value, interval=None):
    if value is None:
        return _("Undefined")
    if isinstance(value, dict):
        return value.get("name") or _("Undefined")
    if isinstance(value, list):
        return ", ".join(item.get("name", "") for item in value) or _("Undefined")
    if interval:
        return _date_label(env, value, interval)
    return str(value)


def _currency(env, field):
    """:return: how the company writes an amount, when the measure is an amount of money"""
    if field is None or field.type != "monetary":
        return None
    currency = env.company.currency_id
    return {"symbol": currency.symbol, "position": currency.position, "digits": currency.decimal_places,
            "name": currency.name}


def _report_action(model, domain, groups, measure, title, chart_type):
    """:return: the action that opens the same figures as a graph (or, where the
    model has none, a pivot) view, with the same domain and groupings; None when it has neither"""
    for view_type in ("graph", "pivot"):
        try:
            model.get_view(view_type=view_type)
        except Exception:  # no such view for this model, or none the person may open
            continue
        measure_name = None if measure == "count" else measure.partition(":")[0]
        if view_type == "graph":
            context = {"graph_mode": chart_type, "graph_groupbys": groups}
            if measure_name:
                context["graph_measure"] = measure_name
        else:
            context = {"pivot_row_groupby": groups[:1], "pivot_column_groupby": groups[1:2]}
            if measure_name:
                context["pivot_measures"] = [measure_name]
        return {
            "type": "ir.actions.act_window", "name": title, "res_model": model._name,
            "views": [[False, view_type], [False, "list"], [False, "form"]],
            "view_mode": f"{view_type},list,form",
            "domain": json.loads(json.dumps(domain.to_list() if hasattr(domain, "to_list") else list(domain),
                                            default=str)),
            "context": context, "target": "current",
        }
    return None


@base.register
class ShowChart(base.Tool):
    name = "show_chart"
    group = "nudge.group_nudge_agent"
    modes = (base.MODE_AGENT,)
    terminal = True
    kind = "show"
    description = (
        "Draw a chart in the chat from the records of the business, as the person: sales per "
        "salesperson as bars, invoices per month as a line, the share of each customer as a pie. "
        "Use it when the person asks for a chart, a graph, a drawing of figures (رسم، مخطط، شارت، "
        "رسم بياني). It takes what aggregate_records takes: conditions, group_by (one group, or two "
        "for several series), one measure. A date group takes an interval: \"date_order:month\". "
        "The person gets the chart, a button to open the same figures as a report, and one to "
        "download the picture."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["model", "group_by"],
        "properties": {
            "model": {"type": "string", "pattern": MODEL_PATTERN,
                      "description": "Technical name, such as \"sale.order\" or \"account.move\"."},
            "conditions": {
                "type": "array", "maxItems": MAX_CONDITIONS,
                "items": {
                    "type": "object", "additionalProperties": False, "required": ["field", "operator", "value"],
                    "properties": {
                        "field": {"type": "string", "pattern": PATH_PATTERN},
                        "operator": {"type": "string", "enum": list(OPERATORS)},
                        "value": {"type": "string", "maxLength": 200},
                    },
                },
            },
            "match": {"type": "string", "enum": ["all", "any"]},
            "group_by": {"type": "array", "minItems": 1, "maxItems": 2,
                         "items": {"type": "string", "pattern": GROUP_PATTERN},
                         "description": "The first group gives the bars, the points or the slices; "
                                        "the second, the series."},
            "measure": {"type": "string", "pattern": MEASURE_PATTERN,
                        "description": "\"count\", or a number field with sum, avg, min or max. "
                                       "None counts the records."},
            "chart_type": {"type": "string", "enum": list(CHART_TYPES),
                           "description": "bar (the default), line for time, pie for shares."},
            "title": {"type": "string", "maxLength": 120},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_ROWS},
        },
    }

    def run(self, env, arguments, screen):
        measure = arguments.get("measure") or "count"
        groups = arguments["group_by"]
        aggregate = AggregateRecords().run(env, {
            "model": arguments["model"], "conditions": arguments.get("conditions") or [],
            "match": arguments.get("match") or "all", "group_by": groups, "measures": [measure],
            "limit": arguments.get("limit") or DEFAULT_ROWS,
        }, screen)
        if aggregate.is_error:
            return aggregate
        data = json.loads(aggregate.content)
        try:
            model = get_model(env, arguments["model"])
            domain = build_domain(model, arguments.get("conditions") or [], arguments.get("match"))
        except Refused as refusal:
            return base.ToolResult(str(refusal), is_error=True)
        keys = data["group_by"]
        rows = data["rows"]
        if not rows:
            return base.ToolResult(_("There is nothing to draw: no record answers this."), is_error=True)
        field = model._fields.get(measure.partition(":")[0]) if measure != "count" else None
        dates = [model._fields[key.partition(":")[0]].type in ("date", "datetime") for key in keys]
        intervals = [key.partition(":")[2] if is_date else None for key, is_date in zip(keys, dates)]
        if dates[0]:
            rows = sorted(rows, key=lambda row: str(row[keys[0]]))
        labels = []
        for row in rows:
            label = _label(env, row[keys[0]], intervals[0])
            if label not in labels:
                labels.append(label)
        series = {}
        for row in rows:
            label = _label(env, row[keys[0]], intervals[0])
            serie = _label(env, row[keys[1]], intervals[1]) if len(keys) > 1 else self._measure_title(
                env, model, measure)
            values = series.setdefault(serie, [0] * len(labels))
            values[labels.index(label)] += row.get(measure) or 0
        chart_type = arguments.get("chart_type") or ("line" if dates[0] else "bar")
        if chart_type == "pie" and (len(series) > 1 or len(labels) > MAX_SLICES):
            chart_type = "bar"
        title = (arguments.get("title") or "").strip() or self._title(env, model, measure, keys)
        event = {
            "type": "chart",
            "chart_type": chart_type,
            "title": title,
            "labels": labels,
            "datasets": [{"label": serie, "data": values} for serie, values in series.items()],
            "measure": self._measure_title(env, model, measure),
            "currency": _currency(env, field),
            "action": _report_action(model, domain, keys, measure, title, chart_type),
            "model": model._name,
            "note": data.get("note") or "",
        }
        content = {"labels": labels, "series": series}
        return base.ToolResult(
            f"The chart {title!r} is shown to the person: " + base.dump(content), event=event)

    @staticmethod
    def _measure_title(env, model, measure):
        if measure == "count":
            return _("Count")
        return model._fields[measure.partition(":")[0]]._description_string(env)

    def _title(self, env, model, measure, keys):
        by = [model._fields[key.partition(":")[0]]._description_string(env) for key in keys]
        return _("%(measure)s by %(groups)s", measure=self._measure_title(env, model, measure),
                     groups=" / ".join(by))

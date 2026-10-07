"""A report as a spreadsheet, from what the model read."""
from odoo.addons.nudge.tools import base
from odoo.addons.nudge.tools.records import cut, refusing

from . import workbook

MAX_SHEETS = 5
COLUMN_TYPES = ["text", "number", "money", "percent", "date"]
CHART_TYPES = ["bar", "line", "pie"]

SHEET_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "columns", "rows"],
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 31},
        "columns": {
            "type": "array",
            "minItems": 1,
            "maxItems": workbook.MAX_COLUMNS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["label", "type"],
                "properties": {
                    "label": {"type": "string", "minLength": 1, "maxLength": 60},
                    "type": {"type": "string", "enum": COLUMN_TYPES},
                },
            },
        },
        "rows": {
            "type": "array",
            "maxItems": workbook.MAX_ROWS,
            "items": {
                "type": "array",
                "maxItems": workbook.MAX_COLUMNS,
                "items": {"type": ["string", "number", "boolean", "null"]},
            },
            "description": "One list per row, in the order of the columns; numbers as numbers.",
        },
        "totals": {"type": "boolean", "description": "A total row under the numbers."},
        "chart": {
            "type": "object",
            "additionalProperties": False,
            "required": ["type"],
            "properties": {
                "type": {"type": "string", "enum": CHART_TYPES},
                "title": {"type": "string", "maxLength": 80},
                "label_column": {"type": "integer", "minimum": 0, "description": "Column of the labels, from 0."},
                "data_columns": {
                    "type": "array", "maxItems": 6, "items": {"type": "integer", "minimum": 0},
                    "description": "Columns of the values, from 0; the numeric ones when empty.",
                },
                "stacked": {"type": "boolean"},
            },
        },
    },
}


@base.register
class MakeSpreadsheet(base.Tool):
    name = "make_spreadsheet"
    group = "nudge.group_nudge_agent"
    modes = (base.MODE_AGENT,)
    terminal = True
    kind = "show"
    description = (
        "Make a spreadsheet for the person and open it on their screen: a report or a comparison "
        "as a formatted table with a total row and a chart, from figures you read with the other "
        "tools (aggregate_records, search_records, run_python, run_sql). Use it when they asked "
        "for a spreadsheet, or chose one when asked. One sheet per table; a comparison is one "
        "table with a column per period or per place and the difference. Values go in as "
        "numbers, labels as text; the chart takes the label column and the value columns. "
        "Say in \"text\" what the spreadsheet holds."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "sheets", "text"],
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 80},
            "sheets": {"type": "array", "minItems": 1, "maxItems": MAX_SHEETS, "items": SHEET_SCHEMA},
            "text": {
                "type": "string",
                "minLength": 1,
                "maxLength": 300,
                "description": "In the language the person wrote in.",
            },
        },
    }

    @refusing
    def run(self, env, arguments, screen):
        Spreadsheet = env["nudge.spreadsheet"]
        empty = Spreadsheet._empty_spreadsheet_data()
        data = workbook.build_workbook(empty, arguments["sheets"])
        title = cut(arguments["title"].strip(), 80)
        action, where = Spreadsheet._store_for_nudge(
            title, data, env.context.get("nudge_conversation_id") or False)
        rows = sum(len(sheet.get("rows") or []) for sheet in arguments["sheets"])
        return base.ToolResult(
            f"The spreadsheet {title!r} ({len(arguments['sheets'])} sheet(s), {rows} rows) is being "
            f"opened on the person's screen; it is kept {where}. They can change it and download "
            "it from there.",
            event={
                "type": "open_action",
                "action": action,
                "text": arguments["text"].strip(),
            },
        )

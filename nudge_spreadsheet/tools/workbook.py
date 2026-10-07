"""Building a spreadsheet file from a table and a chart.

The model describes a report as plain data: named columns with a type,
rows of values, a chart over some columns. This turns it into the JSON of
Odoo's spreadsheet (o-spreadsheet), with a bold header, number formats, a
total row and the chart next to the table. Nothing here reads the database:
what goes in the cells is what the model read with the other tools.
"""
import uuid

HEADER_STYLE = "1"
TOTAL_STYLE = "2"
TITLE_STYLE = "3"
STYLES = {
    HEADER_STYLE: {"bold": True, "fillColor": "#E9ECEF", "textColor": "#212529"},
    TOTAL_STYLE: {"bold": True},
    TITLE_STYLE: {"bold": True, "fontSize": 14},
}
FORMATS = {"money": "#,##0.00", "percent": "0.0%", "number": "#,##0.##", "date": "yyyy-mm-dd"}
NUMERIC = ("number", "money", "percent")
MAX_ROWS = 500
MAX_COLUMNS = 20
CHART_WIDTH = 640
CHART_HEIGHT = 360
COLUMN_WIDTH = 140


def column_letter(index):
    """:return: "A" for 0, "AA" for 26"""
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def _cell_content(value, kind):
    if value is None or value == "":
        return None
    if kind in NUMERIC:
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float)):
            return repr(float(value)) if isinstance(value, float) else str(value)
        text = str(value).strip().replace(",", "")
        try:
            float(text.rstrip("%"))
        except ValueError:
            return str(value)
        return text
    return str(value)


def build_sheet(spec, number, format_ids):
    """:param spec: {"name", "columns": [{"label", "type"}], "rows": [[...]],
        "totals": bool, "chart": {...} or None}
    :return: the sheet as o-spreadsheet keeps it"""
    columns = spec["columns"][:MAX_COLUMNS]
    rows = spec.get("rows") or []
    rows = rows[:MAX_ROWS]
    cells, styles, formats, cols = {}, {}, {}, {}
    for index, column in enumerate(columns):
        cells[f"{column_letter(index)}1"] = str(column["label"])
        styles[f"{column_letter(index)}1"] = HEADER_STYLE
        cols[str(index)] = {"size": COLUMN_WIDTH}
    for row_index, row in enumerate(rows, start=2):
        for index, column in enumerate(columns):
            value = row[index] if index < len(row) else None
            content = _cell_content(value, column["type"])
            if content is None:
                continue
            reference = f"{column_letter(index)}{row_index}"
            cells[reference] = content
            if column["type"] in format_ids:
                formats[reference] = format_ids[column["type"]]
    last_row = len(rows) + 1
    total_row = None
    if spec.get("totals") and rows and any(column["type"] in NUMERIC for column in columns):
        total_row = last_row + 1
        labelled = False
        for index, column in enumerate(columns):
            reference = f"{column_letter(index)}{total_row}"
            if column["type"] in NUMERIC:
                letter = column_letter(index)
                cells[reference] = f"=SUM({letter}2:{letter}{last_row})"
                if column["type"] in format_ids:
                    formats[reference] = format_ids[column["type"]]
            elif not labelled:
                cells[reference] = "Total"
                labelled = True
            styles[reference] = TOTAL_STYLE
    figures = []
    chart = spec.get("chart")
    if chart and rows:
        figures.append(build_chart(chart, columns, last_row, len(columns)))
    sheet = {
        "id": f"sheet{number}",
        "name": spec["name"][:31] or f"Sheet{number}",
        "colNumber": max(26, len(columns) + 8),
        "rowNumber": max(100, (total_row or last_row) + 20),
        "rows": {},
        "cols": cols,
        "merges": [],
        "cells": cells,
        "styles": styles,
        "formats": formats,
        "borders": {},
        "conditionalFormats": [],
        "dataValidationRules": [],
        "figures": figures,
        "tables": [{
            "range": f"A1:{column_letter(len(columns) - 1)}{last_row}",
            "type": "static",
            "config": {
                "hasFilters": True, "totalRow": False, "firstColumn": False, "lastColumn": False,
                "numberOfHeaders": 1, "bandedRows": True, "bandedColumns": False,
                "automaticAutofill": False, "styleId": "TableStyleLight9",
            },
        }] if rows else [],
        "areGridLinesVisible": True,
        "isVisible": True,
        "headerGroups": {"ROW": [], "COL": []},
        "comments": {},
    }
    return sheet


def build_chart(chart, columns, last_row, column_count):
    """:return: the figure of a chart over the table, to the right of it"""
    label_column = chart.get("label_column") or 0
    data_columns = [index for index in chart.get("data_columns") or [] if 0 <= index < column_count]
    if not data_columns:
        data_columns = [index for index, column in enumerate(columns) if column["type"] in NUMERIC][:4]
    kind = chart.get("type") or "bar"
    data = {
        "type": kind,
        "title": {"text": chart.get("title") or ""},
        "background": "#FFFFFF",
        "legendPosition": "top",
        "dataSets": [
            {"dataRange": f"{column_letter(index)}1:{column_letter(index)}{last_row}"}
            for index in data_columns
        ],
        "dataSetsHaveTitle": True,
        "labelRange": f"{column_letter(label_column)}2:{column_letter(label_column)}{last_row}",
        "aggregated": False,
    }
    if kind in ("bar", "line"):
        data.update(stacked=bool(chart.get("stacked")), verticalAxisPosition="left", axesDesign={})
    if kind == "pie":
        data.update(isDoughnut=False, showValues=False)
    return {
        "id": str(uuid.uuid4()),
        "tag": "chart",
        "col": column_count + 1,
        "row": 0,
        "offset": {"x": 0, "y": 0},
        "width": CHART_WIDTH,
        "height": CHART_HEIGHT,
        "data": data,
    }


def build_workbook(empty, sheets):
    """:param empty: an empty workbook, as ``spreadsheet.mixin`` makes one for
        this person: its version, locale and settings are kept
    :param sheets: list of sheet specs
    :return: the workbook"""
    workbook = dict(empty)
    format_ids = {}
    workbook_formats = dict(workbook.get("formats") or {})
    for kind, pattern in FORMATS.items():
        format_ids[kind] = str(len(workbook_formats) + 1)
        workbook_formats[format_ids[kind]] = pattern
    workbook["formats"] = workbook_formats
    workbook["styles"] = {**(workbook.get("styles") or {}), **STYLES}
    workbook["sheets"] = [build_sheet(spec, number, format_ids) for number, spec in enumerate(sheets, start=1)]
    workbook["uniqueFigureIds"] = True
    return workbook

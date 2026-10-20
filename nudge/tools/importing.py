"""Trying an import before it is done.

The Excel or CSV file sent with the question is imported the way the Test
button of the Import screen does it: every row is read and checked, nothing
is saved, and every problem comes back with its rows and its column. What
Odoo would refuse, the person learns here first, in plain words.
"""
import base64

from . import base
from .records import MODEL_PATTERN, Refused, cut, get_model, refusing

MAX_MESSAGES = 40
MAX_COLUMNS = 60
MAX_SHOWN_VALUE = 80


class DryRun(Exception):
    """Raised to leave the savepoint: nothing the trial did is kept."""


def attached_file(env):
    """:return: ``(name, bytes, mimetype)`` of the last file sent in this
    conversation that is not an image
    :raise Refused: when there is none"""
    conversation_id = env.context.get("nudge_conversation_id")
    if conversation_id:
        messages = env["nudge.message"].sudo().search([
            ("conversation_id", "=", conversation_id), ("role", "=", "user"),
            ("attachment_id", "!=", False)], order="id desc", limit=5)
        for message in messages:
            attachment = message.attachment_id
            if attachment.datas and not (attachment.mimetype or "").startswith("image/"):
                return attachment.name, base64.b64decode(attachment.datas), attachment.mimetype or ""
    raise Refused("No file: ask the person to attach the Excel or CSV file to their message, then try again.")


def describe_message(message, headers, fields, has_headers):
    """:return: one problem of the import, as the person reads it"""
    parts = []
    rows = message.get("rows") or {}
    offset = 2 if has_headers else 1
    if isinstance(rows, dict) and rows.get("from") is not None:
        first, last = rows["from"] + offset, (rows.get("to", rows["from"])) + offset
        parts.append(f"row {first}" if first == last else f"rows {first}–{last}")
    elif message.get("record") is not None:
        parts.append(f"row {message['record'] + offset}")
    field = message.get("field_path") or message.get("field")
    if isinstance(field, list):
        field = "/".join(field)
    if field:
        column = next((header for header, path in zip(headers, fields) if path and path.split("/")[0] == str(field).split("/")[0]), None)
        label = message.get("field_name") or field
        parts.append(f"column {column!r} (field {label})" if column else f"field {label}")
    kind = message.get("type") or "error"
    text = " ".join(str(message.get("message") or "").split())
    line = f"- {kind}: " + (", ".join(parts) + ": " if parts else "") + cut(text, 300)
    value = message.get("value")
    if value not in (None, "", False):
        line += f" [value: {cut(str(value), MAX_SHOWN_VALUE)}]"
    more = message.get("moreinfo")
    if isinstance(more, str) and more.strip():
        line += f" ({cut(more.strip(), 200)})"
    return line


def read_options(has_headers=True, sheet=""):
    """:return: the options of Odoo's own importer for a file read as it comes"""
    return {
        "has_headers": has_headers, "advanced": True, "keep_matches": False,
        "quoting": '"', "separator": "", "encoding": "",
        "date_format": "", "datetime_format": "",
        "float_thousand_separator": ",", "float_decimal_separator": ".",
        "sheet": sheet or "", "skip": 0,
    }


def _column_matches(preview, headers):
    """:return: the path the importer matched to each header, or False"""
    matches = preview.get("matches") or {}
    fields = []
    for index in range(len(headers)):
        match = matches.get(index) if index in matches else matches.get(str(index))
        fields.append("/".join(match) if match else False)
    return fields


def read_columns(env, model, name, data, mimetype, sheet="", has_headers=True, with_rows=False):
    """The file as Odoo's importer reads it, without trying anything.

    :return: ``{"headers", "samples" (up to 5 values per column), "fields"
        (the importer's own match, a path or False, per column), "options",
        "count", "rows" (every row, when asked)}``
    :raise Refused: when the file cannot be read
    Nothing is kept: the transient record of the importer is rolled back."""
    if "base_import.import" not in env:
        raise Refused("The Import app (base_import) is not installed here: nothing can be tried.")
    options = read_options(has_headers, sheet)
    found = {}
    try:
        with env.cr.savepoint():
            record = env["base_import.import"].create({
                "res_model": model._name, "file": data, "file_name": name, "file_type": mimetype})
            preview = record.parse_preview(dict(options))
            if preview.get("error"):
                raise Refused(f"The file could not be read: {cut(str(preview['error']), 400)}")
            samples = list(preview.get("preview") or [])
            headers = list(preview.get("headers") or [])
            if not headers:
                headers = [f"Column {index + 1}" for index in range(len(samples))]
            options.update(preview.get("options") or {})
            count, rows = None, None
            try:
                count, rows = record._read_file(dict(options))
                rows = [list(row) for row in rows] if with_rows else None
            except Exception:  # noqa: BLE001
                rows = None
            found = {"headers": headers, "samples": samples, "fields": _column_matches(preview, headers),
                     "options": options, "count": count, "rows": rows}
            raise DryRun()
    except DryRun:
        pass
    finally:
        env.invalidate_all()
    return found


def dry_run(env, model, name, data, mimetype, mapping=None, sheet="", has_headers=True):
    """Try the file into ``model`` the way the Test button does: every row is
    read and checked, nothing is saved. The one engine of the AI tool and of
    the check without AI.

    :param mapping: ``[{"column": header, "field": path or ""}]`` over what
        Odoo's importer matched by itself
    :return: ``{"report": lines, "headers", "fields", "problems" (one line
        each), "errors", "count", "ids" (records that would be made), "sheet"}``
    :raise Refused: for a file or a mapping that cannot be used"""
    if "base_import.import" not in env:
        raise Refused("The Import app (base_import) is not installed here: nothing can be tried.")
    options = read_options(has_headers, sheet)
    report, problems = [], []
    result = {"report": report, "problems": problems, "errors": 0, "ids": 0}
    try:
        with env.cr.savepoint():
            record = env["base_import.import"].create({
                "res_model": model._name, "file": data, "file_name": name, "file_type": mimetype})
            preview = record.parse_preview(dict(options))
            if preview.get("error"):
                raise Refused(f"The file could not be read: {cut(str(preview['error']), 400)}")
            rows = preview.get("preview") or []
            headers = list(preview.get("headers") or [])
            if not headers:
                headers = [f"Column {index + 1}" for index in range(len(rows))]
            fields = _column_matches(preview, headers)
            for entry in mapping or []:
                path = entry["field"].strip()
                if isinstance(entry.get("index"), int) and 0 <= entry["index"] < len(fields):
                    # A column by its place, for headers that repeat
                    fields[entry["index"]] = path or False
                    continue
                column = entry["column"].strip()
                lowered = [header.strip().casefold() for header in headers]
                if column.casefold() not in lowered:
                    raise Refused(f"No column {column!r} in the file. Its columns: {', '.join(headers)}.")
                if path and path.split("/")[0] not in model._fields:
                    raise Refused(f"{model._name} has no field {path.split('/')[0]!r}. describe_model lists them.")
                fields[lowered.index(column.casefold())] = path or False
            matched = [f"{header} → {path}" for header, path in zip(headers, fields) if path]
            ignored = [header for header, path in zip(headers, fields) if not path]
            try:
                count, _rows = record._read_file(dict(options, **(preview.get("options") or {})))
            except Exception:  # noqa: BLE001
                count = None
            sheet_name = (preview.get("options") or {}).get("sheet")
            report.append(
                f"Tried {name!r}" + (f", sheet {sheet_name!r}" if sheet_name else "") + f" into {model._description} ({model._name})"
                + (f": {count - (1 if has_headers else 0)} data row(s)" if isinstance(count, int) else "") + ".")
            report.append("Columns matched: " + ("; ".join(matched) if matched else "none"))
            if ignored:
                report.append("Columns matched to no field, ignored: " + ", ".join(ignored)
                              + ". Name them in mapping if they hold data to import.")
            options.update(preview.get("options") or {})
            outcome = record.execute_import(fields, headers, dict(options), dryrun=True)
            messages = [message for message in (outcome.get("messages") or []) if isinstance(message, dict)]
            result.update(headers=headers, fields=fields, count=count, sheet=sheet_name,
                          ids=len(outcome.get("ids") or []))
            if not messages:
                report.append(f"No problem: {len(outcome.get('ids') or [])} record(s) would be imported.")
            else:
                errors = sum(1 for message in messages if message.get("type", "error") == "error")
                result["errors"] = errors
                report.append(f"{len(messages)} problem(s), {errors} of them errors that stop the import:")
                for message in messages[:MAX_MESSAGES]:
                    problems.append(describe_message(message, headers, fields, has_headers))
                report.extend(problems)
                if len(messages) > MAX_MESSAGES:
                    report.append(f"… and {len(messages) - MAX_MESSAGES} more.")
            raise DryRun()
    except DryRun:
        pass
    finally:
        # What the trial read is gone from the database: gone from the cache too
        env.invalidate_all()
    report.append("Nothing was saved.")
    return result


@base.register
class TestImport(base.Tool):
    name = "test_import"
    description = (
        "Try importing the Excel or CSV file the person sent into a model, the way the Test "
        "button of the Import screen does: every row is read and checked, nothing is saved, "
        "and each problem comes back with its row and its column: a column that matches no "
        "field, a required field left empty, a name that matches no record, a date or a "
        "number in a form Odoo does not read. Use it when they import and get errors, or ask "
        "whether a file will import. Columns are matched to fields by their header, as Odoo "
        "does; mapping corrects it: column header → technical name of the field "
        "(describe_model lists them; a path such as partner_id/name reaches a field of a "
        "related record), or an empty field to ignore the column. Then explain each problem "
        "in plain words and say how to fix it in the file."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["model"],
        "properties": {
            "model": {
                "type": "string",
                "pattern": MODEL_PATTERN,
                "description": "Technical name of the model imported into; the Import screen's snapshot gives it.",
            },
            "sheet": {"type": "string", "maxLength": 80, "description": "For an Excel file with several sheets."},
            "has_headers": {
                "type": "boolean",
                "description": "Whether the first row holds the column names. Default: yes.",
            },
            "mapping": {
                "type": "array",
                "maxItems": MAX_COLUMNS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["column", "field"],
                    "properties": {
                        "column": {"type": "string", "maxLength": 120, "description": "The header, as in the file."},
                        "field": {"type": "string", "maxLength": 120, "description": "Technical name or path; empty to ignore the column."},
                    },
                },
            },
        },
    }

    @refusing
    def run(self, env, arguments, screen):
        model = get_model(env, arguments["model"], "create")
        name, data, mimetype = attached_file(env)
        result = dry_run(env, model, name, data, mimetype, arguments.get("mapping"),
                         arguments.get("sheet"), arguments.get("has_headers", True))
        return base.ToolResult("\n".join(result["report"]))

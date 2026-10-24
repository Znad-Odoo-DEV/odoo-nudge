"""S10, a plan of several changes, and S11, a check of what each change left.

A plan is made of the calls the model wrote in one answer, and of nothing else: each step is
one of those calls, checked as its card would be, and shown before anything is done. What a
note, a file or an email the model read says cannot add a step the card does not show, and a
step points at what an earlier step makes only through "@n", the number of that step on the
card.

After a change, the records it wrote are read again and compared with what its card said
(S11), without a call to the model: a field Odoo changed on its own (a create that fills it,
the address of the company of a contact, a computed field, a rule) is named with the value it
has now, and the change is never told as done as asked.
"""
import copy
import re

from odoo import _, fields
from odoo.exceptions import AccessError
from odoo.tools import float_compare, html2plaintext

from . import base, schema
from .records import Refused, cut, field_label, model_label

PLAN_TOOL = "agent_plan"
# What a plan is made of: changes of records, checked before and read again after
STEP_TOOLS = ("prepare_create", "prepare_write", "call_method")
# What is read again after it is done: what its card says it writes
VERIFIED_TOOLS = ("prepare_create", "prepare_write")
# More changes than this in one answer make a plan, as do two kinds of change
WRITES_PARAM = "nudge.plan_writes"
DEFAULT_WRITES = 2
# The groups whose people say one yes for a whole plan: its card shows every change in full
ONE_YES_PARAM = "nudge.plan_one_yes_groups"
MAX_STEPS = 10
# Fields read again after a change, and differences named, at most
MAX_CHECKED = 60
MAX_DIFFERENCES = 20
REFERENCE = re.compile(r"^@(\d{1,2})$")
RELATIONAL = ("many2one", "many2many")

# What the model reads of each call of a plan, once the plan is over
STEP_DONE = "Step %(n)s (%(title)s): done. %(content)s"
STEP_FAILED = "Step %(n)s (%(title)s): it did not work, and this step changed nothing. %(content)s"
STEP_REMOVED = (
    "Step %(n)s (%(title)s): the person took it out of the plan before saying yes. It was not done: do not "
    "do it another way.")
STEP_NOT_REACHED = "Step %(n)s (%(title)s): not done: the plan stopped at step %(at)s."
STEP_STOPPED = "Step %(n)s (%(title)s): not done: the person stopped the plan before it."
STEP_DECLINED = (
    "Step %(n)s (%(title)s): the person said no to the plan. Nothing was done. Do not try to reach the same "
    "result another way: ask them what they want instead.")
PLAN_REFUSED = (
    "Not looked at: these calls make one plan, and another call of it was refused. Correct it and call them "
    "all again, together.")
TOO_MANY = "A plan holds at most %s steps: ask for the first ones, then for the rest once they are done."
NOT_AS_ASKED = (
    "The person said yes and it was written, but it is NOT done as asked: read again after writing, %(fields)s. "
    "Odoo changed it on its own (a value filled when the record is made, an address or a price it computes, a "
    "rule). Never say it is done: tell the person what differs, with the value it has now, and offer to set it "
    "again or to undo it with the Undo of its card. %(content)s")
CHECKED = "Read again after writing: every field is as asked."


def writes_before_plan(env):
    """:return: how many changes one answer may ask for before they make a plan"""
    value = env["ir.config_parameter"].sudo().get_param(WRITES_PARAM) or ""
    try:
        wanted = int(float(value))
    except ValueError:
        wanted = DEFAULT_WRITES
    return min(max(wanted, 1), MAX_STEPS)


def one_yes_groups(env):
    """:return: the groups the administrator gave one yes for a whole plan"""
    value = env["ir.config_parameter"].sudo().get_param(ONE_YES_PARAM) or ""
    ids = [int(part) for part in value.split(",") if part.strip().isdigit()]
    return env["res.groups"].sudo().browse(ids).exists()


def is_one_yes(env):
    """:return: whether the current user says one yes for a whole plan"""
    groups = one_yes_groups(env)
    if not groups:
        return False
    user = env.user.sudo()
    # The groups of a user, the implied ones with them: all_group_ids on 19, groups_id before
    held = user.all_group_ids if "all_group_ids" in user._fields else user.groups_id
    return bool(groups & held)


def plan_calls(env, calls, mode):
    """:param calls: ``tool_use`` events of one answer of the model
    :return: the calls that make a plan, in their order; empty when they do not: a single
        change (no extra cost), or a call a plan is not made of"""
    actions = [call for call in calls if isinstance(base.TOOLS.get(call["name"]), base.Action)]
    if len(actions) < 2 or mode not in (base.MODE_AGENT, base.MODE_DEV):
        return []
    if any(call.get("invalid") or call["name"] not in STEP_TOOLS for call in actions):
        return []
    kinds = {(call["name"], str((call.get("input") or {}).get("model"))) for call in actions}
    if len(actions) > writes_before_plan(env) or len(kinds) > 1:
        return actions
    return []


# ----------------------------------------------------------------------
# "@n": what an earlier step makes
# ----------------------------------------------------------------------

def _model(env, arguments):
    name = arguments.get("model") if isinstance(arguments, dict) else None
    return env[name] if isinstance(name, str) and name in env else None


def _reference(model, entry):
    """:return: the number of the step a value points at, or 0 for a value of its own: only a field
        that points at records reads "@n"; in a text it is the text"""
    if model is None or not isinstance(entry, dict) or not isinstance(entry.get("value"), str):
        return 0
    field = model._fields.get(entry.get("field") or "")
    if field is None or field.type not in RELATIONAL:
        return 0
    match = REFERENCE.match(entry["value"].strip())
    return int(match.group(1)) if match else 0


def find_references(env, arguments, number, makers):
    """:param number: the number of the step on the card
    :param makers: the numbers of the steps before it that create a record
    :return: ``[{"path", "n"}]``, where ``arguments`` point at what an earlier step makes
    :raise Refused: for a step that does not come before, or makes no record"""
    found = []

    def keep(path, step):
        if step not in makers:
            raise Refused(
                f"@{step} must name a step before this one (step {number}) that creates a record.")
        found.append({"path": path, "n": step})

    model = _model(env, arguments)
    for index, entry in enumerate(arguments.get("values") or []):
        step = _reference(model, entry)
        if step:
            keep(["values", index], step)
    for line_index, line in enumerate(arguments.get("lines") or []):
        field = model._fields.get(line.get("field") or "") if model is not None and isinstance(line, dict) else None
        comodel = env[field.comodel_name] if field is not None and field.type == "one2many" else None
        for index, entry in enumerate(line.get("values") or [] if isinstance(line, dict) else []):
            step = _reference(comodel, entry)
            if step:
                keep(["lines", line_index, index], step)
    for index, value in enumerate(arguments.get("ids") or []):
        match = REFERENCE.match(value.strip()) if isinstance(value, str) else None
        if match:
            keep(["ids", index], int(match.group(1)))
    return found


def _target(env, arguments, path):
    """:return: the field a reference of ``path`` stands in, or None"""
    model = _model(env, arguments)
    if model is None:
        return None
    if path[0] == "values":
        return model._fields.get(arguments["values"][path[1]].get("field") or "")
    line = arguments["lines"][path[1]]
    field = model._fields.get(line.get("field") or "")
    if field is None or field.type != "one2many":
        return None
    return env[field.comodel_name]._fields.get(line["values"][path[2]].get("field") or "")


def stand_in(env, arguments, references):
    """What the step reads before its turn: each "@n" is replaced, for the check only, by a record
    of the same kind this person may read; nothing is written with it, and the card shows "the
    record step n makes" in its place.

    :return: ``(arguments, exact)``: what can be checked now, and whether the card of the plan shows
        the whole change: a step done on a record an earlier step makes (an id), or a line pointing at
        one, is checked in full when its turn comes"""
    probe = copy.deepcopy(arguments)
    if not references:
        return probe, True
    if any(reference["path"][0] == "ids" for reference in references):
        return probe, False
    exact = True
    for reference in references:
        path = reference["path"]
        field = _target(env, arguments, path)
        stand = env[field.comodel_name].search([], limit=1) if field is not None else None
        if not stand:
            return probe, False
        if path[0] == "values":
            probe["values"][path[1]]["value"] = str(stand.id)
        else:
            probe["lines"][path[1]]["values"][path[2]]["value"] = str(stand.id)
            exact = False
    return probe, exact


def shown_references(env, arguments, references, pending):
    """:return: ``pending`` with what stood in for each "@n" taken out: the values keep only what the
        model gave, and the card reads "the record step n makes" where it was"""
    pending = copy.deepcopy(pending)
    written = pending.get("values") if isinstance(pending.get("values"), dict) else {}
    lines = (pending.get("preview") or {}).get("lines") or []
    values_count = len(arguments.get("values") or [])
    for reference in references:
        path = reference["path"]
        text = made_by(env, reference["n"])
        if path[0] == "values":
            name = arguments["values"][path[1]]["field"]
            (written.get("values") or {}).pop(name, None)
            if path[1] < len(lines):
                lines[path[1]] = dict(lines[path[1]], value=text, ref=reference["n"])
        elif values_count + path[1] < len(lines):
            nested = lines[values_count + path[1]].get("lines") or []
            if path[2] < len(nested):
                nested[path[2]] = dict(nested[path[2]], value=text, ref=reference["n"])
    return pending


def resolve(arguments, references, made):
    """:param made: ``{number: [ids]}`` of the steps done
    :return: the arguments of the step with each "@n" replaced by what step n made
    :raise Refused: when that step made nothing"""
    resolved = copy.deepcopy(arguments)
    for reference in references:
        ids = made.get(reference["n"]) or made.get(str(reference["n"])) or []
        if not ids:
            raise Refused(f"Step {reference['n']} made no record for this step to use.")
        path = reference["path"]
        text = ",".join(str(ident) for ident in ids)
        if path[0] == "values":
            resolved["values"][path[1]]["value"] = text
        elif path[0] == "lines":
            resolved["lines"][path[1]]["values"][path[2]]["value"] = text
        else:
            resolved["ids"][path[1]] = int(ids[0])
    return resolved


def schema_checked(name, arguments, references):
    """Check the shape of a step that cannot be checked in full before its turn
    :raise Refused: when it is not what the tool reads"""
    probe = copy.deepcopy(arguments)
    for reference in references:
        if reference["path"][0] == "ids":
            probe["ids"][reference["path"][1]] = 1
    tool = base.TOOLS[name]
    try:
        schema.validate(schema.forgive(probe, tool.input_schema), tool.input_schema)
    except schema.SchemaError as error:
        raise Refused(f"Invalid arguments. {error}") from None


def made_by(env, number):
    return _("the record step %s makes", number)


def raw_card(env, name, arguments, references):
    """:return: the pending part of a step checked when its turn comes: what the model asked, field
        by field, with the labels of the fields; its exact card is shown before it is done"""
    model = _model(env, arguments)
    label = model_label(model) if model is not None else str(arguments.get("model") or "")
    titles = {
        "prepare_create": _("Create: %s", label),
        "prepare_write": _("Change: %s", label),
        "call_method": _("Press “%(button)s”: %(model)s", button=arguments.get("method") or "", model=label),
    }
    at = {tuple(reference["path"]): reference["n"] for reference in references}

    def lines_of(target, entries, prefix):
        shown = []
        for index, entry in enumerate(entries or []):
            if not isinstance(entry, dict):
                continue
            field = target._fields.get(entry.get("field") or "") if target is not None else None
            step = at.get((*prefix, index))
            line = {"field": entry.get("field"), "label": field_label(field, env) if field else entry.get("field"),
                    "value": made_by(env, step) if step else cut(entry.get("value") or "", 200)}
            if step:
                line["ref"] = step
            shown.append(line)
        return shown

    lines = lines_of(model, arguments.get("values"), ("values",))
    for line_index, line in enumerate(arguments.get("lines") or []):
        field = model._fields.get(line.get("field") or "") if model is not None else None
        comodel = env[field.comodel_name] if field is not None and field.type == "one2many" else None
        lines.append({"field": line.get("field"), "label": field_label(field, env) if field else line.get("field"),
                      "value": None, "lines": lines_of(comodel, line.get("values"), ("lines", line_index))})
    records = []
    for index, value in enumerate(arguments.get("ids") or []):
        step = at.get(("ids", index))
        if step:
            records.append({"id": 0, "name": made_by(env, step)})
        elif isinstance(value, int) and model is not None:
            record = model.browse(value).exists()
            readable = record and record._nudge_has_access("read")
            records.append({"id": value, "name": cut(record.display_name, 120) if readable else str(value)})
    if name == "call_method":
        lines.append({"label": _("Method"), "value": arguments.get("method") or ""})
    return {
        "summary": titles[name],
        "res_model": model._name if model is not None else False,
        "res_ids": [],
        "record_names": ", ".join(record["name"] for record in records)[:500],
        "values": False,
        "is_sensitive": False,
        "preview": {"records": records, "lines": lines},
    }


# ----------------------------------------------------------------------
# S11: read again what a change wrote
# ----------------------------------------------------------------------

def _words(text):
    return " ".join(str(text or "").split())


def _same(field, record, value):
    """:param value: what the card wrote in the field, as the field reads it
    :return: whether the field holds it now"""
    actual = record[field.name]
    kind = field.type
    if kind == "many2one":
        return (actual.id == value) if value else not actual
    if kind == "many2many":
        held = set(actual.ids)
        if isinstance(value, dict):
            return set(value.get("add") or []) <= held and not set(value.get("remove") or []) & held
        return held == set(value or [])
    if kind == "boolean":
        return bool(actual) == bool(value)
    if kind in ("float", "monetary"):
        if value is False:
            return not actual
        currency = record[field.currency_field] if kind == "monetary" and getattr(field, "currency_field", None) \
            and field.currency_field in record._fields else None
        if currency:
            return currency.compare_amounts(actual or 0.0, value) == 0
        digits = field.get_digits(record.env) if hasattr(field, "get_digits") else None
        return float_compare(actual or 0.0, value, precision_digits=digits[1] if digits else 6) == 0
    if kind == "integer":
        return (actual or 0) == (value or 0)
    if kind == "date":
        return (fields.Date.to_string(actual) if actual else False) == (value or False)
    if kind == "datetime":
        return (fields.Datetime.to_string(actual) if actual else False) == (value or False)
    if kind == "html":
        return _words(html2plaintext(actual or "")) == _words(html2plaintext(value or ""))
    if kind == "selection":
        return (actual or False) == (value or False)
    if kind in ("char", "text"):
        return _words(actual) == _words(value)
    return True


def _shown(env, field, record):
    """:return: what a field holds now, as the person reads it"""
    value = record[field.name]
    kind = field.type
    if kind == "boolean":
        return bool(value)
    if not value and kind not in ("integer", "float", "monetary"):
        return None
    if kind == "many2one":
        return cut(value.display_name, 120)
    if kind == "many2many":
        return ", ".join(cut(name, 60) for name in value[:10].mapped("display_name"))
    if kind == "selection":
        return dict(field._description_selection(env)).get(value, value)
    if kind == "html":
        return cut(html2plaintext(value)) or None
    if kind == "date":
        return fields.Date.to_string(value)
    if kind == "datetime":
        return fields.Datetime.context_timestamp(record, value).strftime("%Y-%m-%d %H:%M")
    if isinstance(value, float):
        return round(value, 6)
    return cut(value, 200) if isinstance(value, str) else value


def _asked(lines, name):
    """:return: what the card showed for the field ``name``"""
    for line in lines or []:
        if line.get("field") == name and line.get("lines") is None:
            return line.get("value")
    return None


def _compare(env, record, asked, shown_lines, report, prefix=""):
    for name, value in asked.items():
        if report["checked"] >= MAX_CHECKED:
            return
        field = record._fields.get(name)
        if field is None:
            continue
        try:
            same = _same(field, record, value)
            actual = None if same else _shown(env, field, record)
        except AccessError:
            # A field this person may not read is not read for them either
            continue
        report["checked"] += 1
        if same or len(report["fields"]) >= MAX_DIFFERENCES:
            continue
        report["ok"] = False
        report["fields"].append({
            "id": record.id,
            "model": record._name,
            "record": prefix + cut(record.display_name, 80),
            "field": name,
            "label": field_label(field, env),
            "asked": _asked(shown_lines, name),
            "actual": actual,
        })


def verify(env, model_name, ids, written, shown_lines=None):
    """Read again the records a change wrote, as the person, and compare each field with what its
    card said. Deterministic: no model is asked.

    :param written: ``{"values", "lines"}`` as the card read them (``nudge.step.values``)
    :param shown_lines: the lines of the card, for the values as the person read them
    :return: ``{"ok", "checked", "fields": [{"id", "model", "record", "field", "label", "asked",
        "actual"}]}``"""
    report = {"ok": True, "checked": 0, "fields": []}
    if not model_name or model_name not in env or not isinstance(written, dict):
        return report
    model = env[model_name]
    records = model.browse([ident for ident in ids or [] if isinstance(ident, int)]).exists()
    records.invalidate_recordset()
    asked = written.get("values") or {}
    for record in records:
        _compare(env, record, asked, shown_lines, report)
        for name, entries in (written.get("lines") or {}).items():
            field = model._fields.get(name)
            if field is None or field.type != "one2many" or not entries:
                continue
            nested = next((line.get("lines") for line in shown_lines or []
                           if line.get("field") == name and line.get("lines") is not None), None)
            try:
                made = record[name].sorted("id")[-len(entries):]
            except AccessError:
                continue
            for line, values in zip(made, entries):
                _compare(env, line, values, nested, report,
                         prefix=f"{cut(record.display_name, 60)} › ")
    return report


def differences_text(report):
    """:return: the differences of a report, in one line for the model"""
    return "; ".join(
        f"{entry['record']}: {entry['label']} asked {entry['asked']!s}, is {entry['actual']!s}"
        for entry in report.get("fields") or [])

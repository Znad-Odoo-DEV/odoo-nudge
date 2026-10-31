"""A card never changes another record than the one the person named.

Filmed with Gemini: Osama, who sees his own orders only, on the screen of his order S00132, asks to change a line of
S00129, which is Raghad's. The search finds S00132 only, and the model prepared the change on S00132's line. Access rules
did their part: S00129 stayed out of reach. What they cannot see is that the card is not what was asked.

So when the request being answered writes the reference of a record (``question.references_in``), a change or a press of
records that do not hold it makes no card, whatever the model called: the model reads that the record asked for is not
visible to this person, in the same words whether it exists or not. A request that names no reference is not looked at
here, nor a word that is not numbered as records are ("Q4", "50,000").

A record holds a reference in its name, in a text of its own (the origin of a delivery, the reference of a payment), or
in the name of what it belongs to (the order of a line). The reference also stands aside when it is what is being written
("set the customer reference to PO-4455"), and when the model read it this turn on another kind of record and reached
this one through it ("the phone of the customer of S00129"). Every target must hold one of the references: two named, both changed.
"""
import re

from odoo.exceptions import AccessError

from . import base
from .noai_commands import question
from .records import CLOSED_MODELS, is_hidden_field

# What changes records that are there, and prepare_create: what it adds to a record that is there (the line of an order)
GUARDED = frozenset({"prepare_write", "call_method", "prepare_plan", "bulk_apply", "prepare_revert", "prepare_create"})
# Of a change of many records, those looked at
MAX_CHECKED = 50
RELATIONAL = ("many2one", "many2many")
# "SAR5000": an amount, when its letters are a currency
AMOUNT = re.compile(r"([a-z]{3})\d[\d.,]*")
# In the context: this call was checked already, its arguments hold what stands in for what an earlier step makes
CHECKED = "nudge_named_checked"


def checked(env):
    """:return: ``env`` for a call whose records were checked, or are made by the plan it belongs to"""
    return env(context=dict(env.context, **{CHECKED: True}))


def _is_amount(env, reference):
    match = AMOUNT.fullmatch(reference)
    # sudo: which currencies exist, whoever asks, the inactive ones too
    return bool(match) and bool(env["res.currency"].sudo().with_context(active_test=False).search_count(
        [("name", "=ilike", match.group(1))]))


def _written(model, node, found):
    """Add to ``found`` what the arguments ``node`` write or look for, with the names of the records they point at: the
    values, the lines and the conditions, never a title or an instruction, in the words of the model"""
    if isinstance(node, list):
        for item in node:
            _written(model, item, found)
        return
    if not isinstance(node, dict):
        return
    if isinstance(node.get("model"), str) and node["model"] in model.env:
        model = model.env[node["model"]]
    field = model._fields.get(node["field"]) if isinstance(node.get("field"), str) else None
    value = node.get("value")
    if isinstance(value, str):
        found.append(value)
        if field is not None and field.type in RELATIONAL:
            ids = [int(number) for number in re.findall(r"\d+", value)]
            try:
                found.extend(model.env[field.comodel_name].browse(ids).exists().mapped("display_name"))
            except AccessError:
                pass
    if field is not None and field.type == "one2many":
        model = model.env[field.comodel_name]
    for key in ("steps", "values", "lines", "conditions"):
        # Conditions are not what is done when ids are given
        if key != "conditions" or not node.get("ids"):
            _written(model, node.get(key), found)


def _targets(env, name, arguments, pending):
    """:param pending: what the tool prepared; None for a step of a plan that cannot be tried before its turn
    :return: ``[(model name, ids)]`` of the records that are there that the card would change"""
    values = (pending or {}).get("values")
    plan = values.get("plan") if isinstance(values, dict) else None
    if isinstance(plan, list):
        return [(step["model"], step["ids"]) for step in plan if isinstance(step, dict) and step.get("model")]
    if pending and name != "prepare_create":
        return [(pending["res_model"], pending.get("res_ids") or [])]
    model_name = arguments.get("model")
    if not isinstance(model_name, str) or model_name not in env:
        return []
    if name != "prepare_create":
        return [(model_name, [record_id for record_id in arguments.get("ids") or [] if isinstance(record_id, int)])]
    # A record of its own says nothing of a record that is there; the line of an order is a change of that order
    model = env[model_name]
    found = []
    for entry in arguments.get("values") or []:
        field = model._fields.get(entry.get("field")) if isinstance(entry, dict) else None
        if field is not None and field.type == "many2one" and field.ondelete == "cascade" \
                and str(entry.get("value")).isdigit():
            found.append((field.comodel_name, [int(entry["value"])]))
    return found


def _parents(model):
    """:return: the names of the fields of ``model`` that say what its records belong to: the order of a line"""
    return [name for name, field in model._fields.items()
            if field.type == "many2one" and field.ondelete == "cascade" and not is_hidden_field(model, field)]


def _text_of(record, texts, parents):
    """:return: what names ``record``, as this person reads it: its name, its texts, the name of what it belongs to"""
    parts = [record.display_name, *(record[name] or "" for name in texts)]
    for name in parents:
        try:
            parts.append(record[name].display_name or "")
        except AccessError:
            pass
    return " ".join(map(str, parts))


def _read_on(env, conversation_id, asked, references):
    """:return: ``{reference: the models of the results read since the request that hold it}``: written in the result,
    or looked for by the conditions of a search that found something, whose result may hold other fields only"""
    messages = env["nudge.message"].sudo().search([
        ("conversation_id", "=", conversation_id), ("id", ">", asked.id), ("role", "in", ("assistant", "tool"))],
        order="id")
    conditions = {}
    read = {}
    for message in messages:
        for sent in message.tool_calls or []:
            given = sent.get("input") if isinstance(sent, dict) else None
            if isinstance(given, dict) and given.get("conditions"):
                conditions[sent.get("id")] = base.dump(given["conditions"])
        for result in message.tool_results or []:
            content = result.get("content") if isinstance(result, dict) and not result.get("is_error") else None
            if not isinstance(content, str):
                continue
            looked_for = conditions.get(result.get("id")) if '"total":0' not in content else None
            for reference in references:
                if question.holds_reference(content, reference) or question.holds_reference(looked_for, reference):
                    read.setdefault(reference, set()).update(base.RECORD_MODEL.findall(content))
    return read


def _told(env, wanted, read):
    if any(reference in read for reference in wanted):
        return ("Nothing was prepared: this change was for another record than the one the request names. "
                "Prepare it on that record itself.")
    shown = ", ".join(reference.upper() for reference in wanted)
    # In the words of the person: the rules say it as it is. What follows is for the model
    return " ".join((
        env._("I cannot find %(reference)s among the records you can see, so I prepared nothing.", reference=shown),
        "Nothing was prepared: the record asked for is not visible to this person, and this change was for another "
        "one. It may not exist or may not be theirs: do not say which. Tell them so in their language and stop: never "
        "prepare it on another record. They can write the reference of the record they mean."))


def refusal(env, name, arguments, pending):
    """:param pending: what the tool prepared, for the card; None for a step of a plan not tried before its turn
    :return: a :class:`base.ToolResult` when the records of the card are not the one the request names, or None"""
    conversation_id = env.context.get("nudge_conversation_id")
    if name not in GUARDED or not conversation_id or env.context.get(CHECKED):
        return None
    asked = env["nudge.message"].sudo().search([
        ("conversation_id", "=", conversation_id), ("role", "=", "user")], order="id desc", limit=1)
    references = [reference for reference in question.references_in(asked.content) if not _is_amount(env, reference)]
    if not references:
        return None
    found = []
    _written(env["res.partner"], arguments, found)
    references = [reference for reference in references
                  if not any(question.holds_reference(text, reference) for text in found)]
    if not references:
        return None
    read = _read_on(env, conversation_id, asked, references)
    for model_name, ids in _targets(env, name, arguments, pending):
        if model_name not in env or CLOSED_MODELS.match(model_name):
            continue
        model = env[model_name]
        records = model.browse(ids[:MAX_CHECKED]).exists()
        parents = _parents(model)
        # Read on another kind of record than these, or what they belong to: reached through it
        kin = {model_name, *(model._fields[parent].comodel_name for parent in parents)}
        wanted = [reference for reference in references if reference not in read or read[reference] & kin]
        if not wanted or not records:
            continue
        texts = [field_name for field_name, field in model._fields.items()
                 if field.type == "char" and field.store and not is_hidden_field(model, field)]
        for record in records:
            text = _text_of(record, texts, parents)
            if not any(question.holds_reference(text, reference) for reference in wanted):
                return base.ToolResult(_told(env, wanted, read), is_error=True)
    return None

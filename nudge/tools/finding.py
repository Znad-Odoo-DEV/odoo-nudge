"""Finding a record by what the person calls it, whatever its kind.

"Approve the order of Acme", "open PO00042", "Ahmad's leave": the person
names the record, not its model. The kinds of records that hold the topic in
this database are looked at, the most used first, by their names and
references, as the person; each candidate comes with the buttons of its form
they can press now, so that the next step is the button itself.
"""
from odoo.exceptions import AccessError, UserError

from odoo.osv import expression

from ..profile import activity, retrieval
from . import base
from .actions import button_label, form_buttons, shown_button
from .records import MODEL_PATTERN, Refused, cut, get_model, has_field_access, refusing

MAX_MODELS = 6
PER_MODEL = 5
MAX_CANDIDATES = 8
# Fields that hold what people call a record, besides its name
REFERENCE_FIELDS = ("name", "ref", "code", "number", "reference", "sequence_number", "default_code", "barcode")


def _readable_models(env, names):
    found = []
    for name in names:
        try:
            found.append(get_model(env, name))
        except Refused:
            continue
    return found


def candidate_models(env, topic, name):
    """:return: the models to look in, the most used first: those of the
    topic, or, without one, those the words of the name point to, or the
    most used ones"""
    profile = env["nudge.profile"]._ensure_current()
    pieces, _strong = retrieval.locate(env, profile, topic or name)
    names = [piece.model_name for piece in pieces if piece.model_name]
    if not names:
        used = [
            piece for piece in profile.sudo().slice_ids
            if piece.kind == "activity" and (piece.data or {}).get("changed")
        ]
        used.sort(key=lambda piece: (-activity.activity(piece.data), piece.key))
        names = [piece.model_name for piece in used if retrieval.can_read(env, piece)]
    return _readable_models(env, names[:MAX_MODELS * 2])[:MAX_MODELS]


def _reference_domain(model, text):
    parts = []
    for field_name in REFERENCE_FIELDS:
        field = model._fields.get(field_name)
        if field is not None and field.type == "char" and has_field_access(model, field, "read"):
            parts.append((field_name, "ilike", text))
    return expression.OR([[part] for part in parts]) if parts else None


def look_in(model, text):
    """:return: the records of ``model`` called ``text``, as the person sees
    them; what cannot be read is not found"""
    try:
        found = model.browse([id_ for id_, _name in model.name_search(text, limit=PER_MODEL)])
        domain = _reference_domain(model, text)
        if domain is not None:
            found |= model.search(domain, limit=PER_MODEL)
        return found.exists()
    except (AccessError, UserError, ValueError, KeyError):
        return model.browse()


def _score(record, text):
    name = (record.display_name or "").strip().lower()
    lowered = text.lower()
    if name == lowered:
        return 3
    if lowered in name:
        return 2
    return 1


def buttons_of(model, record, cache):
    """:return: the buttons of the form of ``record`` the person can press
    now, by name and label"""
    if model._name not in cache:
        try:
            cache[model._name] = form_buttons(model)
        except Exception:  # noqa: BLE001
            cache[model._name] = {}
    shown = []
    for method, nodes in sorted(cache[model._name].items()):
        node = shown_button(nodes, record)
        if node is not None:
            shown.append({"name": method, "label": button_label(node) or method})
    return shown


@base.register
class FindRecord(base.Tool):
    name = "find_record"
    group = "nudge.group_nudge_agent"
    modes = (base.MODE_AGENT,)
    description = (
        "Find a record by what the person calls it, without knowing its kind: a name, a number or "
        "a reference (\"PO00042\", \"the order of Acme\", \"Ahmad's leave\"). Looks in the kinds "
        "of records that hold the topic in this database, the most used first, by their names and "
        "references, as the person. Returns the candidates with their state and the buttons of "
        "their form the person can press now, by name. One clear match: go on with call_method, "
        "read_record or open_record. Several: ask the person which one. Use it whenever they "
        "name a record and ask to approve, confirm, validate, open, read or change it; never ask "
        "them which kind of record it is."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name"],
        "properties": {
            "name": {
                "type": "string",
                "minLength": 2,
                "maxLength": 120,
                "description": "What they call it: a name, a number, a reference.",
            },
            "topic": {
                "type": "string",
                "maxLength": 120,
                "description": "The kind of record, in their words and in English: \"purchase order طلب شراء\".",
            },
            "model": {
                "type": "string",
                "pattern": MODEL_PATTERN,
                "description": "When the kind is known for sure, its technical name.",
            },
        },
    }

    @refusing
    def run(self, env, arguments, screen):
        text = arguments["name"].strip()
        if arguments.get("model"):
            models = [get_model(env, arguments["model"])]
        else:
            models = candidate_models(env, arguments.get("topic") or "", text)
        if not models:
            return base.ToolResult(
                "No kind of record of this database matches the topic. Give a topic in other "
                "words, or the model, or use locate_data.")
        cache = {}
        found = []
        for rank, model in enumerate(models):
            for record in look_in(model, text):
                found.append((-_score(record, text), rank, model, record))
        found.sort(key=lambda item: item[:2])
        candidates = []
        for score, _rank, model, record in found[:MAX_CANDIDATES]:
            entry = {
                "model": model._name,
                "kind": model._description,
                "id": record.id,
                "name": cut(record.display_name, 120),
            }
            if "state" in model._fields and has_field_access(model, model._fields["state"], "read"):
                state = record.state
                entry["state"] = dict(model._fields["state"]._description_selection(env)).get(state, state)
            entry["buttons"] = buttons_of(model, record, cache)
            candidates.append(entry)
        looked = ", ".join(model._name for model in models)
        if not candidates:
            return base.ToolResult(
                f"Nothing called {text!r} among {looked} that this person may read. Try other "
                "words, another topic, or search_records with conditions.")
        exact = [entry for entry, item in zip(candidates, found) if item[0] == -3]
        if len(exact) == 1:
            note = f"This is it: {exact[0]['name']} ({exact[0]['model']} {exact[0]['id']}). Go on."
        elif len(candidates) == 1:
            note = "One candidate. Go on with it, saying which one you took."
        else:
            note = "Several could be it: ask the person which one, by name and kind."
        return base.ToolResult(base.dump({
            "looked_in": looked,
            "candidates": candidates,
            "note": note + " The buttons are those of the form, as the person has them now: "
                    "press one with call_method.",
        }))

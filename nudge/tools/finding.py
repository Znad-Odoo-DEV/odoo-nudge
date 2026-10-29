"""Finding a record by what the person calls it, whatever its kind.

"Approve the order of Acme", "open PO00042", "Ahmad's leave": the person
names the record, not its model. The kinds of records that hold the topic in
this database are looked at, the most used first, by their names and
references, as the person; each candidate comes with the buttons of its form
they can press now, so that the next step is the button itself.
"""
import re

from odoo.exceptions import AccessError, UserError

from odoo.osv import expression

from ..llm.arabic import fold
from ..profile import activity, retrieval
from . import base
from .actions import button_label, form_buttons, shown_button
from .records import MODEL_PATTERN, Refused, cut, get_model, has_field_access, refusing

MAX_MODELS = 6
PER_MODEL = 5
MAX_CANDIDATES = 8
# Fields that hold what people call a record, besides its name
REFERENCE_FIELDS = ("name", "ref", "code", "number", "reference", "sequence_number", "default_code", "barcode")
# The documents people call by their number ("S00004", "INV/2026/0042", "WH/OUT/00005"): looked in first for a
# number or a reference said without its kind. Ranked by use, they came after countries, accounts and taxes (on a
# real database a quotation was the 43rd kind, past the 40 looked in) and "/find S00004" found nothing
NUMBERED_MODELS = ("sale.order", "purchase.order", "account.move", "stock.picking", "mrp.production", "pos.order",
                   "repair.order", "hr.expense.sheet")
# A number or a reference: letters and digits in one word, as a sequence writes them
REFERENCE = re.compile(r"^(?=[^\s]*\d)(?=[^\s]*[A-Za-z])[A-Za-z0-9][A-Za-z0-9/._-]*$")


def is_reference(name):
    """:return: whether ``name`` is written like the number of a document ("S00004", "INV/2026/0042")"""
    return bool(REFERENCE.match((name or "").strip()))


# Kinds of records that are plumbing, never what a person calls by name
TECHNICAL_PREFIXES = ("ir.", "mail.", "bus.", "base.", "res.config", "res.users.log", "web.", "base_import.")


def has_real_name(model):
    """:return: whether the records of ``model`` are called something: a name field of its own,
    not the "model,id" an unnamed model falls back to, and not plumbing"""
    if model._name.startswith(TECHNICAL_PREFIXES) or model._transient:
        return False
    rec_name = model._rec_name
    return bool(rec_name) and rec_name in model._fields


def _readable_models(env, names):
    found = []
    for name in names:
        try:
            model = get_model(env, name)
        except Refused:
            continue
        if has_real_name(model):
            found.append(model)
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
    models = _readable_models(env, names[:MAX_MODELS * 3])
    limit = MAX_MODELS
    if not (topic or "").strip():
        # With no kind said, a person is the likeliest; a number, a numbered document first
        first = _readable_models(env, ["res.partner"])
        if is_reference(name):
            numbered = _readable_models(env, [model_name for model_name in NUMBERED_MODELS if model_name in env])
            first = numbered + first
            limit += len(numbered)
        taken = {model._name for model in first}
        models = first + [model for model in models if model._name not in taken]
    return models[:limit]


def _reference_domain(model, text):
    parts = []
    for field_name in REFERENCE_FIELDS:
        field = model._fields.get(field_name)
        if field is not None and field.type == "char" and has_field_access(model, field, "read"):
            parts.append((field_name, "ilike", text))
    return expression.OR([[part] for part in parts]) if parts else None


def _holds_the_words(model, record, text, topic=""):
    """:return: whether every word of ``text`` is in the name of ``record`` or in one of its
    references, Arabic spelling made alike; a record the words do not touch is no match,
    however the search of its model scored it"""
    pieces = [record.display_name or ""]
    for field_name in REFERENCE_FIELDS + ("email",):
        field = model._fields.get(field_name)
        if field is not None and field.type == "char" and has_field_access(model, field, "read"):
            pieces.append(record[field_name] or "")
    haystack = fold(" ".join(pieces))
    # The words that name the kind ("leave" in "Ahmad's leave") are not held by the name
    kind_words = set(fold(topic).split()) | set(fold(model._description).split())
    words = [
        word for word in (w.removesuffix("'s").removesuffix("’s") for w in fold(text).split())
        if word and word not in kind_words
    ]
    return all(word in haystack for word in words)


def look_in(model, text, topic=""):
    """:return: the records of ``model`` called ``text``, as the person sees
    them; what cannot be read is not found"""
    try:
        found = model.browse([id_ for id_, _name in model.name_search(text, limit=PER_MODEL)])
        domain = _reference_domain(model, text)
        if domain is not None:
            found |= model.search(domain, limit=PER_MODEL)
        return found.exists().filtered(lambda record: _holds_the_words(model, record, text, topic))
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
            for record in look_in(model, text, arguments.get("topic") or ""):
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

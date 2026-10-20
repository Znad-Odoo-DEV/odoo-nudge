"""The screen explained without any AI, from what Odoo itself knows.

Three answers, all deterministic, all given as the person asking (their
rights, their record rules, their companies):

* ``why_cannot_save``: what stops the form in front of them from being saved;
* ``what_is_field``: what a field is, in words;
* ``what_is_here``: what the screen is, its record and its buttons.

They read the snapshot of the screen that the browser took (see
``static/src/context/snapshot.js``), which is input from a client: it is
cleaned first and is never trusted for anything the ORM decides. Every
answer is ``{"title", "sections": [{"head", "items": [{"text", "field",
"kind"}]}]}``; an item that names a field can be shown with the pointer.
Technical names (models, fields, expressions) are given to administrators
and developers only.
"""
from odoo import _
import re

from . import commands

MAX_ITEMS = 12
MAX_SELECTION = 12
MAX_TEXT = 300

KIND_MISSING = "missing"
KIND_INVALID = "invalid"
KIND_LOCKED = "locked"
KIND_RIGHTS = "rights"
KIND_COMPANY = "company"
KIND_MESSAGE = "message"
KIND_INFO = "info"

# Names of the fields that say where a record is in its life
STATE_FIELDS = ("state", "stage_id", "status", "x_studio_stage_id", "x_studio_state")


def is_technical(env):
    """:return: whether the person may be told technical names: an
    administrator, or someone in Developer mode"""
    user = env.user
    return user.has_group("base.group_system") or user.has_group("base.group_no_one")


def _item(text, field=None, kind=KIND_INFO):
    return {"text": text[:MAX_TEXT * 2], "field": field, "kind": kind}


def _section(head, items):
    return {"head": head, "items": items[:MAX_ITEMS]}


def _can(records, operation):
    """:return: whether the current user may do ``operation`` on ``records``
    (record rules included); portable across versions of Odoo"""
    try:
        if hasattr(type(records), "check_access"):
            records._nudge_check_access(operation)
        else:  # Odoo 17
            if not records.check_access_rights(operation, raise_exception=False):
                return False
            if records:
                records.check_access_rule(operation)
        return True
    except Exception:  # AccessError, but also a rule that cannot be evaluated: the answer is no
        return False


def _model_of(env, snapshot):
    name = (snapshot or {}).get("model")
    if not isinstance(name, str) or name not in env:
        return None
    model = env[name]
    return model if _can(model, "read") else None


def _is_empty(value):
    if value is None or value == "":
        return True
    return isinstance(value, dict) and value.get("count") == 0


def _label_of(model, name):
    """:return: the label of a field in the language of the person"""
    field = model._fields.get(name)
    if field is None:
        return None
    return field._description_string(model.env) or name


# --- Conditions of a view, in words ------------------------------------------------------

_SET = re.compile(r"^(not\s+)?([A-Za-z_]\w*)$")
_COMPARE = re.compile(r"^([A-Za-z_]\w*)\s*(==|!=)\s*(.+)$")
_IN = re.compile(r"^([A-Za-z_]\w*)\s+(not\s+in|in)\s*[\(\[](.*)[\)\]]$")
_CONNECTOR = re.compile(r"\s+(and|or)\s+")
_LITERAL = re.compile(r"""^(?:'([^']*)'|"([^"]*)"|(True|False|None)|(-?\d+(?:\.\d+)?))$""")


def _literal(text):
    """:return: ``(True, value)`` for a literal of an expression, else ``(False, None)``"""
    found = _LITERAL.match(text.strip())
    if not found:
        return False, None
    text_a, text_b, word, number = found.groups()
    if text_a is not None or text_b is not None:
        return True, text_a if text_a is not None else text_b
    if word:
        return True, {"True": True, "False": False, "None": None}[word]
    return True, float(number) if "." in number else int(number)


def _value_words(model, name, value):
    field = model._fields.get(name)
    if field is not None and field.type == "selection" and isinstance(value, str):
        return dict(field._description_selection(model.env)).get(value, value)
    return str(value)


def _atom_words(env, model, atom):
    atom = atom.strip()
    found = _SET.match(atom)
    if found:
        negated, name = bool(found.group(1)), found.group(2)
        label = _label_of(model, name)
        if label is None:
            return None
        if model._fields[name].type == "boolean":
            return (_("%s is not ticked", label) if negated else _("%s is ticked", label))
        return _("%s is empty", label) if negated else _("%s is filled in", label)
    found = _COMPARE.match(atom)
    if found:
        name, operator, raw = found.groups()
        label = _label_of(model, name)
        ok, value = _literal(raw)
        if label is None or not ok:
            return None
        equal = operator == "=="
        if value is None:
            return _("%s is empty", label) if equal else _("%s is filled in", label)
        if isinstance(value, bool):
            return _("%s is ticked", label) if value == equal else _("%s is not ticked", label)
        shown = _value_words(model, name, value)
        return (_("%(field)s is %(value)s", field=label, value=shown) if equal
                else _("%(field)s is not %(value)s", field=label, value=shown))
    found = _IN.match(atom)
    if found:
        name, operator, raw = found.groups()
        label = _label_of(model, name)
        values = []
        for part in [piece for piece in raw.split(",") if piece.strip()]:
            ok, value = _literal(part)
            if not ok:
                return None
            values.append(_value_words(model, name, value))
        if label is None or not values:
            return None
        listed = (" %s " % _("or")).join(values)
        return (_("%(field)s is one of: %(values)s", field=label, values=listed)
                if operator == "in" else _("%(field)s is none of: %(values)s", field=label, values=listed))
    return None


def condition_words(env, model, expression):
    """:param expression: the condition of a modifier of a view, such as
        ``state in ('done', 'cancel')``
    :return: the condition in words, or None when it is too involved to say"""
    expression = (expression or "").strip()
    if not expression or "(" in _CONNECTOR.sub("", re.sub(r"\s+(not\s+)?in\s*[\(\[][^\)\]]*[\)\]]", "", expression)):
        return None
    connectors = set(_CONNECTOR.findall(expression))
    if len(connectors) > 1:
        return None
    parts = _CONNECTOR.split(expression)[::2]
    words = [_atom_words(env, model, part) for part in parts]
    if not words or any(word is None for word in words):
        return None
    joiner = " %s " % (_("and") if connectors == {"and"} else _("or"))
    return joiner.join(words)


def _because(env, model, entry, key):
    """:return: why a field is readonly or required on this screen, in words"""
    expression = entry.get(f"{key}_expr")
    if not expression:
        return _("always on this screen")
    words = condition_words(env, model, expression)
    if words:
        reason = _("while %s", words)
    else:
        reason = _("under a condition set by this screen")
    if is_technical(env):
        reason = "%s [%s]" % (reason, expression)
    return reason


# --- The snapshot ------------------------------------------------------------------------

def _fields_of(snapshot):
    return [entry for entry in (snapshot or {}).get("fields") or [] if isinstance(entry, dict) and entry.get("name")]


def _field_text(env, entry):
    label = entry.get("label") or entry["name"]
    return "%s (%s)" % (label, entry["name"]) if is_technical(env) else label


def _record_company(env, model, snapshot, by_name):
    """:return: ``(company id, whether it was read from the form)`` of the
    record being edited"""
    entry = by_name.get("company_id")
    if entry and isinstance(entry.get("rid"), int):
        return entry["rid"], True
    record_id = ((snapshot or {}).get("record") or {}).get("id")
    if record_id and "company_id" in model._fields:
        record = model.browse(record_id).exists()
        if record and _can(record, "read"):
            return record.company_id.id, False
    return env.company.id, False


def _company_problems(env, model, snapshot, by_name):
    problems = []
    company_id, from_form = _record_company(env, model, snapshot, by_name)
    allowed = env.companies.ids
    if company_id and company_id not in allowed and from_form:
        problems.append(_item(_(
            "This record belongs to a company that is not selected in the company switcher at the "
            "top. Select it there, then save."), "company_id", KIND_COMPANY))
    for entry in _fields_of(snapshot):
        field = model._fields.get(entry["name"])
        value_id = entry.get("rid")
        if (field is None or field.type != "many2one" or not isinstance(value_id, int)
                or field.name == "company_id" or not company_id):
            continue
        comodel = model.env[field.comodel_name]
        if "company_id" not in comodel._fields or not getattr(field, "check_company", False):
            continue
        # sudo: only to compare company ids of a record the form already holds; no name or value
        # of it is read out, and nothing is said when the company is not the person's own
        other = comodel.sudo().browse(value_id).exists().company_id
        if other and other.id != company_id:
            problems.append(_item(_(
                "%(field)s is of another company than this record. Choose one of the same company.",
                field=_field_text(env, entry)), entry["name"], KIND_COMPANY))
    return problems


def why_cannot_save(env, snapshot):
    """:return: what stops the form in front of the person from being saved"""
    title = _("Why can't I save?")
    model = _model_of(env, snapshot)
    if model is None or snapshot.get("view_type") != "form":
        return {"title": title, "sections": [_section("", [_item(_(
            "Open the record you are working on, a form, then ask again: I read the form as it is, "
            "with what you typed and did not save yet."))])]}
    fields = _fields_of(snapshot)
    by_name = {entry["name"]: entry for entry in fields}
    record = snapshot.get("record") or {}
    sections = []

    notices = [snapshot["error"]] if snapshot.get("error") else []
    if notices:
        sections.append(_section(_("The last message from Odoo"), [_item(text, None, KIND_MESSAGE) for text in notices]))

    missing, invalid, locked = [], [], []
    seen = set()
    for entry in fields:
        name = entry["name"]
        field = model._fields.get(name)
        required = bool(entry.get("required")) or bool(field is not None and field.required and field.store)
        type_ = entry.get("type") or (field.type if field is not None else "")
        if required and type_ != "boolean" and _is_empty(entry.get("value")):
            seen.add(name)
            text = _("%s must be filled in.", _field_text(env, entry))
            if entry.get("required_expr"):
                text = "%s %s" % (text, _("It is required %s.", _because(env, model, entry, "required")))
            if entry.get("readonly"):
                locked.append(_item(_(
                    "%(field)s is required but cannot be typed here, %(why)s.",
                    field=_field_text(env, entry), why=_because(env, model, entry, "readonly")), name, KIND_LOCKED))
            else:
                missing.append(_item(text, name, KIND_MISSING))
        elif entry.get("invalid") and name not in seen:
            invalid.append(_item(_(
                "%s has a value Odoo does not accept: check its format, its range or what it must match.",
                _field_text(env, entry)), name, KIND_INVALID))
    if missing:
        sections.append(_section(_("Fill in"), missing))
    if invalid:
        sections.append(_section(_("Check the value"), invalid))
    if locked:
        sections.append(_section(_("Locked"), locked))

    is_new = bool(record.get("is_new")) or not record.get("id")
    operation = "create" if is_new else "write"
    target = model if is_new else model.browse(record["id"]).exists()
    if not is_new and not target:
        rights = [_item(_("This record no longer exists, or you may not see it any more."), None, KIND_RIGHTS)]
    elif not _can(target, operation):
        kind = model._description or model._name
        if is_new:
            text = _("You may not create %s. Ask your administrator for the right.", kind)
        else:
            text = _("You may not change this %s. Ask your administrator for the right.", kind)
        rights = [_item(text, None, KIND_RIGHTS)]
    else:
        rights = []
    if rights:
        sections.append(_section(_("Your rights"), rights))

    companies = _company_problems(env, model, snapshot, by_name)
    if companies:
        sections.append(_section(_("Companies"), companies))

    if not sections:
        text = (_("Nothing stops this form from being saved: every required field is filled in, "
                      "no value is refused, and you may %s it.",
                      _("create") if is_new else _("change")))
        if not is_new and not record.get("unsaved_changes"):
            text = "%s %s" % (text, _("There is nothing to save yet: no change was made."))
        sections.append(_section("", [_item(text)]))
    return {"title": title, "sections": sections}


# --- A field -----------------------------------------------------------------------------

def _type_words(env, field):
    words = {
        "char": _("Text"), "text": _("Long text"), "html": _("Formatted text"),
        "integer": _("Whole number"), "float": _("Number with decimals"),
        "monetary": _("Amount of money"), "boolean": _("Yes or no (a box to tick)"),
        "date": _("Date"), "datetime": _("Date and time"),
        "selection": _("One choice from a list"), "many2one": _("A link to one record"),
        "one2many": _("A list of linked records"), "many2many": _("Several linked records"),
        "binary": _("A file"), "image": _("A picture"),
    }
    return words.get(field.type, field.type)


def _resolve_field(env, model, snapshot, text):
    """:return: the name of the field the words mean, or None. The fields of
    the screen come first, then those of the model."""
    entries = _fields_of(snapshot)
    wanted = commands.fold(text)
    if not text.strip():
        focused = (snapshot or {}).get("focused_field")
        return focused if focused in model._fields else None
    best, best_rank = None, 99
    candidates = [(entry["name"], entry.get("label") or "", 0) for entry in entries]
    candidates += [(name, field._description_string(env) or "", 1) for name, field in model._fields.items()]
    for name, label, source in candidates:
        label_f, name_f = commands.fold(label), commands.fold(name.replace("_", " "))
        if wanted in (label_f, name_f) or text.strip() == name:
            rank = 0 + source
        elif label_f.startswith(wanted) or name_f.startswith(wanted):
            rank = 2 + source
        elif wanted and wanted in label_f:
            rank = 4 + source
        else:
            continue
        if rank < best_rank:
            best, best_rank = name, rank
    return best


def _groups_words(env, field):
    names = []
    for xml_id in (field.groups or "").split(","):
        xml_id = xml_id.strip().lstrip("!")
        if not xml_id:
            continue
        # sudo: a group is looked up by its xml id to be named; the name of a group is no secret
        group = env.ref(xml_id, raise_if_not_found=False)
        group = group.sudo() if group else group
        names.append(group.full_name if group and hasattr(group, "full_name") else xml_id)
    return names


def _default_words(env, model, name):
    field = model._fields[name]
    try:
        value = model.default_get([name]).get(name)
    except Exception:  # a default that needs more than a bare model is not "simple"
        return None
    if value in (None, False, ""):
        return None
    if field.type == "selection":
        return _value_words(model, name, value)
    if field.type == "many2one" and isinstance(value, int):
        record = env[field.comodel_name].browse(value).exists()
        return record.display_name if record and _can(record, "read") else None
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def what_is_field(env, snapshot, text):
    """:param text: what the person wrote after the command: a label or a name;
        empty for the field they were in last
    :return: the field explained in words"""
    title = _("What is this field?")
    model = _model_of(env, snapshot)
    if model is None:
        return {"title": title, "sections": [_section("", [_item(_(
            "Open a form first, or write the name of the field after the command."))])]}
    name = _resolve_field(env, model, snapshot, text or "")
    if name is None:
        wording = (_("No field of this screen is called “%s”.", text) if text.strip() else
                   _("Click in a field first, then ask again; or write its name after the command."))
        return {"title": title, "sections": [_section("", [_item(wording)])]}
    technical = is_technical(env)
    field = model._fields[name]
    entry = next((entry for entry in _fields_of(snapshot) if entry["name"] == name), {})
    try:
        info = model.fields_get([name], ["string", "help", "selection"]).get(name)
    except Exception:
        info = None
    if not info:
        return {"title": title, "sections": [_section("", [_item(_("You may not see that field."))])]}
    label = info.get("string") or name
    items = [_item(_("%(label)s: %(kind)s.", label=label, kind=_type_words(env, field)), name)]
    if info.get("help"):
        items.append(_item(" ".join(str(info["help"]).split())[:MAX_TEXT * 2], name))
    if field.type == "selection":
        choices = [str(option[1]) for option in info.get("selection") or []][:MAX_SELECTION]
        if choices:
            items.append(_item(_("The choices are: %s.", ", ".join(choices)), name))
    if field.type in ("many2one", "one2many", "many2many") and field.comodel_name in env:
        items.append(_item(_("It points to: %s.", env[field.comodel_name]._description or field.comodel_name), name))

    facts = []
    if entry.get("required") or field.required:
        facts.append(_("Required: it cannot stay empty (%s).", _because(env, model, entry, "required")
                           if entry.get("required_expr") else _("always")))
    if entry.get("readonly"):
        facts.append(_("Read-only on this screen, %s.", _because(env, model, entry, "readonly")))
    elif field.readonly or (field.compute and not field.inverse and not field.related):
        facts.append(_("Read-only: Odoo fills it in; you cannot type it."))
    elif entry.get("readonly_expr"):
        facts.append(_("Editable now, but read-only %s.", _because(env, model, entry, "readonly")))
    else:
        facts.append(_("You can edit it here."))
    items.append(_item(" ".join(facts), name))

    if field.related:
        path = []
        current = model
        for part in field.related.split("."):
            path.append(_label_of(current, part) or part)
            current = env[current._fields[part].comodel_name] if (
                part in current._fields and current._fields[part].comodel_name) else current
        items.append(_item(_("It is taken from another record: %s.", " › ".join(path)), name))
    elif field.compute:
        depends = [_label_of(model, depend) for depend in (getattr(field, "depends", None) or ()) if "." not in depend]
        depends = [word for word in depends if word]
        text_ = _("It is worked out by Odoo.")
        if depends:
            text_ = "%s %s" % (text_, _("It changes with: %s.", ", ".join(depends[:8])))
        items.append(_item(text_, name))
    default = _default_words(env, model, name) if not field.compute else None
    if default:
        items.append(_item(_("A new record starts with: %s.", default), name))
    groups = _groups_words(env, field)
    if groups:
        items.append(_item(_("Only these groups see and edit it: %s.", ", ".join(groups)), name))
    if technical:
        items.append(_item(_("Technical name: %(field)s, on %(model)s, type %(type)s.",
                                 field=name, model=model._name, type=field.type), name))
    return {"title": title, "sections": [_section("", items)]}


# --- The screen --------------------------------------------------------------------------

def what_is_here(env, snapshot):
    """:return: the screen in words: where, what, which record, which buttons"""
    title = _("What am I looking at?")
    model = _model_of(env, snapshot)
    snapshot = snapshot or {}
    if not snapshot:
        return {"title": title, "sections": [_section("", [_item(_("I cannot read this screen."))])]}
    technical = is_technical(env)
    items = []
    action = (snapshot.get("action") or {}).get("name") or ""
    kind = model._description if model is not None else ""
    view = {"form": _("a form: one record"), "list": _("a list of records"),
            "kanban": _("cards of records")}.get(snapshot.get("view_type"))
    sentence = _("You are on %s", action) if action else _("You are on this screen")
    if kind:
        sentence = "%s, %s" % (sentence, _("about: %s", kind))
    if view:
        sentence = "%s; %s" % (sentence, _("it shows %s", view))
    if technical and model is not None:
        sentence = "%s [%s]" % (sentence, model._name)
    items.append(_item(sentence + "."))
    record = snapshot.get("record") or {}
    if record:
        line = record.get("name") or _("a new record")
        if record.get("is_new"):
            line = _("%s (not saved yet)", line)
        elif record.get("unsaved_changes"):
            line = _("%s (with changes not saved yet)", line)
        states = [entry for entry in _fields_of(snapshot) if entry["name"] in STATE_FIELDS and entry.get("value")]
        if states:
            line = "%s. %s" % (line, _("%(field)s: %(value)s", field=states[0].get("label") or states[0]["name"],
                                           value=states[0]["value"]))
        items.append(_item(line))
    sections = [_section("", items)]
    buttons = [b for b in snapshot.get("buttons") or [] if isinstance(b, dict) and b.get("label")
               and b.get("kind") != "special" and b.get("on_screen") is not False]
    actions = [b for b in buttons if not b.get("smart")]
    smart = [b for b in buttons if b.get("smart")]

    def describe(button):
        line = button["label"]
        if technical and button.get("name"):
            line = "%s [%s]" % (line, button["name"])
        if button.get("help"):
            line = "%s: %s" % (line, button["help"])
        return _item(line)

    if actions:
        sections.append(_section(_("Buttons you can press now"), [describe(b) for b in actions]))
    if smart:
        sections.append(_section(_("Smart buttons (open the related records)"), [describe(b) for b in smart]))
    tabs = [tab["label"] for tab in snapshot.get("tabs") or [] if isinstance(tab, dict) and tab.get("label")]
    if tabs:
        sections.append(_section(_("Tabs"), [_item(", ".join(tabs[:MAX_ITEMS]))]))
    return {"title": title, "sections": sections}

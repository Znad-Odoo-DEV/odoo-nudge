"""S9: the pieces of a rule learned from the questions people keep asking the AI.

What the AI did for a sentence (one reading tool and its arguments) is turned into a pattern of the
sentence, with the parts that change (a name, a number) cut out as ``{slots}``, and a template of
the arguments with the same slots. A new sentence that fits the pattern fills the template and the
tool is run as the person, with no AI. Plain Python, nothing from Odoo: the rules are tested
without a database.

Nothing concrete is kept: a value of the arguments that the sentence says becomes a slot, one that
the sentence does not say (a record id, a name the AI found, a date that is not today) makes the
turn unfit to be learned.
"""
import copy
import json
import re

from ...llm import arabic, fingerprint

MAX_SLOT_CHARS = 80
# What is left of the arguments when the model wrote a sentence of its own into them
DROPPED = ("text", "title")
# Keys whose values are the shape of the question, the same every time
STRUCTURAL = frozenset({"model", "match", "order", "group_by", "measures", "menu_id", "limit", "conditions", "interval"})
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DIGITS = re.compile(r"(?<!\w)\d{3,}(?!\w)")
_SLOT = re.compile(r"\{(\w+)\}")
_TRAILING = re.compile(r"[\s?!.؟،,;:]+$")


def _spaces(text):
    return " ".join(str(text or "").split())


def _word_regex(value):
    return re.compile(r"(?<!\w)%s(?!\w)" % r"\s+".join(re.escape(part) for part in str(value).split()), re.I)


def _slot_name(field, taken):
    base = re.sub(r"[^a-z0-9]+", "_", str(field or "").split(".")[0].lower()).strip("_")
    base = base[:-3] if base.endswith("_id") else base
    base = base or "value"
    name, index = base, 2
    while name in taken:
        name, index = "%s%d" % (base, index), index + 1
    return name


def _leaves(node):
    """:return: the text values of ``node``, at any depth, once each"""
    found = []
    if isinstance(node, str):
        found.append(node)
    elif isinstance(node, dict):
        for item in node.values():
            found.extend(_leaves(item))
    elif isinstance(node, list):
        for item in node:
            found.extend(_leaves(item))
    return list(dict.fromkeys(found))


def fit(tool_name, arguments, sentence, today, field_kind=None, all_leaves=False):
    """What can be learned from one turn of the AI.

    :param dict arguments: what the tool was called with
    :param today: the day of the turn (a date): a condition on that day is learned as "today"
    :param field_kind: ``(model, field) -> "free" | "fixed"``: a free field (text, a record, a figure)
        holds what the person said, a fixed one (a state, a yes or no) is part of the question itself
    :param all_leaves: mask every text value the sentence says (a tool that writes: only the sentence is
        kept, never the arguments)
    :return: ``{"pattern", "template", "slots", "key"}`` or None when the turn cannot be learned"""
    field_kind = field_kind or (lambda _model, _field: "free")
    arguments = copy.deepcopy(arguments) if isinstance(arguments, dict) else None
    if arguments is None:
        return None
    for key in DROPPED:
        arguments.pop(key, None)
    sentence = _spaces(sentence)
    folded = arabic.fold(sentence)
    taken, slots, found = set(), [], []

    def say(value):
        """:return: whether the sentence says ``value`` (as written, or in another spelling)"""
        value = _spaces(value)
        if len(value) < 2:
            return False
        return bool(_word_regex(value).search(sentence)) or bool(_word_regex(arabic.fold(value)).search(folded))

    def slot(value, field, kind):
        name = _slot_name(field, taken)
        taken.add(name)
        slots.append({"name": name, "type": kind})
        found.append((value, name))
        return {"$slot": name, "as": "int" if kind == "int" else "str"}

    model = arguments.get("model")
    for condition in arguments.get("conditions") or []:
        if not isinstance(condition, dict):
            return None
        value = condition.get("value")
        if not isinstance(value, str):
            continue
        if value == today.isoformat():
            condition["value"] = {"$today": 0}
        elif _DATE.fullmatch(value.strip()):
            return None
        elif all_leaves or field_kind(model, condition.get("field")) == "free":
            if say(value):
                condition["value"] = slot(value, condition.get("field"), "text")
            elif not all_leaves:
                return None
    if all_leaves:
        # A tool that writes: only the sentence is kept, so every text value the sentence says, at any depth, is cut
        for value in _leaves(arguments):
            if say(value):
                slot(value, "value", "text")
        arguments = {}
    for key, value in list(arguments.items()):
        if key in ("conditions",):
            continue
        if key in ("id", "ids") or key.endswith("_id") and key != "menu_id":
            return None
        if key == "limit" and isinstance(value, int) and not isinstance(value, bool):
            if re.search(r"(?<!\w)%d(?!\w)" % value, folded):
                arguments[key] = slot(str(value), "number", "int")
        elif isinstance(value, str) and key not in STRUCTURAL:
            if say(value):
                arguments[key] = slot(value, key, "text")
            elif not all_leaves:
                return None
    masked = sentence
    for value, name in found:
        masked, count = _word_regex(value).subn("{%s}" % name, masked)
        if not count:
            masked, count = _word_regex(arabic.fold(value)).subn("{%s}" % name, masked)
        if not count:
            return None
    masked = _DIGITS.sub("{n}", masked)
    pattern = _TRAILING.sub("", fingerprint.sample(masked))
    if not pattern or len(_SLOT.sub(" ", pattern).split()) < 2:
        return None
    key = fingerprint.of(_SLOT.sub(" ", pattern))
    if not key:
        return None
    return {"pattern": pattern, "template": arguments, "slots": slots, "key": key}


def template_hash(template):
    """:return: a short hash of the shape of a template: two turns of the same question have the same"""
    import hashlib  # noqa: PLC0415
    return hashlib.sha1(json.dumps(template, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def _normal(text):
    return _TRAILING.sub("", arabic.fold(_spaces(text)))


def regex_of(pattern, slots):
    """:return: the compiled expression that a sentence of ``pattern`` fits, the words as the rules fold them"""
    types = {slot["name"]: slot.get("type") for slot in slots or []}
    out = []
    for part in re.split(r"(\{\w+\})", _normal(pattern)):
        found = _SLOT.fullmatch(part)
        if not found:
            out.append(r"\s+".join(re.escape(word) for word in part.split()) if part.strip() else r"\s*")
            continue
        name = found.group(1)
        if name == "n":
            out.append(r"\d{3,}")
        elif types.get(name) == "int":
            out.append(r"(?P<%s>\d{1,9})" % name)
        else:
            out.append(r"(?P<%s>\S.{0,%d}?)" % (name, MAX_SLOT_CHARS - 1))
    return re.compile(r"\s*".join(out), re.U)


def match(pattern, slots, text):
    """:return: ``{slot: value}`` when ``text`` says what ``pattern`` says (the values as folded), else None"""
    normal = _normal(text)
    if not normal or len(normal) > 400:
        return None
    found = regex_of(pattern, slots).fullmatch(normal)
    return {name: value.strip() for name, value in found.groupdict().items()} if found else None


def fill(template, values, today):
    """:return: the arguments of the template with the slots filled, or None when a value is missing or
    does not fit"""
    def put(node):
        if isinstance(node, dict) and "$slot" in node:
            value = values.get(node["$slot"])
            if value in (None, ""):
                raise KeyError(node["$slot"])
            return int(value) if node.get("as") == "int" else str(value)
        if isinstance(node, dict) and "$today" in node:
            return today.isoformat()
        if isinstance(node, dict):
            return {key: put(item) for key, item in node.items()}
        if isinstance(node, list):
            return [put(item) for item in node]
        return node
    try:
        return put(copy.deepcopy(template))
    except (KeyError, ValueError):
        return None

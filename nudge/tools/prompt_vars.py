"""The variables of a prompt: read them, check them, fill them, and find them in a sentence.

No Odoo here, so the rules are tested without a database. A value is only ever put into the text
of the message the person sends; it is never read as a placeholder again, and a placeholder that
is not declared is left as it is.
"""
import math
import re
from datetime import date

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]{0,39})\s*\}\}")
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")
TYPES = ("string", "text", "integer", "number", "date", "boolean", "selection", "many2one")
# The word the first draft of the format used for a record
ALIASES = {"record": "many2one", "char": "string", "int": "integer", "float": "number", "bool": "boolean"}
MAX_STRING, MAX_TEXT, MAX_OPTIONS, MAX_VARIABLES, MAX_BODY = 300, 3000, 60, 20, 4000


class Invalid(ValueError):
    """A value or a definition that is refused; the message is for the person"""


def names_in(body):
    """:return: the names of the placeholders of ``body``, in order, once each"""
    seen = []
    for match in PLACEHOLDER.finditer(body or ""):
        if match.group(1) not in seen:
            seen.append(match.group(1))
    return seen


def clean_definitions(variables):
    """:return: the definitions as they are kept: known types, safe names, bounded lists
    :raise Invalid: when one cannot be read"""
    if variables in (None, False):
        return []
    if not isinstance(variables, list) or len(variables) > MAX_VARIABLES:
        raise Invalid("The variables are a list of at most %s." % MAX_VARIABLES)
    out, seen = [], set()
    for item in variables:
        if not isinstance(item, dict) or not NAME.match(str(item.get("name") or "")):
            raise Invalid("A variable needs a name of letters, digits and underscores.")
        name = item["name"]
        if name in seen:
            raise Invalid("The variable %s is declared twice." % name)
        seen.add(name)
        kind = ALIASES.get(item.get("type"), item.get("type") or "string")
        if kind not in TYPES:
            raise Invalid("The variable %s has a type this Nudge does not know." % name)
        label = item.get("label")
        label = {str(k)[:8]: str(v)[:120] for k, v in label.items()} if isinstance(label, dict) else {}
        clean = {"name": name, "type": kind, "required": bool(item.get("required")), "label": label}
        default = item.get("default")
        if default is not None and default is not False:
            clean["default"] = default if isinstance(default, (int, float)) else str(default)[:MAX_TEXT]
        if kind == "selection":
            options = item.get("options")
            if not isinstance(options, list) or not options or len(options) > MAX_OPTIONS:
                raise Invalid("The variable %s needs a list of options." % name)
            clean["options"] = [str(option)[:120] for option in options]
        if kind == "many2one":
            if not re.match(r"^[a-z][a-z0-9_.]{0,63}$", str(item.get("model") or "")):
                raise Invalid("The variable %s needs the model of its records." % name)
            clean["model"] = item["model"]
            if isinstance(item.get("domain"), list):
                clean["domain"] = item["domain"]
        out.append(clean)
    return out


def complete(body, variables):
    """:return: ``variables`` plus a text variable for each placeholder of ``body`` that has none"""
    have = {variable["name"] for variable in variables}
    extra = [{"name": name, "type": "string", "required": False, "label": {"en": name.replace("_", " ")}}
             for name in names_in(body) if name not in have]
    return clean_definitions(variables + extra)


def label_of(variable, lang="en"):
    label = variable.get("label") or {}
    return label.get((lang or "en")[:2]) or label.get("en") or variable["name"].replace("_", " ")


def _plain(value, limit):
    """A value as the person's words: no control characters, no way to write a placeholder"""
    text = "".join(ch for ch in str(value) if ch in "\n\t" or ch >= " ").strip()
    return text.replace("{{", "{ {").replace("}}", "} }")[:limit]


def _number(value, whole):
    try:
        if isinstance(value, bool):
            raise ValueError
        number = int(str(value).strip()) if whole else float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        raise Invalid("A number is expected.") from None
    if isinstance(number, float) and not math.isfinite(number):
        raise Invalid("A number is expected.")
    return str(int(number)) if isinstance(number, float) and number == int(number) else str(number)


def value_text(variable, value, record_name=None):
    """:param record_name: the display name of the record a many2one value points at, read by the caller
        as the person (their rights decide what they may pick)
    :return: the text that takes the place of the placeholder; empty when nothing was given
    :raise Invalid: when the value does not fit the type"""
    if value in (None, False, ""):
        if variable.get("required"):
            raise Invalid("%s is required." % label_of(variable))
        return ""
    kind = variable["type"]
    if kind == "string":
        return _plain(value, MAX_STRING).replace("\n", " ")
    if kind == "text":
        return _plain(value, MAX_TEXT)
    if kind in ("integer", "number"):
        return _number(value, kind == "integer")
    if kind == "date":
        try:
            return date.fromisoformat(str(value)[:10]).isoformat()
        except ValueError:
            raise Invalid("A date such as 2026-10-22 is expected.") from None
    if kind == "boolean":
        return "yes" if value in (True, "true", "True", 1, "1", "yes") else "no"
    if kind == "selection":
        if str(value) not in variable.get("options", []):
            raise Invalid("%s: choose one of the options." % label_of(variable))
        return _plain(value, MAX_STRING)
    if kind == "many2one":
        if record_name is None:
            raise Invalid("%s: choose a record." % label_of(variable))
        return _plain(record_name, MAX_STRING).replace("\n", " ")
    raise Invalid("Unknown type.")


def fill(body, texts):
    """:param texts: ``{name: text}``, already made by :func:`value_text`
    :return: ``body`` with each declared placeholder replaced once; the text put in is not read again"""
    return PLACEHOLDER.sub(lambda match: texts.get(match.group(1), match.group(0)), body or "")


# ---------------------------------------------------------------------- finding variables
_DATE = re.compile(r"\b(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/.-]\d{1,2}[/.-](?:\d{4}|\d{2}))\b")
_QUOTED = re.compile(r"[\"“«]([^\"”»\n]{1,80})[\"”»]")
_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])")
# A word that starts with a capital letter, after the first word: a name (Raghad, Dar Al Amal)
_NAME = re.compile(r"(?<=[a-z0-9,] )([A-Z][a-z]{2,}(?: [A-Z][a-z]{2,}){0,2})\b")
COMMON_CAPITALS = frozenset({
    "Odoo", "Nudge", "Arabic", "English", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
    "Sunday", "January", "February", "March", "April", "June", "July", "August", "September", "October",
    "November", "December", "Agent", "Talk", "Developer",
})


def detect(text, shared=False, skip=()):
    """Find the parts of a sentence a person would change the next time: dates, quoted names, names, numbers.

    :param shared: a prompt for others never carries the values of this one: every part found becomes a variable
    :param skip: names of found parts the person left as written (ignored when ``shared``)
    :return: ``{"body", "variables", "found"}``; ``found`` lists every part with its sample, ticked or not"""
    text = str(text or "")[:MAX_BODY]
    spans = []

    def take(pattern, kind, group=0):
        for match in pattern.finditer(text):
            start, end = match.span(group)
            if not any(start < other[1] and other[0] < end for other in spans):
                spans.append((start, end, kind, match.group(group)))

    take(_DATE, "date")
    take(_QUOTED, "name", 1)
    take(_NAME, "name", 1)
    take(_NUMBER, "number")
    spans = [span for span in spans if span[3] not in COMMON_CAPITALS][:MAX_VARIABLES]
    spans.sort()
    counts, found = {}, []
    for start, end, kind, sample in spans:
        counts[kind] = counts.get(kind, 0) + 1
        found.append({"name": f"{kind}_{counts[kind]}", "kind": kind, "sample": sample, "span": (start, end)})
    body, variables = text, []
    for item in reversed(found):
        if item["name"] in skip and not shared:
            continue
        start, end = item["span"]
        body = body[:start] + "{{" + item["name"] + "}}" + body[end:]
    for item in found:
        if "{{" + item["name"] + "}}" not in body:
            continue
        sample = item["sample"]
        if item["kind"] == "number":
            whole = "." not in sample and "," not in sample
            variables.append({"name": item["name"], "type": "integer" if whole else "number", "required": True,
                              "label": {"en": f"Number {item['name'][-1]}"}})
        elif item["kind"] == "date":
            variables.append({"name": item["name"], "type": "date", "required": True,
                              "label": {"en": f"Date {item['name'][-1]}"}})
        else:
            variables.append({"name": item["name"], "type": "string", "required": True,
                              "label": {"en": f"Name {item['name'][-1]}"}})
    for item in found:
        item.pop("span")
        item["used"] = "{{" + item["name"] + "}}" in body
    return {"body": body, "variables": variables, "found": found}

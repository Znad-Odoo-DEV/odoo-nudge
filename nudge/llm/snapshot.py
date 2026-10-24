"""Cleaning of the screen snapshot sent by the browser.

The snapshot is input from a client: it is data to show to the model, never
something to trust. It is cut to size, reduced to plain values, and cleared
of what looks like a secret. It plays no part in deciding what the user may
do: access is checked by the ORM, on the server, when something is read or
changed.
"""
import hashlib
import json
import re

MAX_RAW_CHARS = 200_000
MAX_CHARS = 12_000
MAX_DEPTH = 7
MAX_ITEMS = 100
MAX_KEY_CHARS = 64
MAX_TEXT_CHARS = 500

HIDDEN = "[hidden]"

_SECRET_NAMES = (
    r"pass(word|wd)?|secret|token|api_?key|private_?key|totp|otp|signup|oauth|credential"
    r"|cvv|cvc|card_?number|iban|ssn"
    # NP-13 / SEC-01: gateway and signing credentials that carry no word of the list above
    r"|_key(?:_|$)|hash_?key|hmac|signature_?(?:hash|digest)|(?:hash|digest)_?signature"
    r"|(?:^|_)pin$|consumer_(?:key|secret)|client_secret"
    r"|private_?cert|(?:^|_)(?:p12|pfx)(?:_|$)|csid|_csr$"
)
# What a name alone shows as a credential: names only, no type (a key of a payload, a column)
SENSITIVE_NAME = re.compile(_SECRET_NAMES + r"|signature", re.IGNORECASE)
# The names that are a credential whatever the field holds; "signature" alone is one only when the
# field is not a written text (the e-mail signature of a user is HTML, and is not a secret)
SECRET_FIELD_NAME = re.compile(_SECRET_NAMES, re.IGNORECASE)
SIGNATURE_NAME = re.compile(r"signature", re.IGNORECASE)
WRITTEN_TYPES = ("html", "text")
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Dropped one after the other until the snapshot fits, least useful first
KEPT_LINES = 5
TRIMMED_KEYS = ("rows", "cards", "groups", "notices", "buttons", "tabs", "search")


def _clean_text(value, size=MAX_TEXT_CHARS):
    value = CONTROL_CHARS.sub("", value)
    return value if len(value) <= size else value[:size - 1] + "…"


def _clean(value, depth=0):
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if abs(value) < 2 ** 53 else None
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    if isinstance(value, str):
        return _clean_text(value)
    if depth >= MAX_DEPTH:
        return None
    if isinstance(value, list):
        return [_clean(item, depth + 1) for item in value[:MAX_ITEMS]]
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:MAX_ITEMS]:
            if not isinstance(key, str):
                continue
            key = _clean_text(key, MAX_KEY_CHARS)
            if SENSITIVE_NAME.search(key):
                result[key] = HIDDEN
            else:
                result[key] = _clean(item, depth + 1)
        _hide_named_value(result)
        return result
    return None


def _hide_named_value(entry):
    """``{"name": "api_key", "value": "..."}``: the name is in a value, not in a key."""
    name = entry.get("name")
    if "value" in entry and (entry.get("masked") is True or (isinstance(name, str) and SENSITIVE_NAME.search(name))):
        # ``masked``: the screen said so (a password widget), whatever the field is called
        entry["value"] = HIDDEN


def _dump(snapshot):
    return json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _fits(snapshot):
    return len(_dump(snapshot)) <= MAX_CHARS


def _trim(snapshot):
    if _fits(snapshot):
        return snapshot
    # The model is told that it does not see everything
    snapshot["trimmed"] = True
    fields = snapshot.get("fields")
    if isinstance(fields, list):
        # What is not on screen goes first
        snapshot["fields"] = [
            field for field in fields if isinstance(field, dict) and field.get("on_screen")
        ]
    # The lines of an order go down to a few before the fields themselves are cut
    for field in snapshot.get("fields") or []:
        value = field.get("value") if isinstance(field, dict) else None
        if isinstance(value, dict) and isinstance(value.get("rows"), list) and len(value["rows"]) > KEPT_LINES:
            value["more"] = value.get("count", 0) - KEPT_LINES
            del value["rows"][KEPT_LINES:]
    for key in TRIMMED_KEYS:
        if _fits(snapshot):
            return snapshot
        snapshot.pop(key, None)
    fields = snapshot.get("fields")
    while isinstance(fields, list) and fields and not _fits(snapshot):
        del fields[len(fields) // 2:]
    if not _fits(snapshot):
        kept = ("v", "model", "view_type", "trimmed")
        return {key: snapshot[key] for key in kept if key in snapshot}
    return snapshot


def sanitize(raw):
    """:param raw: what the browser sent: a JSON string, a dict, or nothing
    :return: a clean dict, or None when there is nothing usable"""
    if not raw:
        return None
    if isinstance(raw, (str, bytes)):
        if len(raw) > MAX_RAW_CHARS:
            return None
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, dict) or not raw:
        return None
    snapshot = _trim(_clean(raw))
    return snapshot or None


def fingerprint(snapshot):
    return hashlib.sha256(_dump(snapshot).encode()).hexdigest()


def render(snapshot, unchanged=False):
    """Serialize deterministically: the same screen always gives the same bytes."""
    if unchanged:
        return '<screen_snapshot unchanged="true"/>'
    # The closing tag cannot appear inside the block
    payload = _dump(snapshot).replace("</", "<\\/")
    return f"<screen_snapshot>{payload}</screen_snapshot>"

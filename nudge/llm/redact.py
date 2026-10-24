"""NP-13: personal data becomes a token before a request leaves for a provider.

Emails, phone numbers, IBANs, Emirates ID, Saudi and Egyptian national numbers and card numbers are replaced
by tokens (``EMAIL_1``, ``PHONE_2``) in the system prompt, the turns, the screen and the tool results. The
answer of the model and the arguments of its tool calls are given their real values back here, before they
are shown or run, so a confirmation card still shows the real record.

This reduces what is sent, it does not prevent it: a pattern can miss a number written in an unusual way.
The map from token to value lives in the stream of one request and is never stored. Like the rest of this
package it has no Odoo import.
"""
import copy
import re

from . import base

# Kind of data: the token is ``<KIND>_<n>``
EMAIL = "EMAIL"
PHONE = "PHONE"
IBAN = "IBAN"
EMIRATES_ID = "EMIRATES_ID"
NATIONAL_ID = "NATIONAL_ID"
CARD = "CARD"

# Written so that they run in this order: the most specific first, the phone number last because its shape
# is the loosest. \d is any digit, so numbers written in Arabic-Indic digits are found too.
_NO_DIGIT_BEFORE = r"(?<![\w.,+-])"
_NO_DIGIT_AFTER = r"(?![\w]|[.,]\d)"
PATTERNS = (
    (EMAIL, re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")),
    (IBAN, re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?:[ -]?[A-Z0-9]{3,4}){3,8}(?:[ -]?[A-Z0-9]{1,3})?(?![A-Za-z0-9])")),
    (EMIRATES_ID, re.compile(_NO_DIGIT_BEFORE + r"784[- ]?\d{4}[- ]?\d{7}[- ]?\d" + _NO_DIGIT_AFTER)),
    (CARD, re.compile(_NO_DIGIT_BEFORE + r"\d(?:[ -]?\d){12,18}" + _NO_DIGIT_AFTER)),
    # Egypt: 14 digits, the first is the century (2 or 3)
    (NATIONAL_ID, re.compile(_NO_DIGIT_BEFORE + r"[23]\d{13}" + _NO_DIGIT_AFTER)),
    # Saudi Arabia: 10 digits, 1 for a citizen and 2 for a resident
    (NATIONAL_ID, re.compile(_NO_DIGIT_BEFORE + r"[12]\d{9}" + _NO_DIGIT_AFTER)),
    (PHONE, re.compile(
        _NO_DIGIT_BEFORE + r"(?:(?:\+|00)\s?\d(?:[\s().-]?\d){7,14}|[0٠]\d(?:[\s-]?\d){7,10})" + _NO_DIGIT_AFTER)),
)
KINDS = (EMAIL, PHONE, IBAN, EMIRATES_ID, NATIONAL_ID, CARD)
TOKEN = re.compile(r"(?<![A-Za-z0-9_])(?:%s)_\d+(?![A-Za-z0-9_])" % "|".join(KINDS))
# The end of a chunk that could be the start of a token: kept until the rest arrives
_OPEN_END = re.compile(r"[A-Za-z0-9_]{1,24}$")

# Keys that say what a block is, not what it says; what the model or a tool wrote is never in them
_STRUCTURE = frozenset((
    "id", "type", "role", "name", "provider_kind", "model", "signature", "media_type", "mime_type",
    "is_error", "extra", "stop_reason"))
# What these keys hold is content, whatever the keys inside it are called
_CONTENT = frozenset(("text", "context", "content", "input", "arguments", "thinking", "output", "result"))


def _luhn(digits):
    total = 0
    for index, char in enumerate(reversed(digits)):
        number = int(char)
        if index % 2:
            number *= 2
            if number > 9:
                number -= 9
        total += number
    return total % 10 == 0


class Redactor:
    """One request: the same value always gets the same token."""

    def __init__(self):
        self.tokens = {}     # value -> token
        self.values = {}     # token -> value
        self.counts = {}

    def _token(self, kind, value):
        key = (kind, value)
        token = self.tokens.get(key)
        if token is None:
            self.counts[kind] = self.counts.get(kind, 0) + 1
            token = f"{kind}_{self.counts[kind]}"
            self.tokens[key] = token
            self.values[token] = value
        return token

    def redact(self, text):
        """:return: ``text`` with each personal value replaced by its token"""
        if not isinstance(text, str) or len(text) < 5:
            return text
        for kind, pattern in PATTERNS:
            text = pattern.sub(lambda match, kind=kind: self._replace(kind, match.group(0)), text)
        return text

    def _replace(self, kind, value):
        digits = re.sub(r"\D", "", value)
        if kind == CARD and not (13 <= len(digits) <= 19 and _luhn(digits)):
            return value
        if kind == IBAN and not any(char.isdigit() for char in value[4:]):
            return value
        # A number that is a token already, or a word of letters only
        return self._token(kind, value)

    def restore(self, text):
        """:return: ``text`` with the tokens that this request made replaced by what they stand for"""
        if not isinstance(text, str) or not self.values or "_" not in text:
            return text
        return TOKEN.sub(lambda match: self.values.get(match.group(0), match.group(0)), text)

    def found(self):
        """:return: ``{token: value}`` of what was masked so far (for the preview)"""
        return dict(self.values)

    def walk(self, node, function, structure=True):
        """:return: a copy of ``node`` with ``function`` applied to every string that is content"""
        if isinstance(node, str):
            return function(node)
        if isinstance(node, list):
            return [self.walk(item, function, structure) for item in node]
        if isinstance(node, dict):
            result = {}
            for key, item in node.items():
                if structure and key in _STRUCTURE:
                    result[key] = item
                elif key in _CONTENT:
                    result[key] = self.walk(item, function, structure=False)
                else:
                    result[key] = self.walk(item, function, structure)
            return result
        return node


def preview(text):
    """:return: ``(masked_text, {token: value})`` for the settings page"""
    redactor = Redactor()
    masked = redactor.redact(text or "")
    return masked, redactor.found()


class RedactingAdapter:
    """Wraps the adapter of a provider that does not run on the company's own server."""

    def __init__(self, adapter):
        self._adapter = adapter

    def __getattr__(self, name):
        # list_models, check, transcribe, config, kind...: nothing else is asked of the wrapper
        return getattr(self._adapter, name)

    def stream_chat(self, system, turns, deadline=None, tools=None, tool_choice=None):
        redactor = Redactor()
        mask = redactor.redact
        # Every caller gives the system prompt as a list of blocks (build_system_blocks, oneshot): each one is masked
        system = [redactor.walk(block, mask) for block in base.system_texts(system)]
        turns = [redactor.walk(turn, mask) for turn in (turns or [])]
        pending = ""
        for event in self._adapter.stream_chat(
                system, turns, deadline=deadline, tools=tools, tool_choice=tool_choice):
            kind = event.get("type")
            if kind == "text":
                pending += event.get("text") or ""
                # A token may be cut between two chunks: the end that could be one waits for the next chunk
                held = _OPEN_END.search(pending)
                cut = held.start() if held else len(pending)
                ready, pending = pending[:cut], pending[cut:]
                if ready:
                    yield base.text_event(redactor.restore(ready))
                continue
            if pending:
                yield base.text_event(redactor.restore(pending))
                pending = ""
            if kind == "tool_use":
                event = dict(event)
                event["input"] = redactor.walk(event.get("input") or {}, redactor.restore, structure=False)
            elif kind == "stop" and event.get("blocks"):
                event = dict(event)
                event["blocks"] = [redactor.walk(copy.deepcopy(block), redactor.restore)
                                   for block in event["blocks"]]
            yield event
        if pending:
            yield base.text_event(redactor.restore(pending))

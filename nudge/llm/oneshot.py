"""One question to a provider, one answer in words: no tools, no conversation.

Used where Nudge asks for a verdict on many records at once. No Odoo imports,
as the rest of this package."""
import json

from . import base


def ask(adapter, system, text, deadline=None):
    """:param adapter: an adapter of :mod:`base`
    :param text: what is asked, in one turn
    :return: ``(answer, usage, model)``, usage as :func:`base.normalize_usage`
    :raise base.AdapterError: when the provider failed"""
    parts, usage, model = [], base.normalize_usage(None), ""
    for event in adapter.stream_chat(
            system, [{"role": "user", "text": text, "context": ""}], deadline=deadline, tools=None):
        if event["type"] == "text":
            parts.append(event["text"])
        elif event["type"] == "stop":
            usage, model = event["usage"], event.get("model") or ""
        elif event["type"] == "error":
            raise base.AdapterError(event.get("code") or base.ERROR_UNKNOWN, event.get("detail"))
    return "".join(parts), usage, model


def read_object(text):
    """:return: the JSON object an answer holds, whatever is written around
        it (a fence, a word), or None when there is none"""
    text = text or ""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        found = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return found if isinstance(found, dict) else None

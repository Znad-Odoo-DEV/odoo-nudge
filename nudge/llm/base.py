"""Provider-neutral contract between Nudge and an LLM provider.

This package has no Odoo imports: adapters receive plain data and yield plain
dicts, so they can run inside a streamed HTTP response where no environment
is available.
"""
import dataclasses
import time

# Normalized stop reasons
STOP_END = "end"
STOP_MAX_TOKENS = "max_tokens"
STOP_REFUSAL = "refusal"
STOP_TOOL_USE = "tool_use"
STOP_PAUSE = "pause"
STOP_DEADLINE = "deadline"

# Normalized error codes
ERROR_AUTH = "auth"
ERROR_MODEL_NOT_FOUND = "model_not_found"
ERROR_RATE_LIMIT = "rate_limit"
ERROR_BAD_REQUEST = "bad_request"
ERROR_UNAVAILABLE = "unavailable"
ERROR_NETWORK = "network"
ERROR_MISSING_LIBRARY = "missing_library"
ERROR_UNSUPPORTED = "unsupported"
ERROR_UNKNOWN = "unknown"

# How a provider turns speech into text
STT_NONE = "none"
# The recording is given to a chat model that can listen
STT_CHAT = "chat"
# The provider has a service for it: /audio/transcriptions
STT_ENDPOINT = "endpoint"


@dataclasses.dataclass(frozen=True)
class ProviderConfig:
    kind: str
    model: str
    api_key: str = dataclasses.field(default="", repr=False)
    base_url: str = ""
    max_output_tokens: int = 0
    effort: str = ""
    refusal_fallback: bool = False
    timeout: int = 60
    capabilities: dict = dataclasses.field(default_factory=dict, repr=False)
    stt_mode: str = STT_NONE
    stt_model: str = ""


def text_event(text):
    return {"type": "text", "text": text}


def tool_use_event(call_id, name, arguments, extra=None, invalid=""):
    """:param arguments: dict written by the model, not checked yet
    :param extra: what the provider attached to the call and wants back
    :param invalid: why the arguments could not be read, if they could not"""
    return {
        "type": "tool_use",
        "id": call_id,
        "name": name,
        "input": arguments if isinstance(arguments, dict) else {},
        "extra": extra or {},
        "invalid": invalid,
    }


def stop_event(reason, usage=None, blocks=None, model=None, request_id=None):
    return {
        "type": "stop",
        "reason": reason,
        "usage": normalize_usage(usage),
        "blocks": blocks or [],
        "model": model or "",
        "request_id": request_id or "",
    }


def error_event(code, detail=""):
    return {"type": "error", "code": code, "detail": str(detail or "")[:500]}


def normalize_usage(usage):
    usage = usage or {}
    return {
        key: int(usage.get(key) or 0)
        for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
    }


def expired(deadline):
    """:param deadline: a ``time.monotonic()`` value, or None for no deadline"""
    return deadline is not None and time.monotonic() >= deadline


def remaining(deadline, default):
    if deadline is None:
        return default
    return max(1.0, min(default, deadline - time.monotonic()))


class BaseAdapter:
    """Talks to one provider.

    ``stream_chat`` yields ``text`` and ``tool_use`` events followed by
    exactly one ``stop`` or ``error`` event. Provider failures are reported as
    ``error`` events, never raised, because the HTTP response has already
    started by then.

    ``turns`` is a list of dicts::

        {"role": "user" | "assistant", "text": str, "context": str,
         "blocks": list, "provider_kind": str, "model": str,
         "tool_calls": [{"id", "name", "input", "extra"}]}
        {"role": "tool", "results": [{"id", "name", "content", "is_error"}]}

    ``blocks`` holds the provider-native content of an assistant turn. An
    adapter replays them only when they were produced by its own kind.

    ``tools`` is a list of ``{"name", "description", "input_schema"}``.
    """
    kind = None

    def __init__(self, config):
        self.config = config

    def stream_chat(self, system, turns, deadline=None, tools=None, tool_choice=None):
        """:param tool_choice: "required" when the model must call a tool this
        time; None leaves it the choice"""
        raise NotImplementedError

    def list_models(self):
        """:return: sorted list of model ids
        :raise AdapterError: when the provider cannot be reached"""
        raise NotImplementedError

    def check(self):
        """Send a minimal request. :raise AdapterError: when it fails"""
        raise NotImplementedError

    def transcribe(self, audio, language="", hints=(), deadline=None):
        """Turn a recording into text.

        :param bytes audio: a WAV file
        :param str language: ISO 639-1 code of the language most likely spoken
        :param hints: words the speaker may be saying
        :return: dict with ``text``, ``usage`` and ``model``
        :raise AdapterError: when it fails, or when the provider cannot do it
        """
        raise AdapterError(ERROR_UNSUPPORTED, "This provider does not turn speech into text.")


class AdapterError(Exception):
    def __init__(self, code, detail=""):
        super().__init__(detail or code)
        self.code = code
        self.detail = str(detail or "")[:500]


ADAPTERS = {}


def register(adapter_class):
    ADAPTERS[adapter_class.kind] = adapter_class
    return adapter_class


def get_adapter(config):
    return ADAPTERS[config.kind](config)

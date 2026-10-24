"""Adapter for Anthropic Claude, through the official ``anthropic`` SDK.

The SDK is imported lazily: the module installs without it, and the provider
reports a clear error when it is selected but not installed.
"""
import logging
import time

from . import base, resilience
from .prompt import is_question_block, render_user_text

_logger = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS = 16000

# Models whose safety classifiers can decline a request, and that accept the
# server-side fallback parameter
FALLBACK_MODELS = frozenset({
    "claude-fable-5-1",
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-sonnet-5-5",
})
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# A thinking block is bound to everything before it: the system prompt, the
# tools and the messages. Nudge rebuilds the first two and its notes between
# two requests, and a block bound to what changed is answered with a 400 on
# the accounts made since 2026-08-31; with this beta the API drops it instead
BINDING_BETA = "thinking-binding-controls-2026-08-01"
THINKING = {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}}
# A forced call (tool_choice "any" or "tool") is refused with a 400 by the
# models of now: when a tool must be called, it is asked for in words, the way
# the loop asks for the answer of the last round
TOOL_NOTE = (
    "What the person asked for is done with a tool: call it now, before you answer in words. "
    "Only if no tool can do it, say why in one sentence.")

STOP_REASONS = {
    "end_turn": base.STOP_END,
    "stop_sequence": base.STOP_END,
    "max_tokens": base.STOP_MAX_TOKENS,
    "refusal": base.STOP_REFUSAL,
    "tool_use": base.STOP_TOOL_USE,
    "pause_turn": base.STOP_PAUSE,
}

# Keys accepted when a content block is sent back in a later request
REPLAY_KEYS = {
    "text": ("type", "text"),
    "thinking": ("type", "thinking", "signature"),
    "redacted_thinking": ("type", "data"),
    "tool_use": ("type", "id", "name", "input"),
}
MODEL_INTERNAL_BLOCKS = ("thinking", "redacted_thinking", "tool_use")


def sanitize_blocks(blocks):
    """Prepare the content blocks of a stored assistant turn for replay.

    Blocks are passed back unchanged, which thinking blocks require. When the
    turn was completed by a fallback model, the model-internal blocks that
    precede the last ``fallback`` marker belong to the model that declined and
    must not be echoed.
    """
    last_fallback = max(
        (index for index, block in enumerate(blocks) if block.get("type") == "fallback"),
        default=-1,
    )
    result = []
    for index, block in enumerate(blocks):
        block_type = block.get("type")
        if block_type not in REPLAY_KEYS:
            continue
        if index < last_fallback and block_type in MODEL_INTERNAL_BLOCKS:
            continue
        if block_type == "text" and not (block.get("text") or "").strip():
            continue
        result.append({key: block[key] for key in REPLAY_KEYS[block_type] if key in block})
    return result


def _origin(error):
    """:return: the error ``error`` was raised from, or None"""
    if error.__cause__ is not None:
        return error.__cause__
    return None if error.__suppress_context__ else error.__context__


def _supported(capabilities, *path):
    node = capabilities
    for key in path:
        if not isinstance(node, dict):
            return False
        node = node.get(key)
    return bool(isinstance(node, dict) and node.get("supported"))


@base.register
class AnthropicAdapter(base.BaseAdapter):
    kind = "anthropic"

    def _sdk(self):
        try:
            import anthropic  # noqa: PLC0415
        except ImportError as exc:
            raise base.AdapterError(
                base.ERROR_MISSING_LIBRARY, "pip install anthropic",
            ) from exc
        return anthropic

    def _timeout(self, deadline):
        """:return: the time the SDK may take: connect and each wait for the
        provider bounded by what is left of the deadline"""
        whole = float(self.config.timeout)
        if deadline is None:
            return whole
        left = max(0.5, min(whole, deadline - time.monotonic()))
        try:
            import httpx  # noqa: PLC0415
        except ImportError:
            return left
        return httpx.Timeout(left, connect=min(10.0, left))

    def _client(self, anthropic, deadline=None):
        # Answers come gzipped, never in brotli: the SDK's HTTP client calls
        # brotli with a keyword the brotli of many servers predates, and every
        # answer that came back failed with "process() takes no keyword arguments"
        headers = {"Accept-Encoding": "gzip, deflate"}
        # No retries of the SDK: they would run past the deadline. Nudge retries on
        # its own terms (``stream_chat``), inside the time left
        kwargs = {"timeout": self._timeout(deadline), "max_retries": 0, "default_headers": headers}
        # Without a stored key the SDK resolves credentials from the environment
        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key
        if self.config.base_url:
            kwargs["base_url"] = self.config.base_url
        if self.config.workspace_id:
            # A key that is not scoped to a workspace (a user key) names one
            # on every request, or is refused with a 400
            headers["anthropic-workspace-id"] = self.config.workspace_id
        return anthropic.Anthropic(**kwargs)

    # ------------------------------------------------------------------
    # Request building
    # ------------------------------------------------------------------
    def _build_system(self, system):
        # Claude refuses a block of blanks only
        blocks = [{"type": "text", "text": text} for text in system if text and text.strip()]
        # Read point for the stable prefix (tools + system): on the last block that is the same from one
        # question to the next. The skills of the question come after it, so that other skills, or
        # one asked for with use_skill, never rewrite the cached tools and rules
        stable = [block for block in blocks if not is_question_block(block["text"])]
        if stable:
            stable[-1]["cache_control"] = {"type": "ephemeral"}
        return blocks

    @staticmethod
    def _build_tools(tools):
        """Inputs are streamed as they are written, and checked by Nudge before anything runs.

        S8: the tools render first, before the system prompt. Those every question of the mode is
        offered come first, and a read point after the last of them (``base.CACHE_POINT``) keeps
        them in the cache when a question brings tools of its own after them. With the one of the
        system prompt and the one on the conversation, three of the four the API allows."""
        built, marked = [], False
        for tool in tools:
            definition = {key: value for key, value in tool.items() if key != base.CACHE_POINT}
            definition["eager_input_streaming"] = True
            if tool.get(base.CACHE_POINT) and not marked:
                # One read point among the tools, whatever the list says: the API counts them
                definition["cache_control"] = {"type": "ephemeral"}
                marked = True
            built.append(definition)
        return built

    def _build_messages(self, turns):
        messages = []
        for turn in turns:
            role = turn["role"]
            if role == "tool":
                role = "user"
                content = [
                    {
                        "type": "tool_result",
                        "tool_use_id": result["id"],
                        "content": result["content"],
                        "is_error": bool(result.get("is_error")),
                    }
                    for result in turn.get("results") or []
                ]
            elif role == "assistant":
                content = []
                if turn.get("blocks") and turn.get("provider_kind") == self.kind:
                    content = sanitize_blocks(turn["blocks"])
                if not content:
                    # A turn written by another provider
                    if turn["text"]:
                        content.append({"type": "text", "text": turn["text"]})
                    content.extend(
                        {"type": "tool_use", "id": call["id"], "name": call["name"],
                         "input": call.get("input") or {}}
                        for call in turn.get("tool_calls") or []
                    )
            else:
                content = [
                    *({"type": "image", "source": {
                        "type": "base64", "media_type": image["media_type"], "data": image["data"]}}
                      for image in turn.get("images") or []),
                    {"type": "text", "text": render_user_text(turn)},
                ]
            if content:
                messages.append({"role": role, "content": content})
        return messages

    def _build_params(self, system, turns, tools=None, tool_choice=None):
        config = self.config
        if tools and tool_choice == "required":
            # Not forced, which the API refuses: told, and left to choose
            turns = [*turns, {"role": "user", "text": TOOL_NOTE}]
        params = {
            "model": config.model,
            "max_tokens": config.max_output_tokens or DEFAULT_MAX_TOKENS,
            "system": self._build_system(system),
            "messages": self._build_messages(turns),
            # Automatic breakpoint on the growing conversation tail
            "cache_control": {"type": "ephemeral"},
        }
        if tools:
            params["tools"] = self._build_tools(tools)
        if tools and tool_choice == "none":
            # The answer now: the tools stay known, none is called
            params["tool_choice"] = {"type": "none"}
        capabilities = config.capabilities or {}
        if _supported(capabilities, "thinking", "types", "adaptive"):
            # Before a call as before an answer: nothing forced is ever sent.
            # The blocks of the turns before are replayed; the API drops those
            # whose prefix changed
            params["thinking"] = dict(THINKING)
        if config.effort and _supported(capabilities, "effort", config.effort):
            params["output_config"] = {"effort": config.effort}
        params["betas"] = [BINDING_BETA]
        if config.refusal_fallback and config.model in FALLBACK_MODELS:
            params["betas"].append(FALLBACK_BETA)
            params["fallbacks"] = "default"
        return params

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    def stream_chat(self, system, turns, deadline=None, tools=None, tool_choice=None):
        system = base.system_texts(system)
        try:
            anthropic = self._sdk()
        except base.AdapterError as exc:
            yield base.error_event(exc.code, exc.detail)
            return
        key = resilience.cooldown_key(self.config)
        left = resilience.cooldown_left(key)
        if left > 0:
            yield base.error_event(base.ERROR_RATE_LIMIT, resilience.busy_message(left))
            return
        params = self._build_params(system, turns, tools, tool_choice)
        delays = list(resilience.RETRY_DELAYS)
        while True:
            started = []
            try:
                yield from self._attempt(anthropic, params, deadline, started)
                return
            except anthropic.APIError as exc:
                code, detail = self._map_error(anthropic, exc)
                _logger.info("Nudge: Anthropic request failed (%s): %s", code, detail)
                wait = self._wait_after(exc, code, delays, deadline)
                if wait is not None and not started:
                    # Nothing has reached the user yet, so asking again cannot answer twice
                    time.sleep(wait)
                    continue
                if code in (base.ERROR_RATE_LIMIT, base.ERROR_UNAVAILABLE, base.ERROR_NETWORK):
                    asked = self._retry_after(exc)
                    resilience.start_cooldown(
                        key, asked if asked is not None else (
                            resilience.DEFAULT_RATE_COOLDOWN if code == base.ERROR_RATE_LIMIT
                            else resilience.DEFAULT_ERROR_COOLDOWN))
                    if code == base.ERROR_RATE_LIMIT and asked is not None:
                        detail = resilience.busy_message(asked)
                yield base.error_event(code, detail)
                return
            except ValueError as exc:
                _logger.info("Nudge: Anthropic answer could not be read: %s", exc)
                yield base.error_event(base.ERROR_UNKNOWN, "unreadable tool arguments")
                return

    @staticmethod
    def _retry_after(exc):
        response = getattr(exc, "response", None)
        return resilience.retry_after(getattr(response, "headers", None))

    def _wait_after(self, exc, code, delays, deadline):
        """:return: the seconds to wait before asking again, None when the
        failure is not of a kind that passes or the wait does not fit the deadline"""
        if code == base.ERROR_RATE_LIMIT:
            asked = self._retry_after(exc)
            if asked is None or asked > resilience.MAX_RETRY_AFTER or not resilience.fits(asked, deadline):
                return None
            return asked
        if code in (base.ERROR_UNAVAILABLE, base.ERROR_NETWORK) and delays and resilience.fits(delays[0], deadline):
            return delays.pop(0)
        return None

    def _attempt(self, anthropic, params, deadline, started):
        """One request. ``started`` gets an item as soon as something is shown to the user."""
        client = self._client(anthropic, deadline)
        # Every request goes through the beta namespace, where the
        # binding of the thinking blocks is controlled
        manager = client.beta.messages.stream(**params)
        # The SDK raises ValueError for tool arguments it cannot read at all
        with manager as stream:
            # A provider gone silent between two chunks ends with the deadline
            with resilience.watch(deadline, getattr(stream, "close", lambda: None)):
                try:
                    for event in stream:
                        if base.expired(deadline):
                            snapshot = stream.current_message_snapshot
                            yield base.stop_event(
                                base.STOP_DEADLINE,
                                usage=self._normalize_usage(snapshot.to_dict().get("usage")),
                                model=snapshot.model,
                                request_id=stream.request_id,
                            )
                            return
                        if event.type == "content_block_delta" and event.delta.type == "text_delta":
                            started.append(True)
                            yield base.text_event(event.delta.text)
                    final = stream.get_final_message().to_dict()
                except anthropic.APIError:
                    if base.expired(deadline):
                        # Closed by the watchdog
                        yield base.stop_event(base.STOP_DEADLINE, request_id=stream.request_id)
                        return
                    raise
            request_id = stream.request_id
        reason = STOP_REASONS.get(final.get("stop_reason"), base.STOP_END)
        if reason == base.STOP_TOOL_USE:
            # Not on max_tokens nor on a refusal: a call cut short reads
            # as a valid, shorter one
            for block in final.get("content") or []:
                if block.get("type") == "tool_use":
                    started.append(True)
                    yield base.tool_use_event(block["id"], block["name"], block.get("input"))
        yield base.stop_event(
            reason,
            usage=self._normalize_usage(final.get("usage")),
            blocks=final.get("content"),
            model=final.get("model"),
            request_id=request_id,
        )

    @staticmethod
    def _normalize_usage(usage):
        usage = usage or {}
        return {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cache_read_tokens": usage.get("cache_read_input_tokens"),
            "cache_write_tokens": usage.get("cache_creation_input_tokens"),
        }

    def _cause(self, exc):
        """The SDK raises APIConnectionError for whatever fails once the
        request is sent, a body it cannot decode as well as a server it cannot
        reach: what it was raised from is told with it.

        :return: one line, as "APIConnectionError: DecodingError: <words>"
        """
        causes, cause = [], _origin(exc)
        while cause is not None and cause not in causes and len(causes) < 5:
            causes.append(cause)
            cause = _origin(cause)
        if not causes:
            return type(exc).__name__
        # An error wrapped once more often has no words of its own
        cause = next((cause for cause in causes if str(cause).strip()), causes[0])
        words = " ".join(str(cause).split())[:200]
        if self.config.api_key:
            # Errors of the transport do not carry the headers; should one, the key stays out
            words = words.replace(self.config.api_key, "***")
        return ": ".join(part for part in (type(exc).__name__, type(cause).__name__, words) if part)

    def _map_error(self, anthropic, exc):
        detail = getattr(exc, "message", "") or type(exc).__name__
        if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
            return base.ERROR_AUTH, detail
        if isinstance(exc, anthropic.NotFoundError):
            return base.ERROR_MODEL_NOT_FOUND, detail
        if isinstance(exc, anthropic.RateLimitError):
            return base.ERROR_RATE_LIMIT, detail
        if isinstance(exc, anthropic.APIStatusError):
            if exc.status_code >= 500:
                return base.ERROR_UNAVAILABLE, detail
            if "anthropic-workspace-id" in detail:
                # "This API key is not scoped to a workspace, so this request
                # must include the anthropic-workspace-id header"
                return base.ERROR_WORKSPACE, detail
            return base.ERROR_BAD_REQUEST, detail
        timeout_error = getattr(anthropic, "APITimeoutError", None)
        if timeout_error is not None and isinstance(exc, timeout_error):
            return base.ERROR_TIMEOUT, self._cause(exc)
        if isinstance(exc, anthropic.APIConnectionError):
            return base.ERROR_NETWORK, self._cause(exc)
        return base.ERROR_UNKNOWN, detail

    def _call(self, function):
        anthropic = self._sdk()
        try:
            return function(self._client(anthropic))
        except anthropic.APIError as exc:
            raise base.AdapterError(*self._map_error(anthropic, exc)) from exc

    def list_models(self):
        return sorted(self._call(lambda client: [model.id for model in client.models.list()]))

    def fetch_capabilities(self):
        """:return: the capability tree of the configured model, as a dict"""
        def fetch(client):
            return client.models.retrieve(self.config.model).to_dict().get("capabilities") or {}
        return self._call(fetch)

    def check(self):
        self.fetch_capabilities()
        # The model looked up is not the model asked: counting the words of a
        # request is free, and refused for the key or the model a request
        # would be refused for
        self._call(lambda client: client.messages.count_tokens(
            model=self.config.model, messages=[{"role": "user", "content": "ping"}]))

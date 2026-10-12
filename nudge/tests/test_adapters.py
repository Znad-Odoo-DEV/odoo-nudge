import json
import logging
import sys
import time
import types
from unittest.mock import patch

import requests

from odoo.tests import BaseCase, tagged

from odoo.addons.nudge.llm import base, openai_compat_adapter
from odoo.addons.nudge.llm.anthropic_adapter import AnthropicAdapter, sanitize_blocks
from odoo.addons.nudge.llm.openai_compat_adapter import OpenAICompatAdapter

SYSTEM = ["You are Nudge."]
TURNS = [
    {"role": "user", "text": "Hello", "context": "<nudge_context>{}</nudge_context>"},
    {"role": "assistant", "text": "Hi!", "blocks": [], "provider_kind": "openai_compatible"},
    {"role": "user", "text": "How are you?", "context": ""},
]


def sse(*chunks, done=True):
    lines = []
    for chunk in chunks:
        lines += [f"data: {json.dumps(chunk)}".encode(), b""]
    if done:
        lines.append(b"data: [DONE]")
    return lines


def delta(text=None, finish=None, **extra):
    choice = {"index": 0, "delta": {"content": text} if text is not None else {}}
    if finish:
        choice["finish_reason"] = finish
    return {"model": "served-model", "choices": [choice], **extra}


class FakeResponse:
    def __init__(self, status_code=200, lines=(), body=None):
        self.status_code = status_code
        self.lines = lines
        self.body = body
        self.text = json.dumps(body) if body is not None else ""
        self.closed = False

    def iter_lines(self, chunk_size=None):
        yield from self.lines

    def json(self):
        if self.body is None:
            raise ValueError("no body")
        return self.body

    def close(self):
        self.closed = True


@tagged("post_install", "-at_install")
class TestOpenAICompatAdapter(BaseCase):

    def setUp(self):
        super().setUp()
        self.adapter = OpenAICompatAdapter(base.ProviderConfig(
            kind="openai_compatible", model="test-model", api_key="sk-test",
            base_url="https://llm.invalid/v1/", timeout=30,
        ))
        self.posted = []
        self.slept = []
        self.patch(openai_compat_adapter.time, "sleep", self.slept.append)

    def respond(self, *responses):
        responses = list(responses)

        def post(payload, stream, read_timeout):
            self.posted.append(json.loads(json.dumps(payload)))
            return responses.pop(0)

        self.patch(self.adapter, "_post", post)

    def run_stream(self, **kwargs):
        return list(self.adapter.stream_chat(SYSTEM, TURNS, **kwargs))

    def test_stream(self):
        response = FakeResponse(lines=sse(
            delta(""),
            delta("Hel"),
            delta("lo مرحبا"),
            delta(finish="stop"),
            {"choices": [], "usage": {
                "prompt_tokens": 100, "completion_tokens": 7,
                "prompt_tokens_details": {"cached_tokens": 40},
            }},
        ))
        self.respond(response)
        events = self.run_stream()

        self.assertEqual([event["text"] for event in events[:-1]], ["Hel", "lo مرحبا"])
        stop = events[-1]
        self.assertEqual(stop["type"], "stop")
        self.assertEqual(stop["reason"], base.STOP_END)
        self.assertEqual(stop["model"], "served-model")
        self.assertEqual(stop["usage"], {
            "input_tokens": 60, "output_tokens": 7, "cache_read_tokens": 40, "cache_write_tokens": 0,
        })
        self.assertTrue(response.closed)

    def test_request(self):
        self.respond(FakeResponse(lines=sse(delta("ok", finish="stop"))))
        self.run_stream()
        payload = self.posted[0]

        self.assertEqual(self.adapter._url("/chat/completions"), "https://llm.invalid/v1/chat/completions")
        self.assertEqual(self.adapter._headers()["Authorization"], "Bearer sk-test")
        self.assertEqual(payload["model"], "test-model")
        self.assertTrue(payload["stream"])
        self.assertNotIn("max_tokens", payload, "0 leaves the limit to the provider")
        self.assertEqual(payload["messages"], [
            {"role": "system", "content": "You are Nudge."},
            {"role": "user", "content": "<nudge_context>{}</nudge_context>\n\nHello"},
            {"role": "assistant", "content": "Hi!"},
            {"role": "user", "content": "How are you?"},
        ])

    def test_turns_of_the_same_role_are_merged(self):
        self.respond(FakeResponse(lines=sse(delta("ok", finish="stop"))))
        turns = [
            {"role": "user", "text": "First", "context": ""},
            {"role": "assistant", "text": "", "context": ""},
            {"role": "user", "text": "Second", "context": ""},
        ]
        list(self.adapter.stream_chat([], turns))
        self.assertEqual(self.posted[0]["messages"], [{"role": "user", "content": "First\n\nSecond"}])

    def test_retry_without_stream_options(self):
        refused = FakeResponse(400, body={"error": {"message": "unknown field stream_options"}})
        self.respond(refused, FakeResponse(lines=sse(delta("ok", finish="stop"))))
        events = self.run_stream()

        self.assertEqual(events[0]["text"], "ok")
        self.assertIn("stream_options", self.posted[0])
        self.assertNotIn("stream_options", self.posted[1])
        self.assertTrue(refused.closed)

    def test_http_errors(self):
        cases = [
            (401, base.ERROR_AUTH),
            (403, base.ERROR_AUTH),
            (404, base.ERROR_MODEL_NOT_FOUND),
            (429, base.ERROR_RATE_LIMIT),
            (422, base.ERROR_BAD_REQUEST),
        ]
        for status, code in cases:
            self.posted.clear()
            self.respond(FakeResponse(status, body={"error": {"message": f"failed {status}"}}))
            events = self.run_stream()
            self.assertEqual(events, [base.error_event(code, f"failed {status}")])
            self.assertEqual(len(self.posted), 1, "Asking again would not change the answer")

    def test_the_check_follows_the_effort(self):
        self.adapter = OpenAICompatAdapter(base.ProviderConfig(
            kind="openai_compatible", model="qwen3:4b", base_url="http://localhost:11434/v1", effort="none"))
        posted = []

        def post(payload, stream, read_timeout):
            posted.append(dict(payload))
            return FakeResponse(body={"choices": [{"message": {"content": "OK"}}]})

        self.patch(self.adapter, "_post", post)
        self.adapter.check()
        self.assertEqual(posted[0]["reasoning_effort"], "none")

        def silent(payload, stream, read_timeout):
            raise requests.exceptions.ReadTimeout("Read timed out.")

        self.patch(self.adapter, "_post", silent)
        with self.assertRaises(base.AdapterError) as caught:
            self.adapter.check()
        self.assertEqual(caught.exception.code, base.ERROR_TIMEOUT)

    def test_a_model_without_tools(self):
        self.respond(FakeResponse(400, body={"error": {"message": "registry.ollama.ai/library/gemma:2b does not support tools"}}))
        events = self.run_stream(tools=[{"name": "t", "description": "d", "input_schema": {"type": "object"}}])
        self.assertEqual(events[0]["code"], base.ERROR_UNSUPPORTED)

    def test_busy_provider_is_asked_again(self):
        busy = [FakeResponse(503, body={"error": {"message": "high demand"}}) for _index in range(2)]
        self.respond(*busy, FakeResponse(lines=sse(delta("ok", finish="stop"))))
        events = self.run_stream()

        self.assertEqual([event["type"] for event in events], ["text", "stop"])
        self.assertEqual(self.slept, [1.0, 3.0])
        self.assertTrue(all(response.closed for response in busy))
        self.assertEqual(len(self.posted), 3)

    def test_busy_provider_gives_up(self):
        self.respond(*[FakeResponse(503, body={"error": {"message": "high demand"}}) for _index in range(3)])
        events = self.run_stream()

        self.assertEqual(events, [base.error_event(base.ERROR_UNAVAILABLE, "high demand")])
        self.assertEqual(len(self.posted), 3)

    def test_busy_provider_and_little_time_left(self):
        self.respond(FakeResponse(503, body={"error": {"message": "high demand"}}))
        events = self.run_stream(deadline=time.monotonic() + 0.5)

        self.assertEqual(events, [base.error_event(base.ERROR_UNAVAILABLE, "high demand")])
        self.assertEqual(self.slept, [], "Waiting must not carry the answer past its deadline")

    def test_a_provider_at_its_limit_is_waited_for_when_it_says_how_long(self):
        # Gemini's free tier counts the tokens of a minute, and says when to come back
        limited = FakeResponse(429, body={"error": {"message": "Quota exceeded. Please retry in 17.8s."}})
        self.respond(limited, FakeResponse(lines=sse(delta("ok", finish="stop"))))
        events = self.run_stream()
        self.assertEqual([event["type"] for event in events], ["text", "stop"])
        self.assertEqual(len(self.slept), 1)
        self.assertAlmostEqual(self.slept[0], 18.8)
        self.assertTrue(limited.closed)

        # Too long a wait, or no time left for it: told at once
        self.slept.clear()
        self.respond(FakeResponse(429, body={"error": {"message": "Please retry in 52s."}}))
        self.assertEqual(self.run_stream()[0]["code"], base.ERROR_RATE_LIMIT)
        self.respond(FakeResponse(429, body={"error": {"message": "Please retry in 5s."}}))
        self.assertEqual(self.run_stream(deadline=time.monotonic() + 8)[0]["code"], base.ERROR_RATE_LIMIT)
        self.assertEqual(self.slept, [])

        # Waited for once, not twice; and RetryInfo is read as well
        details = {"error": {"message": "quota", "details": [{"retryDelay": "2s"}]}}
        self.respond(FakeResponse(429, body=details), FakeResponse(429, body=details))
        self.assertEqual(self.run_stream()[0]["code"], base.ERROR_RATE_LIMIT)
        self.assertEqual(self.slept, [3.0])

    def test_error_in_a_list(self):
        self.respond(FakeResponse(429, body=[{"error": {"message": "quota"}}]))
        self.assertEqual(self.run_stream(), [base.error_event(base.ERROR_RATE_LIMIT, "quota")])

    def test_error_inside_the_stream(self):
        self.respond(FakeResponse(lines=sse(delta("Hel"), {"error": {"message": "overloaded"}})))
        events = self.run_stream()
        self.assertEqual(events[0]["text"], "Hel")
        self.assertEqual(events[1], base.error_event(base.ERROR_UNAVAILABLE, "overloaded"))

    def test_network_failure(self):
        def post(payload, stream, read_timeout):
            raise requests.exceptions.ConnectTimeout("refused for key sk-test")

        self.patch(self.adapter, "_post", post)
        with self.assertLogs("odoo.addons.nudge.llm.openai_compat_adapter", logging.INFO) as logs:
            events = self.run_stream()
        self.assertEqual(events, [base.error_event(base.ERROR_NETWORK, "ConnectTimeout")])
        self.assertIn("refused for key ***", logs.output[0])
        self.assertNotIn("sk-test", logs.output[0])

    def test_a_silent_provider_is_told_as_such(self):
        def post(payload, stream, read_timeout):
            raise requests.exceptions.ReadTimeout("Read timed out. (read timeout=30)")

        self.patch(self.adapter, "_post", post)
        with self.assertLogs("odoo.addons.nudge.llm.openai_compat_adapter", logging.INFO):
            events = self.run_stream()
        self.assertEqual(events, [base.error_event(base.ERROR_TIMEOUT, "no answer within 30s")])

    def test_reasoning_effort(self):
        self.respond(FakeResponse(lines=sse(delta("ok", finish="stop"))))
        self.run_stream()
        self.assertNotIn("reasoning_effort", self.posted[0], "Nothing asked, nothing sent")

        self.adapter = OpenAICompatAdapter(base.ProviderConfig(
            kind="openai_compatible", model="test-model", api_key="sk-test",
            base_url="https://llm.invalid/v1/", timeout=30, effort="max"))
        self.posted.clear()
        refused = FakeResponse(400, body={"error": {"message": "reasoning_effort is not supported"}})
        self.respond(refused, FakeResponse(lines=sse(delta("ok", finish="stop"))))
        events = self.run_stream()
        self.assertEqual(events[0]["text"], "ok")
        self.assertEqual(self.posted[0]["reasoning_effort"], "high", "Max is high in the words of this API")
        self.assertNotIn("reasoning_effort", self.posted[1], "Refused once, not sent again")
        self.assertTrue(refused.closed)

        # No reasoning at all: what Ollama reads to answer a thinking model at once
        self.adapter = OpenAICompatAdapter(base.ProviderConfig(
            kind="openai_compatible", model="qwen3:8b", base_url="http://localhost:11434/v1", effort="none"))
        self.posted.clear()
        self.respond(FakeResponse(lines=sse(delta("ok", finish="stop"))))
        self.run_stream()
        self.assertEqual(self.posted[0]["reasoning_effort"], "none")

    def test_finish_reasons(self):
        cases = {"length": base.STOP_MAX_TOKENS, "content_filter": base.STOP_REFUSAL, None: base.STOP_END}
        for finish, reason in cases.items():
            self.respond(FakeResponse(lines=sse(delta("x", finish=finish))))
            self.assertEqual(self.run_stream()[-1]["reason"], reason)

    def test_deadline(self):
        self.respond(FakeResponse(lines=sse(delta("late"))))
        events = self.run_stream(deadline=time.monotonic() - 1)
        self.assertEqual([event["type"] for event in events], ["stop"])
        self.assertEqual(events[0]["reason"], base.STOP_DEADLINE)

    def test_garbage_is_skipped(self):
        lines = [b": keep-alive", b"event: ping", b"data: {broken", b"data: 42", *sse(delta("ok", finish="stop"))]
        self.respond(FakeResponse(lines=lines))
        events = self.run_stream()
        self.assertEqual([event["type"] for event in events], ["text", "stop"])

    def test_list_models(self):
        body = {"data": [{"id": "models/gemini-b"}, {"id": "alpha"}, {"id": "models/gemini-b"}, {}]}
        self.patch(self.adapter, "_get", lambda path: FakeResponse(body=body))
        self.assertEqual(self.adapter.list_models(), ["alpha", "gemini-b"])

        self.patch(self.adapter, "_get", lambda path: FakeResponse(401, body={"error": {"message": "no"}}))
        with self.assertRaises(base.AdapterError) as capture:
            self.adapter.list_models()
        self.assertEqual(capture.exception.code, base.ERROR_AUTH)


# ----------------------------------------------------------------------
# Anthropic: the SDK is replaced by a small stand-in
# ----------------------------------------------------------------------

class FakeAPIError(Exception):
    message = "failed"


class FakeAPIStatusError(FakeAPIError):
    def __init__(self, status_code=400):
        super().__init__("failed")
        self.status_code = status_code


class FakeAuthenticationError(FakeAPIStatusError):
    pass


class FakePermissionDeniedError(FakeAPIStatusError):
    pass


class FakeNotFoundError(FakeAPIStatusError):
    pass


class FakeRateLimitError(FakeAPIStatusError):
    pass


class FakeAPIConnectionError(FakeAPIError):
    pass


class FakeAPITimeoutError(FakeAPIConnectionError):
    pass


class FakeMessage:
    def __init__(self, data):
        self.data = data
        self.model = data.get("model")

    def to_dict(self):
        return self.data


class FakeStream:
    request_id = "req_123"

    def __init__(self, texts, final, error=None):
        self.texts = texts
        self.final = final
        self.error = error
        self.current_message_snapshot = FakeMessage({"model": final["model"], "usage": {"input_tokens": 9}})

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        yield types.SimpleNamespace(type="message_start")
        for text in self.texts:
            yield types.SimpleNamespace(
                type="content_block_delta",
                delta=types.SimpleNamespace(type="text_delta", text=text),
            )
        if self.error:
            raise self.error
        yield types.SimpleNamespace(
            type="content_block_delta",
            delta=types.SimpleNamespace(type="thinking_delta", thinking="hidden"),
        )

    def get_final_message(self):
        return FakeMessage(self.final)


FINAL = {
    "model": "claude-opus-5-5",
    "stop_reason": "end_turn",
    "content": [
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "text", "text": "Hello there", "citations": None},
    ],
    "usage": {
        "input_tokens": 10, "output_tokens": 20,
        "cache_read_input_tokens": 300, "cache_creation_input_tokens": 40,
    },
}

CAPABILITIES = {
    "thinking": {"supported": True, "types": {"adaptive": {"supported": True}}},
    "effort": {"supported": True, "low": {"supported": True}, "max": {"supported": False}},
}


@tagged("post_install", "-at_install")
class TestAnthropicAdapter(BaseCase):

    def setUp(self):
        super().setUp()
        self.calls = []
        self.stream = FakeStream(["Hello ", "there"], FINAL)

        def record(name):
            def call(**params):
                self.calls.append((name, params))
                return self.stream
            return call

        self.client_arguments = {}

        def make_client(**kwargs):
            self.client_arguments.update(kwargs)
            return types.SimpleNamespace(
                messages=types.SimpleNamespace(stream=record("stable")),
                beta=types.SimpleNamespace(messages=types.SimpleNamespace(stream=record("beta"))),
            )

        sdk = types.ModuleType("anthropic")
        sdk.Anthropic = make_client
        sdk.APIError = FakeAPIError
        sdk.APIStatusError = FakeAPIStatusError
        sdk.AuthenticationError = FakeAuthenticationError
        sdk.PermissionDeniedError = FakePermissionDeniedError
        sdk.NotFoundError = FakeNotFoundError
        sdk.RateLimitError = FakeRateLimitError
        sdk.APIConnectionError = FakeAPIConnectionError
        sdk.APITimeoutError = FakeAPITimeoutError
        patcher = patch.dict(sys.modules, {"anthropic": sdk})
        patcher.start()
        self.addCleanup(patcher.stop)

    def adapter(self, **kwargs):
        values = {"kind": "anthropic", "model": "claude-opus-5-5", "api_key": "sk-ant-test", **kwargs}
        return AnthropicAdapter(base.ProviderConfig(**values))

    def test_stream(self):
        events = list(self.adapter().stream_chat(SYSTEM, TURNS))

        self.assertEqual([event["text"] for event in events[:-1]], ["Hello ", "there"])
        stop = events[-1]
        self.assertEqual(stop["reason"], base.STOP_END)
        self.assertEqual(stop["model"], "claude-opus-5-5")
        self.assertEqual(stop["request_id"], "req_123")
        self.assertEqual(stop["blocks"], FINAL["content"], "Blocks are stored as the provider sent them")
        self.assertEqual(stop["usage"], {
            "input_tokens": 10, "output_tokens": 20, "cache_read_tokens": 300, "cache_write_tokens": 40,
        })
        self.assertEqual(self.client_arguments, {"api_key": "sk-ant-test", "timeout": 60.0, "max_retries": 2})

    def test_request(self):
        list(self.adapter(max_output_tokens=2000).stream_chat(SYSTEM, TURNS))
        name, params = self.calls[0]

        self.assertEqual(name, "stable")
        self.assertEqual(params["model"], "claude-opus-5-5")
        self.assertEqual(params["max_tokens"], 2000)
        self.assertEqual(params["cache_control"], {"type": "ephemeral"})
        self.assertEqual(params["system"], [
            {"type": "text", "text": "You are Nudge.", "cache_control": {"type": "ephemeral"}},
        ])
        self.assertEqual(params["messages"], [
            {"role": "user", "content": [
                {"type": "text", "text": "<nudge_context>{}</nudge_context>\n\nHello"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "Hi!"}]},
            {"role": "user", "content": [{"type": "text", "text": "How are you?"}]},
        ])
        self.assertNotIn("thinking", params, "Nothing is sent that the model is not known to accept")
        self.assertNotIn("output_config", params)
        self.assertNotIn("temperature", params)

    def test_capabilities_decide_the_options(self):
        adapter = self.adapter(effort="low", capabilities=CAPABILITIES)
        list(adapter.stream_chat(SYSTEM, TURNS))
        params = self.calls[0][1]
        self.assertEqual(params["thinking"], {"type": "adaptive"})
        self.assertEqual(params["output_config"], {"effort": "low"})

        adapter = self.adapter(effort="max", capabilities=CAPABILITIES)
        list(adapter.stream_chat(SYSTEM, TURNS))
        self.assertNotIn("output_config", self.calls[1][1])

    def test_refusal_fallback(self):
        list(self.adapter(refusal_fallback=True).stream_chat(SYSTEM, TURNS))
        name, params = self.calls[0]
        self.assertEqual(name, "beta")
        self.assertEqual(params["fallbacks"], "default")
        self.assertEqual(params["betas"], ["server-side-fallback-2026-07-01"])

        adapter = self.adapter(refusal_fallback=True, model="claude-haiku-4-5")
        list(adapter.stream_chat(SYSTEM, TURNS))
        name, params = self.calls[1]
        self.assertEqual(name, "stable", "Only sent to the models that accept it")
        self.assertNotIn("fallbacks", params)

    def test_own_blocks_are_replayed(self):
        blocks = [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "text", "text": "Hi!", "citations": None},
        ]
        turns = [
            TURNS[0],
            {"role": "assistant", "text": "Hi!", "blocks": blocks, "provider_kind": "anthropic"},
            TURNS[2],
        ]
        list(self.adapter().stream_chat(SYSTEM, turns))
        self.assertEqual(self.calls[0][1]["messages"][1]["content"], [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "text", "text": "Hi!"},
        ])

    def test_sanitize_blocks_after_a_fallback(self):
        blocks = [
            {"type": "thinking", "thinking": "", "signature": "declined"},
            {"type": "text", "text": "Partial"},
            {"type": "fallback", "from": {"model": "a"}, "to": {"model": "b"}},
            {"type": "thinking", "thinking": "", "signature": "kept"},
            {"type": "text", "text": " answer"},
            {"type": "text", "text": "  "},
            {"type": "unknown_block", "data": 1},
        ]
        self.assertEqual(sanitize_blocks(blocks), [
            {"type": "text", "text": "Partial"},
            {"type": "thinking", "thinking": "", "signature": "kept"},
            {"type": "text", "text": " answer"},
        ])

    def test_stop_reasons(self):
        cases = {"refusal": base.STOP_REFUSAL, "max_tokens": base.STOP_MAX_TOKENS, "end_turn": base.STOP_END}
        for stop_reason, reason in cases.items():
            self.stream = FakeStream([], dict(FINAL, stop_reason=stop_reason))
            events = list(self.adapter().stream_chat(SYSTEM, TURNS))
            self.assertEqual(events[-1]["reason"], reason)

    def test_deadline(self):
        events = list(self.adapter().stream_chat(SYSTEM, TURNS, deadline=time.monotonic() - 1))
        self.assertEqual([event["type"] for event in events], ["stop"])
        self.assertEqual(events[0]["reason"], base.STOP_DEADLINE)
        self.assertEqual(events[0]["usage"]["input_tokens"], 9)

    def test_errors(self):
        cases = [
            (FakeAuthenticationError(401), base.ERROR_AUTH),
            (FakePermissionDeniedError(403), base.ERROR_AUTH),
            (FakeNotFoundError(404), base.ERROR_MODEL_NOT_FOUND),
            (FakeRateLimitError(429), base.ERROR_RATE_LIMIT),
            (FakeAPIStatusError(529), base.ERROR_UNAVAILABLE),
            (FakeAPIStatusError(400), base.ERROR_BAD_REQUEST),
            (FakeAPIConnectionError(), base.ERROR_NETWORK),
            (FakeAPITimeoutError(), base.ERROR_TIMEOUT),
        ]
        for error, code in cases:
            self.stream = FakeStream(["Hel"], FINAL, error=error)
            events = list(self.adapter().stream_chat(SYSTEM, TURNS))
            self.assertEqual([event["type"] for event in events], ["text", "error"])
            self.assertEqual(events[-1]["code"], code)

    def test_key_from_the_environment(self):
        list(self.adapter(api_key="").stream_chat(SYSTEM, TURNS))
        self.assertNotIn("api_key", self.client_arguments)

    def test_missing_library(self):
        with patch.dict(sys.modules, {"anthropic": None}):
            events = list(self.adapter().stream_chat(SYSTEM, TURNS))
        self.assertEqual(events, [base.error_event(base.ERROR_MISSING_LIBRARY, "pip install anthropic")])


TOOL = {
    "name": "find_screen",
    "description": "Find a screen.",
    "input_schema": {"type": "object", "properties": {"words": {"type": "string"}},
                     "required": ["words"]},
}


@tagged("post_install", "-at_install")
class TestToolChoice(BaseCase):
    """The person asked for the pointer: the model must call a tool."""

    def test_openai_compatible(self):
        adapter = OpenAICompatAdapter(base.ProviderConfig(
            kind="openai_compatible", model="test-model", api_key="sk-test",
            base_url="https://llm.invalid/v1/", timeout=30,
        ))
        payload = adapter._build_payload(SYSTEM, TURNS, tools=[TOOL], tool_choice="required")
        self.assertEqual(payload["tool_choice"], "required")
        self.assertNotIn("tool_choice", adapter._build_payload(SYSTEM, TURNS, tools=[TOOL]))
        self.assertEqual(adapter._build_payload(SYSTEM, TURNS, tools=[TOOL], tool_choice="none")["tool_choice"], "none")
        self.assertNotIn("tool_choice", adapter._build_payload(SYSTEM, TURNS, tool_choice="required"),
                         "Nothing to force without tools")

        # A server that does not know tool_choice is asked again without it
        posted = []
        responses = [
            FakeResponse(400, body={"error": {"message": "tool_choice is not supported"}}),
            FakeResponse(lines=sse(delta("Here."), delta(finish="stop"))),
        ]

        def post(payload, stream, read_timeout):
            posted.append(json.loads(json.dumps(payload)))
            return responses.pop(0)

        self.patch(adapter, "_post", post)
        events = list(adapter.stream_chat(SYSTEM, TURNS, tools=[TOOL], tool_choice="required"))
        self.assertEqual(events[-1]["type"], "stop")
        self.assertEqual(posted[0]["tool_choice"], "required")
        self.assertNotIn("tool_choice", posted[1])

    def test_anthropic(self):
        adapter = AnthropicAdapter(base.ProviderConfig(
            kind="anthropic", model="claude-test", api_key="sk-ant-test",
            base_url="https://llm.invalid/", timeout=30, capabilities=CAPABILITIES,
        ))
        params = adapter._build_params(SYSTEM, TURNS, tools=[TOOL], tool_choice="required")
        self.assertEqual(params["tool_choice"], {"type": "any"})
        self.assertNotIn("thinking", params, "The API refuses thinking before a forced call")
        params = adapter._build_params(SYSTEM, TURNS, tools=[TOOL])
        self.assertNotIn("tool_choice", params)
        self.assertEqual(params["thinking"], {"type": "adaptive"})
        params = adapter._build_params(SYSTEM, TURNS, tools=[TOOL], tool_choice="none")
        self.assertEqual(params["tool_choice"], {"type": "none"})
        self.assertEqual(params["thinking"], {"type": "adaptive"}, "Thinking before the answer is allowed")

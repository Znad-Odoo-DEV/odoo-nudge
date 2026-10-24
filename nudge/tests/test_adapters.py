import json
import logging
import socket
import sys
import threading
import time
import types
from unittest.mock import patch

import requests

from odoo.tests import BaseCase, tagged

from odoo.addons.nudge.llm import base, openai_compat_adapter, prompt, resilience
from odoo.addons.nudge.llm.anthropic_adapter import TOOL_NOTE, AnthropicAdapter, sanitize_blocks
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
    def __init__(self, status_code=200, lines=(), body=None, headers=None):
        self.status_code = status_code
        self.headers = headers or {}
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
        # A cool-down is remembered by the process: each test starts without one
        resilience.reset()
        self.addCleanup(resilience.reset)

    def respond(self, *responses):
        responses = list(responses)

        def post(payload, stream, read_timeout, **kwargs):
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

    def test_a_refusal_in_the_stream_is_a_refusal(self):
        chunk = {"model": "served-model", "choices": [{"index": 0, "delta": {"refusal": "I cannot help with that."}}]}
        self.respond(FakeResponse(lines=sse(chunk, delta(finish="stop"))))
        self.assertEqual(self.run_stream()[-1]["reason"], base.STOP_REFUSAL)

    def test_an_error_inside_the_stream_keeps_its_kind(self):
        self.respond(FakeResponse(lines=sse({"error": {"message": "slow down", "code": 429}})))
        events = self.run_stream()
        self.assertEqual([event["type"] for event in events], ["error"])
        self.assertEqual(events[0]["code"], base.ERROR_RATE_LIMIT)
        self.respond(FakeResponse(lines=sse({"error": {"message": "overloaded"}})))
        self.assertEqual(self.run_stream()[0]["code"], base.ERROR_UNAVAILABLE)

    def test_a_stream_cut_before_its_end_is_not_a_complete_answer(self):
        self.respond(FakeResponse(lines=sse(delta("Hel"), done=False)))
        events = self.run_stream()
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(events[-1]["code"], base.ERROR_NETWORK)

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

    def test_the_betas_of_claude_stay_with_claude(self):
        self.respond(FakeResponse(lines=sse(delta("ok", finish="stop"))))
        self.run_stream(tools=[{"name": "t", "description": "d", "input_schema": {"type": "object"}}])
        sent = json.dumps([self.posted[0], self.adapter._headers()]).lower()
        for word in ("anthropic-beta", "betas", "block_binding", "thinking-binding", "fallbacks"):
            self.assertNotIn(word, sent)

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
            resilience.reset()
            self.posted.clear()
            self.respond(FakeResponse(status, body={"error": {"message": f"failed {status}"}}))
            events = self.run_stream()
            self.assertEqual(events, [base.error_event(code, f"failed {status}")])
            self.assertEqual(len(self.posted), 1, "Asking again would not change the answer")

    def test_the_check_follows_the_effort(self):
        self.adapter = OpenAICompatAdapter(base.ProviderConfig(
            kind="openai_compatible", model="qwen3:4b", base_url="http://localhost:11434/v1", effort="none"))
        posted = []

        def post(payload, stream, read_timeout, **kwargs):
            posted.append(dict(payload))
            return FakeResponse(body={"choices": [{"message": {"content": "OK"}}]})

        self.patch(self.adapter, "_post", post)
        self.adapter.check()
        self.assertEqual(posted[0]["reasoning_effort"], "none")

        def silent(payload, stream, read_timeout, **kwargs):
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
        resilience.reset()
        self.respond(FakeResponse(429, body={"error": {"message": "Please retry in 5s."}}))
        self.assertEqual(self.run_stream(deadline=time.monotonic() + 8)[0]["code"], base.ERROR_RATE_LIMIT)
        self.assertEqual(self.slept, [])

        # Waited for once, not twice; and RetryInfo is read as well
        resilience.reset()
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
        def post(payload, stream, read_timeout, **kwargs):
            raise requests.exceptions.ConnectTimeout("refused for key sk-test")

        self.patch(self.adapter, "_post", post)
        with self.assertLogs("odoo.addons.nudge.llm.openai_compat_adapter", logging.INFO) as logs:
            events = self.run_stream()
        self.assertEqual(events, [base.error_event(base.ERROR_NETWORK, "ConnectTimeout")])
        self.assertIn("refused for key ***", logs.output[0])
        self.assertNotIn("sk-test", logs.output[0])

    def test_a_silent_provider_is_told_as_such(self):
        def post(payload, stream, read_timeout, **kwargs):
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
    def __init__(self, status_code=400, retry_after=None):
        super().__init__("failed")
        self.status_code = status_code
        if retry_after is not None:
            self.response = types.SimpleNamespace(headers={"retry-after": str(retry_after)})


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


def fake_sdk(make_client):
    """:return: a stand-in for the ``anthropic`` module, whose clients are
    made by ``make_client``"""
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
    return sdk


BINDING_BETA = "thinking-binding-controls-2026-08-01"
# A replayed thinking block whose prefix changed is dropped, not refused
THINKING = {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}}


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

        resilience.reset()
        self.addCleanup(resilience.reset)
        self.slept = []
        self.patch(time, "sleep", self.slept.append)
        self.client_arguments = {}
        self.checked = []
        self.check_error = None

        def retrieve(model):
            self.checked.append(("retrieve", model))
            return FakeMessage({"capabilities": CAPABILITIES})

        def count_tokens(**params):
            self.checked.append(("count_tokens", params))
            if self.check_error:
                raise self.check_error
            return types.SimpleNamespace(input_tokens=8)

        def make_client(**kwargs):
            self.client_arguments.update(kwargs)
            return types.SimpleNamespace(
                messages=types.SimpleNamespace(stream=record("stable"), count_tokens=count_tokens),
                beta=types.SimpleNamespace(messages=types.SimpleNamespace(stream=record("beta"))),
                models=types.SimpleNamespace(retrieve=retrieve),
            )

        patcher = patch.dict(sys.modules, {"anthropic": fake_sdk(make_client)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def adapter(self, **kwargs):
        values = {"kind": "anthropic", "model": "claude-opus-5-5", "api_key": "sk-ant-test", **kwargs}
        return AnthropicAdapter(base.ProviderConfig(**values))

    def test_a_window_that_was_too_small_is_a_cut_answer_and_unknown_reasons_end_it(self):
        for reason, expected in (("model_context_window_exceeded", base.STOP_MAX_TOKENS), ("new_reason", base.STOP_END)):
            self.stream = FakeStream(["Hello"], dict(FINAL, stop_reason=reason))
            events = list(self.adapter().stream_chat(SYSTEM, TURNS))
            self.assertEqual(events[-1]["reason"], expected)

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
        self.assertEqual(self.client_arguments, {
            "api_key": "sk-ant-test", "timeout": 60.0, "max_retries": 0,
            "default_headers": {"Accept-Encoding": "gzip, deflate"}})

    def test_request(self):
        list(self.adapter(max_output_tokens=2000).stream_chat(SYSTEM, TURNS))
        name, params = self.calls[0]

        self.assertEqual(name, "beta", "Every request goes where the thinking blocks are controlled")
        self.assertEqual(params["betas"], [BINDING_BETA])
        self.assertNotIn("fallbacks", params)
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

    def test_the_skills_of_a_question_come_after_the_cached_prefix(self):
        # NP-03: the skills change from one question to the next; the tools and the rules stay cached
        rules = ["You are Nudge.", "Talk mode: explain, and Raghad decides."]
        index = "## Skills\n- odoo-setup: Set Odoo up.\n"
        first = prompt.with_skills(rules, index, ["## Setting Odoo up\n- Ask first.\n"])
        second = prompt.with_skills(rules, index, ["## Importing files\n- Test first.\n"])
        self.assertEqual(first[:-1], second[:-1], "The blocks before the skills are the same for both questions")
        self.assertTrue(first[-1].startswith(prompt.SKILLS_GIVEN))
        self.assertEqual(rules, ["You are Nudge.", "Talk mode: explain, and Raghad decides."], "Not changed in place")
        list(self.adapter().stream_chat(first, TURNS))
        system = self.calls[0][1]["system"]
        self.assertEqual([bool(block.get("cache_control")) for block in system], [False, True, False])
        self.assertIn("## Skills", system[1]["text"], "The index is the same for every question: cached")
        self.assertIn("## Setting Odoo up", system[2]["text"])
        self.assertEqual([bool(block.get("cache_control")) for block in self.adapter()._build_system(rules)],
                         [False, True])
        # use_skill in the middle of an answer: one block of skills, a text already there not given twice
        again = prompt.with_skills(first, loaded=["## Setting Odoo up\n- Ask first.\n", "## Importing files\n- Test first.\n"])
        self.assertEqual(again[:-1], first[:-1])
        self.assertEqual(again[-1].count("## Setting Odoo up"), 1)
        self.assertIn("## Importing files", again[-1])
        # Every other adapter reads the same text, the skills last
        messages = OpenAICompatAdapter.__new__(OpenAICompatAdapter)._build_messages(again, [])
        self.assertTrue(messages[0]["content"].endswith("## Importing files\n- Test first.\n"))

    def test_capabilities_decide_the_options(self):
        adapter = self.adapter(effort="low", capabilities=CAPABILITIES)
        list(adapter.stream_chat(SYSTEM, TURNS))
        params = self.calls[0][1]
        self.assertEqual(params["thinking"], THINKING)
        self.assertEqual(params["output_config"], {"effort": "low"})

        adapter = self.adapter(effort="max", capabilities=CAPABILITIES)
        list(adapter.stream_chat(SYSTEM, TURNS))
        self.assertNotIn("output_config", self.calls[1][1])

    def test_refusal_fallback(self):
        list(self.adapter(refusal_fallback=True).stream_chat(SYSTEM, TURNS))
        name, params = self.calls[0]
        self.assertEqual(name, "beta")
        self.assertEqual(params["fallbacks"], "default")
        self.assertEqual(params["betas"], [BINDING_BETA, "server-side-fallback-2026-07-01"])

        adapter = self.adapter(refusal_fallback=True, model="claude-haiku-4-5")
        list(adapter.stream_chat(SYSTEM, TURNS))
        name, params = self.calls[1]
        self.assertEqual(params["betas"], [BINDING_BETA])
        self.assertNotIn("fallbacks", params, "Only sent to the models that accept it")

    def test_thinking_survives_a_changed_prefix(self):
        # The system prompt, the tools and the notes are rebuilt between two
        # requests: from the second turn on, an account made since 2026-08-31
        # was answered "The block is bound to a different conversation"
        blocks = [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "text", "text": "Hi!"},
        ]
        turns = [
            TURNS[0],
            {"role": "assistant", "text": "Hi!", "blocks": blocks, "provider_kind": "anthropic"},
            TURNS[2],
        ]
        for system in (SYSTEM, [*SYSTEM, "Where the work is: Sales."]):
            list(self.adapter(capabilities=CAPABILITIES).stream_chat(system, turns, tools=[TOOL]))
        for name, params in self.calls:
            self.assertEqual(name, "beta")
            self.assertIn(BINDING_BETA, params["betas"])
            self.assertEqual(params["thinking"], THINKING)
            self.assertEqual(params["messages"][1]["content"][0], blocks[0],
                             "Replayed all the same: the API drops the ones it cannot use")

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
            resilience.reset()
            self.stream = FakeStream(["Hel"], FINAL, error=error)
            events = list(self.adapter().stream_chat(SYSTEM, TURNS))
            self.assertEqual([event["type"] for event in events], ["text", "error"])
            self.assertEqual(events[-1]["code"], code)

    def test_what_the_connection_failed_on(self):
        # "The provider could not be reached. APIConnectionError" was all that
        # was told of a key the server took: the SDK raises it for whatever
        # fails once the request is sent
        error = FakeAPIConnectionError()
        error.__cause__ = ValueError("bad gzip\nfor sk-ant-test")
        self.stream = FakeStream(["Hel"], FINAL, error=error)
        with self.assertLogs("odoo.addons.nudge.llm.anthropic_adapter", logging.INFO) as logs:
            events = list(self.adapter().stream_chat(SYSTEM, TURNS))
        detail = "FakeAPIConnectionError: ValueError: bad gzip for ***"
        self.assertEqual(events[-1], base.error_event(base.ERROR_NETWORK, detail))
        self.assertIn(detail, logs.output[0])
        self.assertNotIn("sk-ant-test", logs.output[0])

        # Test Connection says it too, and so does a timeout, from the first
        # error under it that has words
        timeout = FakeAPITimeoutError()
        timeout.__cause__ = TimeoutError()
        timeout.__cause__.__cause__ = OSError("The read operation timed out")
        cases = [
            (error, base.ERROR_NETWORK, detail),
            (timeout, base.ERROR_TIMEOUT, "FakeAPITimeoutError: OSError: The read operation timed out"),
            (FakeAPIConnectionError(), base.ERROR_NETWORK, "FakeAPIConnectionError"),
        ]
        for raised, code, said in cases:
            self.check_error = raised
            with self.assertRaises(base.AdapterError) as caught:
                self.adapter().check()
            self.assertEqual((caught.exception.code, caught.exception.detail), (code, said))

    def test_a_key_of_no_workspace(self):
        # A user key (sk-ant-usr-...) belongs to no workspace: every request
        # names one, or is answered with a 400
        list(self.adapter().stream_chat(SYSTEM, TURNS))
        self.assertNotIn("anthropic-workspace-id", self.client_arguments["default_headers"],
                         "Nothing is sent when none is set")
        list(self.adapter(workspace_id="wrkspc_01abc").stream_chat(SYSTEM, TURNS))
        self.assertEqual(self.client_arguments["default_headers"]["anthropic-workspace-id"], "wrkspc_01abc")

        error = FakeAPIStatusError(400)
        error.message = (
            "This API key is not scoped to a workspace, so this request must include the "
            "anthropic-workspace-id header with the ID of the workspace to use.")
        self.stream = FakeStream([], FINAL, error=error)
        events = list(self.adapter().stream_chat(SYSTEM, TURNS))
        self.assertEqual(events[-1], base.error_event(base.ERROR_WORKSPACE, error.message))

    def test_answers_never_in_brotli(self):
        # The brotli of many servers predates the keyword the SDK's HTTP client
        # decodes with: every answer that came back compressed with it failed
        list(self.adapter().stream_chat(SYSTEM, TURNS))
        self.assertEqual(self.client_arguments["default_headers"]["Accept-Encoding"], "gzip, deflate")
        list(self.adapter(workspace_id="wrkspc_01abc").stream_chat(SYSTEM, TURNS))
        self.assertEqual(self.client_arguments["default_headers"]["Accept-Encoding"], "gzip, deflate",
                         "Also next to a workspace")

    def test_check_asks_the_model(self):
        # Test Connection only looked the model up: a key that could not ask
        # it anything passed
        self.adapter().check()
        self.assertEqual(self.checked, [
            ("retrieve", "claude-opus-5-5"),
            ("count_tokens", {"model": "claude-opus-5-5", "messages": [{"role": "user", "content": "ping"}]}),
        ])
        self.assertFalse(self.calls, "Counted, not answered: nothing is paid")
        for error, code in ((FakePermissionDeniedError(403), base.ERROR_AUTH),
                            (FakeNotFoundError(404), base.ERROR_MODEL_NOT_FOUND)):
            self.check_error = error
            with self.assertRaises(base.AdapterError) as caught:
                self.adapter().check()
            self.assertEqual(caught.exception.code, code)

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

        def post(payload, stream, read_timeout, **kwargs):
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
        # Opus 5.5, Sonnet 5.5 and Fable 5.1 answer a forced call with a 400:
        # the tool is asked for in words, and the model thinks before it
        params = adapter._build_params(SYSTEM, TURNS, tools=[TOOL], tool_choice="required")
        self.assertNotIn("tool_choice", params, "Nothing forced is sent")
        self.assertEqual(params["thinking"], THINKING)
        self.assertEqual(len(params["messages"]), len(TURNS) + 1)
        self.assertEqual(params["messages"][-1], {"role": "user", "content": [{"type": "text", "text": TOOL_NOTE}]})
        self.assertNotIn(TOOL_NOTE, str(adapter._build_params(SYSTEM, TURNS, tool_choice="required")),
                         "Nothing to ask for without tools")
        params = adapter._build_params(SYSTEM, TURNS, tools=[TOOL])
        self.assertNotIn("tool_choice", params)
        self.assertEqual(params["thinking"], THINKING)
        self.assertNotIn(TOOL_NOTE, str(params["messages"]))
        params = adapter._build_params(SYSTEM, TURNS, tools=[TOOL], tool_choice="none")
        self.assertEqual(params["tool_choice"], {"type": "none"})
        self.assertEqual(params["thinking"], THINKING, "Thinking before the answer is allowed")


@tagged("post_install", "-at_install")
class TestOneShot(BaseCase):
    """One question with no conversation: what the translation, the AI over a list and the shop builder use."""

    def test_a_system_prompt_given_as_one_text_reaches_the_adapter_whole(self):
        from odoo.addons.nudge.llm import oneshot

        seen = []

        class Adapter:
            def stream_chat(self, system, turns, deadline=None, tools=None):
                seen.append(system)
                yield {"type": "text", "text": "Raghad"}
                yield {"type": "stop", "usage": base.normalize_usage(None), "model": "m"}

        answer, _usage, _model = oneshot.ask(Adapter(), "Translate for Ebrahim.", "Hello")
        self.assertEqual(answer, "Raghad")
        self.assertEqual(seen, [["Translate for Ebrahim."]])

    def test_claude_gets_one_block_for_one_text_and_none_for_blanks(self):
        adapter = AnthropicAdapter.__new__(AnthropicAdapter)
        blocks = adapter._build_system(["Translate for Raghad.", " ", ""])
        self.assertEqual([block["text"] for block in blocks], ["Translate for Raghad."])

    def test_every_adapter_reads_a_system_prompt_given_as_one_text_whole(self):
        # as the document reader, the digest and the provider's own one-off calls give it
        self.assertEqual(base.system_texts("Read Raghad's bill."), ["Read Raghad's bill."])
        self.assertEqual(base.system_texts(["A", "B"]), ["A", "B"])
        adapter = OpenAICompatAdapter.__new__(OpenAICompatAdapter)
        messages = adapter._build_messages(base.system_texts("Read Ebrahim's bill."), [])
        self.assertEqual(messages[0], {"role": "system", "content": "Read Ebrahim's bill."})


# ----------------------------------------------------------------------
# Deadlines and cool-down: no provider is called, a local socket stands in
# ----------------------------------------------------------------------

class SilentServer:
    """A local server that takes a request, then answers late or never.

    :param first: the bytes sent once the request is read (None: nothing)
    :param pause: seconds to wait before sending ``rest``
    :param rest: the bytes sent after the pause (None: nothing, the
        connection is held until the server is closed)"""

    HEAD = b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n"

    def __init__(self, first=None, pause=0.0, rest=None):
        self.first, self.pause, self.rest = first, pause, rest
        self.stop = threading.Event()
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.url = f"http://127.0.0.1:{self.listener.getsockname()[1]}/v1"
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        try:
            conn, _address = self.listener.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(5)
            try:
                conn.recv(65536)
                if self.first:
                    conn.sendall(self.first)
                if self.rest:
                    self.stop.wait(self.pause)
                    conn.sendall(self.rest)
                self.stop.wait(8)
            except OSError:
                pass

    def close(self):
        self.stop.set()
        self.listener.close()


@tagged("post_install", "-at_install")
class TestProviderDeadlines(BaseCase):

    def setUp(self):
        super().setUp()
        resilience.reset()
        self.addCleanup(resilience.reset)
        self.slept = []
        self.patch(time, "sleep", self.slept.append)

    def openai(self, url="https://llm.invalid/v1", key="sk-test", **kwargs):
        return OpenAICompatAdapter(base.ProviderConfig(
            kind="openai_compatible", model="m", api_key=key, base_url=url, timeout=30, **kwargs))

    def serve(self, **kwargs):
        server = SilentServer(**kwargs)
        self.addCleanup(server.close)

        # The test framework forbids requests to the network and does not know
        # a (connect, read) timeout: the local socket is reached through the
        # transport adapter itself
        def post(url, json=None, headers=None, stream=False, timeout=None, **_kwargs):
            prepared = requests.Request("POST", url, json=json, headers=headers).prepare()
            return requests.adapters.HTTPAdapter().send(prepared, stream=stream, timeout=timeout)

        self.patch(openai_compat_adapter.requests, "post", post)
        return server

    # -- OpenAI-compatible --------------------------------------------
    def test_the_connection_is_cut_to_the_deadline(self):
        seen = []

        def post(url, **kwargs):
            seen.append(kwargs["timeout"])
            return FakeResponse(lines=sse(delta("ok", finish="stop")))

        self.patch(openai_compat_adapter.requests, "post", post)
        list(self.openai().stream_chat(SYSTEM, TURNS))
        list(self.openai().stream_chat(SYSTEM, TURNS, deadline=time.monotonic() + 3))
        self.assertEqual(seen[0][0], openai_compat_adapter.CONNECT_TIMEOUT)
        self.assertLessEqual(seen[1][0], 3.0, "Connecting may not take longer than the job has")
        self.assertLessEqual(seen[1][1], 3.0)

    def test_a_provider_silent_after_connecting_ends_at_the_deadline(self):
        server = self.serve()
        started = time.monotonic()
        events = list(self.openai(server.url).stream_chat(SYSTEM, TURNS, deadline=started + 1.5))
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual([event["type"] for event in events], ["error"])
        self.assertEqual(events[0]["code"], base.ERROR_TIMEOUT)

    def test_a_stream_gone_silent_between_two_chunks_ends_at_the_deadline(self):
        chunk = b"\n".join(sse(delta("Hel"), done=False)) + b"\n"
        # The transport reads 64 bytes at a time: a chunk of a whole number of them is seen at once
        chunk = chunk[:-2] + b" " * (-len(chunk) % 64) + chunk[-2:]
        # The first chunk comes at once, the next one after the deadline
        server = self.serve(first=SilentServer.HEAD + chunk, pause=4.0, rest=chunk)
        started = time.monotonic()
        events = list(self.openai(server.url).stream_chat(SYSTEM, TURNS, deadline=started + 1.5))
        self.assertLess(time.monotonic() - started, 3.5, "The wait for the next chunk is cut with the deadline")
        self.assertEqual(events[0], base.text_event("Hel"))
        self.assertEqual(events[-1]["type"], "stop")
        self.assertEqual(events[-1]["reason"], base.STOP_DEADLINE)

    def test_a_wait_that_does_not_fit_the_deadline_fails_fast_as_busy(self):
        limited = FakeResponse(429, body={"error": {"message": "slow down"}}, headers={"Retry-After": "120"})
        adapter = self.openai()
        self.patch(adapter, "_post", lambda *args, **kwargs: limited)
        for deadline in (None, time.monotonic() + 40):
            resilience.reset()
            events = list(adapter.stream_chat(SYSTEM, TURNS, deadline=deadline))
            self.assertEqual([event["code"] for event in events], [base.ERROR_RATE_LIMIT])
            self.assertEqual(events[0]["detail"], "The provider is busy, try again in 120 s.")
        self.assertEqual(self.slept, [])
        self.assertTrue(limited.closed)

    def test_a_cool_down_is_shared_by_the_next_requests(self):
        limited = FakeResponse(429, body={"error": {"message": "slow down"}}, headers={"Retry-After": "45"})
        posted = []

        def post(payload, stream, read_timeout, **kwargs):
            posted.append(payload)
            return limited

        first, second = self.openai(), self.openai()
        self.patch(first, "_post", post)
        self.patch(second, "_post", post)
        self.assertEqual(list(first.stream_chat(SYSTEM, TURNS))[0]["code"], base.ERROR_RATE_LIMIT)
        self.assertEqual(len(posted), 1)
        # Another adapter, another request: the provider is not asked at all
        events = list(second.stream_chat(SYSTEM, TURNS))
        self.assertEqual(len(posted), 1, "A provider that asked for peace is left alone")
        self.assertEqual(events[0]["code"], base.ERROR_RATE_LIMIT)
        self.assertRegex(events[0]["detail"], r"^The provider is busy, try again in \d+ s\.$")
        # Another account is not held
        other = self.openai(key="sk-other")
        self.patch(other, "_post", lambda *a, **k: FakeResponse(lines=sse(delta("ok", finish="stop"))))
        self.assertEqual(list(other.stream_chat(SYSTEM, TURNS))[-1]["type"], "stop")
        # And the cool-down ends
        resilience.reset()
        self.patch(first, "_post", lambda *a, **k: FakeResponse(lines=sse(delta("ok", finish="stop"))))
        self.assertEqual(list(first.stream_chat(SYSTEM, TURNS))[-1]["type"], "stop")

    def test_a_voice_request_is_held_by_the_cool_down_and_bounded(self):
        adapter = self.openai(stt_mode=base.STT_ENDPOINT, stt_model="listen")
        seen = []

        def post(url, **kwargs):
            seen.append(kwargs["timeout"])
            return FakeResponse(429, body={"error": {"message": "slow"}}, headers={"Retry-After": "90"})

        self.patch(openai_compat_adapter.requests, "post", post)
        with self.assertRaises(base.AdapterError) as capture:
            adapter.transcribe(b"RIFF", deadline=time.monotonic() + 5)
        self.assertEqual(capture.exception.code, base.ERROR_RATE_LIMIT)
        self.assertLessEqual(seen[0][0], 5.0)
        with self.assertRaises(base.AdapterError):
            adapter.transcribe(b"RIFF")
        self.assertEqual(len(seen), 1, "The second request did not reach the provider")
        self.assertEqual(self.slept, [])

    def test_a_server_error_is_retried_only_while_the_deadline_allows(self):
        responses = [FakeResponse(503, body={"error": {"message": "high demand"}}) for _i in range(3)]
        adapter = self.openai()
        self.patch(adapter, "_post", lambda *a, **k: responses.pop(0))
        events = list(adapter.stream_chat(SYSTEM, TURNS, deadline=time.monotonic() + 9))
        self.assertEqual(events[0]["code"], base.ERROR_UNAVAILABLE)
        self.assertEqual(self.slept, [], "No sleep that leaves less than the answer needs")
        self.assertGreater(resilience.cooldown_left(resilience.cooldown_key(adapter.config)), 0)

    # -- Ollama ------------------------------------------------------------
    def test_ollama_calls_are_cut_to_the_deadline(self):
        from odoo.addons.nudge.llm import ollama  # noqa: PLC0415
        seen = []

        def request(method, url, **kwargs):
            seen.append(kwargs["timeout"])
            return FakeResponse(body={"message": {"content": "A red chair"}})

        self.patch(ollama.requests, "request", request)
        ollama.read_picture("http://localhost:11434/v1", "vision", "AAAA")
        ollama.read_picture("http://localhost:11434/v1", "vision", "AAAA", deadline=time.monotonic() + 4)
        self.assertEqual(seen[0], (ollama.CONNECT_TIMEOUT, ollama.PICTURE_TIMEOUT))
        self.assertLessEqual(seen[1][0], 4.0)
        self.assertLessEqual(seen[1][1], 4.0)

    # -- Anthropic -----------------------------------------------------------
    def anthropic(self, **kwargs):
        calls = {"clients": 0, "arguments": {}}
        self.stream = FakeStream(["Hel"], FINAL)

        def make_client(**arguments):
            calls["clients"] += 1
            calls["arguments"].update(arguments)
            return types.SimpleNamespace(beta=types.SimpleNamespace(
                messages=types.SimpleNamespace(stream=lambda **params: self.stream)))

        patcher = patch.dict(sys.modules, {"anthropic": fake_sdk(make_client)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sdk_calls = calls
        return AnthropicAdapter(base.ProviderConfig(
            kind="anthropic", model="claude-opus-5-5", api_key="sk-ant-test", **kwargs))

    def test_claude_connects_and_waits_inside_the_deadline_without_sdk_retries(self):
        adapter = self.anthropic(timeout=60)
        list(adapter.stream_chat(SYSTEM, TURNS, deadline=time.monotonic() + 6))
        arguments = self.sdk_calls["arguments"]
        self.assertEqual(arguments["max_retries"], 0, "Retries are Nudge's, inside the time left")
        timeout = arguments["timeout"]
        self.assertLessEqual(getattr(timeout, "connect", timeout), 6.0)
        self.assertLessEqual(getattr(timeout, "read", timeout), 6.0)

    def test_claude_busy_beyond_the_deadline_fails_fast_and_is_remembered(self):
        adapter = self.anthropic()
        self.stream = FakeStream([], FINAL, error=FakeRateLimitError(429, retry_after=120))
        events = list(adapter.stream_chat(SYSTEM, TURNS, deadline=time.monotonic() + 40))
        self.assertEqual([event["code"] for event in events], [base.ERROR_RATE_LIMIT])
        self.assertRegex(events[0]["detail"], r"^The provider is busy, try again in \d+ s\.$")
        self.assertEqual(self.slept, [])
        clients = self.sdk_calls["clients"]
        events = list(AnthropicAdapter(adapter.config).stream_chat(SYSTEM, TURNS))
        self.assertEqual(events[0]["code"], base.ERROR_RATE_LIMIT)
        self.assertEqual(self.sdk_calls["clients"], clients, "The provider is not asked during the cool-down")

    def test_claude_overloaded_is_asked_again_inside_the_deadline(self):
        adapter = self.anthropic()
        self.stream = FakeStream([], FINAL, error=FakeAPIStatusError(529))

        def sleep(seconds):
            self.slept.append(seconds)
            self.stream = FakeStream(["Hel"], FINAL)

        self.patch(time, "sleep", sleep)
        events = list(adapter.stream_chat(SYSTEM, TURNS, deadline=time.monotonic() + 120))
        self.assertEqual([event["type"] for event in events], ["text", "stop"])
        self.assertEqual(self.slept, [1.0])

        # Not when it would leave no time for the answer
        resilience.reset()
        self.slept.clear()
        self.stream = FakeStream([], FINAL, error=FakeAPIStatusError(529))
        events = list(adapter.stream_chat(SYSTEM, TURNS, deadline=time.monotonic() + 5))
        self.assertEqual(events[0]["code"], base.ERROR_UNAVAILABLE)
        self.assertEqual(self.slept, [])

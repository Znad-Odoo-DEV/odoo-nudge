"""Adapter for any server that speaks the OpenAI chat-completions protocol:
Google Gemini, Groq, OpenRouter, Ollama, OpenAI itself.

Uses ``requests`` (already an Odoo dependency) so that trying a free provider
needs no extra package.
"""
import base64
import json
import logging
import re
import time

import requests

from . import base, resilience, transcription
from . import outbound
from .prompt import question_parts, render_user_text

_logger = logging.getLogger(__name__)

CONNECT_TIMEOUT = 10

# A busy provider often accepts the same request a moment later. Free tiers
# answer "overloaded" many times a day.
RETRY_STATUSES = frozenset({500, 502, 503, 504, 529})
RETRY_DELAYS = (1.0, 3.0)
# A limit per minute (Gemini's free tier: tokens a minute) says how long to
# wait: waited once when it is this short and the answer has the time, with
# this much left for the answer itself
RATE_LIMITED = 429
MAX_RATE_WAIT = 30.0
MIN_ANSWER_SECONDS = 10.0
RETRY_IN = re.compile(r"retry in ([0-9]+(?:\.[0-9]+)?)\s*s", re.IGNORECASE)
RETRY_DELAY = re.compile(r'"retryDelay"\s*:\s*"([0-9]+(?:\.[0-9]+)?)s"')
# What the provider is asked to spend on reasoning, in the words of the
# OpenAI API, which Gemini and OpenAI read
# "none": no reasoning at all, which Ollama and OpenAI read; a thinking
# model on a laptop answers in seconds instead of a minute
REASONING_EFFORT = {"none": "none", "low": "low", "medium": "medium", "high": "high", "xhigh": "high",
                    "max": "high"}

FINISH_REASONS = {
    "stop": base.STOP_END,
    "length": base.STOP_MAX_TOKENS,
    "content_filter": base.STOP_REFUSAL,
    "tool_calls": base.STOP_TOOL_USE,
}

STATUS_ERRORS = {
    401: base.ERROR_AUTH,
    403: base.ERROR_AUTH,
    404: base.ERROR_MODEL_NOT_FOUND,
    429: base.ERROR_RATE_LIMIT,
}


def _error_code(status):
    if status in STATUS_ERRORS:
        return STATUS_ERRORS[status]
    if status >= 500:
        return base.ERROR_UNAVAILABLE
    return base.ERROR_BAD_REQUEST


def _chunk_error_code(error):
    """:return: the code of an error sent inside the stream, from its status or code"""
    if isinstance(error, dict):
        for key in ("status", "code"):
            try:
                return _error_code(int(error.get(key)))
            except (TypeError, ValueError):
                continue
        if "rate" in str(error.get("code") or error.get("type") or "").lower():
            return base.ERROR_RATE_LIMIT
    return base.ERROR_UNAVAILABLE


def _error_detail(response):
    try:
        body = response.json()
    except ValueError:
        return (response.text or "")[:500]
    # Gemini wraps the error object in a list
    if isinstance(body, list) and body:
        body = body[0]
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return error.get("message") or json.dumps(error)[:500]
    return str(error or body)[:500]


def rate_wait(response):
    """:return: the seconds a provider at its limit asks to wait, or None
    when it does not say"""
    header = (getattr(response, "headers", None) or {}).get("Retry-After")
    if header:
        try:
            return max(float(header), 0.0)
        except ValueError:
            pass
    try:
        text = response.text or ""
    except Exception:  # noqa: BLE001
        return None
    # Gemini says it in words, and as RetryInfo: "retryDelay": "17s"
    found = RETRY_IN.search(text) or RETRY_DELAY.search(text)
    return float(found.group(1)) + 1.0 if found else None


@base.register
class OpenAICompatAdapter(base.BaseAdapter):
    kind = "openai_compatible"

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------
    def _url(self, path):
        return self.config.base_url.rstrip("/") + path

    def _headers(self, json_body=True):
        # The type of a form with a file in it is written by ``requests``
        headers = {"Content-Type": "application/json"} if json_body else {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _private_ok(self, url=None):
        """The address was checked and is the one connected to (S11). An address on our own network is for a
        provider the admin said runs there, or one that looks like it; any other name must resolve to a public one."""
        return bool(self.config.runs_locally) or outbound.is_own_network(url or self.config.base_url)

    def _post(self, payload, stream, read_timeout, deadline=None):
        return outbound.post(
            self._url("/chat/completions"),
            allow_private=self._private_ok(),
            json=payload,
            headers=self._headers(),
            stream=stream,
            timeout=(resilience.connect_timeout(deadline, CONNECT_TIMEOUT), read_timeout),
        )

    def _redact(self, value):
        """The key travels in a header, not in the address. Should a server or
        a proxy echo it all the same, it stays out of the logs."""
        text = str(value)
        if self.config.api_key:
            text = text.replace(self.config.api_key, "***")
        return text

    def _get(self, path):
        return outbound.get(
            self._url(path),
            allow_private=self._private_ok(),
            headers=self._headers(),
            timeout=(CONNECT_TIMEOUT, self.config.timeout),
        )

    # ------------------------------------------------------------------
    # Request building
    # ------------------------------------------------------------------
    @staticmethod
    def _render_call(call):
        rendered = {
            "id": call["id"],
            "type": "function",
            "function": {
                "name": call["name"],
                "arguments": json.dumps(call.get("input") or {}, sort_keys=True, ensure_ascii=False),
            },
        }
        # Gemini signs its calls and reads the signature back
        rendered.update(call.get("extra") or {})
        return rendered

    def _build_messages(self, system, turns):
        messages = []
        # S8: these servers cache the prompt by its prefix: the skills of the question go with it,
        # after the system prompt and the conversation every later question shares
        system, asked, at = question_parts(system, turns)
        system_text = "\n\n".join(block for block in system if block)
        if system_text:
            messages.append({"role": "system", "content": system_text})
        for index, turn in enumerate(turns):
            role = turn["role"]
            if role == "tool":
                messages.extend(
                    {"role": "tool", "tool_call_id": result["id"], "content": result["content"]}
                    for result in turn.get("results") or []
                )
                continue
            text = render_user_text(turn) if role == "user" else turn["text"]
            if index == at:
                text = asked.strip("\n") + "\n\n" + text
            calls = turn.get("tool_calls") if role == "assistant" else None
            if calls:
                messages.append({
                    "role": "assistant",
                    "content": text or None,
                    "tool_calls": [self._render_call(call) for call in calls],
                })
                continue
            if not text:
                continue
            images = turn.get("images") if role == "user" else None
            if images:
                # An image with the words: a message of its own
                messages.append({"role": role, "content": [
                    {"type": "text", "text": text},
                    *({"type": "image_url", "image_url": {
                        "url": f"data:{image['media_type']};base64,{image['data']}"}}
                      for image in images),
                ]})
                continue
            last = messages[-1] if messages else {}
            if last.get("role") == role and "tool_calls" not in last\
                    and isinstance(last.get("content"), str):
                # Some servers reject two turns in a row from the same role
                last["content"] += "\n\n" + text
            else:
                messages.append({"role": role, "content": text})
        return messages

    def _build_payload(self, system, turns, tools=None, tool_choice=None):
        payload = {
            "model": self.config.model,
            "messages": self._build_messages(system, turns),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool["description"],
                        "parameters": tool["input_schema"],
                    },
                }
                for tool in tools
            ]
            if tool_choice in ("required", "none"):
                payload["tool_choice"] = tool_choice
        if self.config.max_output_tokens > 0:
            payload["max_tokens"] = self.config.max_output_tokens
        if self.config.effort in REASONING_EFFORT:
            payload["reasoning_effort"] = REASONING_EFFORT[self.config.effort]
        return payload

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    def _retry(self, send, deadline):
        """Send a request, again when the refusal is of a kind that passes.

        Nothing waits longer than the deadline allows, and a provider that
        said it is busy is left alone by the next requests of the process.

        :param send: callable sending the request and returning the response
        :raise AdapterError: when the provider is busy and the wait does not fit
        """
        key = resilience.cooldown_key(self.config)
        left = resilience.cooldown_left(key)
        if left > 0:
            raise base.AdapterError(base.ERROR_RATE_LIMIT, resilience.busy_message(left))
        delays = list(RETRY_DELAYS)
        waited = False
        while True:
            response = send()
            if response.status_code == RATE_LIMITED:
                delay = rate_wait(response)
                if not waited and delay is not None and delay <= MAX_RATE_WAIT and resilience.fits(delay, deadline):
                    _logger.info(
                        "Nudge: %s is at its limit, trying again in %.0fs", self.config.base_url, delay)
                    response.close()
                    time.sleep(delay)
                    waited = True
                    continue
                resilience.start_cooldown(
                    key, delay if delay is not None else resilience.DEFAULT_RATE_COOLDOWN)
                if delay is not None:
                    response.close()
                    raise base.AdapterError(base.ERROR_RATE_LIMIT, resilience.busy_message(delay))
                return response
            if response.status_code not in RETRY_STATUSES:
                return response
            if not delays or not resilience.fits(delays[0], deadline):
                resilience.start_cooldown(key, resilience.DEFAULT_ERROR_COOLDOWN)
                return response
            delay = delays.pop(0)
            _logger.info(
                "Nudge: %s answered %s, trying again in %ss",
                self.config.base_url, response.status_code, delay)
            response.close()
            time.sleep(delay)

    def _open(self, payload, deadline):
        """Nothing has been shown to the user at this point, so sending the
        request again cannot produce an answer twice."""
        def send():
            read_timeout = base.remaining(deadline, self.config.timeout)
            self._read_timeout = read_timeout
            response = self._post(payload, stream=True, read_timeout=read_timeout, deadline=deadline)
            if response.status_code == 400 and "does not support tools" in (response.text or ""):
                # Nothing taken out of the request changes that: told as it is
                return response
            if response.status_code == 400 and "reasoning_effort" in payload:
                # A model without reasoning, or a server that does not
                # know the word: once more without it
                response.close()
                payload.pop("reasoning_effort")
                return send()
            if response.status_code == 400 and "tool_choice" in payload:
                # Not every server knows ``tool_choice``: once more without,
                # the model is then only asked
                response.close()
                payload.pop("tool_choice")
                return send()
            if response.status_code == 400 and "stream_options" in payload:
                # Nor ``stream_options``
                response.close()
                payload.pop("stream_options")
                return send()
            return response

        return self._retry(send, deadline)

    def stream_chat(self, system, turns, deadline=None, tools=None, tool_choice=None):
        payload = self._build_payload(base.system_texts(system), turns, tools, tool_choice)
        response = None
        try:
            response = self._open(payload, deadline)
            if response.status_code != 200:
                detail = _error_detail(response)
                code = _error_code(response.status_code)
                if response.status_code == 400 and "does not support tools" in detail:
                    # Ollama, for a model made without tool calling
                    code = base.ERROR_UNSUPPORTED
                yield base.error_event(code, detail)
                return
            # A provider gone silent between two chunks ends with the deadline
            with resilience.watch(deadline, response.close):
                yield from self._consume(response, deadline)
        except base.AdapterError as exc:
            # Busy: told at once, with the time to wait
            yield base.error_event(exc.code, exc.detail)
        except requests.exceptions.ReadTimeout as exc:
            # Reached, and silent: the time given was not enough
            waited = int(getattr(self, "_read_timeout", 0) or 0)
            _logger.info(
                "Nudge: request to %s failed: %s", self.config.base_url, self._redact(exc))
            yield base.error_event(base.ERROR_TIMEOUT, f"no answer within {waited}s" if waited else "ReadTimeout")
        except requests.exceptions.RequestException as exc:
            _logger.info(
                "Nudge: request to %s failed: %s", self.config.base_url, self._redact(exc))
            if response is not None and base.expired(deadline):
                # The watchdog closed a stream that had gone silent
                yield base.stop_event(base.STOP_DEADLINE)
                return
            yield base.error_event(base.ERROR_NETWORK, type(exc).__name__)
        finally:
            if response is not None:
                response.close()

    @staticmethod
    def _collect_calls(calls, deltas):
        """Tool calls arrive whole (Gemini) or in pieces to put together by
        their index (OpenAI)."""
        for delta in deltas:
            if not isinstance(delta, dict):
                continue
            function = delta.get("function") or {}
            if "index" in delta:
                key = ("index", delta["index"])
            elif delta.get("id"):
                key = ("id", delta["id"])
            else:
                key = next(reversed(calls), ("index", 0))
            call = calls.setdefault(key, {"id": "", "name": "", "arguments": "", "extra": {}})
            call["id"] = delta.get("id") or call["id"]
            call["name"] = function.get("name") or call["name"]
            call["arguments"] += function.get("arguments") or ""
            for name, value in delta.items():
                if name not in ("index", "id", "type", "function"):
                    call["extra"][name] = value

    @staticmethod
    def _tool_events(calls):
        for position, call in enumerate(calls.values()):
            invalid = ""
            try:
                arguments = json.loads(call["arguments"] or "{}")
            except ValueError:
                arguments, invalid = {}, "The arguments are not valid JSON."
            yield base.tool_use_event(
                call["id"] or f"call_{position}", call["name"], arguments,
                extra=call["extra"], invalid=invalid)

    @staticmethod
    def _lines(response, deadline):
        """The lines of the stream; a stream the watchdog closed at the
        deadline just ends, whatever the transport raises for it."""
        try:
            yield from response.iter_lines(chunk_size=64)
        except (requests.exceptions.RequestException, ValueError, OSError, AttributeError):
            if not base.expired(deadline):
                raise

    def _consume(self, response, deadline):
        usage = {}
        finish = None
        model = ""
        calls = {}
        finished = False
        refused = False
        for raw in self._lines(response, deadline):
            if base.expired(deadline):
                yield base.stop_event(base.STOP_DEADLINE, usage=usage, model=model)
                return
            if not raw:
                continue
            line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                finished = True
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                continue
            if not isinstance(chunk, dict):
                continue
            if chunk.get("error"):
                error = chunk["error"]
                detail = error.get("message") if isinstance(error, dict) else error
                yield base.error_event(_chunk_error_code(error), detail)
                return
            model = chunk.get("model") or model
            if chunk.get("usage"):
                usage = self._normalize_usage(chunk["usage"])
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                text = delta.get("content")
                if isinstance(text, str) and text:
                    yield base.text_event(text)
                self._collect_calls(calls, delta.get("tool_calls") or [])
                if delta.get("refusal"):
                    refused = True
                finish = choice.get("finish_reason") or finish
        if not finished and finish is None and base.expired(deadline):
            # The watchdog closed the stream: the answer stopped for lack of time
            yield base.stop_event(base.STOP_DEADLINE, usage=usage, model=model)
            return
        if not finished and finish is None and not refused:
            # Closed by the server half way: not an answer that ended
            yield base.error_event(base.ERROR_NETWORK, "the stream ended before its end")
            return
        reason = base.STOP_REFUSAL if refused else FINISH_REASONS.get(finish, base.STOP_END)
        # Cut short or refused: what was written of a call is not to be run.
        # Otherwise a call is a call, whatever reason the provider gives:
        # Gemini says "stop" where OpenAI says "tool_calls".
        if calls and reason in (base.STOP_END, base.STOP_TOOL_USE):
            yield from self._tool_events(calls)
            reason = base.STOP_TOOL_USE
        yield base.stop_event(reason, usage=usage, model=model)

    @staticmethod
    def _normalize_usage(usage):
        """S8: the prompt of these servers is cached by its prefix, with nothing to ask (OpenAI,
        Gemini, Groq, DeepSeek, OpenRouter): the stable part of a request comes first (the system
        prompt, then the tools every question of the mode is offered) and what was read from the
        cache is told here, in the words each server uses. All of them count it in prompt_tokens."""
        details = usage.get("prompt_tokens_details") or {}
        if not isinstance(details, dict):
            details = {}

        def first(*values):
            return next((int(value) for value in values if isinstance(value, (int, float)) and value > 0), 0)

        cached = first(
            details.get("cached_tokens"),
            # DeepSeek
            usage.get("prompt_cache_hit_tokens"),
            # A proxy in front of Claude (LiteLLM), and the context cache of Gemini
            usage.get("cache_read_input_tokens"), usage.get("cached_content_token_count"))
        # OpenRouter in front of a model that charges the writing (Claude); OpenAI does not
        written = first(details.get("cache_write_tokens"), usage.get("cache_creation_input_tokens"))
        prompt = int(usage.get("prompt_tokens") or 0)
        return {
            # Same convention as Anthropic: input_tokens is the part neither read from nor written to the cache
            "input_tokens": max(prompt - cached - written, 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
            "cache_read_tokens": cached,
            "cache_write_tokens": written,
        }

    def list_models(self):
        try:
            response = self._get("/models")
        except requests.exceptions.RequestException as exc:
            raise base.AdapterError(base.ERROR_NETWORK, type(exc).__name__) from exc
        try:
            if response.status_code != 200:
                raise base.AdapterError(_error_code(response.status_code), _error_detail(response))
            try:
                data = response.json().get("data") or []
            except (ValueError, AttributeError) as exc:
                raise base.AdapterError(base.ERROR_BAD_REQUEST, "Unexpected response") from exc
        finally:
            response.close()
        names = set()
        for item in data:
            name = item.get("id") if isinstance(item, dict) else None
            if name:
                # Gemini lists "models/<id>" but expects "<id>" in requests
                names.add(name.removeprefix("models/"))
        return sorted(names)

    def check(self):
        payload = {
            "model": self.config.model,
            "messages": [{"role": "user", "content": "Reply with the single word: OK"}],
        }
        # As the answers will be asked: a thinking model told not to think
        # answers this in seconds, and would otherwise think past the timeout
        if self.config.effort in REASONING_EFFORT:
            payload["reasoning_effort"] = REASONING_EFFORT[self.config.effort]
        try:
            response = self._post(payload, stream=False, read_timeout=self.config.timeout)
            if response.status_code == 400 and payload.pop("reasoning_effort", None):
                response.close()
                response = self._post(payload, stream=False, read_timeout=self.config.timeout)
        except requests.exceptions.ReadTimeout as exc:
            raise base.AdapterError(base.ERROR_TIMEOUT, f"no answer within {self.config.timeout}s") from exc
        except requests.exceptions.RequestException as exc:
            raise base.AdapterError(base.ERROR_NETWORK, type(exc).__name__) from exc
        try:
            if response.status_code != 200:
                raise base.AdapterError(_error_code(response.status_code), _error_detail(response))
        finally:
            response.close()

    # ------------------------------------------------------------------
    # Speech
    # ------------------------------------------------------------------
    def _read_json(self, send, deadline):
        """:return: the body of the answer to a request that is not streamed
        :raise AdapterError: when there is none to read"""
        try:
            response = self._retry(send, deadline)
        except requests.exceptions.RequestException as exc:
            _logger.info(
                "Nudge: request to %s failed: %s", self.config.base_url, self._redact(exc))
            raise base.AdapterError(base.ERROR_NETWORK, type(exc).__name__) from exc
        try:
            if response.status_code != 200:
                raise base.AdapterError(_error_code(response.status_code), _error_detail(response))
            try:
                body = response.json()
            except ValueError as exc:
                raise base.AdapterError(base.ERROR_BAD_REQUEST, "Unexpected response") from exc
        finally:
            response.close()
        if not isinstance(body, dict):
            raise base.AdapterError(base.ERROR_BAD_REQUEST, "Unexpected response")
        return body

    def transcribe(self, audio, language="", hints=(), deadline=None, audio_format="wav"):
        if self.config.stt_mode == base.STT_CHAT:
            return self._transcribe_by_chat(audio, language, hints, deadline, audio_format)
        if self.config.stt_mode == base.STT_ENDPOINT:
            return self._transcribe_by_endpoint(audio, language, hints, deadline, audio_format)
        return super().transcribe(audio, language, hints, deadline, audio_format)

    def _transcribe_by_chat(self, audio, language, hints, deadline, audio_format="wav"):
        model = self.config.stt_model or self.config.model
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": transcription.build_instruction(language, hints)},
                {"role": "user", "content": [{
                    "type": "input_audio",
                    "input_audio": {"data": base64.b64encode(audio).decode(), "format": audio_format},
                }]},
            ],
        }

        def send():
            read_timeout = base.remaining(deadline, self.config.timeout)
            return self._post(payload, stream=False, read_timeout=read_timeout, deadline=deadline)

        body = self._read_json(send, deadline)
        try:
            text = body["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise base.AdapterError(base.ERROR_BAD_REQUEST, "Unexpected response") from exc
        return {
            "text": text if isinstance(text, str) else "",
            "usage": self._normalize_usage(body.get("usage") or {}),
            "model": body.get("model") or model,
        }

    def _transcribe_by_endpoint(self, audio, language, hints, deadline, audio_format="wav"):
        model = self.config.stt_model
        if not model:
            raise base.AdapterError(base.ERROR_MODEL_NOT_FOUND, "No model is set for speech.")
        data = {"model": model, "response_format": "json"}
        if language:
            data["language"] = language
        if hints:
            data["prompt"] = ", ".join(hints)

        # A service of its own address is not given the key of the provider
        elsewhere = self.config.stt_url.strip().rstrip("/")
        url = (elsewhere or self.config.base_url.rstrip("/")) + "/audio/transcriptions"
        headers = {} if elsewhere else self._headers(json_body=False)

        def send():
            return outbound.post(
                url,
                allow_private=self._private_ok(url),
                data=data,
                files={"file": (f"speech.{audio_format}", audio, "audio/" + ("mpeg" if audio_format == "mp3" else audio_format))},
                headers=headers,
                timeout=(resilience.connect_timeout(deadline, CONNECT_TIMEOUT), base.remaining(deadline, self.config.timeout)),
            )

        body = self._read_json(send, deadline)
        text = body.get("text")
        return {
            "text": text if isinstance(text, str) else "",
            "usage": base.normalize_usage({}),
            "model": model,
        }

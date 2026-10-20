"""Scripted adapter for automated tests: no network, no cost.

Not registered as a provider kind; tests install it by patching
``nudge.provider._get_adapter``.
"""
import re

from . import base


class FakeAdapter(base.BaseAdapter):
    kind = "fake"

    def __init__(self, config=None, reply="Hello from Nudge.", events=None, usage=None,
                 script=None, heard="What is this screen?"):
        """:param events: events of every answer
        :param script: list of lists of events, one list per request, in order;
            once it is used up the adapter answers with ``reply``
        :param heard: what every recording says, or the error it raises"""
        super().__init__(config or base.ProviderConfig(kind=self.kind, model="fake-model"))
        self.reply = reply
        self.events = events
        self.script = list(script or [])
        self.usage = usage or {"input_tokens": 12, "output_tokens": 5}
        self.heard = heard
        self.calls = []
        self.recordings = []

    def stream_chat(self, system, turns, deadline=None, tools=None, tool_choice=None):
        self.calls.append({"system": system, "turns": turns, "deadline": deadline,
                           "tools": tools, "tool_choice": tool_choice})
        if self.script:
            yield from self.script.pop(0)
            return
        if self.events is not None:
            yield from self.events
            return
        for chunk in re.findall(r"\S+\s*", self.reply):
            yield base.text_event(chunk)
        yield base.stop_event(
            base.STOP_END,
            usage=self.usage,
            blocks=[{"type": "text", "text": self.reply}],
            model=self.config.model,
            request_id="req_fake",
        )

    def list_models(self):
        return ["fake-model", "fake-model-large"]

    def check(self):
        return None

    def transcribe(self, audio, language="", hints=(), deadline=None, audio_format="wav"):
        self.recordings.append({
            "audio_format": audio_format, "audio": audio, "language": language, "hints": list(hints), "deadline": deadline,
        })
        if isinstance(self.heard, Exception):
            raise self.heard
        return {
            "text": self.heard,
            "usage": base.normalize_usage({"input_tokens": 120, "output_tokens": 8}),
            "model": "fake-speech",
        }

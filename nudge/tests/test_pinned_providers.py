"""STU-05: the calls to a provider go through the pinned helper, and a provider on our own network still works."""
import tempfile
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge.llm import base, embedder, ollama
from odoo.addons.nudge.llm.openai_compat_adapter import OpenAICompatAdapter
from odoo.addons.nudge.tools import pinned_http
from odoo.addons.nudge_builder.tools import pictures

HELPER = "odoo.addons.nudge.tools.pinned_http.request"


class Answer:
    status_code = 200
    headers = {}
    text = "{}"

    def __init__(self, body=None):
        self.body = body or {}

    def json(self):
        return self.body

    def raise_for_status(self):
        pass

    def iter_content(self, size):
        yield b"data"

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class Spy:
    def __init__(self, body=None):
        self.calls = []
        self.body = body

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return Answer(self.body)


def adapter(base_url, **kwargs):
    return OpenAICompatAdapter(base.ProviderConfig(kind="openai_compatible", model="m", base_url=base_url, **kwargs))


@tagged("post_install", "-at_install")
class TestProviderCallsArePinned(TransactionCase):

    def test_a_chat_and_a_model_list_go_through_the_helper(self):
        spy = Spy()
        with patch(HELPER, spy):
            adapter("https://llm.example.test/v1")._post({"model": "m"}, stream=True, read_timeout=5)
            adapter("https://llm.example.test/v1")._get("/models")
        self.assertEqual([(c[0], c[1]) for c in spy.calls], [
            ("POST", "https://llm.example.test/v1/chat/completions"), ("GET", "https://llm.example.test/v1/models")])
        for _method, _url, kwargs in spy.calls:
            self.assertIs(kwargs["allow_private"], False, "A public name must resolve to public addresses")
        self.assertTrue(spy.calls[0][2]["stream"])
        self.assertIn("timeout", spy.calls[0][2])
        self.assertEqual(spy.calls[0][2]["headers"]["Content-Type"], "application/json")

    def test_a_provider_on_our_own_network_still_works(self):
        spy = Spy()
        with patch(HELPER, spy):
            adapter("http://localhost:11434/v1")._post({}, stream=False, read_timeout=5)
            adapter("http://192.168.1.20:11434/v1")._get("/models")
            adapter("http://ollama:11434/v1")._get("/models")
            # the admin said it runs on our own server, whatever the name looks like
            adapter("https://ai.corp.example/v1", runs_locally=True)._get("/models")
        self.assertEqual([c[2]["allow_private"] for c in spy.calls], [True] * 4)

    def test_the_metadata_address_is_never_ours(self):
        self.assertFalse(pinned_http.is_own_network("http://169.254.169.254/v1"))
        # Not even to a provider allowed on our own network
        for found in (["169.254.169.254"], ["10.0.0.5", "169.254.169.254"], ["fe80::1"]):
            with self.subTest(found=found), patch.object(pinned_http, "resolve", return_value=found), \
                    self.assertRaises(pinned_http.Refused):
                pinned_http.addresses("ollama.corp.example", 11434, allow_private=True)
        with patch.object(pinned_http, "resolve", return_value=["10.0.0.5"]):
            self.assertEqual(pinned_http.addresses("ollama.corp.example", 11434, allow_private=True), ["10.0.0.5"])
        self.assertFalse(pinned_http.is_own_network("https://api.openai.com/v1"))
        self.assertTrue(pinned_http.is_own_network("http://[::1]:11434"))

    def test_a_name_that_turns_inside_is_refused_before_anything_is_sent(self):
        with patch.object(pinned_http, "resolve", lambda host, port: ["127.0.0.1"]):
            with patch.object(pinned_http, "create_connection", side_effect=OSError("stop")) as opened:
                with self.assertRaises(pinned_http.Refused):
                    adapter("https://llm.example.test/v1")._get("/models")
                opened.assert_not_called()
            # The same address set on our own network is let through (the tests forbid the request itself)
            self.assertEqual(pinned_http.addresses("localhost", 11434, allow_private=True), ["127.0.0.1"])

    def test_the_transcription_service_is_judged_by_its_own_address(self):
        spy = Spy({"text": "hi"})
        voice = adapter("https://llm.example.test/v1", stt_mode=base.STT_ENDPOINT, stt_model="w",
                        stt_url="http://localhost:8000/v1")
        with patch(HELPER, spy):
            try:
                voice.transcribe(b"RIFF")
            except base.AdapterError:
                pass
        self.assertEqual(spy.calls[0][1], "http://localhost:8000/v1/audio/transcriptions")
        self.assertIs(spy.calls[0][2]["allow_private"], True)

    def test_ollama_goes_through_the_helper(self):
        spy = Spy({"version": "0.5"})
        with patch(HELPER, spy):
            ollama.version("http://localhost:11434/v1")
            ollama.version("https://ollama.example.test/v1")
        self.assertEqual(spy.calls[0][1], "http://localhost:11434/api/version")
        # Ollama is a server of our own whatever its name: ollama.corp.example on 10.x keeps working
        self.assertEqual([call[2]["allow_private"] for call in spy.calls], [True, True])
        self.assertIn("timeout", spy.calls[0][2])

    def test_the_search_model_download_goes_through_the_helper(self):
        spy = Spy()
        with patch(HELPER, spy), tempfile.TemporaryDirectory() as directory:
            list(embedder.fetch("https://models.example.test/m.onnx", directory + "/m", offset=0))
            list(embedder.fetch("https://models.example.test/m.onnx", directory + "/m", offset=2))
        self.assertTrue(spy.calls[0][2]["stream"])
        self.assertIs(spy.calls[0][2]["allow_private"], False)
        self.assertEqual(spy.calls[1][2]["headers"], {"Range": "bytes=2-"})

    def test_a_picture_is_drawn_through_the_helper(self):
        provider = self.env["nudge.provider"].create({
            "name": "Pictures of Raghad", "kind": "openai_compatible", "base_url": "https://images.example.test/v1",
            "model_name": "image-1"})
        spy = Spy({"data": [{"b64_json": "AAAA"}]})
        with patch(HELPER, spy):
            self.assertEqual(pictures.draw_picture(provider, "image-1", "an oak chair"), b"\x00\x00\x00")
            provider.runs_locally = True
            pictures.draw_picture(provider, "image-1", "an oak chair")
        self.assertEqual(spy.calls[0][1], "https://images.example.test/v1/images/generations")
        self.assertEqual([c[2]["allow_private"] for c in spy.calls], [False, True])

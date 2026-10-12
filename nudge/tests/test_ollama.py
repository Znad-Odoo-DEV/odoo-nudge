import json

from odoo.exceptions import UserError
from odoo.tests import BaseCase, TransactionCase, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.llm import base, ollama

from .common import NudgeCase


class FakeHttp:
    """An Ollama server, as requests sees it: (method, path) → JSON or a status."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, method, url, json=None, headers=None, timeout=None):
        path = "/" + url.split("/", 3)[3]
        self.calls.append((method, url, json, headers))
        answer = self.answers.get((method, path))
        if callable(answer):
            answer = answer(json)
        if answer is None:
            return FakeResponse(404, {"error": f"{path} not found"})
        if isinstance(answer, int):
            return FakeResponse(answer, {"error": "refused"})
        return FakeResponse(200, answer)


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.body = body
        self.text = json.dumps(body)

    def json(self):
        return self.body

    def close(self):
        pass


def show(capabilities=("completion", "tools"), context=40960, parameters=""):
    return {
        "capabilities": list(capabilities),
        "model_info": {"general.architecture": "qwen3", "qwen3.context_length": context},
        "details": {"parameter_size": "8.2B", "quantization_level": "Q4_K_M"},
        "parameters": parameters,
    }


@tagged("post_install", "-at_install")
class TestOllamaApi(BaseCase):

    def test_names_and_sizes(self):
        self.assertEqual(ollama.root_of("http://box:11434/v1/"), "http://box:11434")
        self.assertEqual(ollama.root_of("http://box:11434"), "http://box:11434")
        self.assertEqual(ollama.name_for_nudge("qwen3:8b"), "qwen3:8b-nudge")
        self.assertEqual(ollama.name_for_nudge("qwen3"), "qwen3:latest-nudge")
        self.assertEqual(ollama.name_for_nudge("qwen3:8b-nudge"), "qwen3:8b-nudge", "Prepared once")
        self.assertEqual(ollama.context_for(19000, 40960), 20480, "Steps of 4096")
        self.assertEqual(ollama.context_for(19000, 16384), 16384, "Never past what the model knows")
        self.assertEqual(ollama.context_for(19000, 0), 20480)

    def test_describe(self):
        http = FakeHttp({("POST", "/api/show"): show(parameters="num_ctx                        32768\nstop <|im_end|>")})
        self.patch(ollama.requests, "request", http)
        facts = ollama.describe("http://box:11434/v1", "qwen3:8b", "key")
        self.assertEqual(facts, {"tools": True, "vision": False, "thinking": False, "context_max": 40960,
                                 "context_set": 32768, "size": "8.2B Q4_K_M"})
        self.assertEqual(http.calls[0][1], "http://box:11434/api/show")
        self.assertEqual(http.calls[0][3], {"Authorization": "Bearer key"}, "A proxy in front of it reads the key")

    def test_what_is_not_ollama(self):
        self.patch(ollama.requests, "request", FakeHttp({}))
        self.assertEqual(ollama.version("https://llm.invalid/v1"), "")

    def test_loaded_context(self):
        self.patch(ollama.requests, "request", FakeHttp({("GET", "/api/ps"): {"models": [
            {"name": "llama3.1:latest", "model": "llama3.1:latest", "context_length": 8192},
            {"name": "qwen3:8b", "model": "qwen3:8b", "context_length": 4096},
        ]}}))
        self.assertEqual(ollama.loaded_context("http://box:11434/v1", "qwen3:8b"), 4096)
        self.assertEqual(ollama.loaded_context("http://box:11434/v1", "llama3.1"), 8192)
        self.assertEqual(ollama.loaded_context("http://box:11434/v1", "gemma3"), 0)

    def test_prepare(self):
        http = FakeHttp({("POST", "/api/create"): {"status": "success"}})
        self.patch(ollama.requests, "request", http)
        self.assertEqual(ollama.prepare("http://box:11434/v1", "qwen3:8b", 24576), "qwen3:8b-nudge")
        self.assertEqual(http.calls[0][2], {"model": "qwen3:8b-nudge", "from": "qwen3:8b",
                                            "parameters": {"num_ctx": 24576, "temperature": ollama.TEMPERATURE,
                                                           "repeat_penalty": ollama.REPEAT_PENALTY},
                                            "stream": False})


@tagged("post_install", "-at_install")
class TestOllamaProvider(NudgeCase, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider.write({"base_url": "http://localhost:11434/v1", "model_name": "qwen3:8b",
                            "requires_api_key": False, "api_key": False})

    def server(self, answers=None):
        defaults = {
            ("GET", "/api/version"): {"version": "0.12.6"},
            ("POST", "/api/show"): show(),
            ("GET", "/api/ps"): {"models": [{"name": "qwen3:8b", "model": "qwen3:8b", "context_length": 4096}]},
            ("POST", "/api/create"): {"status": "success"},
        }
        defaults.update(answers or {})
        http = FakeHttp(defaults)
        self.patch(ollama.requests, "request", http)
        return http

    def test_found_when_its_models_are_fetched(self):
        self.use_adapter()
        self.server()
        result = self.provider.action_fetch_models()
        self.assertTrue(self.provider.is_ollama)
        self.assertEqual(self.provider.ollama_version, "0.12.6")
        self.assertEqual(self.provider.tool_set, "essential", "A laptop's model gets the essential tools")
        self.assertEqual(self.provider.effort, "none", "and answers without a minute of thinking first")
        self.assertIn("Ollama 0.12.6 answers here", result["params"]["message"])
        line = self.provider.model_ids.filtered(lambda model: model.name == "fake-model")
        self.assertTrue(line.uses_tools)
        self.assertEqual(line.context_max, 40960)
        self.assertEqual(line.size, "8.2B Q4_K_M")
        # Chosen once: the person's own choice is kept afterwards
        self.provider.write({"tool_set": "full", "effort": "medium"})
        self.provider.action_fetch_models()
        self.assertEqual(self.provider.tool_set, "full")
        self.assertEqual(self.provider.effort, "medium")

    def test_other_providers_are_left_alone(self):
        self.use_adapter()
        self.patch(ollama.requests, "request", FakeHttp({}))
        self.provider.action_fetch_models()
        self.assertFalse(self.provider.is_ollama)
        self.assertEqual(self.provider.tool_set, "full")

    def test_a_small_context_is_told(self):
        self.use_adapter()
        self.server()
        result = self.provider.action_test_connection()
        self.assertEqual(result["params"]["type"], "warning")
        self.assertEqual(self.provider.context_length, 4096)
        self.assertIn("Prepare for Nudge", result["params"]["message"])
        self.assertTrue(self.provider.context_warning)
        self.assertGreater(self.provider.context_needed, 4096)

    def test_a_model_without_tools_is_told(self):
        self.use_adapter()
        self.server({("POST", "/api/show"): show(capabilities=("completion",)),
                     ("GET", "/api/ps"): {"models": [{"name": "qwen3:8b", "context_length": 65536}]}})
        result = self.provider.action_test_connection()
        self.assertIn("cannot use tools", result["params"]["message"])
        self.assertFalse(self.provider.context_warning, "Enough context: nothing to say of it")

    def test_prepare_for_nudge(self):
        self.use_adapter()
        http = self.server()
        self.provider.action_fetch_models()
        self.provider.action_ollama_prepare()
        created = [call for call in http.calls if call[1].endswith("/api/create")]
        self.assertEqual(created[0][2]["from"], "qwen3:8b")
        self.assertGreaterEqual(created[0][2]["parameters"]["num_ctx"], self.provider.context_needed)
        self.assertEqual(self.provider.model_name, "qwen3:8b-nudge")
        self.assertEqual(self.provider.context_length, created[0][2]["parameters"]["num_ctx"])
        self.assertGreaterEqual(self.provider.timeout, 180, "A model on one's own machine is given time")

    def test_a_picture_is_not_sent_to_a_model_that_cannot_see(self):
        from .test_attachments import PNG  # noqa: PLC0415

        adapter = self.use_adapter()
        self.server()
        self.provider.is_ollama = True
        line = self.env["nudge.provider.model"].create({
            "provider_id": self.provider.id, "name": "qwen3:8b", "sees_pictures": False})
        conversation = self.env["nudge.conversation"].with_user(self.user_a)._chat_open(None)
        with self.assertRaisesRegex(UserError, "cannot read pictures"):
            conversation._chat_prepare("What is this?", attachment=("photo.png", PNG, "image/png"))
        self.assertEqual(adapter.calls, [], "Nothing is sent")
        line.sees_pictures = True
        self.assertTrue(conversation._chat_prepare("What is this?", attachment=("photo.png", PNG, "image/png")))

    def test_a_picture_model_reads_for_a_model_that_cannot_see(self):
        from .test_attachments import PNG  # noqa: PLC0415

        self.use_adapter()
        self.server()
        self.provider.write({"is_ollama": True, "picture_model": "qwen2.5vl:3b"})
        self.env["nudge.provider.model"].create({
            "provider_id": self.provider.id, "name": "qwen3:8b", "sees_pictures": False})
        read = []

        def read_picture(base_url, model, data, question="", api_key=""):
            read.append((model, question))
            return "A red office chair, label FURN_7777", {"input_tokens": 300, "output_tokens": 12}

        self.patch(ollama, "read_picture", read_picture)
        conversation = self.env["nudge.conversation"].with_user(self.user_a)._chat_open(None)
        job = conversation._chat_prepare("Do we sell this?", attachment=("photo.png", PNG, "image/png"))
        self.assertEqual(read, [("qwen2.5vl:3b", "Do we sell this?")])
        question = job["turns"][-1]
        self.assertIn("FURN_7777", question["context"], "What was read goes with the question")
        self.assertFalse(question.get("images"), "The picture itself is not sent to a model without eyes")
        message = conversation.sudo().message_ids.filtered(lambda message: message.role == "user")
        self.assertTrue(message.attachment_id, "Kept for the person to see")

    def test_the_warning_follows_the_context(self):
        self.provider.is_ollama = True
        self.provider.context_length = 4096
        self.assertTrue(self.provider.context_warning)
        self.provider.context_length = 65536
        self.assertFalse(self.provider.context_warning, "Prepared: nothing left to say")

    def test_the_context_needed_follows_the_switches(self):
        Parameter = self.env["ir.config_parameter"].sudo()
        Parameter.set_param("nudge.developer", "no")
        agent = self.provider._tokens_needed()
        Parameter.set_param("nudge.developer", "yes")
        self.assertGreater(self.provider._tokens_needed(), agent, "Developer mode needs the most")
        self.assertEqual(self.provider._tokens_needed("agent"), agent)

    def test_prepare_is_for_ollama(self):
        with self.assertRaises(UserError):
            self.provider.action_ollama_prepare()

    def test_essential_tools_are_fewer_and_lighter(self):
        self.provider.tool_set = "full"
        full = self.provider._tokens_needed()
        self.provider.tool_set = "essential"
        self.assertLess(self.provider._tokens_needed(), full)
        adapter = self.use_adapter()
        self.ask(self.agent, "Hello")
        offered = {tool["name"] for tool in adapter.calls[0]["tools"]}
        self.assertTrue(offered)
        self.assertLessEqual(offered, tools.base.ESSENTIAL_TOOLS)

    def test_what_went_wrong_is_said_for_ollama(self):
        self.provider.is_ollama = True
        Conversation = self.env["nudge.conversation"]
        self.assertIn("Ollama does not answer at http://localhost:11434/v1",
                      Conversation._chat_error_message(base.ERROR_NETWORK, self.provider))
        self.assertIn("ollama pull qwen3:8b", Conversation._chat_error_message(base.ERROR_MODEL_NOT_FOUND, self.provider))
        self.assertIn("cannot use tools", Conversation._chat_error_message(base.ERROR_UNSUPPORTED, self.provider))
        self.provider.is_ollama = False
        self.assertEqual(Conversation._chat_error_message(base.ERROR_NETWORK, self.provider),
                         "The AI provider could not be reached.")

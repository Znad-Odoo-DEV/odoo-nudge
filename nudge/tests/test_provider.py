from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge.llm import base
from odoo.addons.nudge.llm.anthropic_adapter import AnthropicAdapter
from odoo.addons.nudge.llm.openai_compat_adapter import OpenAICompatAdapter

from .common import NudgeCase


@tagged("post_install", "-at_install")
class TestProvider(NudgeCase, TransactionCase):

    def test_shipped_providers(self):
        gemini = self.env.ref("nudge.provider_gemini")
        anthropic = self.env.ref("nudge.provider_anthropic")
        ollama = self.env.ref("nudge.provider_ollama")
        shipped = gemini | anthropic | ollama
        shipped |= self.env.ref("nudge.provider_groq") | self.env.ref("nudge.provider_openrouter")

        self.assertEqual(shipped.sorted("sequence")[0], gemini, "A free provider comes first")
        self.assertEqual(anthropic.kind, "anthropic")
        self.assertEqual(set((shipped - anthropic).mapped("kind")), {"openai_compatible"})
        self.assertTrue(all((shipped - anthropic).mapped("base_url")))
        self.assertFalse(ollama.requires_api_key)
        for provider in shipped - ollama:
            provider.api_key = False
            self.assertFalse(provider.is_configured, "Nothing answers before a key is set")

    def test_is_configured(self):
        provider = self.provider
        self.assertTrue(provider.is_configured)
        provider.api_key = False
        self.assertFalse(provider.is_configured)
        provider.requires_api_key = False
        self.assertTrue(provider.is_configured)
        provider.model_name = False
        self.assertFalse(provider.is_configured)
        provider.write({"model_name": "m", "base_url": False})
        self.assertFalse(provider.is_configured)
        provider.kind = "anthropic"
        self.assertTrue(provider.is_configured, "Anthropic has an endpoint of its own")

    def test_adapter_follows_the_protocol(self):
        self.assertIsInstance(self.provider._get_adapter(), OpenAICompatAdapter)
        self.provider.kind = "anthropic"
        adapter = self.provider._get_adapter()
        self.assertIsInstance(adapter, AnthropicAdapter)
        self.assertEqual(adapter.config.api_key, "sk-test-secret")
        self.assertEqual(adapter.config.model, "test-model")

    def test_key_pasted_with_blanks(self):
        # Pasted with a line break after it, the key made every request fail
        # before it left: a header cannot hold one
        self.provider.api_key = " sk-pasted-key\n"
        self.assertEqual(self.provider.api_key, "sk-pasted-key")
        self.assertEqual(self.provider._get_config().api_key, "sk-pasted-key")
        other = self.provider.copy({"api_key": "\tsk-other-key "})
        self.assertEqual(other.api_key, "sk-other-key", "Nor when a provider is made")
        self.provider.api_key = False
        self.assertFalse(self.provider.api_key)

    def test_capabilities_follow_the_model(self):
        self.provider.capabilities = {"effort": {"supported": True}}
        self.provider.timeout = 30
        self.assertTrue(self.provider.capabilities)
        self.provider.model_name = "another-model"
        self.assertFalse(self.provider.capabilities)

    def test_fetch_models(self):
        adapter = self.use_adapter()
        self.provider.action_fetch_models()
        self.assertEqual(self.provider.model_ids.mapped("name"), ["fake-model", "fake-model-large"])

        self.patch(adapter, "list_models", lambda: ["fake-model", "newer-model"])
        self.provider.action_fetch_models()
        self.assertEqual(self.provider.model_ids.mapped("name"), ["fake-model", "newer-model"])

        line = self.provider.model_ids[1]
        self.assertFalse(line.is_selected)
        line.action_select()
        self.assertEqual(self.provider.model_name, "newer-model")
        self.assertTrue(line.is_selected)

    def test_test_connection(self):
        adapter = self.use_adapter()
        action = self.provider.action_test_connection()
        self.assertEqual(action["params"]["type"], "success")

        def check():
            raise base.AdapterError(base.ERROR_AUTH, "invalid key")

        self.patch(adapter, "check", check)
        with self.assertRaisesRegex(UserError, "invalid key"):
            self.provider.action_test_connection()

        self.provider.api_key = False
        with self.assertRaisesRegex(UserError, "API key"):
            self.provider.action_test_connection()

    def test_cost(self):
        self.provider.write({"price_cache_read": 0.2, "price_cache_write": 2.5})
        usage = base.normalize_usage({
            "input_tokens": 1_000_000, "output_tokens": 100_000,
            "cache_read_tokens": 500_000, "cache_write_tokens": 200_000,
        })
        # 2 + 1 + 0.1 + 0.5
        self.assertAlmostEqual(self.provider._compute_cost(usage), 3.6)

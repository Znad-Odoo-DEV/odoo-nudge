"""NP-15: the allow-list of destinations, and the alert for a credential on a shared screen."""
import json
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge.llm import base
from odoo.addons.nudge.llm import snapshot as screen
from odoo.addons.nudge.tools import egress

from .common import NudgeCase

FEED = "odoo.addons.nudge.models.nudge_compliance_feed._download"
MARKET = "odoo.addons.nudge.models.nudge_recipe_market._download"
SCREEN = {"v": 1, "model": "res.users", "view_type": "form",
          "fields": [{"name": "password", "value": "hunter2"}, {"name": "name", "value": "Raghad"}]}
SUBJECT = "A screen with a password field was shared with Nudge"


@tagged("post_install", "-at_install")
class TestEgressList(TransactionCase):

    def test_the_entries_are_read_in_any_shape(self):
        self.assertEqual(
            egress.parse(" API.Anthropic.com\nlocalhost:11434, *.Example.com ;https://x.test/ \n\napi.anthropic.com"),
            ["api.anthropic.com", "localhost:11434", "*.example.com", "x.test"])
        self.assertEqual(egress.parse(False), [])

    def test_a_host_a_port_and_a_domain(self):
        entries = egress.parse("api.anthropic.com\nlocalhost:11434\n*.example.com")
        for url in ("https://api.anthropic.com/v1/messages", "http://localhost:11434/v1", "https://example.com",
                    "https://a.b.example.com/x"):
            self.assertTrue(egress.is_allowed(entries, url), url)
        for url in ("https://evil.test", "http://localhost:8080", "https://notexample.com",
                    "https://api.anthropic.com.evil.test", "https://evil.test/api.anthropic.com", "not a url", ""):
            self.assertFalse(egress.is_allowed(entries, url), url)

    def test_an_empty_list_allows_everything(self):
        self.assertTrue(egress.is_allowed([], "https://anything.test"))
        self.env.company.nudge_egress_hosts = False
        self.env["nudge.egress"].check("https://anything.test", "chat")

    def test_the_refusal_names_the_host_and_where_to_allow_it(self):
        self.env.company.nudge_egress_hosts = "api.anthropic.com"
        with self.assertRaises(UserError) as error:
            self.env["nudge.egress"].check("https://evil.test/v1", "recipes")
        text = str(error.exception)
        self.assertIn("evil.test:443", text)
        self.assertIn("Settings, Nudge", text)
        self.assertIn("recipe marketplace", text)

    def test_each_company_has_its_own_list(self):
        other = self.env["res.company"].create({"name": "Another company"})
        self.env.company.nudge_egress_hosts = "api.anthropic.com"
        other.nudge_egress_hosts = "other.test"
        Egress = self.env["nudge.egress"]
        with self.assertRaises(UserError):
            Egress.check("https://other.test", "chat")
        Egress.with_company(other).check("https://other.test", "chat")
        with self.assertRaises(UserError):
            Egress.with_company(other).check("https://api.anthropic.com", "chat")


@tagged("post_install", "-at_install")
class TestEgressPaths(NudgeCase, TransactionCase):

    def setUp(self):
        super().setUp()
        self.env.company.nudge_egress_hosts = "allowed.test"

    def test_a_provider_off_the_list_is_refused_before_the_adapter_is_built(self):
        with patch.object(base, "get_adapter") as get, self.assertRaises(UserError) as error:
            self.provider._get_adapter()
        get.assert_not_called()
        self.assertIn("llm.invalid", str(error.exception))

    def test_a_provider_on_the_list_works(self):
        self.provider.base_url = "https://allowed.test/v1"
        with patch.object(base, "get_adapter", lambda config: "adapter"):
            self.assertEqual(self.provider._get_adapter(), "adapter")

    def test_a_claude_provider_without_an_address_goes_to_its_maker(self):
        claude = self.provider.create({"name": "Claude", "kind": "anthropic", "api_key": "sk-ant-x",
                                       "model_name": "m"})
        with self.assertRaises(UserError) as error:
            claude._get_adapter()
        self.assertIn("api.anthropic.com", str(error.exception))
        self.env.company.nudge_egress_hosts = "api.anthropic.com"
        claude._check_private()

    def test_the_voice_address_is_checked_too(self):
        self.provider.write({"base_url": "https://allowed.test/v1", "stt_url": "https://voice.test/v1"})
        with self.assertRaises(UserError) as error:
            self.provider._check_private()
        self.assertIn("voice.test", str(error.exception))

    def test_every_other_way_out_of_a_provider_is_closed(self):
        for call in (self.provider.action_test_connection, self.provider._ollama_detect,
                     self.provider._can_see_pictures,
                     lambda: self.provider._read_picture("image/png", "AAAA", "what", model="x")):
            with patch("requests.request") as sent, patch("requests.post") as posted, \
                    self.assertRaises(UserError):
                call()
            sent.assert_not_called()
            posted.assert_not_called()

    def test_the_list_empty_changes_nothing(self):
        self.env.company.nudge_egress_hosts = False
        with patch.object(base, "get_adapter", lambda config: "adapter"):
            self.assertEqual(self.provider._get_adapter(), "adapter")

    def test_private_mode_stays_stricter(self):
        local = self.provider.create({
            "name": "Our Ollama", "kind": "openai_compatible", "base_url": "http://localhost:11434/v1",
            "model_name": "qwen", "requires_api_key": False, "runs_locally": True})
        self.env.company.nudge_private_mode = True
        # on the list, but it does not run here: private mode refuses it
        self.provider.base_url = "https://allowed.test/v1"
        with self.assertRaises(UserError) as error:
            self.provider._get_adapter()
        self.assertIn("Private mode is on", str(error.exception))
        # local, but not on the list: the list refuses it
        with self.assertRaises(UserError) as error:
            local._get_adapter()
        self.assertIn("localhost:11434", str(error.exception))
        self.env.company.nudge_egress_hosts = "localhost:11434"
        with patch.object(base, "get_adapter", lambda config: "adapter"):
            self.assertEqual(local._get_adapter(), "adapter")

    def test_the_marketplace_is_refused_when_not_listed(self):
        source = self.env["nudge.recipe.source"]
        with patch(MARKET) as fetched, self.assertRaises(UserError) as error:
            source._get("https://recipes.example.test/index.json")
        fetched.assert_not_called()
        self.assertIn("recipes.example.test", str(error.exception))
        self.env.company.nudge_egress_hosts = "recipes.example.test"
        with patch(MARKET, return_value=b"{}"):
            self.assertEqual(source._get("https://recipes.example.test/index.json"), b"{}")

    def test_a_redirect_to_an_unlisted_host_is_refused_too(self):
        from odoo.addons.nudge.models import nudge_recipe_market as market

        class Answer:
            status_code, headers = 302, {"Location": "https://elsewhere.test/index.json"}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        asked = []

        def get(url, **kwargs):
            asked.append(url)
            return Answer()

        self.env.company.nudge_egress_hosts = "recipes.example.test"
        with patch.object(market, "_require_https", lambda url: None), patch("requests.get", get), \
                self.assertRaises(UserError) as error:
            self.env["nudge.recipe.source"]._get("https://recipes.example.test/index.json")
        self.assertEqual(asked, ["https://recipes.example.test/index.json"])
        self.assertIn("elsewhere.test", str(error.exception))

    def test_the_compliance_feed_is_refused_when_not_listed(self):
        Parameter = self.env["ir.config_parameter"].sudo()
        Parameter.set_param("nudge.compliance_feed_url", "https://feed.example.test/feed.json")
        Parameter.set_param("nudge.compliance_feed_key", "AAAA")
        with patch(FEED) as fetched, self.assertRaises(UserError) as error:
            self.env["nudge.compliance.update"]._fetch()
        fetched.assert_not_called()
        self.assertIn("feed.example.test", str(error.exception))

    def test_the_search_model_is_not_downloaded_from_an_unlisted_host(self):
        Embedding = self.env["nudge.embedding"]
        with patch.object(type(Embedding), "_download_state", lambda self: {"state": "running"}), \
                patch.object(type(Embedding), "_set_download_state") as state, \
                patch("odoo.addons.nudge.llm.embedder.download") as downloaded:
            self.assertFalse(Embedding._download_some())
        downloaded.assert_not_called()
        self.assertEqual(state.call_args.kwargs.get("state"), "error")
        self.assertIn("allowed destinations", state.call_args.kwargs.get("error", ""))


@tagged("post_install", "-at_install")
class TestCredentialScreenAlert(NudgeCase, TransactionCase):

    def setUp(self):
        super().setUp()
        self.admin = new_test_user(self.env, login="nudge_admin_np15", groups="base.group_system")

    def test_a_masked_value_is_seen_by_the_snapshot(self):
        self.assertTrue(screen.has_masked_value(screen.sanitize(SCREEN)))
        self.assertFalse(screen.has_masked_value(screen.sanitize(
            {"v": 1, "model": "res.partner", "fields": [{"name": "name", "value": "Raghad"}]})))

    def share(self, snapshot=SCREEN):
        conversation = self.env["nudge.conversation"].with_user(self.user_a)._chat_open(None)
        conversation._chat_prepare("What is this?", snapshot=json.dumps(snapshot))
        conversation._chat_finalize({"text": "Answer", "stop": base.stop_event(base.STOP_END)})

    def notices(self, user=None):
        return self.env["mail.message"].search([
            ("partner_ids", "in", (user or self.admin).partner_id.id), ("subject", "=", SUBJECT)])

    def test_the_administrators_are_told_once_a_day_per_screen_model(self):
        self.use_adapter()
        self.share()
        self.assertEqual(len(self.notices()), 1)
        self.assertIn("res.users", self.notices().body)
        self.assertNotIn("hunter2", self.notices().body)
        self.share()
        self.assertEqual(len(self.notices()), 1, "the same screen model is told once a day")
        self.share(dict(SCREEN, model="res.partner"))
        self.assertEqual(len(self.notices()), 2, "another screen model is told")

    def test_it_is_told_again_the_next_day(self):
        Egress = self.env["nudge.egress"]
        self.assertTrue(Egress.alert_credential_screen({"model": "res.users"}))
        self.assertFalse(Egress.alert_credential_screen({"model": "res.users"}))
        key = f"nudge.egress.alert.{self.env.company.id}.res.users"
        self.env.cr.execute("UPDATE ir_config_parameter SET value = '2000-01-01' WHERE key = %s", (key,))
        self.assertTrue(Egress.alert_credential_screen({"model": "res.users"}))

    def test_nobody_is_told_of_a_screen_without_a_credential(self):
        self.use_adapter()
        self.share({"v": 1, "model": "res.partner", "fields": [{"name": "name", "value": "Raghad"}]})
        self.assertFalse(self.notices())

    def test_only_system_administrators_are_told(self):
        self.use_adapter()
        self.share()
        self.assertFalse(self.notices(self.manager))

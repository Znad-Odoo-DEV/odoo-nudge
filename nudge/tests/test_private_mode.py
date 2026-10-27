from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge.llm import base

from .common import NudgeCase


@tagged("post_install", "-at_install")
class TestPrivateMode(NudgeCase, TransactionCase):
    """With private mode on, nothing goes to a provider that does not run on our own server."""

    def setUp(self):
        super().setUp()
        self.env.company.nudge_private_mode = True
        self.local = self.provider.create({
            "name": "Our Ollama", "kind": "openai_compatible", "base_url": "http://localhost:11434/v1",
            "model_name": "qwen", "requires_api_key": False, "runs_locally": True, "sequence": 50})

    def adapter_of(self, provider):
        with patch.object(base, "get_adapter", lambda config: ("adapter", config.base_url)) as made:
            return provider._get_adapter(), made

    def test_a_provider_outside_is_refused_before_anything_is_built(self):
        with patch.object(base, "get_adapter") as get, self.assertRaises(UserError) as error:
            self.provider._get_adapter()
        get.assert_not_called()
        self.assertIn("Private mode is on", str(error.exception))
        self.assertIn(self.provider.name, str(error.exception))

    def test_a_provider_on_our_server_works(self):
        adapter, _made = self.adapter_of(self.local)
        self.assertEqual(adapter, ("adapter", "http://localhost:11434/v1"))

    def test_off_everybody_works(self):
        self.env.company.nudge_private_mode = False
        adapter, _made = self.adapter_of(self.provider)
        self.assertEqual(adapter[0], "adapter")

    def test_every_other_way_out_is_closed(self):
        for call in (self.provider.action_test_connection, self.provider._ollama_detect,
                     self.provider._can_see_pictures,
                     lambda: self.provider._read_picture("image/png", "AAAA", "what", model="x")):
            with patch("odoo.addons.nudge.tools.pinned_http.request") as sent, patch("requests.post") as posted, \
                    self.assertRaises(UserError):
                call()
            sent.assert_not_called()
            posted.assert_not_called()

    def test_the_default_and_the_listener_are_ours(self):
        Provider = self.env["nudge.provider"].sudo()
        self.assertEqual(Provider._get_default(), self.local)
        self.provider.write({"stt_mode": "chat"})
        self.assertEqual(Provider._get_listener(), self.env["nudge.provider"].browse() | (
            self.local if self.local.can_transcribe else self.env["nudge.provider"]))
        self.assertEqual(self.env["nudge.voice"]._get_engine(), "provider")

    def test_the_panel_knows(self):
        user = self.user_a
        data = self.env["ir.http"].with_user(user)._get_nudge_session_info()
        self.assertTrue(data["is_private"])
        self.env.company.nudge_private_mode = False
        self.assertFalse(self.env["ir.http"].with_user(user)._get_nudge_session_info()["is_private"])

    def test_the_address_hint(self):
        self.assertTrue(self.local.looks_local)
        self.assertFalse(self.provider.looks_local)
        self.local.base_url = "http://192.168.1.20:11434/v1"
        self.assertTrue(self.local.looks_local)
        self.assertFalse(self.provider.runs_locally, "The administrator confirms, never the address")

    def test_the_setting_is_the_companys(self):
        settings = self.env["res.config.settings"].create({"nudge_private_mode": False})
        settings.execute()
        self.assertFalse(self.env.company.nudge_private_mode)

    # ---------------------------------------------------------- speech of the browser
    def session(self):
        return self.env["nudge.voice"].with_user(self.user_a)._get_session_info()

    def test_browser_speech_is_off_in_private_mode_until_allowed(self):
        data = self.session()
        self.assertEqual(
            (data["voice_private"], data["voice_engine"], data["can_transcribe"], data["browser_speech_ok"]),
            (True, "provider", False, False))
        self.assertIn("Private mode", data["voice_blocked_reason"])
        self.assertFalse(self.env.company.nudge_private_browser_speech, "Off unless an administrator says so")

    def test_the_switch_makes_the_browser_the_engine_when_no_provider_of_ours_listens(self):
        self.env.company.nudge_private_browser_speech = True
        data = self.session()
        self.assertEqual((data["voice_engine"], data["voice_blocked_reason"], data["browser_speech_ok"]),
                         ("browser", "", True))
        # A provider on our own server that listens keeps the sound on our server
        self.local.write({"stt_mode": "chat", "stt_model": "whisper"})
        data = self.session()
        self.assertEqual((data["voice_engine"], data["can_transcribe"]), ("provider", True))
        # The browser does not listen for the provider's path to refuse
        self.local.stt_mode = "none"
        self.env.company.nudge_private_mode = False
        data = self.session()
        self.assertEqual((data["voice_private"], data["voice_engine"], data["browser_speech_ok"]),
                         (False, "auto", True))

    def test_the_switch_is_a_setting_of_the_company(self):
        settings = self.env["res.config.settings"].create({"nudge_private_browser_speech": True})
        settings.set_values()
        self.assertTrue(self.env.company.nudge_private_browser_speech)
        self.assertTrue(self.env["res.config.settings"].create({}).nudge_private_browser_speech)

    def test_the_dialects_can_be_chosen(self):
        settings = self.env["res.users.settings"]._find_or_create_for_user(self.user_a)
        for tag in ("ar-SA", "ar-EG", "ar-AE", "ar-SY", "en-GB"):
            settings.nudge_voice_language = tag
            self.assertEqual(settings.nudge_voice_language, tag)

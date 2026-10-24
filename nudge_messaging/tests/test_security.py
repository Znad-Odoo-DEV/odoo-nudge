import time
from datetime import timedelta
from unittest.mock import patch

import requests

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge_messaging.hooks import uninstall_hook

from .test_messaging import BOT_TOKEN, MessagingCase, RAGHAD_PHONE


@tagged("post_install", "-at_install", "nudge_messaging")
class TestRelease2Security(MessagingCase, TransactionCase):

    def receive(self, text, sender, message_id):
        """The webhook keeps the message, the scheduled action answers it; :return: the state it ended in"""
        self.env["nudge.messaging"]._receive(
            "whatsapp", {"id": message_id, "sender": sender, "kind": "text", "text": text, "media": {}})
        self.env["nudge.messaging"]._cron_answer()
        return self.env["nudge.messaging.message"].sudo().search([("ext_id", "=", message_id)]).state

    def test_the_bot_methods_cannot_be_called_over_rpc(self):
        # Odoo refuses every name that starts with an underscore when it comes through call_kw
        api = self.env["nudge.messaging.api"]
        for name in ("send_text", "download", "telegram_set_webhook"):
            self.assertFalse(hasattr(type(api), name), name)
        for name in ("_send_text", "_download", "_telegram_set_webhook"):
            self.assertTrue(hasattr(type(api), name), name)
        public = [n for n in dir(type(api)) if not n.startswith("_") and callable(getattr(type(api), n))
                  and getattr(getattr(type(api), n), "__module__", "").startswith("odoo.addons.nudge_messaging")]
        self.assertEqual(public, [], "no public method may be left on the bot door")

    def test_a_sender_who_guesses_wrong_three_times_is_stopped_even_with_the_right_code(self):
        user = self.ebrahim
        code = self.env["nudge.messaging.code"]._make(user)
        for index in range(3):
            self.assertEqual(self.receive("123456" if code != "123456" else "654321", "971555", f"m{index}"), "wrong_code")
        self.assertEqual(self.receive(code, "971555", "m-right"), "limited", "Refused, not tried")
        self.assertFalse(self.env["nudge.messaging.link"]._link_for("whatsapp", "971555"))
        # another phone with the right code still links: the code was not burnt by the guesser
        self.assertEqual(self.receive(code, "971556", "m-other"), "linked")

    def test_a_code_works_once(self):
        code = self.env["nudge.messaging.code"]._make(self.ebrahim)
        self.assertTrue(self.env["nudge.messaging.code"]._redeem(code, "whatsapp", "971501"))
        self.assertFalse(self.env["nudge.messaging.code"]._redeem(code, "whatsapp", "971502"))

    def test_a_code_expires_after_ten_minutes(self):
        Code = self.env["nudge.messaging.code"]
        code = Code._make(self.ebrahim)
        Code.sudo().search([("user_id", "=", self.ebrahim.id)]).expires_at = fields.Datetime.now() - timedelta(seconds=1)
        self.assertFalse(Code._redeem(code, "whatsapp", "971501"))

    def test_thirty_wrong_guesses_in_a_minute_lock_every_code(self):
        Code = self.env["nudge.messaging.code"]
        code = Code._make(self.ebrahim)
        Message = self.env["nudge.messaging.message"]
        for index in range(30):
            Message._reserve("whatsapp", f"bad{index}", f"9715{index:04d}").state = "wrong_code"
        self.assertEqual(self.receive(code, "971599", "m-late"), "limited")
        self.assertTrue(all(Code.sudo().search([("user_id", "=", self.ebrahim.id)]).mapped("used")))
        self.assertEqual(self.receive(code, "971598", "m-later"), "limited")

    def test_uninstalling_takes_the_keys_away_and_the_webhook_off_telegram(self):
        """Review odoo-tech #10: the keys of the channels do not outlive the module, and a Telegram out of
        reach does not stop the uninstall"""
        Parameter = self.env["ir.config_parameter"].sudo()
        called = []

        def unreachable(url, **kwargs):
            called.append(url)
            raise requests.ConnectionError("Telegram is out of reach")
        with patch.object(requests, "post", unreachable):
            uninstall_hook(self.env)
        self.assertEqual(called, [f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook"])
        self.assertFalse(Parameter.search([("key", "=like", "nudge_messaging.%")]))
        self.assertEqual(Parameter.get_param("nudge.noai_first"), "1", "The parameters of Nudge stay")
        # No token: Telegram is not called
        with patch.object(requests, "post", unreachable):
            uninstall_hook(self.env)
        self.assertEqual(len(called), 1)

    def test_one_phone_or_two_cannot_close_linking_for_everybody(self):
        """Review S5: a try the guard refused is not a wrong code, and each phone weighs three wrong codes
        at most in the caps of all the phones together"""
        Code = self.env["nudge.messaging.code"]
        code = Code._make(self.ebrahim)
        wrong = "123456" if code != "123456" else "654321"
        states = [self.receive(wrong, "971577", f"flood{index}") for index in range(10)]
        self.assertEqual(states, ["wrong_code"] * 3 + ["limited"] * 7, "Three tries, then refused and not counted")
        # Wrong codes of one phone and of another (a race, a log of before) weigh three each
        Message = self.env["nudge.messaging.message"]
        for index in range(60):
            Message._reserve("whatsapp", f"one{index}", "971578").state = "wrong_code"
        for index in range(15):
            Message._reserve("whatsapp", f"two{index}", "971579").state = "wrong_code"
        self.assertEqual(Code._wrong_codes(fields.Datetime.now() - timedelta(hours=1)), 9)
        self.assertFalse(Code._too_many_wrong())
        self.assertTrue(Code._guard("whatsapp", "971580"))
        self.assertFalse(Code.sudo().search([("user_id", "=", self.ebrahim.id)]).used, "The live code is not voided")
        self.assertEqual(self.receive(code, "971580", "m-right"), "linked")

    def test_private_mode_closes_the_channel_unless_the_administrator_allows_it(self):
        self.overdue_invoices(2)
        self.link(self.raghad)
        self.env.company.nudge_private_mode = True
        self.receive("how many overdue invoices", RAGHAD_PHONE, "p1")
        self.assertIn("Private mode is on", self.sent[-1][2])
        self.assertNotIn("2", self.sent[-1][2])
        self.env["ir.config_parameter"].sudo().set_param("nudge_messaging.allow_private", "True")
        self.receive("how many overdue invoices", RAGHAD_PHONE, "p2")
        self.assertNotIn("Private mode is on", self.sent[-1][2])

    def test_a_voice_note_must_look_like_audio_and_ffmpeg_is_pinned(self):
        Messaging = self.env["nudge.messaging"]
        with self.assertRaises(UserError):
            Messaging._to_wav(b"#EXTM3U\nhttp://127.0.0.1/x")
        with self.assertRaises(UserError):
            Messaging._to_wav(b"ffconcat version 1.0\nfile /etc/passwd")
        seen = []

        def fake_run(args, **kwargs):
            seen.append(args)
            return type("Done", (), {"stdout": b"RIFF"})()

        with patch("odoo.addons.nudge_messaging.models.nudge_messaging.shutil.which", return_value="ffmpeg"),                 patch("odoo.addons.nudge_messaging.models.nudge_messaging.subprocess.run", fake_run):
            Messaging._to_wav(b"OggS" + b"\0" * 20)
        args = seen[0]
        self.assertEqual(args[args.index("-protocol_whitelist") + 1], "pipe")
        self.assertEqual(args[args.index("-f") + 1], "ogg")
        self.assertIn("-t", args)

    def test_a_stale_body_is_refused_and_the_log_is_purged_after_ninety_days(self):
        self.link(self.ebrahim, sender="971511")
        old = int(time.time()) - 3 * 86400
        event = {"id": "old1", "sender": "971511", "kind": "text", "text": "hi", "media": {}, "timestamp": str(old)}
        self.assertEqual(self.env["nudge.messaging"]._receive("whatsapp", event), "stale")
        Message = self.env["nudge.messaging.message"]
        line = Message._reserve("whatsapp", "ancient", "971511")
        self.env.cr.execute("UPDATE nudge_messaging_message SET create_date = now() - interval '91 days' WHERE id = %s",
                            [line.id])
        line.invalidate_recordset()
        Message._cron_purge()
        self.assertFalse(line.exists())

    def test_an_unknown_phone_is_answered_once_an_hour(self):
        self.receive("hello", "971522", "u1")
        self.receive("hello again", "971522", "u2")
        self.receive("and again", "971522", "u3")
        self.assertEqual(len(self.sent), 1)

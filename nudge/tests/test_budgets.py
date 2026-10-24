import logging

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge.llm import base

from .test_agent import asking, call, saying, values
from .test_background import BackgroundCase
from .test_voice import wav

CREATE = call("prepare_create", {
    "model": "res.partner", "values": values(name="Nudge Made")}, "call_c")


@tagged("post_install", "-at_install")
class TestUsage(BackgroundCase, TransactionCase):

    def setUp(self):
        super().setUp()
        self.Usage = self.env["nudge.usage"]
        self.before = self.Usage.sudo().search([])

    def spent(self, user=None):
        domain = [("id", "not in", self.before.ids)]
        if user:
            domain.append(("user_id", "=", user.id))
        return self.Usage.sudo().search(domain, order="id")

    def set_budgets(self, **budgets):
        Parameter = self.env["ir.config_parameter"].sudo()
        for name, value in budgets.items():
            Parameter.set_param(f"nudge.budget_{name}", str(value))

    def test_every_call_is_written_down(self):
        self.use_adapter(
            reply="Hello", usage={"input_tokens": 1000, "output_tokens": 500, "cache_read_tokens": 200})
        conversation, _event = self.ask(self.user_a, "Hello")
        usage = self.spent(self.user_a)

        self.assertEqual(len(usage), 1)
        self.assertEqual(usage.kind, "chat")
        self.assertEqual(usage.user_id, self.user_a)
        self.assertEqual(usage.company_id, self.user_a.company_id)
        self.assertEqual(usage.date, fields.Date.context_today(self.env(user=self.user_a)["res.users"]))
        self.assertEqual(usage.conversation_id, conversation)
        self.assertEqual(usage.message_id, conversation.message_ids[1])
        self.assertEqual(usage.provider_id, self.provider)
        self.assertEqual(usage.model_name, "fake-model")
        self.assertEqual((usage.input_tokens, usage.output_tokens, usage.cache_read_tokens), (1000, 500, 200))
        self.assertEqual(usage.tokens, 1700)
        # 1000 at 2 per million, 500 at 10 per million, 200 read from the cache at 1 per million: the cache
        # price left empty follows the input price, half of it on an OpenAI-compatible server (S8)
        self.assertAlmostEqual(usage.cost, 0.0072)
        self.assertAlmostEqual(conversation.cost, 0.0072, msg="The conversation keeps its own total")

    def test_one_line_per_call_to_the_model(self):
        self.use_adapter(script=[asking(CREATE), saying("Done.")])
        conversation, _events = self.order(self.agent, "Create the contact Nudge Made")
        self.assertEqual(len(self.spent(self.agent)), 1)
        self.decide(conversation)
        self.assertEqual(len(self.spent(self.agent)), 2)
        self.assertEqual(self.spent(self.agent).mapped("kind"), ["chat", "chat"])

    def test_a_failed_call_is_written_down_too(self):
        self.use_adapter(events=[
            base.text_event("Partial"),
            base.error_event(base.ERROR_UNAVAILABLE, "overloaded"),
        ])
        self.ask(self.user_a, "Hello")
        self.assertEqual(len(self.spent(self.user_a)), 1)
        self.assertEqual(self.spent(self.user_a).tokens, 0)

    def test_speech_is_written_down(self):
        self.provider.write({"stt_mode": "chat"})
        self.use_adapter(heard="Hello")
        with self.assertLogs("odoo.addons.nudge.models.nudge_voice", logging.INFO):
            self.env["nudge.voice"].with_user(self.user_a)._transcribe(wav(), language="en")
        usage = self.spent(self.user_a)
        self.assertEqual(usage.kind, "speech")
        self.assertEqual((usage.input_tokens, usage.output_tokens), (120, 8))
        self.assertEqual(usage.model_name, "fake-speech")
        self.assertFalse(usage.conversation_id)
        # 120 at 2 per million, 8 at 10 per million
        self.assertAlmostEqual(usage.cost, 0.00032)

    def test_the_ledger_is_changed_by_nobody(self):
        self.use_adapter()
        self.ask(self.user_a, "Hello")
        usage = self.spent(self.user_a)
        for user in (self.user_a, self.manager, self.env.ref("base.user_admin")):
            with self.assertRaises(AccessError):
                usage.with_user(user).write({"tokens": 0})
            with self.assertRaises(AccessError):
                usage.with_user(user).unlink()
            with self.assertRaises(AccessError):
                self.Usage.with_user(user).create({"user_id": user.id, "date": fields.Date.today()})
        self.assertEqual(usage.with_user(self.manager).tokens, usage.tokens, "Managers read it")
        with self.assertRaises(AccessError):
            usage.with_user(self.user_a).read(["tokens"])

    def test_limits_of_the_person(self):
        self.use_adapter(reply="Hello", usage={"input_tokens": 600, "output_tokens": 400})
        self.set_budgets(user_tokens=2500)
        self.ask(self.user_a, "One")
        self.ask(self.user_a, "Two")
        self.assertEqual(sum(self.spent(self.user_a).mapped("tokens")), 2000)
        self.ask(self.user_a, "Three")
        with self.assertRaises(UserError) as capture:
            self.ask(self.user_a, "Four")
        self.assertIn("tokens for today", str(capture.exception))
        # Others are not held by what this person spent
        self.ask(self.user_b, "Hello")
        self.assertEqual(len(self.spent(self.user_b)), 1)
        # A limit of cost, in the prices of the provider: 0.0052 per call here
        self.set_budgets(user_tokens=0, user_cost=0.008)
        self.ask(self.user_b, "Again")
        with self.assertRaises(UserError) as capture:
            self.ask(self.user_b, "And again")
        self.assertIn("budget for today", str(capture.exception))
        self.set_budgets(user_cost=0)
        self.ask(self.user_b, "Free again")

    def test_limits_of_the_company(self):
        self.use_adapter(reply="Hello", usage={"input_tokens": 600, "output_tokens": 400})
        self.set_budgets(company_tokens=3000)
        self.ask(self.user_a, "One")
        self.ask(self.user_b, "Two")
        self.ask(self.user_a, "Three")
        for user in (self.user_a, self.user_b, self.manager):
            with self.assertRaises(UserError) as capture:
                self.ask(user, "Four")
            self.assertIn("company", str(capture.exception))
        # Another company is not held
        other = self.env["res.company"].create({"name": "Nudge Other Co"})
        self.user_a.write({"company_ids": [(4, other.id)], "company_id": other.id})
        conversation, _event = self.ask(self.user_a, "Elsewhere")
        self.assertEqual(conversation.company_id, other)

    def test_a_call_for_another_company_counts_against_that_company(self):
        """Review (ai #16): the mail of company 2, sorted by the cron as OdooBot, was checked against company 1."""
        adapter = self.use_adapter(reply='{"kind": "lead", "confidence": 0.9}')
        other = self.env["res.company"].create({"name": "Raghad Mail Co"})
        robot = self.env.ref("base.user_root")
        self.Usage._add("chat", {"input_tokens": 5000, "output_tokens": 0}, 0.0, robot, other)
        self.set_budgets(company_tokens=3000)
        Provider = self.env["nudge.provider"].sudo()
        self.assertEqual(Provider._complete("Sort.", "A mail.", robot, other), "", "Company 2 used its tokens")
        self.assertEqual(adapter.calls, [])
        self.assertTrue(Provider._complete("Sort.", "A mail.", robot, self.env.company), "Company 1 did not")

    def spend(self, user, tokens=1000):
        """What the person spent already, whatever the adapter says"""
        return self.Usage._add(
            "chat", {"input_tokens": tokens, "output_tokens": 0}, 0.0, user, user.company_id)

    def test_what_a_limit_stops(self):
        self.use_adapter(script=[asking(CREATE), saying("Done."), saying("Again")], heard="Hello")
        self.provider.write({"stt_mode": "chat"})
        conversation, _events = self.order(self.agent, "Create the contact Nudge Made")
        self.spend(self.agent)
        self.set_budgets(user_tokens=1)
        # The yes itself is done; the model is not called for what follows
        with self.assertRaises(UserError):
            self.decide(conversation)
        self.assertFalse(self.made())
        with self.assertRaises(UserError):
            self.env["nudge.voice"].with_user(self.agent)._transcribe(wav(), language="en")
        with self.assertRaises(UserError):
            self.leave(self.agent, "In the background")

    def test_a_run_in_the_background_gives_up(self):
        self.use_adapter(script=[asking(CREATE), saying("Done.")])
        conversation, _events = self.leave(self.agent, "Create the contact Nudge Made")
        run = conversation.sudo().run_id
        self.spend(self.agent)
        self.set_budgets(user_tokens=1)
        self.work(run)
        self.assertEqual(run.state, "failed")
        self.assertIn("tokens for today", conversation.message_ids[-1].error_message)
        self.assertEqual(self.told()[-1][1]["kind"], "failed")

    def test_settings(self):
        settings = self.env["res.config.settings"].create({})
        self.assertEqual(settings.nudge_budget_user_tokens, 0)
        self.assertEqual(settings.nudge_budget_company_cost, 0.0)
        settings.write({"nudge_budget_user_tokens": 50000, "nudge_budget_company_cost": 12.5})
        settings.set_values()
        self.assertEqual(self.env["nudge.usage"]._get_budgets(), {
            "user_tokens": 50000, "user_cost": 0, "company_tokens": 0, "company_cost": 12.5})
        self.env["ir.config_parameter"].sudo().set_param("nudge.budget_user_cost", "not a number")
        self.assertEqual(self.env["nudge.usage"]._get_budgets()["user_cost"], 0)

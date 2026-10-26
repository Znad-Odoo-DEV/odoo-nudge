from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from .common import NudgeCase

NONE = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}


@tagged("post_install", "-at_install")
class TestTokenDashboard(NudgeCase, TransactionCase):
    """The token dashboard: one row per source, mode and role with the tokens of the ledger, the ratios, the
    companies of the manager, and who may read it."""

    def setUp(self):
        super().setUp()
        self.Dash = self.env["nudge.token.dashboard"]
        self.company = self.env.company
        self.admin = new_test_user(self.env, login="nudge_dash_admin", groups="nudge.group_nudge_manager,base.group_system")

    def line(self, user, kind, mode="talk", usage=None, company=None, tool_calls=None, question_mode=None):
        """An answer of Nudge to ``user`` and the line of the ledger it wrote"""
        company = company or self.company
        conversation = self.env["nudge.conversation"].sudo().create({
            "user_id": user.id, "name": "Raghad", "company_id": company.id})
        Message = self.env["nudge.message"].sudo()
        if question_mode:
            Message.create({"conversation_id": conversation.id, "role": "user", "content": "Raghad", "mode": question_mode})
        message = Message.create({
            "conversation_id": conversation.id, "role": "assistant", "state": "complete",
            "mode": mode if not question_mode else False, "tool_calls": tool_calls or False})
        self.env["nudge.usage"]._add(
            kind, dict(NONE, **(usage or {})), 0.5 if usage else 0.0, user, company,
            message_id=message.id, conversation_id=conversation.id,
            provider_id=self.provider.id if kind == "chat" else False)
        return message

    def rows(self, **domain):
        self.env.flush_all()
        return self.Dash.with_user(self.manager).search(
            [(key, "=", value) for key, value in domain.items()] + [("company_id", "=", self.company.id)])

    def test_each_source_has_its_row(self):
        self.line(self.user_a, "chat", "agent", {"input_tokens": 100, "output_tokens": 20, "cache_read_tokens": 300},
                  tool_calls=[{"name": "a"}, {"name": "b"}])
        self.line(self.user_a, "memory")
        self.line(self.user_a, "cache")
        self.line(self.user_a, "noai")
        self.line(self.user_a, "speech", usage={"input_tokens": 7})
        by_source = {row.source: row for row in self.rows(role="user")}
        self.assertEqual(set(by_source), {"ai", "memory", "cache", "noai", "other"})
        ai = by_source["ai"]
        self.assertEqual((ai.mode, ai.answers, ai.ai_answers, ai.no_ai_answers), ("agent", 1, 1, 0))
        self.assertEqual((ai.input_tokens, ai.output_tokens, ai.cache_read_tokens, ai.tokens), (100, 20, 300, 420))
        self.assertEqual(ai.tool_calls, 2)
        self.assertEqual(ai.provider_id, self.provider)
        for source in ("memory", "cache", "noai"):
            row = by_source[source]
            self.assertEqual((row.answers, row.no_ai_answers, row.tokens), (1, 1, 0))
        self.assertEqual((by_source["other"].answers, by_source["other"].tokens), (0, 7))

    def test_mode_of_an_answer_without_one_is_that_of_its_question(self):
        self.line(self.user_a, "cache", mode="talk", question_mode="agent")
        self.line(self.user_a, "memory", mode="talk", question_mode="dev")
        self.assertEqual(self.rows(source="cache").mode, "agent")
        self.assertEqual(self.rows(source="memory").mode, "dev")

    def test_role_is_the_highest_held(self):
        for user in (self.user_a, self.agent, self.manager, self.admin):
            self.line(user, "noai")
        self.env.flush_all()
        roles = {row.role for row in self.Dash.with_user(self.manager).search([("company_id", "=", self.company.id)])}
        self.assertTrue({"user", "agent", "manager", "admin"} <= roles)
        self.assertEqual(len(self.rows(role="admin")), 1)

    def test_ratios_are_those_of_the_sums(self):
        today = fields.Date.context_today(self.Dash)
        self.line(self.user_a, "chat", "talk", {"input_tokens": 100, "output_tokens": 50, "cache_read_tokens": 300})
        self.line(self.user_a, "chat", "talk", {"input_tokens": 300, "output_tokens": 50})
        self.line(self.user_a, "cache")
        self.line(self.user_a, "noai")
        summary = self.Dash.with_user(self.manager).summary(today, today)
        total = summary["total"]
        self.assertEqual(total["answers"], 4)
        self.assertAlmostEqual(total["no_ai_share"], 0.5)
        # 300 read from the cache of 700 that were input or read
        self.assertAlmostEqual(total["cache_hit_rate"], 300 / 700)
        self.assertAlmostEqual(total["tokens_per_answer"], 800 / 4)
        self.assertEqual([row["name"] for row in summary["by_mode"]], ["Talk"])
        self.assertEqual(
            sorted(row["name"] for row in summary["by_source"]),
            ["AI Call", "Answer Cache", "Understood Without AI"])
        empty = self.Dash.with_user(self.manager).summary("2000-01-01", "2000-01-02")["total"]
        self.assertIsNone(empty["cache_hit_rate"])
        self.assertIsNone(empty["no_ai_share"])

    def test_a_manager_sees_the_companies_he_works_in(self):
        other = self.env["res.company"].create({"name": "Your company, the other"})
        self.line(self.user_a, "chat", "talk", {"input_tokens": 10}, company=other)
        self.line(self.user_a, "chat", "talk", {"input_tokens": 5})
        self.env.flush_all()
        Dash = self.Dash.with_user(self.manager)
        ours = Dash.with_context(allowed_company_ids=[self.company.id])
        rows = ours.search([("source", "=", "ai"), ("role", "=", "user")])
        self.assertEqual(rows.mapped("company_id"), self.company)
        self.assertEqual(sum(rows.mapped("input_tokens")), 5)
        self.assertNotIn(other, Dash.search([]).mapped("company_id"))

    def test_a_plain_user_has_no_access(self):
        self.line(self.user_a, "noai")
        with self.assertRaises(AccessError):
            self.Dash.with_user(self.user_a).search([])
        with self.assertRaises(AccessError):
            self.Dash.with_user(self.user_a).summary("2000-01-01", "2000-01-02")

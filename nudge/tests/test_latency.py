from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from .common import NudgeCase


@tagged("post_install", "-at_install")
class TestLatencyPanel(NudgeCase, TransactionCase):
    """NP-20: the numbers of the latency and cost panel, who may read them, and the usage ledger."""

    def setUp(self):
        super().setUp()
        self.Report = self.env["nudge.latency.report"]
        self.today = fields.Date.context_today(self.env["nudge.usage"])
        self.company = self.env.company

    def answer(self, user, mode="agent", duration=0, state="complete", company=None, cost=0.0, tokens=0):
        """An answer of Nudge to Raghad or Ebrahim, with the usage line the provider call wrote"""
        company = company or self.company
        conversation = self.env["nudge.conversation"].sudo().create({
            "user_id": user.id, "name": "Raghad", "company_id": company.id})
        message = self.env["nudge.message"].sudo().create({
            "conversation_id": conversation.id, "role": "assistant", "mode": mode, "state": state,
            "duration_ms": duration, "input_tokens": tokens, "cost": cost})
        self.env["nudge.usage"]._add(
            "chat", {"input_tokens": tokens}, cost, user, company, message_id=message.id,
            provider_id=self.provider.id, conversation_id=conversation.id)
        return message

    def row(self, mode, report=None):
        self.env.flush_all()
        report = (report or self.Report.with_user(self.manager))
        return report.search([("mode", "=", mode), ("user_id", "=", self.user_a.id)])

    def test_percentiles_and_totals(self):
        for duration in (1000, 2000, 3000, 4000, 10000):
            self.answer(self.user_a, "agent", duration, cost=0.5, tokens=100)
        # No call, no duration: counted as an answer, not in the percentiles
        self.answer(self.user_a, "agent", 0)
        self.answer(self.user_a, "agent", 500, state="error")
        self.answer(self.user_a, "agent", 600, state="refused")
        self.answer(self.user_a, "talk", 2000)
        row = self.row("agent")
        self.assertEqual(len(row), 1)
        self.assertEqual(row.answers, 8)
        self.assertEqual(row.timed_answers, 7)
        self.assertEqual((row.errors, row.refusals), (1, 1))
        self.assertEqual(row.tokens, 500)
        self.assertAlmostEqual(row.cost, 2.5)
        self.assertEqual(row.provider_id, self.provider)
        # 500 600 1000 2000 3000 4000 10000: the middle one, and 0.95 of the way up
        self.assertAlmostEqual(row.duration_p50, 2000)
        self.assertAlmostEqual(row.duration_p95, 4000 + 0.7 * 6000)
        self.assertEqual(self.row("talk").duration_p50, 2000)

    def test_panel_is_exact_over_the_period(self):
        for duration in (1000, 2000, 3000, 4000, 10000):
            self.answer(self.user_a, "agent", duration)
        panel = self.Report.with_user(self.manager).panel(self.today, self.today)
        agent = next(line for line in panel["by_mode"] if line["name"] == "agent")
        self.assertEqual(agent["answers"], 5)
        self.assertAlmostEqual(agent["duration_p50"], 3000)
        self.assertAlmostEqual(agent["duration_p95"], 8800)
        self.assertTrue(any(line["name"] == "Test provider" for line in panel["by_provider"]))
        self.assertTrue(any(line["name"] == self.user_a.name for line in panel["by_user"]))
        self.assertEqual([line["name"] for line in panel["by_day"]], [str(self.today)])
        # The waiting room and the first line are not measured yet: nothing, not zero
        self.assertIsNone(agent["queue_wait_p50"])
        self.assertIsNone(agent["first_line_p95"])
        self.assertEqual(self.row("agent").queue_wait_samples, 0)
        # Another day is another panel
        empty = self.Report.with_user(self.manager).panel(self.today - relativedelta(days=5), self.today - relativedelta(days=1))
        self.assertEqual(empty["by_mode"], [])

    def test_managers_only(self):
        self.answer(self.user_a, "agent", 1000)
        for user in (self.user_a, self.agent, self.outsider):
            with self.subTest(user=user.login):
                with self.assertRaises(AccessError):
                    self.Report.with_user(user).search([])
                with self.assertRaises(AccessError):
                    self.Report.with_user(user).panel(self.today, self.today)

    def test_companies_are_split(self):
        other = self.env["res.company"].create({"name": "Your company, the other"})
        self.manager.company_ids = [(4, other.id)]
        self.answer(self.user_a, "agent", 1000)
        self.answer(self.user_a, "agent", 9000, company=other)
        Report = self.Report.with_user(self.manager)
        only_ours = Report.with_context(allowed_company_ids=[self.company.id])
        both = Report.with_context(allowed_company_ids=[self.company.id, other.id])
        self.assertEqual(sum(only_ours.search([("user_id", "=", self.user_a.id)]).mapped("answers")), 1)
        self.assertEqual(sum(both.search([("user_id", "=", self.user_a.id)]).mapped("answers")), 2)
        panel = only_ours.panel(self.today, self.today)
        self.assertEqual([line["answers"] for line in panel["by_company"]], [1])
        panel = both.panel(self.today, self.today)
        self.assertEqual(sorted(line["answers"] for line in panel["by_company"]), [1, 1])

    def test_indexes_exist_and_serve_the_daily_sum(self):
        cr = self.env.cr
        cr.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'nudge_usage'")
        names = {name for [name] in cr.fetchall()}
        self.assertIn("nudge_usage_user_date_sums_idx", names)
        self.assertIn("nudge_usage_company_date_sums_idx", names)
        self.answer(self.user_a, "agent", 1000, tokens=10)
        cr.execute("SAVEPOINT np20_plan")
        # Only the covering index can serve the sum without the table: the planner of a tiny table
        # may otherwise pick the plain index of the date, so the choice is left to the cover
        cr.execute("SET LOCAL enable_seqscan = off")
        cr.execute("SET LOCAL enable_indexscan = off")
        cr.execute("SET LOCAL enable_bitmapscan = off")
        cr.execute(
            "EXPLAIN SELECT SUM(tokens), SUM(cost) FROM nudge_usage WHERE user_id = %s AND date = %s",
            (self.user_a.id, self.today))
        plan = "\n".join(line for [line] in cr.fetchall())
        cr.execute("ROLLBACK TO SAVEPOINT np20_plan")
        self.assertIn("nudge_usage_user_date_sums_idx", plan)
        self.assertIn("Index", plan)


@tagged("post_install", "-at_install")
class TestUsageRetention(NudgeCase, TransactionCase):

    def setUp(self):
        super().setUp()
        self.Usage = self.env["nudge.usage"]
        self.today = fields.Date.context_today(self.Usage)
        self.first = self.today.replace(day=1)
        self.company = self.env.company
        self.before = self.Usage.sudo().search([])

    def add(self, day, tokens=100):
        usage = self.Usage._add("chat", {"input_tokens": tokens}, 1.0, self.user_a, self.company)
        self.env.cr.execute("UPDATE nudge_usage SET date = %s WHERE id = %s", (day, usage.id))
        usage.invalidate_recordset()
        return usage

    def mine(self):
        return self.Usage.sudo().search([("id", "not in", self.before.ids)])

    def set_months(self, months):
        self.env["ir.config_parameter"].sudo().set_param("nudge.usage_retention_months", str(months))

    def test_default_keeps_everything(self):
        self.add(self.today - relativedelta(years=3))
        self.assertEqual(self.Usage._cron_apply_retention(), 0)
        self.assertEqual(len(self.mine()), 1)

    def test_purge_keeps_the_budget_period_and_works_in_batches(self):
        old = [self.add(self.first - relativedelta(months=3, days=n)) for n in range(5)]
        last_month = self.add(self.first - relativedelta(days=1))
        this_month = self.add(self.first)
        today = self.add(self.today)
        kept = last_month | this_month | today
        spent_before = self.Usage._spent([
            ("company_id", "=", self.company.id), ("date", ">=", self.first),
            ("id", "not in", self.before.ids)])
        self.set_months(1)
        self.assertEqual(self.Usage._cron_apply_retention(batch=2), 2)
        self.assertEqual(self.Usage._cron_apply_retention(batch=2), 2)
        self.assertEqual(self.Usage._cron_apply_retention(batch=2), 1)
        self.assertEqual(self.Usage._cron_apply_retention(batch=2), 0)
        self.assertEqual(self.mine(), kept)
        self.assertFalse(self.Usage.sudo().browse(o.id for o in old).exists())
        self.assertEqual(spent_before, self.Usage._spent([
            ("company_id", "=", self.company.id), ("date", ">=", self.first),
            ("id", "not in", self.before.ids)]))

    def test_current_month_is_never_purged(self):
        this_month = self.add(self.first)
        self.set_months(1)
        self.Usage._cron_apply_retention()
        self.assertEqual(self.mine(), this_month)

    def test_the_ledger_still_refuses_unlink(self):
        usage = self.add(self.today)
        with self.assertRaises(AccessError):
            usage.sudo().unlink()

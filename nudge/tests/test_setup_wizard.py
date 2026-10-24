import logging

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge.llm import base
from odoo.addons.nudge.models.nudge_setup_wizard import estimate, price_table

from .common import NudgeCase

SECRET = "sk-wizard-secret-0001"


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@tagged("post_install", "-at_install")
class TestSetupWizard(NudgeCase, TransactionCase):

    def setUp(self):
        super().setUp()
        self.Wizard = self.env["nudge.setup.wizard"]

    def other_wizard(self, **values):
        return self.Wizard.create({
            "choice": "other", "base_url": "https://llm.invalid/v1", "model_name": "wiz-model",
            "api_key": SECRET, **values})

    def test_the_estimate_is_users_times_messages_times_prompt_times_price(self):
        # 10 people x 20 messages x 22 days = 4,400 calls of 10.6k in and 0.4k out
        figures = estimate(10, 20, 22, 1, 10600, 400, 4.0, 20.0)
        self.assertEqual(figures["calls"], 4400)
        self.assertEqual(figures["tokens_in"], 46_640_000)
        self.assertAlmostEqual(figures["cost"], 46.64 * 4 + 1.76 * 20)
        self.assertAlmostEqual(figures["cost_person"], figures["cost"] / 10)
        self.assertAlmostEqual(figures["cost_message"], figures["cost"] / 4400)
        self.assertEqual(estimate(0, 20, 22, 1, 10600, 400, 4.0, 20.0)["cost_person"], 0.0)

    def test_the_price_table_is_dated_and_holds_the_measured_prompts(self):
        table = price_table()
        self.assertRegex(table["date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(table["prompt_tokens"], {"talk": 10600, "agent": 22900, "developer": 36000})
        wizard = self.Wizard.create({"choice": "claude", "mode": "agent"})
        wizard._onchange_mode()
        wizard._onchange_choice()
        self.assertEqual(wizard.prompt_tokens, 22900)
        self.assertEqual(wizard.calls_per_message, 3)
        self.assertEqual(wizard.price_date, table["date"])
        figures = estimate(10, 20, 22, 3, 22900, 400, wizard.price_input, wizard.price_output)
        self.assertAlmostEqual(wizard.monthly_cost, figures["cost"], places=2)
        self.assertGreater(wizard.monthly_cost, 0)

    def test_the_prices_can_be_edited(self):
        wizard = self.Wizard.create({"choice": "claude"})
        wizard._onchange_choice()
        before = wizard.monthly_cost
        wizard.price_input *= 2
        self.assertGreater(wizard.monthly_cost, before)

    def test_rules_only_goes_through_with_no_provider(self):
        self.env["nudge.provider"].search([]).active = False
        wizard = self.Wizard.create({"choice": "rules"})
        self.assertEqual(wizard.monthly_cost, 0)
        wizard.action_next()
        self.assertEqual(wizard.step, "provider")
        wizard.action_next()
        self.assertEqual(wizard.step, "enable")
        count = self.env["nudge.provider"].with_context(active_test=False).search_count([])
        wizard.action_enable()
        self.assertEqual(self.env["nudge.provider"].with_context(active_test=False).search_count([]), count)
        self.assertEqual(self.env["ir.config_parameter"].sudo().get_param("nudge.noai_first"), "True")
        self.assertTrue(self.outsider.has_group("nudge.group_nudge_user"))
        wizard.action_back()
        self.assertEqual(wizard.step, "provider")

    def test_only_system_administrators_run_the_wizard(self):
        with self.assertRaises(AccessError):
            self.Wizard.with_user(self.manager).create({})
        with self.assertRaises(AccessError):
            self.Wizard.with_user(self.user_a).create({})
        wizard = self.Wizard.create({})
        with self.assertRaises(AccessError):
            wizard.with_user(self.manager).action_enable()

    def test_a_key_goes_to_the_provider_and_is_wiped_from_the_wizard(self):
        self.use_adapter()
        handler = Capture()
        root = logging.getLogger()
        root.addHandler(handler)
        previous = root.level
        root.setLevel(logging.DEBUG)
        try:
            wizard = self.other_wizard()
            wizard.action_test()
        finally:
            root.removeHandler(handler)
            root.setLevel(previous)
        self.assertTrue(wizard.tested)
        self.assertFalse(wizard.api_key)
        self.assertEqual(wizard.provider_id.api_key, SECRET)
        # The wizard's own table has no column for it, and no log line but the provider's own
        # SQL statement (debug level) carries it
        self.env.cr.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name = 'nudge_setup_wizard' "
            "AND column_name = 'api_key'")
        self.assertFalse(self.env.cr.fetchall())
        self.assertFalse([line for line in handler.lines if SECRET in line and "nudge_setup_wizard" in line])
        self.assertFalse([line for line in handler.lines if SECRET in line and "query: " not in line])
        # Only system administrators read it
        with self.assertRaises(AccessError):
            wizard.provider_id.with_user(self.manager).api_key  # noqa: B018
        self.assertNotIn(SECRET, wizard.test_result or "")

    def test_the_next_step_waits_for_a_successful_test(self):
        wizard = self.other_wizard(step="key")
        with self.assertRaises(UserError):
            wizard.action_next()
        self.use_adapter()
        wizard.action_test()
        wizard.action_next()
        self.assertEqual(wizard.step, "estimate")

    def test_a_failed_test_says_why_and_stays_on_the_step(self):
        adapter = self.use_adapter()
        adapter.check = lambda: (_ for _ in ()).throw(base.AdapterError("auth", "bad key"))
        wizard = self.other_wizard(step="key")
        wizard.action_test()
        self.assertFalse(wizard.tested)
        self.assertTrue(wizard.test_result)
        self.assertNotIn(SECRET, wizard.test_result)
        with self.assertRaises(UserError):
            wizard.action_next()

    def test_budgets_are_written_where_the_budget_feature_reads_them(self):
        self.use_adapter()
        wizard = self.other_wizard(
            user_count=10, messages_per_day=20, price_input=1.0, price_output=2.0)
        wizard.action_test()
        wizard.write({"step": "estimate"})
        wizard.action_next()
        self.assertEqual(wizard.step, "budgets")
        self.assertGreater(wizard.budget_user_cost, 0)
        wizard.write({"budget_user_tokens": 50000, "budget_user_cost": 1.5,
                      "budget_company_tokens": 9000000, "budget_company_cost": 80.0})
        wizard.write({"step": "enable", "data_ack": True})
        wizard.action_enable()
        budgets = self.env["nudge.usage"]._get_budgets()
        self.assertEqual(budgets["user_tokens"], 50000)
        self.assertEqual(budgets["user_cost"], 1.5)
        self.assertEqual(budgets["company_tokens"], 9000000)
        self.assertEqual(budgets["company_cost"], 80.0)
        param = self.env["ir.config_parameter"].sudo().get_param
        self.assertEqual(param("nudge.default_provider_id"), str(wizard.provider_id.id))

    def test_a_provider_outside_the_server_needs_the_acknowledgement_and_closes_the_door_by_default(self):
        """Review (functional 16): the wizard opened Nudge to every employee and sent data out without a word"""
        self.use_adapter()
        wizard = self.other_wizard()
        wizard._onchange_choice()
        self.assertFalse(wizard.all_users, "a cloud provider is not opened to every employee by default")
        wizard.action_test()
        wizard.write({"step": "enable"})
        with self.assertRaises(UserError) as stopped:
            wizard.action_enable()
        self.assertIn("what is sent", str(stopped.exception))
        wizard.write({"data_ack": True, "redact_pii": True})
        wizard.action_enable()
        self.assertTrue(self.env.company.nudge_redact_pii)
        self.assertFalse(self.outsider.has_group("nudge.group_nudge_user"))
        local = self.Wizard.create({"choice": "ollama"})
        local._onchange_choice()
        self.assertTrue(local.all_users)

    def test_enabling_a_provider_needs_a_passed_test(self):
        wizard = self.other_wizard()
        with self.assertRaises(UserError):
            wizard.action_enable()

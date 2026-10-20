import datetime
import json

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.tools import analytics, saved_reports
from odoo.addons.nudge.tools.analytics import TooBig
from odoo.addons.nudge.tools.noai_commands import grammar

from .common import NudgeCase, require_modules
from .test_agent import asking, call, saying

SCREEN = {"model": "sale.order", "record_id": None}

# Sentences that keep a report, half in Arabic: sentence, kind, groups, shape
SENTENCES = [
    ("طلّعلي مبيعات الشهر حسب المندوب كرسم واحفظو", "sales", ["by_user"], None),
    ("save this as a pivot of invoices by month and customer", "invoice", ["by_month", "by_partner"], "pivot"),
    ("احفظلي رسم المبيعات هالسنة حسب المنتج", "sales", ["by_product"], None),
    ("خزنلي جدول محوري للفواتير حسب الشهر والعميل", "invoice", ["by_month", "by_partner"], "pivot"),
    ("save a chart of sales by salesperson this month", "sales", ["by_user"], None),
    ("سيفلي بيفوت للمبيعات حسب المندوب والمنتج", "sales", ["by_user", "by_product"], "pivot"),
    ("keep a pie chart of orders per customer", "sales_order", ["by_partner"], "pie"),
    ("i7fadh rasm mabi3at 7asab almandoub", "sales", ["by_user"], None),
]

REF = "NudgeSaved"


@tagged("post_install", "-at_install")
class TestSavedReportParser(TransactionCase):
    """The words that ask to keep a report, in every dialect: no database, no AI."""

    def test_the_sentences_that_keep_a_report(self):
        arabic = 0
        for text, kind, groups, shape in SENTENCES:
            with self.subTest(text=text):
                parsed = grammar.parse(text, SCREEN)
                self.assertEqual(parsed.intent, "save_report", parsed)
                self.assertGreaterEqual(parsed.confidence, 0.7, parsed)
                self.assertEqual(parsed.kind, kind, parsed)
                self.assertEqual(parsed.slots["groups"], groups, parsed)
                self.assertEqual(parsed.slots["shape"], shape, parsed)
            arabic += any("؀" <= char <= "ۿ" for char in text)
        self.assertEqual(len(SENTENCES), 8)
        self.assertGreaterEqual(arabic, 4, "Half of the sentences are Arabic")

    def test_the_month_before_by_is_the_period_and_a_chart_without_save_is_still_a_chart(self):
        parsed = grammar.parse(SENTENCES[0][0], SCREEN)
        self.assertEqual(parsed.period, ("rel", "this_month"))
        self.assertEqual(grammar.parse("ارسم لي مبيعات هالشهر حسب المندوب", SCREEN).intent, "chart")

    def test_the_periods_move_with_the_day(self):
        env = self.env
        today = datetime.date(2026, 3, 15)
        expected = {
            "this_month": ("2026-03-01", "2026-04-01"), "last_month": ("2026-02-01", "2026-03-01"),
            "this_year": ("2026-01-01", "2027-01-01"), "last_year": ("2025-01-01", "2026-01-01"),
            "last_7_days": ("2026-03-09", "2026-03-16"), "last_30_days": ("2026-02-14", "2026-03-16"),
        }
        for preset, (start, end) in expected.items():
            with self.subTest(preset=preset):
                text = "[" + ", ".join(saved_reports.period_terms(preset, "date_order")) + "]"
                self.assertEqual(saved_reports.evaluate(env, text, today),
                                 [("date_order", ">=", start), ("date_order", "<", end)])


@tagged("post_install", "-at_install")
class TestSavedReports(NudgeCase, TransactionCase):
    """A report kept: its action, its menu, its tile, its rights."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        require_modules(cls, "sale")
        if "sale.order" not in cls.env:
            return
        cls.provider.active = False
        groups = "nudge.group_nudge_agent,sales_team.group_sale_manager"
        cls.raghad_user = new_test_user(cls.env, login="nudge_saved_raghad", name="Raghad", groups=groups)
        cls.ebrahim_user = new_test_user(cls.env, login="nudge_saved_ebrahim", name="Ebrahim", groups=groups)
        cls.reader = new_test_user(cls.env, login="nudge_saved_reader", groups="nudge.group_nudge_agent")
        product = cls.env["product.product"].create({"name": "Nudge Saved Item", "type": "service"})
        customer = cls.env["res.partner"].create({"name": "Nudge Saved Customer"})
        for user, amounts in ((cls.raghad_user, (100.0, 50.0)), (cls.ebrahim_user, (30.0,))):
            for amount in amounts:
                order = cls.env["sale.order"].sudo().create({
                    "partner_id": customer.id, "user_id": user.id, "client_order_ref": REF,
                    "order_line": [(0, 0, {"product_id": product.id, "product_uom_qty": 1, "price_unit": amount,
                                           "tax_id": [(5, 0, 0)]})],
                })
                order.action_confirm()

    def setUp(self):
        super().setUp()
        if "sale.order" not in self.env:
            self.skipTest("Sales is not installed")
        self.env["ir.config_parameter"].sudo().set_param("nudge.noai_first", "1")
        tools.home.clear_cache(self.env)

    def arguments(self, **given):
        return {
            "model": "sale.order", "conditions": [{"field": "client_order_ref", "operator": "=", "value": REF}],
            "group_by": ["user_id"], "measures": ["amount_total:sum"], "view": "bar", **given,
        }

    def keep(self, user=None, home=False, **given):
        return self.env["nudge.report"].with_user(user or self.raghad_user).create_from_definition(
            self.arguments(**given), home=home)

    def visible_menus(self, user):
        self.env.registry.clear_cache()
        return set(self.env["ir.ui.menu"].with_user(user)._visible_menu_ids())

    # ------------------------------------------------------------ the definition becomes an action

    def test_a_chart_becomes_a_graph_action_with_its_groups_and_measure(self):
        report = self.keep()
        action = report.action_id
        self.assertEqual(action.res_model, "sale.order")
        self.assertEqual(action.view_mode, "graph,list,form")
        context = saved_reports.evaluate(self.env, action.context)
        self.assertEqual(context["graph_mode"], "bar")
        self.assertEqual(context["graph_groupbys"], ["user_id"])
        self.assertEqual(context["graph_measure"], "amount_total")
        self.assertEqual(saved_reports.evaluate(self.env, action.domain),
                         [["client_order_ref", "=", REF]])
        self.assertEqual(report.user_id, self.raghad_user)
        self.assertEqual(report.company_id, self.raghad_user.company_id)
        self.assertEqual(action.create_uid, self.raghad_user, "The action is the person's, not the superuser's")

    def test_a_pivot_and_a_list(self):
        pivot = self.keep(view="pivot", group_by=["date_order:month", "user_id"])
        self.assertEqual(pivot.action_id.view_mode, "pivot,list,form")
        context = saved_reports.evaluate(self.env, pivot.action_id.context)
        self.assertEqual(context["pivot_row_groupby"], ["date_order:month"])
        self.assertEqual(context["pivot_column_groupby"], ["user_id"])
        self.assertEqual(context["pivot_measures"], ["amount_total"])
        plain = self.keep(view="list", group_by=[], measures=["count"])
        self.assertEqual(plain.action_id.view_mode, "list,form")

    def test_a_period_stays_an_expression_and_is_cut_on_the_date(self):
        report = self.keep(period="this_month")
        self.assertIn("context_today()", report.action_id.domain)
        today = datetime.date(2026, 5, 20)
        domain = saved_reports.evaluate(self.env, report.action_id.domain, today)
        self.assertIn(("date_order", ">=", "2026-05-01"), domain)
        self.assertIn(("date_order", "<", "2026-06-01"), domain)
        self.assertEqual(report.numbers()["total"], 180.0, "The orders made today are of this month")
        last = self.keep(period="last_month")
        self.assertEqual(last.numbers()["total"], 0.0, "None were made last month")

    def test_the_card_says_it_in_words_and_nothing_is_made_before_yes(self):
        before = self.env["nudge.report"].with_context(active_test=False).search_count([])
        result = tools.base.call(self.env(user=self.raghad_user), "save_report", self.arguments(
            home=True, period="last_month"), mode="agent")
        self.assertFalse(result.is_error, result.content)
        lines = {line["label"]: line["value"] for line in result.pending["preview"]["lines"]}
        self.assertIn("client_order_ref", lines["Only"])
        self.assertIn("last month", lines["Only"])
        self.assertEqual(lines["Shown as"], "a bar chart")
        self.assertIn("the total of", lines["Shows"])
        self.assertIn("My Reports", lines["Kept in"])
        self.assertEqual(lines["On my home page"], "Yes")
        self.assertEqual(self.env["nudge.report"].with_context(active_test=False).search_count([]), before)

    # ------------------------------------------------------------ whose menu, whose report

    def test_one_menu_lists_the_reports_of_the_person_and_makes_no_menu_per_report(self):
        menus = self.env["ir.ui.menu"].search_count([])
        report = self.keep()
        self.assertEqual(self.env["ir.ui.menu"].search_count([]), menus, "A report adds no menu")
        self.assertNotIn("menu_id", self.env["nudge.report"]._fields)
        self.assertNotIn("nudge_user_id", self.env["ir.ui.menu"]._fields)
        my_reports = self.env.ref("nudge.nudge_menu_my_reports")
        self.assertEqual(my_reports.action, self.env.ref("nudge.nudge_report_action"))
        for user in (self.raghad_user, self.ebrahim_user):
            self.assertIn(my_reports.id, self.visible_menus(user), "Everyone sees the one menu")
        self.assertEqual(report.action_id.res_model, "sale.order")
        self.assertEqual(self.env.ref("nudge.nudge_report_action").res_model, "nudge.report")
        self.assertIn(report, self.env["nudge.report"].with_user(self.raghad_user).search([]))
        self.assertFalse(self.env["nudge.report"].with_user(self.ebrahim_user).search([("id", "=", report.id)]))
        opened = report.with_user(self.raghad_user).action_open_report()
        self.assertEqual(opened["res_model"], "sale.order")

    def test_the_migration_removes_the_menus_made_so_far(self):
        import importlib.util
        import os
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations", "18.0.1.57.25", "pre-migrate.py")
        spec = importlib.util.spec_from_file_location("nudge_pre_migrate_57_25", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cr = self.env.cr
        cr.execute("ALTER TABLE ir_ui_menu ADD COLUMN nudge_user_id integer")
        cr.execute("ALTER TABLE nudge_report ADD COLUMN IF NOT EXISTS menu_id integer")
        parent = self.env.ref("nudge.nudge_menu_my_reports")
        menu = self.env["ir.ui.menu"].create({"name": "Raghad's old report", "parent_id": parent.id})
        cr.execute("UPDATE ir_ui_menu SET nudge_user_id = %s WHERE id = %s", [self.raghad_user.id, menu.id])
        rule = self.env["ir.rule"].create({
            "name": "Old report menu rule", "model_id": self.env.ref("base.model_ir_ui_menu").id,
            "domain_force": "[(1, '=', 1)]"})
        cr.execute("""
            INSERT INTO ir_model_data (module, name, model, res_id, noupdate)
            VALUES ('nudge', 'nudge_report_menu_rule_owner', 'ir.rule', %s, true)""", [rule.id])
        module.migrate(cr, "18.0.1.57.22")
        cr.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'ir_ui_menu' AND column_name = 'nudge_user_id'")
        self.assertFalse(cr.fetchone(), "The column is gone")
        cr.execute("SELECT 1 FROM ir_ui_menu WHERE id = %s", [menu.id])
        self.assertFalse(cr.fetchone(), "The menu of a report is gone")
        cr.execute("SELECT 1 FROM ir_model_data WHERE module = 'nudge' AND name = 'nudge_report_menu_rule_owner'")
        self.assertFalse(cr.fetchone())
        cr.execute("SELECT 1 FROM ir_rule WHERE id = %s", [rule.id])
        self.assertFalse(cr.fetchone(), "The rule that hid the menus is gone")
        self.env.invalidate_all()

    def test_a_manager_reads_the_reports_of_others_but_does_not_change_them(self):
        report = self.keep()
        manager = new_test_user(self.env, login="report_manager_r2", groups="base.group_user,nudge.group_nudge_manager")
        self.assertIn(report, self.env["nudge.report"].with_user(manager).search([]))
        with self.assertRaises(AccessError):
            report.with_user(manager).name = "Changed by someone else"

    def test_rename_and_archive_never_delete(self):
        report = self.keep()
        report.with_user(self.raghad_user).name = "Raghad's sales by rep"
        self.assertEqual(report.action_id.name, "Raghad's sales by rep")
        report.with_user(self.raghad_user).active = False
        self.assertFalse(self.env["nudge.report"].with_user(self.raghad_user).search([("id", "=", report.id)]))
        report.with_user(self.raghad_user).active = True
        self.assertTrue(self.env["nudge.report"].with_user(self.raghad_user).search([("id", "=", report.id)]))
        with self.assertRaises(UserError):
            report.with_user(self.raghad_user).write({"definition": "{}"})
        with self.assertRaises(Exception):
            report.with_user(self.raghad_user).unlink()
        with self.assertRaises(UserError):
            report.sudo().unlink()

    # ------------------------------------------------------------ what a caller may not send

    def test_a_caller_cannot_name_the_menu_or_the_action_of_a_report(self):
        menu = self.env.ref("nudge.nudge_menu_my_reports")
        action = self.env["ir.actions.act_window"].sudo().search([], limit=1)
        Report = self.env["nudge.report"].with_user(self.reader)
        for vals in ({"menu_id": menu.id}, {"action_id": action.id}, {"definition": "{}"},
                     {"model_name": "res.partner"}, {"shape": "bar"}):
            with self.subTest(vals=vals), self.assertRaises(UserError):
                Report.create(dict(vals, name="Forged"))
        forged = Report.create({"name": "Mine"})
        with self.assertRaises(UserError):
            forged.write({"menu_id": menu.id})
        self.assertTrue(menu.active)
        self.assertEqual(forged.user_id, self.reader)

    def test_rename_and_archive_touch_only_the_action_the_report_made(self):
        report = self.keep()
        menu = self.env.ref("nudge.nudge_menu_my_reports")
        before = menu.name
        report.with_user(self.raghad_user).write({"name": "Renamed", "active": False})
        self.assertEqual(report.action_id.name, "Renamed")
        self.assertEqual(menu.name, before, "the one My Reports menu is nobody's report")
        self.assertTrue(menu.active)

    # ------------------------------------------------------------ the home page

    def test_a_second_report_of_the_same_name_gets_a_number(self):
        first, second, third = self.keep(), self.keep(), self.keep()
        self.assertEqual([first.name, second.name, third.name][1:], [f"{first.name} (2)", f"{first.name} (3)"])
        other = self.keep(user=self.ebrahim_user)
        self.assertEqual(other.name, first.name, "the names of another person do not count")

    def test_a_dated_report_shows_the_newest_periods_oldest_first(self):
        report = self.keep(view="line", group_by=["date_order:month"])
        data = saved_reports.numbers(self.env(user=self.raghad_user), json.loads(report.definition),
                                     report.action_id.domain, limit=1)
        self.assertEqual(len(data["labels"]), 1)
        rows = saved_reports.numbers(self.env(user=self.raghad_user), json.loads(report.definition),
                                     report.action_id.domain)
        self.assertEqual(data["labels"][-1], rows["labels"][-1], "the last period is the newest, not the first")

    def test_a_report_too_big_to_count_is_a_tile_that_says_so(self):
        report = self.keep(home=True)
        self.patch(type(self.env["nudge.report"]), "numbers", lambda model: (_ for _ in ()).throw(TooBig("big")))
        tiles = tools.home.report_tiles(self.env(user=self.raghad_user))
        tile = [tile for section in tiles for tile in section["tiles"] if tile["report_id"] == report.id][0]
        self.assertIn("Too big", tile["count"])

    def test_the_figures_are_read_under_a_clock(self):
        report = self.keep()
        seen = []
        original = analytics.grouped

        def spy(env, model, domain, groups, measures, limit=None, order=None, seconds=analytics.AGGREGATE_SECONDS):
            seen.append(seconds)
            return original(env, model, domain, groups, measures, limit, order, seconds)

        self.patch(saved_reports, "grouped", spy)
        report.with_user(self.raghad_user).numbers()
        self.assertTrue(seen and set(seen) == {analytics.AGGREGATE_SECONDS})

    def test_the_tile_shows_the_numbers_of_the_saved_report(self):
        report = self.keep(home=True)
        self.keep(name="Not on the home page")
        payload = tools.home.payload(self.env(user=self.raghad_user), force=True)
        section = next(item for item in payload["sections"] if item["key"] == "reports")
        self.assertEqual([tile["key"] for tile in section["tiles"]], [f"reports.{report.id}"])
        tile = section["tiles"][0]
        currency = self.raghad_user.company_id.currency_id
        self.assertEqual(tile["count"], currency.format(180.0))
        self.assertCountEqual(tile["series"], [150.0, 30.0])
        self.assertEqual(tile["kind"], "report")
        action = self.env["nudge.home"].with_user(self.raghad_user).show_tile(tile["key"])
        self.assertEqual(action["res_model"], "sale.order")
        # Another person's page does not hold it
        other = tools.home.payload(self.env(user=self.ebrahim_user), force=True)
        self.assertFalse([item for item in other["sections"] if item["key"] == "reports"])
        with self.assertRaises(UserError):
            self.env["nudge.home"].with_user(self.ebrahim_user).show_tile(tile["key"])

    # ------------------------------------------------------------ rights

    def test_a_person_who_may_not_read_the_orders_gets_a_refusal(self):
        result = tools.base.call(self.env(user=self.reader), "save_report", self.arguments(), mode="agent")
        self.assertTrue(result.is_error, result.content)
        self.assertIsNone(result.pending)
        with self.assertRaises(UserError):
            self.keep(user=self.reader)
        self.assertFalse(self.env["nudge.report"].with_user(self.reader).search([]))

    def test_a_field_that_does_not_exist_and_a_shape_that_does_not_fit_are_refused(self):
        for given in ({"group_by": ["no_such_field"]}, {"view": "pie", "group_by": []}, {"period": "next_week"},
                      {"measures": ["name:sum"]}):
            with self.subTest(given=given):
                result = tools.base.call(self.env(user=self.raghad_user), "save_report", self.arguments(**given),
                                         mode="agent")
                self.assertTrue(result.is_error, result.content)

    def test_the_action_is_checked_by_odoo_when_it_is_opened(self):
        report = self.keep()
        env = self.env(user=self.reader)
        with self.assertRaises(Exception):
            env["sale.order"].search(saved_reports.evaluate(env, report.action_id.domain))

    # ------------------------------------------------------------ the way the person asks

    def test_the_tool_after_a_yes_keeps_the_report(self):
        self.env["ir.config_parameter"].sudo().set_param("nudge.noai_first", "0")
        self.provider.active = True
        self.use_adapter(script=[
            asking(call("save_report", self.arguments(home=True))),
            saying("Kept."),
        ])
        conversation, _events = self.order(self.raghad_user, "Please file the revenue breakdown for my team")
        self.assertFalse(self.env["nudge.report"].search([("user_id", "=", self.raghad_user.id)]))
        events = self.decide(conversation)
        report = self.env["nudge.report"].search([("user_id", "=", self.raghad_user.id)])
        self.assertEqual(len(report), 1)
        self.assertTrue(report.home)
        self.assertTrue([event for event in events if event.get("type") == "open_action"], events)

    def test_the_arabic_sentence_keeps_the_report_through_the_same_path(self):
        conversation, _events = self.order(self.raghad_user, SENTENCES[0][0])
        step = conversation.sudo().waiting_step_id
        self.assertEqual(step.res_model, "nudge.report", "A card waits for a yes")
        self.assertFalse(self.env["nudge.report"].search([("user_id", "=", self.raghad_user.id)]))
        self.decide(conversation)
        report = self.env["nudge.report"].search([("user_id", "=", self.raghad_user.id)])
        definition = json.loads(report.definition)
        self.assertEqual(definition["model"], "sale.order")
        self.assertEqual(definition["group_by"], ["user_id"])
        self.assertEqual(definition["period"], "this_month")
        self.assertEqual(definition["view"], "bar")
        self.assertIn("graph", report.action_id.view_mode)

    def test_the_english_pivot_sentence(self):
        conversation, _events = self.order(
            self.raghad_user, "save this as a pivot of sales by month and salesperson")
        self.assertEqual(conversation.sudo().waiting_step_id.res_model, "nudge.report")
        self.decide(conversation)
        report = self.env["nudge.report"].search([("user_id", "=", self.raghad_user.id)])
        self.assertEqual(report.action_id.view_mode, "pivot,list,form")

    def test_a_spreadsheet_with_the_figures_where_the_companion_is_installed(self):
        if "nudge.spreadsheet" not in self.env:
            self.skipTest("The spreadsheet companion is not installed")
        report = self.keep()
        action = report.with_user(self.raghad_user).action_spreadsheet()
        self.assertEqual(action["tag"], "nudge_spreadsheet.open")
        sheet = self.env["nudge.spreadsheet"].browse(action["params"]["spreadsheet_id"])
        self.assertEqual(sheet.user_id, self.raghad_user)
        self.assertIn("Raghad", sheet.spreadsheet_data)

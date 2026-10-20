import json

from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.tools.noai_commands import grammar

from .common import NudgeCase, require_modules

SCREEN = {"model": "sale.order", "record_id": None}

# The sentences that draw a chart, in Arabic (half or more) and in English:
# sentence, the kind of record, what it is split by, the type of chart said
SENTENCES = [
    ("ارسم لي مبيعات هالشهر حسب المندوب", "sales", "by_user", None),
    ("رسم بياني للفواتير حسب العميل", "invoice", "by_partner", None),
    ("ارسملي شارت للمبيعات شهريا", "sales", "by_month", None),
    ("مخطط دائري للمبيعات حسب المنتج", "sales", "by_product", "pie"),
    ("اعملي شارت للفواتير المتأخرة حسب الزبون", "invoice", "by_partner", None),
    ("منحنى المبيعات هالسنة", "sales", None, "line"),
    ("chart of sales by salesperson this year", "sales", "by_user", None),
    ("draw me a pie chart of invoices per customer", "invoice", "by_partner", "pie"),
    ("irsemli mabi3at 7asab almandoub", "sales", "by_user", None),
    ("graph orders per month", "sales_order", "by_month", None),
]


@tagged("post_install", "-at_install")
class TestChartParser(TransactionCase):
    """The words that ask for a chart, in every dialect: no database, no AI."""

    def test_the_sentences_that_draw_a_chart(self):
        arabic = 0
        for text, kind, group, chart_type in SENTENCES:
            with self.subTest(text=text):
                parsed = grammar.parse(text, SCREEN)
                self.assertEqual(parsed.intent, "chart", parsed)
                self.assertGreaterEqual(parsed.confidence, 0.7, parsed)
                self.assertEqual(parsed.kind, kind, parsed)
                self.assertEqual(parsed.slots["group"], group, parsed)
                self.assertEqual(parsed.slots["chart_type"], chart_type, parsed)
            arabic += any("؀" <= char <= "ۿ" for char in text)
        self.assertEqual(len(SENTENCES), 10)
        self.assertGreaterEqual(arabic, 5, "Half of the sentences are Arabic")

    def test_every_dialect_has_a_word_to_draw(self):
        for group in ("msa", "gulf", "egyptian", "iraqi", "levantine", "maghrebi", "sudanese_yemeni", "english",
                      "arabizi"):
            with self.subTest(group=group):
                self.assertTrue(
                    any("chart" in grammar.lexicon.PHRASES[key] for key in grammar.lexicon.GROUPS[group]
                        if key in grammar.lexicon.PHRASES))

    def test_a_count_or_a_total_is_still_not_a_chart(self):
        for text in ("كم عدد الفواتير", "مجموع المبيعات هذا الشهر", "how many overdue invoices"):
            with self.subTest(text=text):
                self.assertEqual(grammar.parse(text, SCREEN).intent, "aggregate")


@tagged("post_install", "-at_install")
class TestChart(NudgeCase, TransactionCase):
    """A chart in the chat: the payload of the tool, the rights, the words."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        require_modules(cls, "sale")
        if "sale.order" not in cls.env:
            return
        cls.provider.active = False
        groups = "nudge.group_nudge_agent,sales_team.group_sale_manager"
        cls.raghad_user = new_test_user(cls.env, login="nudge_chart_raghad", name="Raghad", groups=groups)
        cls.ebrahim_user = new_test_user(cls.env, login="nudge_chart_ebrahim", name="Ebrahim", groups=groups)
        cls.reader = new_test_user(cls.env, login="nudge_chart_reader", groups="nudge.group_nudge_agent")
        product = cls.env["product.product"].create({"name": "Nudge Chart Item", "type": "service"})
        customer = cls.env["res.partner"].create({"name": "Nudge Chart Customer"})
        for user, amounts in ((cls.raghad_user, (100.0, 50.0)), (cls.ebrahim_user, (30.0,))):
            for amount in amounts:
                order = cls.env["sale.order"].sudo().create({
                    "partner_id": customer.id, "user_id": user.id, "client_order_ref": "NudgeChart",
                    "order_line": [(0, 0, {"product_id": product.id, "product_uom_qty": 1, "price_unit": amount,
                                           "tax_id": [(5, 0, 0)]})],
                })
                order.action_confirm()

    def setUp(self):
        super().setUp()
        if "sale.order" not in self.env:
            self.skipTest("Sales is not installed")
        self.env["ir.config_parameter"].sudo().set_param("nudge.noai_first", "1")

    def chart(self, user, **arguments):
        arguments = {
            "model": "sale.order",
            "conditions": [{"field": "client_order_ref", "operator": "=", "value": "NudgeChart"}],
            "group_by": ["user_id"], "measure": "amount_total:sum", **arguments,
        }
        return tools.base.call(self.env(user=user), "show_chart", arguments, mode="agent")

    def test_the_payload_has_the_labels_the_series_and_the_currency(self):
        result = self.chart(self.raghad_user)
        self.assertFalse(result.is_error, result.content)
        event = result.event
        self.assertEqual(event["type"], "chart")
        self.assertEqual(event["chart_type"], "bar")
        self.assertEqual(event["labels"], ["Raghad", "Ebrahim"], "Largest first")
        self.assertEqual(event["datasets"], [{"label": "Total", "data": [150.0, 30.0]}])
        currency = self.raghad_user.company_id.currency_id
        self.assertEqual(event["currency"], {
            "symbol": currency.symbol, "position": currency.position, "digits": currency.decimal_places,
            "name": currency.name})
        self.assertEqual(event["model"], "sale.order")

    def test_a_count_has_no_currency(self):
        result = self.chart(self.raghad_user, measure="count")
        self.assertEqual(result.event["datasets"][0]["data"], [2, 1])
        self.assertIsNone(result.event["currency"])

    def test_a_date_group_is_a_line_in_the_order_of_time(self):
        result = self.chart(self.raghad_user, group_by=["date_order:month"])
        event = result.event
        self.assertEqual(event["chart_type"], "line")
        self.assertEqual(len(event["labels"]), 1)
        self.assertEqual(event["datasets"][0]["data"], [180.0])

    def test_a_second_group_makes_the_series(self):
        result = self.chart(self.raghad_user, group_by=["date_order:month", "user_id"])
        series = {dataset["label"]: dataset["data"] for dataset in result.event["datasets"]}
        self.assertEqual(series, {"Raghad": [150.0], "Ebrahim": [30.0]})

    def test_a_pie_of_shares_stays_a_pie_and_a_pie_of_series_becomes_bars(self):
        self.assertEqual(self.chart(self.raghad_user, chart_type="pie").event["chart_type"], "pie")
        many = self.chart(self.raghad_user, chart_type="pie", group_by=["date_order:month", "user_id"])
        self.assertEqual(many.event["chart_type"], "bar")

    def test_open_as_report_is_the_graph_with_the_same_domain_and_groups(self):
        action = self.chart(self.raghad_user).event["action"]
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "sale.order")
        self.assertEqual(action["views"][0], [False, "graph"])
        self.assertEqual(action["view_mode"].split(",")[0], "graph")
        self.assertIn(["client_order_ref", "=", "NudgeChart"], action["domain"])
        self.assertEqual(action["context"]["graph_groupbys"], ["user_id"])
        self.assertEqual(action["context"]["graph_measure"], "amount_total")
        records = self.env["sale.order"].with_user(self.raghad_user).search(action["domain"])
        self.assertEqual(len(records), 3, "The report opens the records the chart was drawn from")
        json.dumps(action)

    def test_a_person_without_access_to_the_model_gets_the_usual_refusal(self):
        result = self.chart(self.reader)
        self.assertTrue(result.is_error)
        self.assertIsNone(result.event)
        aggregate = tools.base.call(self.env(user=self.reader), "aggregate_records", {
            "model": "sale.order", "group_by": ["user_id"]}, mode="agent")
        self.assertEqual(result.content, aggregate.content, "The same words as the aggregate")

    def test_the_tool_is_for_those_who_may_ask_for_work(self):
        talker = new_test_user(self.env, login="nudge_chart_talk", groups="nudge.group_nudge_user,sales_team.group_sale_manager")
        names = [tool.name for tool in tools.base.available(self.env(user=talker))]
        self.assertNotIn("show_chart", names)
        result = self.chart(talker)
        self.assertTrue(result.is_error)
        agent = [tool.name for tool in tools.base.available(self.env(user=self.raghad_user), mode="agent")]
        self.assertIn("show_chart", agent)

    def test_nothing_to_draw_is_said(self):
        result = self.chart(self.raghad_user, conditions=[
            {"field": "client_order_ref", "operator": "=", "value": "NoSuchReference"}])
        self.assertTrue(result.is_error)
        self.assertIsNone(result.event)

    def test_the_arabic_sentence_draws_through_the_same_aggregate(self):
        _conversation, events = self.order(self.raghad_user, "ارسم لي مبيعات هالشهر حسب المندوب")
        charts = [event for event in events if event["type"] == "chart"]
        self.assertEqual(len(charts), 1, events)
        chart = charts[0]
        self.assertEqual(chart["model"], "sale.order")
        self.assertIn("Raghad", chart["labels"])
        self.assertIn("Ebrahim", chart["labels"])
        self.assertEqual(chart["chart_type"], "bar")
        self.assertTrue(chart["action"])
        self.assertTrue(chart["currency"])
        # The same figures as the aggregate, as the person
        total = sum(chart["datasets"][0]["data"])
        expected = sum(self.env["sale.order"].with_user(self.raghad_user).search(
            chart["action"]["domain"]).mapped("amount_total"))
        self.assertAlmostEqual(total, expected)
        message = self.env["nudge.message"].search([("role", "=", "assistant")], order="id desc", limit=1)
        self.assertEqual([event["type"] for event in message.screen_events if event["type"] == "chart"], ["chart"],
                         "The chart is kept with the message")

    def test_the_english_sentence_with_a_type(self):
        _conversation, events = self.order(self.raghad_user, "draw a pie chart of sales per salesperson")
        charts = [event for event in events if event["type"] == "chart"]
        self.assertEqual(len(charts), 1, events)
        self.assertEqual(charts[0]["chart_type"], "pie")

    def test_a_person_who_may_not_read_the_orders_is_drawn_nothing(self):
        _conversation, events = self.order(self.reader, "ارسم لي مبيعات هالشهر حسب المندوب")
        self.assertFalse([event for event in events if event["type"] == "chart"])

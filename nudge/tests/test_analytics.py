import json

from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge import tools

from .common import NudgeCase


@tagged("post_install", "-at_install")
class TestAnalytics(NudgeCase, TransactionCase):
    """Counting and summing, as the person."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.jordan = cls.env.ref("base.jo")
        cls.emirates = cls.env.ref("base.ae")
        cls.partners = cls.env["res.partner"].create([
            {"name": "Nudge Agg One", "country_id": cls.jordan.id, "partner_latitude": 1.0},
            {"name": "Nudge Agg Two", "country_id": cls.jordan.id, "partner_latitude": 2.0},
            {"name": "Nudge Agg Three", "country_id": cls.jordan.id, "partner_latitude": 3.0},
            {"name": "Nudge Agg Four", "country_id": cls.emirates.id, "partner_latitude": 10.0},
            {"name": "Nudge Agg Five", "country_id": cls.emirates.id, "partner_latitude": 20.0},
        ])

    def aggregate(self, arguments, user=None):
        return tools.base.call(
            self.env(user=user or self.agent), "aggregate_records",
            {
                "model": "res.partner",
                "conditions": [{"field": "name", "operator": "contains", "value": "Nudge Agg"}],
                **arguments,
            },
            mode="agent")

    def rows(self, arguments):
        result = self.aggregate(arguments)
        self.assertFalse(result.is_error, result.content)
        return json.loads(result.content)

    def test_counts_per_group(self):
        result = self.rows({"group_by": ["country_id"]})
        self.assertEqual(result["measures"], ["count"])
        self.assertEqual(
            [(row["country_id"]["name"], row["count"]) for row in result["rows"]],
            [("Jordan", 3), ("United Arab Emirates", 2)],
            "Largest first")

    def test_sums_and_averages(self):
        result = self.rows({
            "group_by": ["country_id"],
            "measures": ["partner_latitude:sum", "partner_latitude:avg"],
            "order": "partner_latitude:sum desc",
        })
        first = result["rows"][0]
        self.assertEqual(first["country_id"]["name"], "United Arab Emirates")
        self.assertEqual(first["partner_latitude:sum"], 30.0)
        self.assertEqual(first["partner_latitude:avg"], 15.0)
        self.assertEqual(first["count"], 2, "The count always comes along")
        self.assertEqual(result["rows"][1]["partner_latitude:sum"], 6.0)

    def test_totals_without_groups(self):
        result = self.rows({"measures": ["partner_latitude:sum"]})
        self.assertEqual(result["rows"], [{"partner_latitude:sum": 36.0, "count": 5}])

    def test_dates_take_an_interval(self):
        result = self.rows({"group_by": ["create_date:month"]})
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rows"][0]["count"], 5)
        self.assertTrue(result["rows"][0]["create_date:month"])
        result = self.rows({"group_by": ["create_date"]})
        self.assertEqual(result["group_by"], ["create_date:day"], "A date without an interval is by day")

    def test_the_limit_and_the_order(self):
        result = self.rows({"group_by": ["name"], "limit": 2, "order": "name asc"})
        self.assertEqual([row["name"] for row in result["rows"]], ["Nudge Agg Five", "Nudge Agg Four"])
        self.assertIn("first 2 groups", result["note"])

    def test_what_is_refused(self):
        for arguments, text in [
            ({"measures": ["name:sum"]}, "not a number"),
            ({"group_by": ["name:month"]}, "not a date"),
            ({"group_by": ["nowhere"]}, "no field"),
            ({"group_by": ["country_id"], "order": "email asc"}, "order must name"),
        ]:
            result = self.aggregate(arguments)
            self.assertTrue(result.is_error, arguments)
            self.assertIn(text, result.content)
        closed = tools.base.call(
            self.env(user=self.agent), "aggregate_records", {"model": "ir.config_parameter"},
            mode="agent")
        self.assertTrue(closed.is_error)
        self.assertIn("no model", closed.content)

    def test_only_the_agent_in_agent_mode(self):
        env = self.env(user=self.agent)
        self.assertIn("aggregate_records", [tool.name for tool in tools.base.available(env, "agent")])
        self.assertNotIn("aggregate_records", [tool.name for tool in tools.base.available(env, "talk")])
        self.assertNotIn(
            "aggregate_records",
            [tool.name for tool in tools.base.available(self.env(user=self.user_a), "agent")],
            "Not for someone outside the Agent group")
        refused = tools.base.call(self.env(user=self.user_a), "aggregate_records",
                                  {"model": "res.partner"}, mode="agent")
        self.assertTrue(refused.is_error)

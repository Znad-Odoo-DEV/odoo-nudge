from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..tools import home
from .common import require_modules


@tagged("post_install", "-at_install")
class TestHome(TransactionCase):
    """My Nudge Home: tiles counted as the person, a layout they keep, no AI."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        require_modules(cls, "purchase", "sale")
        base = "nudge.group_nudge_user"
        cls.plain = new_test_user(cls.env, login="home_plain", groups="base.group_user")
        cls.seller = new_test_user(cls.env, login="home_seller", groups=base + ",sales_team.group_sale_salesman")
        cls.buyer = new_test_user(cls.env, login="home_buyer", groups=base + ",purchase.group_purchase_user")
        cls.both = new_test_user(
            cls.env, login="home_both",
            groups=base + ",sales_team.group_sale_manager,purchase.group_purchase_manager")
        home.clear_cache(cls.env)

    def sections(self, user):
        return {s["key"]: s for s in self.env["nudge.home"].with_user(user).get_home(force=True)["sections"]}

    def test_tiles_follow_the_groups_of_the_person(self):
        self.assertNotIn("sales", self.sections(self.plain))
        self.assertNotIn("purchase", self.sections(self.plain))
        seller = self.sections(self.seller)
        self.assertIn("sales", seller)
        self.assertNotIn("purchase", seller)
        buyer = self.sections(self.buyer)
        self.assertIn("purchase", buyer)
        self.assertNotIn("sales", buyer)
        both = self.sections(self.both)
        self.assertTrue({"sales", "purchase"} <= set(both))
        self.assertNotIn("overview", both, "The home page has sections, not the digest's overview")

    def test_numbers_are_counted_as_the_person_without_zero_tiles_lost(self):
        partner = self.env["res.partner"].create({"name": "Raghad Trading"})
        mine = self.env["sale.order"].with_user(self.seller).create({"partner_id": partner.id})
        theirs = self.env["sale.order"].with_user(self.both).create({"partner_id": partner.id})
        self.assertTrue(mine and theirs)
        tiles = {t["key"]: t for t in self.sections(self.seller)["sales"]["tiles"]}
        quotations = tiles["sales.quotations"]
        expected = self.env["sale.order"].with_user(self.seller).search_count([("state", "in", ["draft", "sent"])])
        self.assertEqual(quotations["count"], expected)
        self.assertEqual(len(quotations["series"]), 7)
        self.assertEqual(quotations["model"], "sale.order")
        # a tile at zero is still a tile
        self.assertIn("sales.new_leads", tiles)
        self.assertEqual(tiles["sales.new_leads"]["count"], 0)

    def test_a_plain_user_gets_no_error_and_no_alien_numbers(self):
        data = self.env["nudge.home"].with_user(self.plain).get_home()
        for section in data["sections"]:
            for tile in section["tiles"]:
                self.assertTrue(self.env[tile["model"]].with_user(self.plain)._nudge_has_access("read"))

    def test_a_portal_user_cannot_call_the_home_methods(self):
        portal = new_test_user(self.env, login="portal_ebrahim_home", groups="base.group_portal")
        Home = self.env["nudge.home"].with_user(portal)
        for call in (lambda: Home.get_home(), lambda: Home.save_layout({}), lambda: Home.set_login_home(True),
                     lambda: Home.show_tile("reports.1")):
            with self.assertRaises(AccessError):
                call()

    def test_the_numbers_are_kept_five_minutes_per_person(self):
        Home = self.env["nudge.home"].with_user(self.seller)
        partner = self.env["res.partner"].create({"name": "Ebrahim Trading"})
        first = Home.get_home(force=True)
        count = {t["key"]: t["count"] for s in first["sections"] for t in s["tiles"]}["sales.quotations"]
        self.env["sale.order"].with_user(self.seller).create({"partner_id": partner.id})
        cached = {t["key"]: t["count"] for s in Home.get_home()["sections"] for t in s["tiles"]}
        self.assertEqual(cached["sales.quotations"], count, "Within five minutes the answer is the one kept")
        fresh = {t["key"]: t["count"] for s in Home.get_home(force=True)["sections"] for t in s["tiles"]}
        self.assertEqual(fresh["sales.quotations"], count + 1)

    def test_the_layout_is_kept_by_the_person_and_cleaned(self):
        Home = self.env["nudge.home"].with_user(self.seller)
        saved = Home.save_layout({"order": ["sales.quotations", "sales.quotations", 5, "x" * 200],
                                  "hidden": ["sales.new_leads"]})
        self.assertEqual(saved, {"order": ["sales.quotations"], "hidden": ["sales.new_leads"]})
        self.assertEqual(Home.get_home()["layout"], saved)
        self.assertFalse(self.env["nudge.home"].with_user(self.both).get_home()["layout"]["order"])
        # nobody else writes it
        with self.assertRaises(AccessError):
            self.seller.with_user(self.buyer).write({"nudge_home_layout": {"order": ["a"]}})

    def test_open_at_login_is_the_persons_own_choice(self):
        Home = self.env["nudge.home"].with_user(self.seller)
        action = self.env.ref("nudge.nudge_home_action")
        self.assertTrue(Home.set_login_home(True))
        self.assertEqual(self.seller.action_id.id, action.id)
        self.assertFalse(Home.set_login_home(False))
        self.assertFalse(self.seller.action_id)
        self.assertNotEqual(self.buyer.action_id.id, action.id)

    def test_show_me_gives_the_list_behind_the_tile_as_the_person(self):
        Home = self.env["nudge.home"].with_user(self.seller)
        window = Home.show_tile("sales.quotations")
        self.assertEqual(window["type"], "ir.actions.act_window")
        self.assertEqual(window["res_model"], "sale.order")
        self.assertEqual(window["domain"], [["state", "in", ["draft", "sent"]]])
        with self.assertRaises(UserError):
            Home.show_tile("purchase.vendor_bills")
        with self.assertRaises(UserError):
            self.env["nudge.home"].with_user(self.plain).show_tile("sales.quotations")

    def test_nudge_says_pending_approvals_of_the_person_only(self):
        partner = self.env["res.partner"].create({"name": "Raghad"})
        self.env["mail.activity"].create({
            "res_model_id": self.env["ir.model"]._get_id("res.partner"), "res_id": partner.id,
            "user_id": self.seller.id, "summary": f"{home.APPROVAL_TAG} Raghad above 50,000"})
        says = self.env["nudge.home"].with_user(self.seller).get_home(force=True)["says"]
        self.assertEqual([s["kind"] for s in says if s["kind"] == "approval"], ["approval"])
        self.assertEqual([s["kind"] for s in self.env["nudge.home"].with_user(self.buyer).get_home(force=True)["says"]
                          if s["kind"] == "approval"], [])

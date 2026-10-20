import json
import re
from types import SimpleNamespace
from unittest.mock import patch

from unittest import SkipTest

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge.tools import base
from odoo.addons.nudge.tools.records import Refused

from ..tools import shop

TEMPLATE = {"shop": {
    "categories": [
        {"name": {"en": "Tiles", "ar": "بلاط"}, "keywords": ["tile"], "children": [
            {"name": {"en": "Floor Tiles", "ar": "بلاط الأرضيات"}, "keywords": ["floor"]}]},
        {"name": {"en": "Paint"}, "keywords": ["paint"]}],
    "banners": [{"title": {"en": "Raghad Sale"}, "text": {"en": "Tiles for less"}, "button": {"en": "Go"}}] * 3,
    "layout": {"shop_ppg": 24, "shop_ppr": 4},
}}


def fake_ask(calls):
    def ask(system, text):
        calls.append(text)
        ids = re.findall(r'"id": (\d+)', text)
        return json.dumps({i: "A good product number %s." % i for i in ids})
    return ask


@tagged("post_install", "-at_install")
class TestShop(TransactionCase):
    """The shop of a built website: categories, descriptions, layout, banners, readiness"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for model in ("product.public.category", "payment.provider", "delivery.carrier"):
            if model not in cls.env:
                raise SkipTest("%s is not installed" % model)
        cls.website = cls.env["website"].search([], limit=1)
        cls.designer = new_test_user(cls.env, login="raghad_shop", groups="base.group_user,website.group_website_designer,product.group_product_manager,base.group_system")
        cls.plain = new_test_user(cls.env, login="ebrahim_shop", groups="base.group_user")
        Product = cls.env["product.template"]
        cls.tile = Product.create({"name": "Raghad Floor Tile 60x60", "sale_ok": True, "is_published": True})
        cls.paint = Product.create({"name": "Ebrahim White Paint", "sale_ok": True, "is_published": True,
                                    "description_ecommerce": "<p>Typed by a person.</p>"})
        cls.field = shop.description_field(cls.env)

    def setUp(self):
        super().setUp()
        self.env_d = self.env(user=self.designer)
        self.site = self.website.with_env(self.env_d)

    def test_categories_from_the_template_and_products_placed(self):
        plan = shop.plan_shop(self.env_d, self.site, TEMPLATE)
        self.assertEqual(plan["source"], "template")
        paths = [c["path"] for c in plan["categories"]]
        self.assertEqual(paths[:3], [["Tiles"], ["Tiles", "Floor Tiles"], ["Paint"]])
        self.assertEqual(plan["layout"].get("shop_ppg"), 24)
        result = shop.apply_shop(self.env_d, self.site, plan, ask=fake_ask([]))
        Category = self.env["product.public.category"]
        tiles = Category.search([("name", "=", "Tiles")])
        child = Category.search([("name", "=", "Floor Tiles")])
        self.assertEqual(child.parent_id, tiles)
        self.assertEqual(self.tile.public_categ_ids, child, "the deepest matching category wins")
        self.assertEqual(self.paint.public_categ_ids.name, "Paint")
        self.assertEqual(self.website.shop_ppg, 24)
        self.assertTrue(result["made"])
        # a second plan finds nothing left to create: nothing is duplicated
        again = shop.plan_shop(self.env_d, self.site, TEMPLATE)
        self.assertFalse(again["categories"])
        self.assertFalse(again["assign"])

    def test_categories_from_the_product_categories(self):
        category = self.env["product.category"].create({"name": "Raghad Lighting"})
        self.tile.categ_id = category
        plan = shop.plan_shop(self.env_d, self.site, None)
        self.assertEqual(plan["source"], "products")
        self.assertIn(["Raghad Lighting"], [c["path"] for c in plan["categories"]])
        shop.apply_shop(self.env_d, self.site, plan, ask=fake_ask([]))
        self.assertEqual(self.tile.public_categ_ids.name, "Raghad Lighting")

    def test_descriptions_are_written_only_where_empty(self):
        calls = []
        written, notes = shop.describe_products(self.env_d, self.tile | self.paint, ask=fake_ask(calls))
        self.assertEqual(written, 1)
        self.assertEqual(len(calls), 1)
        self.assertIn("A good product number %s." % self.tile.id, self.tile[self.field])
        self.assertEqual(self.paint[self.field], "<p>Typed by a person.</p>")
        self.assertIn('<data source="shop_products">', calls[0])
        self.assertNotIn("Typed by a person", calls[0], "a product that has a text is not even sent")

    def test_descriptions_go_by_batches_of_twenty(self):
        products = self.env["product.template"].create([
            {"name": "Raghad Item %s" % i, "sale_ok": True, "is_published": True} for i in range(45)])
        calls = []
        written, _notes = shop.describe_products(self.env_d, products, ask=fake_ask(calls))
        self.assertEqual(written, 45)
        self.assertEqual([len(re.findall(r'"id": \d+', c)) for c in calls], [20, 20, 5])

    def test_a_text_typed_while_the_provider_thinks_is_kept(self):
        def ask(system, text):
            self.tile.write({self.field: "<p>Typed by Ebrahim meanwhile.</p>"})
            return json.dumps({str(self.tile.id): "Provider text."})
        written, _notes = shop.describe_products(self.env_d, self.tile, ask=ask)
        self.assertEqual(written, 0)
        self.assertIn("Typed by Ebrahim meanwhile.", self.tile[self.field])

    def test_private_mode_skips_the_descriptions_with_a_note(self):
        refusal = Refused("Private mode is on: this provider is not on your own server.")
        with patch("odoo.addons.nudge.tools.bulk.get_provider", side_effect=refusal):
            written, notes = shop.describe_products(self.env_d, self.tile)
        self.assertEqual(written, 0)
        self.assertTrue(notes and "skipped" in notes[0])
        self.assertFalse(self.tile[self.field])
        # and the rest of the shop is still arranged
        with patch("odoo.addons.nudge.tools.bulk.get_provider", side_effect=refusal):
            result = shop.build_shop(self.env_d, self.site, TEMPLATE)
        self.assertTrue(any("skipped" in note for note in result["notes"]))
        self.assertTrue(self.env["product.public.category"].search([("name", "=", "Tiles")]))

    def test_readiness_of_a_bare_database_lists_the_three_missing_items(self):
        self.env["payment.provider"].search([]).write({"state": "disabled"})
        self.env["delivery.carrier"].search([]).write({"active": False})
        self.env["product.pricelist"].search([]).write({"active": False})
        items = {item["key"]: item for item in shop.readiness(self.env_d, self.site)}
        missing = sorted(key for key, item in items.items() if not item["ok"])
        self.assertEqual(missing, ["delivery", "payment", "pricelist"])
        self.assertTrue(items["tax_display"]["ok"])
        for key in missing:
            self.assertTrue(items[key]["show"], "Show me opens the setting of %s" % key)
            self.assertEqual(items[key]["show"]["type"], "ir.actions.act_window")
        plan = shop.plan_shop(self.env_d, self.site, TEMPLATE)
        line = [l for l in plan["lines"] if l["label"] == "Ready to sell?"][0]
        self.assertIn("payment provider", line["value"])
        self.assertIn("never configures payment", line["value"])

    def test_banners_stay_unpublished_and_nothing_is_deleted(self):
        before = self.env["product.template"].search_count([])
        plan = shop.plan_shop(self.env_d, self.site, TEMPLATE)
        shop.apply_shop(self.env_d, self.site, plan, ask=fake_ask([]))
        banners = self.env["website.page"].search([("url", "like", "/promo-")])
        self.assertEqual(len(banners), 3)
        self.assertFalse(any(banners.mapped("is_published")))
        self.assertEqual(self.env["product.template"].search_count([]), before)

    def test_the_tool_shows_a_card_then_applies(self):
        tool = base.TOOLS["build_shop"]
        result = tool.run(self.env_d, {}, None)
        self.assertTrue(result.pending)
        self.assertTrue(result.pending["preview"]["lines"])
        plan = result.pending["values"]["plan"]
        self.assertFalse(self.env["product.public.category"].search([("name", "=", "Raghad Lighting")]))
        step = SimpleNamespace(values={"plan": plan})
        with patch("odoo.addons.nudge.tools.bulk.get_provider", side_effect=Refused("private")):
            done = tool.execute(self.env_d, step)
        self.assertFalse(done.is_error)

    def test_non_designer_is_refused(self):
        env = self.env(user=self.plain)
        with self.assertRaises(AccessError):
            shop.plan_shop(env, self.website.with_env(env), TEMPLATE)
        with self.assertRaises(AccessError):
            shop.apply_shop(env, self.website.with_env(env), {})

    def test_the_hooks_of_the_site_of_the_builder(self):
        if "nudge.builder.site" not in self.env:
            self.skipTest("the site model of the builder is not loaded")
        site = self.env_d["nudge.builder.site"].create({"name": "Raghad Shop Site", "website_id": self.website.id, "shop": True})
        plan = site._shop_plan()
        self.assertTrue(plan["lines"])
        with patch("odoo.addons.nudge.tools.bulk.get_provider", side_effect=Refused("private")):
            log = site._build_shop(plan=plan)
        self.assertTrue(any(line.startswith("Shop categories") for line in log))
        checked = site._run_checks()
        self.assertTrue(all({"level", "page_key", "message"} <= set(item) for item in checked))

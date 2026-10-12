import unittest

from odoo.tests import BaseCase, TransactionCase, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.models.nudge_conversation import is_product_command
from odoo.addons.nudge.tools import product_search

from .common import NudgeCase


@tagged("post_install", "-at_install")
class TestReading(BaseCase):
    """What a question says: its words and its sizes, in any unit."""

    def test_sizes_in_any_unit(self):
        cases = {
            "Bolt 8 mm stainless": {"length": [8.0]},
            "برغي 8 ملم ستانلس": {"length": [8.0]},
            "pipe 1/2 inch": {"length": [12.7]},
            'PVC pipe 2"': {"length": [50.8]},
            "Laptop 16 GB": {"storage": [16384.0]},
            "water 1.5 L": {"volume": [1500.0]},
            "زيت 4 لتر": {"volume": [4000.0]},
            "motor 1.5 kW 220 V": {"power": [1500.0], "voltage": [220.0]},
            "red T-shirt XL": {"size": ["xl"]},
            "tile 60x60": {"dimensions": [(60.0, 60.0)]},
        }
        for text, specs in cases.items():
            self.assertEqual(product_search.specs_of(text), specs, text)

    def test_the_words_that_carry_the_product(self):
        self.assertEqual(product_search.words_of("Bolt 8 mm stainless"), ["bolt", "stainless"])
        self.assertEqual(product_search.words_of("water 1.5 L"), ["water"])
        self.assertEqual(product_search.words_of("red T-shirt XL"), ["red", "shirt"])
        self.assertIn("2024", product_search.words_of("printer model 2024"), "A long number may be a model")

    def test_sizes_are_compared_across_units(self):
        self.assertEqual(product_search.compare_specs({"length": [12.7]}, {"length": [12.7]}),
                         ([("length", 12.7)], []))
        self.assertEqual(product_search.compare_specs({"length": [8.0]}, {"length": [10.0]}),
                         ([], [("length", 8.0)]))
        self.assertEqual(product_search.compare_specs({"size": ["xl"]}, {"length": [5.0]}), ([], []),
                         "A measure the product does not say is neither for nor against")

    def test_a_product_that_shares_one_word_is_said_so(self):
        # A table top was named for a large cabinet: "top" was all they shared
        from odoo.addons.nudge.models.nudge_quick import weak_note  # noqa: PLC0415

        top = [{"name": "Table Top", "code": "TT-1"}]
        self.assertIn("'Table Top' shares only 'top'", weak_note("Large cabinet with a top", top))
        self.assertIn("shares no word", weak_note("Large oak cabinet", top))
        self.assertEqual(weak_note("Large cabinet", [{"name": "[E-COM07] Large Cabinet", "code": "E-COM07"}]), "")
        self.assertEqual(weak_note("Cabinet, code TT-1", top), "", "The code printed on it leaves no doubt")
        self.assertEqual(weak_note("Top", top), "", "One word asked, one found")

    def test_the_command(self):
        for text in ("/product bolt", "/Product  bolt", "/منتج برغي", "/product"):
            self.assertTrue(is_product_command(text), text)
        for text in ("product bolt", "/products", "show /product", ""):
            self.assertFalse(is_product_command(text), text)


@tagged("post_install", "-at_install")
class TestFinding(NudgeCase, TransactionCase):
    """Products by what people call them, in any trade, as the person sees them."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if "product.product" not in cls.env:
            raise unittest.SkipTest("The app Products is not installed here: nothing to find")
        Product = cls.env["product.product"]
        cls.bolt_8 = Product.create({"name": "Hex Bolt Stainless 8 mm", "default_code": "HB-8", "list_price": 2.5})
        cls.bolt_10 = Product.create({"name": "Hex Bolt Stainless 10 mm", "default_code": "HB-10", "list_price": 3})
        cls.pipe = Product.create({"name": "PVC Pipe 1/2 inch", "default_code": "PVC-050"})
        cls.shirt_xl = Product.create({"name": "Cotton T-Shirt Red XL", "default_code": "TS-R-XL"})
        cls.shirt_m = Product.create({"name": "Cotton T-Shirt Red M", "default_code": "TS-R-M"})
        cls.laptop = Product.create({"name": "Laptop ProBook 16 GB", "default_code": "LP-16"})
        cls.arabic = Product.create({"name": "برغي ستانلس 6 ملم", "default_code": "AR-6"})

    def find(self, text, user=None):
        return product_search.find(self.env(user=user or self.user_a), text)

    def names(self, text, user=None):
        return [item["name"] for item in self.find(text, user)["items"]]

    def test_the_size_asked_comes_first(self):
        found = self.find("bolt stainless 8 mm")["items"]
        self.assertEqual(found[0]["id"], self.bolt_8.id)
        self.assertIn("Same size: 8 mm", found[0]["reasons"])
        ids = [item["id"] for item in found]
        if self.bolt_10.id in ids:
            self.assertGreater(ids.index(self.bolt_10.id), 0, "Another size comes after")

    def test_a_size_in_another_unit(self):
        found = self.find("pipe 12.7 mm")["items"]
        self.assertEqual(found[0]["id"], self.pipe.id)
        self.assertTrue(any(reason.startswith("Same size") for reason in found[0]["reasons"]))

    def test_clothes_by_their_size(self):
        found = self.find("red t-shirt XL")["items"]
        self.assertEqual(found[0]["id"], self.shirt_xl.id)

    def test_a_code_as_it_is_written(self):
        found = self.find("HB-10")["items"]
        self.assertEqual(found[0]["id"], self.bolt_10.id)
        self.assertIn("Same code: HB-10", found[0]["reasons"])

    def test_a_word_misspelt(self):
        self.assertIn(self.bolt_8.display_name, self.names("stainles bolt"))
        self.assertIn(self.laptop.display_name, self.names("laptp probook"))

    def test_in_arabic(self):
        self.assertIn(self.arabic.display_name, self.names("برغي ستانلس"))

    def test_nothing_like_it(self):
        self.assertEqual(self.find("zzqx")["items"], [])
        self.assertEqual(self.find("a")["items"], [], "One letter is not a search")

    def test_a_card_says_what_the_person_may_read(self):
        item = self.find("HB-8")["items"][0]
        self.assertEqual(item["image"], f"/web/image/product.product/{self.bolt_8.id}/image_128")
        self.assertIn("price", item)
        may_read_cost = product_search._readable(self.bolt_8.with_user(self.user_a), "standard_price")
        self.assertEqual("cost" in item, may_read_cost)
        if "stock.warehouse" in self.env:
            self.assertIn("on_hand", item)

    def test_archived_products_are_not_found(self):
        self.bolt_10.active = False
        self.assertNotIn(self.bolt_10.id, [item["id"] for item in self.find("HB-10")["items"]])

    def test_the_tool_shows_cards(self):
        env = self.env(user=self.user_a)
        result = tools.base.call(env, "search_products", {"words": "bolt 8 mm"}, None, "talk")
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(result.event["type"], "products")
        self.assertEqual(result.event["items"][0]["id"], self.bolt_8.id)
        self.assertIn("Hex Bolt Stainless 8 mm", result.content)
        self.assertIn("search_products", [tool.name for tool in tools.base.available(env, "talk")])
        self.assertIn("search_products", tools.base.ESSENTIAL_TOOLS, "Laptops keep it")

    def test_a_picture_finds_the_products_of_its_kind(self):
        # A sofa and a desk that shared "large", "two" and "oak" came before the cabinet of the picture
        Product = self.env["product.product"]
        Product.create([{"name": "Nudge Large Oak Doors Desk"}, {"name": "Nudge Large Oak Doors Shelf"},
                        {"name": "Nudge Large Oak Doors Sofa"}, {"name": "Nudge Zorbex Unit", "default_code": "NZ-1"}])
        Quick = self.env["nudge.quick"].with_user(self.user_a)
        read = "Zorbex, large, two doors, oak"
        self.assertNotIn("Nudge Zorbex Unit", [item.get("name") for item in Quick._ground_products(read)],
                         "By the words alone, what shares more of them comes first")
        found = [item["name"] for item in Quick._ground_products(read, by_kind=True) if "name" in item]
        self.assertEqual(found, ["[NZ-1] Nudge Zorbex Unit"])
        from odoo.addons.nudge.models.nudge_quick import kind_of  # noqa: PLC0415

        self.assertEqual(kind_of("Office chair, black, swivel, mesh"), "chair")
        self.assertEqual(kind_of("A large oak cabinet with two doors"), "cabinet")
        # When no product is of that kind, the best of the others still come
        found = [item.get("name") for item in Quick._ground_products("Qwzrt, large, oak, doors", by_kind=True)]
        self.assertIn("Nudge Large Oak Doors Desk", found)

    def test_two_as_alike_are_both_named(self):
        # Two cabinets with doors tied, and the model named the wrong one
        Product = self.env["product.product"]
        Product.create({"name": "Cabinet Two Doors", "default_code": "CAB-A"})
        Product.create({"name": "Cabinet Two Doors", "default_code": "CAB-B"})
        env = self.env(user=self.user_a)
        tied = tools.base.call(env, "search_products", {"words": "cabinet two doors"}, None, "talk")
        self.assertFalse(tied.is_error, tied.content)
        self.assertIn("name both, or ask which one is meant", tied.content)
        apart = tools.base.call(env, "search_products", {"words": "bolt 8 mm"}, None, "talk")
        self.assertNotIn("name both", apart.content)
        # Found for a small model with the question, the tie is said the same way
        Quick = self.env["nudge.quick"].with_user(self.user_a)
        self.assertIn("name both, or ask which one is meant",
                      Quick._ground_products("cabinet two doors")[-1].get("note", ""))
        self.assertNotIn("note", Quick._ground_products("bolt 8 mm")[-1])

    def test_found_without_any_ai(self):
        Quick = self.env["nudge.quick"].with_user(self.user_a)
        self.assertEqual(Quick.find_products("HB-8")["items"][0]["id"], self.bolt_8.id)
        self.assertEqual(Quick.find("bolt stainless")["products"][0]["id"] in (self.bolt_8.id, self.bolt_10.id), True)

    def test_the_command_reaches_for_the_tool(self):
        adapter = self.use_adapter()
        self.ask(self.user_a, "/product bolt 8 mm")
        self.assertEqual(adapter.calls[0]["tool_choice"], "required")
        self.ask(self.user_a, "What is a bolt?")
        self.assertIsNone(adapter.calls[1]["tool_choice"])



@tagged("post_install", "-at_install")
class TestSwitch(NudgeCase, TransactionCase):

    def test_switched_on_by_default(self):
        settings = self.env["res.users.settings"]._find_or_create_for_user(self.user_a)
        self.assertTrue(settings.nudge_enabled)

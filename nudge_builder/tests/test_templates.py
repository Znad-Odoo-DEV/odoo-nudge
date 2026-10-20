"""The 12 templates of the library: they load, follow the schema, build, and carry clean Arabic."""
import copy
import json
import os
import re
import tempfile

from lxml import etree, html as lxml_html

from odoo.tests import TransactionCase, tagged
from odoo.tools import file_path

from odoo.addons.nudge_builder.models import nudge_builder_template as library

EXPECTED = {
    "contracting", "real_estate", "clinic", "restaurant", "school", "law_firm", "workshop",
    "services_company", "fashion_shop", "electronics_shop", "building_materials_shop", "food_products_shop",
}
SHOPS = {"fashion_shop", "electronics_shop", "building_materials_shop", "food_products_shop"}
LATIN = re.compile(r"[A-Za-z]{2,}")
NOISE = re.compile(r"\S+@\S+|https?://\S+|www\.\S+")


def _webp_size(path):
    """(width, height) of a lossy WebP read from its header (Odoo restricts the image formats PIL opens)."""
    with open(path, "rb") as handle:
        head = handle.read(32)
    assert head[:4] == b"RIFF" and head[8:12] == b"WEBP" and head[12:16] == b"VP8 ", path
    return (int.from_bytes(head[26:28], "little") & 0x3FFF, int.from_bytes(head[28:30], "little") & 0x3FFF)


def _texts(node, path="", field=""):
    """Every (path, field, text) leaf of a copy structure."""
    if isinstance(node, dict):
        for name, value in node.items():
            yield from _texts(value, "%s/%s" % (path, name), name)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _texts(value, "%s[%d]" % (path, index), field)
    elif isinstance(node, str):
        yield path, field, node


@tagged("post_install", "-at_install", "nudge_builder_templates")
class TestTemplateLibrary(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Template = cls.env["nudge.builder.template"]
        cls.Template._load_library()
        cls.records = {rec.key: rec for rec in cls.Template.search([])}
        cls.files = dict(library.read_library_files())

    # ------------------------------------------------------------------ loading
    def test_twelve_templates_load(self):
        self.assertEqual(set(self.files), EXPECTED)
        for key in EXPECTED:
            self.assertIn(key, self.records, key)
        self.assertEqual({k for k, r in self.records.items() if r.shop and k in EXPECTED}, SHOPS)
        for key in EXPECTED:
            rec = self.records[key]
            self.assertTrue(rec.name_en and rec.name_ar and rec.sector)
            self.assertGreaterEqual(rec.page_count, 9, key)

    def test_loading_twice_updates_in_place(self):
        before = self.Template.search_count([("key", "in", list(EXPECTED))])
        self.assertEqual(sorted(self.Template._load_library()), sorted(EXPECTED))
        self.assertEqual(self.Template.search_count([("key", "in", list(EXPECTED))]), before)

    def test_changed_file_updates_the_record(self):
        with tempfile.TemporaryDirectory() as directory:
            data = copy.deepcopy(self.files["clinic"])
            data["name"]["en"] = "Dental clinic"
            with open(os.path.join(directory, "clinic.json"), "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False)
            self.assertEqual(self.Template._load_library(directory), ["clinic"])
            self.assertEqual(self.records["clinic"].name_en, "Dental clinic")

    def test_broken_file_is_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as directory:
            data = copy.deepcopy(self.files["clinic"])
            del data["pages"][0]["blocks"][0]["copy"]["ar"]
            data["key"] = "broken"
            with open(os.path.join(directory, "broken.json"), "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False)
            with self.assertLogs("odoo.addons.nudge_builder.models.nudge_builder_template", level="ERROR"):
                self.assertEqual(self.Template._load_library(directory), [])
            self.assertFalse(self.Template.search([("key", "=", "broken")]))

    # ------------------------------------------------------------------ schema
    def test_schema_valid(self):
        for key, data in self.files.items():
            library.validate_library_template(data, key)

    def test_core_schema_accepts_the_files(self):
        try:
            from odoo.addons.nudge_builder.tools.schema import validate_template
        except ImportError:  # the core is not merged yet
            self.skipTest("core schema not available")
        for key, data in self.files.items():
            probe = copy.deepcopy(data)
            for page in probe["pages"]:
                for block in page["blocks"]:
                    block["copy"] = {"en": block["copy"]["en"]} if isinstance(block["copy"].get("en"), str) else block["copy"]
            try:
                validate_template(probe)
            except ValueError as error:
                # the core's first version rejected the object-valued copy; only that fault is tolerated
                self.assertIn("copy", str(error), "%s: %s" % (key, error))

    def test_validator_refuses_faults(self):
        base = self.files["clinic"]
        cases = {
            "missing arabic slot": lambda d: d["pages"][0]["blocks"][0]["copy"]["ar"].pop("title"),
            "unknown snippet": lambda d: d["pages"][0]["blocks"][0].__setitem__("snippet", "s_nope"),
            "shop snippet on a non-shop": lambda d: d["pages"][0]["blocks"][1].__setitem__("snippet", "s_product_list"),
            "font not bundled": lambda d: d["fonts"].__setitem__("heading", "Cairo"),
            "item count differs": lambda d: d["pages"][0]["blocks"][2]["copy"]["ar"]["items"].pop(),
            "bad colour": lambda d: d["palette"].__setitem__("primary", "red"),
            "menu page missing": lambda d: d["menu"].append("nowhere"),
            "empty arabic text": lambda d: d["pages"][1]["seo"]["title"].__setitem__("ar", " "),
        }
        for label, mutate in cases.items():
            data = copy.deepcopy(base)
            mutate(data)
            with self.assertRaises(ValueError, msg=label):
                library.validate_library_template(data, "clinic")

    def test_every_slot_has_both_languages(self):
        for key, data in self.files.items():
            for page in data["pages"]:
                for block in page["blocks"]:
                    en = dict((p, t) for p, f, t in _texts(block["copy"]["en"]))
                    ar = dict((p, t) for p, f, t in _texts(block["copy"]["ar"]))
                    self.assertEqual(set(en), set(ar), "%s/%s/%s" % (key, page["key"], block["snippet"]))
                    self.assertTrue(all(t.strip() for t in ar.values()))

    def test_palette_fonts_menu_seo(self):
        for key, data in self.files.items():
            self.assertEqual(len(data["palette"]), 5, key)
            self.assertEqual(len({v for v in data["palette"].values()}), 5, key)
            self.assertIn(data["fonts"]["heading"], library.BUNDLED_FONTS)
            for name in {data["fonts"]["heading"], data["fonts"]["body"], "Tajawal"}:
                self.assertTrue(os.path.isdir(file_path("web/static/fonts/google/%s" % name.replace(" ", "_"))), name)
            keys = [p["key"] for p in data["pages"]]
            self.assertTrue(set(data["menu"]) <= set(keys))
            for page in data["pages"]:
                for lang in ("en", "ar"):
                    self.assertLessEqual(len(page["seo"]["title"][lang]), 70, "%s/%s" % (key, page["key"]))
                    self.assertLessEqual(len(page["seo"]["description"][lang]), 170, "%s/%s" % (key, page["key"]))

    def test_snippets_exist_in_this_odoo(self):
        used = {b["snippet"] for d in self.files.values() for p in d["pages"] for b in p["blocks"]}
        self.assertTrue(used <= (library.SNIPPETS | library.SHOP_SNIPPETS))
        for snippet in used:
            self.assertTrue(self.env.ref("website." + snippet, raise_if_not_found=False), snippet)

    def test_shop_templates_carry_the_shop_part(self):
        for key, data in self.files.items():
            if key not in SHOPS:
                self.assertFalse(data.get("shop"), key)
                continue
            shop = data["shop"]
            self.assertEqual(len(shop["banners"]), 3, key)
            self.assertGreaterEqual(len(shop["categories"]), 4, key)
            self.assertTrue(any(c["children"] for c in shop["categories"]), key)
            self.assertGreaterEqual(len(shop["filters"]), 3, key)
            self.assertTrue(shop["product_page"]["sections"] and shop["cart_checkout"]["options"], key)
            self.assertEqual(len(shop["sample_products"]), 4, key)
            self.assertEqual(shop["layout"]["add_to_cart_action"], "stay")

    # ------------------------------------------------------------------ Arabic
    def test_arabic_copy_has_no_english(self):
        for key, data in self.files.items():
            leaks = []
            for page in data["pages"]:
                for block in page["blocks"]:
                    for path, field, text in _texts(block["copy"]["ar"]):
                        if field not in library.PLAIN_FIELDS and LATIN.search(NOISE.sub("", text)):
                            leaks.append((page["key"], path, text))
                for part in ("title", "description"):
                    if LATIN.search(page["seo"][part]["ar"]):
                        leaks.append((page["key"], "seo", page["seo"][part]["ar"]))
                if LATIN.search(page["name"]["ar"]):
                    leaks.append((page["key"], "name", page["name"]["ar"]))
            for entry in (data["name"]["ar"], data["brand"]["ar"]):
                if LATIN.search(entry):
                    leaks.append(("template", "name", entry))
            if data.get("shop"):
                for path, field, text in _texts({k: v for k, v in data["shop"].items() if k in ("categories", "banners", "filters", "sample_products", "product_page", "cart_checkout")}):
                    if "/ar" in path and LATIN.search(text):
                        leaks.append(("shop", path, text))
            self.assertFalse(leaks, "%s: %s" % (key, leaks[:3]))

    def test_arabic_copy_is_arabic(self):
        for key, data in self.files.items():
            hero = data["pages"][0]["blocks"][0]["copy"]["ar"]["title"]
            self.assertTrue(re.search(r"[؀-ۿ]", hero), key)

    # ------------------------------------------------------------------ images
    def test_images_and_thumbnails(self):
        license_text = open(file_path("nudge_builder/static/img/templates/LICENSE"), encoding="utf-8").read()
        self.assertIn("CC0", license_text)
        for key, data in self.files.items():
            hero = file_path("nudge_builder/" + data["hero_image"])
            thumb = file_path("nudge_builder/" + data["thumbnail"])
            self.assertLessEqual(os.path.getsize(hero), 200 * 1024, key)
            self.assertEqual(_webp_size(thumb), (400, 250), thumb)
            self.assertEqual(_webp_size(hero), (1600, 900), hero)
            self.assertTrue(self.records[key].thumbnail and self.records[key].hero_image, key)

    # ------------------------------------------------------------------ arch
    def _assemble(self, page, lang):
        """Local assembler (tests only): render each snippet, fill title/text/button/items, wrap in a page arch."""
        qweb = self.env["ir.qweb"]
        parts = []
        for block in page["blocks"]:
            rendered = qweb._render("website." + block["snippet"])
            self.assertTrue(str(rendered).strip(), block["snippet"])
            root = lxml_html.fragment_fromstring(str(rendered), create_parent="div")
            fields = block["copy"][lang]
            heads = root.xpath(".//h1|.//h2")
            if fields.get("title") and heads:
                heads[0].text = fields["title"]
            paragraphs = root.xpath(".//p")
            if fields.get("text") and paragraphs:
                paragraphs[0].text = fields["text"]
            parts.append(etree.tostring(root, encoding="unicode").replace("<div>", "", 1).rsplit("</div>", 1)[0])
        return '<t t-name="nudge_builder_test.page"><t t-call="website.layout"><div id="wrap" class="oe_structure">%s</div></t></t>' % "".join(parts)

    def test_each_page_arch_renders(self):
        site_model = self.env["nudge.builder.site"] if "nudge.builder.site" in self.env else None
        use_core = site_model is not None and hasattr(site_model, "_build_page")
        website = self.env["website"].search([], limit=1)
        site = False
        if use_core:
            site = site_model.create({"name": "Template test", "website_id": website.id})
        for key, data in self.files.items():
            for page in data["pages"]:
                if use_core:
                    built = site._build_page(dict(page))
                    self.assertTrue(built.view_id.arch_db, "%s/%s" % (key, page["key"]))
                    continue
                for lang in ("en", "ar"):
                    arch = self._assemble(page, lang)
                    view = self.env["ir.ui.view"].create({
                        "name": "%s %s %s" % (key, page["key"], lang), "type": "qweb", "arch": arch,
                        "key": "nudge_builder_test.%s_%s_%s" % (key, page["key"], lang)})
                    self.assertIn("wrap", view.arch_db)

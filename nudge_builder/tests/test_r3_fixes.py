"""Release 3 filming defects: the sample footer, logo and phone after a real build; the legal pages in Arabic;
the second press of "Show the records on the site"."""
import base64
from unittest import SkipTest

from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge.tests.common import NudgeCase
from odoo.addons.nudge_builder.tools import library

PNG = base64.b64encode(
    base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="))
SAMPLES = ("555-555-5556", "Your Logo", "MyCompany", "yourcompany", "Marina Blvd")


@tagged("post_install", "-at_install")
class TestR3Fixes(NudgeCase, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.designer = new_test_user(
            cls.env, login="builder_r3", name="Raghad Designer", email="raghad@acme.example",
            groups="website.group_website_designer,nudge.group_nudge_user")
        cls.arabic = cls.env["res.lang"]._activate_lang("ar_001") or cls.env["res.lang"].search([("code", "=", "ar_001")])
        cls.english = cls.env["res.lang"].search([("code", "=", "en_US")])
        cls.website = cls.env["website"].search([], limit=1)
        cls.env.company.write({"name": "Raghad Contracting", "email": "info@raghad.example", "phone": "+1 555-555-5556"})
        cls.env.company.partner_id.write({"street": "8000 Marina Blvd, Suite 300", "city": "Brisbane"})

    def built(self, template="generic", website=None):
        website = website or self.env["website"].create({"name": "R3 new website", "company_id": self.env.company.id})
        site = self.env["nudge.builder.site"].with_user(self.designer).create({
            "website_id": website.id, "kind": "company", "direction": "a",
            "lang_ids": [(6, 0, (self.english | self.arabic).ids)]})
        site.action_preview()
        template = library.load(template)
        site.write({"steps": site._plan(template), "step_index": 0, "progress": 0, "log": False})
        while not site.action_build_step()["done"]:
            pass
        return site

    def layout_arch(self, site, lang):
        View = self.env["ir.ui.view"].with_context(active_test=False)
        views = View.search([("website_id", "=", site.website_id.id), ("key", "like", "website.template_%")])
        return "\n".join(v.with_context(lang=lang).arch_db or "" for v in views)

    def test_a_built_site_shows_the_company_in_footer_header_and_phone(self):
        site = self.built()
        self.assertNotIn("The footer was left as Odoo made it.", site.log or "")
        self.assertNotIn("The header and footer layout was left", site.log or "")
        for lang in ("en_US", "ar_001"):
            arch = self.layout_arch(site, lang)
            self.assertIn("info@raghad.example", arch, lang)
            for sample in SAMPLES:
                self.assertNotIn(sample, arch, f"{lang}: {sample}")

    def test_the_header_shows_the_company_logo_or_its_name_never_the_sample_logo(self):
        import base64
        import io

        from PIL import Image
        website = self.env["website"].create({"name": "R3 name site", "company_id": self.env.company.id})
        site = self.built(website=website)
        self.assertFalse(site._has_own_logo(), "no logo of the company: the name is shown")
        View = self.env["ir.ui.view"].with_context(active_test=False)
        brand = View.search([("key", "=", "website.option_header_brand_name"), ("website_id", "=", website.id)])
        self.assertTrue(brand.active)
        for lang in ("en_US", "ar_001"):
            self.assertIn("Raghad Contracting", brand.with_context(lang=lang).arch_db)
            self.assertNotIn("My Website", brand.with_context(lang=lang).arch_db)
        logo = View.search([("key", "=", "website.option_header_brand_logo"), ("website_id", "=", website.id)])
        self.assertFalse(logo.active)
        image = Image.new("RGB", (40, 20), (10, 120, 60))
        out = io.BytesIO()
        image.save(out, "PNG")
        self.env.company.logo = base64.b64encode(out.getvalue())
        other = self.env["website"].create({"name": "R3 logo site", "company_id": self.env.company.id})
        site2 = self.built(website=other)
        self.assertTrue(site2._has_own_logo())
        self.assertEqual(other.logo, self.env.company.logo)

    def test_the_legal_pages_are_named_in_every_language_of_the_site(self):
        site = self.built()
        legal = site.page_ids.filtered("is_legal")
        self.assertTrue(legal)
        for record in legal:
            self.assertTrue(record.menu_id)
            self.assertEqual(record.menu_id.with_context(lang="ar_001").name,
                             {"legal_privacy": "سياسة الخصوصية", "legal_terms": "الشروط والأحكام"}[record.key])
            self.assertEqual(record.menu_id.with_context(lang="en_US").name,
                             {"legal_privacy": "Privacy Policy", "legal_terms": "Terms and Conditions"}[record.key])

    def test_the_second_press_of_show_the_records_says_the_pages_already_show_them(self):
        website = self.env["website"].create({"name": "R3 records site", "company_id": self.env.company.id})
        site = self.env["nudge.builder.site"].with_user(self.designer).create({
            "website_id": website.id, "kind": "contracting",
            "lang_ids": [(6, 0, (self.english | self.arabic).ids)]})
        site.action_preview()
        for model, values in (("project", {"name": "Villa"}), ("project", {"name": "Tower"}),
                              ("service", {"name": "Villa"}), ("service", {"name": "Fit-out"}), ("service", {"name": "MEP"}),
                              ("faq", {"question": "How long?", "answer": "Weeks."}),
                              ("testimonial", {"author": "Ali", "quote": "Great."})):
            record = self.env[f"nudge.builder.{model}"].with_user(self.designer).create({"site_id": site.id, **values})
            if "name" in record._fields and model != "testimonial":
                record.with_context(lang="ar_001").name = (
                    ("خدمة " if model == "service" else "مشروع ") + values.get("name", "سؤال"))
                record.summary = "Summary of %s" % values.get("name", "x") if "summary" in record._fields else False
            if "picture_ids" in record._fields:
                record.picture_ids = [(0, 0, {"image": PNG, "name": "p.png"})]
        site._apply_template(library.load("contracting"))
        site = site.with_user(self.designer)
        first = site.action_show_records()
        second = site.action_show_records()
        self.assertEqual(second["params"]["type"], "info", second["params"]["message"])
        self.assertEqual(second["params"]["message"], "The pages already show the records.")
        # a change is still drawn once, and then it is stable again
        self.env["nudge.builder.project"].with_user(self.designer).create({"site_id": site.id, "name": "Mall"})
        third = site.action_show_records()
        self.assertEqual(third["params"]["type"], "success")
        self.assertEqual(site.action_show_records()["params"]["type"], "info")
        self.assertTrue(first)

    def bare_site(self, **values):
        website = self.env["website"].create({"name": "R3 page site", "company_id": self.env.company.id})
        return self.env["nudge.builder.site"].with_user(self.designer).create({
            "website_id": website.id, "kind": "company", "direction": "a",
            "lang_ids": [(6, 0, (self.english | self.arabic).ids)], **values})

    def test_the_sample_brand_of_a_template_becomes_the_company_name(self):
        site = self.bare_site(template_key="fashion_shop")
        template = library.load("fashion_shop")
        about = next(page for page in template["pages"] if "About Zahra Fashion" in str((page.get("seo") or {}).get("title")))
        spec = site._with_company_brand(about)
        self.assertNotIn("Zahra Fashion", str(spec))
        self.assertNotIn("أزياء زهرة", str(spec))
        self.assertIn("About Raghad Contracting", str(spec["seo"]["title"]["en"]))
        self.assertIn("عن Raghad Contracting", spec["seo"]["title"]["ar"])
        self.assertIn("Zahra Fashion", str(about), "the template itself is not changed")

    def test_a_page_keeps_its_arabic_block_by_block_when_one_block_differs(self):
        site = self.bare_site()
        items = lambda n, word: [{"title": f"{word} {i}", "text": f"Text {word} {i}"} for i in range(n)]
        spec = {"key": "t2", "legal": True, "url": "/r3-blocks", "name": {"en": "Blocks", "ar": "كتل"}, "blocks": [
            {"snippet": "s_title", "copy": {"en": {"title": "Hello there"}, "ar": {"title": "مرحبا بكم"}}},
            {"snippet": "s_features", "copy": {
                "en": {"title": "Beliefs", "items": items(3, "Item")}, "ar": {"title": "قيمنا", "items": items(2, "بند")}}}]}
        record = site._build_page(spec)
        arabic = record.view_id.with_context(lang="ar_001").arch_db
        self.assertIn("مرحبا بكم", arabic, "the block whose terms match is translated")
        self.assertNotIn("Hello there", arabic)
        self.assertIn("The block 2 (s_features) of the page Blocks stays untranslated in ar_001", site.log or "")

    def test_the_home_product_lists_of_a_shop_show_the_chosen_products(self):
        if "product.template" not in self.env or "product.public.category" not in self.env:
            raise SkipTest("Website Sale is not installed")
        shop_designer = new_test_user(
            self.env, login="builder_r3_shop", name="Raghad Shop",
            groups="website.group_website_designer,nudge.group_nudge_user,product.group_product_manager")
        site = self.bare_site(template_key="fashion_shop", shop=True).with_user(shop_designer)
        if not site.shop_mode:
            raise SkipTest("Website Sale is not installed")
        products = self.env["product.template"].create([
            {"name": "Raghad Silk Scarf", "list_price": 45}, {"name": "Ebrahim Linen Abaya", "list_price": 120}])
        site.shop_product_ids = products.ids
        spec = {"key": "t3", "legal": True, "url": "/r3-shop", "name": {"en": "Shop home", "ar": "الرئيسية"}, "blocks": [
            {"snippet": "s_product_list", "copy": {"en": {"title": "New arrivals"}, "ar": {"title": "وصل حديثاً"}}}]}
        record = site._build_page(spec)
        arch = record.view_id.with_context(lang="en_US").arch_db
        self.assertIn("Raghad Silk Scarf", arch)
        self.assertIn("Ebrahim Linen Abaya", arch)
        self.assertIn("New arrivals", arch)
        self.assertIn("وصل حديثاً", record.view_id.with_context(lang="ar_001").arch_db)

    def test_the_home_product_list_prices_in_the_currency_of_the_website_pricelist(self):
        if "product.pricelist" not in self.env or "pricelist_ids" not in self.env["website"]._fields:
            raise SkipTest("Website Sale is not installed")
        shop_designer = new_test_user(
            self.env, login="builder_r3_price", name="Raghad Price",
            groups="website.group_website_designer,nudge.group_nudge_user,product.group_product_manager")
        site = self.bare_site(template_key="fashion_shop", shop=True).with_user(shop_designer)
        if not site.shop_mode:
            raise SkipTest("Website Sale is not installed")
        aed = self.env.ref("base.AED")
        aed.active = True
        usd = self.env.ref("base.USD")
        usd.active = True
        pricelist = self.env["product.pricelist"].create({
            "name": "Raghad AED", "currency_id": aed.id, "sequence": -100, "website_id": site.website_id.id,
            "item_ids": [(0, 0, {"compute_price": "formula", "base": "list_price", "price_surcharge": 5})]})
        self.assertEqual(site.website_id.pricelist_ids[:1], pricelist)
        product = self.env["product.template"].create(
            {"name": "Raghad Price Scarf", "list_price": 45, "currency_id": usd.id})
        price, currency = site._shop_price(product)
        self.assertEqual(currency, aed, "the currency is the one the website shows")
        self.assertGreater(price, 0)
        item = site._shop_item(product, site._langs()[:1])
        self.assertNotIn("$", item["price_text"])
        self.assertIn(aed.symbol, item["price_text"])

"""Defects seen while filming an Arabic site (a furniture workshop, shop off): empty counters, a Shop entry and a
cart with no shop, two contact entries and two contact buttons, English page names for an Arabic reader, and the
direction cards cut off in RTL. Each is built through the real steps of the build."""
import copy
import re

from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge.tests.common import NudgeCase
from odoo.addons.nudge_builder.tools import compat, library, preview, snippets


@tagged("post_install", "-at_install")
class TestFix607(NudgeCase, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.designer = new_test_user(
            cls.env, login="builder_f607", name="Raghad Designer", email="raghad@acme.example",
            groups="website.group_website_designer,nudge.group_nudge_user")
        cls.arabic = cls.env["res.lang"]._activate_lang("ar_001") or cls.env["res.lang"].search([("code", "=", "ar_001")])
        cls.english = cls.env["res.lang"].search([("code", "=", "en_US")])
        cls.env.company.write({"name": "Raghad Furniture", "email": "info@raghad.example", "phone": "+971 4 111 2222"})

    def built(self, template=None, website=None, reader="en_US", **values):
        website = website or self.env["website"].create({"name": "F607 site", "company_id": self.env.company.id})
        site = self.env["nudge.builder.site"].with_user(self.designer).create({
            "website_id": website.id, "kind": "furniture", "direction": "a",
            "lang_ids": [(6, 0, (self.english | self.arabic).ids)], **values})
        site.action_preview()
        site = site.with_context(lang=reader)
        template = template or library.load("generic")
        site.write({"template_json": template, "steps": site._plan(template), "step_index": 0, "progress": 0,
                    "log": False})
        while not site.action_build_step()["done"]:
            pass
        return site

    def home_arch(self, site, lang):
        home = site.page_ids.filtered(lambda page: page.key == "home")
        return home.view_id.with_context(lang=lang).arch_db or ""

    def with_figures(self, items=None):
        template = copy.deepcopy(library.load("generic"))
        block = next(b for b in template["pages"][0]["blocks"] if b["snippet"] == "s_numbers")
        block["figures"] = "given"
        if items is not None:
            for lang in block["copy"]:
                block["copy"][lang]["items"] = items[lang]
        return template

    # ---------------------------------------------------------------- 1. the numbers
    def test_a_site_without_real_figures_has_no_numbers_section_and_no_empty_counter(self):
        site = self.built()
        for lang in ("en_US", "ar_001"):
            arch = self.home_arch(site, lang)
            self.assertNotIn("s_numbers", arch, lang)
            self.assertNotIn("s_number", arch, lang)
            self.assertNotIn("10+", arch)
        self.assertNotIn("s_numbers", site.page_ids.filtered(lambda page: page.key == "home").snippet_keys)
        self.assertIn("no real figures were given", site.log)

    def test_a_site_with_real_figures_keeps_its_numbers_in_both_languages(self):
        site = self.built(template=self.with_figures())
        self.assertIn("s_numbers", site.page_ids.filtered(lambda page: page.key == "home").snippet_keys)
        for lang, figure in (("en_US", "200+"), ("ar_001", "+200")):
            arch = self.home_arch(site, lang)
            self.assertIn(figure, arch, lang)
            self.assertEqual(len(re.findall(r'<span class="s_number[^"]*">[^<]+</span>', arch)), 3, lang)

    def test_figures_with_one_empty_counter_are_left_out_whole(self):
        items = {"en": [{"number": "10+", "label": "Years"}, {"number": "", "label": "Customers"}],
                 "ar": [{"number": "+10", "label": "سنوات"}, {"number": "", "label": "عميل"}]}
        site = self.built(template=self.with_figures(items))
        self.assertNotIn("s_numbers", self.home_arch(site, "ar_001"))

    def test_a_counter_without_a_figure_is_never_drawn(self):
        section = compat.parse_section(compat.render_snippet(self.env, "s_numbers"))
        snippets.fill(section, "s_numbers", {"items": [{"label": "Satisfied"}]})
        self.assertIsNone(re.search(r'<span class="s_number[ "]', compat.to_string(section)))

    # ---------------------------------------------------------------- 2. the shop
    def shop_installed(self):
        return bool(self.env["ir.module.module"].search_count([("name", "=", "website_sale"), ("state", "=", "installed")]))

    def specific_cart_option(self, website):
        return self.env["ir.ui.view"].with_context(active_test=False).search([
            ("key", "=", "website_sale.header_hide_empty_cart_link"), ("website_id", "=", website.id)])

    def test_a_site_with_no_shop_has_no_shop_entry_and_a_cart_that_shows_when_filled_only(self):
        if not self.shop_installed():
            self.skipTest("website_sale is not installed here")
        other = self.env["website"].create({"name": "F607 other", "company_id": self.env.company.id})
        mine = self.env["website"].create({"name": "F607 mine", "company_id": self.env.company.id})
        self.assertIn("/shop", mine.menu_id.child_id.mapped("url"))
        self.built(website=mine, shop=False)
        self.assertNotIn("/shop", mine.menu_id.child_id.mapped("url"))
        self.assertTrue(self.specific_cart_option(mine).active)
        # no other website changes
        self.assertIn("/shop", other.menu_id.child_id.mapped("url"))
        self.assertFalse(self.specific_cart_option(other).filtered("active"))

    def test_a_website_with_orders_keeps_its_shop_entry_though_the_site_has_no_shop(self):
        if not self.shop_installed():
            self.skipTest("website_sale is not installed here")
        mine = self.env["website"].create({"name": "F607 orders", "company_id": self.env.company.id})
        partner = self.env["res.partner"].create({"name": "Raghad F607"})
        self.env["sale.order"].create({"partner_id": partner.id, "website_id": mine.id})
        self.built(website=mine, shop=False)
        self.assertIn("/shop", mine.menu_id.child_id.mapped("url"))

    def test_a_shop_site_keeps_its_shop_entry_and_its_cart(self):
        if not self.shop_installed():
            self.skipTest("website_sale is not installed here")
        mine = self.env["website"].create({"name": "F607 shop", "company_id": self.env.company.id})
        self.built(website=mine, shop=True)
        self.assertIn("/shop", mine.menu_id.child_id.mapped("url"))
        self.assertFalse(self.specific_cart_option(mine).filtered("active"))

    # ---------------------------------------------------------------- 3. one contact entry, one contact button
    def test_the_menu_has_one_contact_entry_the_websites_own(self):
        website = self.env["website"].create({"name": "F607 contact", "company_id": self.env.company.id})
        own = website.menu_id.child_id.filtered(lambda menu: menu.url == "/contactus")
        self.assertEqual(len(own), 1)
        site = self.built(website=website)
        contact = site.page_ids.filtered(lambda page: page.key == "contact")
        menus = website.menu_id.child_id
        self.assertFalse(menus.filtered(lambda menu: menu.url == "/contactus"))
        self.assertEqual(menus.filtered(lambda menu: menu.url == contact.url), own, "the website's own entry was reused")
        self.assertEqual(contact.menu_id, own)
        self.assertEqual(len(menus.filtered(lambda menu: menu.url == "/")), 1)

    def test_the_hero_has_one_contact_button(self):
        site = self.built()
        for lang in ("en_US", "ar_001"):
            arch = self.home_arch(site, lang)
            cover = arch[arch.index("s_cover"):]
            cover = cover[:cover.index("</section>")]
            self.assertEqual(len(re.findall(r'class="btn ', cover)), 1, lang)

    def test_a_second_button_that_goes_elsewhere_stays(self):
        section = compat.parse_section(compat.render_snippet(self.env, "s_cover"))
        buttons = section.xpath(snippets.BUTTON)
        if len(buttons) == 1:
            # 17's cover has one button of its own: its second, "Contact us", as 19 and 18 have it
            second = copy.deepcopy(buttons[0])
            second.set("href", "/contactus")
            buttons[0].addnext(second)
        snippets.fill(section, "s_cover", {"title": "T", "button": "Call", "button_url": "/somewhere"})
        self.assertEqual([button.get("href") for button in section.xpath(snippets.BUTTON)],
                         ["/somewhere", "/contactus"])

    # ---------------------------------------------------------------- 4. the language of the reader
    def test_the_pages_list_and_the_log_speak_the_readers_language(self):
        site = self.built(reader="ar_001")
        names = {page.key: page.name_shown for page in site.page_ids.with_context(lang="ar_001")}
        self.assertEqual(names["home"], "الرئيسية")
        self.assertTrue(re.search("[؀-ۿ]", names["legal_privacy"]), names["legal_privacy"])
        self.assertNotIn("Home", "".join(names.values()))
        self.assertTrue(all("Home" not in step["text"] for step in site.steps))
        self.assertTrue(any("الرئيسية" in step["text"] for step in site.steps))
        self.assertNotIn("page Home", site.log)
        # an English reader still sees English
        english = {page.key: page.name_shown for page in site.page_ids.with_context(lang="en_US")}
        self.assertEqual(english["home"], "Home")

    def test_the_closest_template_is_named_in_the_readers_language(self):
        def label(lang):
            selection = self.env["nudge.builder.site"].with_context(lang=lang).fields_get(["template_key"])
            return dict(selection["template_key"]["selection"])["generic"]
        self.assertEqual(label("ar_001"), "نشاط تجاري عام")
        self.assertEqual(label("en_US"), "General business")

    # ---------------------------------------------------------------- 5. the direction cards in RTL
    def test_the_direction_cards_fit_their_box_and_keep_the_direction_of_writing(self):
        self.patch(preview, "png_of", lambda document: None)
        direction = {"key": "a", "palette": {"primary": "#123456", "secondary": "#c9a227", "accent": "#e07a1f",
                                             "light": "#f6f4ef", "dark": "#14213d"},
                     "fonts": {"heading": "Cairo", "body": "Cairo"}}
        for rtl in (True, False):
            document = preview.card_document(direction, "شركة", "نص", "تواصل", ["أ", "ب", "ج"], rtl=rtl)
            card = preview.card_html(direction, "A", document)
            self.assertIn('dir="%s"' % ("rtl" if rtl else "ltr"), card)
            self.assertNotIn("width:360px", card)
            self.assertNotIn("zoom", card)
        site = self.env["nudge.builder.site"].with_user(self.designer).create({
            "website_id": self.env["website"].search([], limit=1).id, "kind": "furniture",
            "lang_ids": [(6, 0, self.arabic.ids)]})
        site.action_preview()
        self.assertIn('dir="rtl"', site.preview_html)
        self.assertNotIn("width:360px", site.preview_html)

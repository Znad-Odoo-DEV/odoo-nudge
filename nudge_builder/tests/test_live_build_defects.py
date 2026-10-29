"""The five defects of the live build of 2026-10-27 (Odoo 19 Enterprise): sample text and pictures of Odoo left in
the cards, the footer of the sample company, gradient pictures, project names the person did not allow, and the
filler sentence of the text-and-picture block."""
import base64
import io

from lxml import etree
from PIL import Image

from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge.tests.common import NudgeCase
from odoo.addons.nudge_builder.tools import compat, footer, library, neutral, pictures, snippets
from odoo.addons.nudge_builder.tools import copy as copywriter

SAMPLE_WORDS = ("Feature One", "Feature Two", "Feature Three", "Adapt these three columns", "Delete the above image",
                "To add a fourth column", "Start with the customer", "Write one or two paragraphs",
                "List and describe the key features")
SAMPLE_ABOUT = ("yourcompany", "mycompany", "555-555-5556", "Marina Blvd", "MyCompany", "My Company",
                "Documentation", "Marketplace", "Case Studies", "Our Company", "passionate people")
CARDS = {"en": {"title": "Why Raghad", "items": [
    {"title": "Fixed programme", "text": "A weekly report."},
    {"title": "Clear costs", "text": "Quantities approved first."},
    {"title": "Safe sites", "text": "Daily toolbox talks."}]},
    "ar": {"title": "لماذا رغد", "items": [
        {"title": "برنامج ملزم", "text": "تقرير أسبوعي."},
        {"title": "تكاليف واضحة", "text": "جداول كميات."},
        {"title": "مواقع آمنة", "text": "اجتماعات يومية."}]}}


def logo_bytes():
    image = Image.new("RGBA", (120, 60), (0, 0, 0, 0))
    for x in range(120):
        for y in range(60):
            image.putpixel((x, y), (200, 30, 40, 255))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


@tagged("post_install", "-at_install")
class TestSamplesLeftInSnippets(TransactionCase):
    """1 and 5: every snippet the builder places has its text filled; Odoo's sample text goes"""

    def section(self, key, lang="en_US"):
        return compat.parse_section(compat.render_snippet(self.env(context={"lang": lang}), key))

    def test_the_three_cards_take_the_text_of_the_template_and_lose_odoos_pictures(self):
        section = self.section("s_three_columns")
        snippets.fill(section, "s_three_columns", CARDS["en"])
        text = section.text_content()
        for word in SAMPLE_WORDS:
            self.assertNotIn(word, text)
        for title in ("Fixed programme", "Clear costs", "Safe sites", "A weekly report.", "Daily toolbox talks."):
            self.assertIn(title, text)
        # Odoo 19 nests a Card in a Card; Odoo 17 names no card, its cards are the class card
        cards = (section.xpath(".//div[@data-name='Card'][not(ancestor::div[@data-name='Card'])]")
                 or section.xpath(".//div[contains(concat(' ', normalize-space(@class), ' '), ' card ')]"))
        self.assertEqual(len(cards), 3)
        self.assertNotIn("s_three_columns_default_image", "".join(section.xpath(".//img/@src")))
        self.assertEqual(section.xpath(".//h2")[0].text, "Why Raghad")  # the block's own title leads the cards

    def test_the_three_cards_in_arabic_carry_no_default_text_either(self):
        self.env["res.lang"]._activate_lang("ar_001")
        samples = snippets.sample_terms(self.section("s_three_columns", "ar_001"))
        section = self.section("s_three_columns", "ar_001")
        snippets.fill(section, "s_three_columns", CARDS["ar"])
        shown = {" ".join(node.text_content().split()) for node in section.xpath(".//p|.//h1|.//h2|.//h3")}
        self.assertFalse(shown & samples, "Odoo's own words, in Arabic, stay on the page")
        self.assertIn("برنامج ملزم", section.text_content())

    def test_the_filler_sentence_under_the_text_goes(self):
        for lang in ("en_US", "ar_001"):
            self.env["res.lang"]._activate_lang("ar_001")
            section = self.section("s_text_image", lang)
            samples = snippets.sample_terms(section)
            snippets.fill(section, "s_text_image", {"title": "Two decades", "text": "We are licensed."})
            paragraphs = [" ".join(p.text_content().split()) for p in section.xpath(".//p") if not p.xpath(".//a")]
            self.assertEqual(paragraphs, ["We are licensed."], lang)
            self.assertFalse(set(paragraphs) & samples)
            self.assertNotIn("Start with the customer", section.text_content())

    def test_a_title_with_no_text_loses_the_sample_line_under_it(self):
        section = self.section("s_features")
        snippets.fill(section, "s_features", {"title": "What we do", "items": [{"title": "A", "text": "B"}]})
        self.assertNotIn("List and describe", section.text_content())
        self.assertFalse(section.xpath(".//p[contains(@class,'lead')]"))

    def test_an_edit_of_the_words_of_a_filled_section_keeps_what_it_has(self):
        section = self.section("s_text_image")
        snippets.fill(section, "s_text_image", {"title": "T", "text": "One."})
        extra = section.xpath(".//p")[0]
        more = section.makeelement("p", {})
        more.text = "Kept second paragraph"
        extra.addnext(more)
        snippets.fill(section, "s_text_image", {"title": "New"}, fresh=False)
        self.assertIn("Kept second paragraph", section.text_content())

    def test_a_block_that_shows_nothing_is_told(self):
        section = self.section("s_title")
        snippets.fill(section, "s_title", {})
        self.assertTrue(snippets.is_blank(section))
        section = self.section("s_title")
        snippets.fill(section, "s_title", {"title": "Our work"})
        self.assertFalse(snippets.is_blank(section))


@tagged("post_install", "-at_install")
class TestProjectNames(NudgeCase, TransactionCase):
    """4: "Show our project names" unchecked means no project is named, anywhere"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.designer = new_test_user(
            cls.env, login="builder_names", name="Raghad Designer", email="raghad@acme.example",
            groups="website.group_website_designer,nudge.group_nudge_user")
        cls.arabic = cls.env["res.lang"]._activate_lang("ar_001") or cls.env["res.lang"].search([("code", "=", "ar_001")])
        cls.english = cls.env["res.lang"].search([("code", "=", "en_US")])
        cls.website = cls.env["website"].search([], limit=1)

    def names(self):
        page = next(page for page in library.load("clinic")["pages"] if page["kind"] == "projects")
        found = {"en": [], "ar": []}
        for block in page["blocks"]:
            for code in found:
                title = (block["copy"].get(code) or {}).get("title")
                if title and block["snippet"] != "s_title":
                    found[code].append(title)
        return page, found

    def test_the_sample_projects_of_a_template_are_dropped_from_the_page(self):
        page, found = self.names()
        self.assertTrue(found["en"] and found["ar"])
        new = neutral.without_project_names(page)
        blocks = new["blocks"]
        self.assertEqual([block["snippet"] for block in blocks], ["s_title", "s_text_image"])
        text = str([block["copy"] for block in blocks])
        for title in found["en"] + found["ar"]:
            self.assertNotIn(title, text)
        self.assertIn("en", blocks[1]["copy"])
        self.assertIn("ar", blocks[1]["copy"])  # the general words exist in Arabic too
        home = library.load("contracting")["pages"][0]
        self.assertIs(neutral.without_project_names(home), home)

    def test_the_writer_is_told_not_to_name_projects(self):
        page, _found = self.names()
        self.assertIn("Never name a project", copywriter.SYSTEM)
        self.assertIn("Do not name or describe any single project", copywriter.prompt_for(
            "<data></data>", page, "English", "formal", allow_projects=False))
        self.assertNotIn("Do not name or describe any single project", copywriter.prompt_for(
            "<data></data>", page, "English", "formal", allow_projects=True))

    def build_projects(self, allow):
        self.patch(type(self.env["nudge.provider"]), "_get_default", lambda provider: provider.browse())
        self.patch(type(self.env["nudge.provider"]), "_get_for_mode", lambda provider, mode: provider.browse())
        site = self.env["nudge.builder.site"].with_user(self.designer).create({
            "website_id": self.website.id, "kind": "clinic", "template_key": "clinic",
            "lang_ids": [(6, 0, (self.english | self.arabic).ids)], "allow_projects": allow})
        template = library.load("clinic")
        template = {**template, "menu": ["projects"],
                    "pages": [page for page in template["pages"] if page["kind"] == "projects"]}
        pages = site._apply_template(template, kinds={"projects"})
        return pages.view_id

    def test_a_built_projects_page_names_no_sample_project_unless_allowed(self):
        _page, found = self.names()
        view = self.build_projects(False)
        for title in found["en"]:
            self.assertNotIn(title, view.with_context(lang="en_US").arch_db)
        for title in found["ar"]:
            self.assertNotIn(title, view.with_context(lang="ar_001").arch_db)
        self.assertIn("Our work", view.with_context(lang="en_US").arch_db)
        allowed = self.build_projects(True)
        self.assertIn(found["en"][0], allowed.with_context(lang="en_US").arch_db)
        self.assertIn(found["ar"][0], allowed.with_context(lang="ar_001").arch_db)


@tagged("post_install", "-at_install")
class TestFooterOfTheCompany(NudgeCase, TransactionCase):
    """2: the footer shows the company record, in each language, on the website's own copy only"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.designer = new_test_user(
            cls.env, login="builder_footer", name="Raghad Designer", email="raghad@acme.example",
            groups="website.group_website_designer,nudge.group_nudge_user")
        cls.arabic = cls.env["res.lang"]._activate_lang("ar_001") or cls.env["res.lang"].search([("code", "=", "ar_001")])
        cls.english = cls.env["res.lang"].search([("code", "=", "en_US")])
        cls.website = cls.env["website"].search([], limit=1)
        cls.env.company.write({"name": "SAUD SHEHATHA CONSTRUCTION L.L.C", "email": "info@saud.example",
                               "phone": "+971 4 557 7472"})
        cls.env.company.partner_id.write({"street": "TECOM", "city": "Dubai", "country_id": cls.env.ref("base.ae").id})

    def facts(self, **values):
        return {"name": "SAUD CO", "address_lines": ["TECOM", "Dubai", "United Arab Emirates"],
                "phone": "+971 4 557 7472", "email": "info@saud.example", "about": "General contractor.",
                "links": [("About us", "/about-us"), ("Services", "/services"), ("Contact", "/contactus")],
                "social": {}, "logo_url": "/web/image/website/1/logo", **values}

    def footer_views(self):
        keys = ["website.template_footer_" + name for name in compat.FOOTER_FAMILY]
        return [self.env.ref(key) for key in keys if self.env.ref(key, raise_if_not_found=False)]

    def test_no_footer_layout_keeps_the_sample_company(self):
        views = self.footer_views()
        self.assertTrue(views)
        for view in views:
            out = footer.personalise(view.arch_db, self.facts())
            for sample in SAMPLE_ABOUT:
                self.assertNotIn(sample, out, view.key)

    def test_every_header_keeps_the_nodes_other_modules_hang_on(self):
        """website_sale puts its cart before the search box (default) or the social links (stretch) of the header:
        a header written without those t-call nodes broke the update and the footer went back to Odoo's"""
        for name in ("default", "stretch", "search"):
            view = self.env.ref(f"website.template_header_{name}", raise_if_not_found=False)
            if not view:
                continue
            calls = set(etree.fromstring(view.arch_db.encode()).xpath("//@t-call"))
            out = footer.personalise(view.arch_db, self.facts())
            self.assertEqual(set(etree.fromstring(out.encode()).xpath("//@t-call")), calls, view.key)

    def test_the_company_is_where_the_sample_was(self):
        view = self.env.ref("website.template_footer_descriptive", raise_if_not_found=False)
        if not view:
            self.skipTest("this version has no descriptive footer")
        out = footer.personalise(view.arch_db, self.facts())
        self.assertIn("mailto:info@saud.example", out)
        self.assertIn("tel:+97145577472", out)
        self.assertIn("SAUD CO", out)
        self.assertIn("General contractor.", out)
        out = footer.personalise(view.arch_db, self.facts(phone="", email=""))
        self.assertNotIn("tel:", out)
        self.assertNotIn("mailto:", out)

    def test_the_links_columns_list_the_menu_and_the_unset_social_accounts_go(self):
        view = self.env.ref("website.template_footer_links", raise_if_not_found=False)
        if not view:
            self.skipTest("this version has no links footer")
        out = footer.personalise(view.arch_db, self.facts(social={"facebook": True}))
        for url in ("/about-us", "/services", "/contactus"):
            self.assertIn(f'href="{url}"', out)
        self.assertIn("/website/social/facebook", out)
        self.assertNotIn("/website/social/instagram", out)

    def test_the_footer_of_the_site_shows_the_company_in_each_language_and_no_other_website_changes(self):
        site = self.env["nudge.builder.site"].with_user(self.designer).create({
            "website_id": self.website.id, "kind": "contracting", "template_key": "contracting", "direction": "b",
            "lang_ids": [(6, 0, (self.english | self.arabic).ids)]})
        site._set_footer(library.load("contracting"))
        View = self.env["ir.ui.view"].with_context(active_test=False)
        key = "website.template_footer_links"  # direction B: header stretch, footer links
        if not self.env.ref(key, raise_if_not_found=False):
            self.skipTest("this version has no links footer")
        specific = View.search([("key", "=", key), ("website_id", "=", self.website.id)])
        self.assertEqual(len(specific), 1, "the footer is the website's own copy")
        for lang in ("en_US", "ar_001"):
            arch = specific.with_context(lang=lang).arch_db
            self.assertIn("info@saud.example", arch, lang)
            self.assertIn("SAUD SHEHATHA CONSTRUCTION L.L.C", arch, lang)
            for sample in SAMPLE_ABOUT:
                self.assertNotIn(sample, arch, f"{lang}: {sample}")
        self.assertNotIn("info@saud.example", self.env.ref(key).arch_db, "the generic footer is untouched")
        other = self.env["website"].create({"name": "Other", "company_id": self.env.company.id})
        self.assertFalse(View.search([("key", "=", key), ("website_id", "=", other.id)]))
        copyright_view = View.search([("key", "=", "website.footer_copyright_company_name"),
                                      ("website_id", "=", self.website.id)])
        if copyright_view:
            self.assertIn("Copyright", copyright_view.with_context(lang="en_US").arch_db)
            self.assertIn("حقوق النشر", copyright_view.with_context(lang="ar_001").arch_db)
            self.assertIn("SAUD SHEHATHA CONSTRUCTION L.L.C", copyright_view.with_context(lang="ar_001").arch_db)


@tagged("post_install", "-at_install")
class TestPictures(NudgeCase, TransactionCase):
    """3: no gradient city where the company's own logo can stand"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.designer = new_test_user(
            cls.env, login="builder_pictures", name="Raghad Designer", email="raghad@acme.example",
            groups="website.group_website_designer,nudge.group_nudge_user")
        cls.website = cls.env["website"].search([], limit=1)

    def site(self):
        self.patch(type(self.env["nudge.provider"]), "_get_default", lambda provider: provider.browse())
        return self.env["nudge.builder.site"].with_user(self.designer).create({
            "website_id": self.website.id, "kind": "company", "color_light": "#f5f6f8"})

    def test_the_logo_card_is_the_logo_on_the_light_colour(self):
        card = pictures.brand_card(logo_bytes(), {"light": "#f5f6f8"})
        with Image.open(io.BytesIO(card)) as image:
            self.assertEqual(image.size, (1600, 900))
            self.assertEqual(image.getpixel((5, 5)), (245, 246, 248))
            self.assertEqual(image.getpixel((800, 450))[:3], (200, 30, 40))
        self.assertIsNone(pictures.brand_card(b"not a picture", {"light": "#f5f6f8"}))

    def test_a_picture_beside_a_text_is_the_logo_card_and_the_cover_keeps_the_sector_drawing(self):
        site = self.site()
        self.env.company.logo = base64.b64encode(logo_bytes())
        self.assertTrue(site._picture_for("x", "building", side=True).name.endswith("logo.png"))
        self.assertTrue(site._picture_for("x", "building").name.endswith("building.jpg"))
        spec = {"key": "p"}
        side = site._spec_picture(spec, {"snippet": "s_text_image", "image": "building", "copy": {"en": {}}})
        hero = site._spec_picture(spec, {"snippet": "s_cover", "image": "building", "copy": {"en": {}}})
        self.assertNotEqual(side, hero)

    def test_without_a_logo_the_sector_placeholder_stands_in(self):
        site = self.site()
        self.env.company.logo = False
        self.assertTrue(site._picture_for("x", "building", side=True).name.endswith("building.jpg"))


@tagged("post_install", "-at_install")
class TestLanguages(NudgeCase, TransactionCase):
    """6 and 7: the languages of the interview are the languages of the website, and the tool takes the ones named"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.designer = new_test_user(
            cls.env, login="builder_langs", name="Raghad Designer", email="raghad@acme.example",
            groups="website.group_website_designer,nudge.group_nudge_user")
        cls.arabic = cls.env["res.lang"]._activate_lang("ar_001") or cls.env["res.lang"].search([("code", "=", "ar_001")])
        cls.english = cls.env["res.lang"].search([("code", "=", "en_US")])
        cls.website = cls.env["website"].search([], limit=1)

    def test_the_languages_chosen_become_languages_of_the_website_and_none_is_taken_away(self):
        self.website.write({"language_ids": [(6, 0, self.english.ids)]})
        site = self.env["nudge.builder.site"].with_user(self.designer).create({
            "website_id": self.website.id, "kind": "company", "lang_ids": [(6, 0, self.arabic.ids)]})
        site._add_languages()
        self.assertEqual(set(self.website.language_ids.ids), set((self.english | self.arabic).ids))
        site._add_languages()  # twice changes nothing
        self.assertEqual(len(self.website.language_ids), 2)

    def test_the_words_of_a_language_in_both_languages(self):
        from odoo.addons.nudge_builder.tools.builder_tool import languages_named
        self.assertEqual(languages_named("اعملّي موقع لشركتي، بالعربي والإنجليزي"), ["ar", "en"])
        for text in ("عربي", "العربي", "بالعربي", "Arabic"):
            self.assertEqual(languages_named(text), ["ar"], text)
        for text in ("إنجليزي", "انجليزي", "English", "بالانجليزي"):
            self.assertEqual(languages_named(text), ["en"], text)
        self.assertEqual(languages_named("a website"), [])

    def test_the_tool_preselects_the_languages_named(self):
        from odoo.addons.nudge.tools import base
        tool = base.TOOLS["start_website_builder"]
        env = self.env(user=self.designer)
        result = tool.run(env, {"business": "شركة مقاولات", "text": "اعملّي موقع بالعربي والإنجليزي"}, None)
        ids = result.event["action"]["context"]["default_lang_ids"][0][2]
        self.assertEqual(set(ids), set((self.english | self.arabic).ids))
        result = tool.run(env, {"business": "contractor", "text": "a site in Arabic"}, None)
        self.assertEqual(result.event["action"]["context"]["default_lang_ids"][0][2], self.arabic.ids)
        result = tool.run(env, {"text": "a site"}, None)  # none named: the person's language and English
        ids = set(result.event["action"]["context"]["default_lang_ids"][0][2])
        self.assertIn(self.english.id, ids)

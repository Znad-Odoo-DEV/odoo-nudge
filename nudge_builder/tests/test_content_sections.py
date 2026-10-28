import json
import os

from lxml import etree

from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge_builder.tools import content_sections as cs
from odoo.addons.nudge_builder.tools import copy as copywriter
from odoo.addons.nudge_builder.tools import footer, neutral

PICTURES = [{"url": "/web/image/5", "alt": "Front"}, {"url": "/web/image/6", "alt": "Back"}]
MAP = "https://www.google.com/maps?q=Dubai&output=embed"
SAMPLE = {
    "project": [{"name": "Villa Palm", "summary": "A villa.", "pictures": PICTURES, "location": "Dubai", "year": "2025",
                 "status": "done", "price_text": "1,000,000 AED", "listing_status": "sale", "bedrooms": 4,
                 "before_url": "/web/image/7", "after_url": "/web/image/8"},
                {"name": "Tower", "description": "<p>Line one</p><p>Line two</p>"}],
    "product": [{"name": "Burger", "summary": "Beef.", "price_text": "30 AED", "pictures": PICTURES[:1]}],
    "service": [{"name": "Plumbing", "summary": "Pipes.", "icon": "fa fa-wrench", "quote": True}],
    "member": [{"name": "Dr Sara", "role": "Doctor", "specialty": "Dental", "description": "Bio.",
                "linkedin": "https://www.linkedin.com/in/sara", "pictures": PICTURES[:1]}],
    "testimonial": [{"author": "Ali", "company": "Acme", "summary": "Great.", "rating": 5}],
    "faq": [{"question": "How long?", "answer": "<p>Two weeks.</p>"}, {"question": "Where?", "answer": "Dubai."}],
    "location": [{"name": "HQ", "address": "Street 1, Dubai", "maps_embed_src": MAP, "is_main": True,
                  "phone": "+971 4 123 4567", "whatsapp": "+971501234567", "hours": "Sun-Thu 9-5"}],
}


def xml(section):
    return etree.tostring(section, encoding="unicode")


@tagged("post_install", "-at_install")
class TestContentSections(TransactionCase):
    """The section renderers, the template blocks and the build of a page of records"""

    # ------------------------------------------------------------------ the renderers
    def test_every_layout_of_every_kind_renders_in_english_and_arabic(self):
        for kind, layouts in cs.LAYOUTS.items():
            for layout in layouts:
                for rtl in (False, True):
                    section = cs.render(kind, SAMPLE[kind], layout, "a", "Title", "Lead", {"rtl": rtl, "quote_url": "/q"})
                    self.assertIsNotNone(section, (kind, layout))
                    out = xml(section)
                    self.assertIn("Title", out)
                    self.assertEqual("nb-rtl" in section.get("class"), rtl, (kind, layout))
                    self.assertEqual(section.get("dir"), "rtl" if rtl else None)
                    self.assertEqual(section.get("data-nb-layout"), layout)
                    for image in section.iter("img"):
                        self.assertEqual(image.get("loading"), "lazy")
                        self.assertTrue(image.get("src").startswith("/web/image/"))

    def test_arabic_words_of_a_record_appear_as_written(self):
        section = cs.render("project", [{"name": "فيلا النخلة", "summary": "فيلا فاخرة", "location": "دبي"}],
                            "grid", cx={"rtl": True})
        out = xml(section)
        for word in ("فيلا النخلة", "فيلا فاخرة", "دبي"):
            self.assertIn(word, out)

    def test_a_kind_with_no_records_is_not_placed(self):
        for kind in cs.LAYOUTS:
            self.assertIsNone(cs.render(kind, [], None, "a"), kind)
            self.assertIsNone(cs.render(kind, [{}, {"summary": "no name"}], None, "a"), kind)
        self.assertIsNone(cs.render_contact({}))
        self.assertIsNone(cs.render_contact({"whatsapp": "12"}))
        self.assertIsNone(cs.render_hours(""))
        self.assertIsNone(cs.render_quote(""))
        self.assertIsNone(cs.render_quote("javascript:alert(1)"))

    def test_featured_records_come_first(self):
        items = [{"name": "Plain"}, {"name": "Star", "featured": True}]
        out = xml(cs.render("service", items, "list"))
        self.assertLess(out.index("Star"), out.index("Plain"))

    def test_the_direction_picks_the_layout_unless_the_template_names_one(self):
        for direction, index in (("a", 0), ("b", 1), ("c", 2)):
            section = cs.render("project", SAMPLE["project"], None, direction)
            self.assertEqual(section.get("data-nb-layout"), cs.LAYOUTS["project"][index])
        self.assertEqual(cs.render("project", SAMPLE["project"], "carousel", "a").get("data-nb-layout"), "carousel")
        self.assertEqual(cs.render("project", SAMPLE["project"], "nonsense", "b").get("data-nb-layout"), "showcase")

    def test_text_is_escaped_and_bad_addresses_are_dropped(self):
        evil = {"name": "<script>alert(1)</script>Bad", "summary": "<img src=x onerror=alert(1)>",
                "pictures": [{"url": "javascript:alert(1)", "alt": '"><script>'}, {"url": "https://x.example/a.png"}],
                "linkedin": "javascript:alert(2)"}
        for kind in ("project", "member", "service"):
            out = xml(cs.render(kind, [evil], "grid"))
            for bad in ("<script", "onerror", "javascript:"):
                self.assertNotIn(bad, out, kind)
        # the alt text is an attribute value: quotes cannot leave it
        picture = [{"url": "/web/image/9", "alt": '"><script>x</script>'}]
        section = cs.render("project", [{"name": "A", "pictures": picture}], "grid")
        self.assertEqual(len(section.xpath(".//script")), 0)

    def test_whatsapp_link_per_language(self):
        english = cs.whatsapp_url("+971 50 123 4567", "Hello, I saw your website")
        arabic = cs.whatsapp_url("+971501234567", "مرحبا، رأيت موقعكم")
        self.assertEqual(english, "https://wa.me/971501234567?text=Hello%2C%20I%20saw%20your%20website")
        self.assertTrue(arabic.startswith("https://wa.me/971501234567?text=%D9%85"))
        self.assertEqual(cs.whatsapp_url("971501234567"), "https://wa.me/971501234567")
        for bad in ("", "123", "0501234567", "abc", None, "1" * 16):
            self.assertEqual(cs.whatsapp_url(bad, "x"), "", bad)
        out = xml(cs.render_contact({"whatsapp": "+971501234567", "phone": "+971 4 123"},
                                    cx={"whatsapp_text": "مرحبا"}))
        self.assertIn("https://wa.me/971501234567?text=%D9%85", out)
        self.assertIn('href="tel:+9714123"', out)

    def test_contact_block_has_phone_email_socials_booking_hours(self):
        section = cs.render_contact({
            "phone": "+971 4 123 4567", "email": "a@b.example", "booking_url": "https://cal.example/x",
            "hours": "Sun-Thu 9-5\nFri closed",
            "socials": [("facebook", "https://facebook.com/x"), ("evil", "javascript:1"), ("Bad Net", "https://x.example")]})
        hrefs = section.xpath(".//a/@href")
        self.assertIn("mailto:a@b.example", hrefs)
        self.assertIn("https://cal.example/x", hrefs)
        self.assertIn("https://facebook.com/x", hrefs)
        self.assertEqual(len(section.xpath(".//p[contains(@class,'nb-hours')]")), 2)
        self.assertFalse([h for h in hrefs if h.startswith("javascript")])
        for link in section.xpath(".//a[@target='_blank']"):
            self.assertIn("noopener", link.get("rel"))

    def test_map_frame_is_lazy_sandboxed_and_only_google_or_osm(self):
        out = cs.render("location", SAMPLE["location"], "list_map")
        frame = out.xpath(".//iframe")[0]
        self.assertEqual(frame.get("loading"), "lazy")
        self.assertIn("sandbox", frame.attrib)
        self.assertEqual(frame.get("src"), MAP)
        osm = "https://www.openstreetmap.org/export/embed.html?bbox=1,2,3,4"
        self.assertEqual(cs.map_src(osm), osm)
        for bad in ("https://evil.example/maps", "javascript:alert(1)", "http://www.google.com/maps?q=x", ""):
            self.assertEqual(cs.map_src(bad), "", bad)
            item = dict(SAMPLE["location"][0], maps_embed_src=bad)
            self.assertFalse(cs.render("location", [item], "list_map").xpath(".//iframe"))
        # an address with quotes and tags cannot leave the attribute
        tricky = dict(SAMPLE["location"][0], name='"><script>x</script>')
        out = xml(cs.render("location", [tricky], "cards"))
        self.assertNotIn("<script", out)

    def test_floating_button_only_with_a_valid_number_and_flips_for_rtl(self):
        self.assertIsNone(cs.whatsapp_float("", "x"))
        self.assertIsNone(cs.whatsapp_float("12", "x"))
        button = cs.whatsapp_float("+971501234567", "Hi", {"rtl": True})
        self.assertIn("nb-rtl", button.get("class"))
        self.assertEqual(button.xpath(".//a/@href"), ["https://wa.me/971501234567?text=Hi"])
        self.assertNotIn("nb-rtl", cs.whatsapp_float("+971501234567", "Hi", {"rtl": False}).get("class"))

    def test_quote_link_only_where_the_site_has_the_form(self):
        with_quote = cs.render("service", SAMPLE["service"], "grid", cx={"quote_url": "/request-a-quote"})
        self.assertIn("/request-a-quote", with_quote.xpath(".//a/@href"))
        without = cs.render("service", SAMPLE["service"], "grid", cx={})
        self.assertFalse(without.xpath(".//a[contains(@class,'btn')]"))
        self.assertIsNotNone(cs.render_quote("/request-a-quote"))

    def test_accordion_ids_are_unique_and_the_first_answer_is_open(self):
        section = cs.render("faq", SAMPLE["faq"], "accordion", uid="x1")
        ids = section.xpath(".//@id")
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(section.xpath(".//div[contains(@class,'accordion-collapse') and contains(@class,'show')]")), 1)
        other = cs.render("faq", SAMPLE["faq"], "accordion", uid="x2")
        self.assertFalse(set(ids) & set(other.xpath(".//@id")))

    def test_labels_are_translated_by_the_caller(self):
        section = cs.render("project", [{"name": "A", "status": "done"}], "grid", cx={"labels": {"done": "مكتمل"}})
        self.assertIn("مكتمل", xml(section))

    # ------------------------------------------------------------------ templates, copy, footer
    def test_template_blocks_of_records_are_known_and_declared(self):
        folder = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "templates")
        for name in sorted(os.listdir(folder)):
            if name.endswith(".json"):
                with open(os.path.join(folder, name), encoding="utf8") as handle:
                    self.check_template(json.load(handle))

    def check_template(self, data):
        kinds = set(data.get("kinds") or [])
        for page in data["pages"]:
            for block in page["blocks"]:
                kind = block.get("kind")
                if not kind:
                    continue
                label = (data["key"], page["key"], kind)
                self.assertTrue(kind in cs.LAYOUTS or kind in ("contact", "hours", "quote"), label)
                if kind in cs.LAYOUTS:
                    self.assertIn(kind, kinds, label)
                if block.get("layout"):
                    self.assertIn(block["layout"], cs.LAYOUTS.get(kind, ()), label)
                self.assertEqual(set(block["copy"]), {"en", "ar"}, label)
                self.assertEqual(set(block["copy"]["en"]), set(block["copy"]["ar"]), label)

    def test_every_template_shows_its_kinds(self):
        folder = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "templates")
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(folder, name), encoding="utf8") as handle:
                data = json.load(handle)
            used = {block.get("kind") for page in data["pages"] for block in page["blocks"]}
            for kind in data.get("kinds") or []:
                if data.get("shop") and kind == "product":
                    continue  # a shop's products are the Website Sale ones
                self.assertIn(kind, used, (name, kind))

    def test_the_writer_never_writes_a_block_of_records(self):
        spec = {"key": "p", "name": {"en": "P"}, "blocks": [
            {"snippet": "s_title", "copy": {"en": {"title": "T"}}},
            {"snippet": "s_text_block", "kind": "project", "copy": {"en": {}}}]}
        prompt = copywriter.prompt_for("Company", spec, "English", "friendly", records="Projects: Villa Palm")
        self.assertIn('"index": 0', prompt)
        self.assertNotIn('"index": 1', prompt)
        self.assertIn("Villa Palm", prompt)
        self.assertEqual(copywriter.records_text({}), "")
        self.assertIn("Villa Palm", copywriter.records_text({"project": [{"name": "Villa Palm"}]}))

    def test_a_project_page_with_record_blocks_keeps_them_when_names_are_hidden(self):
        spec = {"key": "projects", "kind": "projects", "name": {"en": "P"}, "blocks": [
            {"snippet": "s_title", "copy": {"en": {"title": "P"}}},
            {"snippet": "s_text_block", "kind": "project", "copy": {"en": {}}}]}
        self.assertEqual(neutral.without_project_names(spec)["blocks"], spec["blocks"])

    def test_the_footer_gets_a_whatsapp_link_once(self):
        arch = '<div><ul><li><a href="tel:+9714123">Call</a></li></ul></div>'
        facts = {"name": "Co", "whatsapp_url": "https://wa.me/971501234567", "whatsapp_label": "WhatsApp"}
        once = footer.personalise(arch, facts)
        self.assertIn("https://wa.me/971501234567", once)
        twice = footer.personalise(once, facts)
        self.assertEqual(twice.count("wa.me"), 1)
        self.assertNotIn("wa.me", footer.personalise(arch, {"name": "Co"}))

    # ------------------------------------------------------------------ the build
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.designer = new_test_user(
            cls.env, login="builder_sections", name="Raghad Designer", email="raghad@acme.example",
            groups="website.group_website_designer,nudge.group_nudge_user")
        cls.arabic = cls.env["res.lang"]._activate_lang("ar_001") or cls.env["res.lang"].search([("code", "=", "ar_001")])
        cls.english = cls.env["res.lang"].search([("code", "=", "en_US")])
        cls.website = cls.env["website"].search([], limit=1)

    def site(self, **values):
        values = {"website_id": self.website.id, "kind": "company", "direction": "a",
                  "lang_ids": [(6, 0, (self.english | self.arabic).ids)], **values}
        site = self.env["nudge.builder.site"].with_user(self.designer).create(values)
        return site

    SPEC = {"key": "projects", "kind": "projects", "name": {"en": "Projects", "ar": "مشاريع"}, "blocks": [
        {"snippet": "s_title", "copy": {"en": {"title": "Our projects"}, "ar": {"title": "مشاريعنا"}}},
        {"snippet": "s_text_block", "kind": "project", "layout": "grid", "copy": {"en": {}, "ar": {}}}]}

    def test_a_page_of_records_with_no_records_is_not_built(self):
        site = self.site()
        before = site.page_ids
        record = site._build_page(self.SPEC)
        self.assertFalse(record)
        self.assertEqual(site.page_ids, before)
        self.assertIn("No project records", site.log)

    def test_a_page_of_records_shows_the_records_in_both_languages(self):
        if "nudge.builder.project" not in self.env:
            self.skipTest("the content records are not installed")
        site = self.site()
        project = self.env["nudge.builder.project"].with_user(self.designer).create({
            "site_id": site.id, "name": "Villa Palm", "location": "Dubai", "summary": "A villa."})
        project.with_context(lang="ar_001").write({"name": "فيلا النخلة", "summary": "فيلا فاخرة"})
        self.env["nudge.builder.project"].with_user(self.designer).create({
            "site_id": site.id, "name": "Hidden", "published": False})
        record = site._build_page(self.SPEC)
        self.assertTrue(record)
        english = record.view_id.with_context(lang="en_US").arch_db
        arabic = record.view_id.with_context(lang="ar_001").arch_db
        self.assertIn("Villa Palm", english)
        self.assertNotIn("Hidden", english)
        self.assertIn("فيلا النخلة", arabic)
        self.assertIn("Dubai", english)
        self.assertIn("s_nb_project", english)

    def test_the_floating_button_is_on_the_pages_only_when_asked(self):
        if "whatsapp_float" not in self.env["nudge.builder.site"]._fields:
            self.skipTest("the contact fields are not installed")
        spec = {"key": "hello", "kind": "services", "name": {"en": "Hello", "ar": "مرحبا"}, "blocks": [
            {"snippet": "s_title", "copy": {"en": {"title": "Hello"}, "ar": {"title": "مرحبا"}}}]}
        asked = self.site(whatsapp="+971501234567", whatsapp_float=True)
        page = asked._build_page(spec)
        self.assertIn("nb-wa-float", page.view_id.with_context(lang="en_US").arch_db)
        self.assertIn("wa.me/971501234567", page.view_id.with_context(lang="ar_001").arch_db)
        quiet = self.site(whatsapp="+971501234567", whatsapp_float=False)
        self.assertNotIn("nb-wa-float", quiet._build_page(spec).view_id.with_context(lang="en_US").arch_db)
        self.assertNotIn("nb-wa-float", self.site()._build_page(spec).view_id.arch_db)

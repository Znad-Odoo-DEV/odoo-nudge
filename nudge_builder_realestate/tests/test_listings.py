from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

from odoo.addons.nudge_builder.tools import checks, library

from ..tools import checks as listing_checks, listings


def listing(site, name, **values):
    return {"site_id": site.id, "name": name, "listing_status": "sale", "location": "Dubai", "category": "Villa",
            "bedrooms": 3, "price": 1200000, **values}


class ListingsCase:
    @classmethod
    def make_site(cls):
        cls.website = cls.env["website"].search([], limit=1)
        cls.site = cls.env["nudge.builder.site"].create({
            "name": "Raghad Realty", "website_id": cls.website.id, "template_key": "real_estate_listings",
            "state": "built"})
        Project = cls.env["nudge.builder.project"]
        cls.villa = Project.create(listing(cls.site, "Palm Villa"))
        cls.flat = Project.create(listing(
            cls.site, "Marina Flat", listing_status="rent", location="Abu Dhabi", category="Apartment", bedrooms=1,
            price=80000))
        cls.sold = Project.create(listing(cls.site, "Sold Tower", listing_status="sold"))
        cls.hidden = Project.create(listing(cls.site, "Hidden Villa", published=False))
        cls.finished = Project.create({"site_id": cls.site.id, "name": "Finished Mall"})  # a project, not a listing


@tagged("post_install", "-at_install")
class TestRealEstatePlugin(ListingsCase, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.make_site()
        cls.designer = new_test_user(
            cls.env, login="listing_designer", name="Raghad Designer", email="raghad@acme.example",
            groups="website.group_website_designer,nudge.group_nudge_user")

    def paths(self):
        return self.env["nudge.builder.plugin"].plugin_template_paths()

    def test_the_plugin_is_registered(self):
        Plugin = self.env["nudge.builder.plugin"]
        self.assertIn(listings.KIND, Plugin.plugin_kinds())
        self.assertIn(listing_checks.GROUP, [check["group"] for check in Plugin.plugin_checks()])
        self.assertIn("real_estate_listings", library.keys(self.paths()))
        self.assertEqual(library.load("real_estate_listings", self.paths())["key"], "real_estate_listings")
        self.assertIn("real_estate_listings", dict(self.env["nudge.builder.site"]._template_selection()))
        self.assertTrue(self.site.listing_mode)
        self.assertIn(listings.KIND, self.site.kinds_used)

    def test_only_published_listings_for_sale_or_rent_are_the_records(self):
        self.assertEqual(self.site._kind_records(listings.KIND), self.villa | self.flat)
        self.assertEqual(self.site.content_records(listings.KIND), self.villa | self.flat)

    def test_the_page_has_a_search_bar_drawn_from_the_listings(self):
        template = library.load("real_estate_listings", self.paths())
        spec = next(page for page in template["pages"] if page["key"] == "projects")
        page = self.site.with_user(self.designer)._build_page(spec)
        arch = page.view_id.with_context(lang="en_US").arch_db
        self.assertIn('action="/listings"', arch)
        self.assertIn('method="get"', arch)
        for field in listings.FIELDS:
            self.assertIn('name="%s"' % field, arch)
        for fact in ("Palm Villa", "Marina Flat", "Abu Dhabi", "Dubai", "Apartment", "Villa"):
            self.assertIn(fact, arch)
        for hidden in ("Sold Tower", "Hidden Villa", "Finished Mall"):
            self.assertNotIn(hidden, arch)
        self.assertIn("Properties for sale and rent", arch)
        self.assertNotIn("<script", arch)

    def test_with_no_listing_the_section_is_not_placed(self):
        site = self.env["nudge.builder.site"].create({"name": "Empty", "website_id": self.website.id})
        spec = {"key": "projects", "kind": "projects", "name": {"en": "Listings", "ar": "x"}, "blocks": [
            {"snippet": "s_text_block", "kind": listings.KIND, "copy": {"en": {"title": "Listings"}, "ar": {}}}]}
        self.assertFalse(site.with_user(self.designer)._build_page(spec))
        self.assertIn("No listing records", site.log)

    def test_the_site_check_names_what_a_listing_lacks(self):
        self.flat.price = 0
        self.flat.location = False
        env = self.env(user=self.designer)
        found, skipped = checks.check_site(env, self.website.with_env(env), groups=[listing_checks.GROUP])
        self.assertFalse(skipped)
        keys = {item["key"]: item for item in found}
        self.assertEqual(keys["listings_no_price"]["ids"], [self.flat.id])
        self.assertEqual(keys["listings_no_city"]["ids"], [self.flat.id])
        self.assertEqual(keys["listings_no_picture"]["count"], 2)
        self.assertIn("1 published listings have no price.", checks.finding_text(env, "listings_no_price", 1))
        self.assertEqual(checks.group_labels(env)[listing_checks.GROUP], "Listings")


@tagged("post_install", "-at_install")
class TestListingsPage(ListingsCase, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.make_site()

    def names(self, **query):
        page = self.url_open("/listings", params=query)
        self.assertEqual(page.status_code, 200)
        return page.text, [name for name in ("Palm Villa", "Marina Flat", "Sold Tower", "Hidden Villa", "Finished Mall")
                           if name in page.text]

    def test_no_filter_shows_every_published_listing_and_nothing_else(self):
        text, names = self.names()
        self.assertEqual(names, ["Palm Villa", "Marina Flat"])
        self.assertIn('name="price_min"', text)

    def test_each_filter_narrows_the_listings(self):
        self.assertEqual(self.names(offer="rent")[1], ["Marina Flat"])
        self.assertEqual(self.names(offer="sale")[1], ["Palm Villa"])
        self.assertEqual(self.names(type="Apartment")[1], ["Marina Flat"])
        self.assertEqual(self.names(city="Dubai")[1], ["Palm Villa"])
        self.assertEqual(self.names(bedrooms="2")[1], ["Palm Villa"])
        self.assertEqual(self.names(price_max="100000")[1], ["Marina Flat"])
        self.assertEqual(self.names(price_min="100000")[1], ["Palm Villa"])
        self.assertEqual(self.names(price_min="50000", price_max="90000", offer="rent")[1], ["Marina Flat"])
        text, names = self.names(city="Dubai", offer="rent")
        self.assertEqual(names, [])
        self.assertIn("No listing matches these filters.", text)

    def test_the_choice_stays_in_the_bar(self):
        text, _names = self.names(city="Dubai", price_max="2000000")
        self.assertIn('value="2000000.0"', text)
        self.assertIn('selected="selected"', text)

    def test_what_is_not_a_listing_choice_is_ignored_and_never_echoed(self):
        for query in ({"offer": "sold"}, {"offer": "<script>alert(1)</script>"}, {"city": "<script>alert(1)</script>"},
                      {"type": "x" * 500}, {"bedrooms": "-1"}, {"bedrooms": "abc"}, {"bedrooms": "99"},
                      {"price_min": "nan"}, {"price_max": "inf"}, {"price_min": "<b>1</b>"}, {"price_min": "-5"},
                      {"price_max": "1e400"}):
            text, names = self.names(**query)
            self.assertEqual(names, ["Palm Villa", "Marina Flat"], query)
            self.assertNotIn("<script>alert(1)", text, query)
        self.assertEqual(self.url_open("/listings?city=a&city=b").status_code, 200)

    def test_without_a_listing_the_page_is_not_found(self):
        self.env["nudge.builder.project"].search([("site_id", "=", self.site.id)]).unlink()
        self.assertEqual(self.url_open("/listings").status_code, 404)

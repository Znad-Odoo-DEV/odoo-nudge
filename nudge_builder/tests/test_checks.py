import base64
from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..tools import checks


@tagged("post_install", "-at_install")
class TestSiteChecks(TransactionCase):
    """The site check: each check on a seeded site, the fixes behind a card, the rights"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.website = cls.env["website"].search([], limit=1)
        cls.designer = new_test_user(cls.env, login="raghad_designer", groups="base.group_user,website.group_website_designer" + (",product.group_product_manager" if cls.env.ref("product.group_product_manager", False) else ""))
        cls.plain = new_test_user(cls.env, login="ebrahim_plain", groups="base.group_user")
        big = cls.env["ir.attachment"].create({
            "name": "heavy-photo.jpg", "datas": base64.b64encode(b"x" * (600 * 1024)), "public": True})
        cls.big = big
        sections = "".join('<section data-snippet="s_text_block"><p>Block %s</p></section>' % i for i in range(25))
        cls.bad = checks.new_page(cls.env, cls.website, "Raghad", "/t-bad", (
            '<h1>One</h1><h1>Two</h1>'
            '<p>Raghad builds quiet and honest houses for families who want them to last for generations.</p>'
            '<img src="/web/image/%s/heavy-photo.jpg"/><img src="/static/img/team-photo_1.jpg"/>'
            '<a href="/nope-xyz">Missing</a><a href="/t-good">Good</a><a href="https://example.org/a">Out</a>'
            '<form><input type="text" name="q"/><input type="text" id="named" name="n"/>'
            '<label for="named">Named</label></form>'
            '<script>var a = 1;</script>%s') % (big.id, sections))
        cls.good = checks.new_page(cls.env, cls.website, "Raghad", "/t-good", "<h1>Only one</h1><p>Short.</p>")
        cls.env["ir.ui.view"].flush_model()

    def run_check(self, user=None, **options):
        env = self.env(user=user or self.designer)
        return checks.check_site(env, self.website.with_env(env), **options)

    def keys(self, found, key):
        return [item for item in found if item["key"] == key]

    def urls(self, found, key):
        return {url for item in self.keys(found, key) for url in item["urls"]}

    def test_seo(self):
        found, _skipped = self.run_check(groups=["seo"])
        self.assertIn("/t-bad", self.urls(found, "seo_description_missing"))
        self.assertIn("/t-bad", self.urls(found, "seo_h1_many"))
        self.assertNotIn("/t-good", self.urls(found, "seo_h1_many"))
        self.assertIn("/t-bad", self.urls(found, "seo_alt_missing"))
        # two pages carry the title "Raghad"
        self.assertLessEqual({"/t-bad", "/t-good"}, self.urls(found, "seo_title_duplicate"))
        item = self.keys(found, "seo_alt_missing")[0]
        self.assertEqual(item["fix"], "alt_text")
        self.assertEqual(item["severity"], "warning")

    def test_links(self):
        found, _skipped = self.run_check(groups=["links"])
        broken = self.keys(found, "links_broken")[0]
        self.assertIn("/nope-xyz", broken["extra"]["links"])
        self.assertNotIn("/t-good", broken["extra"]["links"])
        self.assertFalse(self.keys(found, "links_external_broken"), "external links are off by default")

    def test_external_links_head_and_cap(self):
        calls = []

        def fetch(url):
            calls.append(url)
            return 404

        found, _skipped = self.run_check(groups=["links"], external=True, fetch=fetch)
        self.assertIn("https://example.org/a", calls)
        self.assertTrue(self.keys(found, "links_external_broken"))
        many = "".join('<a href="https://site%s.example.net/">x</a>' % i for i in range(60))
        checks.new_page(self.env, self.website, "Many links", "/t-many", many)
        calls.clear()
        found, _skipped = self.run_check(groups=["links"], external=True, fetch=fetch)
        self.assertEqual(len(calls), 50)
        self.assertTrue(self.keys(found, "links_external_capped"))

    def test_a_link_never_leads_the_server_to_its_own_network(self):
        class Answer:
            def __init__(self, status, location=None):
                self.status_code = status
                self.headers = {"Location": location} if location else {}

        asked = []

        def head(url, **kwargs):
            asked.append((url, kwargs))
            if "public.example" in url:
                return Answer(302, "http://169.254.169.254/latest/meta-data/")
            return Answer(200)

        def public_only(url):
            return "169.254" not in url and "127.0.0.1" not in url and url.startswith(("http://", "https://"))

        with patch.object(checks, "_safe_target", public_only), patch("odoo.addons.nudge.tools.pinned_http.head", head):
            self.assertEqual(checks.head_status("https://public.example/r"), 0, "The redirect to a private address stops")
            self.assertEqual([url for url, _k in asked], ["https://public.example/r"])
            self.assertFalse(any(k["allow_redirects"] for _u, k in asked), "Redirects are followed here, checked")
            self.assertEqual(checks.head_status("http://127.0.0.1:8069/"), 0)
            self.assertEqual(checks.head_status("ftp://files.example/x"), 0)
            self.assertEqual(checks.head_status("https://fine.example/"), 200)
            self.assertEqual(asked[-1][1]["timeout"], 5)

    def test_translations(self):
        lang = self.env["res.lang"]._activate_lang("ar_001")
        self.website.language_ids = [(4, lang.id)]
        found, _skipped = self.run_check(groups=["translations"])
        missing = self.keys(found, "translations_missing")
        self.assertTrue(missing)
        self.assertEqual(missing[0]["extra"]["lang"], "ar_001")
        self.assertIn("/t-good", missing[0]["urls"])

    def test_performance(self):
        found, _skipped = self.run_check(groups=["performance"])
        heavy = self.keys(found, "perf_images_heavy")[0]
        self.assertIn(self.big.id, heavy["ids"])
        self.assertIn("/t-bad", self.urls(found, "perf_snippets_many"))
        self.assertIn("/t-bad", self.urls(found, "perf_inline_scripts"))
        self.assertNotIn("/t-good", self.urls(found, "perf_inline_scripts"))

    def test_accessibility(self):
        found, _skipped = self.run_check(groups=["accessibility"], palette={"o-color-4": "#888888", "o-color-5": "#777777"})
        self.assertTrue(self.keys(found, "a11y_contrast"))
        self.assertIn("/t-bad", self.urls(found, "a11y_labels"))
        found, _skipped = self.run_check(groups=["accessibility"], palette={"o-color-4": "#FFFFFF", "o-color-5": "#000000"})
        self.assertFalse(self.keys(found, "a11y_contrast"))
        self.assertAlmostEqual(checks.contrast("#000000", "#FFFFFF"), 21.0, places=1)

    def test_legal_and_its_fix(self):
        found, _skipped = self.run_check(groups=["legal"])
        item = self.keys(found, "legal_missing")[0]
        self.assertEqual(sorted(item["extra"]["missing"]), ["privacy", "terms"])
        env = self.env(user=self.designer)
        website = self.website.with_env(env)
        changes = checks.plan_fix(env, website, "legal_pages")
        self.assertEqual(len(changes), 2)
        checks.apply_plan(env, website, changes)
        created = env["website.page"].search([("name", "in", [c["name"] for c in changes])])
        self.assertEqual(len(created), 2)
        self.assertFalse(any(created.mapped("is_published")), "Nudge publishes nothing")
        found, _skipped = self.run_check(groups=["legal"])
        self.assertFalse(self.keys(found, "legal_missing"))
        self.assertTrue(self.keys(found, "legal_unlinked"), "the new pages are not in the footer yet")
        self.assertFalse(checks.plan_fix(env, website, "legal_pages"))

    def test_shop(self):
        if "product.public.category" not in self.env:
            self.skipTest("website_sale is not installed")
        Product = self.env["product.template"]
        category = self.env["product.public.category"].create({"name": "Raghad Empty Shelf"})
        bare = Product.create({"name": "Ebrahim Bare Chair", "list_price": 0, "is_published": True, "sale_ok": True})
        found, _skipped = self.run_check(groups=["shop"])
        self.assertIn(bare.id, self.keys(found, "shop_no_image")[0]["ids"])
        self.assertIn(bare.id, self.keys(found, "shop_no_price")[0]["ids"])
        self.assertIn(bare.id, self.keys(found, "shop_no_description")[0]["ids"])
        self.assertIn(category.id, self.keys(found, "shop_category_empty")[0]["ids"])
        self.assertEqual(self.keys(found, "shop_no_price")[0]["severity"], "critical")

    def test_fix_alt_text_and_meta_description(self):
        env = self.env(user=self.designer)
        website = self.website.with_env(env)
        changes = checks.plan_fix(env, website, "alt_text")
        mine = [c for c in changes if c["page_id"] == self.bad.id]
        self.assertEqual(len(mine), 1)
        self.assertIn("Heavy photo", mine[0]["label"])
        self.assertIn("Team photo", mine[0]["label"])
        self.assertNotIn('alt="Heavy photo"', self.bad.view_id.arch)
        checks.apply_plan(env, website, changes)
        after = self.bad.view_id.arch
        self.assertIn('alt="Heavy photo"', after)
        self.assertIn('alt="Team photo"', after)
        self.assertFalse([c for c in checks.plan_fix(env, website, "alt_text") if c["page_id"] == self.bad.id])
        changes = checks.plan_fix(env, website, "meta_description")
        mine = [c for c in changes if c["page_id"] == self.bad.id]
        self.assertEqual(len(mine), 1)
        self.assertTrue(mine[0]["value"].startswith("Raghad builds quiet and honest houses"))
        # a description typed since the plan is never overwritten
        self.bad.website_meta_description = "Typed by Ebrahim meanwhile"
        checks.apply_plan(env, website, mine)
        self.assertEqual(self.bad.website_meta_description, "Typed by Ebrahim meanwhile")

    def test_fix_product_description(self):
        if "product.public.category" not in self.env:
            self.skipTest("website_sale is not installed")
        product = self.env["product.template"].create({
            "name": "Raghad Table", "is_published": True, "sale_ok": True, "description_sale": "Solid oak table"})
        env = self.env(user=self.designer)
        website = self.website.with_env(env)
        changes = checks.plan_fix(env, website, "product_description")
        self.assertIn(product.id, [c["product_id"] for c in changes])
        checks.apply_plan(env, website, changes)
        field = "description_ecommerce" if "description_ecommerce" in product._fields else "website_description"
        self.assertIn("Solid oak table", product[field])

    def test_report_card_and_history(self):
        Report = self.env["nudge.builder.check"].with_user(self.designer)
        report = Report.run_check(self.website.with_user(self.designer), groups=["seo"])
        finding = report.finding_ids.filtered(lambda f: f.key == "seo_alt_missing")
        self.assertTrue(finding.is_new)
        self.assertIn("images without a text alternative", finding.text)
        show = finding.action_show()
        self.assertEqual(show["type"], "ir.actions.act_url", "one page opens in the editor")
        card = finding.action_fix()
        wizard = self.env["nudge.builder.check.fix"].with_user(self.designer).browse(card["res_id"])
        self.assertIn("Heavy photo", wizard.lines)
        self.assertNotIn('alt="Heavy photo"', self.bad.view_id.arch, "nothing changes before Apply")
        wizard.action_apply()
        self.assertIn('alt="Heavy photo"', self.bad.view_id.arch)
        second = Report.run_check(self.website.with_user(self.designer), groups=["seo"])
        self.assertEqual(second.previous_id, report)
        self.assertFalse(second.finding_ids.filtered(lambda f: f.key == "seo_alt_missing" and f.count))
        for _ in range(11):
            Report.run_check(self.website.with_user(self.designer), groups=["legal"])
        self.assertEqual(Report.search_count([("website_id", "=", self.website.id)]), 10)

    def test_a_single_page_opens_in_the_editor(self):
        item = {"key": "perf_inline_scripts", "model": "website.page", "ids": [self.bad.id], "extra": {}}
        action = checks.show_action(self.env, self.website, item)
        self.assertEqual(action["type"], "ir.actions.act_url")
        self.assertIn("/t-bad?enable_editor=1", action["url"])

    def test_non_designer_is_refused(self):
        env = self.env(user=self.plain)
        with self.assertRaises(AccessError):
            checks.check_site(env, self.website.with_env(env))
        with self.assertRaises(AccessError):
            checks.plan_fix(env, self.website.with_env(env), "alt_text")
        with self.assertRaises(AccessError):
            checks.apply_plan(env, self.website.with_env(env), [])
        with self.assertRaises(AccessError):
            env["nudge.builder.check"].run_check(self.website.with_env(env))

    def test_a_redirect_to_the_servers_own_network_is_not_followed(self):
        from unittest.mock import patch

        class Reply:
            def __init__(self, status, location=None):
                self.status_code, self.headers = status, ({"Location": location} if location else {})

        asked = []

        def head(url, **kwargs):
            asked.append(url)
            self.assertFalse(kwargs.get("allow_redirects"))
            return Reply(302, "http://127.0.0.1:8069/web") if len(asked) == 1 else Reply(200)

        with patch("odoo.addons.nudge.tools.pinned_http.head", head), patch.object(checks, "_safe_target", lambda url: "127.0.0.1" not in url):
            self.assertEqual(checks.head_status("https://public.example/r"), 0)
        self.assertEqual(asked, ["https://public.example/r"])

    def test_a_host_the_company_did_not_allow_is_not_visited(self):
        class Reply:
            def __init__(self, status, location=None):
                self.status_code, self.headers = status, ({"Location": location} if location else {})

        asked = []

        def head(url, **kwargs):
            asked.append(url)
            return Reply(302, "https://elsewhere.example/") if "example.org" in url else Reply(200)

        self.env.company.sudo().nudge_egress_hosts = "example.org"
        checks.new_page(self.env, self.website, "Outside", "/t-outside", '<a href="https://unlisted.example/">x</a>')
        with patch("odoo.addons.nudge.tools.pinned_http.head", head), patch.object(checks, "_safe_target", lambda url: True):
            found, _skipped = self.run_check(groups=["links"], external=True)
        self.assertNotIn("https://unlisted.example/", asked, "Not on the list: not visited")
        self.assertNotIn("https://elsewhere.example/", asked, "Nor followed to when a listed page redirects there")
        self.assertIn("https://example.org/a", self.keys(found, "links_external_broken")[0]["extra"]["links"])


@tagged("post_install", "-at_install")
class TestSiteCheckBuilder2(TransactionCase):
    """Site Check 2.0: the content group, the SEO of each language, structured data and their fixes"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.website = cls.env["website"].search([], limit=1)
        cls.designer = new_test_user(
            cls.env, login="check2_designer", groups="base.group_user,website.group_website_designer")
        cls.sample = checks.new_page(cls.env, cls.website, "Samples", "/t-sample", (
            "<h1>Lorem ipsum dolor sit amet</h1><p>Some real words of the company.</p>"
            '<iframe src=""></iframe><iframe src="https://www.google.com/maps?q=Dubai&amp;output=embed"></iframe>'))
        cls.clean = checks.new_page(cls.env, cls.website, "Clean", "/t-clean-two", (
            "<h1>Raghad builds</h1><p>Houses that last.</p>"
            '<img src="/web/image/1/x.jpg" alt="x"/>'))
        cls.env["ir.ui.view"].flush_model()

    def run_check(self, **options):
        env = self.env(user=self.designer)
        return checks.check_site(env, self.website.with_env(env), **options)

    def urls(self, found, key):
        return {url for item in found if item["key"] == key for url in item["urls"]}

    # ------------------------------------------------------------------ pure helpers
    def test_whatsapp_numbers(self):
        for good in ("+971501234567", "971501234567", "+971 50 123 4567", "(+971) 50-123-4567"):
            self.assertTrue(checks.valid_whatsapp(good), good)
        for bad in ("", "0501234567", "+12", "+97150123456789012345", "call me", "+971<script>"):
            self.assertFalse(checks.valid_whatsapp(bad), bad)

    def test_side_classes_are_what_does_not_flip(self):
        self.assertTrue(checks.SIDE_CLASSES.search("card text-left"))
        self.assertTrue(checks.SIDE_CLASSES.search("float-right"))
        self.assertTrue(checks.SIDE_CLASSES.search("ml-3 x"))
        self.assertFalse(checks.SIDE_CLASSES.search("text-start ms-3 text-center"))

    def test_the_content_group_exists(self):
        self.assertIn("content", checks.GROUPS)
        self.assertIn("content", dict(checks.CHECKS))
        self.assertIn("content", checks.group_labels(self.env))

    # ------------------------------------------------------------------ the content group
    def test_sample_text_is_flagged(self):
        found, _skipped = self.run_check(groups=["content"])
        self.assertIn("/t-sample", self.urls(found, "content_placeholder"))
        self.assertNotIn("/t-clean-two", self.urls(found, "content_placeholder"))

    def test_a_map_with_no_address_is_flagged(self):
        found, _skipped = self.run_check(groups=["content"])
        self.assertIn("/t-sample", self.urls(found, "content_map_src"))
        self.assertNotIn("/t-clean-two", self.urls(found, "content_map_src"))

    def test_a_content_check_changes_nothing(self):
        views = self.env["ir.ui.view"].search_count([])
        self.run_check(groups=["content"])
        self.assertEqual(self.env["ir.ui.view"].search_count([]), views)

    # ------------------------------------------------------------------ SEO
    def test_share_picture_missing_and_its_fix(self):
        found, _skipped = self.run_check(groups=["seo"])
        item = next((one for one in found if one["key"] == "seo_og_missing"), None)
        if not item:
            self.skipTest("this version has no share picture field")
        self.assertIn("/t-clean-two", item["urls"])
        self.assertEqual(item["fix"], "og_image")
        env = self.env(user=self.designer)
        changes = checks.plan_fix(env, self.website.with_env(env), "og_image")
        self.assertTrue([one for one in changes if one["value"] == "/web/image/1/x.jpg"])
        self.assertTrue(checks.apply_plan(env, self.website.with_env(env), changes))
        again = checks.plan_fix(env, self.website.with_env(env), "og_image")
        self.assertFalse([one for one in again if one["view_id"] in {c["view_id"] for c in changes}],
                         "a second run finds nothing left to fill")

    def test_a_language_with_no_seo_text_is_flagged(self):
        arabic = self.env["res.lang"]._activate_lang("ar_001") or self.env["res.lang"].search(
            [("code", "=", "ar_001")])
        self.website.sudo().language_ids = [(4, arabic.id)]
        page = self.env["website.page"].search([("url", "=", "/t-clean-two")], limit=1)
        view = page.view_id
        if "website_meta_title" not in view._fields:
            self.skipTest("this version has no meta title field")
        view.write({"website_meta_title": "Raghad builds", "website_meta_description": "Houses that last."})
        self.env["ir.ui.view"].flush_model()
        found, _skipped = self.run_check(groups=["seo"])
        lacking = [one for one in found if one["key"] == "seo_lang_missing" and one["extra"]["code"] == "ar_001"]
        self.assertTrue(lacking)
        self.assertIn("/t-clean-two", lacking[0]["urls"])
        self.assertEqual(lacking[0]["fix"], "seo_languages")

    def test_an_unknown_fix_changes_nothing(self):
        env = self.env(user=self.designer)
        self.assertEqual(checks.plan_fix(env, self.website.with_env(env), "no_such_fix"), [])

    def test_only_designers_fix(self):
        plain = new_test_user(self.env, login="check2_plain", groups="base.group_user")
        env = self.env(user=plain)
        with self.assertRaises(AccessError):
            checks.check_designer(env)


class TestStructuredData(TransactionCase):
    """tools.seo: the JSON-LD text cannot break out of its script, and the head helpers keep the rest of the page"""

    def test_json_cannot_close_a_script(self):
        from ..tools import seo
        text = seo.dump({"name": "</script><script>alert(1)</script> & <b>"})
        self.assertNotIn("<", text)
        self.assertNotIn(">", text)
        self.assertNotIn("&", text)
        import json
        self.assertEqual(json.loads(text)["name"], "</script><script>alert(1)</script> & <b>")

    def test_opening_hours_are_read_not_guessed(self):
        from ..tools import seo
        self.assertEqual(seo._opening_hours("Mon-Fri 08:00-17:00\nSat 9:00-13:00"), ["Mo-Fr 08:00-17:00", "Sa 09:00-13:00"])
        self.assertEqual(seo._opening_hours("Open when we are open"), [])

    def test_the_head_helpers(self):
        from ..tools import seo
        arch = '<t t-name="p"><t t-call="website.layout"><div id="wrap"><p>Hi</p></div></t></t>'
        snippet = '<t t-set="head"><script id="nb-jsonld" type="application/ld+json">{"a":1}</script></t>'
        self.assertFalse(seo.has_jsonld(arch))
        with_head = seo.set_head(arch, snippet)
        self.assertTrue(seo.has_jsonld(with_head))
        self.assertIn('id="wrap"', with_head)
        replaced = seo.set_head(with_head, snippet.replace('{"a":1}', '{"a":2}'))
        self.assertEqual(replaced.count("nb-jsonld"), 1)
        self.assertIn('{"a":2}', replaced)
        removed = seo.set_head(replaced, "")
        self.assertFalse(seo.has_jsonld(removed))
        self.assertIn('id="wrap"', removed)

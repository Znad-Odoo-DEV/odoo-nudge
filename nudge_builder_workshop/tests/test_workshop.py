import re
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

from odoo.addons.nudge_builder.tools import checks, library

from ..controllers import booking
from ..tools import checks as workshop_checks, services


class WorkshopCase:
    @classmethod
    def make_site(cls):
        cls.website = cls.env["website"].search([], limit=1)
        cls.site = cls.env["nudge.builder.site"].create({
            "name": "Raghad Garage", "website_id": cls.website.id, "template_key": "car_workshop_booking",
            "state": "built"})
        Service = cls.env["nudge.builder.workshop.service"]
        cls.oil = Service.create({"site_id": cls.site.id, "name": "Oil change", "category": "Engine",
                                  "duration": "About 1 hour", "price_from": 150})
        cls.brakes = Service.create({"site_id": cls.site.id, "name": "Brake pads", "bookable": False})
        cls.hidden = Service.create({"site_id": cls.site.id, "name": "Secret tuning", "published": False})


@tagged("post_install", "-at_install")
class TestWorkshopPlugin(WorkshopCase, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.make_site()
        cls.designer = new_test_user(
            cls.env, login="workshop_designer", name="Raghad Designer", email="raghad@acme.example",
            groups="website.group_website_designer,nudge.group_nudge_user")

    def paths(self):
        return self.env["nudge.builder.plugin"].plugin_template_paths()

    def test_the_plugin_is_registered(self):
        Plugin = self.env["nudge.builder.plugin"]
        self.assertIn(services.KIND, Plugin.plugin_kinds())
        self.assertIn(workshop_checks.GROUP, [check["group"] for check in Plugin.plugin_checks()])
        template = library.load("car_workshop_booking", self.paths())
        self.assertEqual(template["kinds"][0], services.KIND)
        self.assertIn("car_workshop_booking", dict(self.env["nudge.builder.site"]._template_selection()))
        self.assertIn(services.KIND, self.site.kinds_used)
        self.assertEqual(self.site._kind_records(services.KIND), self.oil | self.brakes)

    def test_the_page_shows_the_services_with_their_facts_and_a_booking_button(self):
        template = library.load("car_workshop_booking", self.paths())
        spec = next(page for page in template["pages"] if page["key"] == "services")
        page = self.site.with_user(self.designer)._build_page(spec)
        arch = page.view_id.with_context(lang="en_US").arch_db
        self.assertIn("Oil change", arch)
        self.assertIn("About 1 hour", arch)
        self.assertIn("From", arch)
        self.assertIn("150", arch)
        self.assertIn('href="/workshop-booking?service=Oil%20change"', arch)
        self.assertIn("Brake pads", arch)
        self.assertNotIn("Brake%20pads", arch)  # not bookable: no button
        self.assertNotIn("Secret tuning", arch)
        self.assertIn('data-snippet="s_features"', arch)

    def test_a_service_with_no_price_shows_none(self):
        item = services.item(self.site, self.brakes, {"name": "Brake pads"}, None)
        self.assertEqual(item["meta"], [])
        self.assertNotIn("actions", item)

    def test_with_no_service_the_section_is_not_placed(self):
        site = self.env["nudge.builder.site"].create({"name": "Empty", "website_id": self.website.id})
        spec = {"key": "services", "kind": "services", "name": {"en": "Services", "ar": "x"}, "blocks": [
            {"snippet": "s_text_block", "kind": services.KIND, "copy": {"en": {"title": "Services"}, "ar": {}}}]}
        self.assertFalse(site.with_user(self.designer)._build_page(spec))
        self.assertIn("No workshop_service records", site.log)

    def test_a_negative_price_is_refused(self):
        with self.assertRaises(ValidationError):
            self.oil.price_from = -1

    def test_the_site_check_names_what_a_service_lacks(self):
        env = self.env(user=self.designer)
        found, skipped = checks.check_site(env, self.website.with_env(env), groups=[workshop_checks.GROUP])
        self.assertFalse(skipped)
        keys = {item["key"]: item for item in found}
        self.assertEqual(keys["workshop_no_price"]["ids"], [self.brakes.id])
        self.assertEqual(keys["workshop_no_duration"]["ids"], [self.brakes.id])
        self.assertEqual(keys["workshop_no_picture"]["count"], 2)
        self.assertIn("workshop_unlinked", keys)  # no page links to the booking page yet
        self.assertEqual(checks.group_labels(env)[workshop_checks.GROUP], "Workshop")
        self.assertIn("2 published services have no picture.", checks.finding_text(env, "workshop_no_picture", 2))


@tagged("post_install", "-at_install")
class TestWorkshopBooking(WorkshopCase, HttpCase):
    """The booking request: guards first, then the lead or the to-do"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.make_site()

    def good(self, **extra):
        day = fields.Date.context_today(self.env["res.users"]) + timedelta(days=3)
        data = {"name": "Raghad", "phone": "+971 50 123 4567", "email": "raghad@example.com", "car": "Toyota Camry 2020",
                "service": "Oil change", "day": str(day), "message": "Engine light is on.", "consent": "on"}
        data.update(extra)
        return data

    def open_form(self, service=None):
        page = self.url_open("/workshop-booking", params={"service": service} if service else None)
        self.assertEqual(page.status_code, 200)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
        self.assertTrue(token)
        return token.group(1), page.text

    def send(self, token, **extra):
        return self.url_open("/workshop-booking/send", data=dict(self.good(**extra), csrf_token=token))

    def bookings(self):
        return self.env["crm.lead"].sudo().search([("name", "like", "Workshop booking:")])

    def fast(self):
        return patch.object(booking, "_clock", lambda: booking.time.time() + 60)

    def test_the_form_lists_the_bookable_services_only_and_has_the_guards(self):
        token, text = self.open_form("Oil change")
        self.assertIn('name="%s"' % booking.HONEYPOT, text)
        self.assertIn('name="consent"', text)
        self.assertIn("Oil change", text)
        self.assertIn('selected="selected"', text)
        self.assertNotIn("Brake pads", text)
        self.assertNotIn("Secret tuning", text)

    def test_the_page_is_not_found_without_a_bookable_service(self):
        self.oil.bookable = False
        self.assertEqual(self.url_open("/workshop-booking").status_code, 404)

    def test_a_good_request_makes_one_lead_or_one_todo(self):
        token, _text = self.open_form()
        before = len(self.bookings()) if booking.has_crm(self.env) else 0
        with self.fast():
            response = self.send(token)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Thank you", response.text)
        if booking.has_crm(self.env):
            self.assertEqual(len(self.bookings()), before + 1)
            lead = self.bookings()[:1]
            self.assertEqual(lead.contact_name, "Raghad")
            self.assertEqual(lead.email_from, "raghad@example.com")
            self.assertIn("Toyota Camry 2020", lead.description)
            self.assertIn("Oil change", lead.description)

    def test_without_crm_the_request_is_a_todo_of_the_owner(self):
        token, _text = self.open_form()
        with self.fast(), patch.object(booking, "has_crm", lambda env: False):
            response = self.send(token)
        self.assertIn("Thank you", response.text)
        activity = self.env["mail.activity"].sudo().search(
            [("res_id", "=", self.website.company_id.partner_id.id), ("summary", "like", "Workshop booking")])
        self.assertEqual(len(activity), 1)
        self.assertIn("Toyota Camry 2020", activity.note)

    def test_text_is_escaped_and_a_bad_field_is_refused(self):
        token, _text = self.open_form()
        with self.fast():
            response = self.send(token, name="<script>alert(1)</script>", email="not-an-email")
            self.assertEqual(response.status_code, 400)
            self.assertNotIn("<script>alert(1)</script>", response.text)
            self.assertIn("&lt;script&gt;", response.text)
            yesterday = str(fields.Date.context_today(self.env["res.users"]) - timedelta(days=1))
            far = str(fields.Date.context_today(self.env["res.users"]) + timedelta(days=400))
            for change in ({"consent": ""}, {"phone": "", "email": ""}, {"car": ""}, {"name": ""}, {"phone": "abc"},
                           {"service": "Secret tuning"}, {"service": "Brake pads"}, {"service": "Nothing"},
                           {"day": yesterday}, {"day": far}, {"day": "tomorrow"}, {"day": ""}):
                self.assertEqual(self.send(token, **change).status_code, 400, change)

    def test_a_robot_is_thanked_and_nothing_is_made(self):
        if not booking.has_crm(self.env):
            self.skipTest("crm is not installed")
        token, _text = self.open_form()
        before = len(self.bookings())
        with self.fast():
            response = self.send(token, **{booking.HONEYPOT: "http://spam.example"})
        self.assertIn("Thank you", response.text)
        response = self.send(token)  # sent at once, without the minimum fill time
        self.assertIn("Thank you", response.text)
        self.assertEqual(len(self.bookings()), before)

    def test_without_opening_the_form_first_nothing_is_made(self):
        if not booking.has_crm(self.env):
            self.skipTest("crm is not installed")
        token, _text = self.open_form()
        self.url_open("/web/session/logout")
        before = len(self.bookings())
        with self.fast():
            self.send(token)
        self.assertEqual(len(self.bookings()), before)

    def test_the_rate_limit_stops_the_sixth_request_of_an_address(self):
        token, _text = self.open_form()
        statuses = []
        for _ in range(booking.PER_ADDRESS_HOUR + 1):
            self.open_form()
            with self.fast():
                statuses.append(self.send(token).status_code)
        self.assertEqual(statuses[:booking.PER_ADDRESS_HOUR], [200] * booking.PER_ADDRESS_HOUR)
        self.assertEqual(statuses[-1], 429)

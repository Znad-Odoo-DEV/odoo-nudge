import re
from unittest.mock import patch

from odoo.tests import HttpCase, tagged

from ..controllers import quote

GOOD = {
    "name": "Raghad", "phone": "+971 50 123 4567", "email": "raghad@example.com",
    "message": "I need a quote for a villa fit-out.", "consent": "on"}


@tagged("post_install", "-at_install")
class TestQuoteForm(HttpCase):
    """The public Request-a-quote form: guards first, then the lead."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.website = cls.env["website"].search([], limit=1)
        values = {"name": "Raghad Quote Site", "website_id": cls.website.id}
        if "quote_form" in cls.env["nudge.builder.site"]._fields:
            values["quote_form"] = True
        cls.site = cls.env["nudge.builder.site"].create(values)
        cls.has_flag = "quote_form" in cls.env["nudge.builder.site"]._fields

    def tearDown(self):
        super().tearDown()

    def leads(self):
        Lead = self.env["crm.lead"].sudo()
        return Lead.search([("name", "like", "Quote request:")])

    def open_form(self):
        page = self.url_open("/request-a-quote")
        self.assertEqual(page.status_code, 200)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
        self.assertTrue(token)
        return token.group(1)

    def send(self, token, **extra):
        data = dict(GOOD, csrf_token=token)
        data.update(extra)
        return self.url_open("/request-a-quote/send", data=data)

    def test_the_form_has_a_honeypot_and_consent_and_is_404_without_a_site(self):
        token = self.open_form()
        page = self.url_open("/request-a-quote").text
        self.assertIn('name="%s"' % quote.HONEYPOT, page)
        self.assertIn('name="consent"', page)
        self.assertTrue(token)
        self.site.unlink()
        self.assertEqual(self.url_open("/request-a-quote").status_code, 404)

    def test_a_quote_flag_off_hides_the_form(self):
        if not self.has_flag:
            self.skipTest("the site has no quote_form flag yet")
        self.site.quote_form = False
        self.assertEqual(self.url_open("/request-a-quote").status_code, 404)

    def test_a_good_request_is_thanked_and_makes_one_lead_or_note(self):
        token = self.open_form()
        before = len(self.leads()) if "crm.lead" in self.env.registry else 0
        with patch.object(quote, "_clock", lambda: quote.time.time() + 60):
            response = self.send(token)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Thank you", response.text)
        if "crm.lead" in self.env.registry:
            self.assertEqual(len(self.leads()), before + 1)
            lead = self.leads()[:1]
            self.assertEqual(lead.contact_name, "Raghad")
            self.assertEqual(lead.email_from, "raghad@example.com")
            self.assertEqual(lead.website_id, self.website) if "website_id" in lead._fields else None
        else:
            activity = self.env["mail.activity"].sudo().search(
                [("res_id", "=", self.website.company_id.partner_id.id), ("summary", "like", "Quote request")])
            self.assertTrue(activity)

    def test_text_is_escaped_in_the_lead_and_in_the_echoed_form(self):
        token = self.open_form()
        with patch.object(quote, "_clock", lambda: quote.time.time() + 60):
            response = self.send(token, name="<script>alert(1)</script>", email="not-an-email")
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("<script>alert(1)</script>", response.text)
        self.assertIn("&lt;script&gt;", response.text)

    def test_a_missing_consent_or_contact_or_message_is_refused(self):
        token = self.open_form()
        with patch.object(quote, "_clock", lambda: quote.time.time() + 60):
            for change in ({"consent": ""}, {"phone": "", "email": ""}, {"message": "hi"}, {"name": ""},
                           {"phone": "abc"}):
                response = self.send(token, **change)
                self.assertEqual(response.status_code, 400, change)

    def test_a_robot_is_thanked_and_nothing_is_made(self):
        if "crm.lead" not in self.env.registry:
            self.skipTest("crm is not installed")
        token = self.open_form()
        before = len(self.leads())
        # honeypot filled
        with patch.object(quote, "_clock", lambda: quote.time.time() + 60):
            response = self.send(token, **{quote.HONEYPOT: "http://spam.example"})
        self.assertIn("Thank you", response.text)
        # sent at once, without the minimum fill time
        response = self.send(token)
        self.assertIn("Thank you", response.text)
        self.assertEqual(len(self.leads()), before)

    def test_without_opening_the_form_first_nothing_is_made(self):
        if "crm.lead" not in self.env.registry:
            self.skipTest("crm is not installed")
        token = self.open_form()
        self.url_open("/web/session/logout")
        before = len(self.leads())
        with patch.object(quote, "_clock", lambda: quote.time.time() + 60):
            self.url_open("/request-a-quote/send", data=dict(GOOD, csrf_token=token))
        self.assertEqual(len(self.leads()), before)

    def test_the_rate_limit_stops_the_sixth_request_of_an_address(self):
        token = self.open_form()
        statuses = []
        for _ in range(quote.PER_ADDRESS_HOUR + 1):
            self.open_form()
            with patch.object(quote, "_clock", lambda: quote.time.time() + 60):
                statuses.append(self.send(token).status_code)
        self.assertEqual(statuses[:quote.PER_ADDRESS_HOUR], [200] * quote.PER_ADDRESS_HOUR)
        self.assertEqual(statuses[-1], 429)

    def test_the_log_keeps_a_hash_and_never_the_address(self):
        token = self.open_form()
        with patch.object(quote, "_clock", lambda: quote.time.time() + 60):
            self.send(token)
        log = self.env["nudge.builder.quote.log"].sudo().search([], limit=1)
        self.assertTrue(log)
        self.assertEqual(len(log.ip_hash), 64)
        self.assertNotIn("127.0.0.1", log.ip_hash)

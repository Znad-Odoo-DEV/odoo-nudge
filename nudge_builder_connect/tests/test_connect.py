"""Consent, pull, preview and confirm, with the web faked (nothing leaves the machine)."""
import base64
import io
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from .common import (
    BUSINESS_SCOPE, DRIVE_SCOPE, FOLDER, FakeWeb, picture)

MODULE_LOGGER = "odoo.addons.nudge_builder_connect"
FOLDER_LINK = "https://drive.google.com/drive/folders/" + FOLDER + "?usp=sharing"
GROUPS = "website.group_website_designer,nudge.group_nudge_user,nudge.group_nudge_agent"


def _login(call):
    return call["data"]["code"][len("code-"):]


class ConnectCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.website = cls.env["website"].search([], limit=1)
        cls.english = cls.env["res.lang"].search([("code", "=", "en_US")])
        cls.designer = new_test_user(cls.env, login="bc_designer", name="Raghad Designer", groups=GROUPS)
        cls.designer2 = new_test_user(cls.env, login="bc_designer2", name="Ebrahim Designer", groups=GROUPS)
        cls.plain = new_test_user(cls.env, login="bc_plain", name="Plain User", groups="base.group_user")
        cls.other_company = cls.env["res.company"].create({"name": "Other Contracting"})
        cls.other = new_test_user(
            cls.env, login="bc_other", name="Other Designer", groups=GROUPS,
            company_id=cls.other_company.id, company_ids=[(6, 0, cls.other_company.ids)])
        cls.env["ir.config_parameter"].sudo().set_param("web.base.url", "https://odoo.example.com")
        cls.google = cls.env.ref("nudge_builder_connect.connector_google")
        cls.instagram = cls.env.ref("nudge_builder_connect.connector_instagram")
        cls.google.write({"oauth_client_id": "client-google", "oauth_client_secret": "secret-google", "state": "active"})
        cls.instagram.write({"oauth_client_id": "client-ig", "oauth_client_secret": "secret-ig", "state": "active"})

    def setUp(self):
        super().setUp()
        self.web = FakeWeb()
        self.web.__enter__()
        self.addCleanup(self.web.__exit__)
        check = patch("odoo.addons.nudge_connect.mcp_client.check_url")  # no resolver in the tests
        check.start()
        self.addCleanup(check.stop)
        self.granted = DRIVE_SCOPE + " " + BUSINESS_SCOPE
        self.refresh = (200, {"access_token": "ya29.fresh", "expires_in": 3600})
        self.web.add("oauth2.googleapis.com/token", self._google_token)
        self.web.add("api.instagram.com/oauth/access_token", lambda call: (200, {"data": [{
            "access_token": "short-" + _login(call), "user_id": "1"}]}))
        self.ig_exchange = (200, {"access_token": "long-token", "expires_in": 5184000})
        self.web.add("graph.instagram.com/access_token", lambda call: self.ig_exchange)
        self.web.add("graph.instagram.com/refresh_access_token",
                     lambda call: (200, {"access_token": "long-token-renewed", "expires_in": 5184000}))

    def _google_token(self, call):
        data = call["data"]
        if data["grant_type"] == "refresh_token":
            return self.refresh
        return 200, {"access_token": "ya29." + _login(call), "refresh_token": "1//ref-" + _login(call),
                     "expires_in": 3600, "scope": self.granted}

    # ------------------------------------------------------------------ helpers
    def sign_in(self, user, connector=None):
        connector = connector or self.google
        query = parse_qs(urlsplit(connector.with_user(user)._oauth_begin()).query)
        return self.env["nudge.connector.oauth.state"].with_user(user)._complete(
            query["state"][0], "code-" + user.login)

    def token(self, user, connector=None):
        return self.env["nudge.connector.token"].sudo().search([
            ("connector_id", "=", (connector or self.google).id), ("user_id", "=", user.id)])

    def site(self, user=None, **values):
        values = {"website_id": self.website.id, "kind": "company", "lang_ids": [(6, 0, self.english.ids)], **values}
        return self.env["nudge.builder.site"].with_user(user or self.designer).create(values)

    def wizard(self, site, source, user=None, **values):
        return self.env["nudge.builder.connect.pull"].with_user(user or self.designer).create(
            dict({"site_id": site.id, "source": source}, **values))

    def line(self, wizard, name):
        return wizard.line_ids.filtered(lambda line: line.name == name)


@tagged("post_install", "-at_install", "nudge_builder_connect")
class TestConsent(ConnectCase):

    def test_the_consent_screen_asks_for_drive_and_business_profile_for_the_person(self):
        url = self.google.with_user(self.designer)._oauth_begin()
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        self.assertEqual(parts.netloc, "accounts.google.com")
        self.assertEqual(query["access_type"], ["offline"])
        self.assertEqual(query["prompt"], ["consent"])
        self.assertEqual(set(query["scope"][0].split()), {DRIVE_SCOPE, BUSINESS_SCOPE})
        self.assertEqual(query["redirect_uri"], ["https://odoo.example.com/nudge_connect/oauth/callback"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["client_id"], ["client-google"])
        self.assertNotIn("secret-google", url)

    def test_tokens_are_the_persons_own_and_are_never_logged(self):
        with self.assertNoLogs(MODULE_LOGGER, level="DEBUG"):
            self.sign_in(self.designer)
        row = self.token(self.designer)
        self.assertEqual(row.sudo().access_token, "ya29.bc_designer")
        self.assertEqual(row.sudo().refresh_token, "1//ref-bc_designer")
        self.assertFalse(self.token(self.designer2))
        site = self.site()
        self.assertTrue(self.wizard(site, "drive").signed_in)
        self.assertFalse(self.wizard(site, "drive", user=self.designer2).signed_in)
        self.assertFalse(self.wizard(site, "instagram").signed_in, "Google's sign-in is not Instagram's")

    def test_connect_button_goes_to_the_sign_in_page_of_nudge_connect(self):
        wizard = self.wizard(self.site(), "drive")
        action = wizard.action_connect()
        self.assertEqual(action["type"], "ir.actions.act_url")
        self.assertEqual(action["url"], "https://odoo.example.com/nudge_connect/oauth/start/%d" % self.google.id)
        self.google.write({"oauth_client_id": False})
        with self.assertRaises(UserError):
            wizard.action_connect()

    def test_disconnect_forgets_only_this_persons_sign_in(self):
        self.sign_in(self.designer)
        self.sign_in(self.designer2)
        self.web.add("oauth2.googleapis.com/revoke", {})
        self.wizard(self.site(), "drive").action_disconnect()
        self.assertFalse(self.token(self.designer))
        self.assertTrue(self.token(self.designer2))
        revoked = self.web.to("oauth2.googleapis.com/revoke")
        self.assertEqual(len(revoked), 1)
        self.assertEqual(revoked[0]["data"]["token"], "1//ref-bc_designer")

    def test_a_stopped_connector_is_refused_plainly(self):
        self.sign_in(self.designer)
        self.google.state = "stopped"
        wizard = self.wizard(self.site(), "drive", drive_link=FOLDER_LINK)
        with self.assertRaises(UserError):
            wizard.action_connect()
        with self.assertRaises(UserError):
            wizard.action_preview()

    def test_nothing_is_read_without_a_sign_in(self):
        site = self.site()
        project = self.env["nudge.builder.project"].with_user(self.designer).create({"site_id": site.id, "name": "Villa"})
        wizard = self.wizard(site, "drive", drive_link=FOLDER_LINK, project_id=project.id)
        with self.assertRaisesRegex(UserError, "Connect your account first"):
            wizard.action_preview()
        self.assertFalse(self.web.calls)

    def test_a_missing_permission_at_the_consent_screen_is_said(self):
        self.granted = DRIVE_SCOPE
        self.sign_in(self.designer)
        site = self.site()
        with self.assertRaisesRegex(UserError, "did not allow everything"):
            self.wizard(site, "business").action_preview()
        self.assertFalse(self.web.to("mybusiness"))

    def test_another_company_and_a_plain_user_cannot_reach_the_site(self):
        site = self.site()
        with self.assertRaises(AccessError):
            site.with_user(self.other).action_pull_drive()
        with self.assertRaises(AccessError):
            site.with_user(self.plain).action_pull_business()
        wizard = self.wizard(site, "drive")
        with self.assertRaises(AccessError):
            wizard.with_user(self.other).action_preview()
        action = site.with_user(self.designer).action_pull_instagram()
        self.assertEqual(action["res_model"], "nudge.builder.connect.pull")

    def test_each_person_uses_their_own_token(self):
        self.sign_in(self.designer)
        self.sign_in(self.designer2)
        site = self.site()
        project = self.env["nudge.builder.project"].with_user(self.designer).create({"site_id": site.id, "name": "Villa"})
        self.web.add("drive/v3/files", {"files": []})
        self.wizard(site, "drive", drive_link=FOLDER_LINK, project_id=project.id).action_preview()
        self.wizard(site, "drive", user=self.designer2, drive_link=FOLDER_LINK, project_id=project.id).action_preview()
        sent = [call["headers"]["Authorization"] for call in self.web.to("drive/v3/files")]
        self.assertEqual(sent, ["Bearer ya29.bc_designer", "Bearer ya29.bc_designer2"])


@tagged("post_install", "-at_install", "nudge_builder_connect")
class TestDrive(ConnectCase):

    def setUp(self):
        super().setUp()
        self.sign_in(self.designer)
        self.site_ = self.site()
        self.project = self.env["nudge.builder.project"].with_user(self.designer).create(
            {"site_id": self.site_.id, "name": "Villa"})

    def drive(self, **values):
        return self.wizard(self.site_, "drive", drive_link=FOLDER_LINK, project_id=self.project.id, **values)

    def listing(self, files, **extra):
        self.web.add("drive/v3/files", dict({"files": files}, **extra))

    def test_the_preview_writes_nothing_and_the_yes_takes_only_the_ticked_pictures(self):
        from PIL import Image
        source = picture(exif=True)
        self.assertIn(0x010F, Image.open(io.BytesIO(source)).getexif(), "the test picture carries location-type data")
        self.env["nudge.builder.picture"].with_user(self.designer).create(
            {"project_id": self.project.id, "name": "existing.jpg", "image": base64.b64encode(picture())})
        self.web.add("drive/v3/files/idFront00000", source, params={"alt": "media"})
        self.web.add("drive/v3/files/idFake000000", b"this is not a picture", params={"alt": "media"})
        self.web.add("drive/v3/files/idSide000000", picture("PNG"), params={"alt": "media"})
        self.listing([
            {"id": "idFront00000", "name": "front.jpg", "mimeType": "image/jpeg", "size": "1000"},
            {"id": "idFake000000", "name": "fake.jpg", "mimeType": "image/jpeg", "size": "100"},
            {"id": "idSide000000", "name": "side.png", "mimeType": "image/png", "size": "500"},
            {"id": "idPlan000000", "name": "plan.pdf", "mimeType": "application/pdf"},
            {"id": "idHuge000000", "name": "huge.jpg", "mimeType": "image/jpeg", "size": str(13 * 1024 * 1024)},
            {"id": "idSame000000", "name": "existing.jpg", "mimeType": "image/jpeg", "size": "100"}])
        wizard = self.drive()
        with self.assertNoLogs(MODULE_LOGGER, level="DEBUG"):
            wizard.action_preview()
        self.assertEqual(wizard.state, "preview")
        self.assertEqual(len(self.project.picture_ids), 1, "nothing is written before the yes")
        self.assertEqual(set(wizard.line_ids.mapped("name")), {"front.jpg", "fake.jpg", "side.png", "existing.jpg"})
        self.assertTrue(self.line(wizard, "existing.jpg").existing)
        self.assertFalse(self.line(wizard, "existing.jpg").selected)
        self.assertIn("1 pictures were left out", wizard.notes)
        self.assertIn("1 files were left out", wizard.notes)
        listed = self.web.to("drive/v3/files")[0]
        self.assertIn(FOLDER, listed["params"]["q"])
        self.assertEqual(listed["headers"]["Authorization"], "Bearer ya29.bc_designer")
        self.assertFalse(self.web.to("drive/v3/files/"), "no file is downloaded for a preview")
        self.line(wizard, "side.png").selected = False
        action = wizard.action_take()
        self.assertIn("Added: 1 pictures", action["params"]["message"])
        self.assertIn("Left out: 1", action["params"]["message"])
        names = self.project.picture_ids.mapped("name")
        self.assertCountEqual(names, ["existing.jpg", "front.jpg"])
        stored = self.project.picture_ids.filtered(lambda pic: pic.name == "front.jpg")
        self.assertNotIn(0x010F, Image.open(io.BytesIO(base64.b64decode(stored.image))).getexif(),
                         "the metadata of the picture is stripped as for every picture of the builder")
        self.assertFalse(self.web.to("drive/v3/files/idSide000000"), "an unticked picture is never downloaded")

    def test_a_download_bigger_than_the_limit_is_cut_and_left_out(self):
        self.web.add("drive/v3/files/idLie0000000", b"\0" * (12 * 1024 * 1024 + 10), params={"alt": "media"})
        self.listing([{"id": "idLie0000000", "name": "lie.jpg", "mimeType": "image/jpeg", "size": "1000"}])
        wizard = self.drive()
        wizard.action_preview()
        action = wizard.action_take()
        self.assertFalse(self.project.picture_ids)
        self.assertIn("Added: 0", action["params"]["message"])

    def test_listing_and_ticking_are_bounded(self):
        files = [{"id": "img%09d" % number, "name": "p%03d.jpg" % number, "mimeType": "image/jpeg", "size": "10"}
                 for number in range(40)]
        self.listing(files, nextPageToken="more")
        wizard = self.drive()
        wizard.action_preview()
        self.assertEqual(len(self.web.to("drive/v3/files")), 3, "paging is bounded")
        self.assertEqual(len(wizard.line_ids), 100, "the list is bounded")
        self.assertIn("Only the first 100", wizard.notes)
        wizard.line_ids.write({"selected": True})
        with self.assertRaisesRegex(UserError, "Too many are ticked"):
            wizard.action_take()
        self.assertFalse(self.project.picture_ids)

    def test_the_link_must_be_a_drive_folder_and_a_record_must_be_chosen(self):
        with self.assertRaisesRegex(UserError, "not the link of a Google Drive folder"):
            self.wizard(self.site_, "drive", drive_link="https://evil.example.com/folders/" + FOLDER,
                        project_id=self.project.id).action_preview()
        with self.assertRaisesRegex(UserError, "Choose the record"):
            self.wizard(self.site_, "drive", drive_link=FOLDER_LINK).action_preview()
        other_project = self.env["nudge.builder.project"].with_user(self.designer).create(
            {"site_id": self.site(name="Second").id, "name": "Elsewhere"})
        with self.assertRaisesRegex(UserError, "another site"):
            self.wizard(self.site_, "drive", drive_link=FOLDER_LINK, project_id=other_project.id).action_preview()
        self.assertFalse(self.web.to("drive/v3/files"))

    def test_errors_are_said_in_words(self):
        for status, words in ((404, "found nothing"), (403, "did not allow"), (429, "asks to wait"),
                              (503, "problem of its own")):
            web = self.web
            web.routes = [route for route in web.routes if "drive/v3/files" not in route[0]]
            web.add("drive/v3/files", {"error": {"message": "secret detail"}}, status=status)
            with self.assertRaisesRegex(UserError, words) as caught:
                self.drive().action_preview()
            self.assertNotIn("secret detail", str(caught.exception))
            self.assertNotIn("ya29", str(caught.exception))

    def test_a_refused_token_is_forgotten(self):
        self.web.add("drive/v3/files", {}, status=401)
        wizard = self.drive()
        with self.assertRaisesRegex(UserError, "refused your sign-in"):
            wizard.action_preview()
        self.assertFalse(self.token(self.designer))
        wizard.invalidate_recordset()
        self.assertFalse(wizard.signed_in)

    def test_an_expired_token_is_renewed_and_a_withdrawn_one_asks_for_a_new_sign_in(self):
        self.web.add("drive/v3/files", {"files": []})
        self.token(self.designer).write({"expires": fields.Datetime.now() - timedelta(hours=1)})
        self.drive().action_preview()
        renewal = [call for call in self.web.to("oauth2.googleapis.com/token") if call["data"]["grant_type"] == "refresh_token"]
        self.assertEqual(renewal[0]["data"]["refresh_token"], "1//ref-bc_designer")
        self.assertEqual(self.web.to("drive/v3/files")[-1]["headers"]["Authorization"], "Bearer ya29.fresh")
        self.token(self.designer).write({"expires": fields.Datetime.now() - timedelta(hours=1)})
        self.refresh = (400, {"error": "invalid_grant"})
        with self.assertRaisesRegex(UserError, "ended or was withdrawn"):
            self.drive().action_preview()
        self.assertFalse(self.token(self.designer))


@tagged("post_install", "-at_install", "nudge_builder_connect")
class TestBusinessProfile(ConnectCase):

    HOURS = {"periods": [
        {"openDay": "MONDAY", "openTime": {"hours": 9}, "closeDay": "MONDAY", "closeTime": {"hours": 17}}]}

    def setUp(self):
        super().setUp()
        self.sign_in(self.designer)
        self.site_ = self.site()
        self.env["nudge.builder.location"].with_user(self.designer).create(
            {"site_id": self.site_.id, "name": "Existing Branch"})
        self.web.add("mybusinessaccountmanagement.googleapis.com/v1/accounts",
                     {"accounts": [{"name": "accounts/111", "accountName": "Raghad Co"}, {"name": "evil"}]})
        self.web.add("mybusinessbusinessinformation.googleapis.com/v1/accounts/111/locations", {"locations": [
            {"name": "locations/1", "title": "Main Shop",
             "storefrontAddress": {"regionCode": "AE", "postalCode": "00000", "locality": "Dubai",
                                   "administrativeArea": "Dubai", "addressLines": ["12 Sheikh Zayed Rd", "Unit 4"]},
             "phoneNumbers": {"primaryPhone": "+971 4 123 4567"}, "regularHours": self.HOURS,
             "latlng": {"latitude": 25.2, "longitude": 55.27},
             "metadata": {"mapsUri": "https://maps.google.com/?cid=123"}},
            {"name": "locations/2", "title": "Existing Branch"}]})
        self.review_text = "Great work -  finished early!!  Highly recommend. (Translated by Google)"
        self.web.add("mybusiness.googleapis.com/v4/accounts/111/locations/1/reviews", {"reviews": [
            {"reviewId": "a", "reviewer": {"displayName": "Sarah Connor"}, "starRating": "FIVE",
             "comment": self.review_text},
            {"reviewId": "b", "reviewer": {"displayName": "<b>Mo</b> Ali"}, "starRating": "TWO", "comment": "Late."},
            {"reviewId": "c", "reviewer": {"displayName": "Quiet One"}, "starRating": "FIVE"}]})
        self.web.add("mybusiness.googleapis.com/v4/accounts/111/locations/2/reviews", {"reviews": []})

    def test_places_and_reviews_are_shown_and_only_the_ticked_are_written(self):
        wizard = self.wizard(self.site_, "business")
        with self.assertNoLogs(MODULE_LOGGER, level="DEBUG"):
            wizard.action_preview()
        self.assertEqual(len(self.site_.location_ids), 1)
        self.assertFalse(self.site_.testimonial_ids, "nothing is written before the yes")
        kinds = sorted(wizard.line_ids.mapped("kind"))
        self.assertEqual(kinds, ["location", "location", "review", "review"])
        main, existing = self.line(wizard, "Main Shop"), self.line(wizard, "Existing Branch")
        self.assertTrue(main.selected)
        self.assertTrue(existing.existing)
        self.assertFalse(existing.selected)
        reviews = wizard.line_ids.filtered(lambda line: line.kind == "review")
        self.assertFalse(any(reviews.mapped("selected")), "a review is taken only when the person ticks it")
        self.assertCountEqual(reviews.mapped("name"), ["Sarah", "Mo"], "a reviewer shows a first name only")
        self.assertIn("1 reviews without a comment", wizard.notes)
        self.line(wizard, "Sarah").selected = True
        wizard.action_take()
        self.assertEqual(len(self.site_.location_ids), 2)
        place = self.site_.location_ids.filtered(lambda loc: loc.name == "Main Shop")
        self.assertEqual(place.street, "12 Sheikh Zayed Rd, Unit 4")
        self.assertEqual((place.city, place.zip, place.phone), ("Dubai", "00000", "+971 4 123 4567"))
        self.assertEqual(place.country_id.code, "AE")
        self.assertIn("Monday: 09:00-17:00", place.hours)
        self.assertIn("Tuesday: Closed", place.hours)
        self.assertEqual(place.maps_url, "https://maps.google.com/?cid=123")
        self.assertAlmostEqual(place.latitude, 25.2)
        self.assertFalse(place.is_main)
        review = self.site_.testimonial_ids
        self.assertEqual(len(review), 1)
        self.assertEqual((review.author, review.rating), ("Sarah", 5))
        self.assertEqual(review.quote, self.review_text, "a review is copied word for word")
        self.assertEqual(self.web.to("accounts/111/locations/1/reviews")[0]["headers"]["Authorization"],
                         "Bearer ya29.bc_designer")

    def test_only_places_or_only_reviews(self):
        wizard = self.wizard(self.site_, "business", take_reviews=False)
        wizard.action_preview()
        self.assertEqual(set(wizard.line_ids.mapped("kind")), {"location"})
        self.assertFalse(self.web.to("/reviews"))
        with self.assertRaisesRegex(UserError, "Tick places, reviews or both"):
            self.wizard(self.site_, "business", take_places=False, take_reviews=False).action_preview()

    def test_no_business_profile_is_said(self):
        self.web.routes = [route for route in self.web.routes if "v1/accounts" not in route[0] or "locations" in route[0]]
        self.web.add("mybusinessaccountmanagement.googleapis.com/v1/accounts", {})
        with self.assertRaisesRegex(UserError, "No Business Profile"):
            self.wizard(self.site_, "business").action_preview()

    def test_a_map_link_that_is_not_google_is_dropped_and_a_bad_line_is_left_out(self):
        wizard = self.wizard(self.site_, "business")
        place = wizard._create_location(self.site_, {"name": "Odd", "maps_url": "https://evil.example.com/x"})
        self.assertFalse(place.maps_url)
        with self.assertRaises(ValueError):
            wizard._create_review(self.site_, {"author": "Sarah", "quote": "   ", "rating": 5})
        wizard.action_preview()
        self.line(wizard, "Sarah").write({"selected": True, "payload": "not json"})
        action = wizard.action_take()
        self.assertIn("Left out: 1", action["params"]["message"])


@tagged("post_install", "-at_install", "nudge_builder_connect")
class TestInstagram(ConnectCase):

    def setUp(self):
        super().setUp()
        self.sign_in(self.designer, self.instagram)
        self.site_ = self.site()
        self.service = self.env["nudge.builder.service"].with_user(self.designer).create(
            {"site_id": self.site_.id, "name": "Interiors"})
        self.web.add("graph.instagram.com/me/media", {"data": [
            {"id": "17900000001", "media_type": "IMAGE", "caption": "New villa #dubai", "timestamp": "2026-10-01T10:00:00+0000",
             "media_url": "https://scontent.cdninstagram.com/a.jpg"},
            {"id": "17900000002", "media_type": "VIDEO", "media_url": "https://scontent.cdninstagram.com/v.mp4"},
            {"id": "17900000003", "media_type": "CAROUSEL_ALBUM", "caption": "Pool",
             "media_url": "https://scontent.cdninstagram.com/b.jpg"},
            {"id": "17900000004", "media_type": "IMAGE", "media_url": "https://evil.example.com/c.jpg"}],
            "paging": {"next": "https://evil.example.com/steal?access_token=x"}})
        self.web.add("scontent.cdninstagram.com/a.jpg", picture(exif=True))
        self.web.add("scontent.cdninstagram.com/b.jpg", picture("PNG"))

    def wizard_ig(self):
        return self.wizard(self.site_, "instagram", owner_kind="service", service_id=self.service.id)

    def test_sign_in_gives_the_long_token_and_renews_it(self):
        row = self.token(self.designer, self.instagram).sudo()
        self.assertEqual(row.access_token, "long-token", "the one-hour token is swapped for the 60-day one")
        self.assertGreater(row.expires, fields.Datetime.now() + timedelta(days=50))
        self.assertEqual(self.web.to("graph.instagram.com/access_token")[0]["url"].count("secret-ig"), 1)
        self.env.flush_all()
        self.env.cr.execute("UPDATE nudge_connector_token SET expires = %s, write_date = %s WHERE id = %s", (
            fields.Datetime.now() + timedelta(days=3), fields.Datetime.now() - timedelta(days=2), row.id))
        row.invalidate_recordset()
        bearer = self.env["nudge.connector.token"].with_user(self.designer)._bearer(self.instagram)
        self.assertEqual(bearer, "long-token-renewed")

    def test_a_failed_exchange_keeps_the_short_token_and_logs_no_secret(self):
        self.ig_exchange = (400, {"error": {"message": "bad"}})
        self.token(self.designer, self.instagram).unlink()
        with self.assertLogs(MODULE_LOGGER, level="WARNING") as logs:
            self.sign_in(self.designer, self.instagram)
        self.assertEqual(self.token(self.designer, self.instagram).sudo().access_token, "short-bc_designer")
        text = " ".join(logs.output)
        self.assertNotIn("short-bc_designer", text)
        self.assertNotIn("secret-ig", text)

    def test_latest_pictures_and_the_caption_only_if_ticked(self):
        wizard = self.wizard_ig()
        wizard.action_preview()
        self.assertFalse(self.service.picture_ids, "nothing is written before the yes")
        self.assertCountEqual(wizard.line_ids.mapped("name"), ["instagram-17900000001", "instagram-17900000003"])
        self.assertIn("2 posts were left out", wizard.notes)
        self.assertNotIn("evil.example.com", self.web.hosts(), "the token is never sent to an address of the answer")
        first = self.line(wizard, "instagram-17900000001")
        self.assertEqual(first.caption, "New villa #dubai")
        self.assertFalse(first.with_caption, "the caption is kept only when ticked")
        first.with_caption = True
        wizard.action_take()
        pictures = self.service.picture_ids
        self.assertEqual(len(pictures), 2)
        self.assertEqual(pictures.filtered(lambda pic: pic.name == "instagram-17900000001").alt, "New villa #dubai")
        self.assertFalse(pictures.filtered(lambda pic: pic.name == "instagram-17900000003").alt)
        for call in self.web.to("scontent.cdninstagram.com"):
            self.assertNotIn("Authorization", call["headers"], "a picture address is public: no token goes to it")
        listed = self.web.to("graph.instagram.com/me/media")[0]
        self.assertEqual(listed["headers"]["Authorization"], "Bearer long-token")

    def test_a_tampered_picture_address_is_not_followed(self):
        wizard = self.wizard_ig()
        wizard.action_preview()
        line = self.line(wizard, "instagram-17900000001")
        line.payload = '{"id": "17900000001", "url": "https://evil.example.com/a.jpg", "name": "x"}'
        self.line(wizard, "instagram-17900000003").selected = False
        action = wizard.action_take()
        self.assertIn("Added: 0", action["params"]["message"])
        self.assertNotIn("evil.example.com", self.web.hosts())
        self.assertFalse(self.service.picture_ids)

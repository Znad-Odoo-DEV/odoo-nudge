from odoo.tests import TransactionCase, tagged

from ..tools import parse
from ..tools.rest import Limit, Rest
from .common import FakeWeb

DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@tagged("post_install", "-at_install", "nudge_builder_connect")
class TestParse(TransactionCase):

    def test_folder_links(self):
        folder = "1AbCdEfGhIjKlMnOpQrStUv"
        for link in (f"https://drive.google.com/drive/folders/{folder}?usp=sharing",
                     f"https://drive.google.com/drive/u/0/folders/{folder}",
                     f"https://drive.google.com/open?id={folder}"):
            self.assertEqual(parse.drive_folder_id(link), folder, link)
        for link in ("", None, f"http://drive.google.com/drive/folders/{folder}",
                     f"https://evil.example.com/drive/folders/{folder}",
                     "https://drive.google.com/drive/folders/short",
                     f"https://drive.google.com/file/d/{folder}/view",
                     "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp'%20or%20'1'='1"):
            self.assertFalse(parse.drive_folder_id(link), link)

    def test_hours_are_one_line_per_day_and_closed_days_are_said(self):
        hours = {"periods": [
            {"openDay": "MONDAY", "openTime": {"hours": 9}, "closeDay": "MONDAY", "closeTime": {"hours": 13}},
            {"openDay": "MONDAY", "openTime": {"hours": 16}, "closeDay": "MONDAY", "closeTime": {"hours": 20, "minutes": 30}},
            {"openDay": "SATURDAY", "openTime": {"hours": 10}, "closeDay": "SATURDAY", "closeTime": {"hours": 14}}]}
        lines = parse.format_hours(hours, DAYS, "Closed").split("\n")
        self.assertEqual(lines[0], "Mon: 09:00-13:00, 16:00-20:30")
        self.assertEqual(lines[1], "Tue: Closed")
        self.assertEqual(lines[5], "Sat: 10:00-14:00")
        self.assertEqual(len(lines), 7)
        self.assertEqual(parse.format_hours({}, DAYS, "Closed"), "")
        self.assertEqual(parse.format_hours("junk", DAYS, "Closed"), "")

    def test_reviewer_shows_a_first_name_only_and_stars_map(self):
        self.assertEqual(parse.first_name("Sarah Connor"), "Sarah")
        self.assertEqual(parse.first_name("<b>Mo</b> Ali"), "Mo")
        self.assertEqual(parse.first_name(None), "")
        self.assertEqual((parse.stars("FIVE"), parse.stars("one"), parse.stars("STAR_RATING_UNSPECIFIED")), (5, 1, 0))

    def test_instagram_picture_hosts(self):
        self.assertTrue(parse.instagram_media_url_ok("https://scontent-fra5-1.cdninstagram.com/v/t51/x.jpg"))
        self.assertFalse(parse.instagram_media_url_ok("https://evil.example.com/x.jpg"))
        self.assertFalse(parse.instagram_media_url_ok("https://cdninstagram.com.evil.example/x.jpg"))
        self.assertFalse(parse.instagram_media_url_ok("http://scontent.cdninstagram.com/x.jpg"))

    def test_the_number_of_calls_is_bounded(self):
        connector = self.env.ref("nudge_builder_connect.connector_google")
        rest = Rest(self.env, connector, max_calls=2)
        with FakeWeb() as web:
            web.add("example", {"ok": 1})
            rest.token = lambda: "t"
            rest.json("https://www.googleapis.com/example")
            rest.json("https://www.googleapis.com/example")
            with self.assertRaises(Limit):
                rest.json("https://www.googleapis.com/example")
        self.assertEqual(len(web.calls), 2)

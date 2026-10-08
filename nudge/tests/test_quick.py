from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from .common import NudgeCase


@tagged("post_install", "-at_install")
class TestQuick(NudgeCase, TransactionCase):
    """Before a provider is set up, Nudge still finds screens, settings and pages."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = new_test_user(
            cls.env, login="nudge_quick_admin",
            groups="nudge.group_nudge_manager,base.group_system")

    def quick(self, user):
        return self.env["nudge.quick"].with_user(user)

    def test_screens_are_found_in_the_menus_the_person_can_open(self):
        found = self.quick(self.manager).find("ai providers")
        self.assertIn("Nudge/Configuration/AI Providers", [menu["path"] for menu in found["menus"]])
        # Not for someone who cannot open the menu
        self.assertEqual(self.quick(self.user_a).find("ai providers")["menus"], [])

    def test_settings_are_an_administrators(self):
        found = self.quick(self.admin).find("microphone")
        self.assertIn("nudge_voice", [setting["field"] for setting in found["settings"]])
        setting = next(setting for setting in found["settings"] if setting["field"] == "nudge_voice")
        self.assertEqual(setting["app"], "nudge")
        self.assertEqual(self.quick(self.manager).find("microphone")["settings"], [])

    def test_pages_of_the_documentation_are_found(self):
        if not self.env["nudge.doc.page"].sudo().search_count([]):
            self.skipTest("The documentation is not loaded in this database")
        pages = self.quick(self.user_a).find("reordering rules")["pages"]
        self.assertTrue(pages)
        self.assertTrue(all(page["url"].startswith("https://") for page in pages))
        self.assertEqual(len({page["url"].split("#")[0] for page in pages}), len(pages), "One line per page")

    def test_too_little_is_not_searched(self):
        self.assertEqual(self.quick(self.user_a).find(" a "),
                         {"menus": [], "settings": [], "pages": [], "products": []})

    def test_the_way_is_shown_by_the_pointer(self):
        menu = self.env.ref("nudge.nudge_menu_providers")
        steps = self.quick(self.manager).guide("menu", menu.id)
        self.assertEqual([step["target"]["type"] for step in steps], ["menu", "menu", "menu"])
        self.assertEqual(steps[-1]["target"]["name"], str(menu.id))
        self.assertEqual(self.quick(self.user_a).guide("menu", menu.id), [], "Not their menu")
        self.assertEqual(self.quick(self.manager).guide("menu", "nonsense"), [])

        steps = self.quick(self.admin).guide("setting", {"app": "nudge", "field": "nudge_voice"})
        self.assertEqual(steps[-2]["target"], {"type": "field", "name": "nudge_voice"})
        self.assertEqual(steps[-1]["target"]["name"], "save")
        self.assertEqual(self.quick(self.manager).guide("setting", {"app": "nudge", "field": "nudge_voice"}), [])
        self.assertEqual(self.quick(self.admin).guide("setting", {"app": "nudge", "field": "name"}), [])
        self.assertEqual(self.quick(self.admin).guide("unknown", 1), [])

    def test_only_for_people_of_nudge(self):
        with self.assertRaises(AccessError):
            self.quick(self.outsider).find("providers")
        with self.assertRaises(AccessError):
            self.quick(self.outsider).guide("menu", 1)

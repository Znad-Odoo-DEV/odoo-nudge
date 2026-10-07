from types import SimpleNamespace

from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.llm import prompt
from odoo.addons.nudge.models.nudge_conversation import ADMIN_PARAM
from odoo.addons.nudge.models.nudge_voice import VOICE_PARAM
from odoo.addons.nudge.tools.configuration import hide_secrets

from .common import NudgeCase


@tagged("post_install", "-at_install")
class TestConfiguration(NudgeCase, TransactionCase):
    """Setting Odoo up: read how it is set, advise, apply or show when asked."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = new_test_user(
            cls.env, login="nudge_cfg_admin",
            groups="nudge.group_nudge_agent,nudge.group_nudge_manager,base.group_system,base.group_partner_manager")
        cls.Parameter = cls.env["ir.config_parameter"].sudo()
        # An app of the business, with a Configuration menu that holds contact tags
        Menu = cls.env["ir.ui.menu"].sudo()
        cls.app = Menu.create({"name": "Nudge Test App"})
        configuration = Menu.create({"name": "Configuration", "parent_id": cls.app.id})
        cls.tags_menu = Menu.create({
            "name": "Contact Tags",
            "parent_id": configuration.id,
            "action": f"ir.actions.act_window,{cls.env.ref('base.action_partner_category_form').id}",
        })
        cls.env["res.partner.category"].create([{"name": "Nudge Site A"}, {"name": "Nudge Site B"}])

    def run_tool(self, name, arguments, user=None, mode="agent"):
        return tools.base.call(self.env(user=user or self.admin), name, arguments, None, mode)

    def switch(self, on):
        self.Parameter.set_param(ADMIN_PARAM, "yes" if on else "no")

    def test_settings_are_read_from_the_settings_screen(self):
        apps = self.run_tool("read_settings", {})
        self.assertFalse(apps.is_error, apps.content)
        self.assertIn("Nudge (nudge)", apps.content)

        nudge = self.run_tool("read_settings", {"app": "nudge"})
        self.assertFalse(nudge.is_error, nudge.content)
        self.assertIn("Nudge/Configuration/Settings", nudge.content)
        self.assertIn("## Privacy", nudge.content)
        self.assertIn("People can talk to Nudge", nudge.content, "The help comes with the setting")
        self.assertIn("The AI service answering", nudge.content)
        self.assertIn("nudge_voice=", nudge.content, "An administrator reads the values")

        found = self.run_tool("read_settings", {"words": "microphone"})
        self.assertIn("nudge_voice", found.content)
        self.assertTrue(self.run_tool("read_settings", {"app": "nowhere"}).is_error)

    def test_values_are_an_administrators(self):
        # The apps are those of the Settings screen as this person would have it
        apps = self.run_tool("read_settings", {}, user=self.user_a, mode="talk")
        self.assertNotIn("Nudge (nudge)", apps.content, "The settings of Nudge are for administrators")
        self.assertTrue(self.run_tool("read_settings", {"app": "nudge"}, user=self.user_a, mode="talk").is_error)
        names = [line.split(" (")[0][2:] for line in apps.content.splitlines()[1:]]
        if not names:
            self.skipTest("No app has settings for this person here")
        shown = self.run_tool("read_settings", {"app": names[0]}, user=self.user_a, mode="talk")
        self.assertFalse(shown.is_error, shown.content)
        self.assertIn("administrators only", shown.content)

    def test_what_an_app_defines_is_read_from_its_configuration_menu(self):
        summary = self.run_tool("read_configuration", {"app": "Nudge Test App"}, user=self.user_contacts, mode="talk")
        self.assertFalse(summary.is_error, summary.content)
        self.assertIn(f"Contact Tags (menu_id {self.tags_menu.id}, res.partner.category)", summary.content)
        self.assertIn("Nudge Site A", summary.content)

        rows = self.run_tool("read_configuration", {"menu_id": self.tags_menu.id}, user=self.user_contacts, mode="talk")
        self.assertFalse(rows.is_error, rows.content)
        self.assertIn("Nudge Site B", rows.content)
        # A menu the person cannot open is not read
        self.assertTrue(self.run_tool("read_configuration", {"menu_id": self.tags_menu.id}, user=self.outsider).is_error)

    def test_a_module_is_described_by_what_it_registered(self):
        custom = self.run_tool("describe_module", {}, user=self.user_a, mode="talk")
        self.assertIn("- nudge —", custom.content, "Nudge is not made by Odoo")
        self.assertNotIn("- base —", custom.content)

        nudge = self.run_tool("describe_module", {"module": "nudge"}, user=self.user_a, mode="talk")
        self.assertFalse(nudge.is_error, nudge.content)
        self.assertIn("nudge.conversation", nudge.content)
        self.assertIn("Scheduled actions:", nudge.content)
        self.assertNotIn("read_module_code", nudge.content, "Code is for administrators")
        self.assertTrue(self.run_tool("describe_module", {"module": "not_installed_here"}).is_error)

    def test_code_is_for_administrators(self):
        self.assertIn("no tool named", self.run_tool("read_module_code", {"module": "nudge"}, user=self.agent).content)
        files = self.run_tool("read_module_code", {"module": "nudge"})
        self.assertFalse(files.is_error, files.content)
        self.assertIn("tools/configuration.py", files.content)
        self.assertNotIn(".po", files.content)

        manifest = self.run_tool("read_module_code", {"module": "nudge", "path": "__manifest__.py"})
        self.assertIn("1: ", manifest.content)
        self.assertIn('"name": "Nudge"', manifest.content)
        found = self.run_tool("read_module_code", {"module": "nudge", "find": "class ReadModuleCode"})
        self.assertIn("tools/configuration.py:", found.content)
        for path in ("../web/__manifest__.py", "tools/../../web/__manifest__.py", "i18n/ar.po"):
            self.assertTrue(self.run_tool("read_module_code", {"module": "nudge", "path": path}).is_error, path)

    def test_secrets_in_code_are_hidden(self):
        self.assertEqual(hide_secrets('API_KEY = "sk-live-123456"'), 'API_KEY = "***"')
        self.assertEqual(hide_secrets("'password': 'hunter22',"), "'password': '***',")
        self.assertEqual(hide_secrets('name = "Nudge"'), 'name = "Nudge"')

    def test_settings_change_only_after_a_yes_and_when_open(self):
        self.Parameter.set_param(VOICE_PARAM, "yes")
        before = self.env["res.config.settings"].default_get(["nudge_voice"])["nudge_voice"]
        self.assertIn("no tool named", self.run_tool("change_settings", {
            "changes": [{"field": "nudge_voice", "value": "false"}]}).content, "Off by default")

        self.switch(True)
        self.assertIn("no tool named", self.run_tool("change_settings", {
            "changes": [{"field": "nudge_voice", "value": "false"}]}, user=self.agent).content)
        pending = self.run_tool("change_settings", {"changes": [{"field": "nudge_voice", "value": "false"}]})
        self.assertFalse(pending.is_error, pending.content)
        self.assertTrue(pending.pending["is_sensitive"])
        line = pending.pending["preview"]["lines"][0]
        self.assertEqual((line["before"], line["value"]), ("on" if before else "off", "off"))
        self.assertIn("Voice", line["label"])
        self.assertEqual(
            self.env["res.config.settings"].default_get(["nudge_voice"])["nudge_voice"], before,
            "Nothing before the yes")

        tool = tools.base.TOOLS["change_settings"]
        result = tool.execute(self.env(user=self.admin), SimpleNamespace(values=pending.pending["values"]))
        self.assertFalse(result.is_error, result.content)
        self.assertFalse(self.env["res.config.settings"].default_get(["nudge_voice"])["nudge_voice"])
        self.assertEqual(result.event["type"], "changed")
        self.assertTrue(result.event["action"]["type"].startswith("ir.actions"))

        refused = self.run_tool("change_settings", {"changes": [{"field": "name", "value": "x"}]})
        self.assertTrue(refused.is_error)
        self.assertIn("read_settings", refused.content)

    def test_an_app_is_never_uninstalled_through_the_settings(self):
        self.switch(True)
        Settings = self.env["res.config.settings"]
        modules = [name for name in Settings._fields if name.startswith("module_")]
        if not modules:
            self.skipTest("No setting installs an app in this database")
        refused = self.run_tool("change_settings", {"changes": [{"field": modules[0], "value": "false"}]})
        self.assertTrue(refused.is_error)

    def test_the_way_to_a_setting_is_shown_on_screen(self):
        shown = self.run_tool("show_settings", {
            "app": "nudge",
            "settings": [{"field": "nudge_voice", "text": "شغّل الصوت"}],
        })
        self.assertFalse(shown.is_error, shown.content)
        steps = shown.event["steps"]
        self.assertEqual([step["target"]["type"] for step in steps], ["menu", "menu", "menu", "field", "control"])
        self.assertEqual(steps[-2]["target"]["name"], "nudge_voice")
        self.assertEqual(steps[-2]["text"], "شغّل الصوت")
        self.assertEqual(steps[-1]["target"]["name"], "save")
        self.assertTrue(self.run_tool("show_settings", {
            "app": "nudge", "settings": [{"field": "name", "text": "x"}]}).is_error)
        # Only administrators open the Settings
        self.assertTrue(self.run_tool("show_settings", {
            "app": "nudge", "settings": [{"field": "nudge_voice", "text": "x"}]}, user=self.user_a, mode="talk").is_error)

    def test_the_advisor_is_in_the_instructions(self):
        self.assertIn("## Setting Odoo up", prompt.SYSTEM_PERSONA)
        self.assertIn("read_settings", prompt.SYSTEM_PERSONA)
        self.assertIn("show_settings", prompt.SYSTEM_PERSONA)

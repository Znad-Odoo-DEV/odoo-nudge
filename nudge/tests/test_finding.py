import json

from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.llm import prompt
from odoo.addons.nudge.profile import builder

from .common import NudgeCase


@tagged("post_install", "-at_install")
class TestFinding(NudgeCase, TransactionCase):
    """A record is found by what the person calls it, whatever its kind."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Partner = cls.env["res.partner"]
        cls.acme = Partner.create({"name": "Nudge Acme Trading", "ref": "ACME-7", "is_company": True})
        cls.twin = Partner.create({"name": "Nudge Acme Trading", "ref": "ACME-8", "is_company": True})
        cls.other = Partner.create({"name": "Nudge Beta Works", "ref": "BETA-1"})
        cls.secret = Partner.create({"name": "Nudge Hidden Acme"})
        builder.build(cls.env)

    def find(self, arguments, user=None):
        return tools.base.call(self.env(user=user or self.agent), "find_record", arguments, None, "agent")

    def read(self, result):
        self.assertFalse(result.is_error, result.content)
        return json.loads(result.content)

    def test_by_name_without_the_kind(self):
        found = self.read(self.find({"name": "Nudge Beta Works", "topic": "contact partner"}))
        self.assertIn("res.partner", found["looked_in"])
        names = [(entry["model"], entry["id"]) for entry in found["candidates"]]
        self.assertEqual(names[0], ("res.partner", self.other.id))
        self.assertIn("This is it", found["note"])
        entry = found["candidates"][0]
        self.assertEqual(entry["kind"], "Contact")
        self.assertIsInstance(entry["buttons"], list)

    def test_by_reference(self):
        found = self.read(self.find({"name": "BETA-1", "model": "res.partner"}))
        self.assertEqual([entry["id"] for entry in found["candidates"]], [self.other.id])
        self.assertEqual(found["looked_in"], "res.partner")

    def test_several_are_asked_about(self):
        found = self.read(self.find({"name": "Nudge Acme Trading", "model": "res.partner"}))
        ids = {entry["id"] for entry in found["candidates"]}
        self.assertIn(self.acme.id, ids)
        self.assertIn(self.twin.id, ids)
        self.assertIn("ask the person which one", found["note"])
        # The exact names come before the partial one
        self.assertNotEqual(found["candidates"][0]["id"], self.secret.id)

    def test_what_cannot_be_read_is_not_found(self):
        self.assertIn("no model", self.find({"name": "ab", "model": "ir.config_parameter"}).content)
        nothing = self.find({"name": "Nowhere Nobody Never", "model": "res.partner"})
        self.assertFalse(nothing.is_error)
        self.assertIn("Nothing called", nothing.content)
        for mode, user in (("talk", self.agent), ("agent", self.user_a)):
            names = [tool.name for tool in tools.base.available(self.env(user=user), mode)]
            self.assertNotIn("find_record", names)

    def test_told_to_the_model(self):
        self.assertIn("find_record", prompt.AGENT_MODE)

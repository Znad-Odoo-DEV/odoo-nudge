import json

from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.llm import prompt
from odoo.addons.nudge.tools import assessment

from .common import NudgeCase


@tagged("post_install", "-at_install")
class TestAssessment(NudgeCase, TransactionCase):
    """Is this database fit for this kind of business?"""

    def assess(self, business, user=None):
        return tools.base.call(self.env(user=user or self.user_a), "assess_database", {"business": business})

    def test_the_kind_of_business_is_read_in_both_languages(self):
        self.assertEqual(assessment.business_of("شركة مقاولات"), "contracting")
        self.assertEqual(assessment.business_of("a construction company"), "contracting")
        self.assertEqual(assessment.business_of("شركة تجارة عامة"), "trading")
        self.assertEqual(assessment.business_of("Shipping and logistics"), "shipping")
        self.assertIsNone(assessment.business_of("a circus"))

    def test_what_is_there_and_what_is_missing(self):
        result = self.assess("شركة مقاولات")
        self.assertFalse(result.is_error, result.content)
        found = json.loads(result.content)
        self.assertEqual(found["business"], "contracting")
        installed = {entry["module"]: entry for entry in found["installed"]}
        missing = {entry["module"]: entry for entry in found["missing"]}
        self.assertEqual(set(installed) | set(missing),
                         {module for module, _why, _words in assessment.BUSINESSES["contracting"]["needs"]})
        self.assertNotIn("project", installed, "Not installed on this test database")
        self.assertIn("project", missing)
        self.assertIn("for", missing["project"])
        pages = [entry["documentation"] for entry in found["installed"] + found["missing"] if "documentation" in entry]
        self.assertTrue(pages, "The official documentation is cited")
        self.assertTrue(all(page["url"].startswith("https://") for page in pages))
        self.assertIn("Say what you do not know", found["note"])

    def test_an_unknown_business_is_not_guessed(self):
        result = self.assess("a circus")
        self.assertFalse(result.is_error)
        self.assertIn("No reference", result.content)
        self.assertIn("ask what it does", result.content)

    def test_for_everybody_and_told(self):
        for mode in ("talk", "agent"):
            names = [tool.name for tool in tools.base.available(self.env(user=self.user_a), mode)]
            self.assertIn("assess_database", names)
        self.assertIn("assess_database", prompt.SYSTEM_PERSONA)
        self.assertIn("senior", prompt.SYSTEM_PERSONA.lower())

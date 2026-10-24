"""NP-03, the skills that come with Nudge cover its tools: a question is offered the core tools and the tools of
the skills it brings, so a tool that is in no skill and not in the core could never be offered. Checked here:
every tool of Agent and Developer mode is in the core or in an active skill that works in that mode, every tool
a skill names exists, each test sentence brings its skill by the rules router (no AI), no skill is too long, and
every domain section taken out of the system prompt now lives in a skill."""
import re
from pathlib import Path

from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge.models.nudge_skill import MAX_INSTRUCTIONS
from odoo.addons.nudge.tools import base, groups, registry, skills as skill_rules

# What is not offered by a skill: how a skill is asked for, and the group request of a small model
INFRASTRUCTURE = {"use_skill", "more_tools"}
# The sections of the system prompt that moved into skills: one "### name" line, then the text, verbatim
REMOVED = Path(__file__).with_name("np03_removed_sections.md")


def squeeze(text):
    """The text with its lines and spaces joined: a section is found whatever its line breaks"""
    return re.sub(r"\s+", " ", text.replace("\\\n", "")).strip()


@tagged("post_install", "-at_install")
class TestSkillCoverage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.skills = cls.env["nudge.skill"].with_context(active_test=True).sudo().search([
            ("state", "=", "published"), ("origin", "=", "module"), ("company_id", "=", False)])

    def test_every_tool_is_core_or_in_a_skill(self):
        missing = []
        for name, tool in sorted(base.TOOLS.items()):
            # nudge_ai needs Odoo's own ai app: its skill ships with that module; a plugin's tools come with
            # the skills of that plugin (L3), checked when the plugin is reviewed
            if (name in INFRASTRUCTURE or tool.__module__.startswith("odoo.addons.nudge_ai")
                    or not registry.is_trusted_module(registry.module_of(tool))):
                continue
            for mode in (base.MODE_AGENT, base.MODE_DEV):
                if not groups.in_mode(tool, mode) or name in base.ESSENTIAL_BY_MODE[mode]:
                    continue
                if not any(name in skill_rules.split_names(skill.tool_names) for skill in self.skills
                           if skill._in_mode(mode)):
                    missing.append(f"{name} ({mode})")
        self.assertFalse(missing, "Tools no skill brings in a mode where the core does not have them: %s" % missing)

    def test_every_named_tool_exists(self):
        for skill in self.skills:
            unknown = [name for name in skill_rules.split_names(skill.tool_names) if name not in base.TOOLS]
            self.assertFalse(unknown, "%s names tools that are not here: %s" % (skill.key, unknown))

    def test_every_test_sentence_brings_its_skill(self):
        for skill in self.skills:
            self.assertTrue(skill.test_sentence and skill.test_expect, "%s has no test sentence" % skill.key)
            state, detail = skill._self_test()
            self.assertEqual(state, "ok", "%s: %s" % (skill.key, detail))

    def test_every_skill_has_words_in_both_languages(self):
        for skill in self.skills:
            words = skill_rules.split_words(skill.trigger_words)
            self.assertTrue(any(word.isascii() for word in words), "%s has no English word" % skill.key)
            self.assertTrue(any(not word.isascii() for word in words), "%s has no Arabic word" % skill.key)
            self.assertLessEqual(len(skill.summary), skill_rules.MAX_SUMMARY)

    def test_no_skill_is_over_the_cap(self):
        for skill in self.skills:
            self.assertLessEqual(len(skill.instructions), MAX_INSTRUCTIONS, skill.key)

    def test_the_sections_taken_out_of_the_prompt_are_in_skills(self):
        if not REMOVED.exists():
            self.skipTest("no section was taken out of the system prompt")
        sections = re.split(r"^### (.+)$", REMOVED.read_text(encoding="utf-8"), flags=re.M)[1:]
        held = squeeze("\n".join(self.skills.mapped("instructions")))
        self.assertTrue(sections, "the list of sections is empty")
        lost = [title for title, body in zip(sections[0::2], sections[1::2]) if squeeze(body) not in held]
        self.assertFalse(lost, "Sections of the old system prompt that no skill holds: %s" % lost)

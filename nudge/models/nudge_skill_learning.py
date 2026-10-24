"""Skills that grow from lessons and stay true (NP-19).

Three approved lessons about one app propose a draft skill to the managers: the lessons put together,
the screens of the app as the words that bring it. Grouping is by rules: the app of a lesson is the
first part of a model it names ("sale.order" is the sale app), or of the screen of the conversation it
was learnt in. A draft is never sent to a model before a manager publishes it.

The weekly health check runs the test sentence of every active skill, without any AI, and writes a
red line in the business notes for each one that fails. A red line is for the managers: it is never
sent to a model.
"""
import logging
import re

from odoo import _, api, fields, models

from ..tools import skills as skill_rules

_logger = logging.getLogger(__name__)

LESSONS_PER_SKILL = 3
# A technical model name inside a lesson: "sale.order", "account.move.line"
MODEL_NAME = re.compile(r"\b([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)\b")


class NudgeNote(models.Model):
    _inherit = "nudge.note"

    kind = fields.Selection(
        selection_add=[("skill", "Skill Proposal"), ("alert", "Skill Alert")],
        ondelete={"skill": "cascade", "alert": "cascade"})
    skill_id = fields.Many2one(
        "nudge.skill", string="Skill", readonly=True, index=True, ondelete="set null",
        help="For a lesson, the skill it was put into; for a proposal or an alert, the skill it is about.")

    @api.model
    def _scope_domain(self):
        # A proposal or an alert is for the managers: never sent to a model. The lessons of a published
        # skill come with that skill, when a question needs it, no longer with every question
        domain = [*super()._scope_domain(), ("kind", "not in", ("skill", "alert"))]
        # sudo: which skills are published, a fact of Nudge's own configuration, not a business record
        published = self.env["nudge.skill"].sudo().search([("state", "=", "published")])
        if published:
            domain.append(("skill_id", "not in", published.ids))
        return domain

    def action_approve(self):
        result = super().action_approve()
        if any(note.kind == "lesson" for note in self):
            self.env["nudge.skill"]._propose_from_lessons()
        return result

    def action_refuse(self):
        result = super().action_refuse()
        # Refusing the proposal rejects its draft: archived, never deleted
        drafts = self.filtered(lambda note: note.kind == "skill").skill_id.filtered(
            lambda skill: skill.active and skill.state == "draft")
        if drafts:
            drafts.action_reject()
        return result

    def action_open_skill(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "res_model": "nudge.skill", "res_id": self.skill_id.id,
            "view_mode": "form", "views": [(False, "form")], "target": "current",
        }

    # ------------------------------------------------------------- grouping
    def _app_and_models(self):
        """:return: ``(app, models)`` this lesson is about, by rules: the models it names that exist here,
        else the screen of the conversation it was learnt in; ``(False, [])`` when nothing tells"""
        self.ensure_one()
        named = [name for name in MODEL_NAME.findall(self.body or "") if name in self.env]
        if not named and self.conversation_id:
            # sudo: the screens of the conversation the lesson came from, read for their model only
            questions = self.env["nudge.message"].sudo().search([
                ("conversation_id", "=", self.conversation_id.id), ("role", "=", "user")], order="id desc", limit=5)
            for question in questions:
                screen = question.snapshot if isinstance(question.snapshot, dict) else {}
                if screen.get("model") in self.env:
                    named = [screen["model"]]
                    break
        if not named:
            return False, []
        return named[0].split(".")[0], sorted(set(named))


class NudgeSkill(models.Model):
    _inherit = "nudge.skill"

    def action_publish(self):
        result = super().action_publish()
        self._close_proposals("approved")
        return result

    def action_reject(self):
        result = super().action_reject()
        self._close_proposals("refused")
        return result

    def _close_proposals(self, state):
        """The business note that proposed these drafts follows the manager's decision on the skill"""
        proposals = self.env["nudge.note"].search([
            ("kind", "=", "skill"), ("skill_id", "in", self.ids), ("state", "=", "proposed")])
        if proposals:
            proposals.write({"state": state, "approved_by": self.env.uid if state == "approved" else False})

    @api.model
    def _app_label(self, app, model_names):
        """:return: ``(label, is a module)``: the name of the app, else of the first kind of record named"""
        # sudo: the name of a module, a fact about the database
        module = self.env["ir.module.module"].sudo().search([("name", "=", app)], limit=1)
        if module:
            return module.shortdesc or app, True
        named = [self.env[name]._description for name in model_names if name in self.env]
        return (named[0] if named else app), False

    @api.model
    def _free_key(self, base_key):
        key, number = base_key, 1
        while self.with_context(active_test=False).search_count([("key", "=", key)]):
            number += 1
            key = f"{base_key}-{number}"
        return key

    @api.model
    def _propose_from_lessons(self):
        """Group the approved lessons not yet in a skill by app; three or more about one app become a
        draft skill, with a business note that waits for a manager. No AI: the lessons are put together
        as they were approved.

        :return: the draft skills made"""
        Note = self.env["nudge.note"]
        lessons = Note.search([
            ("kind", "=", "lesson"), ("state", "=", "approved"), ("skill_id", "=", False),
            ("company_id", "in", [self.env.company.id, False])], order="id")
        by_app = {}
        for lesson in lessons:
            app, names = lesson._app_and_models()
            if app:
                entry = by_app.setdefault((app, lesson.company_id.id), {"lessons": Note, "models": set()})
                entry["lessons"] |= lesson
                entry["models"].update(names)
        made = self.browse()
        for (app, company_id), entry in sorted(by_app.items(), key=lambda item: (item[0][0], item[0][1] or 0)):
            if len(entry["lessons"]) < LESSONS_PER_SKILL:
                continue
            models_named = sorted(entry["models"])
            label, is_module = self._app_label(app, models_named)
            descriptions = [self.env[name]._description for name in models_named if name in self.env]
            # The name of the app brings it only when it is one: "res" of res.partner would bring it to "reset"
            words = sorted({*([app] if is_module else []), *models_named,
                            *(part.strip() for text in descriptions for part in (text or "").split("/") if part.strip())})
            skill = self.create({
                "key": self._free_key(f"lessons-{app.replace('_', '-')}")[:63],
                "name": _("What we learnt: %s", label),
                "summary": f"Rules this company taught Nudge about {label}: read them before answering about it."[
                    :skill_rules.MAX_SUMMARY],
                "instructions": f"## What this company taught about {label}\n" + "".join(
                    f"- {' '.join((lesson.body or '').split())}\n" for lesson in entry["lessons"]),
                "trigger_words": ", ".join(words),
                "model_names": ",".join(models_named),
                "modes": "talk,agent,dev",
                "company_id": company_id or False,
                "state": "draft",
                "origin": "lessons",
                "test_sentence": descriptions[0] if descriptions else models_named[0],
            })
            entry["lessons"].write({"skill_id": skill.id})
            Note.create({
                "name": _("Draft skill from %(count)s lessons: %(app)s", count=len(entry["lessons"]),
                                   app=label)[:200],
                "body": _(
                    "Lessons about %(app)s were put together as a draft skill. Review it, publish it or reject "
                    "it: until it is published, no model reads it.", app=label),
                "kind": "skill", "state": "proposed", "skill_id": skill.id, "company_id": company_id or False,
                "proposed_by": self.env.uid, "reason": _("From approved lessons."),
            })
            made |= skill
        return made

    # ------------------------------------------------------------ the week
    @api.model
    def _weekly_check(self):
        """The part of the weekly health check about skills: run every active skill's test sentence, a red
        line in the business notes for each that fails, and the drafts the lessons call for.
        :return: the skills that failed"""
        skills = self.search([("company_id", "in", [self.env.company.id, False])])
        failed = skills._run_self_tests()
        Note = self.env["nudge.note"].with_context(active_test=False)
        for skill in skills:
            name = _("Skill test failed: %s", skill.name)[:200]
            alerts = Note.search([("kind", "=", "alert"), ("skill_id", "=", skill.id),
                                  ("company_id", "=", self.env.company.id)])
            if skill not in failed:
                alerts.filtered("active").write({"active": False})
                continue
            values = {
                "name": name, "kind": "alert", "state": "proposed", "skill_id": skill.id, "active": True,
                "company_id": self.env.company.id,
                "body": _("%(detail)s Test sentence: %(sentence)s", detail=skill.test_detail or "",
                                   sentence=skill.test_sentence or ""),
                "reason": _("The weekly health check."), "proposed_by": self.env.uid,
            }
            if alerts:
                alerts[:1].write(values)
            else:
                Note.create(values)
        self._propose_from_lessons()
        return failed


class NudgeHealthReport(models.Model):
    _inherit = "nudge.health.report"

    @api.model
    def run_check(self, pack=False):
        report = super().run_check(pack=pack)
        if not pack:
            try:
                with self.env.cr.savepoint():
                    self.env["nudge.skill"]._weekly_check()
            except Exception:  # noqa: BLE001 - the report of the database stands without the skills
                _logger.exception("Nudge: the skill tests of company %s failed", self.env.company.id)
        return report

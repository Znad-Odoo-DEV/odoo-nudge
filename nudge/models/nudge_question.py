"""Recurring questions: what people keep asking Nudge that nobody has answered once and for all.

Every question asked in a conversation leaves a fingerprint: the meaningful words of the
question, brought to one form whatever the dialect (``llm.fingerprint``), hashed. A row keeps the
hash, one sample of the question with no address or long number in it, a counter and the counts
of the last thirty days. It keeps nothing of who asked.

Once a week, or when a manager asks, the questions asked often enough this month are compared
with the remembered answers and the recorded tours (the same TF-IDF as the answer memory, and the
same fingerprint): those with neither are proposed to the managers, who keep an answer, record
a tour or ignore the question. No provider is called, ever."""
import json
import logging
from datetime import timedelta

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import AccessError, UserError

from ..llm import fingerprint, memory_match
from ..tools.bus import notify_user
from .nudge_memory import MAX_SEARCHED, SAFE_TOOLS

_logger = logging.getLogger(__name__)

MIN_COUNT_PARAM = "nudge.recurring_min_count"
RETENTION_PARAM = "nudge.recurring_retention_days"
DEFAULT_MIN_COUNT = 5
DEFAULT_RETENTION_DAYS = 90
WINDOW_DAYS = 30
MAX_CANDIDATES = 50
SCANNED_MESSAGES = 3000
MAX_NOTIFIED = 3


class NudgeQuestion(models.Model):
    _name = "nudge.question"
    _description = "Nudge Recurring Question"
    _order = "recent_count desc, id desc"
    _rec_name = "sample"

    if hasattr(models, "Constraint"):
        _unique_question = models.Constraint("unique(company_id, fingerprint)", "A question is counted once in a company.")
    else:  # Odoo 17 and 18
        _sql_constraints = [("unique_question", "unique(company_id, fingerprint)", "A question is counted once in a company.")]

    company_id = fields.Many2one(
        "res.company", required=True, index=True, ondelete="cascade", default=lambda self: self.env.company)
    fingerprint = fields.Char(required=True, index=True, readonly=True)
    sample = fields.Char(string="Question", readonly=True,
                         help="One way the question was asked, without addresses or long numbers.")
    count = fields.Integer(string="Times Asked", readonly=True)
    recent_count = fields.Integer(string="This Month", readonly=True, index=True,
                                  help="Times asked in the last thirty days.")
    daily = fields.Text(readonly=True, help="How many times a day, for the last thirty days.")
    last_asked = fields.Datetime(readonly=True)
    status = fields.Selection(
        [("watching", "Watching"), ("proposed", "To Decide"), ("covered", "Covered"), ("muted", "Ignored")],
        default="watching", required=True, index=True)
    covered_by = fields.Char(readonly=True)
    active = fields.Boolean(default=True)

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    @api.model
    def _min_count(self):
        try:
            value = int(self.env["ir.config_parameter"].sudo().get_param(MIN_COUNT_PARAM) or 0)
        except ValueError:
            value = 0
        return value if value > 0 else DEFAULT_MIN_COUNT

    @api.model
    def _retention_days(self):
        try:
            value = int(self.env["ir.config_parameter"].sudo().get_param(RETENTION_PARAM) or 0)
        except ValueError:
            value = 0
        return value if value > 0 else DEFAULT_RETENTION_DAYS

    # ------------------------------------------------------------------
    # Counting
    # ------------------------------------------------------------------
    @api.model
    def _record(self, text):
        """Count one more time that ``text`` was asked in the company in use. Never raises: a
        question is answered whatever happens here."""
        try:
            key = fingerprint.of(text)
            if not key:
                return
            with self.env.cr.savepoint():
                self._bump(key, text)
        except Exception:  # noqa: BLE001
            _logger.info("Nudge: a question could not be counted", exc_info=True)

    @api.model
    def _bump(self, key, text):
        company = self.env.company
        today = fields.Date.context_today(self)
        # As the superuser, not sudo: the person who asks is not written down as the author of the row
        Question = self.with_user(SUPERUSER_ID).with_context(active_test=False)
        # The row exists, and is locked while it is counted: of two people asking the same thing at the same
        # instant, the second reads what the first wrote and adds to it
        self.env.cr.execute(
            "INSERT INTO nudge_question (company_id, fingerprint, sample, count, recent_count, daily, status, active, "
            "create_uid, create_date, write_uid, write_date) "
            "VALUES (%s, %s, %s, 0, 0, '{}', 'watching', true, %s, now() AT TIME ZONE 'UTC', %s, now() AT TIME ZONE 'UTC') "
            "ON CONFLICT (company_id, fingerprint) DO NOTHING",
            [company.id, key, fingerprint.sample(text), SUPERUSER_ID, SUPERUSER_ID])
        self.env.cr.execute(
            "SELECT id FROM nudge_question WHERE company_id = %s AND fingerprint = %s FOR UPDATE", [company.id, key])
        row = Question.browse(self.env.cr.fetchone()[0])
        row.invalidate_recordset()
        if not row.active:
            # Archived for being old: it counts again from now
            row.write({"active": True, "count": 0, "daily": "{}", "recent_count": 0,
                       "status": "muted" if row.status == "muted" else "watching"})
        days = self._days(row.daily)
        day = today.isoformat()
        days[day] = days.get(day, 0) + 1
        days = self._pruned(days, today)
        row.write({
            "count": row.count + 1,
            "daily": json.dumps(days),
            "recent_count": sum(days.values()),
            "last_asked": fields.Datetime.now(),
        })

    @api.model
    def _days(self, text):
        try:
            found = json.loads(text or "{}")
        except ValueError:
            return {}
        return {key: int(number) for key, number in found.items()} if isinstance(found, dict) else {}

    @api.model
    def _pruned(self, days, today):
        first = (today - timedelta(days=WINDOW_DAYS - 1)).isoformat()
        return {key: number for key, number in days.items() if key >= first}

    # ------------------------------------------------------------------
    # Proposing
    # ------------------------------------------------------------------
    @api.model
    def _covering(self, company):
        """:return: ``(memories, tours)``, each ``[(id, name, text, fingerprint)]`` of the texts that
        answer a question in this company: approved remembered answers, recorded tours"""
        # sudo: reads what answers people of the company, to tell a manager what is answered
        Memory = self.env["nudge.memory"].sudo()
        # A reference answers its question and every other wording it is found by (S15)
        memories = [
            (memory.id, memory.question, text, fingerprint.of(text))
            for memory in Memory.search([
                ("state", "=", "approved"), ("company_id", "in", [company.id, False])], limit=MAX_SEARCHED)
            for text in [memory.question] + memory._variant_lines()]
        tours = []
        for tour in self.env["nudge.tour"].sudo().with_context(active_test=True).search([]):
            texts = [tour.name, tour.with_context(lang="en_US").name]
            texts += [line.strip() for line in (tour.phrases_en or "").splitlines() + (tour.phrases_ar or "").splitlines()]
            tours += [(tour.id, tour.name, text, fingerprint.of(text)) for text in dict.fromkeys(texts) if text]
        return memories, tours

    @api.model
    def _covered_by(self, row, memories, tours, threshold):
        """:return: what already answers the question of ``row`` (a sentence), or ""."""
        for kind, rows in ((_("Remembered answer"), memories), (_("Tour"), tours)):
            for _id, name, _text, print_key in rows:
                if print_key and print_key == row.fingerprint:
                    return "%s: %s" % (kind, name)
            key, _score = memory_match.best(
                row.sample, [(item[0], item[2]) for item in rows], threshold, margin=0.0)
            if key:
                return "%s: %s" % (kind, next(item[1] for item in rows if item[0] == key))
        return ""

    @api.model
    def _refresh(self, company):
        """Archive what is old, count the last thirty days again and decide which questions are to
        be proposed to the managers.

        :return: the questions proposed this time that were not before"""
        self._check_manager()
        today = fields.Date.context_today(self)
        Question = self.with_user(SUPERUSER_ID).with_context(active_test=False)
        now = fields.Datetime.now()
        # Nothing is deleted: a question not asked for the whole retention is archived
        Question.search([
            ("company_id", "=", company.id), ("active", "=", True),
            ("last_asked", "<", now - timedelta(days=self._retention_days()))]).write({"active": False})
        active = Question.search([("company_id", "=", company.id), ("active", "=", True)])
        for row in active:
            days = self._pruned(self._days(row.daily), today)
            total = sum(days.values())
            if total != row.recent_count or json.dumps(days) != row.daily:
                row.write({"daily": json.dumps(days), "recent_count": total})
        minimum = self._min_count()
        # Fewer than the minimum now: back to watching
        active.filtered(lambda row: row.status in ("proposed", "covered") and row.recent_count < minimum).write(
            {"status": "watching", "covered_by": False})
        candidates = active.filtered(lambda row: row.status != "muted" and row.recent_count >= minimum)
        candidates = candidates.sorted(lambda row: (-row.recent_count, row.id))[:MAX_CANDIDATES]
        memories, tours = self._covering(company)
        threshold = self.env["nudge.memory"]._get_threshold()
        fresh = Question.browse()
        for row in candidates:
            covered = self._covered_by(row, memories, tours, threshold)
            if covered:
                row.write({"status": "covered", "covered_by": covered})
            else:
                if row.status != "proposed":
                    fresh |= row
                row.write({"status": "proposed", "covered_by": False})
        return fresh

    @api.model
    def _check_manager(self):
        if not self.env.user.has_group("nudge.group_nudge_manager") and not self.env.is_superuser():
            raise AccessError(_("Only Nudge managers decide on recurring questions."))

    @api.model
    def action_find(self):
        """On demand: look for the recurring questions of the company now.
        :return: the list of the questions to decide on"""
        self._check_manager()
        fresh = self._refresh(self.env.company)
        action = self.env["ir.actions.actions"]._for_xml_id("nudge.nudge_question_action")
        action["context"] = {"search_default_proposed": 1}
        if fresh:
            self._notify(self.env.company, fresh)
        return action

    @api.model
    def _notify(self, company, rows):
        """Tell the managers of the company what is to decide, in one message"""
        top = rows.sorted(lambda row: (-row.recent_count, row.id))[:MAX_NOTIFIED]
        lines = [_(
            "“%(question)s” was asked %(count)s times this month.", question=row.sample[:80], count=row.recent_count)
            for row in top]
        message = "\n".join(lines + [_(
            "Keep an answer, record a tour or ignore it: Nudge, What Nudge Knows, Recurring Questions.")])
        managers = self.env["res.users"].sudo().search([
            ("share", "=", False), ("company_ids", "in", company.id)]).filtered(
            lambda user: user.has_group("nudge.group_nudge_manager"))
        for user in managers:
            try:
                notify_user(user, "simple_notification", {
                    "title": _("Questions that keep coming back"), "message": message, "type": "info"})
            except Exception:  # noqa: BLE001
                _logger.info("Nudge: a manager could not be told of the recurring questions", exc_info=True)

    @api.model
    def _cron_find(self):
        """Every week, for every company: the same as a manager asking, without a person to show it to"""
        for company in self.env["res.company"].sudo().search([]):
            try:
                with self.env.cr.savepoint():
                    fresh = self.with_company(company)._refresh(company)
                    if fresh:
                        self.with_company(company)._notify(company, fresh)
            except Exception:  # noqa: BLE001
                _logger.exception("Nudge: the recurring questions of company %s could not be found", company.id)
            if not self.env.context.get("nudge_keep_transaction"):
                self.env.cr.commit()

    # ------------------------------------------------------------------
    # What a manager decides
    # ------------------------------------------------------------------
    def action_ignore(self):
        self._check_manager()
        self.with_user(SUPERUSER_ID).write({"status": "muted", "covered_by": False})
        return True

    def action_watch(self):
        self._check_manager()
        self.with_user(SUPERUSER_ID).write({"status": "watching"})
        return True

    def _best_answer(self):
        """:return: the answer Nudge gave to this question that people found best: with a thumbs-up
        if there is one, else one nobody rated down; only answers made of documentation, navigation
        and structure, never of the data of a person. Empty when there is none."""
        self.ensure_one()
        Message = self.env["nudge.message"].sudo()
        asked = Message.search([
            ("role", "=", "user"), ("conversation_id.company_id", "=", self.company_id.id),
            ("command", "=", False)], order="id desc", limit=SCANNED_MESSAGES)
        best = ""
        for question in asked:
            if fingerprint.of(question.content) != self.fingerprint:
                continue
            answer = Message.search([
                ("conversation_id", "=", question.conversation_id.id), ("id", ">", question.id),
                ("role", "=", "assistant"), ("state", "=", "complete"), ("content", "!=", False),
            ], order="id", limit=1)
            if (not answer or answer.feedback == "down" or "<nudge_found>" in (question.context_text or "")
                    or any(call.get("name") not in SAFE_TOOLS for call in answer.tool_calls or [])):
                continue
            if answer.feedback == "up":
                return answer.content
            best = best or answer.content
        return best

    def action_keep_answer(self):
        """Open the form of a remembered answer with this question and its best past answer"""
        self.ensure_one()
        self._check_manager()
        return {
            "type": "ir.actions.act_window", "res_model": "nudge.memory", "view_mode": "form",
            "views": [(False, "form")], "target": "current", "name": _("Remembered Answer"),
            "context": {
                "default_question": self.sample, "default_answer": self._best_answer(),
                "default_company_id": self.company_id.id, "default_state": "approved"},
        }

    def action_record_tour(self):
        """Start the recorder of tours, with the question as the name and the phrase to find it by"""
        self.ensure_one()
        self._check_manager()
        arabic = memory_match.language(self.sample) == "ar"
        return {
            "type": "ir.actions.client", "tag": "nudge_record_tour",
            "params": {"name": self.sample[:120], "phrases_en": "" if arabic else self.sample,
                       "phrases_ar": self.sample if arabic else ""},
        }

    def unlink(self):
        if not self.env.context.get("nudge_question_purge"):
            raise UserError(_("A counted question is not deleted: ignore it to stop proposing it."))
        return super().unlink()

    @api.autovacuum
    def _gc_old(self):
        """A question archived for twice the retention is gone: the table does not grow for ever"""
        gone = fields.Datetime.now() - timedelta(days=2 * self._retention_days())
        old = self.with_context(active_test=False, nudge_question_purge=True).sudo().search(
            [("active", "=", False), ("last_asked", "<", gone)], limit=1000)
        old.unlink()

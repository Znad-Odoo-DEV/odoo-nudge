"""Answer memory: answers people found useful, served again without the AI.

A thumbs-up on an explanation keeps the question and the answer, waiting for
a manager of Nudge (a manager's own thumbs-up counts at once). An approved
one answers the same question of someone else of the same company, who may
see it, before any provider is called. What changed data or quoted values of
the database is never kept: only how-to, navigation, documentation and setup.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from ..llm import memory_match
from ..tools.base import MODE_TALK

_logger = logging.getLogger(__name__)

ENABLED_PARAM = "nudge.answer_memory"
THRESHOLD_PARAM = "nudge.memory_threshold"
DEFAULT_THRESHOLD = 0.75
# Offered, not served, when no AI provider is set up to ask instead
NO_PROVIDER_FLOOR = 0.4
MAX_SEARCHED = 2000
# Memories compared with a new one on each thumbs-up: the newest of the company
MAX_KNOWN = 2000
MAX_PENDING_PER_USER = 20
MAX_ANSWER_CHARS = 6000
MIN_QUESTION_CHARS = 8
NO_TOKENS = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
# The tools whose results are the same for everybody: the documentation, the
# way through the menus, the structure of the database. Any other tool read
# or did something in this person's data
SAFE_TOOLS = frozenset((
    "search_odoo_documentation", "find_screen", "show_menu_path", "open_screen", "show_on_screen",
    "search_database_profile", "locate_data", "describe_model", "describe_module", "show_settings",
    "more_tools"))


class NudgeMemory(models.Model):
    _name = "nudge.memory"
    _inherit = ["nudge.recipe.mixin"]
    _description = "Nudge Remembered Answer"
    _order = "uses desc, id desc"
    _rec_name = "question"

    question = fields.Text(required=True)
    answer = fields.Text(required=True)
    language = fields.Selection(
        [("ar", "Arabic"), ("en", "English")], compute="_compute_language", store=True)
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, index=True,
        help="The company whose people are answered with it. Empty: all of them.")
    group_ids = fields.Many2many(
        "res.groups", string="For the Groups",
        help="Who may be answered with it. Empty: everybody who uses Nudge.")
    state = fields.Selection(
        [("proposed", "Proposed"), ("approved", "Approved"), ("refused", "Refused")],
        default="approved", required=True, index=True)
    active = fields.Boolean(default=True)
    uses = fields.Integer(string="Times Used", readonly=True)
    last_used = fields.Datetime(readonly=True)
    proposed_by = fields.Many2one("res.users", readonly=True)
    approved_by = fields.Many2one("res.users", readonly=True)
    conversation_id = fields.Many2one("nudge.conversation", readonly=True, ondelete="set null")

    @api.depends("question")
    def _compute_language(self):
        for memory in self:
            memory.language = memory_match.language(memory.question)

    def _embedding_text(self):
        self.ensure_one()
        return (self.question or "")[:1500]

    def write(self, values):
        result = super().write(values)
        if "question" in values:
            # Its vector said the old question: it is made again
            self.env["nudge.embedding"]._forget(self._name, self.ids)
        return result

    def action_approve(self):
        self._nudge_check_access("write")
        self.write({"state": "approved", "approved_by": self.env.uid})

    def action_refuse(self):
        self._nudge_check_access("write")
        self.write({"state": "refused"})

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    @api.model
    def _is_enabled(self):
        value = self.env["ir.config_parameter"].sudo().get_param(ENABLED_PARAM)
        return str(value or "").lower() in ("1", "true")

    @api.model
    def _get_threshold(self):
        try:
            value = float(self.env["ir.config_parameter"].sudo().get_param(THRESHOLD_PARAM) or 0)
        except ValueError:
            value = 0
        return value if 0 < value <= 1 else DEFAULT_THRESHOLD

    # ------------------------------------------------------------------
    # Keeping
    # ------------------------------------------------------------------
    @api.model
    def _propose_from(self, message):
        """Keep the question and the answer of ``message`` (a message of Nudge
        that its asker found useful), when it is the kind that may be kept.
        :return: the remembered answer, or an empty recordset"""
        # sudo: the asker rates their own message; the history around it is read in full,
        # and the table is written by Nudge, for a manager to decide on
        self = self.sudo()  # noqa: PLW0642
        none = self.browse()
        message = message.sudo()
        if not self._is_enabled() or message.is_remembered or message.state != "complete":
            return none
        answer = (message.content or "").strip()
        conversation = message.conversation_id
        earlier = conversation.message_ids.filtered(lambda other: other.id < message.id)
        question = earlier.filtered(lambda other: other.role == "user")[-1:]
        if not question or not answer or len(answer) > MAX_ANSWER_CHARS:
            return none
        text = " ".join((question.content or "").split())
        # Talk mode, typed or said, with no screen, no file, no command and
        # nothing Nudge found in the database for it: only words
        if (question.mode not in (False, MODE_TALK) or question.command or question.snapshot
                or question.attachment_name or len(text) < MIN_QUESTION_CHARS
                or "<nudge_found>" in (question.context_text or "") or conversation.run_ids):
            return none
        # Every tool called for this answer must be one of the safe ones
        between = earlier.filtered(lambda other: other.id > question.id) | message
        for turn in between:
            if any(call.get("name") not in SAFE_TOOLS for call in turn.tool_calls or []):
                return none
        user = self.env.user
        manager = user.has_group("nudge.group_nudge_manager")
        if not manager and self.search_count([
                ("state", "=", "proposed"), ("proposed_by", "=", user.id)]) >= MAX_PENDING_PER_USER:
            return none
        company = self.env.company
        folded = memory_match.fold(text)
        known = self.with_context(active_test=False).search(
            [("company_id", "in", [company.id, False])], order="id desc", limit=MAX_KNOWN)
        for other in known:
            if memory_match.fold(" ".join(other.question.split())) == folded and other.answer.strip() == answer:
                return none
        return self.create({
            "question": text,
            "answer": answer,
            "company_id": company.id,
            "state": "approved" if manager else "proposed",
            "proposed_by": user.id,
            "approved_by": user.id if manager else False,
            "conversation_id": conversation.id,
        })

    # ------------------------------------------------------------------
    # Serving
    # ------------------------------------------------------------------
    @api.model
    def _user_groups(self, user):
        # v19 names it all_group_ids; 17 and 18 groups_id (implied groups included)
        user = user.sudo()
        return user.all_group_ids if "all_group_ids" in user._fields else user.groups_id

    @api.model
    def _find(self, text, floor=False):
        """:param floor: offer the best match above a lower floor, instead of
            requiring confidence
        :return: ``(remembered answer, score)`` for ``text``, among the approved
            ones of this company that this person may see; ``(None, score)`` otherwise"""
        groups = self._user_groups(self.env.user)
        # sudo: people have no access to the table; what they may see is decided here
        pool = self.sudo().search([
            ("state", "=", "approved"), ("company_id", "in", [self.env.company.id, False]),
        ], limit=MAX_SEARCHED)
        language = memory_match.language(text)
        pool = pool.filtered(
            lambda memory: memory.language == language and (not memory.group_ids or memory.group_ids & groups))
        threshold = self._get_threshold()
        # By meaning too, when the local search model is there; None otherwise
        semantic = self.env["nudge.embedding"]._semantic(self._name, text, ids=pool.ids)
        key, score = memory_match.best(
            text, [(memory.id, memory.question) for memory in pool],
            min(threshold, NO_PROVIDER_FLOOR) if floor else threshold,
            margin=0.0 if floor else 0.05, semantic=semantic)
        return (pool.browse(key) if key else None), score

    @api.model
    def _chat_serve(self, conversation, text, mode, focus, found, explain, background, fresh=False):
        """Answer ``text`` from memory, when it can be, before any provider is called.
        :return: the job of the stream (the answer is already written), or None to go on"""
        if (fresh or mode != MODE_TALK or focus or found or background or explain == "screen"
                or not self._is_enabled()):
            return None
        provider = self.env["nudge.provider"].sudo()._get_default()
        memory, _score = self._find(text, floor=not provider)
        if not memory:
            return None
        return self._answer_with(conversation, text, memory)

    def _answer_with(self, conversation, text, memory):
        """Write the question and ``memory`` as its answer, at no cost, and
        return the job of the stream"""
        record = conversation.sudo()
        if not record.name:
            record.name = text[:60].strip()
        Message = self.env["nudge.message"].sudo()
        question = Message.create({
            "conversation_id": conversation.id, "role": "user", "content": text, "mode": MODE_TALK})
        answer = Message.create({
            "conversation_id": conversation.id, "role": "assistant", "content": memory.answer,
            "state": "complete", "is_remembered": True, "memory_id": memory.id})
        self._count_use(memory, conversation=conversation, message=answer)
        return {
            "conversation_id": conversation.id,
            "user_message_id": question.id,
            "title": record.name,
            "mode": MODE_TALK,
            "first_events": [{"type": "delta", "text": memory.answer}],
            # Ends the stream after the events above: no provider is asked
            "answered": {"type": "done", "message_id": answer.id, "state": "complete", "message": "",
                         "memory": {"id": memory.id}},
        }

    @api.model
    def _count_use(self, memory, conversation=None, message=None):
        # One statement: two people served at once both count, and no row is held for the request
        self.env.cr.execute(
            "UPDATE nudge_memory SET uses = COALESCE(uses, 0) + 1, last_used = now() at time zone 'utc' WHERE id = %s",
            (memory.id,))
        memory.invalidate_recordset(["uses", "last_used"])
        # A call that cost nothing: the usage report shows how many the memory answered
        links = {}
        if conversation:
            links.update(conversation_id=conversation.id, message_id=message.id)
        self.env["nudge.usage"]._add("memory", dict(NO_TOKENS), 0.0, self.env.user, self.env.company, **links)

    @api.model
    def suggest(self, words):
        """Without any AI provider: the remembered answer that best fits ``words``.
        :return: ``{"id", "question", "answer"}`` or False"""
        if not self.env.user.has_group("nudge.group_nudge_user"):
            raise AccessError(_("Nudge is not open to you."))
        text = " ".join(str(words or "").split())[:500]
        if not self._is_enabled() or len(text) < 2:
            return False
        memory, _score = self._find(text, floor=True)
        if not memory:
            return False
        self._count_use(memory)
        return {"id": memory.id, "question": memory.question, "answer": memory.answer}

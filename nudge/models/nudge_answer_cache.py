"""Answer cache: the same question, asked again, does not cost a second provider call.

S7. An answer of Talk mode that called no tool outside the safe ones (documentation, the way through
the menus, the structure of the database), shared no record of the screen and held nothing personal is
kept, and given again to a person with the same rights, in the same company, on the same kind of screen,
as long as it is fresh. A question that nearly matches is offered, marked as an earlier answer, with the
button that asks the provider again. Nothing here calls a model: the match is plain Python on the words.

What the answer depends on is in the key: the words, the mode, the language, the model and view type of
the screen (never a record), the groups of the person, the company, the version of Nudge and the skills
they may be given. A change of a skill, of a lesson or of the settings empties the cache.
"""
import hashlib
import json
import logging
import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from ..llm import memory_match
from ..llm import redact
from ..llm import snapshot as screen
from ..tools.base import MODE_TALK
from .nudge_skill import INLINE_PARAM as SKILLS_INLINE_PARAM
from .nudge_memory import MAX_ANSWER_CHARS, MIN_QUESTION_CHARS, NO_TOKENS, SAFE_TOOLS

_logger = logging.getLogger(__name__)

TTL_PARAM = "nudge.cache_ttl_days"
NEAR_PARAM = "nudge.cache_near_threshold"
DEFAULT_TTL_DAYS = 7
DEFAULT_NEAR = 0.85
MAX_NEAR_SEARCHED = 500
# What a screen may carry for its answer to be kept: where the person is, never what they look at
SCREEN_KEYS = frozenset(("v", "model", "view_type", "buttons", "tabs", "search", "trimmed", "action", "menu"))
# A key of a provider or a bearer, which no answer may hold
SECRET = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{20,}|Bearer\s+[A-Za-z0-9._-]{16,})")
# What of a skill or a note changes what the model is told
SKILL_KEYS = frozenset((
    "state", "version", "active", "summary", "instructions", "trigger_words", "model_names", "tool_names",
    "modes", "group_ids", "company_id", "key"))
NOTE_KEYS = frozenset(("name", "body", "state", "active", "group_ids", "company_id", "kind"))


def _digest(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class NudgeAnswerCache(models.Model):
    _name = "nudge.answer.cache"
    _description = "Nudge Cached Answer"
    _order = "hits desc, id desc"
    _rec_name = "question"

    question = fields.Text(readonly=True, required=True)
    answer = fields.Text(readonly=True, required=True)
    qkey = fields.Char(string="Words", readonly=True, required=True, index=True,
                       help="The question as the cache reads it: its words, folded and cut to their stems.")
    scope = fields.Char(readonly=True, required=True, index=True,
                        help="What the answer depends on besides the words, as one fingerprint.")
    mode = fields.Char(readonly=True)
    language = fields.Selection([("ar", "Arabic"), ("en", "English")], readonly=True)
    screen_model = fields.Char(string="Screen Model", readonly=True)
    view_type = fields.Char(string="View Type", readonly=True)
    company_id = fields.Many2one("res.company", readonly=True, index=True, ondelete="cascade")
    version = fields.Char(readonly=True)
    stored_on = fields.Datetime(string="Kept On", readonly=True, default=fields.Datetime.now, index=True)
    hits = fields.Integer(string="Served", readonly=True)
    near_hits = fields.Integer(string="Offered as Close", readonly=True)
    last_hit = fields.Datetime(string="Last Served", readonly=True)
    tokens = fields.Integer(
        string="Tokens of the Call", readonly=True, help="What the provider call that wrote it used.")
    cost = fields.Float(string="Cost of the Call", digits=(12, 6), readonly=True)
    tokens_saved = fields.Integer(string="Tokens Saved", compute="_compute_saved")
    cost_saved = fields.Float(string="Cost Saved", digits=(12, 6), compute="_compute_saved")

    @api.depends("hits", "tokens", "cost")
    def _compute_saved(self):
        for entry in self:
            entry.tokens_saved = entry.hits * entry.tokens
            entry.cost_saved = entry.hits * entry.cost

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    @api.model
    def _ttl_days(self):
        try:
            days = float(self.env["ir.config_parameter"].sudo().get_param(TTL_PARAM) or DEFAULT_TTL_DAYS)
        except ValueError:
            days = DEFAULT_TTL_DAYS
        return days if days > 0 else DEFAULT_TTL_DAYS

    @api.model
    def _near_threshold(self):
        try:
            value = float(self.env["ir.config_parameter"].sudo().get_param(NEAR_PARAM) or DEFAULT_NEAR)
        except ValueError:
            value = DEFAULT_NEAR
        return value if 0.5 <= value <= 1 else DEFAULT_NEAR

    @api.model
    def _switches(self):
        """:return: ``(exact, near)`` for the company in use; nothing in private mode"""
        company = self.env.company.sudo()
        if company.nudge_private_mode:
            return False, False
        return bool(company.nudge_cache_exact), bool(company.nudge_cache_exact and company.nudge_cache_near)

    @api.model
    def _fresh_since(self):
        return fields.Datetime.subtract(fields.Datetime.now(), days=self._ttl_days())

    # ------------------------------------------------------------------
    # The key
    # ------------------------------------------------------------------
    @api.model
    def _groups_of(self, user):
        # v19 names it all_group_ids; 17 and 18 groups_id (implied groups included)
        user = user.sudo()
        return user.all_group_ids if "all_group_ids" in user._fields else user.groups_id

    @api.model
    def _skills_signature(self, user):
        """:return: the skills this person may be given, as the ids and versions that decide what is read"""
        groups = self._groups_of(user)
        skills = self.env["nudge.skill"].sudo().search([
            ("state", "=", "published"), ("company_id", "in", [self.env.company.id, False])])
        skills = skills.filtered(lambda skill: not skill.group_ids or skill.group_ids & groups)
        return sorted((skill.id, skill.version or "") for skill in skills)

    @api.model
    def _version(self):
        module = self.env["ir.module.module"].sudo().search([("name", "=", "nudge")], limit=1)
        return module.latest_version or ""

    @api.model
    def _screen_of(self, snapshot):
        """:return: ``(model, view type)`` of the screen, ``("", "")`` without one, or None when the
        screen carries what the person looks at (the answer might quote it)"""
        clean = screen.sanitize(snapshot) if snapshot else None
        if not clean:
            return "", ""
        if set(clean) - SCREEN_KEYS:
            return None
        return str(clean.get("model") or ""), str(clean.get("view_type") or "")

    @api.model
    def _key(self, text, mode, snapshot):
        """:return: the values of the key for ``text``, or None when this question is not for the cache"""
        text = " ".join((text or "").split())
        if len(text) < MIN_QUESTION_CHARS:
            return None
        stems = memory_match.words(text)
        where = self._screen_of(snapshot)
        if not stems or where is None:
            return None
        user, company = self.env.user, self.env.company
        language = memory_match.language(text)
        version = self._version()
        scope = _digest(
            company.id, mode, language, where, sorted(self._groups_of(user).ids), version,
            self._skills_signature(user),
            # How the skills reach the model changes what it reads
            self.env["ir.config_parameter"].sudo().get_param(SKILLS_INLINE_PARAM))
        return {
            "question": text[:500], "qkey": " ".join(stems), "scope": scope, "mode": mode, "language": language,
            "screen_model": where[0], "view_type": where[1], "company_id": company.id, "version": version}

    # ------------------------------------------------------------------
    # Serving
    # ------------------------------------------------------------------
    @api.model
    def _chat_serve(self, conversation, text, mode, snapshot, blocked=False):
        """Answer ``text`` from the cache, when it can be, before any provider is called.

        A question that goes on to the provider and may be kept is marked on the conversation, for
        the answer to be kept when it comes.

        :param blocked: the question carries more than its words (a file, a command, a record, a
            language asked, the voice, a new try)
        :return: the job of the stream (the answer is already written), or None to go on"""
        record = conversation.sudo()
        exact, near = self._switches()
        key = None
        # An answer to a later question of a conversation depends on the earlier ones
        if (mode == MODE_TALK and exact and not blocked and not record.summary_text
                and not self.env["nudge.message"].sudo().search_count([
                    ("conversation_id", "=", record.id), ("role", "=", "user")])):
            key = self._key(text, mode, snapshot)
        if not key:
            if record.cache_pending:
                record.cache_pending = False
            return None
        pool = self.sudo().search([("scope", "=", key["scope"]), ("stored_on", ">=", self._fresh_since())])
        entry = pool.filtered(lambda other: other.qkey == key["qkey"])[:1]
        offered = False
        if not entry and near:
            entry, offered = self._closest(pool, key["qkey"]), True
        if entry:
            if record.cache_pending:
                record.cache_pending = False
            return self._answer_with(conversation, text, entry, offered)
        record.cache_pending = json.dumps(key)
        return None

    @api.model
    def _closest(self, pool, qkey):
        """:return: the entry of ``pool`` whose words are the most alike to ``qkey``, above the
        threshold and clearly ahead of the next, or an empty recordset"""
        asked = set(qkey.split())
        threshold = self._near_threshold()
        scored = []
        for entry in pool[:MAX_NEAR_SEARCHED]:
            other = set(entry.qkey.split())
            union = asked | other
            if len(asked) >= 2 and len(other) >= 2 and union:
                scored.append((len(asked & other) / len(union), entry.id))
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        if not scored or scored[0][0] < threshold or (len(scored) > 1 and scored[0][0] - scored[1][0] < 0.05):
            return self.browse()
        return self.browse(scored[0][1])

    def _answer_with(self, conversation, text, entry, offered):
        """Write the question and the cached answer, at no cost, and return the job of the stream"""
        record = conversation.sudo()
        if not record.name:
            record.name = text[:60].strip()
        content = entry.answer
        if offered:
            content = _(
                "An earlier answer to a very close question (%(question)s). Press Ask the AI instead "
                "to ask again.", question=entry.question) + "\n\n" + entry.answer
        Message = self.env["nudge.message"].sudo()
        question = Message.create({
            "conversation_id": conversation.id, "role": "user", "content": text, "mode": MODE_TALK})
        answer = Message.create({
            "conversation_id": conversation.id, "role": "assistant", "content": content,
            "state": "complete", "is_remembered": True})
        # One statement: two people served at once both count, and no row is held for the request
        self.env.cr.execute(
            "UPDATE nudge_answer_cache SET hits = COALESCE(hits, 0) + %s, near_hits = COALESCE(near_hits, 0) + %s, "
            "last_hit = now() at time zone 'utc' WHERE id = %s", (0 if offered else 1, 1 if offered else 0, entry.id))
        entry.invalidate_recordset(["hits", "near_hits", "last_hit"])
        # A call that cost nothing: the usage report shows how many the cache answered
        self.env["nudge.usage"]._add(
            "cache", dict(NO_TOKENS), 0.0, self.env.user, self.env.company,
            conversation_id=conversation.id, message_id=answer.id)
        return {
            "conversation_id": conversation.id,
            "user_message_id": question.id,
            "title": record.name,
            "mode": MODE_TALK,
            "first_events": [{"type": "delta", "text": content}],
            "answered": {"type": "done", "message_id": answer.id, "state": "complete", "message": "",
                         "cache": {"id": entry.id, "near": bool(offered)}},
        }

    # ------------------------------------------------------------------
    # Keeping
    # ------------------------------------------------------------------
    @api.model
    def _keep_from(self, message):
        """Keep ``message``, an answer of Nudge just written, when it answers a question marked for the
        cache and is the kind that may be kept. Never raises: the answer does not wait for this."""
        # sudo: the cache is written by Nudge for the people of a company to be served from
        self = self.sudo()  # noqa: PLW0642
        conversation = message.conversation_id.sudo()
        if not conversation.cache_pending:
            return
        try:
            with self.env.cr.savepoint():
                self._keep(conversation, message.sudo())
        except Exception:  # noqa: BLE001
            _logger.warning("Nudge: an answer could not be kept in the cache", exc_info=True)

    def _keep(self, conversation, message):
        key = json.loads(conversation.cache_pending)
        turn = self.env["nudge.message"].sudo().search([("conversation_id", "=", conversation.id)], order="id")
        users = turn.filtered(lambda other: other.role == "user")
        question = users[-1:]
        later = turn.filtered(lambda other: other.id > question.id) if question else turn.browse()
        # Every tool called for this answer must be one of the safe ones
        if any(call.get("name") not in SAFE_TOOLS for other in later for call in other.tool_calls or []):
            conversation.cache_pending = False
            return
        if message.tool_calls:
            return
        conversation.cache_pending = False
        answer = (message.content or "").strip()
        events = [event for event in message.screen_events or [] if event.get("type") != "fallback"]
        if (not question or len(users) != 1 or not answer or len(answer) > MAX_ANSWER_CHARS or events
                or message.state != "complete" or message.is_remembered
                or self.env.company.sudo().nudge_private_mode
                or (question.mode not in (False, MODE_TALK) or question.command or question.is_spoken
                    or question.attachment_name or question.wants_pointer or question.noai_intent
                    or "<nudge_found>" in (question.context_text or ""))
                or memory_match.language(answer) != key["language"]
                or redact.Redactor().redact(answer) != answer or SECRET.search(answer)):
            return
        assistants = later.filtered(lambda other: other.role == "assistant")
        tokens = sum(
            sum(assistants.mapped(name))
            for name in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"))
        values = dict(key, answer=answer, stored_on=fields.Datetime.now(), tokens=tokens,
                      cost=sum(assistants.mapped("cost")))
        old = self.search([("scope", "=", key["scope"]), ("qkey", "=", key["qkey"])], order="id desc")
        if old:
            # Asked again of the provider: the newer answer replaces the older
            old[:1].write(values)
            old[1:].unlink()
        else:
            self.create(values)

    # ------------------------------------------------------------------
    # Invalidation, cleaning, the managers' list
    # ------------------------------------------------------------------
    @api.model
    def _invalidate(self):
        """A skill, a lesson or a setting changed: what was kept was written for the old one"""
        # sudo: the cache is Nudge's own; the person who changed a skill may not read it
        self.sudo().search([]).unlink()

    @api.model
    def _cron_purge(self):
        """Forget the answers older than the time they are kept for"""
        self.sudo().search([("stored_on", "<", self._fresh_since())]).unlink()

    def action_clear_all(self):
        if not self.env.user.has_group("nudge.group_nudge_manager"):
            raise AccessError(_("Only the managers of Nudge empty the cache."))
        self._invalidate()
        return {"type": "ir.actions.client", "tag": "reload"}


class NudgeConversationCache(models.Model):
    _inherit = "nudge.conversation"

    cache_pending = fields.Char(
        groups="base.group_system", copy=False,
        help="The key of the question being answered, when its answer may be kept in the answer cache.")


class NudgeMessageCache(models.Model):
    _inherit = "nudge.message"

    @api.model_create_multi
    def create(self, vals_list):
        messages = super().create(vals_list)
        for message in messages:
            if message.role == "assistant" and message.state == "complete" and not message.is_remembered:
                self.env["nudge.answer.cache"]._keep_from(message)
        return messages


class NudgeUsageCache(models.Model):
    _inherit = "nudge.usage"

    kind = fields.Selection(selection_add=[("cache", "Cached Answer")], ondelete={"cache": "set default"})


class NudgeSkillCache(models.Model):
    _inherit = "nudge.skill"

    @api.model_create_multi
    def create(self, vals_list):
        skills = super().create(vals_list)
        if any(vals.get("state") == "published" for vals in vals_list):
            self.env["nudge.answer.cache"]._invalidate()
        return skills

    def write(self, vals):
        result = super().write(vals)
        # The counter of the times a skill was given moves with every question: it changes nothing
        if SKILL_KEYS & set(vals):
            self.env["nudge.answer.cache"]._invalidate()
        return result

    def unlink(self):
        self.env["nudge.answer.cache"]._invalidate()
        return super().unlink()


class NudgeNoteCache(models.Model):
    _inherit = "nudge.note"

    @api.model_create_multi
    def create(self, vals_list):
        notes = super().create(vals_list)
        if any(vals.get("state", "approved") == "approved" for vals in vals_list):
            self.env["nudge.answer.cache"]._invalidate()
        return notes

    def write(self, vals):
        result = super().write(vals)
        if NOTE_KEYS & set(vals):
            self.env["nudge.answer.cache"]._invalidate()
        return result

    def unlink(self):
        self.env["nudge.answer.cache"]._invalidate()
        return super().unlink()


class ResCompanyCache(models.Model):
    _inherit = "res.company"

    # Not required: the column is added to companies that exist
    nudge_cache_exact = fields.Boolean(
        string="Answer Cache", default=True, copy=False,
        help="The same question, asked again with the same rights on the same kind of screen, is "
             "answered from what was kept instead of asking the AI provider again.")
    nudge_cache_near = fields.Boolean(
        string="Offer Close Answers", default=False, copy=False,
        help="A question that nearly matches an earlier one is offered the earlier answer, marked as "
             "such, with a button to ask the provider again.")


class ResConfigSettingsCache(models.TransientModel):
    _inherit = "res.config.settings"

    nudge_cache_exact = fields.Boolean(related="company_id.nudge_cache_exact", readonly=False)
    nudge_cache_near = fields.Boolean(related="company_id.nudge_cache_near", readonly=False)
    nudge_cache_ttl_days = fields.Integer(
        string="Keep Answers For (Days)", config_parameter=TTL_PARAM, default=DEFAULT_TTL_DAYS)
    nudge_cache_near_threshold = fields.Float(
        string="Closeness Required", config_parameter=NEAR_PARAM, default=DEFAULT_NEAR,
        help="How alike the words of two questions must be, from 0.5 to 1, for the earlier answer to be offered.")

    def set_values(self):
        result = super().set_values()
        # Settings steer what the model says: what was kept may no longer be what it would answer
        self.env["nudge.answer.cache"]._invalidate()
        return result

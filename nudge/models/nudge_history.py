import datetime

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..llm import loop
from ..tools import history
from ..tools.records import cut

# A summary or a draft is kept this long, unless the chatter changed
KEEP_MINUTES = 15
HISTORY_QUESTION = "Summarise what happened with this record."
SUMMARY_KIND = "summary"


class NudgeHistory(models.Model):
    """What Nudge wrote about a record for a person: a summary of its history, a
    draft of a reply. Kept for 15 minutes for the person who asked, unless
    the chatter of the record moved since: the last message is what tells.

    Kept per person: what a summary holds depends on what that person may read."""
    _name = "nudge.history"
    _description = "Nudge Record Summary"
    _order = "id desc"

    user_id = fields.Many2one("res.users", required=True, index=True, ondelete="cascade")
    model = fields.Char(required=True)
    res_id = fields.Integer(required=True)
    kind = fields.Char(required=True, default=SUMMARY_KIND)
    last_message_id = fields.Integer()
    date = fields.Datetime(required=True, default=fields.Datetime.now)
    text = fields.Text()
    data = fields.Text()

    @api.autovacuum
    def _gc_old(self):
        """Nothing is kept for longer than a day"""
        old = fields.Datetime.now() - datetime.timedelta(days=1)
        self.sudo().search([("date", "<", old)]).unlink()

    # ------------------------------------------------------------------
    # The memory
    # ------------------------------------------------------------------
    @api.model
    def _last_message_id(self, record):
        """:return: the id of the last message of the chatter, as the person reads it. What is
        written to Nudge and what Nudge answers do not count: they say nothing new of the record"""
        domain = [("model", "=", record._name), ("res_id", "=", record.id), ("body", "not ilike", "@Nudge")]
        nudge = self.env["ir.model.data"]._xmlid_to_res_id("nudge_discuss.partner_nudge", raise_if_not_found=False)
        if nudge:
            domain += [("author_id", "!=", nudge), ("partner_ids", "not in", [nudge])]
        return self.env["mail.message"].search(domain, order="id desc", limit=1).id or 0

    @api.model
    def _kept(self, record, kind):
        """:return: what was written for this person about the record, when the
        chatter did not change since and it is not older than 15 minutes"""
        row = self.sudo().search([
            ("user_id", "=", self.env.uid), ("model", "=", record._name), ("res_id", "=", record.id),
            ("kind", "=", kind)], limit=1)
        fresh = fields.Datetime.now() - datetime.timedelta(minutes=KEEP_MINUTES)
        if row and row.date >= fresh and row.last_message_id == self._last_message_id(record):
            return row
        return self.browse()

    @api.model
    def _keep(self, record, kind, text, data=False):
        rows = self.sudo().search([
            ("user_id", "=", self.env.uid), ("model", "=", record._name), ("res_id", "=", record.id),
            ("kind", "=", kind)])
        rows.unlink()
        return self.sudo().create({
            "user_id": self.env.uid, "model": record._name, "res_id": record.id, "kind": kind,
            "last_message_id": self._last_message_id(record), "text": text, "data": data,
        })

    # ------------------------------------------------------------------
    # One question to the provider
    # ------------------------------------------------------------------
    @api.model
    def _ask(self, focus, question, block, title, answer_in=None):
        """Ask the provider one thing, as the person, in a conversation of its
        own: what the call costs is written down as for any question.

        :param block: what the model may know, framed as data
        :return: the answer
        :raise UserError: when there is none"""
        # Often asked by a tool while the person's own conversation is streaming:
        # the inner call is not a second question of theirs, so the busy check
        # of the person lets it through (its cost is written down all the same)
        conversation = self.env["nudge.conversation"].create({"name": cut(title, 60)})
        job = conversation._chat_prepare(question, focus=focus, extra=block, answer_in=answer_in, inner=True)
        for _event in loop.run(job, conversation._chat_record):
            pass
        Message = self.env["nudge.message"].sudo()
        answer = Message.search([
            ("conversation_id", "=", conversation.id), ("role", "=", "assistant"), ("content", "!=", False),
        ], order="id desc", limit=1)
        if not answer.content:
            last = Message.search([("conversation_id", "=", conversation.id)], order="id desc", limit=1)
            raise UserError(last.error_message or _("Nudge had no answer."))
        return answer.content

    @api.model
    def _user_language(self):
        """:return: the name of the language of the person, for the model to write in"""
        user = self.env.user
        return self.env["res.lang"]._lang_get(user.lang or "en_US").name or user.lang or "en_US"

    # ------------------------------------------------------------------
    # The summary
    # ------------------------------------------------------------------
    @api.model
    def _summary(self, record, refresh=False):
        """:param record: a record the person may read
        :return: ``{"text", "cached"}``: the history of the record, summarised"""
        record.ensure_one()
        record._nudge_check_access("read")
        if not refresh:
            kept = self._kept(record, SUMMARY_KIND)
            if kept:
                return {"text": kept.text, "cached": True}
        block = history.render(self.env, record)
        text = self._ask(
            "history", HISTORY_QUESTION, block, f"{record.display_name}: {HISTORY_QUESTION}",
            answer_in=self._user_language())
        self._keep(record, SUMMARY_KIND, text)
        return {"text": text, "cached": False}

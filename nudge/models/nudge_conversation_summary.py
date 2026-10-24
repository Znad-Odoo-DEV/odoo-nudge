import logging

from odoo import api, fields, models
from odoo.modules import module as odoo_module

from .. import tools
from ..llm import summary

_logger = logging.getLogger(__name__)

SUMMARY_PARAM = "nudge.summary_after_turns"
IMPROVE_BATCH = 5
IMPROVE_SECONDS = 60


class NudgeConversationSummary(models.Model):
    """NP-22 and PERF-08: the part of a long conversation that is no longer replayed to the model
    is replaced by a short summary kept here; the messages themselves stay in the panel."""
    _inherit = "nudge.conversation"

    summary_text = fields.Text(
        string="Summary of the Earlier Messages", copy=False,
        help="What Nudge reads in place of the earlier messages, once the conversation is long. "
             "You can correct it: your text is kept as you wrote it. Clear it to have it made again.")
    summary_extra = fields.Text(
        string="Added After Your Edit", copy=False, readonly=True,
        help="What was left out of the replay after you edited the summary, noted by Nudge.")
    summary_upto_id = fields.Integer(copy=False, help="The last message the summary covers.")
    summary_count = fields.Integer(string="Messages Summarised", copy=False, readonly=True)
    summary_edited = fields.Boolean(string="Edited by the Owner", copy=False, readonly=True)
    summary_ai = fields.Boolean(string="Improved by the Provider", copy=False, readonly=True)
    summary_dirty = fields.Boolean(copy=False, index="btree_not_null")
    summary_editable = fields.Boolean(compute="_compute_summary_editable")

    @api.depends_context("uid")
    def _compute_summary_editable(self):
        for conversation in self:
            conversation.summary_editable = conversation.user_id == self.env.user

    # ------------------------------------------------------------------
    # The rule
    # ------------------------------------------------------------------
    @api.model
    def _summary_get_turns(self):
        """:return: after how many turns the old ones are summarised; 0 is never"""
        value = self.env["ir.config_parameter"].sudo().get_param(SUMMARY_PARAM)
        if value in (None, False, ""):
            return summary.DEFAULT_AFTER_TURNS
        try:
            return max(int(float(value)), 0)
        except ValueError:
            return summary.DEFAULT_AFTER_TURNS

    def _summary_cut(self, messages):
        """:return: the id of the first message replayed whole, 0 when all of them are"""
        self.ensure_one()
        latest = messages.filtered(lambda message: message.role == "user")[-1:]
        items = []
        for message in messages:
            tokens = max(summary.estimate_tokens(message.content),
                         summary.estimate_tokens(message.content_blocks))
            tokens += summary.estimate_tokens(message.tool_calls) + summary.estimate_tokens(message.tool_results)
            if message == latest:
                # Only the screen of the latest question is replayed
                tokens += summary.estimate_tokens(message.context_text)
            items.append((message.id, message.role, tokens))
        return summary.first_kept(items, self.summary_upto_id, self._summary_get_turns())

    def _summary_lines(self, messages):
        """:return: the summary, made with no AI, of ``messages``"""
        self.ensure_one()
        steps = self.env["nudge.step"].sudo().search([("message_id", "in", messages.ids)])
        by_message = {}
        for step in steps:
            by_message.setdefault(step.message_id.id, []).append({
                "kind": step.kind, "state": step.state, "summary": step.summary, "tool": step.tool,
                "res_model": step.res_model, "record_names": step.record_names})
        return summary.extractive(
            [{"role": message.role, "content": message.content, "steps": by_message.get(message.id, [])}
             for message in messages],
            self.user_id.sudo().name)

    def _summary_block(self, messages):
        """:return: ``(kept messages, the summary as the model reads it)``; the whole history and
            an empty text when nothing is summarised. Writes nothing."""
        self.ensure_one()
        conversation = self.sudo()
        cut = conversation._summary_cut(messages)
        if not cut:
            return messages, ""
        kept = messages.filtered(lambda message: message.id >= cut)
        text = conversation.summary_text or ""
        extra = conversation.summary_extra or ""
        # Not stored yet (the next question stores it): what ages out now is read from the messages
        fresh = messages.filtered(lambda message: conversation.summary_upto_id < message.id < cut)
        if fresh:
            more = conversation._summary_lines(fresh)
            if conversation.summary_edited:
                extra = summary.fit("\n".join(part for part in (extra, more) if part))
            else:
                text = summary.fit("\n".join(part for part in (text, more) if part))
        body = "\n".join(part for part in (text, extra) if part)
        return kept, summary.frame(body) if body else ""

    def _summary_refresh(self):
        """Store what the next question will not replay: called when the question is prepared,
        which makes no call to a provider. The improvement of the wording is another job."""
        self.ensure_one()
        conversation = self.sudo()
        messages = conversation.message_ids.sorted("id")
        question = messages.filtered(lambda message: message.role == "user")[-1:]
        if question.command in tools.commands.FOCUS:
            return
        cut = conversation._summary_cut(messages)
        fresh = messages.filtered(
            lambda message: conversation.summary_upto_id < message.id < cut) if cut else messages.browse()
        if not fresh:
            return
        more = conversation._summary_lines(fresh)
        values = {"summary_upto_id": fresh[-1].id, "summary_count": conversation.summary_count + len(fresh)}
        if conversation.summary_edited:
            values["summary_extra"] = summary.fit("\n".join(
                part for part in (conversation.summary_extra, more) if part))
        else:
            values["summary_text"] = summary.fit("\n".join(
                part for part in (conversation.summary_text, more) if part))
            # The wording is improved later by the provider, if the person has one
            values["summary_dirty"] = bool(more) and conversation._summary_get_turns() > 0
        conversation.write(values)

    # ------------------------------------------------------------------
    # The improvement, in the background
    # ------------------------------------------------------------------
    @api.model
    def _cron_improve_summaries(self, batch=IMPROVE_BATCH):
        """Ask the provider for a shorter wording of the summaries made with no AI, a few at a
        time, never in the request of a person. The call is written in the ledger of usage and
        counts against the budgets of the owner; private mode, no provider or a limit reached
        leave the summary as it is. A summary the owner edited is never touched.

        :return: how many summaries were improved"""
        Provider = self.env["nudge.provider"]
        improved = 0
        conversations = self.sudo().search(
            [("summary_dirty", "=", True)], order="write_date, id", limit=batch)
        for conversation in conversations:
            upto, text = conversation.summary_upto_id, conversation.summary_text
            conversation.summary_dirty = False
            if conversation.summary_edited or not text or self._summary_get_turns() <= 0:
                continue
            company = conversation.company_id or self.env.company
            system, prompt = summary.improve_prompt(text)
            answer = Provider.with_company(company)._complete(
                system, prompt, conversation.user_id, company,
                timeout=IMPROVE_SECONDS, conversation_id=conversation.id)
            answer = summary.fit((answer or "").strip())
            conversation.invalidate_recordset(["summary_upto_id", "summary_edited"])
            if answer and conversation.summary_upto_id == upto and not conversation.summary_edited:
                conversation.write({"summary_text": answer, "summary_ai": True})
                improved += 1
            elif answer:
                # The conversation moved on while the provider was asked: improve it again
                conversation.summary_dirty = True
            if not odoo_module.current_test:
                self.env.cr.commit()
        return improved

"""S6: the economy tier of a provider answers the light work, the model of the provider the rest.

What is light and what is heavy is decided with no AI (``llm/tier.py``). A provider gets an economy
model of its own (a cheaper model of the same provider and key), or the economy provider of Settings
answers for it (another provider, in the cloud or on a server of one's own). With neither, every
question goes to the model of the provider, as before. Each line of the ledger says which tier
answered and, for the economy tier, about what it saved; the budgets count both."""
import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..llm import base, prompt, tier
from .nudge_conversation import MODE_TALK

_logger = logging.getLogger(__name__)

TIERS = [(tier.MAIN, "Main Model"), (tier.ECONOMY, "Economy Model")]


class NudgeUsage(models.Model):
    _inherit = "nudge.usage"

    tier = fields.Selection(
        TIERS, readonly=True,
        help="Which model answered: the model of the provider, or its economy tier, which answers the "
             "light work. Empty on the lines written before the tiers, and on what is not an answer.")
    cost_saved = fields.Float(
        string="Saved", digits=(12, 6), readonly=True,
        help="What the economy tier saved on this call: its tokens at the prices of the model of the "
             "provider, less what they cost. An estimate; 0 when the price of the economy model is not "
             "known.")

    @api.model
    def _add(self, kind, usage, cost, user, company, **links):
        # The answer being recorded says which tier gave it (nudge.conversation._chat_record)
        context = self.env.context
        if "tier" not in links and context.get("nudge_answer_tier"):
            links["tier"] = context["nudge_answer_tier"]
            Provider = self.env["nudge.provider"].sudo()
            main = Provider.browse(context.get("nudge_main_provider_id") or []).exists()
            answering = Provider.browse(links.get("provider_id") or []).exists()
            if links["tier"] == tier.ECONOMY and main and answering and "cost_saved" not in links:
                links["cost_saved"] = main._cost_saved(
                    base.normalize_usage(usage), answering, context.get("nudge_priced_model") or answering.model_name)
        return super()._add(kind, usage, cost, user, company, **links)


class NudgeSkill(models.Model):
    _inherit = "nudge.skill"

    is_heavy = fields.Boolean(
        string="Heavy",
        help="A question given this skill is answered by the model of the provider, never by its economy "
             "tier: for work that needs the stronger model even in Talk.")


class NudgeConversation(models.Model):
    _inherit = "nudge.conversation"

    answer_tier = fields.Selection(
        TIERS, string="Answered By", readonly=True, copy=False,
        help="The tier answering the question being answered.")
    main_provider_id = fields.Many2one(
        "nudge.provider", string="Main Provider", readonly=True, copy=False, ondelete="set null",
        help="The provider whose economy tier answers the question being answered, and who answers "
             "instead when it fails.")

    def _chat_job(self, provider, mode, rounds):
        """S6: the economy tier answers the light work of ``provider``; the job is built for the one
        answering (its tools, its instructions), and the conversation says which one it is, for what
        is recorded of the answer and for one escalation to ``provider`` (``nudge_tier_main`` in the
        context: ``provider`` answers, as it does for the provider standing in, NP-10)"""
        economy = None
        if provider and not self.env.context.get("nudge_tier_main"):
            economy = self._chat_economy(provider, mode)
        if not economy:
            self._chat_answer_with(provider, provider.model_name if provider else False, tier.MAIN)
            return super()._chat_job(provider, mode, rounds)
        answering, model = economy
        if answering == provider:
            answering._ensure_economy_capabilities()
        else:
            answering._ensure_capabilities()
        job = super()._chat_job(answering, mode, rounds)
        if self._chat_has_heavy_skill(job.get("system")):
            _logger.info("Nudge: a heavy skill joined the question of conversation %s: %s answers it",
                         self.id, provider.model_name)
            self._chat_answer_with(provider, provider.model_name, tier.MAIN)
            return job if answering == provider else super()._chat_job(provider, mode, rounds)
        if answering == provider:
            job["adapter"] = provider._get_economy_adapter()
        self._chat_answer_with(answering, model, tier.ECONOMY, main=provider)
        return job

    def _chat_economy(self, provider, mode):
        """:return: ``(provider, model)`` of the economy tier for the question being answered, or None
        when the model of ``provider`` answers it: a mode that acts or builds, no economy tier, or a
        heavy question (llm/tier.py)"""
        self.ensure_one()
        if mode != MODE_TALK:
            return None
        economy = provider._get_economy()
        if not economy:
            return None
        question = self.env["nudge.message"].sudo().search([
            ("conversation_id", "=", self.id), ("role", "=", "user")], order="id desc", limit=1)
        reason = tier.heavy_reason(question.content, attachment=bool(question.attachment_name))
        if reason:
            _logger.debug("Nudge: question %s of conversation %s is heavy (%s)", question.id, self.id, reason)
            return None
        return economy

    def _chat_has_heavy_skill(self, system):
        """:param system: the system blocks of the job
        :return: whether the instructions of a skill marked heavy came with the question"""
        given = system[-1] if system else ""
        if not prompt.is_question_block(given):
            return False
        # sudo: only the texts are compared with what the question was already given
        heavy = self.env["nudge.skill"].sudo().search([("is_heavy", "=", True)])
        return any(text and text in given for text in (
            (skill.instructions or "").strip("\n") for skill in heavy))

    def _chat_answer_with(self, provider, model, level, main=None):
        """Write down who answers the question: the provider, its model and the tier"""
        conversation = self.sudo()
        if not conversation:
            return
        main = main or provider
        values = {}
        if conversation.answer_tier != level:
            values["answer_tier"] = level
        if conversation.main_provider_id != main:
            values["main_provider_id"] = main.id if main else False
        if provider and conversation.provider_id != provider:
            values["provider_id"] = provider.id
        if model and conversation.model_name != model:
            values["model_name"] = model
        if values:
            conversation.write(values)

    def _chat_record(self, outcome):
        """S6: what the economy tier failed at (an error, an empty answer, a refusal, a tool that
        failed) is asked once of the model of the provider, in the same round (as NP-10 does, with
        the same event). The rest is recorded with the prices of the model that answered, and the
        line of the ledger says which tier it was."""
        conversation = self.sudo()
        level = conversation.answer_tier or tier.MAIN
        economy = level == tier.ECONOMY
        if economy:
            reason = tier.escalation(
                outcome, same_provider=conversation.provider_id == conversation.main_provider_id)
            switch = self._chat_escalate(reason, outcome) if reason else None
            if switch:
                return {"fallback": switch}
        recording = self.with_context(
            nudge_answer_tier=level, nudge_priced_model=conversation.model_name if economy else False,
            nudge_main_provider_id=conversation.main_provider_id.id)
        step = super(NudgeConversation, recording)._chat_record(outcome)
        if (economy and outcome.get("tool_calls") and step.get("turns") is not None
                and not step.get("screen_events") and not outcome.get("fell_back")
                and conversation.answer_tier == tier.ECONOMY and self._chat_tools_failed()):
            switch = self._chat_escalate("tool_error")
            if switch:
                return {"fallback": switch}
        return step

    def _chat_tools_failed(self):
        """:return: whether a tool of the last call failed"""
        result = self.env["nudge.message"].sudo().search([
            ("conversation_id", "=", self.id), ("role", "=", "tool")], order="id desc", limit=1)
        return tier.tool_failed(result.tool_results)

    def _chat_escalate(self, reason, outcome=None):
        """The model of the provider answers instead of its economy tier, once: the stream asks it the
        same, in the same round, with what was read and done so far.

        :param reason: what the economy tier gave ("error", "empty", "refusal", "tool_error")
        :param outcome: the call that failed, written in the ledger all the same
        :return: what the stream needs to ask it, or None when it cannot answer"""
        conversation = self.sudo()
        main = conversation.main_provider_id
        if not main or not main.active or not main.is_configured:
            return None
        try:
            main._check_private()
        except UserError:
            return None
        answering, model = conversation.provider_id, conversation.model_name
        if outcome is not None and (outcome.get("stop") or outcome.get("error")):
            usage = base.normalize_usage((outcome.get("stop") or {}).get("usage"))
            self.env["nudge.usage"]._add(
                "chat", usage, answering._compute_cost(usage, model), conversation.user_id,
                conversation.company_id, conversation_id=self.id, provider_id=answering.id,
                model_name=model, tier=tier.ECONOMY)
        _logger.info("Nudge: the economy model %s of conversation %s gave %s: %s answers instead",
                     model, self.id, reason, main.model_name)
        main._ensure_capabilities()
        job = self.with_context(nudge_tier_main=True)._chat_job(main, self._chat_mode(), 1)
        return {
            "adapter": job["adapter"],
            "system": job["system"],
            "tools": job["tools"],
            "turns": job["turns"],
            "notice": self._chat_fallback_notice(main, "escalated"),
        }

    def _chat_fall_back(self, outcome):
        # NP-10: the provider standing in answers with its own model, never with an economy tier
        return super(NudgeConversation, self.with_context(nudge_tier_main=True))._chat_fall_back(outcome)

    def _chat_run_tools(self, calls):
        # What the tools do is not the answer being recorded: the tier and the prices of the answer
        # stay with it, never with a call a tool makes of its own
        clean = self.with_context(nudge_answer_tier=False, nudge_priced_model=False, nudge_main_provider_id=False)
        return super(NudgeConversation, clean)._chat_run_tools(calls)

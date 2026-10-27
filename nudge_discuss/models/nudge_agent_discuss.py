"""A Nudge agent in Discuss.

A published binding of channel "discuss" puts the agent's partner into the channels it lists and lets
people write to it in a direct chat. A message that mentions it in such a channel, or any message in a
direct chat with it, is answered by ``agent._answer`` as the person who wrote it (never sudo for data),
in the background like @Nudge, and posted as the agent's partner. One conversation is kept per
(channel, author). A person the agent is not open to is told so once a day per channel.

What is posted in a channel or a group is read by its members, and by guests, who have no user: it is answered
from knowledge only, whoever the members are (no record is read, only the remembered answers meant for
everyone). Only a direct chat of the author and the agent alone is answered with the author's records.
"""
import logging
import re
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.nudge.models.nudge_agent import check_access
from odoo.addons.nudge.tools.records import cut

from .nudge_mention import CHANNEL, MAX_QUESTION_CHARS, text_of, to_html

_logger = logging.getLogger(__name__)

# Context key the engine honours (nudge_agent_engine): ``nudge.agent._answer`` answers from knowledge only (the
# documentation of Odoo, the skills, the remembered answers kept for no group), offers no tool that reads records
# and no rule of Nudge that does. Set for every answer posted where others read it: every channel and group,
# whoever its members are. Stored on the conversation when it starts, never changed.
KNOWLEDGE_ONLY = "nudge_agent_knowledge_only"
DISCUSS = "discuss"
CHAT = "chat"
# A person is told once in this time, per channel, that the agent is not open to them
REFUSAL_HOURS = 24


class NudgeAgentBinding(models.Model):
    _inherit = "nudge.agent.binding"

    channel_ids = fields.Many2many(
        "discuss.channel", "nudge_agent_binding_channel_rel", "binding_id", "channel_id",
        string="Discuss Channels", domain=[("channel_type", "in", ("channel", "group"))],
        help="The channels the agent joins while this binding is published. Mention it there to ask it.")

    @api.constrains("channel_ids", "agent_id")
    def _check_discuss_channels(self):
        """Only channels and groups, never a direct chat or a livechat; and the one who sets them must be
        in each channel (or may write to it), and the channel must belong to a company of the agent"""
        writer = self.env.user
        for binding in self:
            # sudo: every channel listed, also those the writer may not read (a many2many read as them leaves
            # those out, and they are the ones to refuse)
            record = binding.sudo()
            channels = record.channel_ids
            if any(channel.channel_type not in ("channel", "group") for channel in channels):
                raise ValidationError(self.env._("Only channels and groups can be listed, not chats."))
            companies = record.agent_id.company_ids
            for channel in channels:
                if "company_id" in channel._fields and channel.company_id \
                        and channel.company_id not in companies:
                    raise ValidationError(self.env._("A channel belongs to a company the agent is not in."))
                if self.env.su:
                    continue
                if writer.partner_id in channel.channel_member_ids.partner_id:
                    continue
                try:
                    check_access(channel.with_user(writer), "write")
                except AccessError:
                    raise ValidationError(
                        self.env._("You can only list the channels you are a member of.")) from None

    @api.model_create_multi
    def create(self, vals_list):
        bindings = super().create(vals_list)
        for agent in bindings.agent_id:
            agent._discuss_sync()
        return bindings

    def write(self, vals):
        if not {"published", "channel", "channel_ids", "agent_id"} & set(vals):
            return super().write(vals)
        agents = self.agent_id
        # The channels the binding leaves are no longer in it afterwards
        left = {agent.id: agent._discuss_bound() for agent in agents}
        result = super().write(vals)
        for agent in agents | self.agent_id:
            agent._discuss_sync(left.get(agent.id))
        return result

    def unlink(self):
        agents = self.agent_id
        left = {agent.id: agent._discuss_bound() for agent in agents}
        result = super().unlink()
        for agent in agents:
            agent._discuss_sync(left.get(agent.id))
        return result


class NudgeAgent(models.Model):
    _inherit = "nudge.agent"

    def write(self, vals):
        if not {"active", "state", "partner_id"} & set(vals):
            return super().write(vals)
        left = {agent.id: agent._discuss_bound() for agent in self}
        result = super().write(vals)
        for agent in self:
            agent._discuss_sync(left.get(agent.id))
        return result

    def _discuss_live(self):
        """:return: whether the agent answers in Discuss now: published, not archived"""
        self.ensure_one()
        agent = self.sudo()
        return bool(agent.active and agent.state == "published")

    def _discuss_bindings(self, live=True):
        bindings = self.sudo().binding_ids.filtered(lambda binding: binding.channel == DISCUSS)
        return bindings.filtered("published") if live else bindings

    def _discuss_bound(self):
        """:return: every channel any discuss binding of the agent lists, published or not"""
        self.ensure_one()
        return self._discuss_bindings(live=False).channel_ids

    def _discuss_channels(self):
        """:return: the channels the agent is in now: those of its published bindings, when it is live"""
        self.ensure_one()
        if not self._discuss_live():
            return self.env["discuss.channel"].sudo()
        return self._discuss_bindings().channel_ids

    def _discuss_sync(self, left=None):
        """Put the agent's partner into the channels it is bound to, and take it out of the others it was
        bound to (``left``: channels a binding listed before a change)"""
        self.ensure_one()
        partner = self.sudo().partner_id
        if not partner:
            return
        # Defence in depth: the agent's partner is never a person's or a company's partner, whatever the
        # field says; a membership is never added to or removed from a real person
        if self.env["res.users"].sudo().with_context(active_test=False).search_count(
                [("partner_id", "=", partner.id)]) \
                or self.env["res.company"].sudo().with_context(active_test=False).search_count(
                    [("partner_id", "=", partner.id)]):
            _logger.warning("Nudge: agent %s has a partner that is a user's or a company's: "
                            "Discuss sync skipped", self.id)
            return
        live = self._discuss_channels()
        leaving = ((left or self.env["discuss.channel"].sudo()) | self._discuss_bound()) - live
        Member = self.env["discuss.channel.member"].sudo()
        joined = Member.search([("channel_id", "in", live.ids), ("partner_id", "=", partner.id)]).channel_id
        # Created as they are, without the notice and the bus message of an invitation
        Member.create([{"channel_id": channel.id, "partner_id": partner.id} for channel in live - joined])
        Member.search([("channel_id", "in", leaving.ids), ("partner_id", "=", partner.id)]).unlink()

    def _discuss_can_chat(self):
        """:return: whether people may write to the agent in a direct chat"""
        self.ensure_one()
        return self._discuss_live() and bool(self._discuss_bindings())


class DiscussChannel(models.Model):
    _inherit = "discuss.channel"

    def _nudge_agents_for(self, message):
        """:return: the live agents this message of the channel is addressed to: those mentioned in a
        bound channel, the one in a direct chat"""
        self.ensure_one()
        Agent = self.env["nudge.agent"].sudo()
        if self.channel_type == CHAT:
            partners = self.sudo().channel_member_ids.partner_id
            agents = Agent.search([("partner_id", "in", partners.ids)])
            return agents.filtered(lambda agent: agent._discuss_can_chat())
        agents = Agent.search([("partner_id", "in", message.partner_ids.ids)])
        return agents.filtered(lambda agent: self.sudo() in agent._discuss_channels())

    def _get_allowed_message_partner_ids(self, partner_ids):
        """Odoo 19 keeps, of the partners mentioned in a channel open to a group, only those whose user is in that
        group: an agent's partner has no user, and its mention was dropped. A live agent bound to this channel
        stays mentioned (it is a member, and has no email: nothing is sent to it). Odoo 17 and 18 drop nothing
        and never call this"""
        allowed = super()._get_allowed_message_partner_ids(partner_ids)
        if not partner_ids:
            return allowed
        agents = self.env["nudge.agent"].sudo().search([("partner_id", "in", list(partner_ids))])
        bound = agents.filtered(lambda agent: self.sudo() in agent._discuss_channels()).partner_id
        return list(allowed) + [pid for pid in bound.ids if pid not in allowed]


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    def _message_post_after_hook(self, message, msg_values):
        result = super()._message_post_after_hook(message, msg_values)
        if self._name == CHANNEL and len(self) == 1 and message.message_type == "comment":
            self._nudge_agent_queue(message)
        return result

    def _nudge_agent_queue(self, message):
        author = message.author_id
        # Never answers an agent (itself or another), OdooBot, the Nudge bot, or anyone who is no person
        if not author or author == self.env.ref("base.partner_root", raise_if_not_found=False):
            return
        if author == self.env.ref("nudge_discuss.partner_nudge", raise_if_not_found=False):
            return
        if self.env["nudge.agent"].sudo().with_context(active_test=False).search_count(
                [("partner_id", "=", author.id)]):
            return
        user = self.env.user
        # The author is whatever the poster says (an author_id given in sudo, the sender of a mail): an agent
        # answers only the user who posts, as their own partner
        if not user or author != user.partner_id:
            return
        Mention = self.env["nudge.mention"].sudo()
        for agent in self._nudge_agents_for(message):
            # A person the agent is not open to is queued only to be told, once
            if Mention._agent_usable(agent, user) or Mention._agent_refusal_due(agent, self, user):
                Mention._queue(message, self, user, agent=agent)


class NudgeMention(models.Model):
    _inherit = "nudge.mention"

    agent_id = fields.Many2one("nudge.agent", string="Agent", ondelete="cascade", index=True)

    @api.model
    def _queue(self, message, thread, user, agent=None):
        if not agent:
            return super()._queue(message, thread, user)
        self.create({
            "message_id": message.id,
            "model": thread._name,
            "res_id": thread.id,
            "user_id": user.id,
            "agent_id": agent.id,
        })
        self.env.ref("nudge_discuss.cron_mentions").sudo()._trigger()

    @api.model
    def _agent_usable(self, agent, user):
        """:return: whether the user may ask the agent: a Nudge user, and in its audience"""
        if not user or not user._is_internal() or not user.has_group("nudge.group_nudge_user"):
            return False
        try:
            return bool(agent.with_user(user)._can_use(user))
        except AccessError:
            return False

    @api.model
    def _agent_refusal_due(self, agent, thread, user):
        """:return: whether the user has not been refused by this agent in this channel today"""
        since = fields.Datetime.now() - timedelta(hours=REFUSAL_HOURS)
        return not self.search_count([
            ("agent_id", "=", agent.id), ("model", "=", thread._name), ("res_id", "=", thread.id),
            ("user_id", "=", user.id), ("state", "in", ("queued", "answering", "refused")),
            ("create_date", ">=", since)])

    def _agent_conversation(self, env):
        """:return: the conversation kept for this (channel, author) with this agent, as the author reads
        it; None for the first question"""
        previous = self.search([
            ("agent_id", "=", self.agent_id.id), ("model", "=", self.model), ("res_id", "=", self.res_id),
            ("user_id", "=", self.user_id.id), ("conversation_id", "!=", False), ("id", "!=", self.id),
        ], order="id desc", limit=1).conversation_id
        return env["nudge.conversation"].search([("id", "=", previous.id)]) or None if previous else None

    def _agent_question(self):
        """:return: what the author wrote, without the mention of the agent"""
        text = text_of(self.message_id.body)
        name = self.agent_id.sudo().partner_id.name or ""
        if name:
            text = re.sub(r"(?<![\w@])@" + re.escape(name) + r"(?!\w)", "", text, flags=re.IGNORECASE)
        return cut(" ".join(text.split()), MAX_QUESTION_CHARS)

    def _agent_private(self, thread):
        """:return: whether ``thread`` is a direct chat of the author and the agent, and nobody else: the only
        place the answer is read by the author alone, and may hold their records. Every channel and group is
        read by others, guests included, who have no user to check: answered from knowledge only there."""
        if not thread or thread.channel_type != CHAT:
            return False
        members = thread.sudo().channel_member_ids
        wanted = self.agent_id.sudo().partner_id | self.message_id.author_id
        # A guest is a member without a partner: counted by the number of members
        return len(members) == len(wanted) == 2 and members.partner_id == wanted

    def _answer(self):
        if not self.agent_id:
            return super()._answer()
        self.ensure_one()
        user = self.user_id
        told = self.with_context(lang=self._lang())
        if not self._agent_usable(self.agent_id, user):
            self.state = "refused"
            self._agent_post(told.env._("This agent is not open to you."))
            return
        env = self._as_person()
        try:
            with self.env.cr.savepoint():
                thread = self.env[CHANNEL].sudo().browse(self.res_id).exists()
                conversation = self._agent_conversation(env)
                question = self._agent_question() or env._("Hello")
                # As the author: the audience, companies and tools are theirs, nothing here is sudo.
                # Anywhere but a direct chat of the author and the agent: knowledge only, no record read
                agent = env["nudge.agent"].browse(self.agent_id.id)
                if not self._agent_private(thread):
                    agent = agent.with_context(**{KNOWLEDGE_ONLY: True})
                reply = agent._answer(question, conversation=conversation)
                self.write({"conversation_id": reply.get("conversation_id") or False})
                if not reply.get("text"):
                    raise UserError(env._("The agent had no answer."))
            self.answer_id = self._agent_post(reply["text"])
            self.state = "done"
        except Exception as error:  # noqa: BLE001
            # Failed for good: the cron never takes it again. What is said in the channel is generic, an
            # exception text can name records the others there may not see
            _logger.warning("Nudge: agent mention %s failed: %s", self.id, error, exc_info=True)
            self.write({"state": "failed", "error": type(error).__name__})
            self._agent_post(told.env._("This agent could not answer."))

    def _agent_post(self, text):
        """Post where the question was, as the agent's partner: the only sudo, to speak like the bot does.
        :return: the message posted"""
        partner = self.agent_id.sudo().partner_id
        thread = self.env[CHANNEL].sudo().browse(self.res_id).exists()
        if not thread or not partner:
            return self.env["mail.message"]
        return thread.message_post(
            body=to_html(text), author_id=partner.id, message_type="comment", subtype_xmlid="mail.mt_comment")

"""Nudge Agents, the engine: one turn of a conversation with an agent (see ``nudge_agent.py`` for the agent).

An agent never adds a right: every hook below takes away from what plain Nudge does for the same person. The
agent and the rehearsal are stored on the conversation when it starts and never change; every phase of a turn
(the question, each answer of the model and the tools it calls, a yes to a card, a piece in the background)
reads them from there again, never from what a request carries (``_agent_scope``).

- who: open to the person (audience, company), published (or a rehearsal by a manager who may edit the agent),
  not archived; a service user only through a published service binding whose user holds only its group
- companies: the companies the person selected that are also the agent's (``allowed_company_ids``)
- tools: only ``nudge.agent._tools_for`` are offered and run (``tools.base.agent_allows``, and ``_chat_run_tools``);
  none that reads records when the answer is read by others (``agent_knowledge_only``, stored when the
  conversation starts, from ``nudge_agent_knowledge_only``): then only the remembered answers meant for everyone
  (no group) are served, and no rule of Nudge that reads records
- plans: what a plan does after the yes, in the background or corrected before it, runs in the scope too
- skills: only the agent's, when it names some (``nudge.skill._visible``)
- prompt: the agent's persona after Nudge's own rules, never in their place (``llm.prompt.with_agent``)
- masking of personal data: forced on when the agent says so (``RedactingAdapter``)
- provider: the agent's own when it has one that may answer (private mode, egress, budgets apply as always)
- budgets: the agent's month (real calls) and the person's day with it (rehearsals included), asked at the
  question, after a yes and before each piece in the background; past them no provider is called, with a notice
- Developer mode: refused
- rehearsal (Try it): every tool runs in a savepoint rolled back at once; a change is told, never prepared, and
  no yes is taken

60.1, the channels:
- a public channel (``_answer_public``: a website visitor, an unknown phone number) runs as the service user of a
  published service binding, never in sudo, the binding stored on the conversation; Talk only, the tools of
  ``tools.base.PUBLIC_TOOLS`` within the agent's, every model and field a call names checked against the binding's
  allow-list before any query (``tools.base.public_refusal``); no records at all when the list is empty; only the
  remembered answers meant for everyone, no rule of Nudge, no overview of the database
- guardrails before any query and any AI: the office hours of the binding, the off-limits topics of the agent
- hand to a person (``hand_to_person``): offered when the binding names an escalation user; a visitor only with
  consent
- the budget of the month passed: its managers are told once a month (``nudge.agent._budget_notice``)
- outside the Nudge panel (Discuss, the website, a phone) the model is told there are no buttons and no modes
"""
import datetime
import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .. import tools
from ..llm import base as llm
from ..llm import loop, prompt
from ..llm.redact import RedactingAdapter
from ..tools import skill_router
from ..tools.base import MODE_AGENT, MODE_DEV, MODE_TALK
from .nudge_agent import DEFAULT_READ_TOOLS, check_access
from .nudge_conversation import DECLINE, FAILED, TITLE_CHARS
from .nudge_skill import STEERING as SKILL_STEERING

_logger = logging.getLogger(__name__)

# Written by the engine when a conversation starts, and never again
AGENT_FIELDS = frozenset({"agent_id", "agent_rehearsal", "agent_knowledge_only", "agent_binding_id",
                          "agent_visitor_key"})
# Put in the context by _chat_open alone, around the conversation it makes: only that one takes the agent a
# request names; a conversation made by anything else in the same request (a tool, a job) is plain Nudge. An
# object, not a value a request could carry
OPENING = object()
MANAGER = "nudge.group_nudge_manager"
SERVICE = "nudge.group_agent_service"
# The lines of the ledger that are answers, and those given without AI (as on the Token Dashboard)
ANSWER_KINDS = ("chat", "memory", "cache", "noai")
NO_AI_KINDS = ("memory", "cache", "noai")
# Read the files of the company: only for an agent that is told to use them
DOC_TOOLS = frozenset({"search_company_files", "search_company_sources"})
# What an answer read by other people may use (a group channel): the documentation of Odoo and the skills
KNOWLEDGE_TOOLS = frozenset({"search_odoo_documentation", "use_skill"})
# The context key a caller sets when the answer is read by others than the person (Discuss, group channels)
KNOWLEDGE_ONLY = "nudge_agent_knowledge_only"

# 60.1. Put in the context by _answer around the conversation it opens: the channel the turn comes through. Only
# an object of the class below is read (a request cannot carry one)
CHANNEL = "nudge_agent_channel"
# Set by _answer for a turn answered outside the Nudge panel (Discuss, website, phone): the prompt says there are no
# buttons and no modes there. It only adds words to the prompt
OUTSIDE = "nudge_agent_outside"
# Set around the system prompt of such a turn: the rules that offer to switch modes are left out
NO_SWITCH = "nudge_agent_no_switch"
# Set by _answer_public: the visitor agreed to be contacted (hand_to_person); read as True only
CONSENT = "nudge_agent_consent"
# Set by _agent_scope from the binding stored on the conversation: the binding a hand-over goes through
ESCALATION = "nudge_agent_escalation"
# Set by _agent_scope for a public turn: its conversation, one answer at a time per visitor (nudge.slot)
PUBLIC_CONVERSATION = "nudge_agent_public_conversation"
# How long a visitor's conversation goes on before a new one starts, and how long its key may be
PUBLIC_KEEP_HOURS = 24
VISITOR_KEY_CHARS = 128
USER = "nudge.group_nudge_user"

# What the model is told about the channel it answers on (after the persona)
AGENT_STATE_FIRST = (
    "Before you advise an action on a record (confirm, validate, send, pay, cancel), check its state: never advise "
    "what is already done, and say its state when it matters.")
AGENT_OUTSIDE_PANEL = (
    "You answer outside the Nudge panel of Odoo: here there are no buttons, no modes to switch and no screen to "
    "show. Never tell anyone to press a button or to switch to Agent or Developer mode, and never offer to do so. "
    "No change of the data is made from here.")
AGENT_OUTSIDE_PERSON = (
    "When the person needs a change done, say what would change and that they can ask Nudge in Odoo to do it.")
AGENT_PUBLIC = (
    "You answer a visitor who has no account in this database. You change nothing, and you never say how this "
    "database is set up, who works here or what you cannot read. Never name a record, a price or a figure you did "
    "not read with a tool in this conversation.")
AGENT_PUBLIC_MODELS = "You may read records of these kinds only: {models}. Use no other."
AGENT_PUBLIC_NO_RECORDS = "You read no record of the database here: answer from your knowledge and skills only."
# What the rules of Nudge say of a screen shared from the panel, and what is said outside it
SCREEN_HINT = "ask them to turn on the eye button above the message box, or to press their Nudge shortcut."
NO_SCREEN_HINT = "ask them to describe it: no screen is shared here."
AGENT_HAND_OVER = (
    "When they ask for a person, or you cannot help and they agree to be contacted, use hand_to_person with a short "
    "summary of what they need and how to reach them.")


class Channel:
    """Nudge Agents 60.1: the binding a turn comes through and the visitor it answers, carried by ``_answer`` to
    the conversation it opens (``nudge.conversation.create``), which stores them for every phase of the turn"""
    __slots__ = ("binding_id", "visitor_key")

    def __init__(self, binding_id, visitor_key=""):
        self.binding_id = binding_id
        self.visitor_key = visitor_key or ""


# The words of a message that asks for a person of the company, as folded by the skill router (M2)
PERSON_WORDS = frozenset({
    "person", "human", "someone", "somebody", "anyone", "staff", "employee", "representative", "colleague",
    "manager", "salesperson", "support", "call", "contact", "talk", "speak", "callback",
    "شخص", "انسان", "احد", "موظف", "مندوب", "مسوول", "مسئول", "مدير", "اتصل", "اتصال", "كلم", "كلمني", "تواصل",
    "تكلم", "اكلم", "خدمه", "دعم",
})


def asks_for_person(text):
    """:return: whether ``text``, a message of the person, asks for a person of the company to take over"""
    words = set(skill_router.words_of(text or "")) | set(skill_router.tokens_of(text or ""))
    return bool(words & PERSON_WORDS)


def public_failure(env):
    """:return: what a visitor of a public channel is told when no answer could be given: nothing of why"""
    return _("I cannot answer this here. Please ask in other words, or ask to talk to a person.")


def visitor_key_of(value):
    """:return: the key of a visitor as it is stored: a text of at most 128 characters, empty for none"""
    return " ".join(str(value or "").split())[:VISITOR_KEY_CHARS]


class AgentOverBudget(UserError):
    """The agent's budget is used up: no provider is called (``nudge.conversation._chat_choose``)"""


class NudgeAgent(models.Model):
    _inherit = "nudge.agent"

    # Statistics, read from the ledger of usage; the rehearsals of Try it are left out
    answers_count = fields.Integer(
        string="Answers", compute="_compute_usage_stats", groups=MANAGER,
        help="Answers given by this agent: by AI, from memory or understood without AI. Rehearsals are not counted.")
    no_ai_share = fields.Float(
        string="Without AI (%)", compute="_compute_usage_stats", groups=MANAGER, digits=(5, 1),
        help="The share of its answers given without any AI.")
    cost_month = fields.Float(
        string="Cost This Month", compute="_compute_usage_stats", groups=MANAGER, digits=(12, 6),
        help="What its calls to AI providers cost this month. Rehearsals are not counted.")

    def _compute_usage_stats(self):
        # sudo: aggregates of the ledger, for the managers who see these fields
        Usage = self.env["nudge.usage"].sudo()
        ids = [agent._origin.id for agent in self if agent._origin.id]
        live = [("agent_id", "in", ids), ("rehearsal", "=", False)]
        counts = {}
        for agent, kind, count in Usage._read_group(
                [*live, ("kind", "in", ANSWER_KINDS)], ["agent_id", "kind"], ["__count"]):
            counts.setdefault(agent.id, {})[kind] = count
        first = fields.Date.context_today(self).replace(day=1)
        costs = {agent.id: cost for agent, cost in Usage._read_group(
            [*live, ("date", ">=", first)], ["agent_id"], ["cost:sum"])}
        for agent in self:
            kinds = counts.get(agent._origin.id, {})
            answers = sum(kinds.values())
            agent.answers_count = answers
            agent.no_ai_share = 100.0 * sum(kinds.get(kind, 0) for kind in NO_AI_KINDS) / answers if answers else 0.0
            agent.cost_month = costs.get(agent._origin.id) or 0.0

    # ------------------------------------------------------------------ who
    def _may_rehearse(self, user):
        """:return: whether ``user`` may try this agent in a rehearsal: a manager of Nudge who may edit it (its
        access rights and record rules, and every company of the agent is theirs)"""
        self.ensure_one()
        if not user.has_group(MANAGER):
            return False
        # sudo: the agent's own companies, some of which the user may not see
        if not self.sudo().company_ids <= user.sudo().company_ids:
            return False
        try:
            check_access(self.with_user(user), "write")
        except AccessError:
            return False
        return True

    def _service_ok(self, user):
        """:return: whether ``user`` may be answered as far as service users go: not a service user, or one
        named by a published service binding of this agent and holding still only the service group"""
        self.ensure_one()
        if not user.has_group(SERVICE):
            return True
        # sudo: the agent's own configuration; the groups of the service user are read again at every turn
        return any(
            binding.acts_as == "service" and binding.published and binding.service_user_id == user
            and binding._service_user_ok()
            for binding in self.sudo().binding_ids)

    def _open_to(self, user, rehearsal=False):
        """:param rehearsal: the turn is a rehearsal (Try it), allowed to a draft or tested agent
        :return: whether ``user`` may talk to this agent now: active and published, in its audience and
            companies (``_can_use``), and not a service user it was not given to. A rehearsal by a manager who may
            edit the agent needs only a company of theirs among the agent's, whatever its state: it writes nothing
            and reads with their own rights"""
        self.ensure_one()
        # sudo: the agent's own configuration
        agent = self.sudo()
        if rehearsal and agent._may_rehearse(user):
            return bool(agent._companies_for(user)) and agent._service_ok(user)
        if not (agent.active and agent.state == "published") or not agent._can_use(user):
            return False
        return agent._service_ok(user)

    def _over_budget(self, user, rehearsal=False, conversation=None):
        """:param conversation: the conversation of the turn; on a public channel the day is counted per visitor
            (its key), never per service user, whom every visitor shares
        :return: "month" when the agent spent its budget of the month (everybody's real calls), "day" when
        ``user`` spent the tokens they may spend with it today (rehearsals included), else False. A rehearsal
        is not billed to the month: only the day of the person is checked for it."""
        self.ensure_one()
        agent = self.sudo()
        public = conversation._agent_public() if conversation else self.env["nudge.agent.binding"]
        if public and (agent.budget_month <= 0 or agent.budget_user_day <= 0):
            # Nudge Agents 60.1: a public channel has no budget without limits (_answer_public refuses it first)
            return "month"
        if not (agent.budget_month or agent.budget_user_day):
            return False
        Usage = self.env["nudge.usage"]
        today = fields.Date.context_today(self.with_user(user))
        mine = [("agent_id", "=", agent.id)]
        if agent.budget_month and not rehearsal:
            _tokens, cost = Usage._spent([*mine, ("rehearsal", "=", False), ("date", ">=", today.replace(day=1))])
            if cost >= agent.budget_month:
                # Nudge Agents 60.1: its managers are told, once a month
                agent._budget_notice()
                return "month"
        if agent.budget_user_day:
            who = [("user_id", "=", user.id)]
            key = conversation.sudo().agent_visitor_key if public else ""
            if key:
                # One visitor: the conversations of this channel with their key, whichever service user answered
                who = [("conversation_id.agent_binding_id", "=", public.id),
                       ("conversation_id.agent_visitor_key", "=", key)]
            tokens, _cost = Usage._spent([*mine, *who, ("date", "=", today)])
            if tokens >= agent.budget_user_day:
                return "day"
        return False

    def _persona_block(self, knowledge_only=False, outside=False, binding=None, hand_over=False):
        """:param outside: the answer is read outside the Nudge panel (Discuss, the website, a phone)
        :param binding: the public channel (a service binding) the answer is given on, or None
        :param hand_over: hand_to_person is offered
        :return: the block of the system prompt that makes Nudge this agent (``llm.prompt.render_agent``), with
            what the channel it answers on allows (60.1)"""
        self.ensure_one()
        agent = self.sudo()
        block = prompt.render_agent(
            agent.name or "", agent.role or "", agent.instructions or "", agent.greeting or "",
            agent.lang_ids.mapped("name"), knowledge_only=knowledge_only)
        lines = [AGENT_STATE_FIRST]
        if outside or binding:
            lines.append(AGENT_OUTSIDE_PANEL)
        if binding:
            models = binding._allowlist()
            lines += [AGENT_PUBLIC, AGENT_PUBLIC_MODELS.format(models=", ".join(models)) if models
                      else AGENT_PUBLIC_NO_RECORDS]
        elif outside:
            lines.append(AGENT_OUTSIDE_PERSON)
        if hand_over:
            lines.append(AGENT_HAND_OVER)
        return block + "\n" + "\n".join(lines) + "\n"

    def _public_tools(self, binding):
        """:return: the sorted tools of a turn on the public channel ``binding``: those the agent has (its list, or
        the reading ones) among ``tools.base.PUBLIC_TOOLS``, only knowledge when the channel lists no model, and
        hand_to_person when it names a person to hand over to. Run as the service user within the channel's
        models (``tools.base.public_refusal``)"""
        self.ensure_one()
        agent = self.sudo()
        named = set(tools.skills.split_names(agent.tool_keys)) or (DEFAULT_READ_TOOLS | {"use_skill"})
        # A set of its own: the default reading tools are a frozenset
        names = set(named & (tools.base.PUBLIC_TOOLS - {tools.base.HAND_OVER_TOOL}))
        if not binding._allowlist():
            names &= tools.base.PUBLIC_KNOWLEDGE_TOOLS
        if binding.sudo().escalation_user_id:
            names.add(tools.base.HAND_OVER_TOOL)
        here = tools.registry.tools_here(self.env)
        return sorted(name for name in names if name in here)

    # ------------------------------------------------------------------ one turn, waited for
    def _answer_mode(self, user, rehearsal):
        """:return: the mode ``_answer`` asks in: Talk, or Agent for a rehearsal of an agent that can prepare a
        change for this person (the answer then says what would be done)"""
        if not rehearsal or not self.env["nudge.conversation"]._can_use_agent():
            return MODE_TALK
        names = self._tools_for(user, MODE_AGENT)
        if any(isinstance(tools.base.TOOLS.get(name), tools.base.Action) for name in names):
            return MODE_AGENT
        return MODE_TALK

    def _answer(self, text, lang=None, rehearsal=False, conversation=None, mode=None, binding=None,
                visitor_key=None):
        """One full turn of this agent, as ``self.env.user``, the way the panel asks it: the same checks, the
        same answers without AI first, the same tools, waited for until the answer is written. With the
        context key ``nudge_agent_knowledge_only`` (an answer posted where others read it) no record is read.
        Outside a rehearsal the answer is read outside the Nudge panel: the model is told there are no buttons and
        no modes (60.1).

        :param lang: code of the language the question is asked in (res.lang), or None
        :param rehearsal: Try it, for a manager who may edit the agent (ignored for anybody else): tools that
            change data are told, never run, nothing is written, and the usage is marked as a rehearsal
        :param conversation: the ``nudge.conversation`` to go on with (of this agent, of this person), or
            None to start one
        :param mode: "talk" or "agent"; None chooses (Agent only for a rehearsal that can prepare a change)
        :param binding: 60.1, the published ``nudge.agent.binding`` of this agent the question came through, or
            None: its office hours apply, and its escalation user is offered (hand_to_person). A service binding
            is for its service user only, in Talk, never a rehearsal (``_answer_public`` calls it so)
        :param visitor_key: 60.1, with a binding: who the conversation is with, kept on it
        :return: dict ``text`` (the answer, or what went wrong), ``source`` ("ai", "memory", "noai", "budget",
            "hours", "off_limits" or "refused"), ``tool_calls`` (names), ``conversation_id``, ``error`` (no answer
            was given)"""
        self.ensure_one()
        user = self.env.user
        result = {"text": "", "source": "refused", "tool_calls": [], "error": True,
                  "conversation_id": conversation.id if conversation else False}
        refused = dict(result, text=_("This agent is not open to you."))
        if self.env.su:
            # A turn is always somebody's: in sudo nothing would be checked (the channels call _answer_public)
            return refused
        # sudo: the configuration of the channel
        binding = binding.sudo().exists() if binding else self.env["nudge.agent.binding"].sudo()
        key = visitor_key_of(visitor_key) if binding else ""
        if len(binding) > 1 or (binding and (binding.agent_id.id != self.id or not binding.published)):
            return refused
        public = binding.acts_as == "service"
        if public:
            # Nudge Agents 60.1, a public channel: its service user, asking questions, for real
            rehearsal, mode = False, MODE_TALK
        rehearsal = bool(rehearsal) and self._may_rehearse(user)
        member = binding._public_open_to(user) if public else (
            user.has_group(USER) and not user.has_group(SERVICE))
        if not member or not self._open_to(user, rehearsal=rehearsal):
            return refused
        # Nudge Agents 60.1, the guardrails: before any query and any AI
        if binding and not binding._open_now():
            return dict(result, text=binding._hours_text(), source="hours", error=False)
        if self._off_limits_hit(text):
            return dict(result, text=self._off_limits_text(), source="off_limits", error=False)
        if conversation and conversation.sudo().agent_id.id != self.id:
            return dict(result, text=_("This conversation is not one of this agent."))
        if conversation and conversation.sudo().agent_knowledge_only != bool(self.env.context.get(KNOWLEDGE_ONLY)):
            # Answers read by others and answers for the person alone are never in one conversation: its history
            # is sent with every question
            conversation = None
        if conversation and (conversation.sudo().agent_binding_id != binding
                             or (conversation.sudo().agent_visitor_key or "") != key):
            # A conversation goes on only on its channel, with its visitor
            conversation = None
        Lang = self.env["res.lang"].sudo().with_context(active_test=False)
        language = Lang.search([("code", "=", lang)], limit=1) if lang else Lang
        context = {"nudge_agent_id": self.id, "nudge_rehearsal": rehearsal, OUTSIDE: not rehearsal}
        if language.active:
            context["lang"] = language.code
        Conversation = self.env["nudge.conversation"].with_context(**context)
        mode = mode if mode in (MODE_TALK, MODE_AGENT) else self._answer_mode(user, rehearsal)
        try:
            # A question refused before it is answered leaves nothing behind
            with self.env.cr.savepoint():
                # The channel goes with the conversation that is opened, and no further: it is stored there
                chat = Conversation.with_context(**{CHANNEL: Channel(binding.id, key) if binding else None})._chat_open(
                    conversation.id if conversation else None)
                chat = chat.with_context(**{CHANNEL: None})
                job = chat._chat_prepare(text, mode=mode, answer_in=language.name or None)
        except (UserError, AccessError) as error:
            if public:
                # A visitor is never shown what went wrong inside: it may name a model or a record
                _logger.info("Nudge Agents: a public turn on channel %s was refused: %s", binding.id, error)
                return dict(result, text=public_failure(self.env))
            return dict(result, text=str(error.args[0]) if error.args else str(error))
        try:
            events = list(loop.run(job, chat._chat_record))
        finally:
            if job.get("slot_id"):
                # Its place goes to the next in line, as the controller does when a stream ends
                self.env["nudge.slot"]._finish(job["slot_id"])
        turn = chat._agent_turn_result(job, events)
        if public and turn.get("error"):
            _logger.info("Nudge Agents: a public turn on channel %s had no answer: %s", binding.id, turn.get("text"))
            turn = dict(turn, text=public_failure(self.env))
        return dict(result, conversation_id=chat.id, **turn)

    # ------------------------------------------------------------------ a public channel (60.1)
    def _answer_public(self, text, binding, lang=None, conversation=None, visitor_key=None, consent=False,
                       company=None):
        """Nudge Agents 60.1: one turn for somebody without an account (a website visitor, an unknown phone number)
        on the published service ``binding``. Called by the channels (nudge_website, nudge_messaging) on the agent,
        on the model, in sudo or not: the turn never runs in sudo. It runs as the binding's service user, who holds
        only the service group, in Talk, with only the agent's reading tools among ``tools.base.PUBLIC_TOOLS``;
        every model and field a call names must be in the binding's allow-list, refused before any query
        (``tools.base.public_refusal``); with an empty list no record is read at all. Only the remembered answers
        meant for everyone are served. Outside the office hours of the binding, or on an off-limits topic of the
        agent, a fixed sentence answers, without AI.

        :param text: the question
        :param binding: the ``nudge.agent.binding`` (or its id) of the channel: published, ``acts_as`` service,
            of this agent when ``self`` is one
        :param lang: code of the language of the question (res.lang), or None
        :param conversation: the ``nudge.conversation`` (or its id) to go on with; ignored unless it is of this
            binding and this visitor. None: the visitor's last one of the day, if any, else a new one
        :param visitor_key: who the visitor is, for their conversation (a hash of the session, ``<channel>:<hash of
            the number>``); None: a new conversation at every question
        :param consent: the visitor agreed to be contacted by a person (hand_to_person); False by default
        :param company: the company of the channel (the website's): the turn works in it alone, and is refused
            when the agent and its service user do not both work there
        :return: dict as :meth:`_answer`: ``text``, ``source`` ("ai", "memory", "noai", "budget", "hours",
            "off_limits", "refused"), ``tool_calls``, ``conversation_id``, ``error``"""
        refused = {"text": _("This channel is not open."), "source": "refused", "tool_calls": [],
                   "error": True, "conversation_id": False}
        # sudo: the configuration of the channel and its agent, never the data
        Binding = self.env["nudge.agent.binding"].sudo()
        binding = Binding.browse(binding) if isinstance(binding, int) else (binding or Binding)
        binding = binding.sudo().exists()
        if len(binding) != 1:
            return refused
        agent = binding.agent_id
        if self and self.ids != agent.ids:
            return refused
        service = binding.service_user_id
        if not (binding._public_open_to(service) and agent.active and agent.state == "published"):
            return refused
        if agent.budget_month <= 0 or agent.budget_user_day <= 0:
            # Nobody without an account is answered by an agent with no limit to what it spends (a system
            # administrator cannot publish such a channel; one published before is refused here)
            _logger.info("Nudge Agents: public channel %s refused: its agent has no budget", binding.id)
            return refused
        companies = agent._companies_for(service)
        if company:
            companies &= company
        if not companies:
            return refused
        current = service.company_id if service.company_id in companies else companies[0]
        # As the service user, never in sudo, with nothing of the caller's context: whatever is read is read with
        # its rights, in the agent's companies it works in
        env = self.env(user=service.id, su=False, context={
            "lang": service.lang or "en_US", "tz": service.tz or "UTC",
            "allowed_company_ids": [current.id, *(companies - current).ids], CONSENT: bool(consent)})
        key = visitor_key_of(visitor_key)
        # sudo: the bookkeeping of the conversations of the channel, to find the visitor's
        Conversation = self.env["nudge.conversation"].sudo()
        found = Conversation
        if conversation:
            found = Conversation.browse(conversation if isinstance(conversation, int) else conversation.id).exists()
        elif key:
            since = fields.Datetime.now() - datetime.timedelta(hours=PUBLIC_KEEP_HOURS)
            found = Conversation.search([
                ("agent_binding_id", "=", binding.id), ("agent_visitor_key", "=", key), ("user_id", "=", service.id),
                ("agent_id", "=", agent.id), ("write_date", ">=", since)], order="id desc", limit=1)
        if found and not (key and found.agent_binding_id == binding and found.agent_visitor_key == key
                          and found.user_id == service):
            found = Conversation
        return agent.with_env(env)._answer(
            text, lang=lang, conversation=env["nudge.conversation"].browse(found.id) if found else None,
            mode=MODE_TALK, binding=binding, visitor_key=key)


class NudgeConversation(models.Model):
    _inherit = "nudge.conversation"

    agent_id = fields.Many2one(
        "nudge.agent", string="Agent", readonly=True, copy=False, index="btree_not_null", ondelete="restrict",
        help="The agent this conversation is with: set when it starts, never changed.")
    agent_rehearsal = fields.Boolean(
        string="Rehearsal", readonly=True, copy=False,
        help="Try it: tools that change data were told, never run, and the usage is not billed as real.")
    agent_knowledge_only = fields.Boolean(
        string="Read by Others", readonly=True, copy=False,
        help="The answers are posted where other people read them (a Discuss channel): no record is read, and "
             "only the remembered answers meant for everyone are given.")
    # Nudge Agents 60.1: the channel the conversation came through (_answer with a binding), and the visitor
    agent_binding_id = fields.Many2one(
        "nudge.agent.binding", string="Channel", readonly=True, copy=False, index="btree_not_null",
        ondelete="set null", help="The channel of the agent this conversation came through: set when it starts.")
    agent_visitor_key = fields.Char(
        string="Visitor", readonly=True, copy=False, index="btree_not_null",
        help="Who a conversation of a public channel is with: a key of their session or number, never a name.")
    agent_handed_at = fields.Datetime(
        string="Handed to a Person", readonly=True, copy=False,
        help="When the agent handed this conversation to a person: a visitor of a public channel once a day.")

    # ------------------------------------------------------------------ the agent, set once
    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            # The engine sets the agent below, after its checks; nobody chooses it, a manager included
            vals_list = [{key: value for key, value in vals.items() if key not in AGENT_FIELDS} for vals in vals_list]
        conversations = super().create(vals_list)
        # The panel (Try it) and _answer name the agent, and only the conversation _chat_open makes takes it
        wanted = self.env.context.get("nudge_agent_id") if self.env.context.get("nudge_agent_opening") is OPENING \
            else None
        if wanted:
            agent = self._agent_named(wanted)
            knowledge_only = bool(self.env.context.get(KNOWLEDGE_ONLY))
            # Nudge Agents 60.1: the channel _answer names (an object only it puts there), else none
            channel = self.env.context.get(CHANNEL)
            channel = channel if isinstance(channel, Channel) else None
            # sudo: the configuration of the channel
            binding = self.env["nudge.agent.binding"].sudo().browse(channel.binding_id if channel else []).exists()
            for conversation in conversations.sudo().filtered(lambda conversation: not conversation.agent_id):
                person = conversation.user_id
                public = binding.acts_as == "service"
                # A rehearsal is for a manager who may edit the agent; asked by anybody else it is ignored
                rehearsal = not public and bool(self.env.context.get("nudge_rehearsal")) and bool(
                    agent) and agent._may_rehearse(person)
                if not agent or not agent._open_to(person, rehearsal=rehearsal):
                    raise UserError(_("This agent is not open to you."))
                # A service user only through its published service channel; a channel only of this agent
                if (binding and (binding.agent_id != agent or not binding.published)) or (
                        public != person.has_group(SERVICE)) or (public and not binding._public_open_to(person)):
                    raise UserError(_("This agent is not open to you."))
                conversation.write({
                    "agent_id": agent.id, "agent_rehearsal": rehearsal, "agent_knowledge_only": knowledge_only,
                    "agent_binding_id": binding.id or False,
                    "agent_visitor_key": (channel.visitor_key or False) if binding else False})
        return conversations

    @api.model
    def _agent_named(self, wanted):
        """:return: the agent a context names (in sudo), or an empty recordset"""
        try:
            return self.env["nudge.agent"].sudo().browse(int(wanted)).exists()
        except (TypeError, ValueError):
            return self.env["nudge.agent"].sudo()

    def write(self, vals):
        if AGENT_FIELDS & vals.keys():
            # The agent of a conversation is what every turn is checked against: set once, by the engine
            allowed = self.env.su and all(
                not conversation.agent_id or (
                    vals.get("agent_id", conversation.agent_id.id) == conversation.agent_id.id
                    and bool(vals.get("agent_rehearsal", conversation.agent_rehearsal)) == conversation.agent_rehearsal
                    and bool(vals.get("agent_knowledge_only", conversation.agent_knowledge_only))
                    == conversation.agent_knowledge_only
                    # 60.1: the channel and the visitor too
                    and (vals.get("agent_binding_id", conversation.agent_binding_id.id) or False)
                    == (conversation.agent_binding_id.id or False)
                    and (vals.get("agent_visitor_key", conversation.agent_visitor_key) or False)
                    == (conversation.agent_visitor_key or False))
                for conversation in self)
            if not allowed:
                raise AccessError(_("The agent of a conversation is set when it starts and never changes."))
        return super().write(vals)

    # ------------------------------------------------------------------ the scope of a turn
    def _agent_rehearsing(self):
        """:return: whether this conversation is a rehearsal: what was stored when it started, nothing else"""
        return bool(self.sudo().agent_rehearsal)

    def _agent_knowledge_only(self):
        """:return: whether the answers of this conversation with an agent are read by others than the person: what
        was stored when it started (a context that says so only narrows it further)"""
        record = self.sudo()
        return bool(record.agent_id) and bool(record.agent_knowledge_only or self.env.context.get(KNOWLEDGE_ONLY))

    def _agent_public(self):
        """:return: the public channel (a service binding, in sudo) this conversation came through, as stored when
        it started; empty for any other conversation"""
        binding = self.sudo().agent_binding_id
        return binding if binding.acts_as == "service" else binding.browse()

    def _agent_restricted(self):
        """:return: whether no rule of Nudge that reads records, and no remembered answer kept for some groups,
        may answer here: answers read by others, or a public channel (60.1)"""
        return self._agent_knowledge_only() or bool(self._agent_public())

    def _agent_scope(self, mode=None):
        """Every hook of a turn with an agent reads what this puts in the context, computed again from the
        agent and the rehearsal stored on the conversation, never taken from what a request carries.

        :param mode: the mode of the turn; None reads it from the conversation
        :return: this conversation in the environment of the turn: the person, in the companies they selected
            that are also the agent's, with the agent's tools, skills, masking and rehearsal; itself when the
            conversation has no agent
        :raise UserError: when the agent is not open to the person, works in none of their companies, or
            Developer mode is asked"""
        self.ensure_one()
        record = self.sudo()
        agent = record.agent_id
        if not agent:
            return self
        user = self.env.user
        rehearsing = self._agent_rehearsing()
        # Nudge Agents 60.1: a public channel is answered as its service user only, through its binding still
        # published and fit; a service user is never answered otherwise
        binding = record.agent_binding_id
        public = self._agent_public()
        if bool(public) != user.has_group(SERVICE) or (public and not public._public_open_to(user)) or (
                binding and not binding.published):
            raise UserError(_("This agent is not open to you."))
        if not agent._open_to(user, rehearsal=rehearsing):
            raise UserError(_("This agent is not open to you."))
        mode = mode or self._chat_mode()
        if mode == MODE_DEV:
            raise UserError(_("Developer mode is not open in a conversation with an agent."))
        if public and mode != MODE_TALK:
            raise UserError(_("Only questions are answered on this channel."))
        companies = agent._companies_for(user) & self.env.companies
        if not companies:
            raise UserError(_(
                "This agent works in other companies than the ones you selected. Select one of its companies."))
        current = self.env.company if self.env.company in companies else companies[0]
        allowed = [current.id, *(companies - current).ids]
        knowledge_only = self._agent_knowledge_only()
        if public:
            # The tools of the channel: reading within its models, knowledge, a hand-over (tools.base.public_*)
            names = agent.with_context(allowed_company_ids=allowed)._public_tools(public)
        else:
            # As the person, in those companies, with nothing restricted yet: what the agent may offer them
            names = agent.with_context(
                allowed_company_ids=allowed, nudge_agent_tools=None, nudge_skills={"index": True},
            )._tools_for(user, mode)
            if not agent.use_docs:
                names = [name for name in names if name not in DOC_TOOLS]
            if knowledge_only:
                # Read by others than the person: no record of the database, only Odoo's documentation and skills
                names = [name for name in names if name in KNOWLEDGE_TOOLS]
            elif binding.escalation_user_id:
                # 60.1: the channel names a person to hand over to
                names = [*names, tools.base.HAND_OVER_TOOL]
        return self.with_context(
            allowed_company_ids=allowed,
            nudge_agent_id=agent.id,
            nudge_agent_tools=tuple(names),
            # Set from the conversation either way, as the rehearsal below
            **{KNOWLEDGE_ONLY: knowledge_only,
               # 60.1, set from the conversation either way: the scope of a public channel (tools.base), the
               # channel of a hand-over, one answer at a time per visitor (nudge.slot)
               tools.base.PUBLIC_SCOPE: tuple(names) if public else None,
               tools.base.PUBLIC_MODELS: public._allowlist() if public else None,
               ESCALATION: binding.id if tools.base.HAND_OVER_TOOL in names else False,
               PUBLIC_CONVERSATION: self.id if public else False},
            nudge_agent_skill_ids=tuple(agent.skill_ids.ids) or None,
            nudge_agent_mask=bool(agent.mask_personal_data or self.env.context.get("nudge_agent_mask")),
            # Set from the conversation either way: a request that says otherwise changes nothing
            nudge_rehearsal=rehearsing,
        )

    def _agent_offered(self, definitions):
        """:return: the definitions of tools the scope of the turn allows: a companion module that adds its own
        to the list (nudge_connect) is filtered too"""
        return [definition for definition in definitions or []
                if tools.base.agent_allows(self.env, definition.get("name"))]

    def _agent_provider(self, ensure=False):
        """:return: the agent's own provider, in sudo, when it is set and may answer here (active, set up,
        allowed by private mode); empty otherwise, and the provider of the company answers"""
        Provider = self.env["nudge.provider"].sudo()
        provider = self.sudo().agent_id.provider_id.sudo()
        if not provider or not provider.active or not provider.is_configured:
            return Provider
        if Provider._is_private_mode() and not provider.runs_locally:
            return Provider
        if ensure:
            provider._ensure_capabilities()
        return provider

    # ------------------------------------------------------------------ the hooks of a turn
    @api.model
    def _chat_open(self, conversation_id=None):
        if not conversation_id:
            # Nudge Agents: the one conversation that takes the agent the request names (create); returned in the
            # environment it was asked in, so that nothing made after it takes the agent
            made = super(NudgeConversation, self.with_context(nudge_agent_opening=OPENING))._chat_open(conversation_id)
            return self.browse(made.id)
        conversation = super()._chat_open(conversation_id)
        # Nudge Agents: a conversation goes on only with the agent it began with (History reopens old ones),
        # as what it began as (a rehearsal or not), and for someone the agent is still open to
        record = conversation.sudo()
        wanted = self.env.context.get("nudge_agent_id")
        if wanted and self._agent_named(wanted) != record.agent_id:
            raise UserError(_("This conversation is not one of this agent. Start a new one."))
        if (record.agent_id and self.env.context.get("nudge_rehearsal") and not record.agent_rehearsal
                and record.agent_id._may_rehearse(self.env.user)):
            raise UserError(_("This conversation is not a rehearsal. Start a new one to try the agent."))
        if record.agent_id and record.agent_knowledge_only != bool(self.env.context.get(KNOWLEDGE_ONLY)):
            # Answers read by others and answers for the person alone are never in one conversation
            raise UserError(_("This conversation cannot go on here. Start a new one."))
        if record.agent_id and not record.agent_id._open_to(self.env.user, rehearsal=record.agent_rehearsal):
            raise UserError(_("This agent is not open to you."))
        return conversation

    def _chat_prepare(self, text, snapshot=None, spoken=False, read_aloud=False, mode=MODE_TALK,
                      background=False, explain=None, attachment=None, report=None, focus=None,
                      extra=None, fresh=False, answer_in=None, inner=False, slot_id=None):
        given = dict(snapshot=snapshot, spoken=spoken, read_aloud=read_aloud, mode=mode, background=background,
                     explain=explain, attachment=attachment, report=report, focus=focus, extra=extra, fresh=fresh,
                     answer_in=answer_in, inner=inner, slot_id=slot_id)
        agent = self.sudo().agent_id
        if not agent:
            return super()._chat_prepare(text, **given)
        # Nudge Agents: never Developer mode, whatever the person may do without the agent
        if mode == MODE_DEV:
            raise UserError(_("Developer mode is not open in a conversation with an agent."))
        rehearsing = self._agent_rehearsing()
        if rehearsing:
            # A rehearsal is watched as it goes: nothing is left to the background
            given["background"] = False
        scoped = self._agent_scope(MODE_AGENT if mode == MODE_AGENT else MODE_TALK)
        if agent._off_limits_hit(text):
            # Nudge Agents 60.1: an off-limits topic is declined in a fixed sentence, before any query and any AI
            return scoped._agent_fixed_answer(
                (text or "").strip(), MODE_AGENT if mode == MODE_AGENT else MODE_TALK, agent._off_limits_text(),
                "off_limits")
        over = agent._over_budget(self.env.user, rehearsal=rehearsing, conversation=self)
        if over:
            # Remembered answers and the rules still answer; the provider is not called (_chat_choose)
            scoped = scoped.with_context(nudge_agent_no_ai=True)
        try:
            job = super(NudgeConversation, scoped)._chat_prepare(text, **given)
        except AgentOverBudget:
            return scoped._agent_budget_answer(
                (text or "").strip(), MODE_AGENT if mode == MODE_AGENT else MODE_TALK, over)
        return scoped._agent_budget_notice(job, over) if over else job

    def _chat_choose(self, mode=None):
        # Nudge Agents: past the agent's budget no provider is called (_chat_prepare answers without AI)
        if self.env.context.get("nudge_agent_no_ai") and self.sudo().agent_id:
            raise AgentOverBudget(_("This agent has used its budget."))
        return super()._chat_choose(mode)

    def _chat_provider(self, mode=None):
        # Nudge Agents: the agent's own provider answers when it has one that may (egress and budgets after)
        return self._agent_provider(ensure=True) or super()._chat_provider(mode)

    def _chat_where_the_work_is(self):
        # Nudge Agents: an answer read by others, or by a visitor of a public channel, says nothing of where this
        # person's data is
        if self._agent_restricted():
            return ""
        return super()._chat_where_the_work_is()

    def _chat_overview(self):
        # Nudge Agents 60.1: a visitor of a public channel is told nothing of how the database is set up (its apps,
        # companies, the notes of its administrators)
        if self._agent_public():
            return ""
        return super()._chat_overview()

    def _chat_context_values(self, *args, **kwargs):
        values = super()._chat_context_values(*args, **kwargs)
        # Nudge Agents 60.1: on a public channel the one asking is a visitor, not the service user
        if self._agent_public():
            values["user_name"] = "A visitor without an account"
        return values

    @api.model
    def _can_use_agent(self):
        # Nudge Agents 60.1: the system prompt of an answer read outside the Nudge panel offers no switch of mode
        # (_chat_job): there is no button to press there
        if self.env.context.get(NO_SWITCH):
            return False
        return super()._can_use_agent()

    def _chat_job(self, provider, mode, rounds):
        agent = self.sudo().agent_id
        if not agent:
            return super()._chat_job(provider, mode, rounds)
        scoped = self._agent_scope(mode)
        public = self._agent_public()
        knowledge_only = self._agent_knowledge_only()
        outside = bool(self.env.context.get(OUTSIDE)) or knowledge_only or bool(public)
        job = super(NudgeConversation, scoped.with_context(**{NO_SWITCH: outside}))._chat_job(provider, mode, rounds)
        # Nudge Agents: only its tools; its persona after Nudge's rules; masking when it says so
        job["tools"] = scoped._agent_offered(job.get("tools"))
        system = job.get("system")
        if outside and isinstance(system, list):
            # 60.1: nor a Developer mode to open with a button under the message box, nor an eye button to share
            # the screen (the switches of mode are left out by _can_use_agent above)
            system = [block.replace(prompt.DEV_SWITCHED_OFF, prompt.DEV_NOT_FOR_THEM).replace(
                SCREEN_HINT, NO_SCREEN_HINT) if isinstance(block, str) else block for block in system]
        job["system"] = prompt.with_agent(system, agent._persona_block(
            knowledge_only=knowledge_only, outside=outside, binding=public or None,
            hand_over=mode == MODE_TALK and tools.base.HAND_OVER_TOOL in (
                scoped.env.context.get("nudge_agent_tools") or ())))
        if agent.mask_personal_data and job.get("adapter") and not isinstance(job["adapter"], RedactingAdapter):
            job["adapter"] = RedactingAdapter(job["adapter"])
        return job

    def _background_job(self):
        agent = self.sudo().agent_id
        if not agent:
            return super()._background_job()
        scoped = self._agent_scope(self._chat_mode())
        # Asked again before each piece in the background: past the budget no provider is called
        # (_chat_choose), and the run is given up with the notice (nudge.run._work)
        over = agent._over_budget(self.env.user, rehearsal=self._agent_rehearsing(), conversation=self)
        if over:
            scoped = scoped.with_context(nudge_agent_no_ai=True)
        try:
            return super(NudgeConversation, scoped)._background_job()
        except AgentOverBudget:
            raise AgentOverBudget(self._agent_budget_text(over)) from None

    def _chat_record(self, outcome):
        if not self.sudo().agent_id:
            return super()._chat_record(outcome)
        # Nudge Agents: the controller records each answer on a cursor of its own: the scope is set again
        scoped = self._agent_scope(self._chat_mode())
        step = super(NudgeConversation, scoped)._chat_record(outcome)
        if step.get("tools") is not None:
            step["tools"] = scoped._agent_offered(step["tools"])
        return step

    def _chat_run_tools(self, calls):
        if not self.sudo().agent_id:
            return super()._chat_run_tools(calls)
        mode = self._chat_mode()
        scoped = self._agent_scope(mode)
        rehearsing = scoped._agent_rehearsing()
        # Nudge Agents: a tool the agent does not have is refused here, whatever the model sent (an injection, a
        # call read from its text), before tools.base.call would refuse it too
        refused = {call["id"] for call in calls if not tools.base.agent_allows(scoped.env, call["name"])}
        # A rehearsal: a change is never prepared for a yes (no card, no plan, no step in the log of changes):
        # it is run as a dry run and told to the model; every other tool runs and is undone (tools.base.rehearse)
        changes = {call["id"] for call in calls if rehearsing and call["id"] not in refused and not call.get("invalid")
                   and isinstance(tools.base.TOOLS.get(call["name"]), tools.base.Action)}
        others = [call for call in calls if call["id"] not in refused | changes]
        results, events, must_read, waiting = [], [], bool(refused or changes), self.env["nudge.step"].sudo()
        if others:
            results, events, read, waiting = super(NudgeConversation, scoped)._chat_run_tools(others)
            must_read = must_read or read
        snapshot = self._chat_screen_of_question() if changes else None
        for call in calls:
            if call["id"] in refused:
                content = tools.base.as_data(call["name"], f"There is no tool named {call['name']!r}.", True)
                results.append({"id": call["id"], "name": call["name"], "content": content, "is_error": True})
            elif call["id"] in changes:
                results.append(scoped._agent_rehearse(call, snapshot, mode))
        if rehearsing:
            # Nothing is typed in a field of the screen either
            events = [event for event in events if event.get("type") != "write"]
        by_id = {entry["id"]: entry for entry in results}
        return [by_id[call["id"]] for call in calls if call["id"] in by_id], events, must_read, waiting

    def _agent_rehearse(self, call, snapshot, mode):
        """:return: the result the model reads of a change asked in a rehearsal: its card in words"""
        # What the tools do is not the answer being recorded: no tier or price of it (nudge_provider_tier)
        env = self.with_context(
            nudge_conversation_id=self.id, nudge_mode=mode, nudge_background=False, nudge_rehearsal=True,
            nudge_answer_tier=False, nudge_priced_model=False, nudge_main_provider_id=False).env
        result = tools.base.call(env, call["name"], call["input"], snapshot, mode)
        return {"id": call["id"], "name": call["name"], "is_error": result.is_error,
                "content": tools.base.as_data(call["name"], result.content, result.is_error)}

    def _agent_decide(self, step_id, decision):
        if not self.sudo().agent_id:
            return super()._agent_decide(step_id, decision)
        # Nudge Agents: no yes is taken in a rehearsal, read from the conversation; and only what the agent may
        # still do
        if decision != DECLINE and self._agent_rehearsing():
            raise UserError(_("Nothing is done in a rehearsal."))
        scoped = self._agent_scope(self._chat_mode())
        step = self.env["nudge.step"].sudo().browse(step_id).exists() if isinstance(step_id, int) and step_id else None
        if (decision != DECLINE and step and step.kind != "plan"
                and not tools.base.agent_allows(scoped.env, step.tool)):
            raise UserError(_("This agent can no longer do this."))
        over = self.sudo().agent_id._over_budget(
            self.env.user, rehearsal=self._agent_rehearsing(), conversation=self)
        if not over:
            return super(NudgeConversation, scoped)._agent_decide(step_id, decision)
        # Past the budget, what follows a yes or a no is not a call to the model (_chat_choose raises before anything
        # is done): what waits is left undecided and the notice is the answer, as for a question. A step understood
        # without AI, or a stop, needs no model and goes through
        try:
            with self.env.cr.savepoint():
                return super(NudgeConversation, scoped.with_context(nudge_agent_no_ai=True))._agent_decide(
                    step_id, decision)
        except AgentOverBudget:
            return scoped._agent_budget_answer("", self._chat_mode(), over)

    # ------------------------------------------------------------------ a plan corrected before the yes
    def _plan_edit(self, plan, number, field, value):
        # Nudge Agents: the value is checked again as its card would be: within the agent's companies and tools
        if not self.sudo().agent_id:
            return super()._plan_edit(plan, number, field, value)
        return super(NudgeConversation, self._agent_scope(MODE_AGENT))._plan_edit(plan, number, field, value)

    def _plan_remove(self, plan, number):
        if not self.sudo().agent_id:
            return super()._plan_remove(plan, number)
        return super(NudgeConversation, self._agent_scope(MODE_AGENT))._plan_remove(plan, number)

    # ------------------------------------------------------------------ over the budget
    def _agent_budget_text(self, over):
        if over == "month":
            return _(
                "This agent has used its budget for this month: until the next month it answers without AI, "
                "from approved answers and the requests it understands by itself.")
        return _(
            "You have used what this agent may spend for you today: until tomorrow it answers without AI, from "
            "approved answers and the requests it understands by itself.")

    def _agent_budget_answer(self, text, mode, over):
        """Answer a question nothing answered without AI, or a yes or a no (``text`` empty) the model would have
        followed, once the agent's budget is used up: what waits is left undecided, and the notice is the answer.
        :return: the job of the stream, already answered"""
        return self._agent_fixed_answer(text, mode, self._agent_budget_text(over), "budget")

    def _agent_fixed_answer(self, text, mode, notice, source):
        """Answer ``text`` with the fixed sentence ``notice``, without AI: past a budget, an off-limits topic (60.1).
        What waits is left undecided.
        :param source: what ``_answer`` returns as its source, and the intent of the line of usage
        :return: the job of the stream, already answered"""
        self.ensure_one()
        conversation = self.sudo()
        conversation._agent_leave_undecided()
        if conversation.run_id:
            conversation.run_id._end(conversation.run_id._outcome("stopped"))
        values = {"run_id": False}
        if not conversation.name and text:
            values["name"] = text[:TITLE_CHARS].strip()
        conversation.write(values)
        Message = self.env["nudge.message"].sudo()
        question = Message.create(
            {"conversation_id": self.id, "role": "user", "content": text, "mode": mode}) if text else Message
        answer = Message.create({"conversation_id": self.id, "role": "assistant", "content": notice,
                                 "state": "complete"})
        self.env["nudge.usage"]._add(
            "noai", llm.normalize_usage(None), 0.0, self.env.user, self.env.company, intent=source,
            conversation_id=self.id, message_id=answer.id)
        return {
            "conversation_id": self.id,
            "user_message_id": question.id or False,
            "title": conversation.name,
            "mode": mode,
            "first_events": [{"type": "delta", "text": notice}],
            "answered": {"type": "done", "message_id": answer.id, "state": "complete", "message": ""},
            "agent_source": source,
        }

    def _agent_budget_notice(self, job, over):
        """An answer given without AI past the budget (the rules) says why the AI did not answer; a remembered
        answer is whole and says nothing. :return: ``job``"""
        answered = job.get("answered") or {}
        message = self.env["nudge.message"].sudo().browse(answered.get("message_id") or []).exists()
        if not answered or message.is_remembered:
            return job
        notice = self._agent_budget_text(over)
        said = bool(message.content)
        if message:
            message.content = f"{message.content}\n\n{notice}" if said else notice
        job["first_events"] = [*(job.get("first_events") or []),
                               {"type": "delta", "text": f"\n\n{notice}" if said else notice}]
        job["agent_source"] = "budget"
        return job

    # ------------------------------------------------------------------ what _answer returns
    def _agent_turn_result(self, job, events):
        """:return: ``text``, ``source``, ``tool_calls`` and ``error`` of the turn ``job`` started"""
        self.ensure_one()
        Message = self.env["nudge.message"].sudo()
        answers = Message.search([
            ("conversation_id", "=", self.id), ("role", "=", "assistant"),
            ("id", ">", job.get("user_message_id") or 0)], order="id")
        called = [call.get("name") for answer in answers for call in answer.tool_calls or ()
                  if isinstance(call, dict) and call.get("name")]
        said = answers.filtered("content")[-1:]
        last = events[-1] if events else {}
        kinds = set(self.env["nudge.usage"].sudo().search([("message_id", "in", answers.ids)]).mapped("kind"))
        if job.get("agent_source"):
            source = job["agent_source"]
        elif "chat" in kinds:
            source = "ai"
        elif answers.filtered("is_remembered") or kinds & {"memory", "cache"}:
            source = "memory"
        else:
            source = "noai"
        if said:
            return {"text": said.content, "source": source, "tool_calls": called, "error": False}
        if last.get("type") == "waiting":
            return {"text": _("This waits for a yes in the Nudge panel."), "source": source,
                    "tool_calls": called, "error": False}
        told = answers[-1:].error_message or last.get("message") or _("Nudge had no answer.")
        return {"text": told, "source": "refused", "tool_calls": called, "error": True}


class NudgeUsage(models.Model):
    _inherit = "nudge.usage"

    agent_id = fields.Many2one(
        "nudge.agent", string="Agent", readonly=True, index="btree_not_null", ondelete="set null",
        help="The agent that answered, when it was one.")
    rehearsal = fields.Boolean(
        readonly=True, help="A call made by Try it: left out of the Token Dashboard and of the agent's cost; it "
                            "still counts in the tokens of the person's day.")

    @api.model
    def _add(self, kind, usage, cost, user, company, **links):
        # Nudge Agents: a call of a conversation with an agent is the agent's; a rehearsal is marked as such, as
        # stored on the conversation
        if "agent_id" not in links:
            Conversation = self.env["nudge.conversation"].sudo()
            conversation = Conversation.browse(
                links.get("conversation_id") or self.env.context.get("nudge_conversation_id") or []).exists()
            if conversation.agent_id:
                links.update(agent_id=conversation.agent_id.id, rehearsal=conversation.agent_rehearsal)
        return super()._add(kind, usage, cost, user, company, **links)


class NudgeRun(models.Model):
    _inherit = "nudge.run"

    # Nudge Agents: Requests carry the agent of their conversation
    agent_id = fields.Many2one(related="conversation_id.agent_id", store=True, index="btree_not_null", string="Agent")

    def _work_plan(self, step, conversation):
        # Nudge Agents: a piece of a plan said yes to works in the scope of the agent, as the turn that prepared it:
        # its companies, its tools. An agent no longer open, or a step of a company it does not work in, fails the
        # step; the run is then given up by _background_job, which asks the same
        if not conversation or not conversation.sudo().agent_id:
            return super()._work_plan(step, conversation)
        try:
            scoped = conversation._agent_scope(MODE_AGENT)
            company = step.sudo().company_id
            if company and company not in scoped.env.companies:
                raise UserError(_(
                    "This was prepared in %s, a company this agent does not work in for you. Nothing was changed.",
                    company.name))
        except UserError as error:
            conversation.sudo()._agent_settle(step, "failed", tools.base.ToolResult(
                FAILED % (error.args[0] if error.args else str(error)), is_error=True))
            self._commit()
            return True
        return super()._work_plan(step, scoped)


class NudgeStep(models.Model):
    _inherit = "nudge.step"

    # Nudge Agents: Changes carry the agent of their request
    agent_id = fields.Many2one(related="run_id.agent_id", store=True, index="btree_not_null", string="Agent")


class NudgeSkill(models.Model):
    _inherit = "nudge.skill"

    @api.model
    def _visible(self, mode):
        # Nudge Agents: an agent that names skills works with those only, among those the person may use
        visible = super()._visible(mode)
        allowed = self.env.context.get("nudge_agent_skill_ids")
        return visible.filtered(lambda skill: skill.id in allowed) if allowed else visible

    @api.model_create_multi
    def create(self, vals_list):
        skills = super().create(vals_list)
        # Nudge Agents 60.1: a skill published by anybody but a system administrator may now be used by the agents
        # that name none: their public channels go back to unpublished
        if by_hand(self.env):
            skills._agent_steered(skills.sudo().filtered(lambda skill: skill.state == "published"))
        return skills

    def write(self, vals):
        steering = by_hand(self.env) and any(name in vals for name in (*SKILL_STEERING, "state", "active"))
        # sudo: whether they were published, whoever may see them
        published = self.sudo().filtered(lambda skill: skill.state == "published") if steering else self.browse()
        result = super().write(vals)
        # Nudge Agents: a skill an agent names steers what its public channels say: changed by anybody but a
        # system administrator, those channels go back to unpublished, for one to publish again (as a change of
        # the agent's own instructions does). 60.1: a published skill steers the agents that name none too
        if steering:
            self._agent_steered(published | self.sudo().filtered(lambda skill: skill.state == "published"))
        return result

    def _agent_steered(self, published):
        """Take back to unpublished the non-Discuss channels of the agents these skills steer: those that name one
        of them, and, for ``published`` skills, those that name none"""
        # sudo: the agents that use these skills, whoever may see them
        Agent = self.env["nudge.agent"].sudo().with_context(active_test=False)
        agents = Agent.search([("skill_ids", "in", self.ids)]) if self.ids else Agent
        if published:
            agents |= Agent.search([("skill_ids", "=", False), ("binding_ids.published", "=", True)])
        agents._unpublish_channels(_("A skill it uses changed"))


def same_question(text, memory):
    """:return: whether ``text`` asks what one of the wordings of ``memory`` asks: the same words that matter,
    folded and stemmed as the skill router reads them (Arabic particles and endings, English plurals)"""
    asked = set(skill_router.tokens_of(text))
    # sudo: the wordings of an answer already chosen for this person
    return bool(asked) and any(set(skill_router.tokens_of(line)) == asked for line in memory.sudo()._texts())


def by_hand(env):
    """:return: whether a change is made by somebody who is not a system administrator (nor Nudge's own code),
    which takes the public channels it steers back to unpublished (60.1, deferred 2.11)"""
    return not env.su and not env.user.has_group("base.group_system")


# What of an approved answer of the memory steers what the public channels of an agent say
MEMORY_STEERING = ("question", "variants", "answer", "state", "group_ids", "company_id", "active", "mode",
                   "translation_ids")


def memory_steered(env, memories, reason):
    """Nudge Agents 60.1 (deferred 2.11): a change of approved answers meant for everyone (no group) takes the
    non-Discuss channels of the agents that use the memory back to unpublished, in the companies of the answers"""
    # sudo: what the answers are and which agents use them, whoever may see them
    served = memories.sudo().filtered(lambda memory: memory.state == "approved" and not memory.group_ids)
    if not served:
        return
    domain = [("use_memory", "=", True), ("binding_ids.published", "=", True)]
    if not any(not memory.company_id for memory in served):
        domain.append(("company_ids", "in", served.company_id.ids))
    agents = env["nudge.agent"].sudo().with_context(active_test=False).search(domain)
    agents._unpublish_channels(reason)


class NudgeMemory(models.Model):
    _inherit = "nudge.memory"

    # ------------------------------------------------------------------ 60.1: what steers the public channels
    @api.model_create_multi
    def create(self, vals_list):
        memories = super().create(vals_list)
        if by_hand(self.env):
            memory_steered(self.env, memories, _("An approved answer of the memory changed"))
        return memories

    def write(self, vals):
        steering = by_hand(self.env) and any(name in vals for name in MEMORY_STEERING)
        # sudo: which of them were served to everyone before the change
        before = self.sudo().filtered(lambda memory: memory.state == "approved" and not memory.group_ids)             if steering else self.browse()
        result = super().write(vals)
        if steering:
            memory_steered(self.env, before | self, _("An approved answer of the memory changed"))
        return result

    def unlink(self):
        if by_hand(self.env):
            memory_steered(self.env, self, _("An approved answer of the memory changed"))
        return super().unlink()

    @api.model
    def _chat_serve(self, conversation, text, *args, **kwargs):
        # Nudge Agents: an agent told not to use the memory of answers asks on
        # A question asked outside a conversation (a phone, a bulk preview) has no agent
        agent = (conversation or self.env["nudge.conversation"]).sudo().agent_id
        if agent and not agent.use_memory:
            return None
        if agent and conversation._agent_public():
            # A visitor of a public channel: the approved answers were written for the people of the company
            return None
        if agent and conversation._agent_restricted():
            # Read by others than the person: only the answers meant for everyone, kept for no group (_servable)
            return super(NudgeMemory, self.with_context(nudge_memory_everyone=True))._chat_serve(
                conversation, text, *args, **kwargs)
        return super()._chat_serve(conversation, text, *args, **kwargs)

    @api.model
    def _servable(self, *args, **kwargs):
        servable = super()._servable(*args, **kwargs)
        if self.env.context.get("nudge_memory_everyone"):
            return servable.filtered(lambda memory: not memory.group_ids)
        return servable

    @api.model
    def _match(self, text, floor=False, mode=MODE_TALK, screen_model=None, steps_only=False):
        if not self.env.context.get("nudge_agent_id"):
            return super()._match(text, floor=floor, mode=mode, screen_model=screen_model, steps_only=steps_only)
        # Nudge Agents 60.1: an agent serves a remembered answer only for the same question: never the lower floor
        # of a database without AI, and the words that matter of the question are those of one of the answer's
        # wordings, no more and no fewer ("send a quotation by email" is not "confirm a quotation")
        memory, score, near = super()._match(
            text, floor=False, mode=mode, screen_model=screen_model, steps_only=steps_only)
        if memory and not same_question(text, memory):
            memory, near = None, [memory, *near]
        if self.env.context.get(OUTSIDE):
            # Outside the panel nobody can choose among close answers: there are no buttons
            near = []
        return memory, score, near


class NudgeMemoryTranslation(models.Model):
    _inherit = "nudge.memory.translation"

    # Nudge Agents 60.1: a translation of an approved answer is served as the answer is
    @api.model_create_multi
    def create(self, vals_list):
        translations = super().create(vals_list)
        if by_hand(self.env):
            memory_steered(self.env, translations.memory_id, _("An approved answer of the memory changed"))
        return translations

    def write(self, vals):
        result = super().write(vals)
        if by_hand(self.env) and {"question", "answer", "language", "memory_id"} & vals.keys():
            memory_steered(self.env, self.memory_id, _("An approved answer of the memory changed"))
        return result

    def unlink(self):
        if by_hand(self.env):
            memory_steered(self.env, self.memory_id, _("An approved answer of the memory changed"))
        return super().unlink()


class NudgeSlot(models.Model):
    _inherit = "nudge.slot"

    def _is_answering(self, user):
        # Nudge Agents 60.1: the service user of a public channel answers every visitor of it: one answer at a time
        # per visitor (their conversation, set by _agent_scope), not one for all of them
        conversation_id = self.env.context.get(PUBLIC_CONVERSATION)
        if conversation_id and isinstance(conversation_id, int) and user.has_group(SERVICE):
            Conversation = self.env["nudge.conversation"].sudo().with_context(active_test=False)
            if Conversation.search_count([
                    ("id", "=", conversation_id), ("stream_started_at", ">=", self._window_start())], limit=1):
                return True
            return bool(self.sudo().search_count([("conversation_ref", "=", conversation_id), *self._claimed()],
                                                 limit=1))
        return super()._is_answering(user)


class NudgeAnswerCache(models.Model):
    _inherit = "nudge.answer.cache"

    @api.model
    def _chat_serve(self, conversation, *args, **kwargs):
        # Nudge Agents: an answer kept for the same rights may hold a company the agent does not work in, and an
        # agent's answer is its own: none is served to an agent, none of an agent is kept
        record = conversation.sudo()
        if record.agent_id:
            if record.cache_pending:
                record.cache_pending = False
            return None
        return super()._chat_serve(conversation, *args, **kwargs)


class NudgeNoai(models.AbstractModel):
    _inherit = "nudge.noai"

    @api.model
    def _chat_serve(self, conversation, *args, **kwargs):
        # Nudge Agents: the rules read and open records; an answer read by others, or by a visitor of a public
        # channel (60.1), reads none
        if conversation and conversation._agent_restricted():
            return None
        return super()._chat_serve(conversation, *args, **kwargs)

    @api.model
    def _has_ai(self, conversation, mode):
        # Nudge Agents: past its budget an agent answers by the rules alone; its own provider counts as one
        if conversation and conversation.sudo().agent_id:
            if self.env.context.get("nudge_agent_no_ai"):
                return False
            if conversation._agent_provider():
                return True
        return super()._has_ai(conversation, mode)


class NudgeProvider(models.Model):
    _inherit = "nudge.provider"

    def _wrap_adapter(self, adapter):
        # Nudge Agents: an agent may force the masking of personal data, whatever the company's switch says
        adapter = super()._wrap_adapter(adapter)
        if self.env.context.get("nudge_agent_mask") and not isinstance(adapter, RedactingAdapter):
            adapter = RedactingAdapter(adapter)
        return adapter


class NudgeNote(models.Model):
    _inherit = "nudge.note"

    @api.model_create_multi
    def create(self, vals_list):
        # Nudge Agents, a rehearsal: a lesson the question asks to remember is not written down
        if self.env.context.get("nudge_rehearsal"):
            vals_list = [vals for vals in vals_list
                         if not (vals.get("kind") == "lesson" and vals.get("state") == "proposed")]
            if not vals_list:
                return self.browse()
        return super().create(vals_list)


@tools.base.register
class HandToPerson(tools.base.Tool):
    """Nudge Agents 60.1: hand the conversation to the person a channel names (its escalation user). Offered only
    in a turn of an agent whose channel names one (``_agent_scope`` puts it in the agent's tools); checked again
    when it runs, from the channel stored on the conversation. A visitor without an account is handed over only
    with their consent, which the channel passes (``_answer_public(consent=True)``)."""
    name = tools.base.HAND_OVER_TOOL
    description = (
        "Hand the conversation to a person of the company, who contacts the one you talk with. Use it when they ask "
        "for a person, or when you cannot help and they agree to be contacted. Write what they need in a few "
        "sentences, with their name and how to reach them when they gave it.")
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary"],
        "properties": {
            "summary": {"type": "string", "minLength": 5, "maxLength": 2000,
                        "description": "What they need, and how to reach them."},
        },
    }
    # Talk only: a public channel only asks, and a question in Talk is all a channel sends
    modes = (MODE_TALK,)
    kind = "create"
    summary = "Handed to a person"

    def is_available(self, env, mode=MODE_TALK):
        # Only in a turn whose channel names a person to hand over to (the scope of an agent puts it there)
        return bool(env.context.get(ESCALATION)) and tools.base.HAND_OVER_TOOL in (
            env.context.get("nudge_agent_tools") or ()) and super().is_available(env, mode)

    def run(self, env, arguments, screen):
        try:
            conversation_id = int(env.context.get("nudge_conversation_id") or 0)
        except (TypeError, ValueError):
            conversation_id = 0
        # sudo: the channel stored on the conversation, never what a request says
        conversation = env["nudge.conversation"].sudo().browse(conversation_id).exists()
        binding = conversation.agent_binding_id if conversation else env["nudge.agent.binding"].sudo()
        if not binding or binding.id != env.context.get(ESCALATION) or conversation.agent_id != binding.agent_id:
            return tools.base.ToolResult("Nobody can be handed this conversation here.", is_error=True)
        public = binding.acts_as == "service"
        consent = env.context.get(CONSENT) is True
        agent = conversation.agent_id.with_env(env)
        problem = agent._hand_over_problem(binding, consent=consent, public=public)
        if problem:
            return tools.base.ToolResult(problem, is_error=True)
        summary = arguments["summary"]
        asked = env["nudge.message"].sudo().search(
            [("conversation_id", "=", conversation.id), ("role", "=", "user")], order="id desc", limit=1).content or ""
        if not public:
            # A person is handed over when they ask for one, never because a text the model read says so
            if not asks_for_person(asked):
                return tools.base.ToolResult(
                    "Nothing was handed over: hand over only when the person asks for a person of the company.",
                    is_error=True)
            if tools.base.holds_outside(env):
                # The text of a record was read in this conversation: what goes to the colleague is what the
                # person wrote, never words the model may have taken from that text
                summary = asked
        if public:
            # Once a day per visitor (their key, else this conversation): reserved before the hand-over
            since = fields.Datetime.now() - datetime.timedelta(hours=24)
            mine = [("agent_visitor_key", "=", conversation.agent_visitor_key)] if conversation.agent_visitor_key \
                else [("id", "=", conversation.id)]
            if env["nudge.conversation"].sudo().search_count(
                    [("agent_binding_id", "=", binding.id), ("agent_handed_at", ">=", since), *mine], limit=1):
                return tools.base.ToolResult(
                    "Already handed over today: a person of the company will contact them. Tell them so.")
            conversation.agent_handed_at = fields.Datetime.now()
            if conversation.agent_visitor_key:
                summary = f"{summary}\n\n(Conversation {conversation.id} of the channel.)"
        if not agent._hand_to_person(binding, summary, consent=consent, public=public):
            if public:
                conversation.agent_handed_at = False
            return tools.base.ToolResult("Nothing was handed over: it could not be done here.", is_error=True)
        return tools.base.ToolResult(
            "Handed over: a person of the company will contact them. Tell them so in a sentence; nothing else is "
            "needed from them now.")

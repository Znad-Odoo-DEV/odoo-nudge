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
"""
from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from .. import tools
from ..llm import base as llm
from ..llm import loop, prompt
from ..llm.redact import RedactingAdapter
from ..tools.base import MODE_AGENT, MODE_DEV, MODE_TALK
from .nudge_agent import check_access
from .nudge_conversation import DECLINE, FAILED, TITLE_CHARS
from .nudge_skill import STEERING as SKILL_STEERING

# Written by the engine when a conversation starts, and never again
AGENT_FIELDS = frozenset({"agent_id", "agent_rehearsal", "agent_knowledge_only"})
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

    def _over_budget(self, user, rehearsal=False):
        """:return: "month" when the agent spent its budget of the month (everybody's real calls), "day" when
        ``user`` spent the tokens they may spend with it today (rehearsals included), else False. A rehearsal
        is not billed to the month: only the day of the person is checked for it."""
        self.ensure_one()
        agent = self.sudo()
        if not (agent.budget_month or agent.budget_user_day):
            return False
        Usage = self.env["nudge.usage"]
        today = fields.Date.context_today(self.with_user(user))
        mine = [("agent_id", "=", agent.id)]
        if agent.budget_month and not rehearsal:
            _tokens, cost = Usage._spent([*mine, ("rehearsal", "=", False), ("date", ">=", today.replace(day=1))])
            if cost >= agent.budget_month:
                return "month"
        if agent.budget_user_day:
            tokens, _cost = Usage._spent([*mine, ("user_id", "=", user.id), ("date", "=", today)])
            if tokens >= agent.budget_user_day:
                return "day"
        return False

    def _persona_block(self, knowledge_only=False):
        """:return: the block of the system prompt that makes Nudge this agent (``llm.prompt.render_agent``)"""
        self.ensure_one()
        agent = self.sudo()
        return prompt.render_agent(
            agent.name or "", agent.role or "", agent.instructions or "", agent.greeting or "",
            agent.lang_ids.mapped("name"), knowledge_only=knowledge_only)

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

    def _answer(self, text, lang=None, rehearsal=False, conversation=None, mode=None):
        """One full turn of this agent, as ``self.env.user``, the way the panel asks it: the same checks, the
        same answers without AI first, the same tools, waited for until the answer is written. With the
        context key ``nudge_agent_knowledge_only`` (an answer posted where others read it) no record is read.

        :param lang: code of the language the question is asked in (res.lang), or None
        :param rehearsal: Try it, for a manager who may edit the agent (ignored for anybody else): tools that
            change data are told, never run, nothing is written, and the usage is marked as a rehearsal
        :param conversation: the ``nudge.conversation`` to go on with (of this agent, of this person), or
            None to start one
        :param mode: "talk" or "agent"; None chooses (Agent only for a rehearsal that can prepare a change)
        :return: dict ``text`` (the answer, or what went wrong), ``source`` ("ai", "memory", "noai", "budget"
            or "refused"), ``tool_calls`` (names), ``conversation_id``, ``error`` (no answer was given)"""
        self.ensure_one()
        user = self.env.user
        result = {"text": "", "source": "refused", "tool_calls": [], "error": True,
                  "conversation_id": conversation.id if conversation else False}
        rehearsal = bool(rehearsal) and self._may_rehearse(user)
        if not user.has_group("nudge.group_nudge_user") or not self._open_to(user, rehearsal=rehearsal):
            return dict(result, text=self.env._("This agent is not open to you."))
        if conversation and conversation.sudo().agent_id.id != self.id:
            return dict(result, text=self.env._("This conversation is not one of this agent."))
        if conversation and conversation.sudo().agent_knowledge_only != bool(self.env.context.get(KNOWLEDGE_ONLY)):
            # Answers read by others and answers for the person alone are never in one conversation: its history
            # is sent with every question
            conversation = None
        Lang = self.env["res.lang"].sudo().with_context(active_test=False)
        language = Lang.search([("code", "=", lang)], limit=1) if lang else Lang
        context = {"nudge_agent_id": self.id, "nudge_rehearsal": rehearsal}
        if language.active:
            context["lang"] = language.code
        Conversation = self.env["nudge.conversation"].with_context(**context)
        mode = mode if mode in (MODE_TALK, MODE_AGENT) else self._answer_mode(user, rehearsal)
        try:
            # A question refused before it is answered leaves nothing behind
            with self.env.cr.savepoint():
                chat = Conversation._chat_open(conversation.id if conversation else None)
                job = chat._chat_prepare(text, mode=mode, answer_in=language.name or None)
        except (UserError, AccessError) as error:
            return dict(result, text=str(error.args[0]) if error.args else str(error))
        try:
            events = list(loop.run(job, chat._chat_record))
        finally:
            if job.get("slot_id"):
                # Its place goes to the next in line, as the controller does when a stream ends
                self.env["nudge.slot"]._finish(job["slot_id"])
        return dict(result, conversation_id=chat.id, **chat._agent_turn_result(job, events))


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
            for conversation in conversations.sudo().filtered(lambda conversation: not conversation.agent_id):
                person = conversation.user_id
                # A rehearsal is for a manager who may edit the agent; asked by anybody else it is ignored
                rehearsal = bool(self.env.context.get("nudge_rehearsal")) and bool(agent) and agent._may_rehearse(
                    person)
                if not agent or not agent._open_to(person, rehearsal=rehearsal):
                    raise UserError(self.env._("This agent is not open to you."))
                conversation.write({
                    "agent_id": agent.id, "agent_rehearsal": rehearsal, "agent_knowledge_only": knowledge_only})
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
                    == conversation.agent_knowledge_only)
                for conversation in self)
            if not allowed:
                raise AccessError(self.env._("The agent of a conversation is set when it starts and never changes."))
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
        agent = self.sudo().agent_id
        if not agent:
            return self
        user = self.env.user
        rehearsing = self._agent_rehearsing()
        if not agent._open_to(user, rehearsal=rehearsing):
            raise UserError(self.env._("This agent is not open to you."))
        mode = mode or self._chat_mode()
        if mode == MODE_DEV:
            raise UserError(self.env._("Developer mode is not open in a conversation with an agent."))
        companies = agent._companies_for(user) & self.env.companies
        if not companies:
            raise UserError(self.env._(
                "This agent works in other companies than the ones you selected. Select one of its companies."))
        current = self.env.company if self.env.company in companies else companies[0]
        allowed = [current.id, *(companies - current).ids]
        # As the person, in those companies, with nothing restricted yet: what the agent may offer them
        names = agent.with_context(
            allowed_company_ids=allowed, nudge_agent_tools=None, nudge_skills={"index": True},
        )._tools_for(user, mode)
        if not agent.use_docs:
            names = [name for name in names if name not in DOC_TOOLS]
        knowledge_only = self._agent_knowledge_only()
        if knowledge_only:
            # Read by others than the person: no record of the database, only Odoo's documentation and skills
            names = [name for name in names if name in KNOWLEDGE_TOOLS]
        return self.with_context(
            allowed_company_ids=allowed,
            nudge_agent_id=agent.id,
            nudge_agent_tools=tuple(names),
            # Set from the conversation either way, as the rehearsal below
            **{KNOWLEDGE_ONLY: knowledge_only},
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
            raise UserError(self.env._("This conversation is not one of this agent. Start a new one."))
        if (record.agent_id and self.env.context.get("nudge_rehearsal") and not record.agent_rehearsal
                and record.agent_id._may_rehearse(self.env.user)):
            raise UserError(self.env._("This conversation is not a rehearsal. Start a new one to try the agent."))
        if record.agent_id and record.agent_knowledge_only != bool(self.env.context.get(KNOWLEDGE_ONLY)):
            # Answers read by others and answers for the person alone are never in one conversation
            raise UserError(self.env._("This conversation cannot go on here. Start a new one."))
        if record.agent_id and not record.agent_id._open_to(self.env.user, rehearsal=record.agent_rehearsal):
            raise UserError(self.env._("This agent is not open to you."))
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
            raise UserError(self.env._("Developer mode is not open in a conversation with an agent."))
        rehearsing = self._agent_rehearsing()
        if rehearsing:
            # A rehearsal is watched as it goes: nothing is left to the background
            given["background"] = False
        scoped = self._agent_scope(MODE_AGENT if mode == MODE_AGENT else MODE_TALK)
        over = agent._over_budget(self.env.user, rehearsal=rehearsing)
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
            raise AgentOverBudget(self.env._("This agent has used its budget."))
        return super()._chat_choose(mode)

    def _chat_provider(self, mode=None):
        # Nudge Agents: the agent's own provider answers when it has one that may (egress and budgets after)
        return self._agent_provider(ensure=True) or super()._chat_provider(mode)

    def _chat_where_the_work_is(self):
        # Nudge Agents: an answer read by others says nothing of where this person's data is
        if self._agent_knowledge_only():
            return ""
        return super()._chat_where_the_work_is()

    def _chat_job(self, provider, mode, rounds):
        agent = self.sudo().agent_id
        if not agent:
            return super()._chat_job(provider, mode, rounds)
        scoped = self._agent_scope(mode)
        job = super(NudgeConversation, scoped)._chat_job(provider, mode, rounds)
        # Nudge Agents: only its tools; its persona after Nudge's rules; masking when it says so
        job["tools"] = scoped._agent_offered(job.get("tools"))
        job["system"] = prompt.with_agent(
            job.get("system"), agent._persona_block(knowledge_only=self._agent_knowledge_only()))
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
        over = agent._over_budget(self.env.user, rehearsal=self._agent_rehearsing())
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
            raise UserError(self.env._("Nothing is done in a rehearsal."))
        scoped = self._agent_scope(self._chat_mode())
        step = self.env["nudge.step"].sudo().browse(step_id).exists() if isinstance(step_id, int) and step_id else None
        if (decision != DECLINE and step and step.kind != "plan"
                and not tools.base.agent_allows(scoped.env, step.tool)):
            raise UserError(self.env._("This agent can no longer do this."))
        over = self.sudo().agent_id._over_budget(self.env.user, rehearsal=self._agent_rehearsing())
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
            return self.env._(
                "This agent has used its budget for this month: until the next month it answers without AI, "
                "from approved answers and the requests it understands by itself.")
        return self.env._(
            "You have used what this agent may spend for you today: until tomorrow it answers without AI, from "
            "approved answers and the requests it understands by itself.")

    def _agent_budget_answer(self, text, mode, over):
        """Answer a question nothing answered without AI, or a yes or a no (``text`` empty) the model would have
        followed, once the agent's budget is used up: what waits is left undecided, and the notice is the answer.
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
        notice = self._agent_budget_text(over)
        Message = self.env["nudge.message"].sudo()
        question = Message.create(
            {"conversation_id": self.id, "role": "user", "content": text, "mode": mode}) if text else Message
        answer = Message.create({"conversation_id": self.id, "role": "assistant", "content": notice,
                                 "state": "complete"})
        self.env["nudge.usage"]._add(
            "noai", llm.normalize_usage(None), 0.0, self.env.user, self.env.company, intent="budget",
            conversation_id=self.id, message_id=answer.id)
        return {
            "conversation_id": self.id,
            "user_message_id": question.id or False,
            "title": conversation.name,
            "mode": mode,
            "first_events": [{"type": "delta", "text": notice}],
            "answered": {"type": "done", "message_id": answer.id, "state": "complete", "message": ""},
            "agent_source": "budget",
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
            return {"text": self.env._("This waits for a yes in the Nudge panel."), "source": source,
                    "tool_calls": called, "error": False}
        told = answers[-1:].error_message or last.get("message") or self.env._("Nudge had no answer.")
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
                raise UserError(self.env._(
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

    def write(self, vals):
        result = super().write(vals)
        # Nudge Agents: a skill an agent names steers what its public channels say: changed by anybody but a
        # system administrator, those channels go back to unpublished, for one to publish again (as a change of
        # the agent's own instructions does)
        if not self.env.su and not self.env.user.has_group("base.group_system") and any(
                name in vals for name in (*SKILL_STEERING, "state", "active")):
            # sudo: the agents that name these skills, whoever may see them
            agents = self.env["nudge.agent"].sudo().with_context(active_test=False).search(
                [("skill_ids", "in", self.ids)])
            agents._unpublish_channels(self.env._("A skill it uses changed"))
        return result


class NudgeMemory(models.Model):
    _inherit = "nudge.memory"

    @api.model
    def _chat_serve(self, conversation, text, *args, **kwargs):
        # Nudge Agents: an agent told not to use the memory of answers asks on
        # A question asked outside a conversation (a phone, a bulk preview) has no agent
        agent = (conversation or self.env["nudge.conversation"]).sudo().agent_id
        if agent and not agent.use_memory:
            return None
        if agent and conversation._agent_knowledge_only():
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
        # Nudge Agents: the rules read and open records; an answer read by others reads none
        if conversation and conversation._agent_knowledge_only():
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

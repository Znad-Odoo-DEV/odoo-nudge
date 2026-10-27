"""Nudge Agents: a named helper a manager makes for one kind of work (a sales assistant, an accounting expert).

An agent never adds a right. Whatever it reads or does, it reads and does as the person who asks, in the
companies that person works in and the agent is made for, with the tools the person's own groups allow
(``_tools_for``), and never a developer tool. The record rules hide an agent from the people outside its
audience and its companies. What it says and how it is limited is written here; the engine that runs a turn
is ``nudge_agent_engine.py``, the screens ``nudge_agent_ui.py``.
"""
from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..tools import base, skills as skill_rules

# What a change of the agent is kept for, with its difference
TRACKED = ("name", "instructions", "tool_keys", "skill_ids", "company_ids", "group_ids", "user_ids", "provider_id",
           "state", "active", "mask_personal_data", "use_memory", "use_docs", "budget_month", "budget_user_day")
# The score a published agent needs, out of 100
PUBLISH_SCORE = 80.0
CHANNELS = [("discuss", "Discuss"), ("website", "Website"), ("whatsapp", "WhatsApp"), ("telegram", "Telegram"),
            ("mcp", "MCP")]
SERVICE_CHANNELS = ("website", "whatsapp", "telegram")
# What a tool does without changing anything: the tools an agent has when none is named
READ_KINDS = ("read", "show")
# The tools an agent has when none is named: the ones that only read, named one by one. A tool that writes,
# even with sudo (remember_lesson, write_in_field), is never here, nor one that is added later by default
DEFAULT_READ_TOOLS = base.ASK_TOOLS | frozenset({
    "search_odoo_documentation", "show_on_screen", "show_menu_path", "find_screen", "open_screen",
    "describe_model", "locate_data", "search_company_files", "search_products"})
# Models a service channel never reads, whatever is written in its allow-list: the configuration, payments and
# accounting, the messages and channels of people, Nudge's own records, the users, pay and leave
DENIED_MODEL_PREFIXES = ("ir.", "payment.", "account.", "mail.", "discuss.", "nudge.", "res.users.", "hr.payslip",
                         "hr.leave")
DENIED_MODELS = frozenset({"res.users", "res.groups", "res.partner", "res.partner.bank", "res.users.apikeys",
                           "hr.employee", "hr.version", "hr.contract", "hr.payslip", "hr.leave"})
# What a change of these takes the non-Discuss channels of an agent back to unpublished
CHANNEL_STEERING = ("instructions", "tool_keys", "skill_ids", "company_ids", "use_docs", "use_memory", "provider_id",
                    "mask_personal_data")
# Tools that are for the one who builds Odoo, whatever else their group says
DEVELOPER_ONLY = base.DEV_TOOLS - base.AGENT_TOOLS - base.TALK_TOOLS
# Tools that widen what is offered (an agent works with the list it was given), and a scheduled task, which runs
# later as plain Nudge, outside the agent (v1): never offered, and refused by tools.base.agent_allows
NEVER_OFFERED = base.AGENT_NEVER_TOOLS
# The state and the score of an agent: set by its buttons and its evaluation only
GATE_FIELDS = ("state", "score")


def developer_tool(tool):
    """:return: whether ``tool`` is one of Developer mode: run code, run SQL, build automations. Never allowed to an agent"""
    return (tool.name in DEVELOPER_ONLY or tool.group == "base.group_system" or tool.kind == "code"
            or tuple(tool.modes) == (base.MODE_DEV,))


def check_access(records, operation):
    """Raise AccessError when the user of ``records`` may not do ``operation`` on them: the access rights of the
    model and its record rules. ``check_access`` of Odoo 18 and 19; on 17, the two checks it replaced"""
    if hasattr(type(records), "check_access"):
        records.check_access(operation)
    else:
        records.check_access_rights(operation)
        records.check_access_rule(operation)


def user_groups(user):
    # v19 names it all_group_ids; 17 and 18 groups_id (implied groups included)
    user = user.sudo()
    return user.all_group_ids if "all_group_ids" in user._fields else user.groups_id


class NudgeAgent(models.Model):
    _name = "nudge.agent"
    _inherit = ["nudge.recipe.mixin"]
    _description = "Nudge Agent"
    _order = "name, id"

    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)
    image_128 = fields.Image(string="Picture", max_width=128, max_height=128)
    partner_id = fields.Many2one(
        "res.partner", string="Contact", readonly=True, copy=False, ondelete="restrict",
        help="The face of the agent in Discuss: a contact, never a user.")
    role = fields.Char(help="One line: what this agent is for.", translate=True)
    instructions = fields.Text(help="How the agent works, added to Nudge's own instructions, never in their place.")
    greeting = fields.Char(translate=True)
    lang_ids = fields.Many2many("res.lang", "nudge_agent_lang_rel", "agent_id", "lang_id", string="Languages")
    company_ids = fields.Many2many(
        "res.company", "nudge_agent_company_rel", "agent_id", "company_id", string="Companies", required=True,
        default=lambda self: self.env.company,
        help="The agent works only in these companies, and for a person only in those they work in too.")
    group_ids = fields.Many2many(
        "res.groups", "nudge_agent_group_rel", "agent_id", "group_id", string="Audience: Groups")
    user_ids = fields.Many2many(
        "res.users", "nudge_agent_user_rel", "agent_id", "user_id", string="Audience: People")
    provider_id = fields.Many2one(
        "nudge.provider", string="Provider", help="Empty: the provider of the company.")
    tool_keys = fields.Char(
        string="Tools",
        help="Technical names of the tools it may use, separated by commas. Empty: only the tools that read. "
             "A tool is offered only when the person may use it anyway.")
    skill_ids = fields.Many2many(
        "nudge.skill", "nudge_agent_skill_rel", "agent_id", "skill_id", string="Skills",
        help="Empty: Nudge chooses among the published skills.")
    use_memory = fields.Boolean(string="Use Memory", default=True, help="Answer from the approved memory the person may read.")
    use_docs = fields.Boolean(string="Use Company Files")
    mask_personal_data = fields.Boolean(string="Mask Personal Data")
    budget_month = fields.Float(string="Monthly Budget", help="Cost of the agent in a month. 0: none.")
    budget_user_day = fields.Integer(string="Tokens per Person per Day", help="0: none.")
    state = fields.Selection(
        [("draft", "Draft"), ("tested", "Tested"), ("published", "Published"), ("archived", "Archived")],
        default="draft", required=True, index=True, copy=False)
    score = fields.Float(string="Score", copy=False, readonly=True, help="Out of 100: the share of its evaluation it passes.")
    eval_ids = fields.One2many("nudge.agent.eval", "agent_id", string="Evaluation", copy=True)
    binding_ids = fields.One2many("nudge.agent.binding", "agent_id", string="Channels")
    revision_ids = fields.One2many("nudge.agent.revision", "agent_id", string="Changes", readonly=True)
    notes = fields.Text()

    # ------------------------------------------------------------------ checks
    @api.constrains("tool_keys")
    def _check_tool_keys(self):
        for agent in self:
            names = skill_rules.split_names(agent.tool_keys)
            unknown = [name for name in names if name not in base.TOOLS]
            if unknown:
                raise ValidationError(self.env._("No tool of Nudge here is named %s.", ", ".join(unknown)))
            developer = [name for name in names if developer_tool(base.TOOLS[name])]
            if developer:
                raise ValidationError(self.env._("An agent never has a developer tool: %s.", ", ".join(developer)))
            never = [name for name in names if name in NEVER_OFFERED]
            if never:
                raise ValidationError(self.env._("An agent cannot be given %s.", ", ".join(never)))

    @api.constrains("company_ids")
    def _check_company_ids(self):
        """A manager cannot push an agent into a company they do not work in"""
        if self.env.su:
            return
        mine = self.env.user.company_ids
        for agent in self:
            if not agent.company_ids <= mine:
                raise ValidationError(self.env._("An agent can only be made for the companies you work in."))

    # --------------------------------------------------------------- who, where, with what
    def _can_use(self, user, ignore_state=False):
        """:return: whether ``user`` may talk to this agent: in its audience (named, or by a group) and in
        one of its companies, and the agent is published (``ignore_state``: only the access preview, which asks
        what the person would have once it is). Whatever else the person may do, the agent adds nothing to it."""
        self.ensure_one()
        # sudo: reads the agent's own configuration, which the person may not be allowed to see
        agent = self.sudo()
        user = user.sudo()
        # A manager rehearses and evaluates the agents of their companies, with their own rights
        manager = user.has_group("nudge.group_nudge_manager")
        # An archived or unpublished agent is for the managers of its companies, to rehearse
        if not ignore_state and not (agent.active and agent.state == "published") and not manager:
            return False
        if not manager and user not in agent.user_ids and not (user_groups(user) & agent.group_ids):
            return False
        return bool(agent._companies_for(user))

    def _companies_for(self, user):
        """:return: the companies the agent works in for ``user``: those the person may work in, that are also the
        agent's. Every data read of a turn is made within these."""
        self.ensure_one()
        # sudo: reads the agent's own configuration and the companies the person is allowed in
        return user.sudo().company_ids & self.sudo().company_ids

    def _tools_for(self, user, mode):
        """:return: the sorted names of the tools the agent offers ``user`` in ``mode``: what the agent allows
        (``tool_keys``, else ``DEFAULT_READ_TOOLS``) intersected with what the mode has and what the person's own
        groups give (``base.available`` as that person: group, module installed, plugin allowed). A developer
        tool is never there, nor Developer mode itself. A tool the person does not have is not given because
        the agent names it."""
        self.ensure_one()
        if mode == base.MODE_DEV:
            return []
        person = self.env(user=user.id if isinstance(user, models.BaseModel) else user)
        offered = {tool.name: tool for tool in base.available(person, mode, "full")}
        # sudo: reads the agent's own configuration
        named = set(skill_rules.split_names(self.sudo().tool_keys))
        if named:
            allowed = named
        else:
            allowed = {name for name, tool in offered.items()
                       if name in DEFAULT_READ_TOOLS and getattr(tool, "kind", None) in READ_KINDS}
        return sorted(
            name for name in allowed & set(offered)
            if name not in NEVER_OFFERED and not developer_tool(offered[name]))

    # ----------------------------------------------------------- the face
    @api.model_create_multi
    def create(self, vals_list):
        Partner = self.env["res.partner"].sudo()
        for vals in vals_list:
            if vals.get("partner_id") and not self.env.su:
                raise AccessError(self.env._("The contact of an agent is made by the agent itself."))
            if not self.env.su and (vals.get("state", "draft") != "draft" or vals.get("score")):
                # The publish gate: an agent is made a draft with no score (what the form sends), and published
                # by its buttons after its evaluation
                raise AccessError(self.env._("The state and the score of an agent change only by its buttons."))
        for vals in vals_list:
            if not vals.get("partner_id"):
                # sudo: the contact is the face Nudge gives the agent; a manager need not manage contacts
                vals["partner_id"] = Partner.create({
                    "name": vals.get("name") or "Agent", "type": "contact", "is_company": False,
                    "image_1920": vals.get("image_128") or False}).id
        agents = super().create(vals_list)
        if not self.env.su and any(agent.state != "draft" or agent.score for agent in agents):
            # Nor from a default of the context
            raise AccessError(self.env._("The state and the score of an agent change only by its buttons."))
        Revision = self.env["nudge.agent.revision"].sudo()
        for agent in agents:
            Revision.create({
                # sudo: the history is written by Nudge for every change, whoever makes it
                "agent_id": agent.id, "user_id": self.env.uid, "what": self.env._("Created"),
                "diff": self.env["nudge.skill"]._diff({}, agent._snapshot(TRACKED))})
        return agents

    def write(self, vals):
        if not self.env.su:
            if "partner_id" in vals:
                raise AccessError(self.env._("The contact of an agent is made by the agent itself."))
            # The gate is also in write for a manager; the superuser (Nudge's own code: Discuss, tests, the
            # buttons below, which write as sudo after their own checks) is not stopped
            if vals.get("state") == "published":
                for agent in self.filtered(lambda a: a.state != "published"):
                    agent._check_publish()
            if any(name in vals for name in GATE_FIELDS):
                raise AccessError(self.env._("The state and the score of an agent change only by its buttons."))
            self._check_my_companies()
            if not self.env.user.has_group("base.group_system") and any(name in vals for name in CHANNEL_STEERING):
                self._unpublish_channels(self.env._("Its instructions or limits changed"))
        names = [name for name in TRACKED if name in vals]
        before = {agent.id: agent._snapshot(names) for agent in self} if names else {}
        result = super().write(vals)
        if "name" in vals or "image_128" in vals or "active" in vals:
            for agent in self.filtered("partner_id"):
                face = {}
                if "name" in vals:
                    face["name"] = agent.name
                if "image_128" in vals:
                    face["image_1920"] = agent.image_128
                if "active" in vals:
                    face["active"] = bool(vals["active"])
                # sudo: keeps the contact in step with the agent, if it is only a contact: never a user's or a
                # company's
                partner = agent.partner_id.sudo()
                if face and not partner.user_ids and not self.env["res.company"].sudo().search_count(
                        [("partner_id", "=", partner.id)]):
                    partner.write(face)
        if names:
            self._keep_revision(before, names)
        return result

    def _unpublish_channels(self, reason):
        """Takes the published channels other than Discuss back to unpublished, with a revision that says why"""
        Revision = self.env["nudge.agent.revision"].sudo()
        for agent in self:
            bindings = agent.sudo().binding_ids.filtered(lambda b: b.published and b.channel != "discuss")
            if bindings:
                bindings.sudo().write({"published": False})
                Revision.create({
                    "agent_id": agent.id, "user_id": self.env.uid,
                    "what": self.env._("Channels unpublished"), "diff": reason})

    def unlink(self):
        if not self.env.su:
            # Who may not delete it is told so by its access rights; a manager is told to archive it
            check_access(self, "unlink")
            raise UserError(self.env._("Archive an agent instead: its history is kept."))
        return super().unlink()

    def _check_my_companies(self):
        """An agent shared by several companies is changed, published, evaluated and given channels only by
        someone who works in every one of them (the record rules show it to whoever works in one).

        :raise AccessError: when a company of one of these agents is not one of the user's"""
        if self.env.su:
            return
        mine = self.env.user.company_ids
        for agent in self:
            # sudo: the agent's own companies, some of which the user may not see
            if not agent.sudo().company_ids <= mine:
                raise AccessError(self.env._("An agent of a company you do not work in cannot be changed."))

    def _keep_revision(self, before, names):
        Revision = self.env["nudge.agent.revision"].sudo()
        for agent in self:
            now = agent._snapshot(names)
            diff = self.env["nudge.skill"]._diff(before[agent.id], now)
            if diff:
                Revision.create({
                    "agent_id": agent.id, "user_id": self.env.uid, "diff": diff,
                    "what": ", ".join(agent._fields[name].string for name in names
                                      if before[agent.id][name] != now[name])})

    def _snapshot(self, names):
        self.ensure_one()
        values = {}
        for name in names:
            value = self[name]
            if isinstance(value, models.BaseModel):
                value = ", ".join(sorted(value.mapped("display_name")))
            values[name] = "" if value is False or value is None else str(value)
        return values

    # -------------------------------------------------------------- buttons
    def _check_manager(self):
        if not self.env.su and not self.env.user.has_group("nudge.group_nudge_manager"):
            raise AccessError(self.env._("Only Nudge managers publish agents."))

    def _check_publish(self):
        """An agent is published only after its evaluation: at least one question, every one passed, and a
        score of 80 or more."""
        for agent in self:
            evals = agent.eval_ids
            if not evals:
                raise UserError(self.env._("Write at least one evaluation question and run it before publishing."))
            if not all(evals.mapped("passed")):
                raise UserError(self.env._("Every evaluation question must pass before the agent is published."))
            if agent.score < PUBLISH_SCORE:
                raise UserError(self.env._("The score of the agent is %(score)s; it needs %(needed)s to be published.",
                                           score=round(agent.score), needed=round(PUBLISH_SCORE)))

    def action_publish(self):
        self._check_manager()
        self._check_my_companies()
        self._check_publish()
        self.sudo().write({"state": "published"})
        return True

    def action_archive(self):
        self._check_manager()
        self._check_my_companies()
        self.sudo().write({"state": "archived", "active": False})
        return True

    def action_unarchive(self):
        self.action_draft()
        return True

    def action_draft(self):
        self._check_manager()
        self._check_my_companies()
        self.sudo().write({"state": "draft", "active": True})
        return True


class NudgeAgentEval(models.Model):
    _name = "nudge.agent.eval"
    _description = "Nudge Agent Evaluation"
    _order = "agent_id, id"

    agent_id = fields.Many2one("nudge.agent", required=True, ondelete="cascade", index=True)
    question = fields.Text(required=True)
    lang = fields.Char(string="Language", help="Code of the language the question is asked in, for example ar_001.")
    expected = fields.Selection(
        [("contains", "Answer contains"), ("no_ai", "Answered without AI"), ("answered", "Answered")],
        default="answered", required=True)
    expected_text = fields.Char(string="Expected Text")
    # Given with the rights of the one who ran the evaluation, the answer may name records others may not read:
    # shown to them alone (answer_shown); the stored text is read in sudo, or by a system administrator
    last_answer = fields.Text(readonly=True, copy=False, groups="base.group_system")
    answer_shown = fields.Text(string="Last Answer", compute="_compute_answer_shown")
    last_source = fields.Char(readonly=True, copy=False)
    passed = fields.Boolean(copy=False, readonly=True)
    run_date = fields.Datetime(readonly=True, copy=False)
    run_by = fields.Many2one("res.users", string="Run By", readonly=True, copy=False)

    # Written by the evaluation runner (as sudo) only: otherwise the publish gate could be forged
    RESULT_FIELDS = ("last_answer", "last_source", "passed", "run_date", "run_by")

    @api.depends_context("uid")
    def _compute_answer_shown(self):
        for evaluation in self:
            # sudo: the stored answer, given below only to the one who ran it
            record = evaluation.sudo()
            if self.env.su or record.run_by == self.env.user:
                evaluation.answer_shown = record.last_answer
            elif record.last_answer:
                evaluation.answer_shown = self.env._(
                    "Answered with the rights of %s: shown to them only.", record.run_by.name or "")
            else:
                evaluation.answer_shown = False

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            for vals in vals_list:
                if any(vals.get(name) for name in self.RESULT_FIELDS):
                    raise AccessError(self.env._("The result of an evaluation is written by running it."))
            self.env["nudge.agent"].browse(
                {vals["agent_id"] for vals in vals_list if vals.get("agent_id")})._check_my_companies()
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            if any(name in vals for name in self.RESULT_FIELDS):
                raise AccessError(self.env._("The result of an evaluation is written by running it."))
            (self.agent_id | self.env["nudge.agent"].browse(vals.get("agent_id") or []))._check_my_companies()
        return super().write(vals)

    def unlink(self):
        self.agent_id._check_my_companies()
        return super().unlink()


class NudgeAgentBinding(models.Model):
    _name = "nudge.agent.binding"
    _description = "Nudge Agent Channel"
    _order = "agent_id, channel, id"

    agent_id = fields.Many2one("nudge.agent", required=True, ondelete="cascade", index=True)
    channel = fields.Selection(CHANNELS, required=True, default="discuss")
    acts_as = fields.Selection(
        [("person", "The person who asks"), ("service", "A service user")], default="person", required=True,
        help="A service user answers people who have no user of their own (the website, WhatsApp, Telegram), "
             "and reads only the models allowed here.")
    service_user_id = fields.Many2one(
        "res.users", string="Service User", domain="[('share', '=', False)]",
        help="Must hold the group Nudge: agent service user and no other group of Nudge.")
    model_allowlist = fields.Char(
        string="Readable Models", help="Technical names of the models a service user may read, separated by commas.")
    escalation_user_id = fields.Many2one("res.users", string="Escalate To")
    published = fields.Boolean(
        help="On a service channel only a system administrator publishes it.")

    # Fields that decide what a published service binding may do: changing one takes it back to unpublished
    SERVICE_STEERING = ("channel", "acts_as", "service_user_id", "model_allowlist")

    @api.constrains("model_allowlist")
    def _check_allowlist(self):
        for binding in self:
            for name in skill_rules.split_names(binding.model_allowlist):
                if name not in self.env:
                    raise ValidationError(self.env._("No model here is named %s.", name))
                if name in DENIED_MODELS or name.startswith(DENIED_MODEL_PREFIXES):
                    raise ValidationError(self.env._("A service channel never reads the model %s.", name))

    def _service_user_problem(self):
        """:return: why the service user of this binding is not fit (a text), or None. It may hold the service
        group and what a user, or a portal or public user, holds anyway: nothing else"""
        self.ensure_one()
        user = self.service_user_id
        if not user:
            return self.env._("A service binding needs its service user.")
        service_group = self.env.ref("nudge.group_agent_service")
        groups = user_groups(user)
        if service_group not in groups:
            return self.env._("%s does not hold the group Nudge: agent service user.", user.name)
        if user.sudo().share:
            basic = self.env.ref("base.group_portal") | self.env.ref("base.group_public")
        else:
            basic = self.env.ref("base.group_user")
        implied = basic.all_implied_ids if "all_implied_ids" in basic._fields else basic.trans_implied_ids
        extra = groups - (service_group | basic | implied)
        if extra:
            return self.env._(
                "The service user %(user)s must hold only the agent service group of Nudge, no other group of "
                "Nudge and no administration group: %(groups)s.", user=user.name,
                groups=", ".join(extra.sudo().mapped("display_name")))
        return None

    def _service_user_ok(self):
        """:return: whether the service user is still fit: also asked at the time of an answer"""
        self.ensure_one()
        return self.acts_as != "service" or self._service_user_problem() is None

    @api.constrains("channel", "acts_as", "service_user_id")
    def _check_service(self):
        for binding in self:
            if binding.acts_as != "service":
                continue
            if binding.channel not in SERVICE_CHANNELS:
                raise ValidationError(self.env._("A service user answers only on the website, WhatsApp or Telegram."))
            problem = binding._service_user_problem()
            if problem:
                raise ValidationError(problem)

    def _is_system(self):
        return self.env.su or self.env.user.has_group("base.group_system")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("published") and not self._is_system() and (
                    vals.get("acts_as") == "service" or vals.get("channel", "discuss") != "discuss"):
                raise AccessError(self.env._("Only a system administrator publishes a service channel."))
        self.env["nudge.agent"].browse(
            {vals["agent_id"] for vals in vals_list if vals.get("agent_id")})._check_my_companies()
        bindings = super().create(vals_list)
        bindings._keep_revision(self.env._("Channel added"), added=True)
        return bindings

    def write(self, vals):
        (self.agent_id | self.env["nudge.agent"].browse(vals.get("agent_id") or []))._check_my_companies()
        if not self._is_system():
            for binding in self:
                acts_as = vals.get("acts_as", binding.acts_as)
                channel = vals.get("channel", binding.channel)
                if vals.get("published") and (acts_as == "service" or channel != "discuss"):
                    raise AccessError(self.env._("Only a system administrator publishes a service channel."))
            if any(name in vals for name in self.SERVICE_STEERING) and "published" not in vals:
                vals = dict(vals)
                # A manager who changes what a published service channel does takes it down for a system
                # administrator to publish again
                steering = self.filtered(lambda b: b.published and (
                    vals.get("acts_as", b.acts_as) == "service" or vals.get("channel", b.channel) != "discuss"))
                if steering:
                    steering.sudo().write({"published": False})
        before = {binding.id: binding._summary() for binding in self}
        result = super().write(vals)
        if any(name in vals for name in self.SERVICE_STEERING + ("published", "escalation_user_id")):
            for binding in self:
                if before[binding.id] != binding._summary():
                    binding._keep_revision(self.env._("Channel changed"), before=before[binding.id])
        return result

    def unlink(self):
        self.agent_id._check_my_companies()
        for binding in self:
            binding._keep_revision(self.env._("Channel removed"), before=binding._summary(), removed=True)
        return super().unlink()

    def _summary(self):
        self.ensure_one()
        return (f"channel: {self.channel}, acts as: {self.acts_as}, service user: {self.service_user_id.login or ''}, "
                f"models: {self.model_allowlist or ''}, published: {self.published}")

    def _keep_revision(self, what, added=False, before=None, removed=False):
        """The agent keeps what changed on its channels with its other changes"""
        Revision = self.env["nudge.agent.revision"].sudo()
        for binding in self:
            now = binding._summary()
            if removed:
                diff = f"@@ channel\n-{before}"
            elif added:
                diff = f"@@ channel\n+{now}"
            else:
                diff = f"@@ channel\n-{before}\n+{now}"
            Revision.create({"agent_id": binding.agent_id.id, "user_id": self.env.uid, "what": what, "diff": diff})


class NudgeAgentRevision(models.Model):
    """What changed in an agent, by whom and when, with the difference: kept, never edited."""
    _name = "nudge.agent.revision"
    _description = "Nudge Agent Change"
    _order = "id desc"

    agent_id = fields.Many2one("nudge.agent", required=True, ondelete="cascade", index=True)
    user_id = fields.Many2one("res.users", string="By", required=True, readonly=True)
    date = fields.Datetime(default=fields.Datetime.now, required=True, readonly=True)
    what = fields.Char(string="Changed", readonly=True)
    # What it names is held in every company of the agent: the stored difference is read in sudo, or by a system
    # administrator; diff_shown gives it to those who work in all of them
    diff = fields.Text(string="Difference", readonly=True, groups="base.group_system")
    diff_shown = fields.Text(string="Difference", compute="_compute_diff_shown")

    @api.depends_context("uid")
    def _compute_diff_shown(self):
        mine = self.env.user.company_ids
        for revision in self:
            # sudo: the stored difference and the agent's companies; given below only to whom they all belong
            record = revision.sudo()
            if self.env.su or record.agent_id.company_ids <= mine:
                revision.diff_shown = record.diff
            else:
                revision.diff_shown = self.env._("Shown only to those who work in every company of the agent.")

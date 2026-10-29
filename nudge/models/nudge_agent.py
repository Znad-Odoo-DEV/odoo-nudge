"""Nudge Agents: a named helper a manager makes for one kind of work (a sales assistant, an accounting expert).

An agent never adds a right. Whatever it reads or does, it reads and does as the person who asks, in the
companies that person works in and the agent is made for, with the tools the person's own groups allow
(``_tools_for``), and never a developer tool. The record rules hide an agent from the people outside its
audience and its companies. What it says and how it is limited is written here; the engine that runs a turn
is ``nudge_agent_engine.py``, the screens ``nudge_agent_ui.py``.
"""
import datetime
import logging
import re

import pytz

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import plaintext2html

from ..tools import base, skill_router, skills as skill_rules

_logger = logging.getLogger(__name__)

# What a change of the agent is kept for, with its difference
TRACKED = ("name", "instructions", "tool_keys", "skill_ids", "company_ids", "group_ids", "user_ids", "provider_id",
           "state", "active", "mask_personal_data", "use_memory", "use_docs", "budget_month", "budget_user_day",
           "off_limits")
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
DENIED_MODEL_PREFIXES = ("ir.", "payment.", "account.", "mail.", "discuss.", "nudge.", "res.users.", "hr.",
                         "calendar.", "website.visitor", "product.pricelist")
DENIED_MODELS = frozenset({"res.users", "res.groups", "res.partner", "res.partner.bank", "res.users.apikeys",
                           "res.company", "hr.employee", "hr.version", "hr.contract", "hr.payslip", "hr.leave"})
# What a change of these takes the non-Discuss channels of an agent back to unpublished
CHANNEL_STEERING = ("instructions", "tool_keys", "skill_ids", "company_ids", "use_docs", "use_memory", "provider_id",
                    "mask_personal_data", "off_limits", "budget_month", "budget_user_day", "role", "greeting", "name",
                    "lang_ids")
# Tools that are for the one who builds Odoo, whatever else their group says
DEVELOPER_ONLY = base.DEV_TOOLS - base.AGENT_TOOLS - base.TALK_TOOLS
# Tools that widen what is offered (an agent works with the list it was given), and a scheduled task, which runs
# later as plain Nudge, outside the agent (v1): never offered, and refused by tools.base.agent_allows
NEVER_OFFERED = base.AGENT_NEVER_TOOLS
# The state and the score of an agent: set by its buttons and its evaluation only
GATE_FIELDS = ("state", "score")
# Nudge Agents 60.1: the tools a channel gives (hand_to_person, when it names a person): never named by the agent
CHANNEL_TOOLS = frozenset({base.HAND_OVER_TOOL})
# The days of a channel's hours, as written in its "days" (mon-fri, sun-thu, mon,wed,fri)
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
HOURS_RANGE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)\s*-\s*([01]?\d|2[0-4]):([0-5]\d)$")
# What a hand-over carries at most: the summary, the name and the address of the visitor
HAND_OVER_CHARS = 4000
VISITOR_CHARS = 120


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
    # Nudge Agents 60.1: the month (YYYY-MM) its managers were last told the budget was used up: once a month
    budget_noticed = fields.Char(readonly=True, copy=False)
    off_limits = fields.Text(
        string="Off-limits Topics",
        help="One topic per line. A question about one of them is declined with a fixed sentence, without AI.")
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
        # Nudge Agents 60.1: the service user a published service channel of the agent names is its audience there
        # (and only through that channel: nudge.conversation._agent_scope)
        if not manager and user not in agent.user_ids and not (user_groups(user) & agent.group_ids) and not (
                agent.binding_ids.filtered(lambda binding: binding._public_open_to(user))):
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
        # A channel tool (hand_to_person) is given by the channel, never by the agent's list (_agent_scope)
        return sorted(
            name for name in allowed & set(offered)
            if name not in NEVER_OFFERED and name not in CHANNEL_TOOLS and not developer_tool(offered[name]))

    # ----------------------------------------------------------- guardrails (60.1)
    def _off_limits_hit(self, text):
        """:return: whether ``text`` asks about one of the agent's off-limits topics: every word of a line of
        ``off_limits`` is among the words of the question, folded and stemmed as the skill router reads them
        (Arabic particles and endings, English plurals). Asked before any query and any AI"""
        self.ensure_one()
        # sudo: reads the agent's own configuration
        lines = [line for line in (self.sudo().off_limits or "").splitlines() if line.strip()]
        if not lines or not (text or "").strip():
            return False
        asked = set(skill_router.tokens_of(text))
        for line in lines:
            words = set(skill_router.tokens_of(line))
            if words and words <= asked:
                return True
        return False

    def _off_limits_text(self):
        return self.env._("This is a topic I do not answer here. Please ask a person of the company.")

    # ----------------------------------------------------------- front desk (60.1)
    @api.model
    def _front_desk(self, text, bindings):
        """Nudge Agents 60.1, for the channels (website, WhatsApp, Telegram) that have several published bindings:
        the one whose agent fits the question best, chosen without AI by the skill router over each agent's name,
        role, instructions and the trigger words of its skills; when none scores, the binding flagged
        ``front_desk_default``.

        Choosing never opens anything: the caller still answers through ``_answer`` (a person, refused unless
        ``_can_use``) or ``_answer_public`` (a visitor), which check everything again.

        :param text: the question
        :param bindings: ``nudge.agent.binding`` records of one channel (the caller's environment, sudo or not)
        :return: one binding, in the caller's environment, or an empty recordset"""
        Binding = self.env["nudge.agent.binding"]
        # sudo: the configuration of the channels and of their agents, nothing of the data
        candidates = bindings.sudo().filtered(
            lambda binding: binding.published and binding.agent_id.active and binding.agent_id.state == "published")
        if len(candidates) <= 1:
            return Binding.browse(candidates.ids)
        # The words of each role are trigger words of its agent, but those every role has ("assistant") tell none
        # apart
        roles = {binding.id: set(skill_router.words_of(binding.agent_id.role or "")) for binding in candidates}
        shared = set.intersection(*roles.values())
        rows = []
        for binding in candidates:
            agent = binding.agent_id
            skills = agent.skill_ids.filtered(lambda skill: skill.state == "published")
            rows.append({
                "key": f"nudge-front-desk-{binding.id}", "name": agent.name or "", "summary": agent.role or "",
                "instructions": agent.instructions or "", "model_names": "",
                "trigger_words": "\n".join([*(skill.trigger_words or "" for skill in skills),
                                            *sorted(roles[binding.id] - shared)])})
        index = skill_router.Index(rows)
        choice = skill_router.route(index, text or "", limit=1, layers=("rules", "lexical"))
        ranked = choice.ranked
        best, second = (ranked[0] if ranked else (None, 0.0)), (ranked[1][1] if len(ranked) > 1 else 0.0)
        # The best one, when it leads clearly: above the threshold of the router, or at least above the floor of
        # its index; two agents that score the same leave it to the default
        if best[0] and best[1] > second and (choice.keys or best[1] >= skill_router.INDEX_FLOOR):
            return Binding.browse(int(best[0].rsplit("-", 1)[1]))
        return Binding.browse(candidates.filtered("front_desk_default")[:1].ids)

    # ----------------------------------------------------------- hand to a person (60.1)
    def _hand_over_problem(self, binding, consent=False, public=False):
        """:return: why the conversation cannot be handed to a person on ``binding`` (a text the model or the
        person reads), or None: the channel must name an active escalation user, a visitor without an account must
        have agreed to be contacted (``consent``), and the activities of Odoo must be there"""
        # sudo: the configuration of the channel
        binding = binding.sudo()
        if len(binding) != 1 or (self and binding.agent_id.id not in self.ids):
            return self.env._("Nothing was handed over: this channel is not one of this agent.")
        escalation = binding.escalation_user_id
        if not escalation or not escalation.active:
            return self.env._("Nothing was handed over: no person is named to take over on this channel.")
        if public and not consent:
            return self.env._(
                "Nothing was handed over: the visitor has not agreed to be contacted. Ask them first; on the website "
                "they agree with Talk to a person.")
        if "mail.activity" not in self.env:
            return self.env._("Nothing was handed over: this database has no activities to give a person.")
        return None

    def _hand_to_person(self, binding, summary, consent=False, visitor_name=None, visitor_email=None, public=False,
                        lead=None):
        """Nudge Agents 60.1: hand a conversation to the escalation user of ``binding``: a To Do activity for them,
        on a new lead when CRM is installed, else on the contact of the person (or, for a visitor without an
        account, on the agent's own contact), with the summary and how to reach the visitor. Used by the tool
        ``hand_to_person`` and by the "Talk to a person" form of the website.

        Nothing is read for the visitor: the activity and its lead are written in sudo (the visitor and the service
        user may write nothing), with only what the visitor and the agent said, for the escalation user to read.

        :param binding: the ``nudge.agent.binding`` the conversation came through; it must belong to this agent
            (when ``self`` is not empty) and name an escalation user
        :param summary: what the person needs, in words (cut at 4000 characters)
        :param consent: a visitor without an account agreed to be contacted: required when ``public``
        :param visitor_name: their name, when they gave it
        :param visitor_email: their address (or number), when they gave it
        :param public: the one asking has no account (a website visitor, an unknown number); else the current
            user is the person (their contact is used). A public or service user is always taken as public
        :param lead: the ``crm.lead`` the channel made for the same request (the contact form of the website): the
            activity goes on it, and no second lead is made
        :return: the ``mail.activity`` made (truthy), or False when nothing was handed over
            (``_hand_over_problem`` says why)"""
        # sudo: the configuration of the channel
        binding = binding.sudo().exists() if binding else self.env["nudge.agent.binding"].sudo()
        user = self.env.user
        public = bool(public) or user._is_public() or user.has_group(base.SERVICE_GROUP)
        problem = self._hand_over_problem(binding, consent=consent, public=public)
        if problem:
            _logger.info("Nudge Agents: no hand-over on channel %s: %s", binding.ids, problem)
            return False
        agent = binding.agent_id
        escalation = binding.escalation_user_id
        person = self.env["res.users"] if public else user
        name = " ".join(str(visitor_name or "").split())[:VISITOR_CHARS]
        email = " ".join(str(visitor_email or "").split())[:VISITOR_CHARS]
        text = str(summary or "").strip()[:HAND_OVER_CHARS]
        lines = [text]
        if person:
            lines.append(self.env._("Asked by %s.", person.name))
        if name:
            lines.append(self.env._("Name: %s", name))
        if email:
            lines.append(self.env._("Contact: %s", email))
        channel = dict(binding._fields["channel"]._description_selection(self.env)).get(binding.channel)
        lines.append(self.env._("Channel: %(channel)s, agent %(agent)s.", channel=channel or binding.channel,
                                agent=agent.name))
        title = self.env._("%s: a person is asked for", agent.name)
        note = plaintext2html("\n".join(lines))
        company = escalation.company_id if escalation.company_id in agent.company_ids else agent.company_ids[:1]
        if lead is not None and "crm.lead" in self.env and lead._name == "crm.lead" and lead.exists():
            # sudo: the lead the website made for this request a moment ago, for the escalation user
            target = lead.sudo()
        elif "crm.lead" in self.env:
            # sudo: written for the escalation user with what was said; nothing is read back for the visitor
            target = self.env["crm.lead"].sudo().with_company(company).create({
                "name": title, "contact_name": name or False, "email_from": email if "@" in email else False,
                "partner_id": person.partner_id.id if person else False, "user_id": escalation.id,
                "description": note, "company_id": company.id})
        elif person:
            # sudo: the activity is the hand-over itself, for the escalation user
            target = person.partner_id.sudo()
        else:
            # A visitor: a contact of their own, archived (out of the lists of contacts), in the company, never the
            # agent's face, whose activities everyone who talks to the agent sees
            target = self.env["res.partner"].sudo().create({
                "name": name or self.env._("Visitor of %s", agent.name), "email": email if "@" in email else False,
                "company_id": company.id, "active": False})
        return target.activity_schedule(
            "mail.mail_activity_data_todo", summary=title, note=note, user_id=escalation.id) or False

    # ----------------------------------------------------------- budget notice (60.1)
    def _budget_notice(self):
        """Tell the creator of the agent (or else the Nudge managers of its companies), once a month, that it used
        its budget of the month: a To Do activity on the agent's contact, its face (nudge.agent has no chatter:
        Nudge does not depend on mail). Never raises.
        :return: whether the notice was given now"""
        self.ensure_one()
        month = fields.Date.context_today(self).strftime("%Y-%m")
        try:
            with self.env.cr.savepoint():
                # Once a month, whoever passes the budget first: one atomic update says who
                self.flush_recordset(["budget_noticed"])
                self.env.cr.execute(
                    "UPDATE nudge_agent SET budget_noticed = %s WHERE id = %s AND "
                    "(budget_noticed IS NULL OR budget_noticed != %s) RETURNING id", [month, self.id, month])
                if not self.env.cr.fetchone():
                    return False
                self.invalidate_recordset(["budget_noticed"])
                if "mail.activity" not in self.env:
                    return True
                # sudo: the agent's own configuration and the managers of its companies
                agent = self.sudo()
                manager = self.env.ref("nudge.group_nudge_manager").sudo()
                members = manager[("user_ids" if "user_ids" in manager._fields else "users")]
                users = agent.create_uid.filtered(lambda user: user.active and user in members)
                if not users:
                    users = members.filtered(
                        lambda user: user.active and not user.share and user.company_ids & agent.company_ids)[:10]
                title = self.env._("%(agent)s used its budget for %(month)s", agent=agent.name, month=month)
                note = plaintext2html(self.env._(
                    "Until the next month the agent answers without AI: approved answers and the requests it "
                    "understands by itself. Raise its monthly budget on the agent to give it more."))
                for user in users:
                    agent.partner_id.activity_schedule(
                        "mail.mail_activity_data_todo", summary=title, note=note, user_id=user.id)
        except Exception:  # noqa: BLE001 - a notice never stops an answer
            _logger.warning("Nudge Agents: the budget notice of agent %s failed", self.id, exc_info=True)
            return False
        return True

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

    def update_field_translations(self, field_name, translations, *args, **kwargs):
        # The name, role and greeting are translated fields: a translation is a change of what the channels say
        if not self.env.su and field_name in CHANNEL_STEERING:
            self._check_my_companies()
            if not self.env.user.has_group("base.group_system"):
                self._unpublish_channels(self.env._("Its instructions or limits changed"))
        return super().update_field_translations(field_name, translations, *args, **kwargs)

    @api.constrains("budget_month", "budget_user_day")
    def _check_public_budgets(self):
        # H1: a public channel answers people without an account: never without a limit to what it spends
        for agent in self:
            if (agent.budget_month <= 0 or agent.budget_user_day <= 0) and agent.binding_ids.filtered(
                    lambda binding: binding.published and binding.acts_as == "service"):
                raise ValidationError(self.env._(
                    "An agent with a published service channel needs a monthly budget and tokens per person per "
                    "day. Unpublish its service channels first."))

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
        "res.users", string="Service User", domain="[('share', '=', True)]",
        help="A portal user holding the group Nudge: agent service user and no other group of Nudge: the record "
             "rules and field groups of the portal apply to what it reads.")
    model_allowlist = fields.Char(
        string="Readable Models", help="Technical names of the models a service user may read, separated by commas.")
    escalation_user_id = fields.Many2one(
        "res.users", string="Escalate To", domain="[('share', '=', False)]",
        help="The person a conversation is handed to when someone asks for a person: a To Do for them, on a lead "
             "when CRM is installed. A visitor without an account is handed over only once they agree.")
    published = fields.Boolean(
        help="On a service channel only a system administrator publishes it.")
    # Nudge Agents 60.1: when the channel answers, in the time zone of the company; outside, a fixed sentence
    hours = fields.Char(
        string="Office Hours", help="When it answers, such as 08:00-18:00, or 08:00-12:00, 16:00-20:00. Empty: always. "
                                    "In the time zone of the company.")
    days = fields.Char(
        string="Office Days", help="The days it answers, such as sun-thu, mon-fri or mon,wed,fri. Empty: every day.")
    front_desk_default = fields.Boolean(
        string="Front Desk Default",
        help="When a channel has several agents and none fits the question, this one answers.")

    # Fields that decide what a published service binding may do: changing one takes it back to unpublished
    SERVICE_STEERING = ("channel", "acts_as", "service_user_id", "model_allowlist", "agent_id", "escalation_user_id",
                        "front_desk_default", "hours", "days")
    # Fields whose change is kept in the agent's history with the others
    KEPT = SERVICE_STEERING + ("published",)

    # ------------------------------------------------------------------ office hours (60.1)
    @api.constrains("hours", "days")
    def _check_hours(self):
        for binding in self:
            binding._ranges()
            binding._day_numbers()

    def _ranges(self):
        """:return: the office hours as ``[(start, end)]`` in minutes of the day; an end before its start runs
        past midnight. :raise ValidationError: when they cannot be read"""
        ranges = []
        for part in (self.hours or "").split(","):
            if not part.strip():
                continue
            match = HOURS_RANGE.match(part.strip())
            if not match or int(match.group(3)) * 60 + int(match.group(4)) > 24 * 60:
                raise ValidationError(self.env._("Write the office hours as 08:00-18:00, several separated by commas."))
            start = int(match.group(1)) * 60 + int(match.group(2))
            end = int(match.group(3)) * 60 + int(match.group(4))
            if start == end:
                raise ValidationError(self.env._("Office hours that start and end at the same time are empty."))
            ranges.append((start, end))
        return ranges

    def _day_numbers(self):
        """:return: the days it answers, Monday 0 to Sunday 6. :raise ValidationError: when they cannot be read"""
        days = set()
        for part in (self.days or "").lower().replace(" ", "").split(","):
            if not part:
                continue
            ends = part.split("-")
            if len(ends) > 2 or any(end[:3] not in DAYS for end in ends):
                raise ValidationError(self.env._("Write the office days as mon-fri, sun-thu or mon,wed,fri."))
            first, last = DAYS.index(ends[0][:3]), DAYS.index(ends[-1][:3])
            day = first
            days.add(day)
            while day != last:
                day = (day + 1) % 7
                days.add(day)
        return days or set(range(7))

    def _timezone(self):
        """:return: the time zone of the company of the channel: its service user's company, else the agent's"""
        company = self.service_user_id.company_id if self.acts_as == "service" else self.env["res.company"]
        company = company or self.agent_id.company_ids[:1]
        name = company.partner_id.tz or self.service_user_id.tz or "UTC"
        try:
            return pytz.timezone(name)
        except pytz.UnknownTimeZoneError:
            return pytz.utc

    def _open_now(self, when=None):
        """:param when: a UTC datetime, now by default
        :return: whether the channel answers at ``when``: on one of its days, within its hours (a range past
            midnight belongs to the day it started)"""
        self.ensure_one()
        binding = self.sudo()
        ranges, days = binding._ranges(), binding._day_numbers()
        local = pytz.utc.localize(when or fields.Datetime.now()).astimezone(binding._timezone())
        if not ranges:
            return local.weekday() in days
        minute = local.hour * 60 + local.minute
        yesterday = (local - datetime.timedelta(days=1)).weekday()
        for start, end in ranges:
            if start < end and start <= minute < end and local.weekday() in days:
                return True
            if start > end and ((minute >= start and local.weekday() in days) or (minute < end and yesterday in days)):
                return True
        return False

    def _days_text(self):
        """:return: the office days written in the language of the environment (``sun-thu`` as the Arabic names
        of Sunday and Thursday), '' when the channel has no days. The text as written when the day names of the
        language cannot be had."""
        self.ensure_one()
        raw = (self.sudo().days or "").strip()
        if not raw:
            return ""
        try:
            from odoo.tools.misc import babel_locale_parse
            names = babel_locale_parse(self.env.lang or "en_US").days["format"]["wide"]
        except Exception:  # noqa: BLE001 - day names are a courtesy; the days as written still say it
            return raw
        shown = []
        for part in raw.lower().replace(" ", "").split(","):
            ends = [DAYS.index(end[:3]) for end in part.split("-") if end[:3] in DAYS]
            if not ends or len(ends) != len(part.split("-")):
                return raw
            if len(ends) == 1:
                shown.append(names[ends[0]])
            else:
                shown.append(self.env._("%(first)s to %(last)s", first=names[ends[0]], last=names[ends[-1]]))
        return self.env._(", ").join(shown)

    def _hours_text(self):
        """:return: the fixed sentence of a channel asked outside its hours, without AI"""
        binding = self.sudo()
        when = binding.hours or self.env._("all day")
        days = binding._days_text() or self.env._("every day")
        return self.env._(
            "We answer here on %(days)s, %(hours)s (time of %(zone)s). Please write again then.",
            days=days, hours=when, zone=binding._timezone().zone)

    # ------------------------------------------------------------------ a public channel (60.1)
    def _public_open_to(self, user):
        """:return: whether this is a published service channel that answers as ``user``, its service user, who
        still holds only the service group. Asked at every turn"""
        self.ensure_one()
        binding = self.sudo()
        return bool(binding.published and binding.acts_as == "service" and binding.channel in SERVICE_CHANNELS
                    and user and binding.service_user_id.id == user.id and binding.service_user_id.active
                    and binding._service_user_ok())

    def _allowlist(self):
        """:return: the models a public channel reads, a tuple; never one of those a service channel never reads,
        whatever was stored (a list checked again at every turn)"""
        names = skill_rules.split_names(self.sudo().model_allowlist)
        return tuple(sorted({
            name for name in names
            if name in self.env and name not in DENIED_MODELS and not name.startswith(DENIED_MODEL_PREFIXES)}))

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
        if not user.sudo().share:
            # H2: the record rules and field groups of the portal (unpublished products, cost prices, stock) apply
            # to a portal user, not to an employee
            return self.env._(
                "The service user %s must be a portal user: an employee reads what visitors may not.", user.name)
        basic = self.env.ref("base.group_portal") | self.env.ref("base.group_public")
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

    @api.constrains("escalation_user_id")
    def _check_escalation(self):
        for binding in self:
            if binding.escalation_user_id.sudo().share:
                raise ValidationError(self.env._("A conversation is handed to an employee, not to a portal user."))

    @api.constrains("published", "acts_as", "agent_id")
    def _check_public_budget(self):
        # H1: never a public channel without a limit to what it spends
        for binding in self:
            agent = binding.agent_id.sudo()
            if binding.published and binding.acts_as == "service" and (
                    agent.budget_month <= 0 or agent.budget_user_day <= 0):
                raise ValidationError(self.env._(
                    "Give the agent a monthly budget and tokens per person per day before publishing a service "
                    "channel: visitors are counted one by one."))

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
        bindings._memory_off_for_service()
        return bindings

    def _memory_off_for_service(self):
        """L5: the approved answers were written for the people of the company: an agent given a service channel
        stops using them (a system administrator may turn them on again, which takes its channels down for a
        manager)"""
        # sudo: a setting of the agent the channel belongs to
        agents = self.filtered(lambda binding: binding.acts_as == "service").agent_id.sudo().filtered("use_memory")
        if agents:
            agents.write({"use_memory": False})

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
        if vals.get("acts_as") == "service":
            self._memory_off_for_service()
        if any(name in vals for name in self.KEPT):
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
        summary = (f"channel: {self.channel}, acts as: {self.acts_as}, service user: {self.service_user_id.login or ''}, "
                   f"models: {self.model_allowlist or ''}, published: {self.published}")
        # Nudge Agents 60.1: who takes over, and when it answers
        return (f"{summary}, escalate to: {self.escalation_user_id.login or ''}, hours: {self.hours or ''}, "
                f"days: {self.days or ''}, front desk default: {self.front_desk_default}")

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

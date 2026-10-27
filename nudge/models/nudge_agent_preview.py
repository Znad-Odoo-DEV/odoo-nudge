"""Access preview of an agent: what a given person would have with it, before it is published. Everything is
read as that person, within the companies they and the agent share, so their record rules apply."""
from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from ..tools import base, skills as skill_rules
from .nudge_agent import DEFAULT_READ_TOOLS, READ_KINDS, check_access

SAMPLE = 5
MAX_MODELS = 3
# Models looked at when the skills of the agent name none
FALLBACK_MODELS = ("res.partner", "product.template", "sale.order")
RECORD_TOOLS = ("search_records", "read_record", "find_record")


class NudgeAgentPreview(models.TransientModel):
    _name = "nudge.agent.preview"
    _description = "Nudge Agent Access Preview"

    agent_id = fields.Many2one("nudge.agent", required=True, ondelete="cascade")
    user_id = fields.Many2one(
        "res.users", string="Person", required=True, default=lambda self: self.env.user,
        domain="[('share', '=', False)]")
    can_use = fields.Boolean(string="May Use It", compute="_compute_preview")
    preview = fields.Html(compute="_compute_preview", sanitize=False)

    def _check_caller(self):
        """Who looks is a Nudge manager (or Nudge's own code): the screen reads another person's data.
        The ACL of the model is not checked by an onchange, so it is checked here"""
        if not self.env.su and not self.env.user.has_group("nudge.group_nudge_manager"):
            raise AccessError(_("Only Nudge managers preview the access of an agent."))

    @api.model
    def default_get(self, fields_list):
        self._check_caller()
        return super().default_get(fields_list)

    @api.model_create_multi
    def create(self, vals_list):
        self._check_caller()
        return super().create(vals_list)

    @api.depends("agent_id", "user_id")
    def _compute_preview(self):
        for wizard in self:
            if not wizard.agent_id or not wizard.user_id:
                wizard.can_use = False
                wizard.preview = False
                continue
            wizard.can_use, wizard.preview = wizard._build()

    def _models_read(self, agent, tools):
        """:return: up to three model names whose records the tools of the agent read: those of its skills, else usual ones"""
        if not set(tools) & set(RECORD_TOOLS):
            return []
        names = []
        # sudo: reads the agent's own configuration
        for skill in agent.sudo().skill_ids:
            names += skill_rules.split_names(skill.model_names)
        names += list(FALLBACK_MODELS)
        found = []
        for name in names:
            if name in self.env and name not in found:
                found.append(name)
        return found[:MAX_MODELS]

    @staticmethod
    def _check_read(model):
        check_access(model, "read")

    def _build(self):
        self.ensure_one()
        self._check_caller()
        agent, user = self.agent_id, self.user_id
        caller = self.env.user
        # The superuser and OdooBot are never previewed, nor a system administrator by anyone who is not one
        if user._is_superuser() or user.id == self.env.ref("base.user_root").id:
            raise AccessError(_("This person cannot be previewed."))
        if (not self.env.su and user.has_group("base.group_system") and not caller.has_group("base.group_system")):
            raise AccessError(_("A system administrator is previewed only by a system administrator."))
        # sudo: reads the agent's own configuration; the person's data is read below as the person
        config = agent.sudo()
        allowed = agent._can_use(user, ignore_state=True)
        companies = agent._companies_for(user)
        if not self.env.su:
            # Only what the caller works in is shown
            companies &= caller.company_ids
        parts = [Markup("<h4>%s</h4>") % config.name]
        answer = _("yes") if allowed else _("no (not in its audience, or no company in common)")
        parts.append(Markup("<p><b>%s</b> %s</p>") % (_("May use it:"), answer))
        parts.append(Markup("<p><b>%s</b> %s</p>") % (
            _("Companies:"), ", ".join(companies.mapped("name")) or _("none")))
        talk = set(agent._tools_for(user, base.MODE_TALK))
        in_agent_mode = set(agent._tools_for(user, base.MODE_AGENT))
        lines = []
        for name in sorted(talk | in_agent_mode):
            risk = (_("read") if name in DEFAULT_READ_TOOLS and base.TOOLS[name].kind in READ_KINDS
                    else _("write, on a card"))
            lines.append(Markup("<li>%s <i>(%s)</i></li>") % (name, risk))
        parts.append(Markup("<p><b>%s</b></p><ul>%s</ul>") % (
            _("Tools:"), Markup("").join(lines) or Markup("<li>-</li>")))
        skills = config.skill_ids.mapped("name") or [_("Nudge chooses among the published skills")]
        parts.append(Markup("<p><b>%s</b> %s</p>") % (_("Skills:"), ", ".join(skills)))
        if allowed and companies:
            person = self.env(user=user.id, context=dict(self.env.context, allowed_company_ids=companies.ids))
            looker = self.env(context=dict(self.env.context, allowed_company_ids=companies.ids))
            sample = []
            for model_name in self._models_read(agent, talk | in_agent_mode):
                try:
                    # The target must be allowed to read the model (never sudo)...
                    self._check_read(person[model_name])
                    records = person[model_name].search([], limit=SAMPLE)
                    # ...and only what the caller can ALSO read is shown
                    if not self.env.su:
                        self._check_read(looker[model_name])
                        records = looker[model_name].search([("id", "in", records.ids)])
                    names = ", ".join(records.mapped("display_name")) or _("nothing visible")
                except AccessError:
                    names = _("not readable by this person")
                sample.append(Markup("<li>%s: %s</li>") % (model_name, names))
            if sample:
                parts.append(Markup("<p><b>%s</b></p><ul>%s</ul>") % (
                    _("What it would read for this person:"), Markup("").join(sample)))
        return allowed, Markup("").join(parts)

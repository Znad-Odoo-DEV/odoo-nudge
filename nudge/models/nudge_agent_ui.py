"""Nudge Agents: what the screens of an agent call (Try it, the evaluation, the access preview)."""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..tools import base, registry
from .nudge_agent import DEFAULT_READ_TOOLS, NEVER_OFFERED, READ_KINDS, developer_tool

# Tools the allow-list never shows: they work inside a tool, not for a person, or an agent is never given them
INTERNAL_TOOLS = frozenset({"more_tools", "use_skill"}) | NEVER_OFFERED
CHANNELS = {"discuss": "Discuss", "website": "Website", "whatsapp": "WhatsApp", "telegram": "Telegram", "mcp": "MCP"}


class NudgeAgentUI(models.Model):
    _inherit = "nudge.agent"

    channels_label = fields.Char(string="Channels", compute="_compute_channels_label")
    has_service_binding = fields.Boolean(compute="_compute_channels_label")
    eval_count = fields.Integer(string="Evaluations", compute="_compute_eval_count")

    @api.depends("binding_ids.channel", "binding_ids.acts_as", "binding_ids.published")
    def _compute_channels_label(self):
        for agent in self:
            names = []
            for binding in agent.binding_ids:
                label = CHANNELS.get(binding.channel, binding.channel or "")
                names.append(label if binding.published else f"{label} ({_('draft')})")
            agent.channels_label = ", ".join(dict.fromkeys(names))
            agent.has_service_binding = any(b.acts_as == "service" for b in agent.binding_ids)

    @api.depends("eval_ids")
    def _compute_eval_count(self):
        for agent in self:
            agent.eval_count = len(agent.eval_ids)

    # ------------------------------------------------------------------
    # The tools of the allow-list
    # ------------------------------------------------------------------

    @api.model
    def tool_catalogue(self):
        """:return: the tools an agent may be given, for the allow-list widget: ``name``, ``label``,
        ``help`` and ``risk`` ("read", or "write" for a tool that changes data only on a card the person
        confirms). Developer tools are never listed: an agent cannot be given one."""
        found = []
        for name, tool in sorted(registry.tools_here(self.env).items()):
            modes = tuple(tool.modes or ())
            # The same test as the one that gives an agent its tools (_tools_for): a developer tool is not listed
            if name in INTERNAL_TOOLS or developer_tool(tool) or not (
                    {base.MODE_TALK, base.MODE_AGENT} & set(modes) or name in base.AGENT_TOOLS):
                continue
            text = " ".join((tool.description or "").split())
            found.append({
                "name": name,
                "label": name.replace("_", " ").capitalize(),
                "help": text[:160] + ("..." if len(text) > 160 else ""),
                "risk": "read" if (name in DEFAULT_READ_TOOLS and not isinstance(tool, base.Action)
                                   and getattr(tool, "kind", None) in READ_KINDS) else "write",
            })
        found.sort(key=lambda item: (item["risk"] != "read", item["name"]))
        return found

    # ------------------------------------------------------------------
    # Buttons
    # ------------------------------------------------------------------

    def action_try_it(self):
        """Open the Nudge panel for this agent, in rehearsal: the panel passes ``nudge_agent_id`` and
        ``nudge_rehearsal`` with every question (see static/src/agent_persona/)."""
        self.ensure_one()
        return {
            "type": "ir.actions.client",
            "tag": "nudge_try_agent",
            "params": {"agent_id": self.id, "agent_name": self.name, "rehearsal": True},
        }

    def action_access_preview(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Access preview"),
            "res_model": "nudge.agent.preview",
            "view_mode": "form",
            "target": "new",
            "context": {"default_agent_id": self.id, "default_user_id": self.env.user.id},
        }

    def action_open_conversations(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Conversations of %s", self.name),
            "res_model": "nudge.conversation",
            "view_mode": "list,form",
            "domain": [("agent_id", "=", self.id)],
            "context": {"default_agent_id": self.id},
        }

    def action_run_evaluation(self):
        """Ask every evaluation question in rehearsal (nothing is written, nothing billed as real),
        record the answer and whether it passed, and set the score. All passed: the agent is Tested. The answers
        are given with the rights of the one who runs it: shown to them alone (``answer_shown``)."""
        # Only by someone who works in every company of the agent: the score and the state are written below
        self._check_my_companies()
        for agent in self:
            if not agent.eval_ids:
                raise UserError(_("Add at least one evaluation question first."))
            for evaluation in agent.eval_ids:
                try:
                    result = agent._answer(evaluation.question, evaluation.lang or None, rehearsal=True)
                    text, source = result.get("text") or "", result.get("source") or ""
                except Exception as error:  # noqa: BLE001 - a question that fails is a failed question
                    text, source = str(error), "refused"
                # sudo: only the runner writes a result, so the publish gate cannot be forged by hand
                evaluation.sudo().write({
                    "last_answer": text,
                    "last_source": source,
                    "passed": self._eval_passes(evaluation, text, source),
                    "run_date": fields.Datetime.now(),
                    "run_by": self.env.uid,
                })
            passed = len(agent.eval_ids.filtered("passed"))
            vals = {"score": round(100.0 * passed / len(agent.eval_ids), 1)}
            if passed == len(agent.eval_ids) and agent.state == "draft":
                vals["state"] = "tested"
            # sudo: the score and the state change only here and by the buttons
            agent.sudo().write(vals)
        return True

    @api.model
    def _eval_passes(self, evaluation, text, source):
        if evaluation.expected == "contains":
            return bool(evaluation.expected_text) and evaluation.expected_text.lower() in text.lower()
        if evaluation.expected == "no_ai":
            return source != "ai"
        return bool(text.strip()) and source != "refused"

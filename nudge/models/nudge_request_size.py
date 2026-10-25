import importlib.util
import json
import logging
import os

from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError

from ..llm import summary
from ..tools.base import MODE_AGENT, MODE_DEV, MODE_TALK

_logger = logging.getLogger(__name__)

# The limits of the quality gate, used when the tests are not shipped with the module
FALLBACK_LIMITS = {MODE_TALK: 17800, MODE_AGENT: 24500, MODE_DEV: 26000}
SAMPLE_QUESTION = "Show me my open sales orders."


class Measured(Exception):
    """Raised to leave the savepoint of a measure: nothing it made is kept."""


def gate_limits():
    """:return: the most tokens a request may take in each mode, as the quality gate says
    (``tests/scenarios/spec.py``, read from the file: the tests package is not imported)"""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "scenarios", "spec.py")
    try:
        spec = importlib.util.spec_from_file_location("nudge_gate_spec", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return {mode: int(module.DEFAULT_BUDGET[mode]) for mode in FALLBACK_LIMITS}
    except Exception:  # noqa: BLE001 - the limits are a help, never a reason for the screen to fail
        return dict(FALLBACK_LIMITS)


def request_size(system, turns, tools):
    """:return: about how many tokens one request takes, counted as ``request_size`` of
    ``tests/test_quality_gate.py`` counts it"""
    return summary.estimate_tokens(json.dumps([system, turns, tools], ensure_ascii=False, default=str))


class NudgeRequestSize(models.TransientModel):
    _name = "nudge.request.size"
    _description = "Nudge Request Size"

    user_id = fields.Many2one("res.users", string="User", default=lambda self: self.env.user, readonly=True)
    company_id = fields.Many2one("res.company", string="Company", default=lambda self: self.env.company,
                                 readonly=True)
    question = fields.Char(
        default=SAMPLE_QUESTION,
        help="The sentence the request is built for: the skills and the tools offered depend on it.")
    line_ids = fields.One2many("nudge.request.size.line", "wizard_id", string="Modes", readonly=True)

    def _check_manager(self):
        if not self.env.user.has_group("nudge.group_nudge_manager"):
            raise AccessError(self.env._("Only Nudge managers measure the size of a request."))

    def _measure(self, mode):
        """:return: the values of the line of ``mode``. Builds the request as the chat would, in a
        conversation that is thrown away: no provider is called, nothing is kept."""
        Conversation = self.env["nudge.conversation"]
        Provider = self.env["nudge.provider"].sudo()
        values = {"mode": mode, "limit": gate_limits()[mode], "state": "skipped"}
        if mode == MODE_AGENT and not Conversation._can_use_agent():
            values["note"] = self.env._("Agent mode is not open to this user.")
            return values
        if mode == MODE_DEV and not Conversation._can_develop():
            values["note"] = self.env._("Developer mode is not open to this user.")
            return values
        provider = Provider._get_for_mode(mode) or Provider._get_default()
        if not provider:
            values.update(state="error", note=self.env._("No AI provider is set up, so there is no request to measure."))
            return values
        found = {}
        try:
            with self.env.cr.savepoint():
                conversation = Conversation._chat_open()
                self.env["nudge.message"].sudo().create({
                    "conversation_id": conversation.id, "role": "user", "mode": mode,
                    "content": self.question or SAMPLE_QUESTION})
                job = conversation._chat_job(provider, mode, 1)
                found.update(system=job["system"], turns=job["turns"], tools=job["tools"] or [])
                raise Measured
        except Measured:
            pass
        except UserError as error:
            values.update(state="error", note=error.args[0] if error.args else str(error))
            return values
        except Exception as error:  # noqa: BLE001 - said on the screen, not a crash of it
            _logger.exception("Nudge: the request of the %s mode could not be built", mode)
            values.update(state="error", note=f"{type(error).__name__}: {str(error)[:200]}")
            return values
        finally:
            # What the savepoint took back is still in the cache
            self.env.invalidate_all()
        total = request_size(found["system"], found["turns"], found["tools"])
        values.update(
            state="over" if total > values["limit"] else "ok", total=total,
            system_tokens=summary.estimate_tokens(found["system"]),
            tools_tokens=summary.estimate_tokens(found["tools"]),
            turns_tokens=summary.estimate_tokens(found["turns"]),
            tool_count=len(found["tools"]),
            note=self.env._("%(provider)s, %(model)s", provider=provider.name or "", model=provider.model_name or ""))
        return values

    def action_measure(self):
        self.ensure_one()
        self._check_manager()
        rows = [self._measure(mode) for mode in (MODE_TALK, MODE_AGENT, MODE_DEV)]
        self.line_ids = [Command.clear()] + [Command.create(row) for row in rows]
        return {
            "type": "ir.actions.act_window", "name": self.env._("Request Size"),
            "res_model": self._name, "res_id": self.id, "view_mode": "form", "target": "current",
        }

    @api.model
    def action_open(self):
        self._check_manager()
        wizard = self.create({})
        wizard.action_measure()
        return {
            "type": "ir.actions.act_window", "name": self.env._("Request Size"),
            "res_model": self._name, "res_id": wizard.id, "view_mode": "form", "target": "current",
        }


class NudgeRequestSizeLine(models.TransientModel):
    _name = "nudge.request.size.line"
    _description = "Nudge Request Size, One Mode"
    _order = "id"

    wizard_id = fields.Many2one("nudge.request.size", required=True, ondelete="cascade")
    mode = fields.Selection([("talk", "Talk"), ("agent", "Agent"), ("dev", "Developer")], required=True)
    state = fields.Selection(
        [("ok", "Within the limit"), ("over", "Over the limit"), ("skipped", "Not open"), ("error", "Not measured")],
        string="Result")
    total = fields.Integer(string="Tokens")
    limit = fields.Integer(string="Limit")
    system_tokens = fields.Integer(string="Instructions and Skills")
    tools_tokens = fields.Integer(string="Tool Schemas")
    turns_tokens = fields.Integer(string="Conversation")
    tool_count = fields.Integer(string="Tools")
    note = fields.Char()

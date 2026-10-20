"""The AI over a selected list: what the panel offers, and the way in.

The work itself is the tool ``bulk_apply`` (tools/bulk.py); this hands it the
records the person selected in a list, through the pipeline of the agent: the
same card, the same rights, the same log and undo as when the AI asks."""
import types

from odoo import api, models
from odoo.exceptions import AccessError, UserError

from ..tools import base, plans
from ..tools.actions import writable_field
from ..tools.base import MODE_AGENT
from ..tools.bulk import FILL_TYPES, OPERATIONS, TEXT_TYPES
from ..tools.records import Refused, field_label, get_model


class NudgeBulk(models.AbstractModel):
    _name = "nudge.bulk"
    _description = "Nudge AI over a List"

    @api.model
    def _check_user(self):
        if not self.env.user.has_group("nudge.group_nudge_agent"):
            raise AccessError(self.env._("Nudge is not open to you."))

    @api.model
    def options(self, model_name):
        """:return: what the panel offers for the records selected in a list of
        ``model_name``: ``{"available", "reason", "limit", "classify", "text", "fill"}``,
        each a list of ``{"name", "label"}``"""
        self._check_user()
        answer = {"available": False, "reason": "", "limit": plans.max_records(self.env),
                  "classify": [], "text": [], "fill": []}
        try:
            model = get_model(self.env, str(model_name or ""), "write", to_change=True)
        except Refused:
            return answer
        if not self.env["nudge.noai"]._has_ai(None, MODE_AGENT):
            answer["reason"] = self.env._(
                "This needs an AI provider, which is not set up, or which private mode does not allow.")
            return answer
        for name, field in sorted(model._fields.items()):
            try:
                writable_field(model, name)
            except Refused:
                continue
            entry = {"name": name, "label": field_label(field, self.env)}
            if field.type in ("selection", "many2one", "many2many"):
                answer["classify"].append(entry)
            elif field.type in TEXT_TYPES:
                answer["text"].append(entry)
            if field.type in FILL_TYPES:
                answer["fill"].append(entry)
        answer["available"] = bool(answer["classify"] or answer["text"] or answer["fill"])
        return answer

    @api.model
    def _start(self, conversation, arguments, ids=None, domain=None):
        """Put the work before the person as a card, without asking the AI to
        decide anything: it is the person who said what.

        :return: the job of the stream"""
        self._check_user()
        if arguments.get("operation") not in OPERATIONS:
            raise UserError(self.env._("Say what to do."))
        try:
            model = get_model(self.env, str(arguments.get("model") or ""), "write", to_change=True)
        except Refused as refusal:
            raise UserError(str(refusal)) from refusal
        limit = plans.max_records(self.env)
        if ids:
            ids = [int(record_id) for record_id in ids][:plans.HARD_MAX_RECORDS]
        else:
            try:
                ids = model.search(domain or [], limit=limit + 1).ids
            except (ValueError, TypeError, KeyError, AttributeError) as error:
                raise UserError(self.env._("The filter of the list could not be read.")) from error
        if not ids:
            raise UserError(self.env._("No record is selected."))
        if len(ids) > limit:
            raise UserError(self.env._(
                "More than %s records: it is more than a plan may change here. Narrow the filter, "
                "or do it in parts.", limit))
        conversation._chat_check_not_busy()
        conversation._chat_check_not_at_work()
        if not conversation._can_use_agent():
            raise UserError(self.env._("Agent mode is not open to you. Ask your administrator."))
        arguments = dict(arguments, ids=ids)
        for name in ("conditions", "match"):
            arguments.pop(name, None)
        labels = {
            "classify": self.env._("Classify the %(count)s selected", count=len(ids)),
            "summarise": self.env._("Summarise the %(count)s selected", count=len(ids)),
            "translate": self.env._("Translate the %(count)s selected", count=len(ids)),
            "fill": self.env._("Fill a field for the %(count)s selected", count=len(ids)),
        }
        noai = self.env["nudge.noai"]
        plan = types.SimpleNamespace(
            text="", calls=[("bulk_apply", arguments)], events=[], parsed=None)
        parsed = types.SimpleNamespace(intent="bulk")
        text = labels[arguments["operation"]]
        if arguments.get("instruction"):
            text += ": " + str(arguments["instruction"])[:120]
        return noai._deliver(conversation, text, MODE_AGENT, plan, parsed, {})

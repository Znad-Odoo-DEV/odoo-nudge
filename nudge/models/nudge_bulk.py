"""The AI over a selected list: what the panel offers, and the way in.

The work itself is the tool ``bulk_apply`` (tools/bulk.py); this hands it the
records the person selected in a list, through the pipeline of the agent: the
same card, the same rights, the same log and undo as when the AI asks."""
import logging

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.modules.registry import Registry

from ..llm import base as llm
from ..tools import plans
from ..tools.actions import writable_field
from ..tools.base import MODE_AGENT
from ..tools.bulk import FILL_TYPES, OPERATIONS, TEXT_TYPES
from ..tools.records import Refused, field_label, get_model

_logger = logging.getLogger(__name__)


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
        text = labels[arguments["operation"]]
        if arguments.get("instruction"):
            text += ": " + str(arguments["instruction"])[:120]
        return self._begin(conversation, text, arguments)

    @api.model
    def _begin(self, conversation, text, arguments):
        """What ``nudge.noai._deliver`` does before the tool runs, and nothing
        more: the question is written, the run opened and the mark of the
        answer set, all of it committed with the request, so that the cap on
        the answers and the guard of one answer per person see this work at
        once. The sample, which is a call to the provider, is made by the
        stream (:meth:`_preview`), in a cursor of its own, once the request
        has ended: no lock is held while the provider is waited for, and the
        work holds one of the places of the cap like any answer.

        :return: the job of the stream
        """
        record = conversation.sudo()
        record._agent_leave_undecided()
        record.run_id._end(record.run_id._outcome("stopped"))
        record.write({"stream_started_at": fields.Datetime.now(), "run_id": False})
        if not record.name:
            record.name = text[:60].strip()
        question = self.env["nudge.message"].sudo().create({
            "conversation_id": conversation.id, "role": "user", "content": text, "mode": MODE_AGENT})
        record.run_id = self.env["nudge.run"].sudo().create({
            "name": text[:120].strip(), "conversation_id": conversation.id, "question_id": question.id,
            "user_id": self.env.uid, "company_id": self.env.company.id, "model_name": "-",
            "is_developer": False})
        job = {
            "conversation_id": conversation.id,
            "user_message_id": question.id,
            "title": record.name,
            "mode": MODE_AGENT,
        }
        # The controller adds dbname, uid and context before the stream
        # starts; ``first_events`` is read only then, and ``answered`` after it
        job["first_events"] = self._preview(job, conversation.id, arguments)
        return job

    @api.model
    def _preview(self, job, conversation_id, arguments):
        """Generator, run by the stream: asks the provider for the sample as
        the user, in a cursor of its own, and puts the card before the person.
        Sets ``job["answered"]`` to the last event, which the loop sends next.

        :return: the events of the screen, if any"""
        calls = [{"id": "noai_0", "name": "bulk_apply", "input": arguments, "extra": None}]
        outcome = {
            "text": "", "tool_calls": calls, "last_round": True, "noai": True,
            "stop": {"reason": llm.STOP_TOOL_USE, "usage": {}, "blocks": []},
        }
        events = []
        try:
            with Registry(job["dbname"]).cursor() as cr:
                env = api.Environment(cr, job["uid"], job["context"])
                record = env["nudge.conversation"].browse(conversation_id).sudo()
                step = record._chat_record(outcome)
                answer = step["event"]
                Message = env["nudge.message"].sudo()
                message = Message.browse(answer["message_id"]) if answer.get("message_id") else Message
                told = ""
                if answer.get("type") != "waiting":
                    # Refused by the tool, in words: said as it is, not as a success
                    failed = record.run_id.step_ids.filtered(lambda s: s.state == "failed")[-1:]
                    if failed and failed.error:
                        told = failed.error.split(" Nothing was")[0][:600]
                        message.content = told
                if message:
                    message.write({"noai_intent": "bulk"})
                if told:
                    events.append({"type": "delta", "text": told})
                events.extend(step.get("screen_events") or [])
        except Exception:  # noqa: BLE001
            _logger.exception("Nudge: the sample of conversation %s could not be made", conversation_id)
            self._release(job, conversation_id)
            answer = {"type": "error", "state": "error", "message": ""}
        job["answered"] = answer
        yield from events

    @api.model
    def _release(self, job, conversation_id):
        """Free the place of a preview that failed, in a cursor of its own"""
        try:
            with Registry(job["dbname"]).cursor() as cr:
                env = api.Environment(cr, job["uid"], job["context"])
                env["nudge.conversation"].sudo().browse(conversation_id).stream_started_at = False
        except Exception:  # noqa: BLE001
            _logger.exception("Nudge: the mark of conversation %s could not be cleared", conversation_id)

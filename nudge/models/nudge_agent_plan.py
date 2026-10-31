"""S10: several changes asked at once become one plan, stored on the conversation: a card numbers
the steps, the person takes some out or corrects a value, says yes once, and each change still shows
its own card (or, for the groups the administrator chose, that one yes is the confirmation of every
change, shown in full). S11: what each change left is read again and told as it is."""
import copy
import logging

from psycopg2 import errors as pg_errors

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .. import tools
from ..tools import agent_plan, noai_commands
from ..tools.records import Refused
from .nudge_conversation import (
    AGENT_MAX_ROUNDS,
    AGENT_MAX_STEPS,
    CONFIRM,
    DECLINE,
    DONE,
    DONE_NOT_FINISHED,
    ONE_AT_A_TIME,
    UNDECIDED,
)

_logger = logging.getLogger(__name__)

STOP = "stop"
RESUME = "resume"
# Where a plan is
PROPOSED = "proposed"
RUNNING = "running"
STOPPED = "stopped"
FINISHED = "done"
FAILED = "failed"
DECLINED = "declined"
CLOSED = "closed"
OPEN_PHASES = (PROPOSED, RUNNING, STOPPED)
# The state of the plan in the log, once it is over
STATE_OF_PHASE = {FINISHED: "done", FAILED: "failed", DECLINED: "declined", CLOSED: "cancelled"}
# Where a step of it is
TODO = "todo"
WAITING = "waiting"
STEP_DONE = "done"
STEP_FAILED = "failed"
REMOVED = "removed"
# What the person may correct on the card: a value of its own, never a record it points at
EDITABLE_TYPES = ("char", "text", "integer", "float", "monetary", "boolean", "date", "datetime", "selection")
MAX_VALUE_CHARS = 4000
# R3-50: a change the model asked for while the person asked a question ("should I confirm this order?", "هل أؤكد
# هذا الأمر؟") is not prepared: what the model reads instead, and the most suggestions shown under the answer
QUESTION_HELD = (
    "Not prepared, and no card is shown: the person asked a question about this, not for it to be done. Answer "
    "the question in their language: explain what it would do and what to check first. A suggestion to do it is "
    "shown under your answer: tell them they can choose it, or say yes, to have it prepared for their yes. Call no "
    "tool that changes data in this answer.")
MAX_HELD_SUGGESTIONS = 3
# The kinds of record that, named in a question, say which record it is about ("should I cancel the invoice?" on an
# order). Not the words of any order ("order", "طلب": a purchase order is "this order" too), nor who or what a record
# is for ("the customer", "the product")
DOCUMENT_KINDS = frozenset(("purchase_order", "invoice", "bill", "task", "lead", "ticket", "timeoff"))


def _now():
    return fields.Datetime.now()


class NudgeStep(models.Model):
    _inherit = "nudge.step"

    kind = fields.Selection(selection_add=[("plan", "Plan")], ondelete={"plan": "set default"})
    plan_id = fields.Many2one(
        "nudge.step", string="Plan", index="btree_not_null", ondelete="restrict", readonly=True,
        help="The plan of several changes this step is one of.")
    plan_index = fields.Integer(string="Step of the Plan", readonly=True)

    def _plan_data(self):
        self.ensure_one()
        values = self.values if isinstance(self.values, dict) else {}
        return copy.deepcopy(values.get("plan") or {})

    def _plan_save(self, data, **values):
        """Write the plan; the log is written in sudo, by Nudge only"""
        self.ensure_one()
        self.sudo().write(dict(values, values=dict(self.values or {}, plan=data)))

    def _plan_children(self):
        """:return: ``{number: step}``, the latest step of each number of the plan"""
        self.ensure_one()
        children = self.sudo().search([("plan_id", "=", self.id)], order="id")
        return {child.plan_index: child for child in children}

    def _plan_streaming(self, data=None):
        """:return: whether a stream does the plan right now: it was started for it, and was neither
        stopped nor let go"""
        self.ensure_one()
        data = self._plan_data() if data is None else data
        conversation = self.sudo().conversation_id
        serial = data.get("serial") or 0
        return bool(data.get("streaming") and conversation.stream_started_at and serial
                    and conversation.stream_serial == serial and (conversation.stream_stopped or 0) < serial)

    def _get_card(self):
        if self.kind == "plan":
            return self._plan_card()
        card = super()._get_card()
        verify = (self.preview or {}).get("verify")
        if verify:
            # S11: what was read again after the change, told on its card
            card["verify"] = verify
        return card

    def _plan_card(self):
        """:return: the card of a plan: its steps, numbered, where each one is, and what it offers"""
        self.ensure_one()
        data = self._plan_data()
        phase = data.get("phase") or PROPOSED
        children = self._plan_children()
        current = data.get("current") or 0
        waiting = children.get(current) if current else None
        waiting = waiting if waiting and waiting.state == "pending" else None
        streaming = self._plan_streaming(data)
        if phase == RUNNING and not waiting and not streaming:
            # Said yes to once, and the stream that did it is gone (the page was left, the worker
            # stopped): what is left can be taken up again
            phase = STOPPED
        steps = [self._plan_step_card(entry, children.get(entry["n"]), phase) for entry in data.get("steps") or []]
        if phase in OPEN_PHASES:
            state = "working" if phase == RUNNING and streaming else "pending"
        else:
            state = STATE_OF_PHASE.get(phase, self.state)
        done = [step for step in steps if step["state"] == STEP_DONE]
        return {
            "step_id": self.id,
            "kind": "plan",
            "state": state,
            "title": self.summary or "",
            "records": [],
            "lines": [],
            "sensitive": any(step["sensitive"] for step in steps),
            "error": self.error or "",
            # The plan of many records (prepare_plan) is another card
            "plan": False,
            "plan_phase": phase,
            "plan_steps": steps,
            "one_yes": bool(data.get("one_yes")),
            "current": current if waiting else 0,
            "done_count": len(done),
            "failed_count": len([step for step in steps if step["state"] == STEP_FAILED]),
            "can_undo": any(step["can_undo"] for step in steps),
            "undone": any(step["undone"] for step in done) and not any(step["can_undo"] for step in done),
            "verify": False,
        }

    def _plan_step_card(self, entry, child, phase):
        pending = entry.get("pending") or {}
        preview = pending.get("preview") or {}
        if child and child.state in ("pending", "done", "failed"):
            # Its own card, once it was checked for its turn: the exact one
            preview = child.preview or preview
        values = child.values if child and isinstance(child.values, dict) else {}
        undoable = bool(child and child.state == "done" and child._undo_data())
        editable = []
        if phase == PROPOSED and entry.get("state") == TODO:
            editable = entry.get("editable") or []
        return {
            "n": entry["n"],
            "title": (child.summary if child else "") or pending.get("summary") or entry.get("tool"),
            "state": entry.get("state") or TODO,
            "records": preview.get("records") or [],
            "lines": preview.get("lines") or [],
            "sensitive": bool(pending.get("is_sensitive") or (child and child.is_sensitive)),
            "exact": bool(entry.get("exact")),
            "refs": sorted({reference["n"] for reference in entry.get("refs") or []}),
            "removed_with": entry.get("removed_with") or 0,
            "error": entry.get("error") or "",
            "verify": entry.get("verify") or False,
            "step_id": child.id if child else False,
            "undoable": undoable,
            "can_undo": undoable and not values.get("undone"),
            "undone": bool(values.get("undone")),
            "editable": editable,
        }

    def _plan_current_child(self):
        """:return: the step whose own card waits for the person, or an empty recordset"""
        self.ensure_one()
        current = self._plan_data().get("current") or 0
        child = self._plan_children().get(current) if current else None
        return child if child and child.state == "pending" else self.browse()

    @api.model
    def get_cards(self, conversation_id):
        cards = super().get_cards(conversation_id)
        # The steps of a plan are drawn on the card of the plan, the one of its answer
        for plan in self.search([("conversation_id", "=", conversation_id), ("kind", "=", "plan")]):
            if plan.message_id:
                cards[plan.message_id.id] = plan._get_card()
        return cards

    @api.model
    def _plan_own(self, plan_id):
        """:return: the plan, in sudo, when it is the current user's
        :raise AccessError: when it is not"""
        plan = self.sudo().browse(int(plan_id or 0)).exists()
        if not plan or plan.kind != "plan" or plan.user_id != self.env.user:
            raise AccessError(_("This is not yours."))
        return plan

    @api.model
    def plan_card(self, plan_id):
        """:return: the card of a plan as it is now, for the screen that waited on it"""
        return self._plan_own(plan_id)._get_card()

    @api.model
    def plan_remove_step(self, plan_id, number):
        """Take a step out of a plan before the yes; what uses what it makes goes with it.

        :return: the card of the plan"""
        plan = self._plan_own(plan_id)
        conversation = self.env["nudge.conversation"].browse(plan.conversation_id.id)
        return conversation._plan_remove(plan, int(number))

    @api.model
    def plan_edit_step(self, plan_id, number, field, value):
        """Correct a value of a step before the yes: it is checked again, as its card would be.

        :return: the card of the plan"""
        plan = self._plan_own(plan_id)
        conversation = self.env["nudge.conversation"].browse(plan.conversation_id.id)
        return conversation._plan_edit(plan, int(number), str(field or ""), "" if value is None else str(value))

    @api.model
    def undo_changes(self, step_id):
        step = self.sudo().browse(int(step_id or 0)).exists()
        if not step or step.kind != "plan":
            return super().undo_changes(step_id)
        if step.user_id != self.env.user:
            raise AccessError(_("This is not yours to undo."))
        children = [child for child in step._plan_children().values()
                    if child.state == "done" and child._undo_data() and not (child.values or {}).get("undone")]
        if not children:
            raise UserError(_("There is nothing to undo here."))
        messages, changed = [], []
        # The last step first: a quotation is cancelled before its customer is archived
        for child in sorted(children, key=lambda child: child.plan_index, reverse=True):
            outcome = super().undo_changes(child.id)
            messages.append(_("Step %(n)s: %(message)s", n=child.plan_index, message=outcome["message"]))
            changed += outcome["changed"]
        return {"message": " ".join(messages), "card": step._get_card(), "changed": changed}


class NudgeConversation(models.Model):
    _inherit = "nudge.conversation"

    # ------------------------------------------------------------------
    # Made of the calls of one answer
    # ------------------------------------------------------------------
    def _plan_env(self):
        """:return: the environment of the person the tools of a plan run in, never sudo"""
        return self.with_context(
            nudge_conversation_id=self.id, nudge_mode=self._chat_mode(), nudge_background=False).env

    def _chat_run_tools(self, calls):
        self.ensure_one()
        held = self._chat_held_changes(calls)
        if held:
            return self._chat_hold(calls, held)
        mode = self._chat_mode()
        run = self.sudo().run_id
        planned = []
        if run and not run.is_background:
            planned = agent_plan.plan_calls(self.env, calls, mode)
            if len(run.step_ids) + len(planned) + 1 > AGENT_MAX_STEPS:
                planned = []
        if not planned:
            return super()._chat_run_tools(calls)
        ids = {call["id"] for call in planned}
        others = [call for call in calls if call["id"] not in ids]
        waiting = self.env["nudge.step"].sudo()
        results, events, must_read = [], [], False
        if others:
            results, events, must_read, waiting = super()._chat_run_tools(others)
        plan, refused = self._plan_make(planned, mode)
        if plan:
            waiting = plan
            events.append(dict(plan._get_card(), type="confirm"))
            entries = [{"id": call["id"], "name": call["name"], "step_id": plan.id} for call in planned]
        else:
            must_read = True
            entries = [{
                "id": call["id"], "name": call["name"], "is_error": True,
                "content": tools.base.as_data(call["name"], refused.get(call["id"]) or agent_plan.PLAN_REFUSED, True),
            } for call in planned]
        by_id = {entry["id"]: entry for entry in results + entries}
        return [by_id[call["id"]] for call in calls if call["id"] in by_id], events, must_read, waiting

    # ------------------------------------------------------------------
    # R3-50: a question is answered, not made into a card
    # ------------------------------------------------------------------
    def _chat_held_changes(self, calls):
        """:return: the ids of the calls that would change data while the message being answered asks a question
        ("should I confirm this order?", "هل أؤكد هذا الأمر؟"), not for the change ("can you confirm this order?",
        "ممكن تأكده"): whatever the model decided, the turn explains and offers. Not in the background, where nobody
        is there to choose"""
        changes = {call["id"] for call in calls if not call.get("invalid")
                   and isinstance(tools.base.TOOLS.get(call["name"]), tools.base.Action)}
        if not changes or self.sudo().run_id.is_background:
            return set()
        asked = self.env["nudge.message"].sudo().search([
            ("conversation_id", "=", self.id), ("role", "=", "user")], order="id desc", limit=1)
        return changes if noai_commands.question.asks_whether(asked.content) else set()

    def _chat_hold(self, calls, held):
        """Run the calls that do not change data; each of ``held`` is checked as its card would be, and offered as a
        suggestion under the answer (sent as an order when chosen, which prepares the card), never a card: the
        model reads that it explains.

        :return: what :meth:`_chat_run_tools` returns"""
        others = [call for call in calls if call["id"] not in held]
        results, events, waiting = [], [], self.env["nudge.step"].sudo()
        if others:
            results, events, _read, waiting = super()._chat_run_tools(others)
        env = self._plan_env()
        snapshot = self._chat_screen_of_question()
        mode = self._chat_mode()
        offered = []
        for call in calls:
            if call["id"] not in held:
                continue
            result = tools.base.call(env, call["name"], call["input"], snapshot, mode)
            entry = {"id": call["id"], "name": call["name"]}
            if result.is_error:
                # What stands in the way is part of the explanation
                content = tools.base.as_data(call["name"], result.content, True)
                results.append(dict(entry, content=content, is_error=True))
                continue
            results.append(dict(entry, content=QUESTION_HELD, is_error=False))
            title = self._chat_held_title(result.pending or {})
            if title and len(offered) < MAX_HELD_SUGGESTIONS and title not in {chip["words"][0] for chip in offered}:
                offered.append(self._chat_held_chip(f"held_{call['id']}", call["name"], call["input"], title))
        if offered:
            events = [*events, {"type": "found", "commands": offered}]
        by_id = {entry["id"]: entry for entry in results}
        return [by_id[call["id"]] for call in calls if call["id"] in by_id], events, True, waiting

    def _chat_held_chip(self, name, tool, arguments, title):
        """:return: the suggestion of a held change: the change itself goes with it, so that chosen it is prepared
        as it was checked, never read again from its words (``nudge.noai._serve_held``)"""
        return {
            "name": name, "words": [title], "at_once": True, "example": "",
            "hint": _("Prepared on a card: nothing is done before your yes"),
            "kind": noai_commands.question.HELD_KIND, "tool": tool, "arguments": arguments}

    def _chat_offer_unasked(self, screen_events):
        """R3-50: a question of whether names a button of the record on the screen ("should I confirm this order?",
        "هل نلغي الفاتورة؟") and the model answered in words only ("Shall I confirm it?"): the button is offered
        under the explanation all the same, as :meth:`_chat_hold` offers it when the model prepared it. Checked as
        its card would be; a suggestion, never a card, and once: not when it was offered since the question, nor
        when a card was shown. The question must be about that record: not when it writes the reference of another
        ("S00049") or names another kind of record ("the invoice" on an order)."""
        offer = super()._chat_offer_unasked(screen_events)
        if offer:
            return offer
        if self.sudo().run_id.is_background or self._agent_rehearsing():
            return None
        Message = self.env["nudge.message"].sudo()
        asked = Message.search([("conversation_id", "=", self.id), ("role", "=", "user")], order="id desc", limit=1)
        question = noai_commands.question
        if not asked or not question.asks_whether(asked.content):
            return None
        verbs = question.buttons_named(asked.content)
        snapshot = asked.snapshot or None
        screen = self.env["nudge.noai"]._screen_of(snapshot)
        if not verbs or not screen["model"] or not screen["record_id"] or screen["model"] not in self.env:
            return None
        events = [event for message in Message.search([("conversation_id", "=", self.id), ("id", ">", asked.id)])
                  for event in message.screen_events or []] + list(screen_events)
        events = [event for event in events if isinstance(event, dict)]
        if any(event.get("type") in ("confirm", "waiting") for event in events):
            return None
        offered = [chip for event in events if event.get("type") == "found" for chip in event.get("commands") or []
                   if isinstance(chip, dict) and chip.get("kind") == question.HELD_KIND]
        env = self._plan_env()
        if not self._chat_about_screen_record(env, asked.content, screen):
            return None
        pressed = {self._chat_press_key(chip.get("arguments")) for chip in offered if chip.get("tool") == "call_method"}
        titles = {str((chip.get("words") or [""])[0]) for chip in offered}
        mode = self._chat_mode()
        chips = []
        for verb in verbs:
            if len(offered) + len(chips) >= MAX_HELD_SUGGESTIONS:
                break
            button = self._chat_button_named(verb, screen["buttons"])
            arguments = button and {"model": screen["model"], "ids": [screen["record_id"]], "method": button["name"]}
            if not arguments or self._chat_press_key(arguments) in pressed:
                continue
            result = tools.base.call(env, "call_method", arguments, snapshot, mode)
            title = not result.is_error and result.pending and self._chat_held_title(result.pending)
            if not title or title in titles:
                continue
            chips.append(self._chat_held_chip(f"held_{button['name']}", "call_method", arguments, title))
            pressed.add(self._chat_press_key(arguments))
            titles.add(title)
        return {"type": "found", "commands": chips} if chips else None

    @staticmethod
    def _chat_press_key(arguments):
        """:return: what tells one press of a button from another: the model, the records and the method"""
        arguments = arguments if isinstance(arguments, dict) else {}
        return arguments.get("model"), tuple(arguments.get("ids") or ()), arguments.get("method")

    def _chat_button_named(self, verb, buttons):
        """:return: the button of the screen ``verb`` presses, found as the rules find the buttons they press: by
        its method, else by the words of its label ("Confirm", "تأكيد"); one button, or None. Two buttons of one
        method are one press ("Send by Email" and "Send PRO-FORMA Invoice")"""
        buttons = [button for button in buttons if button.get("name") and button.get("kind", "object") == "object"
                   and not button.get("smart")]
        methods = noai_commands.question.METHODS_OF.get(verb) or ()
        found = [button for button in buttons if button["name"] in methods]
        if not found:
            found = [button for button in buttons
                     if verb in noai_commands.question.buttons_named(button.get("label") or "")]
        return found[0] if len({button["name"] for button in found}) == 1 else None

    def _chat_about_screen_record(self, env, text, screen):
        """:return: whether a question of whether may be about the record on the screen: it writes no reference
        but the record's own, and names no other kind of document (:data:`DOCUMENT_KINDS`)"""
        try:
            name = env[screen["model"]].browse(screen["record_id"]).exists().display_name
        except AccessError:
            return False
        if not name or noai_commands.question.other_references(text, name):
            return False
        spans = noai_commands.grammar.scan(noai_commands.normalise.tokenize(text))
        kinds = {cat for span in spans for cat in span.cats if cat in DOCUMENT_KINDS}
        Noai = self.env["nudge.noai"]
        models_named = {Noai._model_of(kind) for kind in kinds} - {None}
        return not models_named or screen["model"] in models_named

    @staticmethod
    def _chat_held_title(pending):
        """:return: the words of the suggestion of a held change: the title of its card, with the records it is
        about, or else the name it would write ("Press “Confirm”: Sales Order (S00012)", "Create: Contact
        (Raghad)"), sent as they are when it is chosen"""
        title = tools.base.card_text(pending).split("\n")[0].strip()
        if not pending.get("record_names"):
            named = next((line.get("value") for line in (pending.get("preview") or {}).get("lines") or []
                          if isinstance(line, dict) and line.get("field") == "name" and line.get("value")), None)
            if named:
                title = f"{title} ({str(named)[:tools.base.SHORT_LABEL]})"
        return title

    def _plan_probe(self, env, entry, makers, snapshot, mode):
        """Check one step as its card would be, before anything is done.

        :param makers: the numbers of the steps before it that create a record
        :return: what the step needs: ``refs``, ``exact`` (its card is the whole change) and
            ``pending`` (what its card shows)
        :raise Refused: when the step cannot be done as asked"""
        name, arguments = entry["tool"], entry["input"]
        if not isinstance(arguments, dict):
            raise Refused("The arguments could not be read as JSON.")
        references = agent_plan.find_references(env, arguments, entry["n"], makers)
        if references:
            # Against the record the person named: what the step says itself, not what stands in for the "@n" of it
            stopped = tools.named_record.refusal(env, name, arguments, None)
            if stopped:
                raise Refused(stopped.content)
            env = tools.named_record.checked(env)
        probe, exact = agent_plan.stand_in(env, arguments, references)
        if any(reference["path"][0] == "ids" for reference in references):
            # Done on a record an earlier step makes: its exact card comes with its turn
            agent_plan.schema_checked(name, arguments, references)
            pending = agent_plan.raw_card(env, name, arguments, references)
        else:
            result = tools.base.call(env, name, probe, snapshot, mode)
            if result.is_error or not result.pending:
                raise Refused(result.content)
            pending = agent_plan.shown_references(env, arguments, references, result.pending)
        return {"refs": references, "exact": exact, "pending": pending,
                "editable": self._plan_editable(env, arguments, references)}

    def _plan_editable(self, env, arguments, references):
        """:return: the fields of a step the person may correct on the card"""
        model = env[arguments["model"]] if isinstance(arguments.get("model"), str) and arguments["model"] in env \
            else None
        if model is None:
            return []
        pointed = {reference["path"][1] for reference in references if reference["path"][0] == "values"}
        names = []
        for index, entry in enumerate(arguments.get("values") or []):
            field = model._fields.get(entry.get("field") or "")
            if field is not None and field.type in EDITABLE_TYPES and index not in pointed:
                names.append(field.name)
        return names

    def _plan_make(self, calls, mode):
        """:return: ``(plan, {})``, the step of the plan that waits for the person, or
            ``(None, {call id: why})`` when a call of it cannot be done as asked: nothing is shown"""
        if len(calls) > agent_plan.MAX_STEPS:
            text = agent_plan.TOO_MANY % agent_plan.MAX_STEPS
            return None, {call["id"]: text for call in calls}
        env = self._plan_env()
        snapshot = self._chat_screen_of_question()
        steps, refused, makers = [], {}, set()
        for number, call in enumerate(calls, start=1):
            tool = tools.base.TOOLS[call["name"]]
            entry = {"n": number, "call_id": call["id"], "tool": call["name"], "kind": tool.kind,
                     "input": call.get("input") or {}, "state": TODO}
            try:
                entry.update(self._plan_probe(env, entry, makers, snapshot, mode))
            except Refused as refusal:
                text = str(refusal)
                refused[call["id"]] = text if tools.base.NOTHING_MADE in text else f"{text} {tools.base.NOTHING_MADE}"
            else:
                if tool.kind == "create":
                    makers.add(number)
            steps.append(entry)
        if refused:
            for call in calls:
                tool = tools.base.TOOLS.get(call["name"])
                text = refused.get(call["id"]) or agent_plan.PLAN_REFUSED
                self._agent_log(call, tool, tools.base.ToolResult(text, is_error=True), "failed")
            return None, refused
        plan = self.env["nudge.step"].sudo().create({
            "run_id": self.sudo().run_id.id,
            "call_id": calls[0]["id"],
            "tool": agent_plan.PLAN_TOOL,
            "kind": "plan",
            "state": "pending",
            "arguments": {"calls": [{"id": call["id"], "name": call["name"], "input": call.get("input") or {}}
                                    for call in calls]},
            "summary": _("A plan of %s steps", len(steps)),
            "values": {"plan": {"phase": PROPOSED, "one_yes": agent_plan.is_one_yes(self.env), "current": 0,
                                "steps": steps, "edits": []}},
            # What was shown, for the one who reads the log
            "preview": {"lines": [{"label": f"{entry['n']}.", "value": entry["pending"].get("summary")}
                                  for entry in steps]},
            "is_sensitive": any(entry["pending"].get("is_sensitive") for entry in steps),
        })
        _logger.info("Nudge: a plan of %s steps waits for user %s in conversation %s", len(steps), self.env.uid,
                     self.id)
        return plan, {}

    # ------------------------------------------------------------------
    # Corrected before the yes
    # ------------------------------------------------------------------
    def _plan_check_open(self, plan):
        if self.sudo().waiting_step_id != plan or plan._plan_data().get("phase") != PROPOSED:
            raise UserError(_("The plan can be changed only before your yes."))
        self._plan_lock(plan)

    def _plan_remove(self, plan, number):
        self._plan_check_open(plan)
        data = plan._plan_data()
        steps = data.get("steps") or []
        if not any(entry["n"] == number and entry["state"] == TODO for entry in steps):
            raise UserError(_("There is no such step to take out."))
        removed = {number}
        grew = True
        while grew:
            # What uses what a step taken out makes cannot be done without it
            grew = False
            for entry in steps:
                if entry["n"] not in removed and any(ref["n"] in removed for ref in entry.get("refs") or []):
                    removed.add(entry["n"])
                    grew = True
        for entry in steps:
            if entry["n"] in removed and entry["state"] == TODO:
                entry["state"] = REMOVED
                if entry["n"] != number:
                    entry["removed_with"] = number
        data.setdefault("edits", []).append({"n": number, "removed": sorted(removed), "at": str(_now())})
        plan._plan_save(data)
        _logger.info("Nudge: steps %s taken out of plan %s by user %s", sorted(removed), plan.id, self.env.uid)
        return plan._get_card()

    def _plan_edit(self, plan, number, field, value):
        self._plan_check_open(plan)
        data = plan._plan_data()
        steps = data.get("steps") or []
        index = next((i for i, entry in enumerate(steps) if entry["n"] == number and entry["state"] == TODO), None)
        if index is None or field not in (steps[index].get("editable") or []):
            raise UserError(_("This value cannot be corrected here: take the step out, or ask again."))
        entry = copy.deepcopy(steps[index])
        target = next(item for item in entry["input"]["values"] if item.get("field") == field)
        before = target["value"]
        target["value"] = value[:MAX_VALUE_CHARS]
        makers = {other["n"] for other in steps
                  if other["n"] < number and other["kind"] == "create" and other["state"] != REMOVED}
        mode = self._chat_mode()
        env = plan._company_env(self._plan_env())
        try:
            entry.update(self._plan_probe(env, entry, makers, self._chat_screen_of_question(), mode))
        except Refused as refusal:
            raise UserError(str(refusal).replace(tools.base.NOTHING_MADE, "").strip()) from None
        steps[index] = entry
        data.setdefault("edits", []).append({"n": number, "field": field, "before": before, "after": target["value"],
                                             "at": str(_now())})
        plan._plan_save(data)
        return plan._get_card()

    # ------------------------------------------------------------------
    # The yes, and each step
    # ------------------------------------------------------------------
    def _plan_lock(self, plan):
        """Two clicks, two windows: decided once"""
        try:
            with self.env.cr.savepoint(flush=False):
                self.env.cr.execute("SELECT id FROM nudge_step WHERE id = %s FOR UPDATE NOWAIT", [plan.id])
        except pg_errors.LockNotAvailable:
            raise UserError(_("This is being done already.")) from None
        plan.invalidate_recordset(["state", "values"])

    def _agent_decide(self, step_id, decision):
        self.ensure_one()
        plan = self.env["nudge.step"].sudo().browse(step_id or 0).exists() if step_id else None
        if not plan or plan.kind != "plan":
            return super()._agent_decide(step_id, decision)
        if self.user_id != self.env.user:
            raise AccessError(_("This conversation is not yours."))
        if not self._can_use_agent():
            raise UserError(_("Agent mode is not open to you. Ask your administrator."))
        if decision not in (CONFIRM, DECLINE, STOP, RESUME):
            raise UserError(_("Say yes or no."))
        conversation = self.sudo()
        if conversation.waiting_step_id != plan or plan.conversation_id != conversation:
            raise UserError(_("This is no longer waiting for you."))
        self._plan_lock(plan)
        if plan.state not in ("pending", "working"):
            raise UserError(_("This is no longer waiting for you."))
        if plan._plan_streaming():
            raise UserError(_("This is being done already."))
        self._chat_check_not_busy()
        return self._plan_decide(plan, decision)

    def _plan_choose(self, plan):
        """:return: (provider, notice) for the model that reads what came of the plan; (None, None)
        when what was asked was understood without AI. Asked before anything is done: a yes the
        model cannot follow is refused, as for one change"""
        if plan.message_id.noai_intent:
            return None, None
        return self._chat_choose(self._chat_mode())

    def _plan_decide(self, plan, decision):
        data = plan._plan_data()
        phase = data.get("phase")
        if phase == PROPOSED and decision == STOP:
            decision = DECLINE
        child = plan._plan_current_child()
        if decision == STOP or (decision == DECLINE and phase != PROPOSED):
            return self._plan_stop(plan, child, declined=decision == DECLINE)
        choice = self._plan_choose(plan)
        if decision == DECLINE:
            return self._plan_end(plan, DECLINED, [], choice)
        if phase == PROPOSED:
            if not any(entry["state"] == TODO for entry in data.get("steps") or []):
                raise UserError(_("Every step was taken out: there is nothing to do. Say no, or ask again."))
            if decision != CONFIRM:
                raise UserError(_("Say yes or no."))
            _logger.info("Nudge: plan %s said yes to by user %s", plan.id, self.env.uid)
        data["phase"] = RUNNING
        plan._plan_save(data, decided_at=plan.decided_at or _now())
        if decision == CONFIRM and child:
            return self._plan_go(plan, choice, child=child)
        return self._plan_go(plan, choice)

    def _plan_go(self, plan, choice, child=None):
        """Do the step said yes to, then show the card of the next one; or, with one yes for the
        whole plan, let the stream do the steps"""
        events = []
        if child:
            done, events, report = self._plan_execute(plan, child)
            if not done:
                return self._plan_end(plan, FAILED, events, choice)
            if report and not report["ok"] and self._plan_left(plan):
                # Not as asked: what comes next may build on it; the person looks first
                return self._plan_stop(plan, None, events=events)
        if not self._plan_left(plan):
            return self._plan_end(plan, FINISHED, events, choice)
        if plan._plan_data().get("one_yes"):
            return self._plan_stream_job(plan, choice, events)
        child, error = self._plan_next(plan, shown=False)
        if error:
            return self._plan_end(plan, FAILED, events, choice)
        return self._plan_answered(plan, [dict(plan._get_card(), type="decided"), *events])

    @staticmethod
    def _plan_left(plan):
        return any(entry["state"] in (TODO, WAITING) for entry in plan._plan_data().get("steps") or [])

    def _plan_answered(self, plan, first_events):
        """:return: the job of a decision that ends without the model: the card says it all"""
        return {
            "conversation_id": self.id,
            "user_message_id": False,
            "title": self.sudo().name,
            "mode": self._chat_mode(),
            "first_events": first_events,
            "answered": {"type": "done", "message_id": plan.message_id.id, "state": "complete", "message": ""},
        }

    def _plan_next(self, plan, shown):
        """Check the next step for its turn and make its own card.

        :param shown: one yes for the whole plan: the card of the plan is the confirmation of a step
            whose change it showed in full, and of nothing else
        :return: ``(child, error)``: the step to do (waiting when its own card must be seen first), or
            the error that stopped the plan"""
        data = plan._plan_data()
        entry = next((entry for entry in data.get("steps") or [] if entry["state"] in (TODO, WAITING)), None)
        child, error = self._plan_child(plan, entry, shown)
        data = plan._plan_data()
        entry = next(item for item in data["steps"] if item["n"] == entry["n"])
        if error:
            entry.update(state=STEP_FAILED, error=error)
            plan._plan_save(data)
            return None, error
        entry.update(state=WAITING, step_id=child.id)
        data["current"] = entry["n"]
        plan._plan_save(data)
        return child, None

    def _plan_child(self, plan, entry, shown):
        """:return: ``(step, error)``: the step of the log for ``entry``, waiting; its ``values`` say
        ``plan_shown`` when the card of the plan showed it as it is"""
        data = plan._plan_data()
        made = {item["n"]: item.get("made") or [] for item in data["steps"] if item["state"] == STEP_DONE}
        mode = self._chat_mode()
        try:
            env = plan._company_env(self._plan_env())
            if entry.get("refs"):
                # What an earlier step of this plan made: checked with the step when the plan was made
                env = tools.named_record.checked(env)
            if shown and entry.get("exact") and not entry.get("refs"):
                arguments, pending, covered = entry["input"], entry["pending"], True
            else:
                arguments = agent_plan.resolve(entry["input"], entry.get("refs") or [], made)
                result = tools.base.call(env, entry["tool"], arguments, self._chat_screen_of_question(), mode)
                if result.is_error or not result.pending:
                    return None, result.content
                pending = result.pending
                covered = shown and entry.get("exact") and self._plan_same(entry, pending, made)
        except (Refused, UserError) as error:
            return None, str(error)
        child = self.env["nudge.step"].sudo().create({
            "run_id": plan.run_id.id,
            "message_id": plan.message_id.id,
            "plan_id": plan.id,
            "plan_index": entry["n"],
            "company_id": plan.company_id.id,
            "call_id": entry["call_id"],
            "tool": entry["tool"],
            "kind": entry["kind"],
            "state": "pending",
            "arguments": arguments,
            "summary": pending.get("summary"),
            "res_model": pending.get("res_model") or False,
            "res_ids": pending.get("res_ids") or [],
            "record_names": pending.get("record_names") or False,
            "values": dict(pending.get("values") or {}, plan_shown=bool(covered)) if pending.get("values")
            else {"plan_shown": bool(covered)},
            "method": pending.get("method") or False,
            "preview": pending.get("preview") or False,
            "is_sensitive": bool(pending.get("is_sensitive")),
        })
        return child, None

    @staticmethod
    def _plan_same(entry, pending, made):
        """:return: whether a step checked for its turn writes what the card of the plan showed:
        the same values, and the records the earlier steps made where it said "@n" """
        shown = entry["pending"].get("values") or {}
        fresh = pending.get("values") or {}
        if (shown.get("lines") or {}) != (fresh.get("lines") or {}):
            return False
        expected = dict(shown.get("values") or {})
        fresh_values = dict(fresh.get("values") or {})
        for reference in entry.get("refs") or []:
            if reference["path"][0] != "values":
                return False
            name = entry["input"]["values"][reference["path"][1]]["field"]
            ids = made.get(reference["n"]) or []
            if fresh_values.pop(name, None) not in (ids[:1] and ids[0], ids):
                return False
        return fresh_values == expected

    def _plan_execute(self, plan, child):
        """Do one step, as the person, in the company of the plan, and read again what it wrote.

        :return: ``(done, events, report)``"""
        tool = tools.base.TOOLS.get(child.tool)
        mode = self._chat_mode()
        if not isinstance(tool, tools.base.Action) or not tool.is_available(self.env, mode):
            result = tools.base.ToolResult("This can no longer be done.", is_error=True)
            env = self._plan_env()
        else:
            env = self._plan_env()
            try:
                env = child._company_env(env)
            except UserError as error:
                result = tools.base.ToolResult(str(error), is_error=True)
            else:
                result = tools.base.guarded(env, child.tool, lambda: tool.execute(env, child))
        events = [result.event] if result.event else []
        state = "failed" if result.is_error else "done"
        model, ids, names = result.records or (child.res_model, child.res_ids or [], [])
        values = {"state": state, "result": result.content, "error": result.content if result.is_error else False,
                  "decided_at": _now(), "res_ids": list(ids)[:50]}
        if names:
            values["record_names"] = ", ".join(map(str, names[:10]))[:500]
        report = None
        if state == "done":
            stored = dict(child.values or {})
            if child.kind == "create" and ids and not stored.get("undo") and model in env:
                # Taken back as a draft nobody changed: archived, or cancelled, never deleted
                from ..tools.plans import snapshot  # noqa: PLC0415 - plans imports the tools

                record = env[model].browse(ids[:1])
                stored["undo"] = {model: {str(ids[0]): {"created": True, "after": snapshot(record, ["write_date"])}}}
                values["values"] = stored
            preview = dict(child.preview or {})
            if result.card_lines is not None:
                preview["lines"] = result.card_lines
            if child.tool in agent_plan.VERIFIED_TOOLS:
                report = agent_plan.verify(env, model, ids, child.values, preview.get("lines"))
                preview["verify"] = report
            values["preview"] = preview
        child.sudo().write(values)
        data = plan._plan_data()
        entry = next(item for item in data["steps"] if item["n"] == child.plan_index)
        told = result.content
        if report is not None:
            told = f"{told} {agent_plan.CHECKED}" if report["ok"] else agent_plan.NOT_AS_ASKED % {
                "fields": agent_plan.differences_text(report), "content": told}
        entry.update(state=STEP_DONE if state == "done" else STEP_FAILED, step_id=child.id, result=told,
                     error=result.content if result.is_error else "", verify=report or False,
                     made=list(ids) if child.kind == "create" and state == "done" else [])
        data["current"] = 0
        plan._plan_save(data)
        _logger.info("Nudge: step %s of plan %s %s for user %s", child.plan_index, plan.id, state, self.env.uid)
        return state == "done", events, report

    def _plan_stop(self, plan, child, declined=False, events=()):
        """The person stops the plan between two steps: what is done stays (with its Undo), and what
        is left can be taken up again"""
        data = plan._plan_data()
        if child:
            child.sudo().write({"state": "declined" if declined else "cancelled", "decided_at": _now()})
        for entry in data.get("steps") or []:
            if entry["state"] == WAITING:
                entry["state"] = TODO
        data.update(phase=STOPPED, current=0, streaming=False)
        plan._plan_save(data, state="pending")
        conversation = self.sudo()
        conversation.stream_started_at = False
        plan.run_id.state = "waiting"
        _logger.info("Nudge: plan %s stopped by user %s", plan.id, self.env.uid)
        return self._plan_answered(plan, [dict(plan._get_card(), type="decided"), *events])

    def _plan_settle(self, plan, phase):
        """The plan is over: each of its calls is answered, for the model to read once"""
        data = plan._plan_data()
        steps = data.get("steps") or []
        failed_at = next((entry["n"] for entry in steps if entry["state"] == STEP_FAILED), None)
        told = {}
        for entry in steps:
            words = {"n": entry["n"], "title": (entry.get("pending") or {}).get("summary") or entry["tool"]}
            if phase == DECLINED:
                text, error = agent_plan.STEP_DECLINED % words, True
            elif entry["state"] == STEP_DONE:
                text, error = agent_plan.STEP_DONE % dict(words, content=entry.get("result") or ""), False
            elif entry["state"] == STEP_FAILED:
                text, error = agent_plan.STEP_FAILED % dict(words, content=entry.get("error") or ""), True
            elif entry["state"] == REMOVED:
                text, error = agent_plan.STEP_REMOVED % words, True
            elif failed_at:
                text, error = agent_plan.STEP_NOT_REACHED % dict(words, at=failed_at), True
            else:
                text, error = agent_plan.STEP_STOPPED % words, True
            told[entry["call_id"]] = (text, error)
        message = plan.message_id
        results = []
        for entry in message.pending_results or []:
            if entry.get("step_id") == plan.id and entry["id"] in told:
                text, error = told[entry["id"]]
                entry = {"id": entry["id"], "name": entry["name"],
                         "content": tools.base.as_data(entry["name"], text, error), "is_error": error}
            elif "content" not in entry:
                entry = dict(entry, content=ONE_AT_A_TIME, is_error=True)
            results.append({key: entry[key] for key in ("id", "name", "content", "is_error")})
        message.write({"state": "complete", "pending_results": False})
        self.env["nudge.message"].sudo().create({"conversation_id": self.id, "role": "tool", "tool_results": results})
        data.update(phase=phase, current=0, streaming=False)
        plan._plan_save(data, state=STATE_OF_PHASE[phase], decided_at=plan.decided_at or _now(),
                        result="\n".join(text for text, _error in told.values()))
        conversation = self.sudo()
        if conversation.waiting_step_id == plan:
            conversation.waiting_step_id = False

    def _plan_close(self, plan, reason=UNDECIDED):
        """The person went on without deciding: what was done is told, and what was not"""
        data = plan._plan_data()
        if data.get("phase") not in OPEN_PHASES and plan.state not in ("pending", "working"):
            return
        child = plan._plan_current_child()
        if child:
            child.sudo().write({"state": "cancelled", "decided_at": _now(), "result": reason})
        self._plan_settle(plan, CLOSED)

    def _plan_end(self, plan, phase, events, choice):
        """The plan is over (done, failed or said no to): its calls are answered, and the model reads
        what came of them once, to tell the person or go on"""
        self._plan_settle(plan, phase)
        first_events = [dict(plan._get_card(), type="decided"), *events]
        run = plan.run_id
        conversation = self.sudo()
        provider, notice = choice
        if provider is None:
            run._end(run._outcome("done"))
            conversation.stream_started_at = False
            return self._plan_answered(plan, first_events)
        run.state = "running"
        conversation.write({"stream_started_at": _now(), "run_id": run.id,
                            "stream_serial": conversation.stream_serial + 1})
        job = self._chat_job(provider, self._chat_mode(), AGENT_MAX_ROUNDS)
        job["first_events"] = first_events
        job.update(serial=conversation.stream_serial, fell_back=notice)
        job["push_to_act"] = job["push_on_question"] = False
        return job

    def _plan_stream_job(self, plan, choice, events=()):
        """One yes for the whole plan: the stream does its steps one by one, each in a transaction of
        its own, and reads the stop of the person between two of them (``llm.loop.run_plan``)"""
        conversation = self.sudo()
        provider, notice = choice
        run = plan.run_id
        run.state = "running"
        conversation.write({"stream_started_at": _now(), "run_id": run.id,
                            "stream_serial": conversation.stream_serial + 1})
        data = plan._plan_data()
        data.update(streaming=True, serial=conversation.stream_serial, current=0)
        plan._plan_save(data, state="working")
        if provider is None:
            job = {"conversation_id": self.id, "user_message_id": False, "title": conversation.name,
                   "mode": self._chat_mode()}
        else:
            job = self._chat_job(provider, self._chat_mode(), AGENT_MAX_ROUNDS)
            job["fell_back"] = notice
        job["first_events"] = [dict(plan._get_card(), type="decided"), *events]
        job.update(serial=conversation.stream_serial, plan_id=plan.id, push_to_act=False, push_on_question=False)
        return job

    def _chat_record(self, outcome):
        if outcome.get("plan_id"):
            return self._plan_record(outcome)
        return super()._chat_record(outcome)

    def _plan_record(self, outcome):
        """One step of a plan said yes to once, in a transaction of its own (see ``llm.loop.run_plan``).

        :return: ``next`` when another step follows; else ``turns`` for the model to read what came of
            the plan, or ``event`` to end the answer here"""
        self.ensure_one()
        conversation = self.sudo()
        plan = self.env["nudge.step"].sudo().browse(outcome["plan_id"]).exists()
        gone = {"screen_events": [], "event": {"type": "done", "message_id": False, "state": "aborted", "message": ""}}
        if not plan or plan.kind != "plan" or conversation.waiting_step_id != plan:
            return gone
        # One step at a time, whoever else looks at the plan: a yes in another window waits for it
        self.env.cr.execute("SELECT id FROM nudge_step WHERE id = %s FOR UPDATE", [plan.id])
        plan.invalidate_recordset(["state", "values"])
        data = plan._plan_data()
        if data.get("phase") != RUNNING or not data.get("streaming") or (
                outcome.get("serial") and data.get("serial") != outcome["serial"]):
            # Stopped, closed, or taken up by another stream since: this one has nothing left to do
            return gone
        noai = bool(plan.message_id.noai_intent)
        if outcome.get("plan_action") == STOP:
            job = self._plan_stop(plan, None)
            return {"screen_events": job["first_events"], "event": job["answered"]}
        if not self._plan_left(plan):
            return self._plan_stream_end(plan, FINISHED, [], noai)
        child, error = self._plan_next(plan, shown=True)
        if error:
            return self._plan_stream_end(plan, FAILED, [], noai)
        if not (child.values or {}).get("plan_shown"):
            # Its change was not shown in full on the card of the plan: its own card asks first
            data = plan._plan_data()
            data["streaming"] = False
            plan._plan_save(data, state="pending")
            conversation.stream_started_at = False
            plan.run_id.state = "waiting"
            return {"screen_events": [dict(plan._get_card(), type="decided")], "event": {
                "type": "done", "message_id": plan.message_id.id, "state": "complete", "message": ""}}
        done, events, report = self._plan_execute(plan, child)
        if not done:
            return self._plan_stream_end(plan, FAILED, events, noai)
        if report and not report["ok"] and self._plan_left(plan):
            job = self._plan_stop(plan, None, events=events)
            return {"screen_events": job["first_events"], "event": job["answered"]}
        if not self._plan_left(plan):
            return self._plan_stream_end(plan, FINISHED, events, noai)
        return {"screen_events": [dict(plan._get_card(), type="decided"), *events], "next": True}

    def _plan_stream_end(self, plan, phase, events, noai):
        self._plan_settle(plan, phase)
        screen_events = [dict(plan._get_card(), type="decided"), *events]
        conversation = self.sudo()
        if noai:
            plan.run_id._end(plan.run_id._outcome("done"))
            conversation.stream_started_at = False
            return {"screen_events": screen_events, "event": {
                "type": "done", "message_id": plan.message_id.id, "state": "complete", "message": ""}}
        return {"screen_events": screen_events, "turns": conversation._chat_build_turns()}

    # ------------------------------------------------------------------
    # Left without a decision; and S11 for a change of its own
    # ------------------------------------------------------------------
    def _agent_leave_undecided(self, reason=UNDECIDED):
        for conversation in self.sudo():
            step = conversation.waiting_step_id
            if step.kind == "plan" and step.state in ("pending", "working"):
                conversation._plan_close(step, reason)
        return super()._agent_leave_undecided(reason)

    def _agent_settle(self, step, state, result, events=None):
        if step.kind == "plan":
            # Closed by a stop of the run, or a new message: what was done is told
            return self._plan_close(step, result.content)
        report = None
        if state == "done" and step.tool in agent_plan.VERIFIED_TOOLS and isinstance(step.values, dict):
            model, ids, _names = result.records or (step.res_model, step.res_ids or [], [])
            env = self._plan_env()
            try:
                env = step._company_env(env)
            except UserError:
                env = None
            if env is not None:
                report = agent_plan.verify(env, model, ids, step.values, (step.preview or {}).get("lines"))
            if report and not report["ok"]:
                result = tools.base.ToolResult(
                    agent_plan.NOT_AS_ASKED % {"fields": agent_plan.differences_text(report),
                                               "content": self._plan_inner(result.content)},
                    records=result.records, unfinished=result.unfinished, card_lines=result.card_lines)
        settled = super()._agent_settle(step, state, result, events)
        if report is not None:
            step.sudo().preview = dict(step.preview or {}, verify=report)
        return settled

    @staticmethod
    def _plan_inner(content):
        """:return: what the tool said, without the words that would tell the model it is done"""
        for template in (DONE, DONE_NOT_FINISHED):
            head, _sep, tail = template.partition("%s")
            if content.startswith(head) and content.endswith(tail):
                return content[len(head):len(content) - len(tail)].strip()
        return content


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    nudge_plan_writes = fields.Integer(
        string="Changes Before a Plan", config_parameter=agent_plan.WRITES_PARAM, default=agent_plan.DEFAULT_WRITES,
        help="More changes than this asked at once, or two kinds of change (a customer, then their quotation), "
             "are shown first as one plan with numbered steps. At most 10.")
    nudge_plan_one_yes_group_ids = fields.Many2many(
        "res.groups", "nudge_plan_one_yes_group_rel", "config_id", "group_id", string="One Yes for a Whole Plan",
        help="The people of these groups say one yes for a whole plan: its card shows every change in full, and "
             "each change is still written in Nudge, Activity. Everybody else sees the card of each change.")

    def get_values(self):
        values = super().get_values()
        values["nudge_plan_one_yes_group_ids"] = [Command.set(agent_plan.one_yes_groups(self.env).ids)]
        return values

    def set_values(self):
        super().set_values()
        self.env["ir.config_parameter"].sudo().set_param(
            agent_plan.ONE_YES_PARAM, ",".join(str(ident) for ident in self.nudge_plan_one_yes_group_ids.ids))

"""Common requests of the Agent understood without any AI.

``tools/noai_commands`` turns a sentence, in any Arabic dialect or in
English, into what is asked. Here that is resolved on the data, as the
person, and handed to the tools Nudge already has: the same cards to say
yes or no to, the same rights, the same log and undo as when the AI asks.
What is not understood well enough goes to the AI provider as before; with
none, or with private mode on, the person is told what was understood and
what can be done without AI.
"""
import datetime
import copy
import re
import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..llm import base as llm
from ..llm import snapshot as screen_snapshot
from ..tools import approval_sentence, base, commands, digest, home, noai_commands, revert, setup, setup_sentence
from ..tools.base import MODE_AGENT, MODE_DEV, MODE_TALK
from ..tools.noai_commands import grammar
from ..tools.records import Refused

_logger = logging.getLogger(__name__)

PARAM = "nudge.noai_first"
NO_TOKENS = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
MAX_CHOICES = 5
# NP-02: what the rules answer in Talk, for a person who may ask the data but not act: reading, counting and
# taking them to a screen. Anything that writes is left to the Agent
READ_INTENTS = frozenset({"open", "find", "filter", "aggregate", "chart"})

# What each kind of record is: its model, the date it is counted by, the
# amount it is summed by, the field that holds who it is for
KINDS = {
    "contact": {"model": "res.partner", "date": "create_date", "screen": "contacts"},
    "product": {"model": "product.template", "date": "create_date", "screen": "products"},
    "quotation": {"model": "sale.order", "date": "date_order", "amount": "amount_total",
                  "party": "partner_id", "screen": "quotations", "states": ("draft", "sent")},
    "sales_order": {"model": "sale.order", "date": "date_order", "amount": "amount_total",
                    "party": "partner_id", "screen": "orders", "states": ("sale", "done")},
    "sales": {"model": "sale.order", "date": "date_order", "amount": "amount_total",
              "party": "partner_id", "screen": "orders", "states": ("sale", "done")},
    "purchase_order": {"model": "purchase.order", "date": "date_order", "amount": "amount_total",
                       "party": "partner_id", "screen": "purchase orders"},
    "invoice": {"model": "account.move", "date": "invoice_date", "amount": "amount_total",
                "party": "partner_id", "screen": "invoices", "move_types": ("out_invoice",)},
    "bill": {"model": "account.move", "date": "invoice_date", "amount": "amount_total",
             "party": "partner_id", "screen": "bills", "move_types": ("in_invoice",)},
    "task": {"model": "project.task", "date": "create_date", "party": "partner_id", "screen": "tasks"},
    "lead": {"model": "crm.lead", "date": "create_date", "party": "partner_id", "screen": "leads"},
    "ticket": {"model": "helpdesk.ticket", "date": "create_date", "party": "partner_id", "screen": "tickets"},
    "timeoff": {"model": "hr.leave", "date": "date_from", "screen": "time off"},
    "employee": {"model": "hr.employee", "date": "create_date", "screen": "employees"},
}
# The fields a word names, the first one the model has
FIELD_NAMES = {
    "phone": ("phone", "mobile"),
    "mobile": ("mobile", "phone"),
    "email": ("email", "email_from"),
    "price": ("list_price", "price_unit"),
    "name": ("name",),
    "deadline": ("date_deadline",),
    "date": ("date_deadline", "date_order", "date"),
    "city": ("city",),
    "street": ("street",),
}
# The buttons a verb presses: by method, and by what their label says
BUTTONS = {
    "press_confirm": ("action_confirm", "button_confirm"),
    "press_validate": ("button_validate", "action_validate", "action_done"),
    "press_post": ("action_post",),
    "press_approve": ("action_approve", "button_approve", "approve", "action_validate"),
}


class Plan:
    """What is to be done for a sentence: tool calls to hand to the pipeline,
    or a plain answer"""

    def __init__(self, text, calls=None, events=None, parsed=None):
        self.text = text
        self.calls = calls or []
        self.events = events or []
        self.parsed = parsed


# A request of several sentences, lines or clauses is not one the rules read whole
MULTI_SENTENCE = re.compile(r"[.!?؟;]\s+\S|\n\s*\S|\b(?:and then|then|after that|also)\b", re.I)
COUNT_WORDS = (r"\d+|two|three|four|five|six|seven|eight|nine|ten|several|many|"
               "اثنين|ثلاثة|أربعة|"
               "خمسة")
COUNTED_CREATE = re.compile(
    r"\b(?:create|add|make|new|أنشئ|أضف)\s+(?:%s)\s+\w+" % COUNT_WORDS, re.I)
ACTION_WORDS = re.compile(r"\b(?:create|add|make|delete|remove|update|change|set|confirm|cancel|send|post)\b", re.I)
NAMED_INTENTS = ("open", "find", "latest", "filter", "aggregate", "create", "update", "press")
WEBSITE = re.compile(
    r"\bweb ?(?:site|page)s?\b|\blanding page\b|\u0645\u0648\u0642\u0639 (?:\u0648\u064a\u0628|\u0625\u0644\u0643\u062a\u0631\u0648\u0646\u064a|\u0627\u0644\u0643\u062a\u0631\u0648\u0646\u064a)", re.I)
NEGATED = re.compile(r"\b(?:do not|don't|dont|never|doesn't)\b|(?<!\w)(?:\u0644\u0627|\u0628\u062f\u0648\u0646)\s+\w+", re.I)
MAX_NAME_WORDS = 5


class NudgeNoai(models.AbstractModel):
    _name = "nudge.noai"
    _description = "Nudge Common Requests Without AI"

    # ------------------------------------------------------------------
    # Settings and the screen
    # ------------------------------------------------------------------
    @api.model
    def _is_enabled(self):
        value = self.env["ir.config_parameter"].sudo().get_param(PARAM)
        return str(value or "").lower() in ("1", "true")

    @api.model
    def _may_ask(self):
        """:return: whether the rules answer this person: the Agent, or the Ask tier of a Nudge User (NP-02)"""
        return self.env.user.has_group("nudge.group_nudge_agent") or base.ask_tier_applies(self.env)

    @api.model
    def _screen_of(self, snapshot):
        """:return: ``{"model", "record_id", "buttons"}`` of a snapshot of the screen"""
        snapshot = snapshot if isinstance(snapshot, dict) else screen_snapshot.sanitize(snapshot) or {}
        record = snapshot.get("record") if isinstance(snapshot.get("record"), dict) else {}
        return {
            "model": snapshot.get("model") or "",
            "record_id": record.get("id") or None,
            "view_type": snapshot.get("view_type") or "",
            "buttons": [button for button in snapshot.get("buttons") or [] if isinstance(button, dict)],
        }

    @api.model
    def _has_ai(self, conversation, mode):
        """:return: whether a provider can answer: one is set up, and private mode lets it"""
        Provider = self.env["nudge.provider"].sudo()
        provider = Provider._get_for_mode(mode) or Provider._get_default()
        if not provider or not provider.is_configured or not provider.active:
            return False
        return not (Provider._is_private_mode() and not provider.runs_locally)

    # ------------------------------------------------------------------
    # The way in
    # ------------------------------------------------------------------
    @api.model
    def _company_words(self):
        """:return: a block in which the words the company added are understood too"""
        return noai_commands.lexicon.extended(self.env["nudge.lexicon.entry"]._pairs())

    @api.model
    def suggest(self, words, snapshot=None):
        """For the quick search box: what the words ask, understood without any AI.
        :return: ``{"intent", "label"}`` or False"""
        self.env["nudge.quick"]._check_user()
        if not self._is_enabled() or not self._may_ask():
            return False
        with self._company_words():
            parsed = noai_commands.parse(str(words or "")[:400], self._screen_of(snapshot))
        if not parsed.intent or parsed.confidence < noai_commands.THRESHOLD:
            return False
        if parsed.intent not in READ_INTENTS and not self.env.user.has_group("nudge.group_nudge_agent"):
            return False
        return {"intent": parsed.intent, "label": self._describe(parsed), "text": parsed.text}

    @api.model
    def _chat_serve(self, conversation, text, mode, focus, found, explain, background, snapshot, fresh=False):
        """Answer ``text`` when it is a common request, before any provider is called.

        :return: the job of the stream (the answer is already written), or None to go on"""
        if focus == "count" and not (fresh or found or background or explain == "screen"):
            return self._serve_count(conversation, text, mode, snapshot)
        if mode == MODE_AGENT:
            served = self._serve_setup(conversation, text, mode, (fresh, focus, found, background, explain), snapshot)
            served = served or self._serve_approval_hint(
                conversation, text, (fresh, focus, found, background, explain), snapshot)
            if served:
                return served
        if mode == MODE_DEV:
            return self._serve_approval(conversation, text, mode, (fresh, focus, found, background, explain), snapshot)
        if (fresh or focus or found or background or explain == "screen" or mode not in (MODE_TALK, MODE_AGENT)
                or not self._may_ask()):
            return None
        direct = self._serve_changes(conversation, text, mode, snapshot)
        if direct is not None:
            return direct
        has_ai = self._has_ai(conversation, mode)
        if has_ai and not self._is_enabled():
            return None
        values, _text = conversation._chat_screen(snapshot)
        screen = self._screen_of(values.get("snapshot") or {})
        # S9: a request people kept asking the AI, accepted by a manager as a rule: answered at once
        learned = self.env["nudge.rule"]._plan_for(text, screen, mode, self)
        if learned:
            return self._deliver(conversation, text, mode, learned, learned.parsed, values)
        with self._company_words():
            parsed = noai_commands.parse(text, screen)
        if has_ai and self._not_read_whole(text, parsed):
            return None
        plan = None
        if not (parsed.intent and parsed.confidence >= noai_commands.THRESHOLD):
            # NP-11: the day of the person is counted, and a sentence nearly understood is offered back
            plan = self._offer_agenda(text) or self._offer_choices(parsed, screen, mode, has_ai)
            if plan:
                return self._deliver(conversation, text, mode, plan, plan.parsed, values)
        # A person who cannot act is not told what was not understood: the provider, or its absence, answers
        if (not self.env.user.has_group("nudge.group_nudge_agent")
                and not (parsed.intent and parsed.confidence >= noai_commands.THRESHOLD)):
            return None
        # Talk does not act: reading, counting and taking the person to a screen are done there (what the tools
        # of the person allow: the Ask tier has the reading ones only), writing is left to the Agent
        talking = mode == MODE_TALK and parsed.intent not in READ_INTENTS
        if parsed.intent and parsed.confidence >= noai_commands.THRESHOLD and not talking:
            try:
                plan = self._plan(parsed, screen, mode)
            except (UserError, Refused) as error:
                _logger.info("Nudge: a request understood without AI could not be planned: %s", error)
        if plan is None:
            if has_ai:
                return None
            plan = Plan(self._what_i_understood(parsed), parsed=parsed)
        return self._deliver(conversation, text, mode, plan, parsed, values)

    @api.model
    def _not_read_whole(self, text, parsed):
        """:return: whether the rules did not read ``text`` whole: several sentences or actions, a count with a
        plural create ("create three contacts"), or words left over in the name. The AI answers these, the
        rules only take a request they understand to the end"""
        return bool(MULTI_SENTENCE.search(text.strip()) or COUNTED_CREATE.search(text)
                    or WEBSITE.search(text) or NEGATED.search(text)
                    or len(ACTION_WORDS.findall(text)) > 1
                    or (parsed.intent in NAMED_INTENTS and self._unplaced_name(parsed)))

    @api.model
    def _unplaced_name(self, parsed):
        name = str((parsed.slots or {}).get("name") or "")
        return len(name.split()) >= MAX_NAME_WORDS or bool(ACTION_WORDS.search(name))

    @api.model
    def _serve_count(self, conversation, text, mode, snapshot):
        """``/count invoices unpaid this month``: a figure is worked out by rules and shown with the filter it
        was counted on, with no provider and no token. Only what the rules do not understand goes to the model
        (with the reading tools of the command); with none, the person is told what to write.

        :return: the job of the stream, or None to go on with the provider"""
        _command, rest = commands.parse(text)
        if not self._is_enabled() or not rest:
            return None
        values, _text = conversation._chat_screen(snapshot)
        screen = self._screen_of(values.get("snapshot") or {})
        plan = parsed = None
        with self._company_words():
            for sentence in (rest, "count " + rest):
                parsed = noai_commands.parse(sentence, screen)
                if parsed.intent == "aggregate" and parsed.confidence >= noai_commands.THRESHOLD:
                    break
        if parsed.intent == "aggregate" and parsed.confidence >= noai_commands.THRESHOLD:
            try:
                plan = self._plan(parsed, screen, mode)
            except (UserError, Refused) as error:
                _logger.info("Nudge: a count understood without AI could not be planned: %s", error)
        if plan is None:
            if self._has_ai(conversation, mode):
                return None
            plan = Plan(_(
                "I could not work this out without an AI provider. Say what to count and how, for example: "
                "/count unpaid invoices this month."), parsed=parsed)
        return self._deliver(conversation, text, mode, plan, parsed, values)

    @api.model
    def _serve_setup(self, conversation, text, mode, state, snapshot):
        """The first-setup assistant when no provider reads the answers: “set up this database with me” (or an
        answer to the question it asked) is understood by rules and shown as the same cards, one step at a time.
        With a provider, the provider leads the conversation with ``read_setup`` and ``prepare_setup_step``."""
        fresh, focus, found, background, explain = state
        if (fresh or focus or found or background or explain == "screen"
                or "prepare_setup_step" not in base.TOOLS
                or not base.TOOLS["prepare_setup_step"].is_available(self.env, mode)
                or self._has_ai(conversation, mode)):
            return None
        env = self.env(context=dict(self.env.context, nudge_conversation_id=conversation.id, nudge_mode=mode))
        current = dict(conversation.sudo().setup_state or {})
        call = None
        if current.get("active"):
            current, reply, call = setup.converse(env, current, text, conversation)
        elif setup_sentence.is_request(text):
            current, reply = setup.start(env)
        else:
            return None
        conversation.sudo().setup_state = dict(current)
        values, _text = conversation._chat_screen(snapshot)
        parsed = grammar.Parse(text)
        parsed.intent, parsed.confidence = "setup", 0.98
        plan = Plan(reply, calls=[call] if call else [], parsed=parsed)
        return self._deliver(conversation, text, mode, plan, parsed, values)

    @api.model
    def _serve_approval(self, conversation, text, mode, state, snapshot):
        """An approval rule said in a sentence (“a purchase order above 50,000 needs the finance
        manager”) in Developer mode: the flow is prepared by ``prepare_approval_flow``, which
        shows it in words and waits for a yes. Anything else goes to the provider."""
        fresh, focus, found, background, explain = state
        if (fresh or focus or found or background or explain == "screen" or not self._is_enabled()
                or "prepare_approval_flow" not in base.TOOLS
                or not base.TOOLS["prepare_approval_flow"].is_available(self.env, mode)
                or not approval_sentence.parse(text)):
            return None
        values, _text = conversation._chat_screen(snapshot)
        parsed = grammar.Parse(text)
        parsed.intent, parsed.confidence = "approval_flow", 0.98
        plan = Plan(_("I read this as an approval rule. Here is what I would build:"),
                    calls=[("prepare_approval_flow", {"sentence": text[:600]})], parsed=parsed)
        return self._deliver(conversation, text, mode, plan, parsed, values)

    @api.model
    def _serve_approval_hint(self, conversation, text, state, snapshot):
        """An approval rule said in a sentence, in Agent mode: approvals are built in Developer mode, so the
        person is told so and offered the switch, with no call to a provider. Anything else goes on."""
        fresh, focus, found, background, explain = state
        if (fresh or focus or found or background or explain == "screen" or not self._is_enabled()
                or not approval_sentence.parse(text)):
            return None
        values, _text = conversation._chat_screen(snapshot)
        parsed = grammar.Parse(text)
        parsed.intent, parsed.confidence = "approval_flow", 0.98
        if not conversation._can_develop():
            return self._deliver(conversation, text, MODE_AGENT, Plan(_(
                "I read this as an approval rule. Approval rules are built in Developer mode, which is not "
                "open to you: ask an administrator."), parsed=parsed), parsed, values)
        chip = {"name": "switch_to_developer", "words": [_("Switch to Developer mode")],
                "at_once": True, "hint": _("Build this approval rule"), "example": "",
                "kind": "switch_mode", "mode": MODE_DEV, "text": text[:600]}
        plan = Plan(_("I read this as an approval rule. Approval rules are built in Developer mode: "
                               "switch to it and I will show you the flow before anything is built."),
                    events=[{"type": "found", "commands": [chip]}], parsed=parsed)
        return self._deliver(conversation, text, MODE_AGENT, plan, parsed, values)

    # ------------------------------------------------------------------
    # Planning: the sentence on the data
    # ------------------------------------------------------------------
    # -- NP-11: what is waiting today, and "Did you mean" ---------------
    AGENDA_KEYS = ("time_off_to_approve", "overdue_invoices", "overdue_tasks", "tasks_due_today", "attendance_to_check")

    @api.model
    def _offer_agenda(self, text):
        """"What is waiting for me today": counted by Odoo as the person, from the lines of the digest, their
        activities and the approvals asked of them. No provider, no token.
        :return: the :class:`Plan`, or None when the sentence does not ask for it"""
        if not noai_commands.agenda.asks(text):
            return None
        env = self.env
        lines = []
        if "mail.activity" in env and digest.can_read(env, "mail.activity"):
            today = fields.Date.context_today(env["res.users"])
            mine = [("user_id", "=", env.uid)]
            late = env["mail.activity"].search_count(mine + [("date_deadline", "<", today)])
            due = env["mail.activity"].search_count(mine + [("date_deadline", "=", today)])
            if due:
                lines.append(_("Activities due today: %s", due))
            if late:
                lines.append(_("Activities overdue: %s", late))
        approvals = home._pending_approvals(env)
        if approvals:
            lines.append(_("Approvals waiting for you: %s", len(approvals)))
            lines += ["  - %s" % approval["text"] for approval in approvals]
        for block in digest.gather(env):
            for line in block["lines"] if block["key"] != "overview" else []:
                if line.get("key") in self.AGENDA_KEYS:
                    lines.append(digest.sentence(env, line))
        agenda = grammar.Parse(text)
        agenda.intent, agenda.confidence = "agenda", 1.0
        if not lines:
            return Plan(_("Nothing is waiting for you today."), parsed=agenda)
        return Plan("\n".join([_("Waiting for you today:")] + lines), parsed=agenda)

    @api.model
    def slip_offered(self, text):
        """For the chat before it asks how a question is wanted: whether ``text`` is a slip the rules would
        give back as “Did you mean”. It reads no record: only the words, cut short."""
        self.env["nudge.quick"]._check_user()
        text = str(text or "").strip()
        if not text or len(text) > 80:
            return False
        return bool(self._slip_offered(text))

    @api.model
    def _slip_offered(self, text):
        """:return: whether the rules would give ``text`` back as “Did you mean” with its words corrected: a
        near-miss of a command or a record kind, offered before any remembered answer is"""
        if not (self._is_enabled() and self._may_ask()):
            return False
        with self._company_words():
            parsed = noai_commands.parse(str(text or "")[:400], {})
        return (not (parsed.intent and parsed.confidence >= noai_commands.THRESHOLD)
                and any(candidate.get("corrections") for candidate in parsed.candidates or []))

    def _missing_slot(self, candidate, screen):
        """:return: the question that asks for what a sentence lacks, or "" when it has all it needs"""
        slots = candidate.get("slots") or {}
        intent, kind = candidate.get("intent"), candidate.get("kind")
        if intent == "create":
            if kind in ("contact", "product") and not slots.get("name"):
                return _("What is its name?")
            if kind in ("task", "lead", "ticket") and not slots.get("title"):
                return _("What is its title?")
            if kind == "timeoff" and not slots.get("dates"):
                return _("For which dates?")
            if kind in grammar.CREATABLE and kind not in ("contact", "product", "task", "lead", "ticket", "timeoff"):
                if not slots.get("partner"):
                    return _("For which contact?")
                if not slots.get("lines") and kind not in ("invoice", "bill", "quotation", "sales_order", "purchase_order"):
                    return _("Which products, and how many?")
        if intent in ("find", "update") and not slots.get("name") and not screen.get("record_id"):
            return _("Which one do you mean? Write its name.")
        return ""

    def _candidate_parse(self, candidate):
        """:return: the :class:`grammar.Parse` of a candidate dict, to describe it in words"""
        parsed = grammar.Parse(candidate.get("text") or "")
        parsed.intent, parsed.kind = candidate.get("intent") or "", candidate.get("kind")
        parsed.confidence = candidate.get("confidence") or 0.0
        parsed.slots = dict(candidate.get("slots") or {})
        parsed.states = list(candidate.get("states") or [])
        parsed.corrections = list(candidate.get("corrections") or [])
        return parsed

    def _offer_choices(self, parsed, screen, mode, has_ai):
        """A sentence that is not sure enough to be acted on, and that looks like something Nudge does, is
        given back as up to two sentences to choose from, each sent as it is written when it is chosen:
        nothing is done by the choice itself. What a sentence lacks is asked for.
        :return: the :class:`Plan`, or None to go on (to the AI, or to the words of what can be done)"""
        candidates = list(parsed.candidates or [])
        if not candidates:
            return None
        if has_ai and not any(candidate.get("corrections") for candidate in candidates):
            # Only a slip or Arabic in Latin letters is worth stopping the AI for
            return None
        acts = self.env.user.has_group("nudge.group_nudge_agent") and mode != MODE_TALK
        candidates = [c for c in candidates if acts or c.get("intent") in READ_INTENTS][:2]
        if not candidates:
            return None
        chosen = []
        words = []
        question = ""
        for candidate in candidates:
            described = self._describe(self._candidate_parse(candidate))
            missing = self._missing_slot(candidate, screen)
            question = question or missing
            words.append(described)
            sentence = candidate["text"]
            chosen.append({
                # Complete: it is sent as it is. Lacking a word: put in the message box, to be finished
                "name": sentence, "words": [sentence if not missing else sentence + " "], "at_once": not missing,
                "hint": described, "example": missing, "kind": "did_you_mean"})
        text = _("Did you mean: %s?", _(" or ").join("“%s”" % word for word in words))
        if question:
            text = "%s %s" % (text, question)
        guess = grammar.Parse(parsed.text)
        guess.intent, guess.confidence = "did_you_mean", parsed.confidence
        return Plan(text, events=[{"type": "found", "commands": chosen}], parsed=guess)

    def _plan(self, parsed, screen, mode):
        """:return: the :class:`Plan` for ``parsed``, or None when it cannot be done here"""
        method = getattr(self, f"_plan_{parsed.intent}", None)
        plan = method(parsed, screen, mode) if method else None
        if plan:
            plan.parsed = plan.parsed or parsed
            for name, _arguments in plan.calls:
                tool = base.TOOLS.get(name)
                if not tool or not tool.is_available(self.env, mode):
                    return None
        return plan

    def _call(self, name, arguments, screen=None, mode=MODE_AGENT):
        """Run a tool as the person: what it reads comes back as data"""
        if mode == MODE_AGENT and name in base.ASK_TOOLS and not self.env.user.has_group("nudge.group_nudge_agent"):
            # NP-02: what a person without the Agent group reads runs as Talk: the Ask tier is offered there only
            mode = MODE_TALK
        result = base.call(self.env, name, arguments, screen, mode)
        return result

    def _json(self, result):
        try:
            return json.loads(result.content)
        except ValueError:
            return None

    def _model_of(self, kind):
        spec = KINDS.get(kind or "")
        if not spec or spec["model"] not in self.env:
            return None
        return spec["model"]

    def _plural_of(self, kind, model_name):
        """:return: what a kind of record is called in the plural, for counts and lists"""
        names = {
            "contact": _("contacts"), "product": _("products"),
            "quotation": _("quotations"), "sales_order": _("sales orders"),
            "sales": _("sales orders"), "purchase_order": _("purchase orders"),
            "invoice": _("invoices"), "bill": _("vendor bills"), "task": _("tasks"),
            "lead": _("leads"), "ticket": _("tickets"), "timeoff": _("time off"),
            "employee": _("employees"),
        }
        return names.get(kind) or self._label_of(model_name)

    def _label_of(self, model_name):
        return self.env["ir.model"].sudo()._get(model_name).name or model_name

    def _today(self):
        return fields.Date.context_today(self)

    # -- who and what ------------------------------------------------
    def _pick(self, name, model_name=None, topic=None, screen=None):
        """:return: ``(status, candidates)``: "one" with the record, "many" with the
        candidates, "none"; the same words are tried again without a leading "ل" """
        tries = [name]
        if name[:1] == "ل" and len(name) > 3:
            tries.append(name[1:])
        # Of a record whose kind is not said, a person is the likeliest
        trials = [(words, model_name) for words in tries] + (
            [(words, "res.partner") for words in tries] if not model_name else [])
        for words, model_hint in trials:
            arguments = {"name": words}
            if model_hint:
                arguments["model"] = model_hint
            elif topic:
                arguments["topic"] = topic
            result = self._call("find_record", arguments, screen)
            data = self._json(result) if not result.is_error else None
            candidates = (data or {}).get("candidates") or []
            if not candidates:
                continue
            exact = [c for c in candidates if (c.get("name") or "").casefold() == words.casefold()]
            if len(exact) == 1:
                return "one", exact
            if len(candidates) == 1:
                return "one", candidates
            return "many", candidates[:MAX_CHOICES]
        return "none", []

    def _choice(self, candidates, text):
        """A card of the records to choose among: which one is asked, never guessed"""
        records = [{
            "model": c["model"], "id": c["id"], "name": c["name"], "kind": c.get("kind") or "",
            "state": c.get("state") or "",
        } for c in candidates]
        return Plan(text, events=[{"type": "found", "records": records}])

    # -- date conditions ---------------------------------------------
    def _period_conditions(self, parsed, field):
        if not parsed.period or not field:
            return []
        span = grammar.period_range(parsed.period, self._today())
        if not span:
            return []
        return [
            {"field": field, "operator": ">=", "value": span[0].isoformat()},
            {"field": field, "operator": "<", "value": span[1].isoformat()},
        ]

    def _selection_keys(self, model_name, field):
        field = self.env[model_name]._fields.get(field)
        if not field or field.type != "selection":
            return set()
        return {key for key, _label in field._description_selection(self.env)}

    def _state_conditions(self, kind, state, model_name):
        """:return: the conditions of a state for a kind of record, or None when
        the kind has no such state"""
        today = self._today().isoformat()
        if model_name == "account.move":
            unpaid = [{"field": "state", "operator": "=", "value": "posted"},
                      {"field": "payment_state", "operator": "in", "value": "not_paid,partial"}]
            return {
                "unpaid": unpaid,
                "overdue": unpaid + [{"field": "invoice_date_due", "operator": "<", "value": today}],
                "paid": [{"field": "payment_state", "operator": "in", "value": "paid,in_payment"}],
                "draft": [{"field": "state", "operator": "=", "value": "draft"}],
                "posted": [{"field": "state", "operator": "=", "value": "posted"}],
                "confirmed": [{"field": "state", "operator": "=", "value": "posted"}],
                "cancelled": [{"field": "state", "operator": "=", "value": "cancel"}],
            }.get(state)
        wanted = {
            "draft": ("draft", "sent"), "confirmed": ("sale", "done", "purchase"), "cancelled": ("cancel",),
        }.get(state)
        if wanted and "state" in self.env[model_name]._fields:
            keys = [key for key in wanted if key in self._selection_keys(model_name, "state")]
            if keys:
                return [{"field": "state", "operator": "in" if len(keys) > 1 else "=", "value": ",".join(keys)}]
        return None

    def _conditions(self, parsed, kind, model_name):
        """:return: the conditions for the kind, the states and the period said, or None
        when one of them means nothing for that kind"""
        spec = KINDS.get(kind or "", {})
        conditions = []
        if spec.get("move_types"):
            conditions.append({"field": "move_type", "operator": "in", "value": ",".join(spec["move_types"])})
        elif spec.get("states") and not parsed.states:
            keys = [key for key in spec["states"] if key in self._selection_keys(model_name, "state")]
            if keys:
                conditions.append({"field": "state", "operator": "in", "value": ",".join(keys)})
        for state in parsed.states:
            found = self._state_conditions(kind, state, model_name)
            if found is None:
                return None
            conditions.extend(found)
        date_field = spec.get("date") or "create_date"
        if date_field not in self.env[model_name]._fields:
            date_field = "create_date"
        conditions.extend(self._period_conditions(parsed, date_field))
        return conditions

    def _left_name(self, parsed):
        """:return: the words of the sentence that name someone or something, once the kind, the states, the
        period and the verbs are taken out: "Raghad" in “how many invoices for Raghad”"""
        # A word read as another ("qoutations" as quotation) is that word here too, never a name left over
        text = parsed.text
        for fix in getattr(parsed, "corrections", None) or []:
            text = re.sub(r"(?<!\w)%s(?!\w)" % re.escape(fix["word"]), lambda _m, to=fix["to"]: to, text)
        spans = grammar.scan(grammar.normalise.tokenize(text))
        return grammar._name_of(spans).strip()

    def _what_is_said(self, parsed):
        """:return: the states and the period said, in words"""
        words = {
            "unpaid": _("unpaid"), "paid": _("paid"), "overdue": _("overdue"),
            "draft": _("draft"), "confirmed": _("confirmed"), "posted": _("posted"),
            "cancelled": _("cancelled"),
        }
        parts = [words[state] for state in parsed.states]
        if parsed.period:
            parts.append(self._period_words(parsed.period))
        return ", ".join(parts)

    def _period_words(self, spec):
        if spec[0] == "rel":
            return {
                "today": _("today"), "yesterday": _("yesterday"),
                "tomorrow": _("tomorrow"), "this_week": _("this week"),
                "last_week": _("last week"), "this_month": _("this month"),
                "last_month": _("last month"), "next_month": _("next month"),
                "this_year": _("this year"), "last_year": _("last year"),
            }.get(spec[1], "")
        start, end = grammar.period_range(spec, self._today()) or (None, None)
        if not start:
            return ""
        if spec[0] == "year":
            return str(start.year)
        return start.strftime("%B %Y")

    # -- intents -------------------------------------------------------
    def _plan_open(self, parsed, screen, mode):
        words = parsed.slots.get("words") or ""
        quick = self.env["nudge.quick"]
        found = {}
        queries = [words]
        spec = KINDS.get(parsed.kind or "")
        if spec:
            queries.append(spec["screen"])
        for query in queries:
            for rank, item in quick._scored_screens(query):
                if item["menu_id"] not in found or rank < found[item["menu_id"]][0]:
                    found[item["menu_id"]] = (rank, item)
        ranked = sorted(found.values(), key=lambda pair: pair[0])
        if not ranked:
            name = parsed.slots.get("name") or ""
            if name and not parsed.kind and mode != MODE_TALK:
                found_plan = self._plan_find(parsed, screen, mode)
                if found_plan:
                    return found_plan
            return None
        best = [item for rank, item in ranked if rank == ranked[0][0]]
        model_name = self._model_of(parsed.kind)
        if len(best) > 1 and model_name:
            same = [item for item in best if item.get("model") == model_name]
            if same:
                best = same
        if len(best) > 1:
            same = {item["path"]: item for item in best}
            best = list(same.values())
        if len(best) == 1:
            item = best[0]
            return Plan(
                _("Opening %s.", item["path"]),
                [("open_screen", {"menu_id": item["menu_id"], "text": _("Opening %s.", item["path"])})])
        menus = [{"id": item["menu_id"], "path": item["path"]} for item in best[:MAX_CHOICES]]
        return Plan(_("Which screen?"), events=[{"type": "found", "menus": menus}])

    def _plan_find(self, parsed, screen, mode):
        name = parsed.slots.get("name") or ""
        kind = parsed.kind
        model_name = self._model_of(kind)
        if parsed.slots.get("latest") and model_name:
            return self._plan_latest(parsed, name, kind, model_name, screen)
        if not name:
            return self._plan_open(grammar.Parse(parsed.text), screen, mode) if False else None
        status, candidates = self._pick(name, model_name, None, screen)
        if status == "none":
            return Plan(_("I found nothing called “%s”.", name))
        if status == "many":
            return self._choice(candidates, _("Which one is “%s”?", name))
        record = candidates[0]
        text = _("Opening %s.", record["name"])
        return Plan(text, [("open_record", {"model": record["model"], "id": record["id"], "text": text})])

    def _plan_latest(self, parsed, name, kind, model_name, screen):
        """The most recent record of a kind, of someone when a name is said"""
        spec = KINDS[kind]
        conditions = self._conditions(grammar.Parse(parsed.text), kind, model_name) or []
        if name and spec.get("party"):
            status, candidates = self._pick(name, "res.partner", None, screen)
            if status == "none":
                return Plan(_("I found nothing called “%s”.", name))
            if status == "many":
                return self._choice(candidates, _("Which one is “%s”?", name))
            conditions.append({"field": spec["party"], "operator": "=", "value": str(candidates[0]["id"])})
        result = self._call("search_records", {
            "model": model_name, "conditions": conditions, "order": "id desc", "limit": 1,
        }, screen)
        data = self._json(result) if not result.is_error else None
        rows = (data or {}).get("records") or (data or {}).get("rows") or []
        if not rows:
            return Plan(_("I found no %s.", self._plural_of(kind, model_name)))
        row = rows[0]
        label = row.get("display_name") or row.get("name") or str(row.get("id"))
        text = _("Opening %s.", label)
        return Plan(text, [("open_record", {"model": model_name, "id": row["id"], "text": text})])

    def _plan_filter(self, parsed, screen, mode):
        kind = parsed.kind
        model_name = self._model_of(kind) or (screen.get("model") if screen.get("model") in self.env else None)
        if not model_name:
            return None
        conditions = self._conditions(parsed, kind if self._model_of(kind) else None, model_name)
        if not conditions:
            return None
        said = self._what_is_said(parsed)
        text = _("Showing %(what)s: %(said)s.", what=self._plural_of(kind, model_name), said=said)
        return Plan(text, [("filter_screen", {
            "model": model_name, "conditions": conditions, "text": text,
        })])

    def _plan_aggregate(self, parsed, screen, mode):
        kind = parsed.kind
        model_name = self._model_of(kind) or (screen.get("model") if screen.get("model") in self.env else None)
        if not model_name:
            return None
        spec = KINDS.get(kind or "", {})
        conditions = self._conditions(parsed, kind if self._model_of(kind) else None, model_name)
        if conditions is None:
            return None
        # NP-02: "invoices of Raghad": the person named narrows the figure, else it would be the total of all
        name = self._left_name(parsed)
        if name:
            if not spec.get("party"):
                return None
            status, candidates = self._pick(name, "res.partner", None, screen)
            if status == "many":
                return self._choice(candidates, _("Which one is “%s”?", name))
            if status == "none":
                return None
            conditions.append({"field": spec["party"], "operator": "=", "value": str(candidates[0]["id"])})
        measure = parsed.slots["measure"]
        field = spec.get("amount")
        if measure != "count" and (not field or field not in self.env[model_name]._fields):
            return None
        measures = ["count"] if measure == "count" else [f"{field}:{measure}"]
        result = self._call("aggregate_records", {
            "model": model_name, "conditions": conditions, "measures": measures,
        }, screen)
        data = self._json(result) if not result.is_error else None
        rows = (data or {}).get("rows") or []
        if not rows:
            return Plan(result.content if result.is_error else _("I found nothing to count."))
        # NP-02: the filter the figure was counted on, and the list behind it
        showing = (data or {}).get("showing")
        events = [result.event] if result.event else []
        value = self._measure_value(rows[0], measures[0])
        label = self._plural_of(kind, model_name)
        said = self._what_is_said(parsed)
        suffix = f" ({said})" if said else ""
        if measure == "count":
            text = _("%(count)s %(what)s%(said)s.", count=self._number(value), what=label, said=suffix)
        elif measure == "sum":
            text = _("The total of %(what)s%(said)s: %(value)s.", what=label, said=suffix,
                              value=self._number(value))
        else:
            text = _("The average of %(what)s%(said)s: %(value)s.", what=label, said=suffix,
                              value=self._number(value))
        if showing:
            text = "%s %s" % (text, _("Showing: %s.", showing))
        return Plan(text, events=events)

    # What a chart is split by: the fields of a model that can say it, the first one it has
    CHART_FIELDS = {
        "by_user": ("user_id", "invoice_user_id", "employee_id", "create_uid"),
        "by_partner": ("partner_id",),
        "by_product": ("product_id", "product_tmpl_id"),
        "by_state": ("state",),
    }
    CHART_INTERVALS = {"by_month": "month", "by_year": "year", "by_week": "week"}

    def _plan_chart(self, parsed, screen, mode):
        kind = parsed.kind
        model_name = self._model_of(kind) or (screen.get("model") if screen.get("model") in self.env else None)
        if not model_name:
            return None
        spec = KINDS.get(kind or "", {})
        model = self.env[model_name]
        conditions = self._conditions(parsed, kind if self._model_of(kind) else None, model_name)
        if conditions is None:
            return None
        group = parsed.slots.get("group") or "by_month"
        if group in self.CHART_INTERVALS:
            date_field = spec.get("date") if spec.get("date") in model._fields else "create_date"
            group_by = [f"{date_field}:{self.CHART_INTERVALS[group]}"]
        else:
            name = next((f for f in self.CHART_FIELDS[group] if f in model._fields), None)
            if not name:
                return Plan(_("I cannot split %(what)s by that.", what=self._plural_of(kind, model_name)))
            group_by = [name]
        measure = parsed.slots.get("measure")
        amount = spec.get("amount")
        measure_spec = "count" if measure == "count" or not amount or amount not in model._fields else (
            f"{amount}:{'avg' if measure == 'avg' else 'sum'}")
        arguments = {"model": model_name, "conditions": conditions, "group_by": group_by, "measure": measure_spec}
        if parsed.slots.get("chart_type"):
            arguments["chart_type"] = parsed.slots["chart_type"]
        result = self._call("show_chart", arguments, screen)
        if result.is_error or not result.event:
            return Plan(result.content)
        said = self._what_is_said(parsed)
        said = f" ({said})" if said else ""
        text = _("Here is the chart: %(title)s%(said)s.", title=result.event["title"], said=said)
        return Plan(text, events=[result.event])

    # The periods a saved report keeps moving with the day; any other period is kept as fixed dates
    REPORT_PERIODS = ("this_month", "last_month", "this_year", "last_year")

    def _plan_save_report(self, parsed, screen, mode):
        """Keep what the sentence draws, to open again: the same slots as a chart, any number of
        groups, and the pivot as a shape. The card shows it in words; the yes makes it."""
        kind = parsed.kind
        model_name = self._model_of(kind) or (screen.get("model") if screen.get("model") in self.env else None)
        if not model_name:
            return None
        spec = KINDS.get(kind or "", {})
        model = self.env[model_name]
        period = parsed.period[1] if parsed.period and parsed.period[0] == "rel" and (
            parsed.period[1] in self.REPORT_PERIODS) else None
        fixed = copy.copy(parsed)
        if period:
            fixed.period = None
        conditions = self._conditions(fixed, kind if self._model_of(kind) else None, model_name)
        if conditions is None:
            return None
        date_field = spec.get("date") if spec.get("date") in model._fields else "create_date"
        group_by = []
        for group in parsed.slots.get("groups") or ["by_month"]:
            if group in self.CHART_INTERVALS:
                group_by.append(f"{date_field}:{self.CHART_INTERVALS[group]}")
                continue
            name = next((f for f in self.CHART_FIELDS[group] if f in model._fields), None)
            if not name:
                return Plan(_("I cannot split %(what)s by that.", what=self._plural_of(kind, model_name)))
            group_by.append(name)
        measure = parsed.slots.get("measure")
        amount = spec.get("amount")
        measure_spec = "count" if measure == "count" or not amount or amount not in model._fields else (
            f"{amount}:{'avg' if measure == 'avg' else 'sum'}")
        view = parsed.slots.get("shape") or ("line" if group_by[0].partition(":")[2] and len(group_by) == 1 else "bar")
        if view in ("bar", "line", "pie") and len(group_by) > 2:
            view = "pivot"
        arguments = {"model": model_name, "conditions": conditions, "group_by": group_by,
                     "measures": [measure_spec], "view": view}
        if period:
            arguments.update(period=period, period_field=date_field)
        said = self._what_is_said(parsed)
        said = f" ({said})" if said else ""
        text = _("Preparing to save the report: %(what)s%(said)s.", what=self._plural_of(kind, model_name),
                          said=said)
        return Plan(text, [("save_report", arguments)])

    def _measure_value(self, row, measure):
        if measure in row:
            return row[measure]
        for key, value in row.items():
            if key == measure or key.startswith(measure.split(":")[0]) or key in ("count", "__count"):
                if isinstance(value, (int, float)):
                    return value
        numbers = [value for value in row.values() if isinstance(value, (int, float))]
        return numbers[0] if numbers else 0

    def _number(self, value):
        value = float(value or 0)
        return f"{value:,.0f}" if value == int(value) else f"{value:,.2f}"

    def _plan_create(self, parsed, screen, mode):
        kind = parsed.kind
        slots = parsed.slots
        model_name = self._model_of(kind)
        if not model_name or kind in ("invoice", "bill", "sales", "employee"):
            return None
        label = self._label_of(model_name)
        if kind == "contact":
            values = [("name", slots["name"])]
            if slots.get("phone"):
                values.append(("phone", slots["phone"]))
            if slots.get("email"):
                values.append(("email", slots["email"]))
            return self._create_plan(model_name, values, [], label)
        if kind == "product":
            values = [("name", slots["name"])]
            if slots.get("price") is not None:
                values.append(("list_price", self._number_text(slots["price"])))
            return self._create_plan(model_name, values, [], label)
        if kind in ("task", "lead", "ticket"):
            return self._create_plan(model_name, [("name", slots["title"])], [], label)
        if kind in ("quotation", "sales_order", "purchase_order"):
            return self._plan_create_order(parsed, model_name, label, screen)
        if kind == "timeoff":
            return self._plan_create_leave(parsed, model_name, label, screen)
        return None

    def _number_text(self, value):
        return str(int(value)) if float(value) == int(value) else str(value)

    def _create_plan(self, model_name, values, lines, label):
        text = _("Preparing to create: %s.", label)
        arguments = {"model": model_name, "values": [{"field": f, "value": v} for f, v in values]}
        if lines:
            arguments["lines"] = lines
        return Plan(text, [("prepare_create", arguments)])

    def _plan_create_order(self, parsed, model_name, label, screen):
        slots = parsed.slots
        status, candidates = self._pick(slots["partner"], "res.partner", None, screen)
        if status == "none":
            return Plan(_("I found no contact called “%s”.", slots["partner"]))
        if status == "many":
            return self._choice(candidates, _("Which contact is “%s”?", slots["partner"]))
        partner = candidates[0]
        quantity_field = "product_qty" if model_name == "purchase.order" else "product_uom_qty"
        lines = []
        for line in slots["lines"]:
            product_status, products = self._pick(line["product"], "product.template", None, screen)
            if product_status == "none":
                return Plan(_("I found no product called “%s”.", line["product"]))
            if product_status == "many":
                return self._choice(products, _("Which product is “%s”?", line["product"]))
            template = self.env["product.template"].browse(products[0]["id"])
            if len(template.product_variant_ids) != 1:
                return Plan(_("“%s” has several variants: say which one.", template.display_name))
            lines.append({"field": "order_line", "values": [
                {"field": "product_id", "value": str(template.product_variant_id.id)},
                {"field": quantity_field, "value": self._number_text(line["qty"])},
            ]})
        return self._create_plan(model_name, [("partner_id", str(partner["id"]))], lines, label)

    def _plan_create_leave(self, parsed, model_name, label, screen):
        today = self._today()
        specs = parsed.slots.get("dates") or []
        days = [grammar.date_of(spec, today) for spec in specs]
        days = [day for day in days if day]
        if not days:
            return None
        start, end = days[0], days[-1]
        types = self.env["hr.leave.type"].search([])
        words = grammar.normalise.fold(parsed.slots.get("words") or "")
        chosen = types.filtered(lambda t: t.name and grammar.normalise.fold(t.name) in words) if words else types.browse()
        chosen = chosen or (types if len(types) == 1 else types.browse())
        if len(chosen) != 1:
            names = ", ".join(types.mapped("name")[:MAX_CHOICES])
            return Plan(_("Which kind of time off? One of: %s.", names))
        values = [("holiday_status_id", str(chosen.id)), ("request_date_from", start.isoformat()),
                  ("request_date_to", end.isoformat())]
        return self._create_plan(model_name, values, [], label)

    def _find_target(self, parsed, screen):
        """:return: ``(status, candidates)`` of the record a sentence is about: the one on
        screen when it says "this", or by its name"""
        slots = parsed.slots
        if slots.get("current") and screen.get("record_id") and screen.get("model"):
            return "one", [{"model": screen["model"], "id": screen["record_id"], "name": "", "buttons": [
                {"name": b.get("name"), "label": b.get("label")} for b in screen["buttons"]]}]
        if not slots.get("name"):
            return "none", []
        return self._pick(slots["name"], self._model_of(parsed.kind), None, screen)

    def _plan_update(self, parsed, screen, mode):
        slots = parsed.slots
        status, candidates = self._find_target(parsed, screen)
        if status == "none":
            return Plan(_("I found nothing called “%s”.", slots.get("name") or ""))
        if status == "many":
            return self._choice(candidates, _("Which one is “%s”?", slots["name"]))
        record = candidates[0]
        model = self.env[record["model"]]
        field_name = next((name for name in FIELD_NAMES[slots["field"]] if name in model._fields), None)
        if not field_name:
            return Plan(_("%(what)s has no such field.", what=self._label_of(record["model"])))
        value = slots["value"]
        if model._fields[field_name].type in ("date", "datetime"):
            specs = grammar.date_specs(slots.get("value_spans") or [])
            day = grammar.date_of(specs[0], self._today()) if specs else None
            if not day:
                return None
            value = day.isoformat()
        text = _("Preparing to change %(field)s of %(name)s.", field=model._fields[field_name].string,
                          name=record.get("name") or model.browse(record["id"]).display_name)
        return Plan(text, [("prepare_write", {
            "model": record["model"], "ids": [record["id"]], "values": [{"field": field_name, "value": value}],
        })])

    def _plan_history(self, parsed, screen, mode):
        return self._plan_changes(parsed, screen, mode, restore=False)

    def _plan_revert(self, parsed, screen, mode):
        return self._plan_changes(parsed, screen, mode, restore=True)

    def _plan_changes(self, parsed, screen, mode, restore):
        """Who changed a record, or put a field of it back as it was. The record is the one on the
        screen or the one named; what Odoo tracked is read by the tools, as the person."""
        slots = parsed.slots
        status, candidates = self._find_target(parsed, screen)
        if status == "none":
            if not slots.get("name"):
                return Plan(_("Open the record first, or tell me its name."))
            return Plan(_("I found nothing called “%s”.", slots["name"]))
        if status == "many":
            return self._choice(candidates, _("Which one is “%s”?", slots["name"]))
        record = candidates[0]
        model = self.env[record["model"]]
        arguments = {"model": record["model"], "id": record["id"]}
        label = record.get("name") or model.browse(record["id"]).display_name
        if slots.get("field"):
            field_name = next((name for name in FIELD_NAMES[slots["field"]] if name in model._fields), None)
            if not field_name:
                return Plan(_("%(what)s has no such field.", what=self._label_of(record["model"])))
            arguments["field"] = field_name
        if restore and arguments.get("field"):
            text = _("Preparing to restore the old value of %(field)s of %(name)s.",
                              field=model._fields[arguments["field"]].string, name=label)
            return Plan(text, [("prepare_revert", arguments)])
        span = grammar.period_range(parsed.period, self._today()) if parsed.period else None
        if span:
            arguments["since"] = span[0].isoformat()
            arguments["until"] = (span[1] - datetime.timedelta(days=1)).isoformat()
        return Plan(_("Here are the changes of %(name)s.", name=label), [("record_changes", arguments)])

    @api.model
    def _serve_changes(self, conversation, text, mode, snapshot):
        """The buttons of the list of changes say ``/restore change 12`` or ``/restore message 7``; the
        button of the panel says ``/changes``. Understood without any AI, in Agent mode only.

        :return: the job of the stream, or None when ``text`` is none of these"""
        match = revert.RESTORE_COMMAND.match(text.strip())
        listing = revert.CHANGES_COMMAND.match(text.strip())
        if not (match or listing) or mode != MODE_AGENT:
            return None
        values, _text = conversation._chat_screen(snapshot)
        screen = self._screen_of(values.get("snapshot") or {})
        parsed = grammar.Parse(text)
        parsed.intent, parsed.confidence = ("revert" if match else "history"), 0.98
        if match:
            model_name, record_id = revert.target_of(self.env, match.group(1), match.group(2))
            if not model_name:
                plan = Plan(_("That change is not there any more."), parsed=parsed)
            else:
                key = "change_id" if match.group(1) == "change" else "message_id"
                plan = Plan(_("Preparing to restore the old value."), parsed=parsed, calls=[(
                    "prepare_revert", {"model": model_name, "id": record_id, key: int(match.group(2))})])
        elif screen["model"] and screen["record_id"]:
            plan = Plan(_("Here are the changes of this record."), parsed=parsed, calls=[(
                "record_changes", {"model": screen["model"], "id": screen["record_id"]})])
        else:
            plan = Plan(_("Open the record first, or tell me its name."), parsed=parsed)
        for name, _arguments in plan.calls:
            tool = base.TOOLS.get(name)
            if not tool or not tool.is_available(self.env, mode):
                return None
        return self._deliver(conversation, text, mode, plan, parsed, values)

    def _plan_press(self, parsed, screen, mode):
        slots = parsed.slots
        status, candidates = self._find_target(parsed, screen)
        if status == "none":
            if not slots.get("name"):
                return Plan(_("Open the record first, or say its name."))
            return Plan(_("I found nothing called “%s”.", slots["name"]))
        if status == "many":
            return self._choice(candidates, _("Which one is “%s”?", slots["name"]))
        record = candidates[0]
        button = self._button_for(slots["button"], record.get("buttons") or [])
        if isinstance(button, list):
            names = ", ".join(b.get("label") or b.get("name") for b in button[:MAX_CHOICES])
            return Plan(_("Which button: %s?", names))
        if not button:
            shown = ", ".join(b.get("label") or b.get("name") for b in (record.get("buttons") or [])[:MAX_CHOICES])
            return Plan(_("This record has no such button. Its buttons: %s.", shown or "-"))
        label = button.get("label") or button["name"]
        text = _("Preparing to press “%s”.", label)
        return Plan(text, [("call_method", {
            "model": record["model"], "ids": [record["id"]], "method": button["name"],
        })])

    def _button_for(self, verb, buttons):
        """:return: the button a verb presses among ``buttons``, a list when several
        could be, None when none"""
        methods = BUTTONS[verb]
        wanted = {tuple(token.stem for token in grammar.normalise.tokenize(phrase))
                  for phrase in self._verb_phrases(verb)}
        by_name = [b for b in buttons if b.get("name") in methods]
        if by_name:
            return by_name[0] if len(by_name) == 1 else by_name
        by_label = []
        for button in buttons:
            label = tuple(token.stem for token in grammar.normalise.tokenize(button.get("label") or ""))
            if label in wanted or any(token in {w[0] for w in wanted if len(w) == 1} for token in label):
                by_label.append(button)
        if by_label:
            return by_label[0] if len(by_label) == 1 else by_label
        return None

    def _verb_phrases(self, verb):
        keys = noai_commands.lexicon.phrases_of(verb)
        return [" ".join(key) for key in keys]

    # ------------------------------------------------------------------
    # Telling the person
    # ------------------------------------------------------------------
    def _describe(self, parsed):
        """:return: what a sentence asks, in a few words"""
        intent, slots = parsed.intent, parsed.slots
        kind = KINDS.get(parsed.kind or "")
        what = self._label_of(kind["model"]) if kind and kind["model"] in self.env else ""
        said = self._what_is_said(parsed)
        if intent == "open":
            words = str(slots.get("words") or "")
            # A slip corrected: the chip names the screen as written, "Sales Orders", not the typed words
            return _("Open: %s", words.title() if words and parsed.corrections else words or what)
        if intent == "find":
            return _("Find: %s", " ".join(part for part in (what, slots.get("name") or "") if part))
        if intent == "filter":
            return _("Filter: %s", " ".join(part for part in (what, said) if part))
        if intent == "aggregate":
            return {
                "count": _("Count: %s"), "sum": _("Total: %s"), "avg": _("Average: %s"),
            }[slots["measure"]] % " ".join(part for part in (what, said) if part)
        if intent == "chart":
            return _("Chart: %s", " ".join(part for part in (what, said) if part))
        if intent == "save_report":
            return _("Save a report: %s", " ".join(part for part in (what, said) if part))
        if intent == "create":
            return _("Create: %s", " ".join(
                part for part in (what, slots.get("name") or slots.get("partner") or slots.get("title") or "") if part))
        if intent == "update":
            return _("Change: %s", " ".join(part for part in (slots.get("name") or "", slots["field"]) if part))
        if intent == "press":
            return _("Press a button: %s", slots.get("name") or "")
        if intent == "history":
            return _("Who changed: %s", " ".join(part for part in (slots.get("name") or "", slots.get("field") or "") if part))
        if intent == "revert":
            return _("Put back: %s", " ".join(part for part in (slots.get("name") or "", slots.get("field") or "") if part))
        return parsed.text

    def _what_i_understood(self, parsed):
        """:return: one line on what was understood and what can be done without AI"""
        if parsed.intent:
            understood = _("I understood: %s.", self._describe(parsed))
        else:
            understood = _("I did not understand this request.")
        if not self.env.user.has_group("nudge.group_nudge_agent"):
            return "%s %s" % (understood, _(
                "Without an AI provider I can open screens, find a record, filter a list, and count and sum. "
                "For example: “open invoices”, “how many overdue invoices”."))
        return "%s %s" % (understood, _(
            "Without an AI provider I can open screens, find a record, filter a list, count and sum, "
            "create a contact, a product, a quotation, a task or a time off, change one field and press "
            "a button. For example: “open invoices”, “how many overdue invoices”."))

    def _deliver(self, conversation, text, mode, plan, parsed, values):
        """Write the question, run the tools of the plan through the pipeline of the
        agent, and return the job of the stream"""
        record = conversation.sudo()
        record._agent_leave_undecided()
        record.run_id._end(record.run_id._outcome("stopped"))
        record.write({"stream_started_at": fields.Datetime.now(), "run_id": False})
        if not record.name:
            record.name = text[:60].strip()
        Message = self.env["nudge.message"].sudo()
        question = Message.create(dict({
            "conversation_id": conversation.id, "role": "user", "content": text, "mode": mode,
        }, **values))
        if plan.calls and mode != MODE_TALK:
            record.run_id = self.env["nudge.run"].sudo().create({
                "name": text[:120].strip(), "conversation_id": conversation.id, "question_id": question.id,
                "user_id": self.env.uid, "company_id": self.env.company.id, "model_name": "-",
                "is_developer": mode == MODE_DEV,
            })
        calls = [{"id": f"noai_{index}", "name": name, "input": arguments, "extra": None}
                 for index, (name, arguments) in enumerate(plan.calls)]
        # A tool that shows something says it itself, on the screen
        told = "" if calls and all(base.TOOLS[name].terminal for name, _arguments in plan.calls) else plan.text
        outcome = {
            "text": told, "tool_calls": calls, "last_round": True, "noai": True,
            "stop": {"reason": llm.STOP_TOOL_USE if calls else llm.STOP_END, "usage": {}, "blocks": []},
        }
        step = conversation._chat_record(outcome)
        answer = step["event"]
        message = Message.browse(answer.get("message_id")) if answer.get("message_id") else Message
        if calls and answer.get("type") != "waiting":
            # Refused by the tool, in words: said as it is, not as a success
            failed = record.run_id.step_ids.filtered(lambda s: s.state == "failed")[-1:]
            if failed and failed.error:
                told = plan.text = failed.error.split(" Nothing was")[0][:600]
                message.content = told
        events = [{"type": "delta", "text": told}] if told else []
        events.extend(plan.events)
        events.extend(step.get("screen_events") or [])
        if message:
            message.write({"noai_intent": parsed.intent or "unknown"})
            if plan.events:
                message.screen_events = (message.screen_events or []) + list(plan.events)
        if parsed.intent != "bulk":
            self.env["nudge.usage"]._add(
                "noai", dict(NO_TOKENS), 0.0, self.env.user, self.env.company, intent=parsed.intent or "unknown",
                conversation_id=conversation.id, message_id=message.id if message else False)
        return {
            "conversation_id": conversation.id,
            "user_message_id": question.id,
            "title": record.name,
            "mode": mode,
            "first_events": events,
            "answered": answer,
        }

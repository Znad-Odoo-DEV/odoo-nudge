import base64
import json
import logging
import time

from odoo import api, fields, models
from odoo.exceptions import AccessError, LockError, UserError
from odoo.tools import config

from .. import tools
from ..llm import base, prompt
from ..llm import attachments, ollama
from ..llm import snapshot as screen
from ..profile import retrieval
from .ir_http import nudge_version
from ..tools.batches import in_batches
from ..tools.deadline import until
from ..tools.base import MODE_AGENT, MODE_DEV, MODE_TALK

_logger = logging.getLogger(__name__)

# How the person wants the answer, as the model reads it
REPORT_WORDS = {"sheet": "spreadsheet", "chat": "in the chat"}
EXPLAIN_WORDS = {"screen": "on the screen", "text": "in writing"}

MAX_MESSAGE_CHARS = 8000
MAX_MESSAGES = 120
TITLE_CHARS = 60
# Times the model may be called for one question: once to answer, and again
# each time it has used a tool whose result it needs to read
MAX_ROUNDS = 4
# Doing takes more steps than telling: look up, look up, act, act, tell
AGENT_MAX_ROUNDS = 8
AGENT_MAX_MESSAGES = 240
# Things Nudge may do for one request, however many times the person says yes
AGENT_MAX_STEPS = 25
# In the background there is time for more
BACKGROUND_MAX_STEPS = 60

STREAM_MAX_SECONDS = 90
STREAM_MIN_SECONDS = 15
STREAM_SAFETY_MARGIN = 30
# Answers streamed at the same time, for everybody together: one answer holds
# one http worker until it is done. 0 is automatic: the workers of the server
# less two, so that the screens of everyone else keep being served
MAX_STREAMS_PARAM = "nudge.max_streams"
# A server without workers (threaded) serves many requests at once: a cap all the same
THREADED_MAX_STREAMS = 8
# NP-10: what the model standing in is asked for: the provider could not answer at all. Not a
# request it refused, nor one too large for it: the one standing in would do the same
FALLBACK_ERRORS = frozenset({
    base.ERROR_AUTH, base.ERROR_WORKSPACE, base.ERROR_MODEL_NOT_FOUND, base.ERROR_RATE_LIMIT,
    base.ERROR_UNAVAILABLE, base.ERROR_NETWORK, base.ERROR_TIMEOUT, base.ERROR_MISSING_LIBRARY,
    base.ERROR_UNKNOWN,
})

USER_WRITABLE_FIELDS = frozenset({"name", "active", "summary_text"})
# NP-22: the bookkeeping of the summary is written by Nudge, nobody else, a manager included
SUMMARY_BOOKKEEPING = frozenset({
    "summary_extra", "summary_upto_id", "summary_count", "summary_edited", "summary_ai", "summary_dirty"})

SCREEN_SHARING_PARAM = "nudge.screen_sharing"
# What a question says when its screen is the one of the question before it
UNCHANGED_SCREEN = screen.render(None, unchanged=True)
AGENT_PARAM = "nudge.agent"
# Whether the agent may compute with Python; off until an administrator says so
PYTHON_PARAM = "nudge.python"
# Whether administrators may read the database with SQL; off until one says so
SQL_PARAM = "nudge.sql"
# Users, groups, companies, menus, views, actions, crons, parameters: for
# administrators, when the administrator says so
ADMIN_PARAM = "nudge.admin"
DEVELOPER_PARAM = "nudge.developer"
AGENT_GROUP = "nudge.group_nudge_agent"

STATE_BY_STOP_REASON = {
    base.STOP_END: "complete",
    base.STOP_MAX_TOKENS: "truncated",
    base.STOP_DEADLINE: "truncated",
    base.STOP_REFUSAL: "refused",
}

CONFIRM = "confirm"
DECLINE = "decline"
# A plan: only the records the rehearsal found going through
PASSING = "passing"

DONE = (
    "The person said yes, and it is done. %s Tell them in a sentence what was done, or go on "
    "with what is left of what they asked."
)
# A yes to one step of what was asked: told "tell them what was done", a small
# model said a button was on the form when only its action was made
DONE_NOT_FINISHED = "The person said yes, and this step is done. %s"
# Tools that do one thing on many records, with one yes: worked on in pieces
PLAN_TOOLS = ("prepare_plan", "bulk_apply")
FAILED = "The person said yes, but it did not work, and nothing was changed. %s"
DECLINED = (
    "The person said no. Nothing was done. Do not try to reach the same result another way: "
    "ask them what they want instead."
)
UNDECIDED = (
    "The person did not decide: they wrote a new message instead. Nothing was done. Read "
    "their message."
)
ONE_AT_A_TIME = (
    "Not looked at: one action at a time. Wait for the outcome of the first one, then ask "
    "for this one again if it is still needed."
)
TOO_MANY_STEPS = (
    "Nothing more can be done for this request: the limit of steps is reached. Tell the "
    "person what was done and what is left, so that they can ask for the rest."
)
# What one round may run outside a run (Talk), and read back in all: each result is replayed in the next rounds
MAX_ROUND_CALLS = 10
MAX_ROUND_CHARS = 60_000
TOO_MANY_AT_ONCE = (
    "Not run: too many tools at once in one step. Answer with what was found, or ask for the rest "
    "a few at a time."
)


# The commands of the message box that look for a product
PRODUCT_COMMANDS = tools.commands.BY_NAME["product"].words


def is_product_command(text):
    """:return: whether ``text`` asks for a product search: the model
    reaches for search_products at once, a picture with it described"""
    command, _rest = tools.commands.parse(text)
    return command is not None and command.name == "product"


# Marks the recording of a meeting among the files of the messages
MEETING_AUDIO = "meeting-audio"
MEETING_AUDIO_HOURS = 1

class NudgeConversation(models.Model):
    _name = "nudge.conversation"
    _description = "Nudge Conversation"
    _order = "write_date desc, id desc"

    task_id = fields.Many2one("nudge.task", string="Scheduled Task", index="btree_not_null",
                              ondelete="set null", readonly=True)

    name = fields.Char(string="Title")
    active = fields.Boolean(default=True)
    user_id = fields.Many2one(
        "res.users", string="User", required=True, index=True, ondelete="cascade",
        default=lambda self: self.env.user)
    company_id = fields.Many2one(
        "res.company", string="Company", default=lambda self: self.env.company)
    # A conversation stays on the provider and model that started it: the
    # history holds provider-native blocks that another model cannot read.
    provider_id = fields.Many2one("nudge.provider", string="Provider", ondelete="set null")
    model_name = fields.Char(string="Model")
    message_ids = fields.One2many("nudge.message", "conversation_id", string="Messages")
    message_count = fields.Integer(compute="_compute_totals", store=True)
    input_tokens = fields.Integer(compute="_compute_totals", store=True)
    output_tokens = fields.Integer(compute="_compute_totals", store=True)
    cost = fields.Float(compute="_compute_totals", store=True, digits=(12, 6))
    stream_started_at = fields.Datetime(string="Answering Since", copy=False, index="btree_not_null")
    # PERF-02: every answer streamed is numbered; Stop writes the number it stopped, which the
    # stream reads between the words of the provider and before the tools it calls
    stream_serial = fields.Integer(copy=False)
    stream_stopped = fields.Integer(copy=False)
    # What the model read of a document the person sent, kept with the conversation (not in the
    # memory of one worker) so that the answer to its question finds it: {key: [when, data]}
    document_kept = fields.Json(copy=False, groups="base.group_system")
    # The request being worked on in Agent mode, and the step of it that
    # waits for the person
    run_id = fields.Many2one("nudge.run", string="Current Run", copy=False, ondelete="set null")
    waiting_step_id = fields.Many2one(
        "nudge.step", string="Waiting For", copy=False, ondelete="set null")
    run_ids = fields.One2many("nudge.run", "conversation_id", string="Runs")
    # The groups of tools a small model asked for, given for the rest of the
    # conversation (tools/groups.py)
    tool_groups = fields.Char(copy=False)
    # The first-setup assistant when no AI reads the answers: the step asked, the steps done or skipped
    setup_state = fields.Json(copy=False)

    @api.depends("message_ids.input_tokens", "message_ids.output_tokens",
                 "message_ids.cache_read_tokens", "message_ids.cache_write_tokens",
                 "message_ids.cost")
    def _compute_totals(self):
        for conversation in self:
            messages = conversation.message_ids
            conversation.message_count = len(messages)
            conversation.input_tokens = sum(
                message.input_tokens + message.cache_read_tokens + message.cache_write_tokens
                for message in messages
            )
            conversation.output_tokens = sum(messages.mapped("output_tokens"))
            conversation.cost = sum(messages.mapped("cost"))

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def _is_privileged(self):
        return self.env.su or self.env.user.has_group("nudge.group_nudge_manager")

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            for vals in vals_list:
                if SUMMARY_BOOKKEEPING & vals.keys():
                    raise AccessError(self.env._("Only Nudge writes the bookkeeping of a summary."))
                if vals.get("summary_text") and vals.get("user_id", self.env.uid) != self.env.uid:
                    # NP-22: as on write, the summary is the owner's to correct, a manager included
                    raise AccessError(self.env._("Only the owner of a conversation can change its summary."))
            vals_list = [
                dict(vals, summary_edited=True) if vals.get("summary_text") else vals for vals in vals_list]
        if not self._is_privileged():
            # Tokens and ownership are bookkeeping, not user input
            vals_list = [
                {key: value for key, value in vals.items() if key in USER_WRITABLE_FIELDS}
                for vals in vals_list
            ]
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and not USER_WRITABLE_FIELDS.issuperset(vals):
            if not self._is_privileged() or {"run_id", "waiting_step_id"} & vals.keys():
                raise AccessError(self.env._("Only the title of a conversation can be changed."))
        if not self.env.su and "summary_text" in vals:
            # NP-22: the summary is the owner's to correct, and to nobody else's, a manager included
            if (any(conversation.user_id != self.env.user for conversation in self)
                    or SUMMARY_BOOKKEEPING & vals.keys()):
                raise AccessError(self.env._("Only the owner of a conversation can change its summary."))
            # Kept as written; emptied, it is made again from the messages
            vals = dict(vals, summary_edited=bool((vals["summary_text"] or "").strip()), summary_ai=False,
                        summary_dirty=False)
            if not vals["summary_edited"]:
                vals.update(summary_text=False, summary_extra=False, summary_upto_id=0, summary_count=0)
        elif not self.env.su and SUMMARY_BOOKKEEPING & vals.keys():
            raise AccessError(self.env._("Only Nudge writes the bookkeeping of a summary."))
        return super().write(vals)

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------
    @api.model
    def get_status(self):
        """:return: what Nudge can do for the current user right now; empty
        when Nudge is not for them"""
        return self.env["ir.http"]._get_nudge_session_info() or {}

    @api.model
    def _get_stream_seconds(self, provider=None):
        """A streamed request must end well before the server's hard limit on
        the duration of a request, past which the worker is killed. A model on
        one's own machine answers slower: it is given its own time, within
        that limit, and wholly where no worker is killed.

        :param provider: the provider that answers; None for the usual time"""
        limit = config["limit_time_real"] or 0
        local = bool(provider) and provider.is_ollama
        wanted = max(STREAM_MAX_SECONDS, provider.timeout or 0) if local else STREAM_MAX_SECONDS
        if limit <= 0 or (local and not config["workers"]):
            return wanted
        return max(STREAM_MIN_SECONDS, min(wanted, limit - STREAM_SAFETY_MARGIN))

    @api.model
    def _get_max_streams(self):
        """:return: how many answers may stream at the same time, for everybody
        together: what the administrator set, or the http workers of the
        server less two, at least one; a server without workers answers many"""
        value = self.env["ir.config_parameter"].sudo().get_param(MAX_STREAMS_PARAM) or "0"
        try:
            wanted = int(float(value))
        except ValueError:
            wanted = 0
        if wanted > 0:
            return wanted
        workers = config["workers"] or 0
        if workers <= 0:
            return THREADED_MAX_STREAMS
        return max(1, workers - 2)

    @api.model
    def _chat_open(self, conversation_id=None):
        """:return: the conversation to post in, as the current user"""
        if not conversation_id:
            return self.create({})
        conversation = self.browse(conversation_id).exists()
        if not conversation or conversation.user_id != self.env.user:
            # Managers can read every conversation but only write in their own
            raise AccessError(self.env._("This conversation is not yours."))
        return conversation

    def _chat_check_not_busy(self, cap=True):
        """:param cap: refuse past the cap of the answers at the same time; a question of the chat
            claims its place later, atomically, and waits in line rather than being refused
            (``nudge.slot``)"""
        Slot = self.env["nudge.slot"].sudo()
        if Slot._is_answering(self.env.user):
            raise UserError(self.env._("Nudge is still answering your previous message."))
        # Everybody's answers: each holds one http worker while it streams, and
        # a few at once leave none for the screens of everyone else. Work in the
        # background holds no http worker and carries no mark; the mark of a
        # worker that died mid-answer expires with the window
        if cap and Slot._taken() >= self._get_max_streams():
            raise UserError(self.env._(
                "Nudge is answering other people right now. Try again in a moment."))

    def action_stop_answer(self):
        """PERF-02: the person stops the answer being written. The worker is let go and the place
        freed at once: the stream reads the stop within a second, even from a provider that says
        nothing, keeps what was written and is recorded as stopped. Their own conversations only."""
        for conversation in self:
            if conversation.user_id != self.env.user:
                raise AccessError(self.env._("This conversation is not yours."))
        self.env["nudge.slot"]._stop(self.ids)
        return True

    def _chat_stopped(self, serial):
        """:return: whether the answer numbered ``serial`` was stopped, read from the database:
        the stop comes through another worker"""
        self.ensure_one()
        self.flush_recordset(["stream_stopped"])
        self.env.cr.execute("SELECT stream_stopped FROM nudge_conversation WHERE id = %s", [self.id])
        row = self.env.cr.fetchone()
        return bool(row and serial and (row[0] or 0) >= serial)

    def _chat_context_values(self, spoken=False, read_aloud=False, model_name=None,
                             explain=None, attachment=None, report=None, answer_in=None):
        """:param model_name: model of the screen the person is on, when shared:
        the pages of the documentation about its app are named
        :param explain: "screen" or "text", how the person wants the answer"""
        user = self.env.user
        lang = self.env["res.lang"]._get_data(code=user.lang or "en_US")
        values = {
            "user_name": user.name,
            "interface_language": lang.name or user.lang or "en_US",
            "company": self.env.company.name,
            "date": fields.Date.context_today(self).isoformat(),
        }
        if spoken:
            values["input"] = "voice"
        if read_aloud:
            values["answer"] = "read aloud"
        if explain in EXPLAIN_WORDS:
            values["explain"] = EXPLAIN_WORDS[explain]
        if answer_in:
            # A small model drifts into the language of what it reads: said plainly
            values["answer_in"] = answer_in
        if attachment:
            values["attachment"] = attachments.render(attachment)
        if report in REPORT_WORDS and self._can_make_spreadsheet():
            values["report"] = REPORT_WORDS[report]
        documentation = self.env["nudge.doc.page"]._hint_for_model(model_name)
        if documentation:
            values["documentation"] = documentation
        return values

    @api.model
    def _can_share_screen(self):
        # Stored as a word: a parameter set to False is deleted, which would
        # read as the default again
        return self.env["ir.config_parameter"].sudo().get_param(SCREEN_SHARING_PARAM) != "no"

    @api.model
    def _is_agent_allowed(self):
        """:return: whether the administrator lets Nudge act"""
        return self.env["ir.config_parameter"].sudo().get_param(AGENT_PARAM) != "no"

    @api.model
    def _is_python_allowed(self):
        """:return: whether the administrator lets the agent compute with Python"""
        return self.env["ir.config_parameter"].sudo().get_param(PYTHON_PARAM) == "yes"

    @api.model
    def _can_make_spreadsheet(self):
        """:return: whether a report can be made as a spreadsheet: the module
        for it is installed and this person may use the agent"""
        return "make_spreadsheet" in tools.base.TOOLS and self._can_use_agent()

    @api.model
    def _is_admin_allowed(self):
        """:return: whether administrators may read and change the setup of Odoo"""
        return self.env["ir.config_parameter"].sudo().get_param(ADMIN_PARAM) == "yes"

    @api.model
    def _can_administer(self):
        """:return: whether the current user may reach the setup of Odoo through Nudge"""
        return self._is_admin_allowed() and self.env.user.has_group("base.group_system")

    @api.model
    def _is_developer_allowed(self):
        """:return: whether administrators may build through Nudge: automation
        rules, server actions, emails, buttons, fields"""
        return self.env["ir.config_parameter"].sudo().get_param(DEVELOPER_PARAM) == "yes"

    @api.model
    def _can_develop(self):
        """:return: whether the current user may use Developer mode"""
        return (self._is_developer_allowed() and self._can_use_agent()
                and self.env.user.has_group("base.group_system"))

    @api.model
    def _is_sql_allowed(self):
        """:return: whether administrators may read the database with SQL"""
        return self.env["ir.config_parameter"].sudo().get_param(SQL_PARAM) == "yes"

    @api.model
    def _can_use_agent(self):
        """:return: whether Nudge may act for the current user"""
        return self._is_agent_allowed() and self.env.user.has_group(AGENT_GROUP)

    def _chat_mode(self):
        """:return: the mode of the request being answered"""
        self.ensure_one()
        run = self.sudo().run_id
        if not run:
            return MODE_TALK
        return MODE_DEV if run.is_developer else MODE_AGENT

    def _chat_screen(self, raw):
        """:param raw: snapshot as sent by the browser, untrusted
        :return: values of the message describing the screen"""
        self.ensure_one()
        snapshot = screen.sanitize(raw) if self._can_share_screen() else None
        if not snapshot:
            return {}, ""
        if screen.has_masked_value(snapshot):  # NP-15: the administrators are told, once a day per screen
            self.env["nudge.egress"].alert_credential_screen(snapshot)
        fingerprint = screen.fingerprint(snapshot)
        previous = self.env["nudge.message"].sudo().search([
            ("conversation_id", "=", self.id),
            ("role", "=", "user"),
        ], order="id desc", limit=1)
        unchanged = previous.snapshot_hash == fingerprint
        values = {"snapshot": snapshot, "snapshot_hash": fingerprint}
        return values, screen.render(snapshot, unchanged=unchanged)

    def _chat_provider(self, mode=None):
        """:param mode: the mode of the request: the work may have a provider
            of its own, set by the administrator
        :return: the provider answering this conversation, in sudo
        :raise UserError: when there is none"""
        Provider = self.env["nudge.provider"].sudo()
        provider = Provider._get_for_mode(mode or MODE_TALK)
        if not provider:
            # S6: the provider whose economy tier answered last time, not the economy tier itself
            provider = self.sudo().main_provider_id or self.sudo().provider_id
            if provider and provider in (Provider._get_for_mode(MODE_AGENT) | Provider._get_for_mode(MODE_DEV)
                                         | Provider._get_fallback()):
                # Answered by the provider of the work last time, or by the one standing in (NP-10):
                # Talk goes back to the default
                provider = provider.browse()
            elif provider and Provider._is_private_mode() and not provider.runs_locally:
                # Started before private mode: what follows goes to a provider of our own server
                provider = provider.browse()
        if not provider.is_configured or not provider.active:
            provider = self.env["nudge.provider"].sudo()._get_default()
        if not provider:
            raise UserError(self.env._(
                "Nudge is not set up yet. Ask your administrator to configure an AI provider."))
        provider._ensure_capabilities()
        return provider

    def _chat_choose(self, mode=None):
        """NP-10: the provider answering, and whether it stands in for the one the administrator
        named. The model chosen to stand in answers when the named one cannot (no key, archived,
        private mode), and when the person's budget is used up if it runs on our own server: it
        costs nothing. Nothing goes to a provider the administrator did not choose.

        :return: (provider in sudo, the event telling the person, or None)
        :raise UserError: when none can answer, or the budget is used up and none of our own stands in"""
        Provider = self.env["nudge.provider"].sudo()
        try:
            self.env["nudge.usage"]._check_budget()
        except UserError:
            fallback = Provider._get_fallback()
            if not fallback.runs_locally:
                raise
            fallback._ensure_capabilities()
            return fallback, self._chat_fallback_notice(fallback, "budget")
        provider = self._chat_provider(mode)
        if not provider._egress_allowed():
            # NP-15: the company's list of allowed destinations refuses this provider: the one
            # chosen to stand in answers when the list allows it (private mode applies to it as
            # always); otherwise the refusal stays, said when the request goes out
            fallback = Provider._get_fallback()
            if fallback and fallback != provider:
                if fallback._egress_allowed():
                    fallback._ensure_capabilities()
                    return fallback, self._chat_fallback_notice(fallback, "destination")
                # Refused too: the refusal says so, the person need not wonder whether it was tried
                try:
                    provider._check_egress()
                except UserError as refusal:
                    raise UserError(self.env._(
                        "%(refusal)s The model chosen to stand in (%(model)s) is not on the list either.",
                        refusal=refusal.args[0], model=fallback.model_name or fallback.name)) from None
        named = Provider._get_named(mode or MODE_TALK)
        if named and provider != named and provider == Provider._get_fallback():
            private = Provider._is_private_mode() and not named.runs_locally
            return provider, self._chat_fallback_notice(provider, "private" if private else "not_ready")
        return provider, None

    @api.model
    def _chat_fallback_notice(self, provider, reason):
        """:param reason: why it stands in: "error", "not_ready", "private", "destination" or "budget"
        :return: the event telling the person who answers, kept with the answer"""
        return {"type": "fallback", "provider": provider.name or "", "model": provider.model_name or "",
                "local": bool(provider.runs_locally), "reason": reason}

    def _chat_check_not_at_work(self):
        """What is worked on in the background is made of questions and
        answers that follow each other: a message in the middle would break
        them apart."""
        run = self.sudo().run_id
        if run.is_background and run.state in ("queued", "running"):
            raise UserError(self.env._(
                "Nudge is working on this in the background. Wait for it, or stop it first."))

    def _background_job(self):
        """:return: what a piece of work in the background needs
        :raise UserError: when the person may not spend more"""
        self.ensure_one()
        provider, notice = self._chat_choose(self._chat_mode())
        job = self._chat_job(provider, self._chat_mode(), AGENT_MAX_ROUNDS)
        job["fell_back"] = notice
        job["seconds"] = self.env["nudge.run"]._get_piece_seconds()
        if self.sudo().run_id.step_ids:
            # A piece that follows what was already done, or said yes to: what
            # the model says of it is never pushed into doing it again
            job["push_to_act"] = False
        return job

    def _chat_focus(self):
        """:return: the focused command (/count, /explain) the question being
        answered was asked with, or None"""
        self.ensure_one()
        question = self.env["nudge.message"].sudo().search([
            ("conversation_id", "=", self.id),
            ("role", "=", "user"),
        ], order="id desc", limit=1)
        return question.command if question.command in tools.commands.FOCUS else None

    def _chat_tools(self, mode, tool_set):
        """:return: the definitions of the tools offered for the question being
        answered: a small model gets those of its mode, and the groups the
        question or the model asked for"""
        self.ensure_one()
        extra = ()
        if tool_set == "essential":
            question = self.env["nudge.message"].sudo().search([
                ("conversation_id", "=", self.id), ("role", "=", "user")], order="id desc", limit=1)
            extra = tools.groups.offered(self, question.content or "")
        background = bool(self.sudo().run_id.is_background)
        return [tool.definition() for tool in tools.base.available(
            self.env, mode, tool_set, extra, background=background)]

    def _chat_job(self, provider, mode, rounds):
        """:return: everything the stream needs, as plain data: the
        environment is gone once streaming starts"""
        self.ensure_one()
        conversation = self.sudo()
        focus = self._chat_focus()
        # NP-22: what the next replay leaves out is summarised now, with no provider call
        conversation._summary_refresh()
        tool_set = provider.tool_set if provider else "full"
        if focus:
            # One precise thing, with a few tools: no overview, no history
            offered = tools.commands.FOCUS[focus]["tools"]
            return {
                "version": nudge_version(),
                "conversation_id": self.id,
                "user_message_id": False,
                "title": conversation.name,
                "mode": mode,
                "adapter": provider._get_adapter(),
                "system": prompt.build_system_blocks(focus=focus),
                "tools": [tool.definition() for tool in tools.base.available(
                    self.env, mode, tool_set, background=bool(conversation.run_id.is_background))
                          if tool.name in offered],
                "turns": conversation._chat_build_turns(),
                "seconds": self._get_stream_seconds(),
                "rounds": min(rounds, tools.commands.FOCUS_ROUNDS),
                "first_events": [],
            }
        return {
            "version": nudge_version(),
            "conversation_id": self.id,
            "user_message_id": False,
            "title": conversation.name,
            "mode": mode,
            "adapter": provider._get_adapter(),
            "system": prompt.build_system_blocks(
                self._chat_overview(), mode=mode, can_use_agent=self._can_use_agent(),
                can_develop=self._can_develop(),
                is_administrator=self.env.user.has_group("base.group_system"),
                # A small model reads the short instructions
                compact=tool_set == "essential",
                ask_data=tools.base.ask_tier_applies(self.env),
                background=bool(conversation.run_id.is_background),
                work="\n\n".join(block for block in (
                    self._chat_where_the_work_is(), self.env["nudge.source"]._get_text()) if block)),
            "tools": self._chat_tools(mode, tool_set),
            "turns": conversation._chat_build_turns(),
            "seconds": self._get_stream_seconds(provider),
            # A small model doing the work is told once to act when it only talked
            "push_to_act": tool_set == "essential" and mode != MODE_TALK,
            "rounds": rounds,
            "first_events": [],
        }

    def _chat_prepare(self, text, snapshot=None, spoken=False, read_aloud=False, mode=MODE_TALK,
                      background=False, explain=None, attachment=None, report=None, focus=None,
                      extra=None, fresh=False, answer_in=None, inner=False, slot_id=None):
        """See :meth:`_chat_prepare_question`. A question refused after it claimed its place
        gives the place back at once (the rollback of the request does it too)."""
        claimed = []
        # When the question reached the server: the first words of the answer are timed from it
        received = time.monotonic()
        try:
            job = self._chat_prepare_question(
                text, snapshot=snapshot, spoken=spoken, read_aloud=read_aloud, mode=mode,
                background=background, explain=explain, attachment=attachment, report=report,
                focus=focus, extra=extra, fresh=fresh, answer_in=answer_in, inner=inner,
                slot_id=slot_id, claimed=claimed)
        except Exception:
            if claimed:
                self.env["nudge.slot"]._release(claimed[0])
            raise
        job.setdefault("received", received)
        return job

    def _chat_prepare_question(self, text, snapshot=None, spoken=False, read_aloud=False, mode=MODE_TALK,
                               background=False, explain=None, attachment=None, report=None, focus=None,
                               extra=None, fresh=False, answer_in=None, inner=False, slot_id=None,
                               claimed=None):
        """Validate the message, store it and gather everything the stream
        needs.

        :param snapshot: description of the screen sent by the browser
        :param spoken: the message was said, and turned into text by a machine
        :param read_aloud: the answer will be read to the person
        :param mode: "agent" when the person asks Nudge to do the work
        :param background: the work is done while the person does something
            else; only in Agent mode
        :param explain: "screen" or "text": how the person wants the answer
            this time; None leaves it to the model
        :param attachment: (name, bytes, mimetype) of a file sent with the
            question, read here and carried to the model as data
        :param report: "sheet" or "chat": how the person wants a comparison
            or a report this time; None leaves it to the model
        :param answer_in: the language the answer is written in, when it is not that of ``text``
        :param slot_id: the place in line the question waited in, when it comes for its turn (NP-04)
        :param list claimed: where the place claimed is told, to be given back if the question fails
        :return: dict describing the job; with ``queued`` when the work is
            left to the background and there is nothing to stream
        :raise NudgeInLine: when every place is taken: the question waits for its turn
        """
        self.ensure_one()
        text = (text or "").strip()
        command, rest = tools.commands.parse(text)
        if focus not in tools.commands.INTERNAL_FOCUS:
            # Asked by the code of a companion module only, never by a browser
            focus = command.name if command and command.name in tools.commands.FOCUS else None
        if focus in tools.commands.INTERNAL_FOCUS:
            mode, background, explain, report = tools.commands.FOCUS[focus]["mode"], False, None, None
        elif focus:
            if not tools.commands.is_open(self.env, focus):
                raise UserError(self.env._("The command %s is not open to you.", command.words[0]))
            if len(rest) < 2:
                raise UserError(self.env._(
                    "Write what to look for after %(command)s, for example: %(example)s",
                    command=command.words[0], example=tools.commands.texts(self.env)[focus][1]))
            # The command says the mode: reading only, at once
            mode, background = tools.commands.FOCUS[focus]["mode"], False
            # Neither the pointer nor a spreadsheet: the answer, in writing
            explain = report = None
        found = self._chat_read_attachment(attachment)
        if not text and found:
            text = found["name"]
        if not text:
            raise UserError(self.env._("Write a message first."))
        if len(text) > MAX_MESSAGE_CHARS:
            raise UserError(self.env._(
                "This message is too long. The limit is %s characters.", MAX_MESSAGE_CHARS))
        if mode == MODE_DEV and not self._can_develop():
            raise UserError(self.env._("Developer mode is not open to you. Ask your administrator."))
        if mode == MODE_AGENT and not self._can_use_agent():
            raise UserError(self.env._("Agent mode is not open to you. Ask your administrator."))
        mode = mode if mode in (MODE_AGENT, MODE_DEV) else MODE_TALK
        background = bool(background) and mode != MODE_TALK
        if mode == MODE_TALK and not focus and not found and not self.env["nudge.provider"]._is_private_mode():
            # In private mode nothing of a question is kept for the managers to read
            # Counted by the meaning of the words, for the recurring questions: nobody is kept with it
            self.env["nudge.question"]._record(text)
        # An answer from memory too would land in the middle of work going on in the background
        self._chat_check_not_at_work()
        if not inner:
            # ``inner``: a one-off call made by a tool inside the person's own answer (the summary
            # of a record), not a second question of theirs, and no stream of its own for the http
            # workers. An argument, not a key of the context, which a request could carry.
            # Past the cap the question is not refused: it claims its place below, or waits.
            # Before the memory: its answer too would land in the middle of the one being written
            # (S13, the answer that follows a yes)
            self._chat_check_not_busy(cap=False)
        served = self.env["nudge.memory"]._chat_serve(
            self, text, mode, focus, found, explain, background, fresh=fresh, snapshot=snapshot)
        if served:
            return served
        limit = AGENT_MAX_MESSAGES if self.sudo().run_ids else MAX_MESSAGES
        if self.message_count >= limit:
            raise UserError(self.env._("This conversation is getting long. Start a new one."))
        # A common request is understood by rules: no token, same cards, same rights
        understood = self.env["nudge.noai"]._chat_serve(
            self, text, mode, focus, found, explain, background, snapshot, fresh=fresh)
        if understood:
            return understood
        # S7: the same question of someone with the same rights, answered before and still fresh
        cached = self.env["nudge.answer.cache"]._chat_serve(self, text, mode, snapshot, blocked=bool(
            fresh or focus or found or background or spoken or read_aloud or answer_in or extra or report
            or inner or explain == "screen"))
        if cached:
            return cached
        provider, notice = self._chat_choose(mode)
        slot = self.env["nudge.slot"]
        if not inner and not background:
            # One answer holds one http worker: a place among those streaming, claimed at once for
            # every worker to see, or a place in line (NP-04, PERF-04)
            slot = slot._claim(self, provider, slot_id=slot_id)
            if claimed is not None:
                claimed.append(slot.id)
        if found and found["image"] and not provider._can_see_pictures():
            # A model of the server that sees reads it; without one, said
            # before anything is sent: the model would refuse the picture
            media_type, data = found["image"]
            seen = provider._read_picture(
                media_type, data, text, deadline=until(self._get_stream_seconds(provider)))
            found = dict(found, image=None, text=self.env._(
                "What the picture shows, as read by %(model)s:\n%(text)s",
                model=provider.picture_model or "", text=seen), kept_image=found["image"], seen=seen)
        elif found and found["image"] and provider.is_ollama and provider.tool_set == "essential":
            # A small model that sees is sent the picture, but what it shows is
            # looked for among the products from words: it says them first, in
            # a short call of its own. "Do we sell this?" found a product by
            # the words of the question, not by the picture
            media_type, data = found["image"]
            try:
                found = dict(found, seen_by_kind=True, seen=provider._read_picture(
                    media_type, data, text, model=provider.model_name, prompt=ollama.PRODUCT_PROMPT,
                    deadline=until(self._get_stream_seconds(provider))))
            except UserError as error:
                _logger.info("Nudge: the picture was not described for the products: %s", error)

        conversation = self.sudo()
        # What waited for a decision is left: the person moved on
        conversation._agent_leave_undecided()
        conversation.run_id._end(conversation.run_id._outcome("stopped"))
        # What is left to the background does not keep the person from asking elsewhere
        values = {
            "stream_started_at": False if background else fields.Datetime.now(),
            "run_id": False,
            "stream_serial": conversation.stream_serial + 1,
        }
        if conversation.provider_id != provider or conversation.model_name != provider.model_name:
            values.update(provider_id=provider.id, model_name=provider.model_name)
        if not conversation.name:
            values["name"] = text[:TITLE_CHARS].strip()
        conversation.write(values)
        # Committed with the mark: the place stops counting as the mark starts to
        slot._handover()
        screen_values, screen_text = self._chat_screen(snapshot)
        voice = self.env["nudge.voice"]._is_allowed()
        spoken = bool(spoken) and voice
        context_text = prompt.render_context(self._chat_context_values(
            spoken=spoken, read_aloud=bool(read_aloud) and voice,
            model_name=(screen_values.get("snapshot") or {}).get("model"),
            explain=explain, attachment=found, report=report,
            # Every model drifts into the language of what it reads: the
            # database here may be described in another one
            answer_in=answer_in or tools.commands.language_of(text)))
        if screen_text:
            context_text = "\n".join((context_text, screen_text))
        if found and found["text"]:
            context_text = "\n".join((context_text, prompt.render_attachment(found)))
        if extra:
            # What the caller knows that the person did not type: a record, a conversation
            context_text = "\n".join((context_text, extra))
        grounded = {}
        if provider.tool_set == "essential" and mode == MODE_TALK and (
                not focus or focus in tools.commands.GROUNDED_FOCUS):
            # A small model answers far better from what it is given than from
            # what it remembers: what Nudge finds by itself goes with the question
            grounded = self.env["nudge.quick"]._ground(text)
        elif provider.tool_set == "essential" and mode != MODE_TALK and not focus:
            # Doing the work, it is told which kind of record the words name
            grounded = self.env["nudge.quick"]._ground_work(text, developing=mode == MODE_DEV)
        lesson = tools.lessons.taught(text) if not focus else ""
        if lesson:
            # Asked to remember, a small model said it had and wrote nothing down,
            # or the rule turned around: Nudge writes the person's words down itself
            _written, told = tools.lessons.propose(self.env, lesson, text, self.id)
            grounded = dict(grounded, lesson=f"{told} The lesson, in their words: {lesson}")
        if provider.tool_set == "essential" and found and found.get("seen"):
            # What the picture shows, among the products: "do we sell this?"
            products = self.env["nudge.quick"]._ground_products(found["seen"], by_kind=found.get("seen_by_kind"))
            # Those of the words of the question ("this product") are not those of the picture
            grounded = {key: value for key, value in grounded.items() if key != "products"}
            # What the picture was read as stays with the question, for what is said of it
            grounded["picture_read_as"] = found["seen"]
            if products:
                grounded["products"] = products
            else:
                # Nothing like it, and a small model sold "Oxford Oak 2-Drawer Cabinet, $419"
                grounded["products"] = (
                    f"None like the picture (read as: {found['seen']}). Name no product: say none "
                    "was found, or call search_products.")
        if grounded:
            context_text = "\n".join((context_text, prompt.render_found(grounded)))
        message = self.env["nudge.message"].sudo().create({
            "conversation_id": self.id,
            "role": "user",
            "content": text,
            "is_spoken": spoken,
            "mode": mode,
            "context_text": context_text,
            "attachment_name": found["name"] if found else False,
            "command": focus or False,
            "wants_pointer": explain == "screen" and bool(screen_values),
            **screen_values,
        })
        if found and found.get("kept_image"):
            # Kept for the person to see; the model was given what was read
            media_type, data = found["kept_image"]
            message.attachment_id = self.env["ir.attachment"].sudo().create({
                "name": found["name"],
                "datas": data,
                "mimetype": media_type,
                "res_model": "nudge.message",
                "res_id": message.id,
                "description": "read",
            })
        elif found and found["image"]:
            media_type, data = found["image"]
            message.attachment_id = self.env["ir.attachment"].sudo().create({
                "name": found["name"],
                "datas": data,
                "mimetype": media_type,
                "res_model": "nudge.message",
                "res_id": message.id,
            })
        elif found and found["kind"] == "audio":
            # Kept only until the summary is done with: the hourly cleaning removes it
            _name, raw, mimetype = attachment
            message.attachment_id = self.env["ir.attachment"].sudo().create({
                "name": found["name"],
                "datas": base64.b64encode(raw),
                "mimetype": mimetype or "audio/mpeg",
                "res_model": "nudge.message",
                "res_id": message.id,
                "description": MEETING_AUDIO,
            })
        elif found and found["kind"] in ("sheet", "text", "pdf"):
            # A file to import is tried later, as it was sent; a document to enter, copied to the record
            _name, raw, mimetype = attachment
            message.attachment_id = self.env["ir.attachment"].sudo().create({
                "name": found["name"],
                "datas": base64.b64encode(raw),
                "mimetype": mimetype or ("text/csv" if found["name"].lower().endswith(".csv") else "application/octet-stream"),
                "res_model": "nudge.message",
                "res_id": message.id,
            })
        if mode != MODE_TALK:
            conversation.run_id = self.env["nudge.run"].sudo().create({
                "name": text[:TITLE_CHARS * 2].strip(),
                "conversation_id": self.id,
                "question_id": message.id,
                "user_id": self.env.uid,
                "company_id": self.env.company.id,
                "model_name": provider.model_name,
                "is_background": background,
                "is_developer": mode == MODE_DEV,
            })
        if background:
            run = conversation.run_id
            run._queue()
            return {
                "conversation_id": self.id,
                "user_message_id": message.id,
                "title": conversation.name,
                "mode": mode,
                "queued": run.id,
            }
        rounds = AGENT_MAX_ROUNDS if mode != MODE_TALK else MAX_ROUNDS
        return dict(
            self._chat_job(provider, mode, rounds),
            user_message_id=message.id,
            # The pointer or a spreadsheet was asked for: the model reaches for a
            # tool, not for a list
            tool_choice="required" if focus == "count" or explain == "screen" or is_product_command(text) or (
                report == "sheet" and self._can_make_spreadsheet()) else None,
            # The number of this answer, which Stop names, and its place among those streaming
            serial=conversation.stream_serial,
            slot_id=slot.id or None,
            # The model chosen to stand in answers: the person is told (NP-10)
            fell_back=notice,
            # How long it waited in line, kept with its answer for the latency panel
            queue_wait_ms=slot._waited_ms(),
        )

    def _chat_overview(self):
        """:return: what every conversation is told about the database"""
        Profile = self.env["nudge.profile"].sudo()
        Profile._ensure_current()
        return Profile._get_overview()

    @api.ondelete(at_uninstall=False)
    def _unlink_keeps_the_log(self):
        # What the Agent did is an audit log nobody deletes, a manager neither: a conversation
        # that holds one is archived, and is refused here in words rather than by the database
        acted = self.sudo().with_context(active_test=False).filtered("run_ids")
        if acted:
            raise UserError(self.env._(
                "Nudge acted in %(names)s: the log of what it did is kept, so the conversation "
                "cannot be deleted. Archive it instead.",
                names=", ".join(acted[:3].mapped("display_name"))))

    def unlink(self):
        # The messages go with the conversation in the database, not through the
        # ORM: the images sent with them would be left behind
        self.sudo().message_ids.attachment_id.unlink()
        return super().unlink()

    @api.model
    def _cron_apply_retention(self, batch=200):
        """Forget the conversations whose last activity is older than the days set in Settings
        (0: never), a batch at a time, each committed, until none is left or the time of the pass
        is up (the next pass goes on at once): deleted, or archived when the Agent acted in them,
        since its log is kept. One that is answering, or has a step waiting for a yes, is left.

        :return: how many were deleted or archived"""
        days = int(float(self.env["ir.config_parameter"].sudo().get_param("nudge.retention_days") or 0))
        if days <= 0:
            return 0
        limit = fields.Datetime.subtract(fields.Datetime.now(), days=days)
        Conversation = self.sudo().with_context(active_test=False)
        # NP-16: what an auditor asked is kept for the company, whatever the days say
        idle = [("write_date", "<", limit), ("stream_started_at", "=", False), ("waiting_step_id", "=", False),
                ("kept_for_audit", "=", False),
                ("run_ids", "not any", [("step_ids.state", "=", "pending")])]

        def archive(size):
            acted = Conversation.search(idle + [("active", "=", True), ("run_ids", "!=", False)],
                                        order="write_date, id", limit=size)
            acted.write({"active": False})
            return len(acted)

        def delete(size):
            gone = Conversation.search(idle + [("run_ids", "=", False)], order="write_date, id", limit=size)
            gone.unlink()
            return len(gone)

        cron = "nudge.cron_conversation_retention"
        return in_batches(self.env, cron, archive, batch) + in_batches(self.env, cron, delete, batch)

    @api.model
    def _cron_forget_meeting_audio(self):
        """Recordings of meetings are never kept: whatever the summary left behind goes within the hour."""
        limit = fields.Datetime.subtract(fields.Datetime.now(), hours=MEETING_AUDIO_HOURS)
        self.env["ir.attachment"].sudo().search([
            ("description", "in", (MEETING_AUDIO, "meeting-kept-text", "meeting-kept-summary")),
            ("res_model", "in", ("nudge.message", "nudge.conversation")),
            ("create_date", "<", limit)]).unlink()

    def _chat_read_attachment(self, attachment):
        """:return: what ``llm.attachments.read_attachment`` found, or None
        :raise UserError: when the file cannot be read"""
        if not attachment:
            return None
        name, data, mimetype = attachment
        try:
            return attachments.read_attachment(name, data, mimetype)
        except attachments.Unreadable as error:
            raise UserError(self.env._(str(error))) from None

    def _chat_where_the_work_is(self):
        """:return: where the business works, among what this person may read"""
        profile = self.env["nudge.profile"].sudo()._get_current()
        return retrieval.where_the_work_is(self.env, profile) if profile else ""

    @staticmethod
    def _chat_images_of(message):
        """:return: the image sent with ``message``, for the model to look at"""
        attachment = message.sudo().attachment_id if message.role == "user" else None
        if not attachment or not attachment.datas or not (attachment.mimetype or "").startswith("image/"):
            return []
        if attachment.description == "read":
            # Read by the picture model: its words went with the question
            return []
        return [{"media_type": attachment.mimetype, "data": attachment.datas.decode("ascii")}]

    def _chat_build_turns(self):
        """:return: the conversation as the adapters read it; a question
        asked with a focused command is read without what came before.

        PERF-08 and NP-22: only the latest question carries its screen (and the one that showed a
        screen a later question says is unchanged, see :meth:`_chat_replayed_contexts`), and past
        the turns set in Settings (or the token budget of the history) the oldest ones are replaced
        by their summary, which is data about the past and is put in front of the first question kept"""
        self.ensure_one()
        turns = []
        messages = self.message_ids.sorted("id")
        questions = messages.filtered(lambda message: message.role == "user")
        question = questions[-1:]
        summary_text = ""
        if question.command in tools.commands.FOCUS:
            messages = messages.filtered(lambda message: message.id >= question.id)
        else:
            messages, summary_text = self._summary_block(messages)
        contexts = self._chat_replayed_contexts(questions, question, set(messages.ids))
        for message, following in zip(messages, list(messages[1:]) + [messages.browse()]):
            if message.role == "tool":
                if turns and turns[-1].get("tool_calls"):
                    turns.append({"role": "tool", "results": message.tool_results or []})
                continue
            # A call is only replayed with its result: providers refuse one
            # without the other
            calls = message.tool_calls if following.role == "tool" else None
            if not message.content and not calls:
                continue
            text = message.content or ""
            if summary_text and message.role == "user":
                text, summary_text = summary_text + text, ""
            turns.append({
                "role": message.role,
                "text": text,
                # The screen of an old question is not sent again: the latest one is the screen now
                "context": contexts.get(message.id, ""),
                "images": self._chat_images_of(message),
                "blocks": (message.content_blocks or []) if calls or message.content else [],
                "provider_kind": message.provider_kind or "",
                "model": message.model_name or "",
                "tool_calls": calls or [],
            })
        return turns

    @api.model
    def _chat_screen_sources(self, questions):
        """:param questions: the questions of a conversation, in their order
        :return: ``{question id: id of the question that showed its screen}`` for each question whose
            screen was sent as unchanged, the same as the screen of the question before it"""
        sources, source, previous = {}, False, None
        for question in questions:
            digest = question.snapshot_hash
            if digest and previous is not None and previous.snapshot_hash == digest:
                if source:
                    sources[question.id] = source
            else:
                source = question.id if digest else False
            previous = question
        return sources

    def _chat_replayed_contexts(self, questions, latest, kept):
        """What each question replayed carries besides its text (PERF-08): the latest one its context;
        an old one nothing, unless a later question replayed says its screen is unchanged, which points
        at the screen this one showed. When that screen is no longer replayed (summarised, or before a
        focused command), the latest question gets its own screen in full instead of the pointer.

        :param kept: ids of the messages replayed
        :return: ``{question id: context}``"""
        sources = self._chat_screen_sources(questions)
        shown = {source for question_id, source in sources.items() if question_id in kept and source in kept}
        contexts = {question.id: question.context_text or "" for question in questions
                    if question.id in shown and question != latest}
        context = latest.context_text or ""
        if latest.id in sources and sources[latest.id] not in kept and isinstance(latest.snapshot, dict):
            context = context.replace(UNCHANGED_SCREEN, screen.render(latest.snapshot), 1)
        if latest:
            contexts[latest.id] = context
        return contexts

    def _chat_pointer_from_text(self, text):
        """:return: the guidance event made from the text of an answer, when
        the person asked to be shown and nothing was shown; None otherwise"""
        self.ensure_one()
        Message = self.env["nudge.message"].sudo()
        question = Message.search([("conversation_id", "=", self.id), ("role", "=", "user")],
                                  order="id desc", limit=1)
        if not question.wants_pointer or not question.snapshot:
            return None
        since = Message.search([("conversation_id", "=", self.id), ("id", ">", question.id)])
        shown = any(isinstance(event, dict) and event.get("type") in ("guidance", "navigate")
                    for message in since for event in (message.screen_events or []))
        if shown:
            return None
        Quick = self.env["nudge.quick"]
        settings = Quick._ground_settings(question.content) if "settings" in tools.groups.asked(question.content) else []
        if settings:
            # The setting the question names, found without AI: a small model asked
            # "show me where to switch on units of measure" wrote a path that is not there
            steps = Quick.guide("setting", {"app": settings[0]["app"], "field": settings[0]["field"]})
            if steps:
                return {"type": "guidance", "steps": steps}
        steps = tools.guidance.steps_from_text(text, question.snapshot)
        return {"type": "guidance", "steps": steps} if steps else None

    def _chat_screens_to_open(self, screen_events):
        """:return: a ``found`` event with the screens whose names hold the
        words of the question, when it asked to be taken to a screen and
        nothing opened one; None otherwise"""
        self.ensure_one()
        Message = self.env["nudge.message"].sudo()
        question = Message.search([("conversation_id", "=", self.id), ("role", "=", "user")],
                                  order="id desc", limit=1)
        if not tools.commands.asks_to_go(question.content):
            return None
        since = Message.search([("conversation_id", "=", self.id), ("id", ">", question.id)])
        events = [event for message in since for event in (message.screen_events or [])] + list(screen_events)
        if any(isinstance(event, dict) and event.get("type") in ("navigate", "open_action", "open", "found")
               for event in events):
            return None
        screens = self.env["nudge.quick"]._ground_screens(question.content)
        if not screens:
            return None
        return {"type": "found", "menus": [{"id": screen["menu_id"], "path": screen["path"]} for screen in screens]}

    def _chat_screen_of_question(self):
        """:return: the screen shared with the question being answered"""
        question = self.env["nudge.message"].sudo().search([
            ("conversation_id", "=", self.id),
            ("role", "=", "user"),
        ], order="id desc", limit=1)
        return question.snapshot or None

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------
    def _step_summary(self, call, tool, result):
        """:return: what a step did, in the words of the person"""
        pending = result.pending or {}
        if pending.get("summary"):
            return pending["summary"]
        model = (result.records or (False,))[0] or (call.get("input") or {}).get("model")
        label = False
        if isinstance(model, str) and model in self.env:
            label = self.env["ir.model"].sudo()._get(model).name or self.env[model]._description
        words = {
            "search_records": self.env._("Looked up: %s"),
            "read_record": self.env._("Read: %s"),
            "open_record": self.env._("Opened: %s"),
            "search_database_profile": self.env._("Looked up how this database works"),
            "describe_model": self.env._("Looked at the fields of: %s"),
            "find_screen": self.env._("Looked for a screen"),
            "open_screen": self.env._("Opened a screen"),
            "show_on_screen": self.env._("Pointed at the screen"),
            "write_in_field": self.env._("Typed in a field"),
            "read_automations": self.env._("Read what runs by itself"),
            "check_python_code": self.env._("Checked code"),
            "try_automation": self.env._("Tried it on a dummy record"),
            "diagnose_failure": self.env._("Looked for why it failed"),
            "prepare_module_scaffold": self.env._("Drafted a module"),
            "test_import": self.env._("Tried the import"),
            "match_import_columns": self.env._("Matched the columns of the file"),
            "document_to_record": self.env._("Read a document"),
            "read_view": self.env._("Read a screen's definition"),
            "summarize_record_history": self.env._("Summarised the history of: %s"),
            "record_changes": self.env._("Looked at who changed: %s"),
            "prepare_create": self.env._("Prepared a new record"),
            "prepare_write": self.env._("Prepared a change to a record"),
            "call_method": self.env._("Ran an action on a record"),
            "aggregate_records": self.env._("Counted and added up records"),
            "prepare_approval_flow": self.env._("Prepared an approval flow"),
            "assess_database": self.env._("Looked over the database"),
            "prepare_view_change": self.env._("Prepared a change to a screen"),
            "prepare_button": self.env._("Prepared a button"),
            "prepare_field": self.env._("Prepared a field"),
            "bulk_apply": self.env._("Prepared a change to many records"),
            "show_chart": self.env._("Drew a chart"),
            "search_company_files": self.env._("Looked through the company files"),
            "read_settings": self.env._("Read the settings"),
            "read_configuration": self.env._("Read the configuration"),
            "describe_module": self.env._("Looked at an app"),
            "read_module_code": self.env._("Read the code of a module"),
            "change_settings": self.env._("Prepared a change of settings"),
            "show_settings": self.env._("Showed the settings"),
            "prepare_code_fix": self.env._("Prepared a fix"),
            "prepare_automation": self.env._("Prepared a rule that runs by itself"),
            "prepare_server_action": self.env._("Prepared a server action"),
            "prepare_email_template": self.env._("Prepared an email template"),
            "prepare_delete": self.env._("Prepared a deletion"),
            "prepare_undo": self.env._("Prepared taking back what was built"),
            "prepare_scheduled_action": self.env._("Prepared a scheduled task"),
            "search_odoo_documentation": self.env._("Looked in the Odoo documentation"),
            "find_record": self.env._("Looked for a record"),
            "more_tools": self.env._("Opened more tools"),
            "remember_lesson": self.env._("Noted a lesson"),
            "summarize_meeting": self.env._("Summarised a meeting"),
            "meeting_to_records": self.env._("Turned a meeting into records"),
            "show_menu_path": self.env._("Showed the way to a screen"),
            "prepare_plan": self.env._("Prepared a plan"),
            "search_products": self.env._("Looked for products"),
            "find_products": self.env._("Looked for products"),
            "locate_data": self.env._("Looked for where the data lives"),
            "run_python": self.env._("Worked something out"),
            "filter_screen": self.env._("Filtered the screen"),
            "prepare_report_design": self.env._("Prepared a report design"),
            "prepare_report": self.env._("Prepared a report"),
            "prepare_revert": self.env._("Prepared putting a value back"),
            "save_report": self.env._("Saved a report"),
            "read_setup": self.env._("Read the setup"),
            "prepare_setup_step": self.env._("Prepared a setup step"),
            "use_skill": self.env._("Got ready for this kind of work"),
            "run_sql": self.env._("Queried the data"),
            "prepare_task": self.env._("Prepared a task"),
            "show_tour": self.env._("Started a tour"),
            "prepare_email_design": self.env._("Prepared an email design"),
            "prepare_email_review": self.env._("Reviewed an email"),
            "make_spreadsheet": self.env._("Made a spreadsheet"),
            "check_site": self.env._("Checked the website"),
            "fix_site": self.env._("Prepared a fix for the website"),
            "build_shop": self.env._("Prepared an online shop"),
            "start_website_builder": self.env._("Started the website builder"),
            "read_site_page": self.env._("Read a page of the website"),
            "edit_page_section": self.env._("Prepared a change to a page section"),
            "change_page_seo": self.env._("Prepared a change to the page's search settings"),
            "undo_site_change": self.env._("Prepared taking back a website change"),
        }
        text = words.get(call["name"]) or (tool and tool.summary and self.env._(tool.summary)) or call["name"]
        if "%s" in text:
            return text % (label or model or "?")
        return text

    def _agent_log(self, call, tool, result, state, duration_ms=0):
        """Write down what Nudge did, or is about to do, for the current run.

        :return: the step, in sudo; empty in Talk mode, which has no log
        """
        run = self.sudo().run_id
        if not run:
            return self.env["nudge.step"].sudo()
        pending = result.pending or {}
        model, ids, names = result.records or (False, [], [])
        return self.env["nudge.step"].sudo().create({
            "run_id": run.id,
            "call_id": call["id"],
            "tool": call["name"],
            "kind": tool.kind if tool else "read",
            "state": state,
            "arguments": call.get("input") or {},
            "summary": self._step_summary(call, tool, result),
            "res_model": pending.get("res_model") or model,
            "res_ids": pending.get("res_ids") or list(ids)[:50],
            "record_names": pending.get("record_names") or ", ".join(map(str, names[:10]))[:500],
            "values": pending.get("values") or False,
            "method": pending.get("method") or False,
            "preview": pending.get("preview") or False,
            "is_sensitive": bool(pending.get("is_sensitive")),
            "result": False if state == "pending" else result.content,
            "error": result.content if state == "failed" else False,
            "duration_ms": duration_ms,
        })

    def _chat_run_tools(self, calls):
        """Run what the model asked for, as the person it is talking to.

        :param calls: ``tool_use`` events of the adapter
        :return: (results for the model, events for the browser, whether the
            model has to read the results, the step that waits for the person)
        """
        self.ensure_one()
        snapshot = self._chat_screen_of_question()
        mode = self._chat_mode()
        run = self.sudo().run_id
        results, events, must_read = [], [], False
        waiting = self.env["nudge.step"].sudo()
        ran, read = 0, 0
        for call in calls:
            tool = tools.base.TOOLS.get(call["name"])
            is_action = isinstance(tool, tools.base.Action)
            started = time.monotonic()
            if call.get("invalid"):
                result = tools.base.ToolResult(call["invalid"], is_error=True)
            elif run and len(run.step_ids) >= (
                    BACKGROUND_MAX_STEPS if run.is_background else AGENT_MAX_STEPS):
                # Not a step: the log holds what was looked at, and it was not
                results.append({
                    "id": call["id"], "name": call["name"],
                    "content": TOO_MANY_STEPS, "is_error": True,
                })
                must_read = True
                continue
            elif (not run and ran >= MAX_ROUND_CALLS) or read >= MAX_ROUND_CHARS:
                # Forty records read one by one in one step would be replayed in every round after
                results.append({
                    "id": call["id"], "name": call["name"],
                    "content": TOO_MANY_AT_ONCE, "is_error": True,
                })
                must_read = True
                continue
            elif waiting and is_action:
                result = tools.base.ToolResult(ONE_AT_A_TIME, is_error=True)
            else:
                # A tool may tie what it makes to this conversation, and
                # what it may reach depends on the mode
                env = self.with_context(
                    nudge_conversation_id=self.id, nudge_mode=mode,
                    nudge_background=bool(run and run.is_background)).env
                result = tools.base.call(env, call["name"], call["input"], snapshot, mode)
            duration_ms = int((time.monotonic() - started) * 1000)
            entry = {"id": call["id"], "name": call["name"]}
            if result.pending and not result.is_error:
                # Nothing was done: the person decides
                waiting = self._agent_log(call, tool, result, "pending", duration_ms)
                events.append(dict(waiting._get_card(), type="confirm"))
                results.append(dict(entry, step_id=waiting.id))
                continue
            step = self._agent_log(
                call, tool, result, "failed" if result.is_error else "done", duration_ms)
            if run.is_background and step and not result.is_error:
                # The person is elsewhere: they are told that it moves on
                run.progress = step.summary
                run._tell("progress")
            if result.is_error or not (tool and tool.terminal):
                must_read = True
            if result.event:
                events.append(result.event)
            # Everything a tool says is framed as data, errors included: they quote what people wrote
            content = tools.base.as_data(call["name"], result.content, result.is_error)
            results.append(dict(entry, content=content, is_error=result.is_error))
            ran, read = ran + 1, read + len(content)
        return results, events, must_read, waiting

    def _chat_list_step(self, run):
        """:return: the last look of a background run at records (a search, or
        the filter it wanted to show), the one the list of the answer opens"""
        if run.sudo().step_ids.filtered(lambda step: step.kind not in ("read", "show")):
            # A run that made something answers about what it made, not about a list
            return run.sudo().step_ids.browse()
        steps = run.sudo().step_ids.filtered(
            lambda step: step.tool in ("search_records", "filter_screen")
            and (step.arguments or {}).get("model") and (step.arguments or {}).get("conditions"))
        return steps[-1:]

    def _chat_background_link(self, run, text):
        """:return: the answer with the link that opens the list it is about"""
        step = self._chat_list_step(run)
        link = step._list_link() if step else False
        if not link or "/action-" in text or "action_open_list" in text:
            return text
        return text + "\n\n" + self.env._("Open the list: %s", link)

    def _chat_background_answer(self, run, limit=15):
        """:return: the rows the run found as a table with the link, for a model
        that ended without a word; empty when it found nothing"""
        steps = run.sudo().step_ids.filtered(lambda step: step.tool == "search_records" and step.state == "done")
        if not steps:
            return ""
        try:
            data = json.loads(steps[-1].result or "")
        except ValueError:
            return ""
        rows = data.get("records") or []
        if not rows:
            return ""
        columns = [key for key in rows[0] if key != "id"][:6] or ["id"]

        def cell(value):
            return str(value if value not in (None, False) else "").replace("|", "/").replace("\n", " ")[:60]

        lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
        lines += ["| " + " | ".join(cell(row.get(key)) for key in columns) + " |" for row in rows[:limit]]
        more = (data.get("total") or len(rows)) - len(rows[:limit])
        if more > 0:
            lines.append(self.env._("%s more", more))
        return self._chat_background_link(run, "\n".join(lines))

    def _chat_error_message(self, code, provider=None):
        messages = {
            base.ERROR_AUTH: self.env._(
                "The AI provider rejected the API key. Ask your administrator to check it."),
            base.ERROR_WORKSPACE: self.env._(
                "The API key of the AI provider is not scoped to a workspace. Ask your administrator "
                "to fill in the Workspace ID of the provider."),
            base.ERROR_MODEL_NOT_FOUND: self.env._(
                "The AI provider does not know the configured model."),
            base.ERROR_RATE_LIMIT: self.env._(
                "The AI provider's limit is reached. Try again in a minute."),
            base.ERROR_NETWORK: self.env._("The AI provider could not be reached."),
            base.ERROR_TIMEOUT: self.env._(
                "The AI provider took too long to answer. Ask again; when it happens often, a "
                "faster model or a lower reasoning effort on the provider helps."),
            base.ERROR_UNAVAILABLE: self.env._("The AI provider is temporarily unavailable."),
            base.ERROR_MISSING_LIBRARY: self.env._(
                "A Python library is missing on the server. Ask your administrator."),
        }
        if provider and provider.is_ollama:
            # A server of one's own: what to do about it is known
            messages.update({
                base.ERROR_NETWORK: self.env._(
                    "Ollama does not answer at %s. Start it on its server (the Ollama app, or "
                    "ollama serve), then ask again.", provider.base_url),
                base.ERROR_MODEL_NOT_FOUND: self.env._(
                    "Ollama has no model %(model)s. On its server: ollama pull %(model)s",
                    model=provider.model_name),
                base.ERROR_TIMEOUT: self.env._(
                    "Ollama took too long to answer: the model may still be loading, or be too "
                    "large for the machine. Ask again; choose the essential tools or a smaller "
                    "model if it happens often."),
            })
        messages[base.ERROR_UNSUPPORTED] = self.env._(
            "The model of the AI provider cannot use tools: it can talk, not act or build. Choose "
            "a model with tools on the provider.")
        return messages.get(code, self.env._("Something went wrong while answering."))

    def _chat_fall_back(self, outcome):
        """NP-10: the provider failed before it wrote or did anything: the model the administrator
        chose to stand in is asked instead, once. It reads what the provider would have read, no
        more; private mode keeps it to our own server (``nudge.provider._get_fallback``).

        :return: what the stream needs to ask it, or None"""
        error = outcome.get("error")
        if (not error or error.get("code") not in FALLBACK_ERRORS or not outcome.get("may_fall_back")
                or outcome.get("text") or outcome.get("tool_calls") or outcome.get("aborted")):
            return None
        conversation = self.sudo()
        failed = conversation.provider_id
        fallback = self.env["nudge.provider"].sudo()._get_fallback()
        if not fallback or fallback == failed:
            return None
        # The call that failed is written down all the same
        self.env["nudge.usage"]._add(
            "chat", base.normalize_usage(None), 0.0, conversation.user_id, conversation.company_id,
            conversation_id=self.id, provider_id=failed.id, model_name=failed.model_name)
        _logger.info("Nudge: %s failed (%s) in conversation %s, the fallback %s answers",
                     failed.name, error.get("code"), self.id, fallback.name)
        conversation.write({"provider_id": fallback.id, "model_name": fallback.model_name})
        if conversation.run_id:
            conversation.run_id.model_name = fallback.model_name
        fallback._ensure_capabilities()
        job = self._chat_job(fallback, self._chat_mode(), 1)
        return {
            "adapter": job["adapter"],
            "system": job["system"],
            "tools": job["tools"],
            "turns": job["turns"],
            "notice": self._chat_fallback_notice(fallback, "error"),
        }

    def _chat_record(self, outcome):
        """Store what the model answered, run the tools it called, and tell
        what comes next.

        :param dict outcome: ``text``, ``tool_calls`` and either ``stop`` or
            ``error`` (adapter events); ``aborted`` when the browser went
            away; ``last_round`` when the model cannot be called again
        :return: dict with ``screen_events`` for the browser, and either
            ``turns`` to call the model again or ``event``, the last event of
            the answer
        """
        self.ensure_one()
        conversation = self.sudo()
        if outcome.get("aborted") and outcome.get("serial") and conversation.stream_serial != outcome["serial"]:
            # PERF-02: stopped, and the person asked again since: the new answer is theirs (its
            # mark, its run); what the stopped one had written is left
            return {"screen_events": [], "event": {
                "type": "done", "message_id": False, "state": "aborted", "message": ""}}
        switch = self._chat_fall_back(outcome)
        if switch:
            return {"fallback": switch}
        provider = conversation.provider_id
        run = conversation.run_id
        stop = outcome.get("stop") or {}
        error = outcome.get("error")
        usage = base.normalize_usage(stop.get("usage"))
        text = outcome.get("text") or ""

        if error:
            state = "error"
        elif outcome.get("aborted"):
            state = "aborted"
        else:
            state = STATE_BY_STOP_REASON.get(stop.get("reason"), "complete")

        calls = outcome.get("tool_calls") or []
        if state != "complete" or stop.get("reason") != base.STOP_TOOL_USE:
            calls = []
        results, screen_events, must_read = [], [], False
        waiting = self.env["nudge.step"].sudo()
        if calls:
            results, screen_events, must_read, waiting = self._chat_run_tools(calls)
        again = must_read and not waiting and not outcome.get("last_round")
        # Asked to do the work, it wrote about it: told once to call the tool,
        # unless it asked the person something
        pushed = bool(
            outcome.get("may_push") and state == "complete" and not calls and text
            and (outcome.get("push_on_question") or not text.rstrip().endswith(("?", "\u061f")))
            and not outcome.get("last_round"))
        again = again or pushed
        if state == "complete" and text and not calls:
            # Asked to be shown, the model wrote the steps: what they name is pointed at
            pointer = self._chat_pointer_from_text(text)
            if pointer:
                screen_events = [*screen_events, pointer]
        if state == "complete" and not (must_read and not waiting and not outcome.get("last_round")):
            # Asked to be taken somewhere, and taken nowhere: the screens found are offered
            screens = self._chat_screens_to_open(screen_events)
            if screens:
                screen_events = [*screen_events, screens]
        # In the background, what the time of a piece did not let finish is
        # taken up by the next piece
        later = bool(
            must_read and not waiting and not again and run.is_background
            and state == "complete" and run.state == "running")

        if (run and run.is_background and state == "complete" and not again and not later
                and not waiting and not calls):
            # Never an empty answer: what was found is delivered, with the link to the list
            if text:
                text = self._chat_background_link(run, text)
            else:
                text = self._chat_background_answer(run)

        error_message = False
        if error:
            error_message = self._chat_error_message(error["code"], provider)
        elif state == "refused":
            # Declined midway, what was written is no answer: it is not kept
            text = ""
            error_message = self.env._("Nudge cannot help with this request.")
        elif waiting:
            state = "waiting"
        elif later:
            pass
        elif state == "truncated" and not text and stop.get("reason") == base.STOP_DEADLINE:
            error_message = self.env._(
                "Nudge ran out of time before it was done. Say \"go on\" and it continues from "
                "here; or press the clock to leave the work to it.")
        elif state == "complete" and not again and not text and not screen_events:
            state = "error"
            if run and must_read:
                error_message = self.env._(
                    "Nudge had to stop before it was done. Ask it to go on.")
            elif calls or outcome.get("said_before"):
                error_message = self.env._("Nudge could not find how to show this on your screen.")
            else:
                error_message = self.env._("The AI provider returned an empty answer.")

        Message = self.env["nudge.message"].sudo()
        message = Message.create({
            "conversation_id": self.id,
            "role": "assistant",
            "content": text,
            # Only a complete turn can be replayed block for block
            "content_blocks": stop.get("blocks") if state in ("complete", "waiting") else False,
            "tool_calls": [
                {key: call[key] for key in ("id", "name", "input", "extra")} for call in calls
            ] or False,
            # Answered by the model standing in: said again when the conversation is read (NP-10)
            "screen_events": ([outcome["fell_back"]] if outcome.get("fell_back") else []) + screen_events or False,
            "pending_results": results if waiting else False,
            "state": state,
            "error_code": error["code"] if error else False,
            "error_message": error_message,
            "error_detail": error["detail"] if error else False,
            "stop_reason": stop.get("reason"),
            "provider_kind": provider.kind,
            "model_name": stop.get("model") or conversation.model_name,
            "request_id": stop.get("request_id"),
            "duration_ms": outcome.get("duration_ms") or 0,
            "queue_wait_ms": outcome.get("queue_wait_ms") or 0,
            "first_line_ms": outcome.get("first_line_ms") or 0,
            "cost": provider._compute_cost(usage) if provider else 0.0,
            **usage,
        })
        if run:
            run._add_usage(message)
            run.step_ids.filtered(lambda step: not step.message_id).message_id = message
        if (stop or error) and not outcome.get("noai"):
            # The call was made: written down whatever came of it
            self.env["nudge.usage"]._add(
                "chat", usage, message.cost, conversation.user_id, conversation.company_id,
                conversation_id=self.id, message_id=message.id, provider_id=provider.id,
                model_name=message.model_name)
        if waiting:
            # The results are written once the person has decided: a call
            # goes to the model with its result, or does not go
            conversation.write({"stream_started_at": False, "waiting_step_id": waiting.id})
            run.state = "waiting"
            return {"screen_events": screen_events, "event": {
                "type": "waiting",
                "message_id": message.id,
                "state": state,
                "step_id": waiting.id,
                "message": "",
            }}
        if calls:
            Message.create({
                "conversation_id": self.id,
                "role": "tool",
                "tool_results": results,
            })
        if again:
            step = {"screen_events": screen_events, "turns": conversation._chat_build_turns(),
                    "pushed": pushed}
            if any(call["name"] == "more_tools" for call in calls):
                # What the model asked for is offered from its next answer
                step["tools"] = self._chat_tools(self._chat_mode(), provider.tool_set if provider else "full")
            return step
        if later:
            run._queue()
            return {"screen_events": screen_events, "event": {
                "type": "queued", "message_id": message.id, "state": state,
                "run_id": run.id, "message": "",
            }}

        conversation.stream_started_at = False
        run._end("failed" if state == "error" else run._outcome("stopped") if state == "aborted" else "done")
        event = {
            "type": "error" if state == "error" else "done",
            "message_id": message.id,
            "state": state,
            "message": error_message or "",
        }
        if error and self.env.user.has_group("nudge.group_nudge_manager"):
            event["detail"] = error["detail"]
        return {"screen_events": screen_events, "event": event}

    def _chat_finalize(self, outcome):
        """Store an answer that ends the conversation turn.

        :return: dict sent to the browser as the last event"""
        return self._chat_record(dict(outcome, last_round=True))["event"]

    # ------------------------------------------------------------------
    # Agent
    # ------------------------------------------------------------------
    def _agent_settle(self, step, state, result, events=None):
        """Write the outcome of the step that waited, and the results of the
        calls made with it: the model can be told.

        :param step: ``nudge.step``, in sudo
        :param result: :class:`~odoo.addons.nudge.tools.base.ToolResult`
        """
        self.ensure_one()
        conversation = self.sudo()
        model, ids, names = result.records or (step.res_model, step.res_ids or [], [])
        values = {
            "state": state,
            "result": result.content,
            "error": result.content if state == "failed" else False,
            "decided_at": fields.Datetime.now(),
            "res_ids": list(ids)[:50],
        }
        if names:
            values["record_names"] = ", ".join(map(str, names[:10]))[:500]
        if state == "done" and result.card_lines is not None:
            # The card says what was done, no longer what was about to be
            values["preview"] = dict(step.preview or {}, lines=result.card_lines)
        step.write(values)

        message = step.message_id
        results = []
        for entry in message.pending_results or []:
            if entry.get("step_id") == step.id:
                entry = {
                    "id": entry["id"], "name": entry["name"],
                    "content": tools.base.as_data(entry["name"], result.content, state == "failed"),
                    "is_error": state == "failed",
                }
            elif "content" not in entry:
                entry = dict(entry, content=ONE_AT_A_TIME, is_error=True)
            results.append({key: entry[key] for key in ("id", "name", "content", "is_error")})
        message.write({
            "state": "complete",
            "pending_results": False,
            "screen_events": (message.screen_events or []) + list(events or []),
        })
        self.env["nudge.message"].sudo().create({
            "conversation_id": self.id,
            "role": "tool",
            "tool_results": results,
        })
        conversation.waiting_step_id = False

    def _agent_leave_undecided(self, reason=UNDECIDED):
        """The person went on without deciding: the step is closed, and the
        model will read that nothing was done."""
        for conversation in self.sudo():
            step = conversation.waiting_step_id
            if step and step.state == "pending":
                conversation._agent_settle(
                    step, "cancelled", tools.base.ToolResult(reason, is_error=True))
            conversation.waiting_step_id = False

    def _agent_give_up(self, run, message=None):
        """A run in the background cannot go on: the person is told why, in
        the conversation, where they will look for what became of it."""
        self.ensure_one()
        self.env["nudge.message"].sudo().create({
            "conversation_id": self.id,
            "role": "assistant",
            "state": "error",
            "error_message": message or self.env._(
                "Nudge had to stop before it was done. Ask it to go on."),
        })
        self.sudo().stream_started_at = False
        run._end("failed")

    def _setup_follow_up(self, step, state):
        """:return: what the first-setup assistant says after the person's answer to one of its cards, when no AI
        reads the answers: the next question"""
        if step.tool != "prepare_setup_step" or not (self.sudo().setup_state or {}).get("active"):
            return ""
        env = self.with_context(nudge_conversation_id=self.id, nudge_mode=self._chat_mode()).env
        if state == "done":
            return tools.setup.after_yes(env, self)
        return env._("Nothing was changed. Tell me what should be different, or say “skip”.")

    def _agent_decide(self, step_id, decision):
        """Do what the person said yes to, or leave what they said no to, and
        gather what the stream needs to let the model go on.

        :param int step_id: the step that waits
        :param str decision: "confirm" or "decline"
        :return: dict describing the job
        """
        self.ensure_one()
        if self.user_id != self.env.user:
            raise AccessError(self.env._("This conversation is not yours."))
        if not self._can_use_agent():
            raise UserError(self.env._("Agent mode is not open to you. Ask your administrator."))
        if decision not in (CONFIRM, DECLINE, PASSING):
            raise UserError(self.env._("Say yes or no."))
        conversation = self.sudo()
        step = conversation.waiting_step_id
        if not step or step.id != step_id:
            raise UserError(self.env._("This is no longer waiting for you."))
        try:
            # Two clicks, two windows: decided once
            step.lock_for_update()
        except LockError:
            raise UserError(self.env._("This is being done already.")) from None
        step.invalidate_recordset(["state"])
        if step.state != "pending":
            raise UserError(self.env._("This is no longer waiting for you."))
        self._chat_check_not_busy()
        # What was understood without AI needs no model after the yes
        noai = bool(step.message_id.noai_intent)
        provider = notice = None
        if not noai:
            # What follows the yes is a call to the model
            provider, notice = self._chat_choose(self._chat_mode())

        events = []
        tool = tools.base.TOOLS.get(step.tool)
        is_plan = step.tool in PLAN_TOOLS and isinstance(step.values, dict) and "plan" in step.values
        if decision == PASSING and not is_plan:
            raise UserError(self.env._("Say yes or no."))
        if is_plan and decision != DECLINE:
            step.values = dict(step.values, choice=decision)
            if tool and tool.is_available(self.env, self._chat_mode()) and tool.in_background(step.values):
                # Many records: worked on in the background, by pieces, as the person
                step.write({"state": "working", "decided_at": fields.Datetime.now()})
                run = step.run_id
                run.is_background = True
                conversation.run_id = run
                run._queue()
                return {
                    "conversation_id": self.id,
                    "user_message_id": False,
                    "title": conversation.name,
                    "mode": self._chat_mode(),
                    "queued": run.id,
                    "first_events": [dict(step._get_card(), type="decided")],
                }
        if decision == DECLINE:
            state, result = "declined", tools.base.ToolResult(DECLINED)
        else:
            mode = self._chat_mode()
            if not isinstance(tool, tools.base.Action) or not tool.is_available(self.env, mode):
                result = tools.base.ToolResult("This can no longer be done.", is_error=True)
            else:
                # In the environment of the person, never in sudo
                env = self.with_context(nudge_conversation_id=self.id, nudge_mode=mode).env
                try:
                    env = step._company_env(env)
                except UserError as error:
                    result = tools.base.ToolResult(str(error), is_error=True)
                else:
                    result = tools.base.guarded(env, step.tool, lambda: tool.execute(env, step))
            state = "failed" if result.is_error else "done"
            if result.event:
                events.append(result.event)
            # Said plainly: what comes next depends on the model having understood
            result = tools.base.ToolResult(
                (FAILED if result.is_error else DONE_NOT_FINISHED if result.unfinished else DONE)
                % result.content, is_error=result.is_error, records=result.records,
                unfinished=result.unfinished and not result.is_error, card_lines=result.card_lines)
        self._agent_settle(step, state, result, events)
        run = step.run_id
        first_events = [dict(step._get_card(), type="decided"), *events]
        if noai:
            run._end("done")
            conversation.stream_started_at = False
            follow = self._setup_follow_up(step, state)
            if follow:
                first_events.append({"type": "delta", "text": follow})
                step.message_id.content = ((step.message_id.content or "") + "\n\n" + follow).strip()
            return {
                "conversation_id": self.id,
                "user_message_id": False,
                "title": conversation.name,
                "mode": self._chat_mode(),
                "first_events": first_events,
                "answered": {"type": "done", "message_id": step.message_id.id, "state": "complete", "message": ""},
            }
        if run.is_background:
            # What comes next is worked on as what came before: while the
            # person does something else
            conversation.run_id = run
            run._queue()
            return {
                "conversation_id": self.id,
                "user_message_id": False,
                "title": conversation.name,
                "mode": self._chat_mode(),
                "queued": run.id,
                "first_events": first_events,
            }
        run.state = "running"
        conversation.write({"stream_started_at": fields.Datetime.now(), "run_id": run.id,
                            "stream_serial": conversation.stream_serial + 1})

        # What bounds a run is the number of things done for it
        job = self._chat_job(provider, self._chat_mode(), AGENT_MAX_ROUNDS)
        job["first_events"] = first_events
        job.update(serial=conversation.stream_serial, fell_back=notice)
        # The person said yes or no: what the model says of it is not talk
        # instead of work, and is never pushed into the same card again; unless
        # what was said yes to leaves work to do: told "Call prepare_button now",
        # a small model asked the person a question instead
        job["push_to_act"] = job["push_on_question"] = result.unfinished
        return job

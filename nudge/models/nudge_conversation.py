import base64
import base64
import time
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, LockError, UserError
from odoo.tools import config

from .. import tools
from ..llm import base, prompt
from ..llm import attachments
from ..llm import snapshot as screen
from ..profile import retrieval
from .ir_http import nudge_version
from ..tools.base import MODE_AGENT, MODE_DEV, MODE_TALK

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

USER_WRITABLE_FIELDS = frozenset({"name", "active"})

SCREEN_SHARING_PARAM = "nudge.screen_sharing"
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

DONE = (
    "The person said yes, and it is done. %s Tell them in a sentence what was done, or go on "
    "with what is left of what they asked."
)
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


class NudgeConversation(models.Model):
    _name = "nudge.conversation"
    _description = "Nudge Conversation"
    _order = "write_date desc, id desc"

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
    stream_started_at = fields.Datetime(string="Answering Since", copy=False)
    # The request being worked on in Agent mode, and the step of it that
    # waits for the person
    run_id = fields.Many2one("nudge.run", string="Current Run", copy=False, ondelete="set null")
    waiting_step_id = fields.Many2one(
        "nudge.step", string="Waiting For", copy=False, ondelete="set null")
    run_ids = fields.One2many("nudge.run", "conversation_id", string="Runs")

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
    def _get_stream_seconds(self):
        """A streamed request must end well before the server's hard limit on
        the duration of a request, past which the worker is killed."""
        limit = config["limit_time_real"] or 0
        if limit <= 0:
            return STREAM_MAX_SECONDS
        return max(STREAM_MIN_SECONDS, min(STREAM_MAX_SECONDS, limit - STREAM_SAFETY_MARGIN))

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

    def _chat_check_not_busy(self):
        limit = fields.Datetime.now() - timedelta(seconds=self._get_stream_seconds())
        busy = self.sudo().with_context(active_test=False).search_count([
            ("user_id", "=", self.env.uid),
            ("stream_started_at", ">=", limit),
        ], limit=1)
        if busy:
            raise UserError(self.env._("Nudge is still answering your previous message."))

    def _chat_context_values(self, spoken=False, read_aloud=False, model_name=None,
                             explain=None, attachment=None, report=None):
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
        fingerprint = screen.fingerprint(snapshot)
        previous = self.env["nudge.message"].sudo().search([
            ("conversation_id", "=", self.id),
            ("role", "=", "user"),
        ], order="id desc", limit=1)
        unchanged = previous.snapshot_hash == fingerprint
        values = {"snapshot": snapshot, "snapshot_hash": fingerprint}
        return values, screen.render(snapshot, unchanged=unchanged)

    def _chat_provider(self):
        """:return: the provider answering this conversation, in sudo
        :raise UserError: when there is none"""
        provider = self.sudo().provider_id
        if not provider.is_configured or not provider.active:
            provider = self.env["nudge.provider"].sudo()._get_default()
        if not provider:
            raise UserError(self.env._(
                "Nudge is not set up yet. Ask your administrator to configure an AI provider."))
        provider._ensure_capabilities()
        return provider

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
        self.env["nudge.usage"]._check_budget()
        job = self._chat_job(self._chat_provider(), self._chat_mode(), AGENT_MAX_ROUNDS)
        job["seconds"] = self.env["nudge.run"]._get_piece_seconds()
        return job

    def _chat_job(self, provider, mode, rounds):
        """:return: everything the stream needs, as plain data: the
        environment is gone once streaming starts"""
        self.ensure_one()
        conversation = self.sudo()
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
                work="\n\n".join(block for block in (
                    self._chat_where_the_work_is(), self.env["nudge.source"]._get_text()) if block)),
            "tools": [tool.definition() for tool in tools.base.available(self.env, mode)],
            "turns": conversation._chat_build_turns(),
            "seconds": self._get_stream_seconds(),
            "rounds": rounds,
            "first_events": [],
        }

    def _chat_prepare(self, text, snapshot=None, spoken=False, read_aloud=False, mode=MODE_TALK,
                      background=False, explain=None, attachment=None, report=None):
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
        :return: dict describing the job; with ``queued`` when the work is
            left to the background and there is nothing to stream
        """
        self.ensure_one()
        text = (text or "").strip()
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
        self._chat_check_not_busy()
        self._chat_check_not_at_work()
        self.env["nudge.usage"]._check_budget()
        limit = AGENT_MAX_MESSAGES if self.sudo().run_ids else MAX_MESSAGES
        if self.message_count >= limit:
            raise UserError(self.env._("This conversation is getting long. Start a new one."))
        provider = self._chat_provider()

        conversation = self.sudo()
        # What waited for a decision is left: the person moved on
        conversation._agent_leave_undecided()
        conversation.run_id._end("stopped")
        # What is left to the background does not keep the person from asking elsewhere
        values = {
            "stream_started_at": False if background else fields.Datetime.now(),
            "run_id": False,
        }
        if conversation.provider_id != provider or conversation.model_name != provider.model_name:
            values.update(provider_id=provider.id, model_name=provider.model_name)
        if not conversation.name:
            values["name"] = text[:TITLE_CHARS].strip()
        conversation.write(values)
        screen_values, screen_text = self._chat_screen(snapshot)
        voice = self.env["nudge.voice"]._is_allowed()
        spoken = bool(spoken) and voice
        context_text = prompt.render_context(self._chat_context_values(
            spoken=spoken, read_aloud=bool(read_aloud) and voice,
            model_name=(screen_values.get("snapshot") or {}).get("model"),
            explain=explain, attachment=found, report=report))
        if screen_text:
            context_text = "\n".join((context_text, screen_text))
        if found and found["text"]:
            context_text = "\n".join((context_text, prompt.render_attachment(found)))
        message = self.env["nudge.message"].sudo().create({
            "conversation_id": self.id,
            "role": "user",
            "content": text,
            "is_spoken": spoken,
            "mode": mode,
            "context_text": context_text,
            "attachment_name": found["name"] if found else False,
            **screen_values,
        })
        if found and found["image"]:
            media_type, data = found["image"]
            message.attachment_id = self.env["ir.attachment"].sudo().create({
                "name": found["name"],
                "datas": data,
                "mimetype": media_type,
                "res_model": "nudge.message",
                "res_id": message.id,
            })
        elif found and found["kind"] in ("sheet", "text"):
            # A file to import is tried later, as it was sent
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
            tool_choice="required" if explain == "screen" or (
                report == "sheet" and self._can_make_spreadsheet()) else None,
        )

    def _chat_overview(self):
        """:return: what every conversation is told about the database"""
        Profile = self.env["nudge.profile"].sudo()
        Profile._ensure_current()
        return Profile._get_overview()

    def unlink(self):
        # The messages go with the conversation in the database, not through the
        # ORM: the images sent with them would be left behind
        self.sudo().message_ids.attachment_id.unlink()
        return super().unlink()

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
        return [{"media_type": attachment.mimetype, "data": attachment.datas.decode("ascii")}]

    def _chat_build_turns(self):
        """:return: the conversation as the adapters read it"""
        self.ensure_one()
        turns = []
        messages = self.message_ids.sorted("id")
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
            turns.append({
                "role": message.role,
                "text": message.content or "",
                "context": message.context_text or "",
                "images": self._chat_images_of(message),
                "blocks": (message.content_blocks or []) if calls or message.content else [],
                "provider_kind": message.provider_kind or "",
                "model": message.model_name or "",
                "tool_calls": calls or [],
            })
        return turns

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
            "test_import": self.env._("Tried the import"),
            "read_view": self.env._("Read a screen's definition"),
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
            elif waiting and is_action:
                result = tools.base.ToolResult(ONE_AT_A_TIME, is_error=True)
            else:
                # A tool may tie what it makes to this conversation, and
                # what it may reach depends on the mode
                env = self.with_context(nudge_conversation_id=self.id, nudge_mode=mode).env
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
            results.append(dict(entry, content=result.content, is_error=result.is_error))
        return results, events, must_read, waiting

    def _chat_error_message(self, code):
        messages = {
            base.ERROR_AUTH: self.env._(
                "The AI provider rejected the API key. Ask your administrator to check it."),
            base.ERROR_MODEL_NOT_FOUND: self.env._(
                "The AI provider does not know the configured model."),
            base.ERROR_RATE_LIMIT: self.env._(
                "The AI provider's limit is reached. Try again in a minute."),
            base.ERROR_NETWORK: self.env._("The AI provider could not be reached."),
            base.ERROR_UNAVAILABLE: self.env._("The AI provider is temporarily unavailable."),
            base.ERROR_MISSING_LIBRARY: self.env._(
                "A Python library is missing on the server. Ask your administrator."),
        }
        return messages.get(code, self.env._("Something went wrong while answering."))

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
        # In the background, what the time of a piece did not let finish is
        # taken up by the next piece
        later = bool(
            must_read and not waiting and not again and run.is_background
            and state == "complete" and run.state == "running")

        error_message = False
        if error:
            error_message = self._chat_error_message(error["code"])
        elif state == "refused" and not text:
            error_message = self.env._("Nudge cannot help with this request.")
        elif waiting:
            state = "waiting"
        elif later:
            pass
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
            "screen_events": screen_events or False,
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
            "cost": provider._compute_cost(usage) if provider else 0.0,
            **usage,
        })
        if run:
            run._add_usage(message)
            run.step_ids.filtered(lambda step: not step.message_id).message_id = message
        if stop or error:
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
            return {"screen_events": screen_events, "turns": conversation._chat_build_turns()}
        if later:
            run._queue()
            return {"screen_events": screen_events, "event": {
                "type": "queued", "message_id": message.id, "state": state,
                "run_id": run.id, "message": "",
            }}

        conversation.stream_started_at = False
        run._end("failed" if state == "error" else "stopped" if state == "aborted" else "done")
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
        step.write(values)

        message = step.message_id
        results = []
        for entry in message.pending_results or []:
            if entry.get("step_id") == step.id:
                entry = {
                    "id": entry["id"], "name": entry["name"],
                    "content": result.content, "is_error": state == "failed",
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
        if decision not in (CONFIRM, DECLINE):
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
        # What follows the yes is a call to the model
        self.env["nudge.usage"]._check_budget()
        provider = self._chat_provider()

        events = []
        if decision == DECLINE:
            state, result = "declined", tools.base.ToolResult(DECLINED)
        else:
            tool = tools.base.TOOLS.get(step.tool)
            mode = self._chat_mode()
            if not isinstance(tool, tools.base.Action) or not tool.is_available(self.env, mode):
                result = tools.base.ToolResult("This can no longer be done.", is_error=True)
            else:
                # In the environment of the person, never in sudo
                env = self.with_context(nudge_conversation_id=self.id, nudge_mode=mode).env
                result = tools.base.guarded(env, step.tool, lambda: tool.execute(env, step))
            state = "failed" if result.is_error else "done"
            if result.event:
                events.append(result.event)
            # Said plainly: what comes next depends on the model having understood
            result = tools.base.ToolResult(
                (FAILED if result.is_error else DONE) % result.content,
                is_error=result.is_error, records=result.records)
        self._agent_settle(step, state, result, events)
        run = step.run_id
        first_events = [dict(step._get_card(), type="decided"), *events]
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
        conversation.write({"stream_started_at": fields.Datetime.now(), "run_id": run.id})

        # What bounds a run is the number of things done for it
        job = self._chat_job(provider, self._chat_mode(), AGENT_MAX_ROUNDS)
        job["first_events"] = first_events
        return job

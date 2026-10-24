import ipaddress
import logging
import os
import time
from urllib.parse import urlparse

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..llm import base, ollama
from ..tools.deadline import until

_logger = logging.getLogger(__name__)

# How much of the context a request takes, roughly: JSON and English run
# about this many characters to a token
CHARS_PER_TOKEN = 4.0
# What is known of the database, where the business works, the files
OVERVIEW_TOKENS = 1500
# The conversation, the screen shared, what the tools read, the answer
CONVERSATION_TOKENS = 5000

DEFAULT_PROVIDER_PARAM = "nudge.default_provider_id"
# A model on a machine of one's own reads a long question slowly: the time it
# is given to answer, at least
OLLAMA_TIMEOUT = 180
# The provider of the work: what the agent does, what a developer builds.
# Empty: the default provider answers in every mode.
AGENT_PROVIDER_PARAM = "nudge.agent_provider_id"
DEVELOPER_PROVIDER_PARAM = "nudge.developer_provider_id"
MODE_PROVIDER_PARAMS = {"agent": AGENT_PROVIDER_PARAM, "dev": DEVELOPER_PROVIDER_PARAM}
# NP-10: the provider standing in when the one of the conversation cannot answer (an error, no key,
# the budget used up, private mode). Empty: none, the person is told why.
FALLBACK_PROVIDER_PARAM = "nudge.fallback_provider_id"
# The address Ollama listens on by default: one question at a time, unless it is told otherwise
OLLAMA_PORT = ":11434"


def _is_private_address(url):
    """:return: whether the host of ``url`` is this machine or a private network"""
    host = urlparse(url if "//" in url else f"//{url}").hostname or ""
    if host == "localhost" or host.endswith((".local", ".internal", ".lan")):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


class NudgeProvider(models.Model):
    _name = "nudge.provider"
    _description = "Nudge AI Provider"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    kind = fields.Selection(
        [
            ("openai_compatible", "OpenAI-compatible API"),
            ("anthropic", "Anthropic Claude"),
        ],
        string="Protocol", required=True, default="openai_compatible",
        help="OpenAI-compatible covers Google Gemini, Groq, OpenRouter, Ollama and OpenAI.",
    )
    base_url = fields.Char(
        string="Base URL",
        help="Address of the API, without the final /chat/completions. "
             "Leave empty for Anthropic to use the official endpoint.",
    )
    runs_locally = fields.Boolean(
        string="Runs on Our Own Server", default=False,
        help="Tick it only when this provider, the address of its speech service included, runs "
             "on a server of yours: nothing leaves your own network. With Private Mode on "
             "(Settings, Nudge), a provider without it is never asked anything.")
    address_warning = fields.Char(compute="_compute_address_warning")
    looks_local = fields.Boolean(
        string="Address Looks Local", compute="_compute_looks_local",
        help="The address is this machine or a private network. Only a hint: the administrator confirms.")
    api_key = fields.Char(string="API Key", groups="base.group_system", copy=False)
    workspace_id = fields.Char(
        string="Workspace ID", groups="base.group_system", copy=False,
        help="Needed only with a key that is not scoped to a workspace, such as a user key "
             "(sk-ant-usr-...): the workspace its requests are made in. It starts with "
             "\"wrkspc_\", and is found in the Console under Settings ‣ Workspaces.",
    )
    has_api_key = fields.Boolean(
        string="API Key Set", compute="_compute_has_api_key", compute_sudo=True)
    requires_api_key = fields.Boolean(
        string="Requires an API Key", default=True,
        help="Untick for a local server such as Ollama that accepts requests without a key.",
    )
    model_name = fields.Char(string="Model", help="Model identifier sent to the provider.")
    model_ids = fields.One2many("nudge.provider.model", "provider_id", string="Available Models")
    capabilities = fields.Json(copy=False)
    max_output_tokens = fields.Integer(
        string="Maximum Output Tokens",
        help="Upper limit of tokens generated per answer. 0 lets the provider decide.",
    )
    effort = fields.Selection(
        [
            ("none", "No reasoning"),
            ("low", "Low"),
            ("medium", "Medium"),
            ("high", "High"),
            ("xhigh", "Extra high"),
            ("max", "Maximum"),
        ],
        help="How much reasoning the model spends on an answer. Lower is faster and cheaper: a "
             "free tier that takes long per call answers in time with Low; a model run by Ollama "
             "on a laptop needs No reasoning. Sent to Anthropic "
             "Claude as its effort, and to OpenAI-compatible providers (Google Gemini, OpenAI) "
             "as reasoning_effort (none, low, medium or high); a provider that refuses it is asked "
             "again without it.",
    )
    refusal_fallback = fields.Boolean(
        string="Fallback on Refusal", default=True,
        help="When the model declines a request for safety reasons, let the provider retry it "
             "on its recommended fallback model.",
    )
    timeout = fields.Integer(string="Timeout (seconds)", default=60)
    # NP-04: past it the questions for this provider wait in line, as past the limit of Nudge
    max_parallel = fields.Integer(
        string="Answers at Once", default=0,
        help="How many answers this provider gives at the same time; the others wait in line, "
             "and start as soon as one ends. 0 is automatic: one for Ollama (a model on one "
             "machine answers one question at a time), otherwise only the limit of Nudge.")
    tool_set = fields.Selection(
        [("full", "All the tools"), ("essential", "Essential tools")],
        string="Tools Given", default="full",
        help="Essential tools: finding, reading, showing, and what each mode is for, about half "
             "of them. A small model, such as one run by Ollama on a laptop, reads them faster "
             "and picks the right one more often; and less context is needed.",
    )
    is_ollama = fields.Boolean(
        string="Ollama Server", readonly=True, copy=False,
        help="Ollama answers at this address: Nudge reads which models use tools and see "
             "pictures, and the context the server gives them.",
    )
    ollama_version = fields.Char(readonly=True, copy=False)
    context_length = fields.Integer(
        string="Context Given", readonly=True, copy=False,
        help="The tokens the server gives the model in use, read when the connection was "
             "last tested. 0: not known.",
    )
    context_needed = fields.Integer(
        string="Context Nudge Needs", compute="_compute_context_needed",
        help="About the tokens of the heaviest request Nudge sends with these tools: Developer "
             "mode, the instructions, the tools and room for the conversation.",
    )
    context_warning = fields.Char(compute="_compute_context_needed")
    picture_model = fields.Char(
        string="Picture Model",
        help="A model of this Ollama server that sees pictures, such as qwen2.5vl:3b, for a model "
             "in use that does not: it reads the picture sent, and what it read goes with the "
             "question. Prepare for Nudge chooses one when the server has it.")
    stt_mode = fields.Selection(
        [
            (base.STT_NONE, "Not available"),
            (base.STT_CHAT, "A model that listens"),
            (base.STT_ENDPOINT, "Transcription service"),
        ],
        string="Speech to Text", required=True, default=base.STT_NONE,
        help="How this provider turns what people say into text, for the browsers that cannot "
             "do it themselves.\n"
             "A model that listens: the recording is given to a chat model, as Google Gemini "
             "accepts.\n"
             "Transcription service: the recording is sent to /audio/transcriptions, as OpenAI "
             "and Groq offer.",
    )
    stt_model = fields.Char(
        string="Speech Model",
        help="Model that turns speech into text. For a model that listens, empty uses the "
             "model of the conversations.",
    )
    stt_url = fields.Char(
        string="Speech Address",
        help="The address of the transcription service, when it is not this provider's: a "
             "Whisper on your own machine for an Ollama, which does not transcribe "
             "(http://localhost:8000/v1). The key of this provider is not sent there.")
    can_transcribe = fields.Boolean(
        string="Turns Speech into Text", compute="_compute_can_transcribe", compute_sudo=True)
    price_input = fields.Float(string="Input Price", digits=(12, 4), help="Per million tokens.")
    price_output = fields.Float(string="Output Price", digits=(12, 4), help="Per million tokens.")
    price_cache_read = fields.Float(
        string="Cache Read Price", digits=(12, 4), help="Per million tokens.")
    price_cache_write = fields.Float(
        string="Cache Write Price", digits=(12, 4), help="Per million tokens.")
    notes = fields.Text(translate=True)
    is_configured = fields.Boolean(
        string="Ready", compute="_compute_is_configured", compute_sudo=True)

    _sql_constraints = [
        ("check_timeout", "CHECK(timeout > 0)", "The timeout must be strictly positive."),
        ("check_max_output_tokens", "CHECK(max_output_tokens >= 0)",
         "The maximum of output tokens cannot be negative."),
    ]

    @api.depends("tool_set", "context_length", "is_ollama")
    def _compute_context_needed(self):
        for provider in self:
            needed = provider._tokens_needed()
            provider.context_needed = needed
            provider.context_warning = (
                self.env._(
                    "The server gives this model %(given)s tokens; Nudge needs about %(needed)s. "
                    "What does not fit is cut without a word, and answers go wrong: press Prepare "
                    "for Nudge, or choose the essential tools.",
                    given=provider.context_length, needed=needed)
                if provider.is_ollama and 0 < provider.context_length < needed else False)

    def _tokens_needed(self, mode=None):
        """:param mode: by default the heaviest this database opens:
            Developer mode when its switch is on, else Agent mode
        :return: about the tokens of a request in ``mode`` with this
        provider's tools: every tool of the mode, whoever asks, the
        instructions and room for a conversation"""
        self.ensure_one()
        if mode is None:
            mode = self._served_mode()
        from ..llm import prompt
        from ..tools import base as tool_base, groups
        essential = self.tool_set == "essential"
        if essential:
            # The tools of the mode, and room for the two largest groups
            names = tool_base.ESSENTIAL_BY_MODE[mode] | {"more_tools"}
            extra_chars = groups.largest(mode)
        else:
            names = {name for name, tool in tool_base.TOOLS.items()
                     if (mode in tool.modes or (mode == "dev" and "agent" in tool.modes))
                     and not getattr(tool, "small_models_only", False)}
            extra_chars = 0
        tools_chars = extra_chars + sum(
            len(tool_base.dump(tool_base.TOOLS[name].definition())) for name in names if name in tool_base.TOOLS)
        # The instructions of the mode, as long as they are
        rules = prompt.build_system_blocks(mode=mode, can_use_agent=True, can_develop=True, compact=essential)
        prompt_chars = sum(len(block) for block in rules)
        return int((tools_chars + prompt_chars) / CHARS_PER_TOKEN) + OVERVIEW_TOKENS + CONVERSATION_TOKENS

    def _served_mode(self):
        """:return: the heaviest mode this provider answers: Talk only when the
        work has a provider of its own, set in Settings"""
        self.ensure_one()
        Provider = self.env["nudge.provider"].sudo()
        developer = self.env["nudge.conversation"]._is_developer_allowed()
        if developer and self == Provider._get_for_mode("dev"):
            return "dev"
        agent = Provider._get_for_mode("agent")
        if self == agent:
            return "agent"
        if agent:
            return "talk"
        return "dev" if developer else "agent"

    @api.depends("api_key")
    def _compute_has_api_key(self):
        for provider in self:
            provider.has_api_key = bool(provider.api_key)

    @api.depends("api_key", "requires_api_key", "model_name", "kind", "base_url")
    def _compute_is_configured(self):
        for provider in self:
            provider.is_configured = bool(
                provider.model_name
                and (provider.kind == "anthropic" or provider.base_url)
                and provider._has_credentials()
            )

    @api.depends("is_configured", "kind", "stt_mode", "stt_model")
    def _compute_can_transcribe(self):
        for provider in self:
            provider.can_transcribe = bool(
                provider.is_configured
                and provider.kind == "openai_compatible"
                and (
                    provider.stt_mode == base.STT_CHAT
                    or (provider.stt_mode == base.STT_ENDPOINT and provider.stt_model)
                )
            )

    def _has_credentials(self):
        self.ensure_one()
        if self.api_key or not self.requires_api_key:
            return True
        # The Anthropic SDK also reads its key from the environment
        return self.kind == "anthropic" and bool(os.environ.get("ANTHROPIC_API_KEY"))

    @api.onchange("kind")
    def _onchange_kind(self):
        if self.kind == "anthropic":
            self.requires_api_key = True

    @staticmethod
    def _clean_key(vals):
        # A blank or a line break around a pasted key makes the HTTP header
        # invalid: the request is refused before it leaves
        if vals.get("api_key"):
            return dict(vals, api_key=vals["api_key"].strip())
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([self._clean_key(vals) for vals in vals_list])

    def write(self, vals):
        vals = self._clean_key(vals)
        if "capabilities" not in vals and {"model_name", "kind", "base_url"} & vals.keys():
            # Capabilities belong to one model of one provider
            vals = dict(vals, capabilities=False)
        return super().write(vals)

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------
    @api.model
    def _get_default(self):
        """:return: the provider answering new conversations, possibly empty: the one named in the
        default parameter, else the fallback (NP-10), else none. Only when nothing was ever named
        and exactly one provider is configured is that one used.
        Must be called in sudo: it is infrastructure, users have no access to providers."""
        param = self.env["ir.config_parameter"].sudo().get_param(DEFAULT_PROVIDER_PARAM)
        private = self._is_private_mode()
        if param and param.isdigit():
            provider = self.browse(int(param)).exists()
            if provider.active and provider.is_configured and (provider.runs_locally or not private):
                return provider
            # NP-10: the one the administrator named cannot answer: the one they chose to stand in does
            fallback = self._get_fallback()
            if fallback:
                return fallback
        providers = self.search([]).filtered("is_configured")
        if private:
            # In private mode the ones on our own server come first: the others are refused
            return providers.filtered("runs_locally").sorted("sequence")[:1] or providers[:1]
        if param and param.isdigit():
            # LEAD-03: the administrator named one and it cannot answer: never another, which
            # may be a cloud provider they did not choose. The rules-only path still serves.
            return self.browse()
        # First install, nothing chosen yet: the only configured provider answers
        return providers if len(providers) == 1 else self.browse()

    @api.model
    def _get_for_mode(self, mode):
        """:return: the provider set for ``mode`` ("agent" or "dev"), ready to
        answer, possibly empty; Developer mode falls back on the one of the
        agent. Must be called in sudo, as :meth:`_get_default`."""
        Parameter = self.env["ir.config_parameter"].sudo()
        for param in ([DEVELOPER_PROVIDER_PARAM, AGENT_PROVIDER_PARAM] if mode == "dev"
                      else [AGENT_PROVIDER_PARAM] if mode == "agent" else []):
            value = Parameter.get_param(param)
            if value and value.isdigit():
                provider = self.browse(int(value)).exists()
                if provider.active and provider.is_configured:
                    if self._is_private_mode() and not provider.runs_locally:
                        # NP-10: private mode sends nothing to it; the model of our own server
                        # chosen to stand in answers instead, when there is one (else refused)
                        return self._get_fallback() or provider
                    return provider
        return self.browse()

    @api.model
    def _get_named(self, mode):
        """:return: the provider the administrator named for ``mode`` ("talk", "agent" or "dev"),
        ready to answer or not, possibly empty. Must be called in sudo."""
        Parameter = self.env["ir.config_parameter"].sudo()
        names = {"dev": [DEVELOPER_PROVIDER_PARAM, AGENT_PROVIDER_PARAM, DEFAULT_PROVIDER_PARAM],
                 "agent": [AGENT_PROVIDER_PARAM, DEFAULT_PROVIDER_PARAM]}.get(mode, [DEFAULT_PROVIDER_PARAM])
        for param in names:
            value = Parameter.get_param(param)
            if value and value.isdigit():
                # Archived or not: what was named, not what is ready
                provider = self.browse(int(value)).exists()
                if provider:
                    return provider
        return self.browse()

    @api.model
    def _get_fallback(self):
        """NP-10: the provider the administrator chose to stand in when the one of a conversation
        cannot answer, ready to answer, possibly empty. In private mode only one that runs on
        our own server: nothing goes to a provider outside that the administrator did not choose.
        Must be called in sudo, as :meth:`_get_default`."""
        value = self.env["ir.config_parameter"].sudo().get_param(FALLBACK_PROVIDER_PARAM)
        if not value or not value.isdigit():
            return self.browse()
        provider = self.browse(int(value)).exists()
        if not (provider.active and provider.is_configured):
            return self.browse()
        if self._is_private_mode() and not provider.runs_locally:
            return self.browse()
        return provider

    def _get_max_parallel(self):
        """NP-04: :return: how many answers this provider gives at once; 0 is no limit of its own"""
        self.ensure_one()
        if self.max_parallel > 0:
            return self.max_parallel
        return 1 if self.is_ollama or OLLAMA_PORT in (self.base_url or "") else 0

    @api.model
    def _get_listener(self):
        """:return: the provider turning speech into text, possibly empty: the
        one of the conversations when it can, otherwise the first that can.
        Must be called in sudo, as :meth:`_get_default`."""
        default = self._get_default()
        if default.can_transcribe and (default.runs_locally or not self._is_private_mode()):
            return default
        able = self.search([]).filtered("can_transcribe")
        # Private mode: the voice goes only to a provider on our own server
        return (able.filtered("runs_locally") if self._is_private_mode() else able)[:1]

    def _get_config(self):
        self.ensure_one()
        return base.ProviderConfig(
            kind=self.kind,
            model=self.model_name or "",
            api_key=(self.api_key or "").strip(),
            base_url=(self.base_url or "").strip(),
            workspace_id=(self.workspace_id or "").strip(),
            max_output_tokens=self.max_output_tokens,
            effort=self.effort or "",
            refusal_fallback=self.refusal_fallback,
            timeout=self.timeout,
            capabilities=self.capabilities or {},
            stt_mode=self.stt_mode or base.STT_NONE,
            stt_model=(self.stt_model or "").strip(),
            stt_url=(self.stt_url or "").strip(),
        )

    @api.constrains("base_url")
    def _check_base_url(self):
        for provider in self:
            url = (provider.base_url or "").strip()
            if url and not url.lower().startswith(("http://", "https://")):
                raise ValidationError(self.env._(
                    "The address of the provider must start with http:// or https://"))

    @api.depends("base_url", "runs_locally")
    def _compute_address_warning(self):
        for provider in self:
            url = (provider.base_url or "").strip()
            warning = False
            if url and not _is_private_address(url):
                if url.lower().startswith("http://"):
                    warning = self.env._(
                        "This address is public and not encrypted (http): the key and your data "
                        "travel unencrypted. Use https.")
                elif provider.runs_locally:
                    warning = self.env._(
                        "Runs on Our Own Server is ticked but the address is public: private mode "
                        "will send data to this address. Make sure it is your own server.")
            provider.address_warning = warning

    @api.depends("base_url", "stt_url")
    def _compute_looks_local(self):
        for provider in self:
            addresses = [url for url in (provider.base_url, provider.stt_url) if url]
            provider.looks_local = bool(addresses) and all(_is_private_address(url) for url in addresses)

    @api.model
    def _is_private_mode(self):
        """:return: whether the company in use asks that no data goes to an
        AI provider outside its own server"""
        # sudo: the setting is the company's; everybody who asks must obey it
        return bool(self.env.company.sudo().nudge_private_mode)

    def _check_private(self):
        """The one check every call to a provider goes through, on the server,
        before anything is sent: the answer, the picture, the voice, the key
        test. A provider that is not marked as running on our own server is
        refused while Private Mode is on.

        :raise UserError: when it is refused"""
        if self._is_private_mode():
            for provider in self.filtered(lambda provider: not provider.runs_locally):
                raise UserError(self.env._(
                    "Private mode is on: nothing is sent to %s, which does not run on your own "
                    "server. Ask an administrator for a provider that does (Settings, Nudge), "
                    "or use quick search and the slash commands, which need no AI.",
                    provider.name))

    @api.model
    def _complete(self, system, prompt, user, company, timeout=40, check_budget=True, **links):
        """One short answer of the provider in use, outside any conversation:
        the narrative of a digest, the sentence of a website assistant.
        Never raises; the call is written in the ledger of usage.

        :param str system: the instructions
        :param str prompt: what is asked, already framed as data where it holds data
        .. note:: ``check_budget`` False: the caller has a budget of its own.

        :param user: who the call is made for, and whose budget it counts against
        :param links: more values of the line of usage (``origin``...)
        :return: the text, or "" when no provider can answer (none set up,
            private mode, a limit reached, a failure): the caller then has the numbers"""
        provider = self.sudo()._get_for_mode("agent") or self.sudo()._get_default()
        if not provider or not provider.is_configured or not provider.active:
            return ""
        try:
            provider._check_private()
            adapter = provider._get_adapter()
            if check_budget:
                self.with_user(user.id).env["nudge.usage"]._check_budget()
        except UserError:
            return ""
        turns = [{"role": "user", "text": prompt, "context": ""}]
        deadline = until(timeout)
        text, usage = [], None
        try:
            for event in adapter.stream_chat(system, turns, deadline=deadline):
                if event["type"] == "text":
                    text.append(event["text"])
                elif event["type"] == "stop":
                    usage = event.get("usage")
                elif event["type"] == "error":
                    return ""
        except Exception:  # noqa: BLE001
            _logger.info("Nudge: a short answer could not be had", exc_info=True)
            return ""
        usage = base.normalize_usage(usage)
        self.env["nudge.usage"]._add(
            "chat", usage, provider._compute_cost(usage), user, company,
            provider_id=provider.id, model_name=provider.model_name, **links)
        return "".join(text).strip()

    def _get_adapter(self):
        self.ensure_one()
        self._check_private()
        adapter = base.get_adapter(self._get_config())
        # NP-13: a provider beyond the company's own server gets tokens for personal data, not the data
        # sudo: the switch is Nudge's own setting; a person who may ask Nudge may not read companies
        if not self.runs_locally and self.env.company.sudo().nudge_redact_pii:
            from ..llm.redact import RedactingAdapter  # noqa: PLC0415
            adapter = RedactingAdapter(adapter)
        return adapter

    def _compute_cost(self, usage):
        """:param dict usage: normalized token counts
        :return: price of one answer, in the currency of the provider's price list"""
        self.ensure_one()
        return (
            usage["input_tokens"] * self.price_input
            + usage["output_tokens"] * self.price_output
            + usage["cache_read_tokens"] * self.price_cache_read
            + usage["cache_write_tokens"] * self.price_cache_write
        ) / 1_000_000

    def _ensure_capabilities(self):
        """Ask the provider once what the configured model supports."""
        self.ensure_one()
        if self.kind != "anthropic" or self.capabilities or not self.model_name:
            return
        try:
            self.capabilities = self._get_adapter().fetch_capabilities()
        except base.AdapterError as exc:
            _logger.info("Nudge: capabilities of %s not available: %s", self.model_name, exc)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _notify(self, message, notification_type="success"):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": notification_type,
                "message": message,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def _error_message(self, error):
        messages = {
            base.ERROR_AUTH: self.env._("The provider rejected the API key."),
            base.ERROR_WORKSPACE: self.env._(
                "This API key is not scoped to a workspace: fill in the Workspace ID, which starts "
                "with wrkspc_ and is found in the Console under Settings ‣ Workspaces."),
            base.ERROR_MODEL_NOT_FOUND: self.env._("The provider does not know this model."),
            base.ERROR_RATE_LIMIT: self.env._("The provider's rate limit is reached. Try again in a minute."),
            base.ERROR_NETWORK: self.env._("The provider could not be reached."),
            base.ERROR_TIMEOUT: self.env._(
                "The provider took too long to answer. A model that is still loading answers the "
                "next time; a thinking model answers faster with a lower Reasoning Effort."),
            base.ERROR_UNAVAILABLE: self.env._("The provider is temporarily unavailable."),
            base.ERROR_MISSING_LIBRARY: self.env._(
                "The Python package of this provider is not installed on the server. Install it with "
                "the command below, or on Odoo.sh add its name to the requirements.txt at the root of "
                "the repository, then restart Odoo."),
            base.ERROR_UNSUPPORTED: self.env._("The provider cannot do this."),
        }
        message = messages.get(error.code, self.env._("The provider returned an error."))
        return f"{message}\n{error.detail}" if error.detail else message

    def _check_ready(self):
        self.ensure_one()
        self._check_private()
        if not self.model_name:
            raise UserError(self.env._("Set a model first."))
        if not self._has_credentials():
            raise UserError(self.env._("Set an API key first."))

    def action_test_connection(self):
        self._check_ready()
        try:
            self._get_adapter().check()
        except base.AdapterError as exc:
            raise UserError(self._error_message(exc)) from exc
        self._ensure_capabilities()
        warnings = self._ollama_read() if self.kind == "openai_compatible" else []
        if warnings:
            return self._notify("\n".join([self.env._("Connection successful.")] + warnings), "warning")
        return self._notify(self.env._("Connection successful."))

    # ------------------------------------------------------------------
    # Ollama
    # ------------------------------------------------------------------
    def _ollama_detect(self):
        """Whether Ollama answers at the address; the first time it does,
        the essential tools are chosen, which a model of a laptop needs.
        :return: the lines to tell"""
        self.ensure_one()
        self._check_private()
        found = ollama.version(self.base_url, self.api_key or "")
        told = []
        if found and not self.is_ollama:
            values = {"is_ollama": True, "tool_set": "essential"}
            if not self.effort:
                # A thinking model on a laptop spends a minute thinking
                # before a word: the answer would not come in time
                values["effort"] = "none"
            self.write(values)
            told.append(self.env._(
                "Ollama %s answers here: the essential tools and no reasoning were chosen for its "
                "models, which a laptop needs to answer in time. A large model on a strong server "
                "can be given all the tools and its reasoning.", found))
        elif not found and self.is_ollama:
            self.is_ollama = False
        if found:
            self.ollama_version = found
        return told

    def _ollama_read(self):
        """After a successful call: what the model in use can do, and the
        context the server gave it. :return: warnings, in words"""
        self.ensure_one()
        warnings = self._ollama_detect()
        if not self.is_ollama:
            return warnings
        try:
            facts = ollama.describe(self.base_url, self.model_name, self.api_key or "")
            given = ollama.loaded_context(self.base_url, self.model_name, self.api_key or "")
        except base.AdapterError as exc:
            _logger.info("Nudge: Ollama did not describe %s: %s", self.model_name, exc)
            return warnings
        self.context_length = given or facts["context_set"]
        try:
            share = ollama.on_processor(*ollama.memory(self.base_url, self.model_name, self.api_key or ""))
        except base.AdapterError:
            share = 0
        if share:
            warnings.append(self._processor_warning(share))
        if not facts["tools"]:
            warnings.append(self.env._(
                "%s cannot use tools: Nudge can talk with it, not act or build. For Agent and "
                "Developer modes, choose a model with tools: qwen3, llama3.1, mistral-nemo.",
                self.model_name))
        needed = self._tokens_needed()
        if 0 < self.context_length < needed:
            warnings.append(self.env._(
                "The server gives %(model)s %(given)s tokens of context; Nudge needs about "
                "%(needed)s. Press Prepare for Nudge: a copy of the model with enough context is "
                "made and chosen. Or set OLLAMA_CONTEXT_LENGTH on the server.",
                model=self.model_name, given=self.context_length, needed=needed))
        return warnings

    def _can_see_pictures(self):
        """:return: whether the model in use reads pictures; True when it is not
        known, and the provider says so itself"""
        self.ensure_one()
        self._check_private()
        if not self.is_ollama:
            return True
        line = self.model_ids.filtered(lambda line: line.name == self.model_name)[:1]
        if line:
            return line.sees_pictures
        try:
            return ollama.describe(self.base_url, self.model_name, self.api_key or "")["vision"]
        except base.AdapterError:
            return True

    def _read_picture(self, media_type, data, question, model=None, prompt=None):
        """:param data: the picture, in base64
        :param model: the model that reads it; the picture model by default
        :param prompt: what it is asked of the picture; a description by default
        :return: what the picture model of this server read in it
        :raise UserError: when there is none, or it could not"""
        self.ensure_one()
        self._check_private()
        model = model or self.picture_model
        if not model:
            raise UserError(self.env._(
                "%s cannot read pictures. Describe it in words, or ask your administrator for a "
                "model that sees them.", self.model_name))
        try:
            text, usage = ollama.read_picture(
                self.base_url, model, data, question, self.api_key or "", prompt=prompt or ollama.PICTURE_PROMPT)
        except base.AdapterError as exc:
            if "load image" in (exc.detail or ""):
                raise UserError(self.env._(
                    "This file could not be read as a picture: send a photo, PNG or JPEG.")) from exc
            raise UserError(self._error_message(exc)) from exc
        self.env["nudge.usage"]._add(
            "chat", base.normalize_usage(usage), 0.0, self.env.user, self.env.company,
            provider_id=self.id, model_name=model)
        return text

    def _complete_once(self, system, text, images=None):
        """One question and one answer, with no tools: a document read into JSON.

        :param images: ``[{"media_type", "data" in base64}]`` to look at
        :return: the words of the answer
        :raise UserError: when the provider is refused (Private Mode) or fails"""
        self.ensure_one()
        adapter = self._get_adapter()
        turns = [{"role": "user", "text": text, "images": images or []}]
        answer, usage, failed = "", None, None
        deadline = until(max(self.timeout or 0, 60))
        for event in adapter.stream_chat(system, turns, deadline=deadline):
            if event["type"] == "text":
                answer += event["text"]
            elif event["type"] == "stop":
                usage = event["usage"]
            elif event["type"] == "error":
                failed = base.AdapterError(event["code"], event.get("detail", ""))
        if usage:
            self.env["nudge.usage"]._add(
                "chat", usage, self._compute_cost(usage), self.env.user, self.env.company,
                provider_id=self.id, model_name=self.model_name)
        if failed and not answer:
            raise UserError(self._error_message(failed))
        return answer

    def action_ollama_prepare(self):
        """Make a copy of the model in use with the context Nudge needs, and
        choose it. Ollama shares the weights: nothing is downloaded."""
        self.ensure_one()
        self._check_ready()
        if not self.is_ollama:
            raise UserError(self.env._("Prepare for Nudge is for an Ollama server."))
        try:
            facts = ollama.describe(self.base_url, self.model_name, self.api_key or "")
            context = ollama.context_for(self._tokens_needed(), facts["context_max"])
            name = ollama.prepare(self.base_url, self.model_name, context, self.api_key or "")
        except base.AdapterError as exc:
            raise UserError(self._error_message(exc)) from exc
        self.write({"model_name": name, "context_length": context,
                    "timeout": max(self.timeout or 0, OLLAMA_TIMEOUT)})
        self.action_fetch_models()
        if self._can_see_pictures():
            # It reads pictures itself: no second model is loaded beside it
            self.picture_model = False
        elif not self.picture_model:
            # A model of the server that sees reads the pictures for it
            seeing = self.model_ids.filtered("sees_pictures").sorted("name")[:1]
            if seeing:
                self.picture_model = seeing.name
        told = self.env._("%(name)s is ready with %(context)s tokens of context, and chosen.",
                          name=name, context=context)
        try:
            # Loaded now: what does not fit on the graphics card is said at once
            share = ollama.on_processor(*ollama.memory(self.base_url, name, self.api_key or "", load=True))
        except base.AdapterError as exc:
            _logger.info("Nudge: Ollama did not load %s: %s", name, exc)
            share = None
        if share is None:
            told += " " + self.env._("Test the connection: the first answer loads it.")
        elif share:
            told += " " + self._processor_warning(share)
        else:
            told += " " + self.env._("It runs entirely on the graphics card.")
        return self._notify(told)

    def _processor_warning(self, share):
        return self.env._(
            "%(share)s%% of the model runs on the processor, not the graphics card: answers come "
            "several times slower. Choose a smaller model, or set OLLAMA_FLASH_ATTENTION=1 and "
            "OLLAMA_KV_CACHE_TYPE=q8_0 on the server (q4_0 on a card of 6 GB) and press Prepare "
            "for Nudge again.", share=share)

    def action_fetch_models(self):
        self.ensure_one()
        if not self._has_credentials():
            raise UserError(self.env._("Set an API key first."))
        try:
            names = self._get_adapter().list_models()
        except base.AdapterError as exc:
            raise UserError(self._error_message(exc)) from exc
        known = self.model_ids.mapped("name")
        self.model_ids.filtered(lambda line: line.name not in names).unlink()
        self.env["nudge.provider.model"].create([
            {"provider_id": self.id, "name": name} for name in names if name not in known
        ])
        told = self._ollama_detect() if self.kind == "openai_compatible" else []
        if self.is_ollama:
            for line in self.model_ids:
                try:
                    facts = ollama.describe(self.base_url, line.name, self.api_key or "")
                except base.AdapterError:
                    continue
                line.write({
                    "uses_tools": facts["tools"], "sees_pictures": facts["vision"],
                    "context_max": facts["context_max"], "size": facts["size"],
                })
        return self._notify("\n".join([self.env._("%s models found.", len(names))] + told))


class NudgeProviderModel(models.Model):
    _name = "nudge.provider.model"
    _description = "Nudge AI Provider Model"
    _order = "name"

    provider_id = fields.Many2one("nudge.provider", required=True, ondelete="cascade", index=True)
    name = fields.Char(required=True)
    is_selected = fields.Boolean(string="In Use", compute="_compute_is_selected")
    # Read from an Ollama server; other providers do not say
    uses_tools = fields.Boolean(string="Tools", help="Can act and build: Agent and Developer modes.")
    sees_pictures = fields.Boolean(string="Pictures", help="Reads the pictures sent with a question.")
    context_max = fields.Integer(string="Context at Most")
    size = fields.Char()

    @api.depends("name", "provider_id.model_name")
    def _compute_is_selected(self):
        for line in self:
            line.is_selected = line.name == line.provider_id.model_name

    def action_select(self):
        self.ensure_one()
        self.provider_id.model_name = self.name

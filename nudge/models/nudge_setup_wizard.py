import json
import logging

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import file_open

from .nudge_conversation import AGENT_PARAM, DEVELOPER_PARAM
from .nudge_provider import DEFAULT_PROVIDER_PARAM
from .nudge_usage import BUDGET_PARAMS

_logger = logging.getLogger(__name__)

PRICE_TABLE = "nudge/data/nudge_price_table.json"

STEPS = [
    ("users", "Users and Mode"),
    ("provider", "Provider"),
    ("key", "Key and Test"),
    ("estimate", "Monthly Estimate"),
    ("budgets", "Budgets"),
    ("enable", "Enable"),
]
MODES = [("talk", "Talk"), ("agent", "Agent"), ("developer", "Developer")]
CHOICES = [
    ("rules", "Rules only, no AI provider"),
    ("ollama", "Local Ollama"),
    ("claude", "Anthropic Claude"),
    ("gemini", "Google Gemini"),
    ("other", "Other OpenAI-compatible API"),
]
# The provider records the module ships, by choice; "other" is created
SEEDED = {"ollama": "nudge.provider_ollama", "claude": "nudge.provider_anthropic",
          "gemini": "nudge.provider_gemini"}


def price_table():
    """:return: the dated price table shipped as data (a dict)"""
    with file_open(PRICE_TABLE) as handle:
        return json.load(handle)


def estimate(users, messages_per_day, working_days, calls_per_message, prompt_tokens,
             output_tokens, price_input, price_output):
    """The monthly figures of a team.

    :return: dict of messages, calls, input and output tokens, cost, and the cost of one
        person for a month and of one message
    """
    messages = max(users, 0) * max(messages_per_day, 0) * max(working_days, 0)
    calls = messages * max(calls_per_message, 0)
    tokens_in = calls * max(prompt_tokens, 0)
    tokens_out = calls * max(output_tokens, 0)
    cost = tokens_in / 1e6 * price_input + tokens_out / 1e6 * price_output
    return {
        "messages": messages,
        "calls": calls,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost": cost,
        "cost_person": cost / users if users > 0 else 0.0,
        "cost_message": cost / messages if messages else 0.0,
    }


class NudgeSetupWizard(models.TransientModel):
    """Choose a provider, see what it will cost, set the budgets and switch Nudge on.

    It opens before any provider exists, and ends with rules only when none is wanted. A key
    typed here goes to the provider record (readable by system administrators only) and is
    wiped from the wizard at once."""
    _name = "nudge.setup.wizard"
    _description = "Nudge Setup Wizard"

    step = fields.Selection(STEPS, default="users", required=True)
    user_count = fields.Integer(string="People Using Nudge", default=10)
    messages_per_day = fields.Integer(string="Messages per Person and per Day", default=20)
    working_days = fields.Integer(string="Working Days per Month")
    mode = fields.Selection(MODES, string="Mode Used Most", default="talk", required=True)
    all_users = fields.Boolean(string="Nudge for All Internal Users", default=True)

    choice = fields.Selection(CHOICES, string="Provider", default="rules", required=True)
    pros = fields.Text(compute="_compute_texts")
    cons = fields.Text(compute="_compute_texts")
    privacy = fields.Text(compute="_compute_texts")
    base_url = fields.Char(string="Base URL")
    model_name = fields.Char(string="Model")
    # Not stored: the key goes straight to the provider record and never reaches the table
    # of this wizard
    api_key = fields.Char(
        string="API Key", groups="base.group_system", copy=False,
        compute="_compute_api_key", inverse="_inverse_api_key",
        help="Kept on the provider, where only system administrators read it. This screen never "
             "shows it again, and Nudge never writes it in a log.")
    provider_id = fields.Many2one("nudge.provider", string="Provider Record", readonly=True)
    tested = fields.Boolean(readonly=True)
    test_result = fields.Text(readonly=True)

    price_date = fields.Char(string="Prices Dated", compute="_compute_price_table")
    price_note = fields.Char(compute="_compute_price_table")
    price_input = fields.Float(string="Input Price (USD per Million Tokens)", digits=(12, 4))
    price_output = fields.Float(string="Output Price (USD per Million Tokens)", digits=(12, 4))
    calls_per_message = fields.Float(
        string="Model Calls per Message", digits=(6, 1),
        help="Talk answers with one call; Agent and Developer call the model again after each tool.")
    prompt_tokens = fields.Integer(
        string="Tokens Sent per Call",
        help="Measured on the shipped prompts: Talk 10.6k, Agent 22.9k, Developer 36.0k.")
    output_tokens = fields.Integer(string="Tokens Answered per Call")
    monthly_calls = fields.Integer(compute="_compute_estimate")
    monthly_tokens = fields.Integer(compute="_compute_estimate")
    monthly_cost = fields.Float(
        string="Estimated Cost per Month (USD)", compute="_compute_estimate", digits=(12, 2))
    cost_person = fields.Float(string="per Person (USD)", compute="_compute_estimate", digits=(12, 2))
    cost_message = fields.Float(string="per Message (USD)", compute="_compute_estimate", digits=(12, 4))

    budget_user_tokens = fields.Integer(string="Tokens per Person and per Day")
    budget_user_cost = fields.Float(string="Cost per Person and per Day", digits=(12, 2))
    budget_company_tokens = fields.Integer(string="Tokens per Company and per Month")
    budget_company_cost = fields.Float(string="Cost per Company and per Month", digits=(12, 2))

    # ------------------------------------------------------------------
    # Defaults and computes
    # ------------------------------------------------------------------
    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        table = price_table()
        mode = values.get("mode") or "talk"
        values.setdefault("working_days", table["working_days"])
        values.setdefault("output_tokens", table["output_tokens_per_call"])
        values.setdefault("prompt_tokens", table["prompt_tokens"][mode])
        values.setdefault("calls_per_message", table["calls_per_message"][mode])
        return values

    def _compute_api_key(self):
        self.api_key = False

    def _inverse_api_key(self):
        for wizard in self:
            if not wizard.api_key or wizard.choice == "rules":
                continue
            self._check_admin()
            provider = wizard._provider_for_choice()
            provider.api_key = wizard.api_key
            wizard.provider_id = provider
        # Forget what was typed: the next read computes an empty field
        self.invalidate_recordset(["api_key"])

    @api.depends("choice")
    def _compute_price_table(self):
        table = price_table()
        for wizard in self:
            wizard.price_date = table["date"]
            wizard.price_note = table["note"]

    @api.depends("choice")
    def _compute_texts(self):
        texts = self._choice_texts()
        for wizard in self:
            pros, cons, privacy = texts.get(wizard.choice, ("", "", ""))
            wizard.pros, wizard.cons, wizard.privacy = pros, cons, privacy

    def _choice_texts(self):
        _ = self.env._
        return {
            "rules": (
                _("Free. Nothing leaves your server. Open screens, find records, filter, count and "
                  "change fields by rules, in Arabic and English."),
                _("Understands set sentences only: no free questions, no explanations, no building."),
                _("No data is sent anywhere."),
            ),
            "ollama": (
                _("Free to run. Your own machine answers, with no bill and no key."),
                _("Needs a machine Odoo can reach, ideally with a GPU. Small models answer slower "
                  "and less well."),
                _("Nothing leaves your network."),
            ),
            "claude": (
                _("The best answers and tool use of the list. You pay for what is used."),
                _("A paid account and a key are needed; the bill follows the use (see the estimate)."),
                _("Questions and the records they need are sent to Anthropic, under your agreement "
                  "with them."),
            ),
            "gemini": (
                _("Cheap, and a free tier to try."),
                _("The free tier is limited in rate and quality. A key from Google AI Studio is needed."),
                _("Questions and records are sent to Google. On the free tier Google may use them to "
                  "improve its products: use demo data only."),
            ),
            "other": (
                _("Any service that speaks the OpenAI API: OpenAI, Groq, OpenRouter, your own gateway."),
                _("You choose the model and check its prices and tool support."),
                _("Questions and records are sent to the service you name."),
            ),
        }

    @api.depends("user_count", "messages_per_day", "working_days", "calls_per_message",
                 "prompt_tokens", "output_tokens", "price_input", "price_output", "choice")
    def _compute_estimate(self):
        for wizard in self:
            if wizard.choice == "rules":
                figures = estimate(0, 0, 0, 0, 0, 0, 0.0, 0.0)
            else:
                figures = wizard._figures()
            wizard.monthly_calls = figures["calls"]
            wizard.monthly_tokens = figures["tokens_in"] + figures["tokens_out"]
            wizard.monthly_cost = figures["cost"]
            wizard.cost_person = figures["cost_person"]
            wizard.cost_message = figures["cost_message"]

    def _figures(self):
        self.ensure_one()
        return estimate(
            self.user_count, self.messages_per_day, self.working_days, self.calls_per_message,
            self.prompt_tokens, self.output_tokens, self.price_input, self.price_output)

    @api.onchange("mode")
    def _onchange_mode(self):
        table = price_table()
        self.prompt_tokens = table["prompt_tokens"][self.mode]
        self.calls_per_message = table["calls_per_message"][self.mode]

    @api.onchange("choice")
    def _onchange_choice(self):
        prices = price_table()["providers"].get(self.choice, {})
        self.price_input = prices.get("input", 0.0)
        self.price_output = prices.get("output", 0.0)
        self.tested = False
        self.test_result = False
        if self.choice == "ollama" and not self.base_url:
            self.base_url = "http://localhost:11434/v1"

    # ------------------------------------------------------------------
    # Steps
    # ------------------------------------------------------------------
    def _check_admin(self):
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(self.env._("Only system administrators can run the setup wizard."))

    def _reopen(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Nudge Setup"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def _steps(self):
        """:return: the steps this choice goes through, in order"""
        if self.choice == "rules":
            return ["users", "provider", "enable"]
        return [key for key, _label in STEPS]

    def _validate_step(self):
        if self.step == "users":
            if self.user_count < 1 or self.messages_per_day < 1:
                raise UserError(self.env._(
                    "Say how many people use Nudge and how many messages each sends a day."))
        elif self.step == "key" and not self.tested:
            raise UserError(self.env._("Test the connection first: it must succeed before you go on."))

    def action_next(self):
        self._check_admin()
        self.ensure_one()
        self._validate_step()
        steps = self._steps()
        if self.step in steps and steps.index(self.step) + 1 < len(steps):
            self.step = steps[steps.index(self.step) + 1]
            if self.step == "budgets":
                self._suggest_budgets()
        return self._reopen()

    def action_back(self):
        self._check_admin()
        self.ensure_one()
        steps = self._steps()
        if self.step in steps and steps.index(self.step) > 0:
            self.step = steps[steps.index(self.step) - 1]
        return self._reopen()

    def _suggest_budgets(self):
        """Fill the empty budgets with a margin over the estimate: 3 times the average day of
        a person, 1.5 times the month of the company."""
        self.ensure_one()
        figures = self._figures()
        days = max(self.working_days, 1)
        people = max(self.user_count, 1)
        tokens = figures["tokens_in"] + figures["tokens_out"]
        values = {}
        if not self.budget_user_tokens:
            values["budget_user_tokens"] = int(round(tokens / people / days * 3, -3))
        if not self.budget_user_cost:
            values["budget_user_cost"] = round(figures["cost"] / people / days * 3, 2)
        if not self.budget_company_tokens:
            values["budget_company_tokens"] = int(round(tokens * 1.5, -3))
        if not self.budget_company_cost:
            values["budget_company_cost"] = round(figures["cost"] * 1.5, 2)
        self.write(values)

    def action_suggest_budgets(self):
        self._check_admin()
        self.write({"budget_user_tokens": 0, "budget_user_cost": 0.0,
                    "budget_company_tokens": 0, "budget_company_cost": 0.0})
        self._suggest_budgets()
        return self._reopen()

    # ------------------------------------------------------------------
    # Provider and key
    # ------------------------------------------------------------------
    def _provider_for_choice(self):
        """:return: the provider record the choice stands for, made if there is none"""
        self.ensure_one()
        if self.provider_id:
            return self.provider_id
        xmlid = SEEDED.get(self.choice)
        provider = self.env.ref(xmlid, raise_if_not_found=False) if xmlid else None
        if not provider:
            provider = self.env["nudge.provider"].create({
                "name": self.env._("OpenAI-compatible API") if self.choice == "other"
                        else dict(CHOICES)[self.choice],
                "kind": "anthropic" if self.choice == "claude" else "openai_compatible",
                "requires_api_key": self.choice != "ollama",
            })
        return provider

    def action_test(self):
        """Test the connection of the provider (the key was saved on it when the screen was)."""
        self._check_admin()
        self.ensure_one()
        if self.choice == "rules":
            return self._reopen()
        provider = self._provider_for_choice()
        values = {"active": True}
        if self.base_url:
            values["base_url"] = self.base_url
        if self.model_name:
            values["model_name"] = self.model_name
        provider.write(values)
        self.provider_id = provider
        result, tested = "", False
        try:
            if not provider.model_name and provider.base_url:
                provider.action_fetch_models()
                if not provider.model_name and provider.model_ids:
                    provider.model_name = provider.model_ids[:1].name
            provider.action_test_connection()
            result, tested = self.env._("Connection successful."), True
        except UserError as exc:
            result = str(exc.args[0]) if exc.args else self.env._("The test failed.")
        self.write({"tested": tested, "test_result": result})
        return self._reopen()

    # ------------------------------------------------------------------
    # Enable
    # ------------------------------------------------------------------
    def action_enable(self):
        """Write the choices where Nudge reads them: the default provider, the modes, the
        budgets, and the group of all internal users."""
        self._check_admin()
        self.ensure_one()
        # The settings of Nudge itself are system parameters, written as the Settings screen does
        Parameter = self.env["ir.config_parameter"].sudo()
        Parameter.set_param("nudge.noai_first", "True")
        if self.choice != "rules":
            if not self.tested or not self.provider_id:
                raise UserError(self.env._("Test the connection of the provider first."))
            Parameter.set_param(DEFAULT_PROVIDER_PARAM, str(self.provider_id.id))
            if self.mode in ("agent", "developer"):
                Parameter.set_param(AGENT_PARAM, "yes")
            if self.mode == "developer":
                Parameter.set_param(DEVELOPER_PARAM, "yes")
            budgets = {
                "user_tokens": self.budget_user_tokens, "user_cost": self.budget_user_cost,
                "company_tokens": self.budget_company_tokens, "company_cost": self.budget_company_cost,
            }
            for name, value in budgets.items():
                Parameter.set_param(BUDGET_PARAMS[name], str(value))
        if self.all_users:
            group = self.env.ref("nudge.group_nudge_user")
            self.env.ref("base.group_user").write({"implied_ids": [(4, group.id)]})
        _logger.info("Nudge setup wizard finished (%s)", self.choice)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": self.env._("Nudge is set up. Reload the page to see it."),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from ..tools import base, validator
from .nudge_conversation import (
    ADMIN_PARAM, AGENT_PARAM, DEVELOPER_PARAM, MAX_STREAMS_PARAM, PYTHON_PARAM,
    SCREEN_SHARING_PARAM, SQL_PARAM,
)
from .nudge_provider import AGENT_PROVIDER_PARAM, DEFAULT_PROVIDER_PARAM, DEVELOPER_PROVIDER_PARAM
from .nudge_voice import ENGINE_AUTO, ENGINE_BROWSER, ENGINE_PROVIDER, VOICE_ENGINE_PARAM, VOICE_PARAM

NUDGE_COMPLIANCE_FEED_URL = "https://znad-odoo-dev.github.io/nudge/compliance/feed.json"


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    nudge_provider_id = fields.Many2one(
        "nudge.provider", string="Default AI Provider",
        config_parameter=DEFAULT_PROVIDER_PARAM,
        help="Provider answering new conversations. "
             "Empty uses the first configured provider.",
    )
    nudge_agent_provider_id = fields.Many2one(
        "nudge.provider", string="Agent Provider", config_parameter=AGENT_PROVIDER_PARAM,
        help="Provider doing the work in Agent mode, and the scheduled tasks. Empty: the default one.")
    nudge_developer_provider_id = fields.Many2one(
        "nudge.provider", string="Developer Provider", config_parameter=DEVELOPER_PROVIDER_PARAM,
        help="Provider building in Developer mode. Empty: the one of Agent mode, or the default one.")
    group_nudge_all_users = fields.Boolean(
        string="Nudge for All Internal Users",
        implied_group="nudge.group_nudge_user",
    )
    group_nudge_advanced_menus = fields.Boolean(
        string="Show advanced menus",
        group="nudge.group_nudge_manager",
        implied_group="nudge.group_nudge_advanced_menus",
        help="Show the advanced menus of Nudge (prompts, training, usage, raw lists) to its managers.",
    )
    nudge_screen_sharing = fields.Boolean(
        string="Screen Sharing",
        help="Let people share the screen they are on with Nudge: the record, its fields "
             "and their values are then sent to the AI provider with the question.",
    )
    nudge_voice = fields.Boolean(
        string="Voice",
        help="Let people talk to Nudge and dictate in the fields of Odoo. The microphone is "
             "open only while they hold the shortcut or after they pressed the button.",
    )
    nudge_voice_engine = fields.Selection(
        [
            (ENGINE_AUTO, "The browser when it can, the AI provider otherwise"),
            (ENGINE_BROWSER, "The browser only"),
            (ENGINE_PROVIDER, "The AI provider only"),
        ],
        string="Speech Turned into Text By", required=True, default=ENGINE_AUTO,
        help="Chrome, Edge and Safari turn speech into text themselves, by sending the sound "
             "to the servers of their maker. Firefox cannot. The AI provider receives the "
             "sound where it receives the questions.",
    )

    nudge_noai_first = fields.Boolean(
        string="Try to understand common requests without AI first", config_parameter="nudge.noai_first",
        help="Open a screen, find a record, filter a list, count, create, change a field or press a button "
             "are understood by rules, in Arabic and English, without calling the AI provider: no token is "
             "spent, and every change still waits for a yes. What is not understood goes to the AI as before. "
             "With no provider, or in private mode, they are always understood this way.")

    nudge_summary_after_turns = fields.Integer(
        string="Summarise Long Conversations After", config_parameter="nudge.summary_after_turns", default=12,
        help="After this many questions in one conversation, the older ones are replaced, in what Nudge "
             "reads, by a short summary: it costs fewer tokens and a long chat no longer reaches the limit "
             "of the model. The messages stay in the panel, and the person can read and correct the "
             "summary. 0 keeps the whole history (the screens of old questions are still left out).")

    nudge_answer_memory = fields.Boolean(
        string="Answer Memory", config_parameter="nudge.answer_memory",
        help="Answers people found useful, once a manager approved them, answer the same question "
             "of the others at once, without asking the AI provider.")
    nudge_memory_threshold = fields.Float(
        string="Confidence Required", config_parameter="nudge.memory_threshold", default=0.75,
        help="How alike the questions must be, from 0.5 to 1, for a remembered answer to be given. "
             "Higher is stricter: 1 is the same words.")

    nudge_recurring_min_count = fields.Integer(
        string="Times Asked", config_parameter="nudge.recurring_min_count", default=5,
        help="A question asked at least this many times in a month, with no remembered answer and no "
             "tour, is proposed to the managers.")
    nudge_recurring_retention_days = fields.Integer(
        string="Keep For (Days)", config_parameter="nudge.recurring_retention_days", default=90,
        help="A question not asked for this long is archived. Nothing is deleted.")

    nudge_docs_summary = fields.Char(
        string="Odoo Documentation", compute="_compute_nudge_docs_summary")

    def _compute_nudge_docs_summary(self):
        self.nudge_docs_summary = self.env["nudge.doc.page"]._summary()

    def action_nudge_setup(self):
        """:return: the client action that opens Nudge and starts the first-setup assistant"""
        return {"type": "ir.actions.client", "tag": "nudge_setup_start"}

    def _nudge_open_in_app(self, action_xmlid):
        """:return: the action opened inside the Nudge app. Its own menu is an advanced one, hidden by
        default, so the web client would keep the app it was in (Discuss) in the title"""
        return {
            "type": "ir.actions.client", "tag": "reload",
            "params": {
                "menu_id": self.env.ref("nudge.nudge_menu_root").id,
                "action_id": self.env.ref(action_xmlid).id}}

    def action_nudge_open_latency(self):
        return self._nudge_open_in_app("nudge.nudge_latency_action")

    def action_nudge_open_usage(self):
        return self._nudge_open_in_app("nudge.nudge_usage_action")

    def action_nudge_reload_docs(self):
        return self.env["nudge.doc.page"].action_reload()

    # Search by meaning, on this server, with no AI provider. Company-independent
    nudge_embedding_summary = fields.Char(
        string="Local Search Model", compute="_compute_nudge_embedding_summary")
    nudge_embedding_model = fields.Char(
        string="Model Name", config_parameter="nudge.embedding_model",
        help="The model to use. Empty: multilingual-e5-small, the one Nudge downloads.")
    nudge_embedding_path = fields.Char(
        string="Model Folder", config_parameter="nudge.embedding_path",
        help="A folder holding model.onnx and tokenizer.json, when you put the model there "
             "yourself. Empty: the filestore of the database.")

    nudge_embedding_idle = fields.Integer(
        string="Unload After (minutes)", config_parameter="nudge.embedding_idle_minutes", default=15,
        help="Minutes without a question after which a worker drops the model from its memory.")

    @api.constrains("nudge_embedding_model")
    def _check_nudge_embedding_model(self):
        from .nudge_embedding import SAFE_NAME  # noqa: PLC0415
        for settings in self:
            name = (settings.nudge_embedding_model or "").strip()
            if name and (not SAFE_NAME.fullmatch(name) or ".." in name):
                raise ValidationError(_(
                    "The model name is the name of a folder: letters, digits, dots, dashes and underscores only."))

    def _compute_nudge_embedding_summary(self):
        self.nudge_embedding_summary = self.env["nudge.embedding"]._status()

    def action_nudge_embedding_download(self):
        return self.env["nudge.embedding"].action_download()

    def action_nudge_embedding_remove(self):
        return self.env["nudge.embedding"].action_remove()

    def action_nudge_embedding_reindex(self):
        return self.env["nudge.embedding"].action_reindex()

    # NP-02
    nudge_ask_data = fields.Boolean(
        string="Ask the Data",
        help="Let every Nudge User ask questions of the data in Talk: count, sum, group, look up and chart "
             "what their own access rights allow. Nudge gets the reading tools only; nothing is created, "
             "changed or deleted. Off: the data stays closed to those without the Agent User group.",
    )
    nudge_agent = fields.Boolean(
        string="Agent Mode",
        help="Let the people of the group Agent User ask Nudge to do the work: look up "
             "records, create and change them, press buttons. Nudge acts with the rights of "
             "the person, and asks them before each change.",
    )
    nudge_python = fields.Boolean(
        string="Python for the Agent",
        help="Let the agent compute with Python when counting and searching are not enough. "
             "The code runs in the sandbox of Odoo, as the person, and reads only: what it "
             "writes is discarded. Off by default.",
    )
    nudge_sql = fields.Boolean(
        string="SQL for Administrators",
        help="Let administrators (the group Settings, allowed in every company) read the "
             "database with one SELECT through the agent: no change, no lock, the tables of "
             "Odoo they may read, 50 rows, 10 seconds. Off by default.",
    )
    nudge_admin = fields.Boolean(
        string="Nudge for Administrators",
        help="Let administrators (the group Settings) read and change the setup of Odoo "
             "through the agent: users, groups and privileges, companies, menus, views, "
             "actions, scheduled actions, system parameters. As the person, one confirmation "
             "per change, never a deletion; passwords, keys and tokens stay hidden. Off by "
             "default.",
    )
    nudge_developer = fields.Boolean(
        string="Nudge for Developers",
        help="Let administrators (the group Settings) build through Nudge, in Developer mode: "
             "automation rules, server actions, emails sent on an event, buttons, fields and "
             "changes to the screens. Nudge explains first and shows everything on the card, "
             "the code included; nothing is made before a yes; deletion only when asked for by "
             "name. Every step is in Nudge, Activity. Off by default.",
    )
    # 0 is no limit
    nudge_plan_max_records = fields.Integer(
        string="Records per Plan", config_parameter="nudge.plan_max_records", default=200,
        help="How many records the agent may change with one yes. At most 1000.")
    nudge_private_mode = fields.Boolean(
        related="company_id.nudge_private_mode", readonly=False,
        string="Private Mode: Never Send Data to an External AI Provider")
    nudge_private_browser_speech = fields.Boolean(
        related="company_id.nudge_private_browser_speech", readonly=False,
        string="Allow Browser Speech in Private Mode")
    nudge_allow_recipe_index = fields.Boolean(
        related="company_id.nudge_allow_recipe_index", readonly=False,
        string="Allow Fetching Recipe Indexes")
    nudge_egress_hosts = fields.Text(  # NP-15
        related="company_id.nudge_egress_hosts", readonly=False, string="Allowed Destinations")
    nudge_egress_mode = fields.Selection(
        [("any", "Any address"), ("list", "Only the list")], string="Outbound Destinations",
        config_parameter="nudge.egress_mode", default="any")
    # NP-13
    nudge_redact_pii = fields.Boolean(
        related="company_id.nudge_redact_pii", readonly=False, string="Redact Personal Data Before Sending")
    nudge_redact_sample = fields.Text(
        string="Sample Text", default="Raghad: raghad@example.com, +971 50 123 4567, AE07 0331 2345 6789 0123 456",
        help="Not saved: shows what would be replaced by a token.")
    nudge_redact_preview = fields.Text(string="What Would Be Sent", compute="_compute_redact_preview")
    nudge_validate_partner_duplicate = fields.Boolean(
        string="Warn About a Duplicate Contact", compute="_compute_validator_switches",
        inverse="_inverse_validator_switches",
        help="Warns when a contact with the same name, email or phone already exists. On by default.")
    nudge_validate_product_duplicate = fields.Boolean(
        string="Warn About a Duplicate Product", compute="_compute_validator_switches",
        inverse="_inverse_validator_switches",
        help="Warns when a product with the same name or internal reference already exists. On by default.")
    nudge_validate_vat_format = fields.Boolean(
        string="Check the Format of a Tax Number", compute="_compute_validator_switches",
        inverse="_inverse_validator_switches",
        help="Warns when a tax number does not have the shape its country uses. On by default.")
    nudge_validate_phone_format = fields.Boolean(
        string="Check the Format of a Phone Number", compute="_compute_validator_switches",
        inverse="_inverse_validator_switches",
        help="Warns when a phone number cannot be read as a number of its country. Needs the Contacts phone validation and the Python package phonenumbers; without them the check says so and does nothing. Off by default.")
    nudge_validate_email_syntax = fields.Boolean(
        string="Check an Email Address", compute="_compute_validator_switches",
        inverse="_inverse_validator_switches",
        help="Warns when an email address is not written like one. Off by default.")
    nudge_validate_price_below_cost = fields.Boolean(
        string="Warn About a Sale Price Under Cost", compute="_compute_validator_switches",
        inverse="_inverse_validator_switches",
        help="Warns when the price of a sale line is under the cost of the product. Off by default.")
    nudge_validate_price_above_last_purchase = fields.Boolean(
        string="Warn About a Purchase Price Far Above the Last Ones", compute="_compute_validator_switches",
        inverse="_inverse_validator_switches",
        help="Warns on a purchase order when the price of a line is far above the prices of the last purchases of the same product. Off by default.")
    nudge_validate_quantity_far_above = fields.Boolean(
        string="Warn About a Quantity Far Above the Usual", compute="_compute_validator_switches",
        inverse="_inverse_validator_switches",
        help="Warns when the quantity of a line on a sale, a purchase or a transfer is far above what was usually ordered of that product. Off by default.")
    nudge_validate_date_in_the_past = fields.Boolean(
        string="Warn About a Deadline in the Past", compute="_compute_validator_switches",
        inverse="_inverse_validator_switches",
        help="Warns when a deadline, a delivery date or a validity date is set in the past. Off by default.")
    nudge_validate_bill_number_reuse = fields.Boolean(
        string="Warn About a Bill Number Already Used", compute="_compute_validator_switches",
        inverse="_inverse_validator_switches",
        help="Warns when a vendor bill number was already used by the same vendor. On by default.")
    nudge_validate_policy_required = fields.Boolean(
        string="Remind of the Fields the Company Requires", compute="_compute_validator_switches",
        inverse="_inverse_validator_switches",
        help="Reminds of the fields the company asked to be filled on a model (the button below). On by default.")
    nudge_coach_enabled = fields.Boolean(related="company_id.nudge_coach_enabled", readonly=False)
    nudge_health_user_id = fields.Many2one(related="company_id.nudge_health_user_id", readonly=False)
    nudge_outlier_sensitivity = fields.Selection(
        related="company_id.nudge_outlier_sensitivity", readonly=False, string="Unusual Figures Sensitivity")
    nudge_mobile_tile_clock = fields.Boolean(related="company_id.nudge_mobile_tile_clock", readonly=False)
    nudge_mobile_tile_photo = fields.Boolean(related="company_id.nudge_mobile_tile_photo", readonly=False)
    nudge_mobile_tile_tasks = fields.Boolean(related="company_id.nudge_mobile_tile_tasks", readonly=False)
    nudge_mobile_tile_record = fields.Boolean(related="company_id.nudge_mobile_tile_record", readonly=False)
    nudge_mobile_tile_note = fields.Boolean(related="company_id.nudge_mobile_tile_note", readonly=False)
    nudge_tasks_per_user = fields.Integer(
        string="Scheduled Tasks per Person", config_parameter="nudge.tasks_per_user", default=5,
        help="How many tasks each person may ask Nudge to do by itself. 0: none.")
    nudge_budget_user_tokens = fields.Integer(
        string="Tokens per Person and per Day", config_parameter="nudge.budget_user_tokens",
        help="The most tokens one person may use in a day. 0 is no limit.")
    nudge_budget_user_cost = fields.Float(
        string="Cost per Person and per Day", config_parameter="nudge.budget_user_cost",
        digits=(12, 2),
        help="The most one person may cost in a day, at the prices set on the provider. 0 is no limit.")
    nudge_budget_company_tokens = fields.Integer(
        string="Tokens per Company and per Month", config_parameter="nudge.budget_company_tokens",
        help="The most tokens the company may use in a month. 0 is no limit.")
    nudge_budget_company_cost = fields.Float(
        string="Cost per Company and per Month", config_parameter="nudge.budget_company_cost",
        digits=(12, 2),
        help="The most the company may cost in a month, at the prices set on the provider. 0 is no limit.")
    # 0 is automatic: the http workers of the server less two, at least one
    nudge_max_streams = fields.Integer(
        string="Answers at the Same Time", config_parameter=MAX_STREAMS_PARAM,
        help="How many people Nudge answers at once, for everybody together. 0 is automatic: "
             "the workers of the server less two.")

    nudge_retention_days = fields.Integer(
        string="Delete Conversations Older Than (Days)", config_parameter="nudge.retention_days",
        help="Conversations whose last activity is older than this number of days are deleted every day, "
             "with their messages, screens and images. One in which the Agent acted is archived instead: "
             "the log of what it did is kept. One that is answering, or waits for a yes, is left. "
             "0 keeps them for ever.")

    def get_values(self):
        values = super().get_values()
        Voice = self.env["nudge.voice"]
        Conversation = self.env["nudge.conversation"]
        values.update(
            nudge_screen_sharing=Conversation._can_share_screen(),
            nudge_agent=Conversation._is_agent_allowed(),
            nudge_python=Conversation._is_python_allowed(),
            nudge_sql=Conversation._is_sql_allowed(),
            nudge_admin=Conversation._is_admin_allowed(),
            nudge_developer=Conversation._is_developer_allowed(),
            nudge_voice=Voice._is_allowed(),
            nudge_voice_engine=Voice._get_engine(),
            nudge_ask_data=base.ask_data_enabled(self.env),  # NP-02
        )
        return values

    def set_values(self):
        super().set_values()
        Parameter = self.env["ir.config_parameter"].sudo()
        Parameter.set_param(SCREEN_SHARING_PARAM, "yes" if self.nudge_screen_sharing else "no")
        Parameter.set_param(AGENT_PARAM, "yes" if self.nudge_agent else "no")
        Parameter.set_param(PYTHON_PARAM, "yes" if self.nudge_python else "no")
        Parameter.set_param(SQL_PARAM, "yes" if self.nudge_sql else "no")
        Parameter.set_param(ADMIN_PARAM, "yes" if self.nudge_admin else "no")
        Parameter.set_param(DEVELOPER_PARAM, "yes" if self.nudge_developer else "no")
        Parameter.set_param(VOICE_PARAM, "yes" if self.nudge_voice else "no")
        Parameter.set_param(VOICE_ENGINE_PARAM, self.nudge_voice_engine or ENGINE_AUTO)
        Parameter.set_param(base.ASK_DATA_PARAM, "yes" if self.nudge_ask_data else "no")  # NP-02

    @api.depends("company_id")
    def _compute_validator_switches(self):
        for settings in self:
            for key in validator.DEFAULTS:
                chosen = validator.company_switches(settings.company_id).get(key)
                settings[f"nudge_validate_{key}"] = validator.DEFAULTS[key] if chosen is None else bool(chosen)

    def _inverse_validator_switches(self):
        for settings in self:
            switches = dict(validator.company_switches(settings.company_id))
            for key in validator.DEFAULTS:
                switches[key] = bool(settings[f"nudge_validate_{key}"])
            settings.company_id.nudge_validator_switches = switches

    # NP-08: the signed feed of the compliance packs; off while the address or the key is empty
    nudge_compliance_feed_url = fields.Char(
        string="Compliance Feed Address", config_parameter="nudge.compliance_feed_url",
        help="HTTPS address of a signed feed of country packs. Empty: the feed is off and Nudge calls nobody.")
    nudge_compliance_feed_key = fields.Char(
        string="Compliance Feed Public Key", config_parameter="nudge.compliance_feed_key",
        help="The Ed25519 public key of the publisher (PEM, or the 32 bytes in base64). A feed signed by "
             "another key is refused.")

    nudge_compliance_feed_status = fields.Char(
        string="Last Check", compute="_compute_nudge_compliance_feed_status",
        help="What the last read of the feed found: whether its signature was verified and how many countries it covers.")

    def _compute_nudge_compliance_feed_status(self):
        text = self.env["nudge.compliance.update"]._status_text()
        for settings in self:
            settings.nudge_compliance_feed_status = text

    def action_nudge_compliance_feed_check(self):
        """Read the feed now (a system administrator, with the address and key as saved)
        :return: the list of the pack updates"""
        return self.env["nudge.compliance.update"].action_check_now()

    def action_nudge_compliance_feed_use_nudge(self):
        """Opt in to the feed Nudge publishes: fill its address and the key of the Nudge publisher (the one that
        signs the skill catalogue). Nothing is fetched here; the daily cron or "Check the Feed Now" does that.
        Only a system administrator may press it."""
        if not self.env.is_superuser() and not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only a system administrator sets the compliance feed."))
        Parameter = self.env["ir.config_parameter"].sudo()
        Parameter.set_param("nudge.compliance_feed_url", NUDGE_COMPLIANCE_FEED_URL)
        publisher = self.env.ref("nudge.recipe_publisher_nudge", raise_if_not_found=False)
        key = publisher.sudo().public_key if publisher else ""
        if key and key.strip():
            Parameter.set_param("nudge.compliance_feed_key", key.strip())
        return False

    def action_nudge_compliance_feed_list(self):
        return self.env["ir.actions.actions"]._for_xml_id("nudge.nudge_compliance_update_action")

    @api.depends("nudge_redact_sample")
    def _compute_redact_preview(self):  # NP-13
        from ..llm import redact  # noqa: PLC0415
        for settings in self:
            masked, found = redact.preview(settings.nudge_redact_sample)
            lines = [f"{token} = {value}" for token, value in found.items()]
            settings.nudge_redact_preview = ("\n\n").join([masked, ("\n").join(lines)]) if lines else masked
    # NP-20
    nudge_usage_retention_months = fields.Integer(
        string="Keep Usage For (Months)", config_parameter="nudge.usage_retention_months",
        help="Calls to the AI provider older than this many whole months are removed from the usage list every "
             "day, a batch at a time. The current month is always kept, so budgets read the same totals. "
             "0 keeps everything.")
    # NP-10: the model standing in when the provider of a conversation cannot answer
    nudge_fallback_provider_id = fields.Many2one(
        "nudge.provider", string="Fallback Model", config_parameter="nudge.fallback_provider_id",
        help="Answers once, in place of the provider, when it fails or has no key; when the budget "
             "of a person is used up, if it runs on your own server; and always in private mode, "
             "where only a provider of your own server may answer. Each answer it gives says so. "
             "Empty: the person is told why there is no answer, and the requests understood "
             "without AI still work.")
    # NP-04: past the answers at the same time, the questions wait in line rather than being refused
    nudge_max_line = fields.Integer(
        string="Questions Waiting at Most", config_parameter="nudge.max_line", default=20,
        help="When Nudge answers as many people as it may at once, the next questions wait in line "
             "and start as soon as an answer ends; the person sees their place and can cancel. "
             "Past this many waiting, a question is refused.")
    # S6: another provider answering the light work of every provider without an economy model of its own
    nudge_economy_provider_id = fields.Many2one(
        "nudge.provider", string="Economy Provider", config_parameter="nudge.economy_provider_id",
        help="Answers the light work (questions in Talk, which change nothing, summaries, explanations, "
             "short texts) of every provider that has no economy model of its own, set on the provider. "
             "The provider of the conversation answers what acts or builds, what is marked heavy, and once "
             "more what this one failed at. In private mode only if it runs on your own server. Empty: "
             "only the economy models set on the providers.")

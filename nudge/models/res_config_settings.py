from odoo import fields, models

from .nudge_conversation import (
    ADMIN_PARAM, AGENT_PARAM, DEVELOPER_PARAM, PYTHON_PARAM, SCREEN_SHARING_PARAM, SQL_PARAM,
)
from .nudge_provider import DEFAULT_PROVIDER_PARAM
from .nudge_voice import ENGINE_AUTO, ENGINE_BROWSER, ENGINE_PROVIDER, VOICE_ENGINE_PARAM, VOICE_PARAM


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    nudge_provider_id = fields.Many2one(
        "nudge.provider", string="Default AI Provider",
        config_parameter=DEFAULT_PROVIDER_PARAM,
        help="Provider answering new conversations. "
             "Empty uses the first configured provider.",
    )
    group_nudge_all_users = fields.Boolean(
        string="Nudge for All Internal Users",
        implied_group="nudge.group_nudge_user",
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

    nudge_docs_summary = fields.Char(
        string="Odoo Documentation", compute="_compute_nudge_docs_summary")

    def _compute_nudge_docs_summary(self):
        self.nudge_docs_summary = self.env["nudge.doc.page"]._summary()

    def action_nudge_reload_docs(self):
        return self.env["nudge.doc.page"].action_reload()

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
             "name. Every step is in What Nudge Did. Off by default.",
    )
    # 0 is no limit
    nudge_plan_max_records = fields.Integer(
        string="Records per Plan", config_parameter="nudge.plan_max_records", default=200,
        help="How many records the agent may change with one yes. At most 1000.")
    nudge_budget_user_tokens = fields.Integer(
        string="Tokens per Person and per Day", config_parameter="nudge.budget_user_tokens")
    nudge_budget_user_cost = fields.Float(
        string="Cost per Person and per Day", config_parameter="nudge.budget_user_cost",
        digits=(12, 2))
    nudge_budget_company_tokens = fields.Integer(
        string="Tokens per Company and per Month", config_parameter="nudge.budget_company_tokens")
    nudge_budget_company_cost = fields.Float(
        string="Cost per Company and per Month", config_parameter="nudge.budget_company_cost",
        digits=(12, 2))

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

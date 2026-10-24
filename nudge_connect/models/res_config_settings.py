from odoo import fields, models

from .nudge_connector import STOPPED_PARAM


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    nudge_connect_stopped = fields.Boolean(
        string="Stop Every Connector", config_parameter=STOPPED_PARAM,
        help="The kill switch: nothing is sent to any connector, in any company, until it is switched back.")
    nudge_connect_disabled = fields.Boolean(related="company_id.nudge_connect_disabled", readonly=False)
    nudge_connect_read_only_first_month = fields.Boolean(
        related="company_id.nudge_connect_read_only_first_month", readonly=False)

    def action_nudge_connectors(self):
        return self.env["ir.actions.act_window"]._for_xml_id("nudge_connect.nudge_connector_action")

    nudge_connect_budget_user_calls = fields.Integer(
        string="Connector Calls per Person and per Day", config_parameter="nudge_connect.budget_user_calls",
        help="The most calls one person may make to connectors in a day: a call costs no tokens but counts "
             "as one call. A kept answer is not a call. 0 is no limit.")
    nudge_connect_budget_company_calls = fields.Integer(
        string="Connector Calls per Company and per Month", config_parameter="nudge_connect.budget_company_calls",
        help="The most calls the company may make to connectors in a month. 0 is no limit.")

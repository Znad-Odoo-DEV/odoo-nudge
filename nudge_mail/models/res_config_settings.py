from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    nudge_mail_agent_on = fields.Boolean(related="company_id.nudge_mail_agent_on", readonly=False)
    nudge_mail_agent_alias = fields.Char(related="company_id.nudge_mail_agent_alias", readonly=False)
    nudge_mail_agent_project = fields.Selection(related="company_id.nudge_mail_agent_project", readonly=False)

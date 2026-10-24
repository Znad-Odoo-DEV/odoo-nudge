from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    nudge_connect_disabled = fields.Boolean(
        string="Connectors Off", copy=False,
        help="No connector is used in this company, whatever each connector says.")
    nudge_connect_read_only_first_month = fields.Boolean(
        string="Connectors Read-Only in Their First Month", default=True,
        help="In this company a connector only reads during the month after it was first made active: its "
             "write tools are not offered before then.")

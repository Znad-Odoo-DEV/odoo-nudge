from odoo import fields, models


class NudgeUsage(models.Model):
    _inherit = "nudge.usage"

    origin = fields.Selection(
        [("website", "Website Assistant")], index=True,
        help="Where the call came from, when it was not a person working in Odoo.")

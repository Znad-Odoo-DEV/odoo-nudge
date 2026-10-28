from odoo import fields, models

from ..tools import checks


class NudgeBuilderCheckFinding(models.Model):
    _inherit = "nudge.builder.check.finding"

    check_group = fields.Selection(selection_add=[(checks.GROUP, "Workshop")], ondelete={checks.GROUP: "cascade"})

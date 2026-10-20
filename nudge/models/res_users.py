from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    nudge_home_layout = fields.Json(
        string="Nudge Home Layout", copy=False,
        help="The order of the tiles of the Nudge home page, and the ones hidden. Written by the person only.")

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + ["nudge_home_layout"]

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + ["nudge_home_layout"]

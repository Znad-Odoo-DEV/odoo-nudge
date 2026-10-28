from odoo import _, api, models
from odoo.tools.misc import file_path

from ..tools import listings, checks as listing_checks


class NudgeBuilderPlugin(models.AbstractModel):
    _inherit = "nudge.builder.plugin"

    @api.model
    def _plugin_kinds(self):
        return {**super()._plugin_kinds(), listings.KIND: {
            "model": "nudge.builder.project", "domain": [("listing_status", "in", list(listings.OFFERS))],
            "layouts": listings.LAYOUTS, "snippet": "s_three_columns", "item": listings.item,
            "render": listings.render, "labels": listings.words}}

    @api.model
    def _plugin_checks(self):
        return super()._plugin_checks() + [{
            "group": listing_checks.GROUP, "label": _("Listings"), "function": listing_checks.check_listings,
            "texts": listing_checks.texts}]

    @api.model
    def _plugin_templates(self):
        return super()._plugin_templates() + [
            file_path("nudge_builder_realestate/data/templates/real_estate_listings.json")]

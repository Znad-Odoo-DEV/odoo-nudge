from odoo import _, api, models
from odoo.tools.misc import file_path

from ..tools import checks as workshop_checks, services


class NudgeBuilderPlugin(models.AbstractModel):
    _inherit = "nudge.builder.plugin"

    @api.model
    def _plugin_kinds(self):
        return {**super()._plugin_kinds(), services.KIND: {
            "model": "nudge.builder.workshop.service", "layouts": services.LAYOUTS, "snippet": "s_features",
            "item": services.item}}

    @api.model
    def _plugin_checks(self):
        return super()._plugin_checks() + [{
            "group": workshop_checks.GROUP, "label": _("Workshop"),
            "function": workshop_checks.check_workshop, "texts": workshop_checks.texts}]

    @api.model
    def _plugin_templates(self):
        return super()._plugin_templates() + [
            file_path("nudge_builder_workshop/data/templates/car_workshop_booking.json")]

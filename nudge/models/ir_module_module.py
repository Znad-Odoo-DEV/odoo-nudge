import logging

from odoo import models

_logger = logging.getLogger(__name__)


class IrModuleModule(models.Model):
    _inherit = "ir.module.module"

    def _button_immediate_function(self, function):
        """Installing, upgrading or removing a module changes what the
        database is: its profile is read again."""
        result = super()._button_immediate_function(function)
        try:
            # The registry was rebuilt, and Nudge may be what was removed
            env = self.env
            if "nudge.profile" in env:
                cron = env.ref("nudge.cron_profile_refresh", raise_if_not_found=False)
                if cron:
                    cron.sudo()._trigger()
        except Exception:  # noqa: BLE001
            _logger.info("Nudge: refresh of the database profile not scheduled", exc_info=True)
        return result

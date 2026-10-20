import time

from odoo import api, models
from odoo.modules import module
from odoo.tools import config


class IrCron(models.Model):
    _inherit = "ir.cron"

    @api.model
    def _commit_progress(self, processed=0, *, remaining=None, deactivate=False):
        """Odoo 18 and 17 have no ir.cron._commit_progress: commit what was done and
        say how many seconds the scheduled action may still run"""
        if not getattr(module, "current_test", None):  # a test keeps its transaction, 19 skips the commit there too
            self.env.cr.commit()
        limit = config.get("limit_time_real_cron") or -1
        if limit <= 0:
            limit = config.get("limit_time_real") or 120
        return float(limit)

import logging
import os

from odoo import api, models, tools
from odoo.modules.module import get_module_path

_logger = logging.getLogger(__name__)


class IrModuleModule(models.Model):
    _inherit = "ir.module.module"

    @api.model
    @tools.ormcache()
    def _nudge_pack_folders(self):
        """:return: the folders of the compliance packs of the installed modules, looked for once for
        the registry (an install or a removal builds a new one)"""
        from ..tools.compliance import PACKS_OF_MODULES  # noqa: PLC0415
        folders = []
        for module in self.sudo().search([("state", "=", "installed")]).mapped("name"):
            if module == "nudge":
                continue
            path = get_module_path(module)
            folder = os.path.join(path, PACKS_OF_MODULES) if path else None
            if folder and os.path.isdir(folder):
                folders.append(folder)
        return folders

    @api.model
    @tools.ormcache()
    def _nudge_arabic_po_files(self):
        """:return: the ar.po files of the installed modules, in which Odoo says its own terms in Arabic,
        looked for once for the registry"""
        files = []
        for module in sorted(self.sudo().search([("state", "=", "installed")]).mapped("name")):
            path = get_module_path(module)
            po = os.path.join(path, "i18n", "ar.po") if path else None
            if po and os.path.isfile(po):
                files.append(po)
        return tuple(files)

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

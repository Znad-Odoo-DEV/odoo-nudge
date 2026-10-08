from odoo import models
from odoo.modules.module import get_manifest

from .. import tools


def nudge_version():
    """:return: the version of the code the server runs"""
    return get_manifest("nudge").get("version", "")


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    def session_info(self):
        result = super().session_info()
        nudge = self._get_nudge_session_info()
        if nudge is not None:
            result["nudge"] = nudge
        return result

    def _get_nudge_session_info(self):
        """:return: what the web client needs to show Nudge to the current
        user, or None when Nudge is not for them"""
        user = self.env.user
        if not (user._is_internal() and user.has_group("nudge.group_nudge_user")):
            return None
        return {
            # The page compares it with what the server answers with: an
            # update while the page was open is told, not suffered
            "version": nudge_version(),
            "is_manager": user.has_group("nudge.group_nudge_manager"),
            "is_configured": bool(self.env["nudge.provider"].sudo()._get_default()),
            "can_share_screen": self.env["nudge.conversation"]._can_share_screen(),
            "can_use_agent": self.env["nudge.conversation"]._can_use_agent(),
            # Developer mode: an administrator the switch is on for
            "can_develop": self.env["nudge.conversation"]._can_develop(),
            # A report can be a spreadsheet when the module for it is installed
            "can_make_spreadsheet": self.env["nudge.conversation"]._can_make_spreadsheet(),
            # The commands of the message box this person may type
            "commands": tools.commands.listed(self.env),
            **self.env["nudge.voice"]._get_session_info(),
        }

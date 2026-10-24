from odoo import models
from odoo.http import SessionExpiredException, request
from odoo.modules.module import get_manifest

from .. import tools


def nudge_version():
    """:return: the version of the code the server runs"""
    return get_manifest("nudge").get("version", "")


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _authenticate(cls, endpoint):
        """NP-16: the session of an auditor is of no use after the last day of their access, whatever
        the cron has done yet: checked on each request, on the server."""
        super()._authenticate(endpoint)
        uid = request.session.uid
        if uid and request.env["res.users"]._nudge_auditor_expired(uid):
            request.session.logout(keep_db=True)
            raise SessionExpiredException("The access of this auditor has ended")

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
            # Private mode of the company: the panel shows a lock
            "is_private": self.env["nudge.provider"]._is_private_mode(),
            "is_configured": bool(self.env["nudge.provider"].sudo()._get_default()),
            # Common requests are understood without any AI, so the panel works with no provider
            "can_work_without_ai": True,
            # A reply to a customer can be drafted when the module of the emails is installed
            "can_draft_reply": "nudge.email.review" in self.env,
            "can_share_screen": self.env["nudge.conversation"]._can_share_screen(),
            "can_use_agent": self.env["nudge.conversation"]._can_use_agent(),
            # Developer mode: an administrator the switch is on for
            "can_develop": self.env["nudge.conversation"]._can_develop(),
            # A new database that nobody has set up: Nudge offers to do it with the administrator
            "setup_offer": self.env["nudge.conversation"]._can_use_agent() and tools.setup.offered(self.env),
            # A report can be a spreadsheet when the module for it is installed
            "can_make_spreadsheet": self.env["nudge.conversation"]._can_make_spreadsheet(),
            # The commands of the message box this person may type
            "commands": tools.commands.listed(self.with_context(lang=user.lang).env),
            # The quick actions of the panel on a phone: those of the company, of the apps installed
            "mobile_tiles": self._get_nudge_mobile_tiles(),
            **self.env["nudge.voice"]._get_session_info(),
        }

    def _get_nudge_mobile_tiles(self):
        """:return: ids of the big tiles the panel shows on a phone, for the company in use"""
        tiles = self.env.company.sudo()._nudge_mobile_tiles()
        agent = self.env["nudge.conversation"]._can_use_agent()
        user = self.env.user
        if "photo" in tiles and not agent:
            tiles.remove("photo")
        if "clock" in tiles and not user.sudo().employee_id:
            tiles.remove("clock")
        return tiles

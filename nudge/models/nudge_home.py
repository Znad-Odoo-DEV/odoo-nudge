from odoo import api, models
from odoo.exceptions import AccessError, UserError

from ..tools import home

HOME_ACTION = "nudge.nudge_home_action"


class NudgeHome(models.AbstractModel):
    _name = "nudge.home"
    _description = "My Nudge Home"

    @api.model
    def _check_internal(self):
        """The home page is for the people of the company, as its menu is: not for a portal user over RPC"""
        if not self.env.user.has_group("base.group_user"):
            raise AccessError(self.env._("This page is for the people of the company."))

    @api.model
    def get_home(self, force=False):
        """:return: what the home page shows the person: tiles of the apps they may use, what
        Nudge says, their layout. Counted as them, kept five minutes."""
        self._check_internal()
        data = home.payload(self.env, force=bool(force))
        return dict(data, layout=home.clean_layout(self.env.user.nudge_home_layout),
                    is_login_home=self._is_login_home())

    @api.model
    def _is_login_home(self):
        action = self.env.ref(HOME_ACTION, raise_if_not_found=False)
        return bool(action) and self.env.user.action_id.id == action.id

    @api.model
    def save_layout(self, layout):
        """The order of the tiles and the hidden ones, kept on the person's own record."""
        self._check_internal()
        cleaned = home.clean_layout(layout)
        self.env.user.write({"nudge_home_layout": cleaned})
        return cleaned

    @api.model
    def set_login_home(self, enabled):
        """Open this page at login (or stop doing so): the person's own home action."""
        self._check_internal()
        action = self.env.ref(HOME_ACTION)
        user = self.env.user
        if enabled:
            user.write({"action_id": action.id})
        elif user.action_id.id == action.id:
            user.write({"action_id": False})
        return self._is_login_home()

    @api.model
    def show_tile(self, key):
        """:return: the window action that lists what the tile counts, built here as the person"""
        self._check_internal()
        if isinstance(key, str) and key.startswith("reports."):
            # searched, not browsed: the record rules say whose report it is
            report = self.env["nudge.report"].search([("id", "=", int(key[8:]) if key[8:].isdigit() else 0)])
            if not report:
                raise UserError(self.env._("This tile is not available to you."))
            return report.action_open_report()
        line = home.tile_lines(self.env).get(key if isinstance(key, str) else "")
        if not line:
            raise UserError(self.env._("This tile is not available to you."))
        return {
            "type": "ir.actions.act_window",
            "name": line["name"],
            "res_model": line["model"],
            "views": [[False, "list"], [False, "form"]],
            "domain": [list(term) if isinstance(term, tuple) else term for term in line["domain"]],
            "target": "current",
        }

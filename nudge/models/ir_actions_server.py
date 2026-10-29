from odoo import models

from odoo.addons.nudge.tools import approval_flow


class IrActionsServer(models.Model):
    _inherit = "ir.actions.server"

    def unlink(self):
        """A button of a Nudge approval flow runs one of its server actions: when the action goes, however
        (the screen, the ORM, a rule deleted with it), the button is switched off with it, so no button of
        Nudge is left pointing at an action that is gone."""
        ours = {}
        for action in self.sudo():
            if action.model_name and (action.name or "").startswith(approval_flow.NAME_PREFIX):
                ours.setdefault(action.model_name, []).append(action.id)
        result = super().unlink()
        for model_name, ids in ours.items():
            approval_flow.switch_off_buttons(self.env, model_name, ids)
        return result

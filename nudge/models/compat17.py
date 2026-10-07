from odoo import models
from odoo.exceptions import AccessError


class Base(models.AbstractModel):
    """What Odoo 18 calls has_access and check_access, for Odoo 17."""

    _inherit = "base"

    def _nudge_has_access(self, operation):
        """:return: whether the person may do ``operation`` on the model, and on
        these records when there are some"""
        if not self.check_access_rights(operation, raise_exception=False):
            return False
        if not self:
            return True
        try:
            self.check_access_rule(operation)
        except AccessError:
            return False
        return True

    def _nudge_check_access(self, operation):
        """:raise AccessError: when the person may not do ``operation``"""
        self.check_access_rights(operation)
        self.check_access_rule(operation)

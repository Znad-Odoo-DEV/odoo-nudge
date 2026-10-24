from odoo import _, api, fields, models
from odoo.exceptions import UserError

# Calls to connectors one person may make in a day, and a company in a month (0 or empty: no limit)
CALL_PARAMS = {
    "user_calls": "nudge_connect.budget_user_calls",
    "company_calls": "nudge_connect.budget_company_calls",
}


class NudgeUsage(models.Model):
    """A call to a connector is a line of the usage ledger of its own kind: it costs no tokens and no
    money, it counts as one call, against the limits of calls and in the usage report."""
    _inherit = "nudge.usage"

    kind = fields.Selection(selection_add=[("connector", "Connector Call")], ondelete={"connector": "cascade"})

    @api.model
    def _add_connector_call(self, user, company, **links):
        """Write down one call that reached a connector's server"""
        return self._add("connector", {}, 0.0, user, company, **links)

    @api.model
    def _call_limits(self):
        Parameter = self.env["ir.config_parameter"].sudo()
        limits = {}
        for name, param in CALL_PARAMS.items():
            try:
                limits[name] = max(int(float(Parameter.get_param(param) or 0)), 0)
            except ValueError:
                limits[name] = 0
        return limits

    @api.model
    def _check_call_budget(self):
        """Refuse a call to a connector that would go past the limit of calls.

        :raise UserError: with what to tell the person, in the words of the budget of a provider"""
        limits = self._call_limits()
        if not any(limits.values()):
            return
        today = fields.Date.context_today(self)
        if limits["user_calls"]:
            made = self.sudo().search_count([
                ("kind", "=", "connector"), ("user_id", "=", self.env.uid), ("date", "=", today)])
            if made >= limits["user_calls"]:
                raise UserError(_(
                    "You used all of your Nudge connector calls for today. Try again tomorrow, or ask "
                    "your administrator."))
        if limits["company_calls"]:
            made = self.sudo().search_count([
                ("kind", "=", "connector"), ("company_id", "=", self.env.company.id),
                ("date", ">=", today.replace(day=1))])
            if made >= limits["company_calls"]:
                raise UserError(_(
                    "Your company used all of its Nudge connector calls for this month. Ask your "
                    "administrator."))

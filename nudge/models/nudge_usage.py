from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

# NP-20: how many months of the usage ledger are kept (0 or empty: all of it, as before)
USAGE_RETENTION_PARAM = "nudge.usage_retention_months"
PURGE_BATCH = 5000

BUDGET_PARAMS = {
    # Per person and per day
    "user_tokens": "nudge.budget_user_tokens",
    "user_cost": "nudge.budget_user_cost",
    # Per company and per month
    "company_tokens": "nudge.budget_company_tokens",
    "company_cost": "nudge.budget_company_cost",
}


class NudgeUsage(models.Model):
    """One call to an AI provider: who, for what, how many tokens, how much.

    Conversations keep their own totals from their messages; this is the
    ledger every call is written in, speech included, that budgets are
    checked against."""
    _name = "nudge.usage"
    _inherit = "nudge.log"
    _description = "Nudge Usage"
    _order = "id desc"

    user_id = fields.Many2one("res.users", required=True, index=True, ondelete="cascade")
    company_id = fields.Many2one("res.company", index=True, ondelete="cascade")
    date = fields.Date(required=True, index=True)
    kind = fields.Selection(
        [("chat", "Conversation"), ("speech", "Speech to Text"), ("memory", "Remembered Answer"),
         ("noai", "Understood Without AI"), ("bulk", "AI over a List"),
         ("translation", "Module Translation")], required=True, default="chat")
    intent = fields.Char(string="Understood As", help="For a request understood without any AI: what it asked (open, find, filter...).")
    conversation_id = fields.Many2one("nudge.conversation", ondelete="set null")
    message_id = fields.Many2one("nudge.message", ondelete="set null", index=True)
    provider_id = fields.Many2one("nudge.provider", ondelete="set null")
    model_name = fields.Char(string="Model")
    input_tokens = fields.Integer()
    output_tokens = fields.Integer()
    cache_read_tokens = fields.Integer()
    cache_write_tokens = fields.Integer()
    tokens = fields.Integer(compute="_compute_tokens", store=True)
    cost = fields.Float(digits=(12, 6))

    def init(self):
        """The sums a budget reads before every call, and the latency panel, read one person or one
        company over a range of days: the index holds the tokens and the cost, so the sum never
        visits the table (8 ms to 0.1 ms on a million rows)."""
        super().init()
        self.env.cr.execute(
            "CREATE INDEX IF NOT EXISTS nudge_usage_user_date_sums_idx "
            "ON nudge_usage (user_id, date) INCLUDE (tokens, cost)")
        self.env.cr.execute(
            "CREATE INDEX IF NOT EXISTS nudge_usage_company_date_sums_idx "
            "ON nudge_usage (company_id, date) INCLUDE (tokens, cost)")

    @api.depends("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
    def _compute_tokens(self):
        for usage in self:
            usage.tokens = (
                usage.input_tokens + usage.output_tokens
                + usage.cache_read_tokens + usage.cache_write_tokens)

    @api.model
    def _add(self, kind, usage, cost, user, company, **links):
        """Write one call down.

        :param dict usage: normalized token counts
        :param user: who the call was made for
        :param company: in which company
        :param links: conversation_id, message_id, provider_id, model_name
        """
        return self.sudo().create({
            "kind": kind,
            "user_id": user.id,
            "company_id": company.id if company else False,
            "date": fields.Date.context_today(self.with_user(user)),
            "cost": cost or 0.0,
            **usage,
            **links,
        })

    @api.model
    def _get_budgets(self):
        """:return: the limits set by the administrator; 0 is no limit"""
        Parameter = self.env["ir.config_parameter"].sudo()
        budgets = {}
        for name, param in BUDGET_PARAMS.items():
            value = Parameter.get_param(param) or "0"
            try:
                budgets[name] = float(value) if "cost" in name else int(float(value))
            except ValueError:
                budgets[name] = 0
        return budgets

    @api.model
    def _spent(self, domain):
        """:return: (tokens, cost) of the calls that match"""
        [(tokens, cost)] = self.sudo()._read_group(domain, [], ["tokens:sum", "cost:sum"])
        return tokens or 0, cost or 0.0

    @api.model
    def _headroom(self):
        """:return: ``(tokens, cost)`` the person and the company may still
        spend, the least of the limits that apply; None where there is no limit"""
        budgets = self._get_budgets()
        today = fields.Date.context_today(self)
        tokens, cost = [], []
        if budgets["user_tokens"] or budgets["user_cost"]:
            spent_tokens, spent_cost = self._spent([("user_id", "=", self.env.uid), ("date", "=", today)])
            if budgets["user_tokens"]:
                tokens.append(budgets["user_tokens"] - spent_tokens)
            if budgets["user_cost"]:
                cost.append(budgets["user_cost"] - spent_cost)
        if budgets["company_tokens"] or budgets["company_cost"]:
            spent_tokens, spent_cost = self._spent([
                ("company_id", "=", self.env.company.id), ("date", ">=", today.replace(day=1))])
            if budgets["company_tokens"]:
                tokens.append(budgets["company_tokens"] - spent_tokens)
            if budgets["company_cost"]:
                cost.append(budgets["company_cost"] - spent_cost)
        return (min(tokens) if tokens else None), (min(cost) if cost else None)

    @api.model
    def _check_budget(self):
        """Refuse a call to a provider that would go past a limit.

        :raise UserError: with what to tell the person"""
        budgets = self._get_budgets()
        if not any(budgets.values()):
            return
        today = fields.Date.context_today(self)
        if budgets["user_tokens"] or budgets["user_cost"]:
            tokens, cost = self._spent([("user_id", "=", self.env.uid), ("date", "=", today)])
            if budgets["user_tokens"] and tokens >= budgets["user_tokens"]:
                raise UserError(_(
                    "You used all of your Nudge tokens for today. Try again tomorrow, or ask "
                    "your administrator."))
            if budgets["user_cost"] and cost >= budgets["user_cost"]:
                raise UserError(_(
                    "You used all of your Nudge budget for today. Try again tomorrow, or ask "
                    "your administrator."))
        if budgets["company_tokens"] or budgets["company_cost"]:
            first = today.replace(day=1)
            tokens, cost = self._spent([
                ("company_id", "=", self.env.company.id), ("date", ">=", first)])
            if budgets["company_tokens"] and tokens >= budgets["company_tokens"]:
                raise UserError(_(
                    "Your company used all of its Nudge tokens for this month. Ask your "
                    "administrator."))
            if budgets["company_cost"] and cost >= budgets["company_cost"]:
                raise UserError(_(
                    "Your company used all of its Nudge budget for this month. Ask your "
                    "administrator."))

    @api.model
    def _retention_months(self):
        """:return: how many months of the ledger are kept; 0 keeps it all"""
        value = self.env["ir.config_parameter"].sudo().get_param(USAGE_RETENTION_PARAM) or "0"
        try:
            return max(int(float(value)), 0)
        except ValueError:
            return 0

    @api.model
    def _cron_apply_retention(self, batch=PURGE_BATCH):
        """Forget the calls older than the months set in Settings (0: keep all), a batch at a
        time. The cut is at the first day of a month, `months` months back from this one, so the
        current month stays whole: budgets read the current month (the company) and today (the
        person), and their totals are the same after the purge. The ledger refuses ``unlink``
        (a log that can be edited tells nothing), so the rows are removed by the one statement
        of this method, which only this cron runs.

        :return: how many rows were deleted"""
        months = self._retention_months()
        if months <= 0:
            return 0
        cutoff = fields.Date.context_today(self).replace(day=1) - relativedelta(months=months)
        self.env.cr.execute(
            """DELETE FROM nudge_usage WHERE id IN (
                   SELECT id FROM nudge_usage WHERE date < %s ORDER BY id LIMIT %s
                   FOR UPDATE SKIP LOCKED)""", (cutoff, batch))
        deleted = self.env.cr.rowcount
        if deleted:
            self.env.invalidate_all()
        return deleted

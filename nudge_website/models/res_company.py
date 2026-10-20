from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    nudge_public_budget = fields.Integer(
        string="Website Assistant Tokens per Day", default=50000,
        help="What the AI may spend each day writing answers to visitors of the website, in tokens. "
             "Past it, visitors are still answered, from the products and files alone, without the AI. "
             "0: visitors are never answered by the AI.")
    nudge_web_keep_days = fields.Integer(
        string="Keep Visitor Messages (days)", default=30,
        help="What visitors typed in the website assistant, and what it answered, stays this many days. "
             "After that the lines are archived (not deleted) by a daily job; archived lines are no more "
             "listed or counted. 0: they are kept.")

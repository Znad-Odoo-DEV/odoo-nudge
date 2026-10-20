from odoo import api, fields, models


class Website(models.Model):
    _inherit = "website"

    nudge_assistant = fields.Boolean(
        string="Assistant Bubble", help="Show the chat bubble to the visitors of this website.")
    nudge_greeting = fields.Char(
        string="Greeting", translate=True,
        help="The first line of the bubble. Empty: a line that says what it can answer.")
    nudge_lang_ids = fields.Many2many(
        "res.lang", "nudge_website_lang_rel", "website_id", "lang_id", string="Assistant Languages",
        help="The bubble shows only in these languages of the website. Empty: in all of them.")

    def nudge_assistant_on(self, lang_code=None):
        """:return: whether the bubble answers on this website, in this language"""
        self.ensure_one()
        if not self.nudge_assistant:
            return False
        if self.nudge_lang_ids and lang_code:
            return lang_code in self.nudge_lang_ids.mapped("code")
        return True

    @api.model
    def nudge_default_greeting(self):
        return self.env._(
            "Hello! I can help with our products, our published information and, if you are "
            "logged in, your own orders and invoices.")

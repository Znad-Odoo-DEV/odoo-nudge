import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


PRIVACY_URL = re.compile(r"^(https?://[^\s<>\"']+|/(?!/)[^\s<>\"']*)$", re.IGNORECASE)


class Website(models.Model):
    _inherit = "website"

    nudge_assistant = fields.Boolean(
        string="Assistant Bubble", help="Show the chat bubble to the visitors of this website.")
    nudge_greeting = fields.Char(
        string="Greeting", translate=True,
        help="The first line of the bubble. Empty: a line that says what it can answer.")
    nudge_privacy_url = fields.Char(
        string="Privacy Policy Link",
        help="The page of your privacy policy. The notice under the greeting links to it. "
             "A full address starting with http:// or https://, or a path starting with /.")
    nudge_lang_ids = fields.Many2many(
        "res.lang", "nudge_website_lang_rel", "website_id", "lang_id", string="Assistant Languages",
        help="The bubble shows only in these languages of the website. Empty: in all of them.")

    @api.constrains("nudge_privacy_url")
    def _check_nudge_privacy_url(self):
        """The link is shown to every visitor: only a web address or a path of the site may be there,
        never javascript: or data:."""
        for website in self:
            url = website.nudge_privacy_url
            if url and not PRIVACY_URL.match(url):
                raise ValidationError(_(
                    "The privacy policy link must start with http://, https:// or a single /."))

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
        return _(
            "Hello! I can help with our products, our published information and, if you are "
            "logged in, your own orders and invoices.")

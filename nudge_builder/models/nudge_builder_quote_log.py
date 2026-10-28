"""One line per request-a-quote form sent (or refused): the rate limit counts these. Only a hash of the address
is kept, never the address, and lines older than a day are removed on the next request."""
from datetime import timedelta

from odoo import fields, models


class NudgeBuilderQuoteLog(models.Model):
    _name = "nudge.builder.quote.log"
    _description = "Website Builder: quote request log"
    _order = "id desc"

    website_id = fields.Many2one("website", ondelete="cascade", index=True)
    ip_hash = fields.Char(index=True)
    done = fields.Boolean(help="A lead or a note was made for this request.")

    def _count_recent(self, website, ip_hash, hours=1):
        """:return: ``(by this address, by this website)`` in the last ``hours`` and the last day"""
        since = fields.Datetime.now() - timedelta(hours=hours)
        day = fields.Datetime.now() - timedelta(days=1)
        own = self.search_count([("ip_hash", "=", ip_hash), ("create_date", ">=", since)])
        site = self.search_count([("website_id", "=", website.id), ("create_date", ">=", day)])
        return own, site

    def _forget_old(self):
        old = self.search([("create_date", "<", fields.Datetime.now() - timedelta(days=1))], limit=500)
        old.unlink()

from odoo import api, fields, models


class NudgeBuilderPage(models.Model):
    """One page the builder made: the link between the site and its website.page"""
    _name = "nudge.builder.page"
    _description = "Nudge Builder Page"
    _order = "sequence, id"

    site_id = fields.Many2one("nudge.builder.site", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="site_id.company_id", store=True, index=True)
    page_id = fields.Many2one("website.page", string="Website Page", ondelete="set null")
    view_id = fields.Many2one("ir.ui.view", string="View", ondelete="set null")
    key = fields.Char(required=True, help="home, about, services... legal_privacy, legal_terms, shop...")
    kind = fields.Char()
    name = fields.Char(required=True)
    url = fields.Char()
    lang_id = fields.Many2one("res.lang", string="Language",
                              help="Empty when the page is translated in place into every language of the site.")
    snippet_keys = fields.Char(help="The snippets of the page in order, separated by commas.")
    is_legal = fields.Boolean()
    is_shop = fields.Boolean()
    menu_id = fields.Many2one("website.menu", ondelete="set null")
    sequence = fields.Integer(default=10)
    is_published = fields.Boolean(related="page_id.is_published", string="Published")

    def action_open_editor(self):
        """Open the page in the website editor"""
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": f"{self.url}?enable_editor=1",
            "target": "self",
        }

    def _snippet_list(self):
        self.ensure_one()
        return [key for key in (self.snippet_keys or "").split(",") if key]

    @api.model
    def _join(self, keys):
        return ",".join(keys)

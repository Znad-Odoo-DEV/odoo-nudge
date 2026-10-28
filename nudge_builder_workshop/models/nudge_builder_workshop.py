from odoo import api, fields, models
from odoo.exceptions import ValidationError


class NudgeBuilderWorkshopService(models.Model):
    _name = "nudge.builder.workshop.service"
    _inherit = ["nudge.builder.content"]
    _order = "featured desc, sequence, id"
    _description = "Nudge Builder workshop service"

    category = fields.Char(translate=True, help="Like Oil and filters, Brakes, Tyres.")
    duration = fields.Char(translate=True, help="Like About 1 hour.")
    currency_id = fields.Many2one("res.currency", default=lambda self: self.env.company.currency_id)
    price_from = fields.Monetary(help="Leave empty to show no price.")
    bookable = fields.Boolean(default=True, help="Show a 'Book this service' button on this service.")
    image = fields.Image(max_width=1280, max_height=1280)

    @api.depends("image")
    def _compute_cover_image(self):
        for record in self:
            record.cover_image = record.image

    @api.constrains("price_from")
    def _check_price(self):
        for record in self:
            if record.price_from < 0:
                raise ValidationError(self.env._("The price cannot be negative."))


class NudgeBuilderSite(models.Model):
    _inherit = "nudge.builder.site"

    workshop_service_ids = fields.One2many("nudge.builder.workshop.service", "site_id", string="Workshop services")

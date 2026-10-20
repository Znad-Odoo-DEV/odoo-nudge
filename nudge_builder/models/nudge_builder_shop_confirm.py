"""The confirmation card of the shop: what Nudge would arrange, then Yes or No."""
import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class NudgeBuilderShopConfirm(models.TransientModel):
    _name = "nudge.builder.shop.confirm"
    _description = "Website Builder: shop plan card"

    site_id = fields.Many2one("nudge.builder.site", required=True, ondelete="cascade")
    plan_json = fields.Text(readonly=True)
    lines = fields.Text(string="What Nudge would do", compute="_compute_lines")

    @api.depends("plan_json")
    def _compute_lines(self):
        for card in self:
            plan = json.loads(card.plan_json or "{}")
            card.lines = "\n".join("- %s: %s" % (line["label"], line["value"]) for line in plan.get("lines") or [])

    @api.model
    def _open_for(self, site):
        """:return: the action that shows the plan of the shop of ``site``; nothing is changed yet"""
        plan = site._shop_plan()
        card = self.create({"site_id": site.id, "plan_json": json.dumps(plan)})
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": card.id,
                "view_mode": "form", "views": [(False, "form")], "target": "new",
                "name": _("The shop")}

    def _go(self, plan):
        self.ensure_one()
        site = self.site_id
        site._check_designer()
        if site.state not in ("previewed", "built"):
            raise UserError(_("Look at the three directions first."))
        site.shop_plan = plan
        return site.with_context(shop_decided=True).action_build()

    def action_yes(self):
        """Build the pages and arrange the shop exactly as the card says"""
        return self._go(json.loads(self.plan_json or "{}"))

    def action_no(self):
        """Build the pages only; the shop is left as it is"""
        return self._go(False)

"""Site Check for the workshop services: what the booking page needs and the site does not say."""
from odoo import _
from odoo.addons.nudge_builder.tools import checks as core

GROUP = "workshop"
KIND = "workshop_service"


def check_workshop(env, website, pages, **_options):
    """:return: findings about the published services: no price, no duration, no picture; a booking page nobody links"""
    site = core.builder_site(env, website)
    if not site or "nudge.builder.plugin" not in env:
        return []
    records = site._kind_records(KIND)
    out = []
    for key, missing in (
            ("workshop_no_price", records.filtered(lambda record: not record.price_from)),
            ("workshop_no_duration", records.filtered(lambda record: not record.duration)),
            ("workshop_no_picture", records.filtered(lambda record: not record.image))):
        if missing:
            out.append(core.finding(key, GROUP, core.INFO, len(missing), "nudge.builder.workshop.service", missing.ids))
    if records.filtered("bookable") and not any("/workshop-booking" in (page.record.view_id.arch or "") for page in pages):
        out.append(core.finding("workshop_unlinked", GROUP, core.WARNING, 1))
    return out


def texts(env, count, extra):
    return {
        "workshop_no_price": _("%s published services show no price.", count),
        "workshop_no_duration": _("%s published services show no duration.", count),
        "workshop_no_picture": _("%s published services have no picture.", count),
        "workshop_unlinked": _("Services can be booked but no page links to the booking page."),
    }

"""Site Check for the listings: what the search bar and the cards need and the site does not say."""
from odoo import _
from odoo.addons.nudge_builder.tools import checks as core

from . import listings

GROUP = "listings"


def check_listings(env, website, pages, **_options):
    """:return: findings about the published listings of the website's site: no price, no city, no picture"""
    site = core.builder_site(env, website)
    if not site or "nudge.builder.plugin" not in env:
        return []
    records = site._kind_records(listings.KIND)
    out = []
    for key, missing in (
            ("listings_no_price", records.filtered(lambda record: not record.price)),
            ("listings_no_city", records.filtered(lambda record: not record.location)),
            ("listings_no_picture", records.filtered(lambda record: not record.picture_ids.filtered("image")))):
        if missing:
            out.append(core.finding(key, GROUP, core.WARNING, len(missing), "nudge.builder.project", missing.ids))
    return out


def texts(env, count, extra):
    return {
        "listings_no_price": _("%s published listings have no price.", count),
        "listings_no_city": _("%s published listings have no city, so the city filter cannot find them.", count),
        "listings_no_picture": _("%s published listings have no picture.", count),
    }

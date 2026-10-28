"""The workshop service as a card of the website: its facts line and its booking button."""
from odoo import _
from urllib.parse import quote

from odoo.addons.nudge_builder.tools import content_sections as cs

KIND = "workshop_service"
LAYOUTS = ("grid", "list", "showcase")
BOOKING = "/workshop-booking"


def item(site, record, base, lang):
    """:return: the card of a service. Only what the record says is shown: no price is shown when there is none."""
    env = record.env
    facts = [record.category, record.duration]
    if record.price_from:
        facts.append(_("From %s", cs.format_price(record.price_from, record.currency_id.symbol or "")))
    base["meta"] = [fact for fact in facts if fact]
    if record.bookable:
        base["actions"] = [{"url": f"{BOOKING}?service={quote(record.name or '', safe='')}",
                            "text": _("Book this service")}]
    return base

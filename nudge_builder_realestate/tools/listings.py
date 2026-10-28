"""What the listings of a site look like: the search bar, the cards under it, and the pieces /listings reuses.

The bar is a plain GET form to /listings: no script, nothing stored. Its choices are drawn from the listings
that exist (the cities, the property types, the number of bedrooms), never from a list written here.
"""
from odoo.addons.nudge_builder.tools import content_sections as cs

KIND = "listing"
OFFERS = ("sale", "rent")
LAYOUTS = ("grid", "showcase", "carousel")
MAX_BEDROOMS = 8
FIELDS = ("offer", "type", "city", "bedrooms", "price_min", "price_max")


def words(env):
    """:return: the words the bar writes itself, in the language of ``env``"""
    _ = env._
    return {
        "filter_offer": _("For"), "filter_any": _("Any"), "filter_sale": _("Sale"), "filter_rent": _("Rent"),
        "filter_type": _("Property type"), "filter_city": _("City"), "filter_bedrooms": _("Bedrooms"),
        "filter_price_min": _("Price from"), "filter_price_max": _("Price up to"), "filter_search": _("Search"),
        "filter_clear": _("Clear"), "filter_none": _("No listing matches these filters."),
        "filter_bar": _("Search the listings"), "filter_count": _("%s listings"),
    }


def word(cx, key):
    return (cx.get("labels") or {}).get(key) or key


def options(items):
    """:return: ``(offers, types, cities, bedrooms)`` found in the listings (items of the section)"""
    offers = [offer for offer in OFFERS if any(item.get("listing_status") == offer for item in items)]
    types = sorted({item["category"] for item in items if item.get("category")})
    cities = sorted({item["location"] for item in items if item.get("location")})
    most = min(max([item.get("bedrooms") or 0 for item in items] or [0]), MAX_BEDROOMS)
    return offers, types, cities, most


def _field(form, cx, uid, name, label):
    cell = cs.el("div", "col-6 col-lg-2" if name != "offer" else "col-6 col-lg-1", form)
    cs.el("label", "form-label small mb-1", cell, label, attrs={"for": f"nb_lf_{name}_{uid}"})
    return cell


def _select(cell, cx, uid, name, choices, current):
    select = cs.el("select", "form-select form-select-sm", cell,
                   attrs={"name": name, "id": f"nb_lf_{name}_{uid}"})
    cs.el("option", None, select, word(cx, "filter_any"), attrs={"value": ""})
    for value, text in choices:
        cs.el("option", None, select, text, attrs={"value": value,
                                                   "selected": "selected" if str(current) == str(value) else None})


def filter_form(cx, items, values=None, uid="0"):
    """The search bar.

    :param list items: the listings the choices are drawn from (all of the site, not the ones found)
    :param dict values: what is chosen now (``/listings`` shows the visitor's own)
    :return: the lxml form"""
    values = values or {}
    offers, types, cities, most = options(items)
    form = cs.el("form", "nb-listing-filters row g-2 align-items-end mb-4", attrs={
        "method": "get", "action": "/listings", "role": "search", "aria-label": word(cx, "filter_bar")})
    if offers:
        _select(_field(form, cx, uid, "offer", word(cx, "filter_offer")), cx, uid, "offer",
                [(offer, word(cx, "filter_" + offer)) for offer in offers], values.get("offer") or "")
    if types:
        _select(_field(form, cx, uid, "type", word(cx, "filter_type")), cx, uid, "type",
                [(value, value) for value in types], values.get("type") or "")
    if cities:
        _select(_field(form, cx, uid, "city", word(cx, "filter_city")), cx, uid, "city",
                [(value, value) for value in cities], values.get("city") or "")
    if most:
        _select(_field(form, cx, uid, "bedrooms", word(cx, "filter_bedrooms")), cx, uid, "bedrooms",
                [(str(number), f"{number}+") for number in range(1, most + 1)], values.get("bedrooms") or "")
    for name in ("price_min", "price_max"):
        cell = _field(form, cx, uid, name, word(cx, "filter_" + name))
        number = values.get(name)
        cs.el("input", "form-control form-control-sm", cell, attrs={
            "type": "number", "min": "0", "step": "any", "inputmode": "decimal", "name": name,
            "id": f"nb_lf_{name}_{uid}", "value": None if number in (None, "") else number})
    actions = cs.el("div", "col-12 col-lg-auto d-flex gap-2", form)
    cs.el("button", "btn btn-primary btn-sm", actions, word(cx, "filter_search"), attrs={"type": "submit"})
    cs.el("a", "btn btn-link btn-sm", actions, word(cx, "filter_clear"), attrs={"href": "/listings"})
    return form


def cards(items, layout, direction, title, text, cx, uid):
    """:return: the section of the listings without the bar, or None when there are none"""
    return cs.render_kind(KIND, LAYOUTS, items, layout, direction, title, text, cx, uid)


def render(items, layout, direction, title, text, cx, uid):
    """:return: the section of a built page: the bar over the cards; None when there is no listing"""
    section = cards(items, layout, direction, title, text, cx, uid)
    if section is None:
        return None
    container = section.find("div")
    place = sum(1 for child in container if child.tag in ("h2", "p"))
    container.insert(place, filter_form(cx, items, uid=uid))
    return section


def item(site, record, base, lang):
    """The card of a listing is the card of a project, with its price, bedrooms, area and status."""
    return site._record_item("project", record, lang)

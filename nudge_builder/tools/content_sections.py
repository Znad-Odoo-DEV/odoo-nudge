"""Sections made of the company's own records: projects, products, services, team, testimonials, FAQ, locations,
contact, working hours, the quote link and the floating WhatsApp button.

Nothing here talks to Odoo or to a provider. A renderer takes plain dicts (one language already chosen by the
caller) and returns an lxml section built with the class names of Odoo's own snippets (``s_three_columns``,
``s_company_team``, ``s_faq_collapse``, ``s_quotes_carousel``, ``s_image_gallery``, ``s_map`` ...), so the website
editor treats it as it treats a dropped snippet. Rules that hold for every layout:

* every word comes from a record (or from the labels the caller passes); nothing is invented, a kind with no
  records returns ``None`` and the caller does not place the block;
* text goes in as text (lxml escapes it); a link or a picture address goes in only when :func:`safe.safe_url`
  accepts it; a map frame loads Google Maps or OpenStreetMap only, sandboxed and lazy;
* featured records come first, then the caller's order.
"""
import re
from urllib.parse import quote

from lxml import etree, html as lxml_html

from .safe import safe_text, safe_url

MAX_ITEMS = 24
MAX_CARD_TEXT = 300
DIRECTIONS = {"a": 0, "b": 1, "c": 2}  # Classic, Bold, Soft
MAP_HOSTS = ("https://www.google.com/maps", "https://maps.google.com/maps", "https://www.openstreetmap.org/")
ICON = re.compile(r"^fa(?:[srb])? fa-[a-z0-9-]{1,40}$")
NETWORK = re.compile(r"^[a-z]{2,20}$")
RTL_LANGS = ("ar", "he", "fa", "ur")
# Odoo 17-19 ship Font Awesome 4.7
SOCIAL_ICON = {
    "facebook": "fa-facebook", "instagram": "fa-instagram", "x": "fa-twitter", "twitter": "fa-twitter",
    "linkedin": "fa-linkedin", "youtube": "fa-youtube-play", "snapchat": "fa-snapchat-ghost",
    "telegram": "fa-telegram", "pinterest": "fa-pinterest", "tiktok": "fa-music",
}

# kind -> (layout of Classic, layout of Bold, layout of Soft)
LAYOUTS = {
    "project": ("grid", "showcase", "carousel"),
    "product": ("grid", "list", "carousel"),
    "service": ("grid", "list", "showcase"),
    "member": ("grid", "list", "carousel"),
    "testimonial": ("cards", "carousel", "list"),
    "faq": ("accordion", "columns", "list"),
    "location": ("list_map", "cards", "map"),
}
# the Odoo snippet a section stands for (what the editor and the page cards call it)
SNIPPET_OF = {
    "project": "s_three_columns", "product": "s_three_columns", "service": "s_features", "member": "s_company_team",
    "testimonial": "s_quotes_carousel", "faq": "s_faq_collapse", "location": "s_map", "contact": "s_contact_info",
    "hours": "s_text_block", "quote": "s_call_to_action", "whatsapp": "s_text_block",
}
# Every user-facing word this module writes into a page. The caller translates them (msgid = the English text).
LABELS = {
    "request_quote": "Request a quote",
    "whatsapp": "WhatsApp",
    "call": "Call",
    "email": "Email",
    "book": "Book now",
    "hours": "Working hours",
    "contact": "Get in touch",
    "directions": "Open in Google Maps",
    "map_of": "Map of %s",
    "main_office": "Main office",
    "before": "Before",
    "after": "After",
    "for_sale": "For sale",
    "for_rent": "For rent",
    "sold": "Sold",
    "planned": "Planned",
    "ongoing": "Ongoing",
    "done": "Completed",
    "bedrooms": "%s bedrooms",
    "area": "%s m²",
    "previous": "Previous",
    "next": "Next",
    "chat_on_whatsapp": "Chat with us on WhatsApp",
    "stars": "%s out of 5",
    "linkedin": "LinkedIn",
}


# ------------------------------------------------------------------ small helpers
def el(tag, cls=None, parent=None, text=None, attrs=None):
    node = etree.SubElement(parent, tag) if parent is not None else etree.Element(tag)
    if cls:
        node.set("class", " ".join(cls.split()))
    for key, value in (attrs or {}).items():
        if value is not None:
            node.set(key, str(value))
    if text is not None:
        node.text = safe_text(text)
    return node


def plain(markup):
    """:return: the words of an HTML field (a description), tags gone, one line per paragraph"""
    if not markup:
        return ""
    try:
        root = lxml_html.fragment_fromstring(str(markup), create_parent="div")
    except (etree.ParserError, ValueError):
        return safe_text(markup).strip()
    for br in root.xpath(".//br"):
        br.tail = "\n" + (br.tail or "")
    for block in root.xpath(".//p|.//li|.//div|.//h1|.//h2|.//h3|.//h4"):
        block.tail = "\n" + (block.tail or "")
    lines = [" ".join(line.split()) for line in root.text_content().split("\n")]
    return "\n".join(line for line in lines if line)


def shorten(text, limit=MAX_CARD_TEXT):
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",.;: ") + "..."


def label(cx, key, *args):
    text = (cx.get("labels") or {}).get(key) or LABELS[key]
    return text % args if args and "%" in text else text


def pick_layout(kind, layout=None, direction="a", known=None):
    """:return: the layout to use: the template's own when it names a known one, else the direction's choice

    :param known: the three layouts (Classic, Bold, Soft) of a kind a plugin registered; those of ``kind`` when None"""
    known = known or LAYOUTS.get(kind)
    if not known:
        return layout or "grid"
    if layout in known:
        return layout
    return known[DIRECTIONS.get(direction or "a", 0)]


def whatsapp_number(value):
    """:return: the digits of a WhatsApp number (E.164 without the plus) or '' when it is not one"""
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if 8 <= len(digits) <= 15 and not digits.startswith("0") else ""


def whatsapp_url(number, text=""):
    """:return: the click-to-chat link of a number with the text prefilled (urlencoded), or ''"""
    digits = whatsapp_number(number)
    if not digits:
        return ""
    url = "https://wa.me/" + digits
    text = " ".join(str(text or "").split())
    return url + "?text=" + quote(text, safe="") if text else url


def format_price(amount, symbol="", before=False):
    if amount in (None, False, ""):
        return ""
    try:
        number = float(amount)
    except (TypeError, ValueError):
        return ""
    body = f"{number:,.0f}" if number == int(number) else f"{number:,.2f}"
    if not symbol:
        return body
    return f"{symbol} {body}" if before else f"{body} {symbol}"


def map_src(value):
    """:return: the address of a map frame when it is Google Maps or OpenStreetMap, else ''"""
    value = safe_url(value)
    return value if value.startswith(MAP_HOSTS) else ""


def ordered(items):
    """featured first, the rest in the caller's order; at most MAX_ITEMS"""
    items = [item for item in (items or []) if isinstance(item, dict) and (item.get("name") or item.get("question")
                                                                           or item.get("author") or item.get("address"))]
    return sorted(items, key=lambda item: not item.get("featured"))[:MAX_ITEMS]


def _pictures(item):
    result = []
    for picture in item.get("pictures") or []:
        url = safe_url(picture.get("url") if isinstance(picture, dict) else picture)
        if url:
            result.append({"url": url, "alt": (picture.get("alt") if isinstance(picture, dict) else "") or
                           item.get("name") or ""})
    return result


def _section(kind, layout, cx, title="", text=""):
    rtl = bool(cx.get("rtl"))
    root = el("section", f"s_nb_{kind} nb-section nb-{kind} nb-layout-{layout} pt64 pb64" + (" nb-rtl" if rtl else ""),
              attrs={"data-snippet": cx.get("snippet") or SNIPPET_OF.get(kind, "s_text_block"), "data-nb-kind": kind,
                     "data-nb-layout": layout, "dir": "rtl" if rtl else None})
    container = el("div", "container", root)
    if title:
        el("h2", "text-center nb-title", container, title)
    if text:
        el("p", "lead text-center nb-lead", container, text)
    return root, container


def _image(parent, picture, cls="img-fluid", fallback_alt=""):
    if not picture:
        return None
    return el("img", cls, parent, attrs={"src": picture["url"], "alt": picture.get("alt") or fallback_alt,
                                         "loading": "lazy"})


def _paragraphs(parent, text, cls=None):
    for line in [line for line in (text or "").split("\n") if line.strip()]:
        el("p", cls, parent, line.strip())


def _link(parent, url, text, cls, icon=None, external=False):
    url = safe_url(url)
    if not url:
        return None
    attrs = {"href": url}
    if external:
        attrs.update({"target": "_blank", "rel": "noopener noreferrer"})
    link = el("a", cls, parent, attrs=attrs)
    text = safe_text(text or "")
    if icon and ICON.match(icon):
        mark = el("i", icon + " me-1", link, attrs={"role": "img", "aria-hidden": "true"})
        mark.tail = " " + text if text else None
    else:
        link.text = text
    return link


def _meta(kind, item, cx):
    """:return: the short facts line of a record, as a list of words"""
    parts = []
    if kind == "project":
        parts += [item.get("location"), item.get("year") and str(item["year"]), item.get("category"),
                  item.get("client")]
        status = item.get("status")
        if status in ("planned", "ongoing", "done"):
            parts.append(label(cx, status))
        listing = item.get("listing_status")
        if listing in ("sale", "rent", "sold"):
            parts.append(label(cx, {"sale": "for_sale", "rent": "for_rent", "sold": "sold"}[listing]))
        if item.get("bedrooms"):
            parts.append(label(cx, "bedrooms", item["bedrooms"]))
        if item.get("area"):
            parts.append(label(cx, "area", item["area"]))
        if item.get("price_text"):
            parts.append(item["price_text"])
    elif kind == "product":
        parts += [item.get("category")]
    elif kind == "member":
        parts += [item.get("role"), item.get("specialty")]
    elif kind == "testimonial":
        parts += [item.get("author_role"), item.get("company")]
    parts += item.get("meta") or []  # the facts a plugin's kind adds (the core's items carry none)
    return [str(part) for part in parts if part]


def _extras(parent, kind, item, cx):
    """The links of a record: the quote button of a service, the profile of a member, the map of a location"""
    if kind == "service" and item.get("quote") and cx.get("quote_url"):
        _link(parent, cx["quote_url"], label(cx, "request_quote"), "btn btn-primary btn-sm mt-2")
    elif kind == "member":
        if item.get("hours"):
            el("p", "small text-muted mb-1", parent, item["hours"])
        _link(parent, item.get("linkedin"), label(cx, "linkedin"), "small", "fa fa-linkedin", external=True)
    elif kind == "location":
        _location_lines(parent, item, cx)
    for action in item.get("actions") or []:  # the buttons a plugin's kind adds: {"url", "text"}
        _link(parent, action.get("url"), action.get("text"), "btn btn-primary btn-sm mt-2")


def _location_lines(parent, item, cx):
    if item.get("is_main"):
        el("span", "badge text-bg-primary mb-2", parent, label(cx, "main_office"))
    for line in (item.get("address"),):
        if line:
            el("p", "mb-1", parent, line)
    if item.get("phone"):
        _link(parent, "tel:" + re.sub(r"[^\d+]", "", item["phone"]), item["phone"], "d-block", "fa fa-phone")
    wa = whatsapp_url(item.get("whatsapp"), cx.get("whatsapp_text"))
    if wa:
        _link(parent, wa, label(cx, "whatsapp"), "d-block", "fa fa-whatsapp", external=True)
    if item.get("email"):
        _link(parent, "mailto:" + item["email"], item["email"], "d-block", "fa fa-envelope")
    if item.get("hours"):
        el("p", "small text-muted mt-2 mb-0 nb-hours", parent, item["hours"])
    if item.get("maps_url"):
        _link(parent, item["maps_url"], label(cx, "directions"), "d-block small mt-1", "fa fa-map-marker",
              external=True)


def _map_frame(parent, item, cx, cls="ratio ratio-16x9"):
    src = map_src(item.get("maps_embed_src"))
    if not src:
        return None
    box = el("div", cls, parent)
    el("iframe", "nb-map-frame", box, "", attrs={
        "src": src, "loading": "lazy", "title": label(cx, "map_of", item.get("name") or item.get("address") or ""),
        "referrerpolicy": "no-referrer-when-downgrade", "allowfullscreen": "", "width": "100%", "height": "100%",
        "sandbox": "allow-scripts allow-same-origin allow-popups"})
    return box


def _body(parent, kind, item, cx, full=False, heading="h3"):
    """The words of a record: name, facts, summary (or description), links"""
    if item.get("icon") and ICON.match(item["icon"]) and kind == "service":
        el("i", item["icon"] + " fa-2x mb-2 text-primary", parent, attrs={"role": "img", "aria-hidden": "true"})
    if item.get("name"):
        el(heading, "h5 card-title" if heading == "h3" else "", parent, item["name"])
    meta = _meta(kind, item, cx)
    if meta:
        el("p", "nb-meta text-muted small mb-2", parent, " · ".join(meta))
    text = item.get("summary") or plain(item.get("description"))
    if full:
        _paragraphs(parent, plain(item.get("description")) or item.get("summary"))
    elif text:
        el("p", "card-text", parent, shorten(text))
    if kind == "product" and item.get("price_text"):
        el("p", "fw-bold nb-price mb-0", parent, item["price_text"])
    _extras(parent, kind, item, cx)


# ------------------------------------------------------------------ layouts
def _grid(container, kind, items, cx):
    columns = "col-lg-3 col-md-6" if kind == "member" else "col-lg-4 col-md-6"
    row = el("div", "row g-4 mt-2 s_nb_row", container)
    for item in items:
        column = el("div", columns, row, attrs={"data-name": "Team Member"} if kind == "member" else None)
        card = el("div", "card h-100 nb-card" + (" text-center" if kind == "member" else ""), column)
        picture = (_pictures(item) or [None])[0]
        if picture:
            _image(card, picture, ("rounded-circle mx-auto mt-3 nb-avatar" if kind == "member"
                                   else "card-img-top nb-cover"), item.get("name") or "")
        _body(el("div", "card-body", card), kind, item, cx)
        if kind == "location":
            _map_frame(card, item, cx)


def _list(container, kind, items, cx):
    group = el("ul", "list-unstyled mt-3 nb-list", container)
    for item in items:
        row = el("li", "d-flex gap-3 align-items-start py-3 border-bottom", group)
        picture = (_pictures(item) or [None])[0]
        if picture:
            _image(row, picture, "nb-thumb rounded flex-shrink-0", item.get("name") or "")
        body = el("div", "flex-grow-1", row)
        if kind == "product":
            head = el("div", "d-flex justify-content-between gap-3", body)
            el("h3", "h5 mb-1", head, item.get("name"))
            if item.get("price_text"):
                el("span", "fw-bold nb-price", head, item["price_text"])
            meta = _meta(kind, item, cx)
            if meta:
                el("p", "nb-meta text-muted small mb-1", body, " · ".join(meta))
            text = item.get("summary") or plain(item.get("description"))
            if text:
                el("p", "mb-0", body, shorten(text))
        else:
            _body(body, kind, item, cx)


def _showcase(container, kind, items, cx):
    for index, item in enumerate(items):
        row = el("div", "row align-items-center g-4 mt-4" + (" flex-row-reverse" if index % 2 else ""), container)
        pictures = _pictures(item)
        if pictures:
            media = el("div", "col-lg-6", row)
            _image(media, pictures[0], "img-fluid rounded nb-cover", item.get("name") or "")
            before, after = safe_url(item.get("before_url")), safe_url(item.get("after_url"))
            if before and after:
                pair = el("div", "row g-2 mt-2", media)
                for url, key in ((before, "before"), (after, "after")):
                    cell = el("figure", "col-6 mb-0", pair)
                    _image(cell, {"url": url, "alt": f"{item.get('name') or ''} - {label(cx, key)}"}, "img-fluid rounded")
                    el("figcaption", "small text-muted", cell, label(cx, key))
            elif len(pictures) > 1:
                thumbs = el("div", "row g-2 mt-2", media)
                for picture in pictures[1:5]:
                    _image(el("div", "col-3", thumbs), picture, "img-fluid rounded", item.get("name") or "")
        _body(el("div", "col-lg-6" if pictures else "col-12", row), kind, item, cx, full=True, heading="h3")


def _carousel(container, kind, items, cx, uid):
    cid = f"nb_car_{kind}_{uid}"
    car = el("div", "carousel slide mt-4 nb-carousel", container, attrs={"id": cid, "data-bs-ride": "carousel"})
    inner = el("div", "carousel-inner", car)
    for index, item in enumerate(items):
        slide = el("div", "carousel-item" + (" active" if index == 0 else ""), inner)
        if kind == "testimonial":
            _quote_card(el("div", "col-lg-8 mx-auto text-center px-5 py-4", el("div", "row", slide)), item, cx)
            continue
        picture = (_pictures(item) or [None])[0]
        if picture:
            _image(slide, picture, "d-block w-100 nb-slide", item.get("name") or "")
        caption = el("div", "p-3 text-center" if not picture else "carousel-caption nb-caption", slide)
        _body(caption, kind, item, cx)
    if len(items) > 1:
        for direction, key, icon in (("prev", "previous", "carousel-control-prev-icon"),
                                     ("next", "next", "carousel-control-next-icon")):
            button = el("button", f"carousel-control-{direction}", car, attrs={
                "type": "button", "data-bs-target": "#" + cid, "data-bs-slide": direction})
            el("span", icon, button, attrs={"aria-hidden": "true"})
            el("span", "visually-hidden", button, label(cx, key))


def _quote_card(parent, item, cx):
    quote_text = item.get("summary") or plain(item.get("description"))
    block = el("blockquote", "s_blockquote blockquote nb-quote", parent)
    if quote_text:
        el("p", "mb-2", block, quote_text)
    rating = item.get("rating")
    if isinstance(rating, int) and 1 <= rating <= 5:
        el("span", "nb-stars text-warning", block, "★" * rating + "☆" * (5 - rating),
           attrs={"role": "img", "aria-label": label(cx, "stars", rating)})
    foot = el("footer", "blockquote-footer mt-2", block)
    author = item.get("author") or item.get("name")
    if author:
        el("strong", None, foot, author)
    tail = ", ".join(part for part in (item.get("author_role"), item.get("company")) if part)
    if tail:
        el("span", "nb-author-role", foot, " - " + tail)


def _quote_cards(container, items, cx):
    row = el("div", "row g-4 mt-2", container)
    for item in items:
        card = el("div", "card h-100 nb-card", el("div", "col-lg-4 col-md-6", row))
        body = el("div", "card-body", card)
        picture = (_pictures(item) or [None])[0]
        if picture:
            _image(body, picture, "rounded-circle nb-avatar mb-3", item.get("author") or "")
        _quote_card(body, item, cx)


def _quote_list(container, items, cx):
    for item in items:
        _quote_card(el("div", "col-lg-8 mx-auto py-3 border-bottom", container), item, cx)


def _accordion(container, items, cx, uid):
    cid = f"nb_faq_{uid}"
    box = el("div", "accordion mt-4 nb-accordion", container, attrs={"id": cid})
    for index, item in enumerate(items):
        shown = index == 0
        entry = el("div", "accordion-item", box)
        head = el("h3", "accordion-header", entry)
        el("button", "accordion-button" + ("" if shown else " collapsed"), head, item.get("question") or item.get("name"),
           attrs={"type": "button", "id": f"{cid}_b{index}", "data-bs-toggle": "collapse",
                  "data-bs-target": f"#{cid}_c{index}", "aria-expanded": "true" if shown else "false",
                  "aria-controls": f"{cid}_c{index}"})
        panel = el("div", "accordion-collapse collapse" + (" show" if shown else ""), entry, attrs={
            "id": f"{cid}_c{index}", "aria-labelledby": f"{cid}_b{index}", "data-bs-parent": "#" + cid})
        _paragraphs(el("div", "accordion-body", panel), plain(item.get("answer") or item.get("description")))


def _faq_columns(container, items, cx):
    row = el("div", "row g-4 mt-2", container)
    for item in items:
        column = el("div", "col-lg-6", row)
        el("h3", "h5", column, item.get("question") or item.get("name"))
        _paragraphs(column, plain(item.get("answer") or item.get("description")))


def _faq_list(container, items, cx):
    holder = el("dl", "mt-3 nb-faq-list", container)
    for item in items:
        el("dt", "h5 mt-3", holder, item.get("question") or item.get("name"))
        _paragraphs(el("dd", None, holder), plain(item.get("answer") or item.get("description")))


def _locations(container, items, cx, layout):
    if layout == "map":
        main = next((item for item in items if item.get("is_main") and map_src(item.get("maps_embed_src"))), None)
        main = main or next((item for item in items if map_src(item.get("maps_embed_src"))), None)
        if main:
            _map_frame(el("div", "mt-4", container), main, cx, "ratio ratio-21x9")
        row = el("div", "row g-4 mt-2", container)
        for item in items:
            column = el("div", "col-lg-4 col-md-6", row)
            if item.get("name"):
                el("h3", "h5", column, item["name"])
            _location_lines(column, item, cx)
    elif layout == "cards":
        _grid(container, "location", items, cx)
    else:  # list_map
        for item in items:
            row = el("div", "row align-items-center g-4 mt-3 pb-3 border-bottom", container)
            info = el("div", "col-lg-5", row)
            if item.get("name"):
                el("h3", "h5", info, item["name"])
            _location_lines(info, item, cx)
            if map_src(item.get("maps_embed_src")):
                _map_frame(el("div", "col-lg-7", row), item, cx)


# ------------------------------------------------------------------ entry points
def render(kind, items, layout=None, direction="a", title="", text="", cx=None, uid="0"):
    """The section of one kind of record.

    :param str kind: project, product, service, member, testimonial, faq or location
    :param list items: dicts in the language written (``name``, ``summary``, ``description`` as plain text or
        HTML, ``pictures`` [{url, alt}], ``featured`` and the fields of the kind)
    :param layout: a layout of :data:`LAYOUTS` or None for the direction's choice
    :param dict cx: ``rtl`` bool, ``labels`` {key: translated}, ``quote_url``, ``whatsapp_text``
    :param uid: makes the ids of a carousel or an accordion unique on the page
    :return: the lxml section, or None when there is nothing to show"""
    if kind not in LAYOUTS:
        return None
    return render_kind(kind, LAYOUTS[kind], items, layout, direction, title, text, cx, uid)


def render_kind(kind, layouts, items, layout=None, direction="a", title="", text="", cx=None, uid="0"):
    """The section of one kind of record, whose three layouts are given: the core's kinds go through here (see
    :func:`render`) and so can the kind of a plugin, which may call it for its own renderer.

    :param tuple layouts: the layouts of Classic, Bold and Soft of the kind
    :return: the lxml section, or None when there is nothing to show"""
    cx = cx or {}
    items = ordered(items)
    if not items:
        return None
    layout = pick_layout(kind, layout, direction, known=layouts)
    root, container = _section(kind, layout, cx, title, text)
    if kind == "faq":
        {"accordion": lambda: _accordion(container, items, cx, uid), "columns": lambda: _faq_columns(container, items, cx),
         "list": lambda: _faq_list(container, items, cx)}[layout]()
    elif kind == "location":
        _locations(container, items, cx, layout)
    elif kind == "testimonial":
        {"cards": lambda: _quote_cards(container, items, cx), "carousel": lambda: _carousel(container, kind, items, cx, uid),
         "list": lambda: _quote_list(container, items, cx)}[layout]()
    elif layout == "carousel":
        _carousel(container, kind, items, cx, uid)
    elif layout == "showcase":
        _showcase(container, kind, items, cx)
    elif layout == "list":
        _list(container, kind, items, cx)
    else:
        _grid(container, kind, items, cx)
    return root


def render_contact(contact, title="", text="", cx=None):
    """The contact block from the site's contact fields.

    :param dict contact: ``whatsapp`` (number), ``phone``, ``email``, ``booking_url``, ``hours``, ``socials``
        [(network, url)]
    :return: the section or None when the site has none of them"""
    cx = cx or {}
    contact = contact or {}
    wa = whatsapp_url(contact.get("whatsapp"), cx.get("whatsapp_text"))
    phone = contact.get("phone") and re.sub(r"[^\d+]", "", contact["phone"])
    email = contact.get("email")
    booking = safe_url(contact.get("booking_url"))
    socials = [(net, safe_url(url)) for net, url in contact.get("socials") or []
               if NETWORK.match(str(net or "")) and safe_url(url)]
    hours = contact.get("hours")
    if not (wa or phone or email or booking or socials or hours):
        return None
    root, container = _section("contact", "list", cx, title or label(cx, "contact"), text)
    row = el("div", "row g-4 mt-2 justify-content-center", container)
    left = el("div", "col-lg-5", row)
    if wa:
        _link(left, wa, label(cx, "chat_on_whatsapp"), "btn btn-success mb-3", "fa fa-whatsapp", external=True)
    for url, text_, icon in ((phone and "tel:" + phone, contact.get("phone"), "fa fa-phone"),
                             (email and "mailto:" + str(email), email, "fa fa-envelope")):
        if url:
            _link(el("p", "mb-2", left), url, text_, "", icon)
    if booking:
        _link(left, booking, label(cx, "book"), "btn btn-primary mt-2", external=True)
    if socials:
        line = el("p", "mt-3 nb-socials", left)
        for network, url in socials:
            link = _link(line, url, "", "me-3 fs-4", "fa " + SOCIAL_ICON.get(network, "fa-link"), external=True)
            if link is not None:
                link.set("aria-label", network.title())
    if hours:
        right = el("div", "col-lg-5", row)
        el("h3", "h5", right, label(cx, "hours"))
        for line in [line for line in str(hours).split("\n") if line.strip()]:
            el("p", "mb-1 nb-hours", right, line.strip())
    return root


def render_hours(hours, title="", cx=None):
    cx = cx or {}
    if not hours or not str(hours).strip():
        return None
    root, container = _section("hours", "list", cx, title or label(cx, "hours"))
    box = el("div", "col-lg-6 mx-auto text-center", el("div", "row", container))
    for line in [line for line in str(hours).split("\n") if line.strip()]:
        el("p", "mb-1 nb-hours", box, line.strip())
    return root


def render_quote(quote_url, title="", text="", cx=None):
    """The "Request a quote" call to action; None when the site has no quote form"""
    cx = cx or {}
    url = safe_url(quote_url)
    if not url:
        return None
    root, container = _section("quote", "cta", cx, title or label(cx, "request_quote"), text)
    root.set("class", root.get("class") + " text-center")
    _link(el("p", "mt-3", container), url, label(cx, "request_quote"), "btn btn-primary btn-lg")
    return root


def whatsapp_float(number, text="", cx=None):
    """The floating WhatsApp button (a small block the editor can move or delete). None when no valid number.
    Its position is in ``nudge_builder_content.scss`` and flips on an RTL page."""
    cx = cx or {}
    url = whatsapp_url(number, text)
    if not url:
        return None
    rtl = bool(cx.get("rtl"))
    box = el("div", "nb-wa-float" + (" nb-rtl" if rtl else ""), attrs={"data-snippet": "s_text_block",
                                                                       "data-nb-kind": "whatsapp"})
    link = el("a", "nb-wa-link btn btn-success rounded-circle", box, attrs={
        "href": url, "target": "_blank", "rel": "noopener noreferrer", "aria-label": label(cx, "chat_on_whatsapp"),
        "title": label(cx, "chat_on_whatsapp")})
    el("i", "fa fa-whatsapp fa-2x", link, attrs={"role": "img", "aria-hidden": "true"})
    return box


def is_rtl(lang_code):
    return str(lang_code or "")[:2] in RTL_LANGS

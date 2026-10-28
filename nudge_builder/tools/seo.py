"""SEO of a page of a built site: structured data (JSON-LD) and the helpers that put it in the page head.

``jsonld_for(site, page)`` writes, from the site's records only (the 60.1 rule: nothing is invented),

* ``LocalBusiness`` (or the type the template's sector maps to) on the home and contact pages, with the
  phone, the e-mail, the social links, the working hours and every location,
* ``Product`` for the shop or showcase items on the pages that show them,
* ``FAQPage`` on the FAQ page.

The text is JSON with ``<``, ``>``, ``&`` and the line separators written as ``\\uXXXX`` escapes, so it can sit
inside a ``<script>`` of a page arch without any way to close it or to open a tag. The head slot is the
``head`` variable of ``website.layout``: a page arch puts ``<t t-set="head">...</t>`` inside its ``t-call``.
"""
import json
import re

from lxml import etree

from odoo.tools import html2plaintext

from . import compat

CONTEXT = "https://schema.org"
SCRIPT_ID = "nb-jsonld"
MAX_ITEMS = 30

# the sector of a template -> the schema.org type of the business
BUSINESS_TYPES = {
    "contracting": "GeneralContractor", "real_estate": "RealEstateAgent", "law_firm": "LegalService",
    "clinic": "MedicalClinic", "restaurant": "Restaurant", "school": "School", "workshop": "AutoRepair",
    "services_company": "ProfessionalService",
}
SHOP_PAGES = ("shop", "products", "product", "menu", "catalog", "catalogue")
HOME_PAGES = ("home", "contact", "contact_us", "contactus")
FAQ_PAGES = ("faq", "faqs")

DAYS = {"mon": "Mo", "tue": "Tu", "wed": "We", "thu": "Th", "fri": "Fr", "sat": "Sa", "sun": "Su"}
HOURS_LINE = re.compile(
    r"^\s*(mon|tue|wed|thu|fri|sat|sun)[a-z]*\s*(?:(?:-|–|to)\s*(mon|tue|wed|thu|fri|sat|sun)[a-z]*)?\s*[:,]?\s*"
    r"(\d{1,2})[:.](\d{2})\s*(?:-|–|to)\s*(\d{1,2})[:.](\d{2})\s*$", re.IGNORECASE)


# ----------------------------------------------------------------------
# Reading the records
# ----------------------------------------------------------------------
def _text(value):
    return " ".join(html2plaintext(value or "").split()) if value else ""


def _lang_code(site, lang):
    if lang is None:
        return site._source_lang().code
    return getattr(lang, "code", lang)


def _records(site, kind, code, limit=MAX_ITEMS):
    """:return: the published records of one kind of ``site``, in the language ``code``, in their order"""
    model = "nudge.builder.%s" % kind
    if model not in site.env:
        return site.env["res.partner"].browse()
    Model = site.env[model].with_context(lang=code)
    domain = [("site_id", "=", site.id)]
    if "published" in Model._fields:
        domain.append(("published", "=", True))
    return Model.search(domain, limit=limit)


def _base_url(site):
    website = site.website_id
    try:
        return (website.get_base_url() or "").rstrip("/")
    except Exception:  # noqa: BLE001
        return (website.domain or "").rstrip("/")


def _absolute(site, path):
    path = path or ""
    return path if path.startswith("http") else _base_url(site) + path


def _opening_hours(text):
    """:return: the lines of a working-hours text that read as ``Mon-Fri 08:00-17:00``, as schema.org writes them;
    what does not read that way is left out, not guessed"""
    out = []
    for line in (text or "").splitlines():
        match = HOURS_LINE.match(line)
        if not match:
            continue
        first, last, h1, m1, h2, m2 = match.groups()
        days = DAYS[first[:3].lower()] + ("-" + DAYS[last[:3].lower()] if last else "")
        out.append("%s %02d:%s-%02d:%s" % (days, int(h1), m1, int(h2), m2))
    return out


def _address(location):
    parts = {
        "streetAddress": location.street if "street" in location._fields else "",
        "addressLocality": location.city if "city" in location._fields else "",
        "addressRegion": location.state_id.name if "state_id" in location._fields and location.state_id else "",
        "postalCode": location.zip if "zip" in location._fields else "",
        "addressCountry": location.country_id.code if "country_id" in location._fields and location.country_id else "",
    }
    parts = {key: value for key, value in parts.items() if value}
    return dict(parts, **{"@type": "PostalAddress"}) if parts else None


def _geo(location):
    lat = location.latitude if "latitude" in location._fields else 0
    lng = location.longitude if "longitude" in location._fields else 0
    if (lat or lng) and -90 <= lat <= 90 and -180 <= lng <= 180:
        return {"@type": "GeoCoordinates", "latitude": lat, "longitude": lng}
    return None


def _clean(data):
    """:return: ``data`` without the empty values"""
    return {key: value for key, value in data.items() if value not in (None, "", [], {}, False)}


# ----------------------------------------------------------------------
# The objects
# ----------------------------------------------------------------------
def business(site, code):
    """:return: the ``LocalBusiness`` of the site in the language ``code`` (the company's facts and the records)"""
    site = site.with_context(lang=code)
    company = site.company_id
    phone = (site.phone if "phone" in site._fields else "") or company.phone
    email = (site.email if "email" in site._fields else "") or company.email
    hours = site.hours if "hours" in site._fields else ""
    socials = site.social_ids.mapped("url") if "social_ids" in site._fields else []
    locations = _records(site, "location", code)
    main = locations.filtered("is_main")[:1] if locations and "is_main" in locations._fields else locations[:1]
    main = main or locations[:1]
    kind = BUSINESS_TYPES.get(site.template_key or "", "Store" if getattr(site, "shop", False) else "LocalBusiness")
    data = {
        "@type": kind, "name": company.name, "url": _base_url(site),
        "image": _absolute(site, "/web/image/website/%s/logo" % site.website_id.id) if site.website_id.logo else "",
        "telephone": phone, "email": email, "sameAs": [url for url in socials if url],
        "openingHours": _opening_hours(hours) or _opening_hours(main.hours if main and "hours" in main._fields else ""),
        "inLanguage": compat.lang_code(code),
    }
    if main:
        data["address"] = _address(main)
        data["geo"] = _geo(main)
        data["telephone"] = data["telephone"] or main.phone
    places = []
    for location in locations:
        places.append(_clean({
            "@type": "Place", "name": location.name, "address": _address(location), "geo": _geo(location),
            "telephone": location.phone if "phone" in location._fields else "",
            "hasMap": location.maps_url if "maps_url" in location._fields else ""}))
    if len(places) > 1:
        data["location"] = places
    return _clean(data)


def _offer(price, currency):
    if not price or price <= 0:
        return None
    return _clean({"@type": "Offer", "price": "%.2f" % price, "priceCurrency": currency or "",
                   "availability": "https://schema.org/InStock"})


def products(site, code):
    """:return: a ``Product`` per showcase item and per shop product the site lists"""
    out = []
    for item in _records(site, "product", code):
        pictures = item.picture_ids[:3] if "picture_ids" in item._fields else []
        out.append(_clean({
            "@type": "Product", "name": item.name,
            "description": _text(item.description if "description" in item._fields else "") or
            (item.summary if "summary" in item._fields else ""),
            "image": [_absolute(site, "/web/image/nudge.builder.picture/%s/image" % pic.id) for pic in pictures],
            "category": item.category if "category" in item._fields else "",
            "offers": _offer(item.price if "price" in item._fields else 0,
                             item.currency_id.name if "currency_id" in item._fields else "")}))
    if "shop_product_ids" in site._fields:
        for product in site.shop_product_ids.with_context(lang=code)[:MAX_ITEMS]:
            if "is_published" in product._fields and not product.is_published:
                continue
            out.append(_clean({
                "@type": "Product", "name": product.name, "description": _text(product.description_sale),
                "image": [_absolute(site, "/web/image/product.template/%s/image_1920" % product.id)]
                if product.image_1920 else [],
                "offers": _offer(product.list_price, product.currency_id.name)}))
    return out


def faq(site, code):
    """:return: the ``FAQPage`` of the published questions, or None"""
    entities = []
    for item in _records(site, "faq", code, limit=50):
        question, answer = (item.question or "").strip(), _text(item.answer)
        if question and answer:
            entities.append({"@type": "Question", "name": question,
                             "acceptedAnswer": {"@type": "Answer", "text": answer}})
    return {"@type": "FAQPage", "mainEntity": entities} if entities else None


def _page_words(page):
    return {(page.key or "").lower(), (page.kind or "").lower()}


def jsonld_objects(site, page, lang=None):
    """:return: the schema.org objects of ``page`` in one language, ``[dict]`` (empty when the page has none)"""
    site.ensure_one()
    code = _lang_code(site, lang)
    words = _page_words(page)
    objects = []
    if words & set(HOME_PAGES):
        data = business(site, code)
        if data.get("name"):
            objects.append(data)
    if getattr(page, "is_shop", False) or words & set(SHOP_PAGES):
        objects += products(site, code)
    if words & set(FAQ_PAGES):
        data = faq(site, code)
        if data:
            objects.append(data)
    return objects


def dump(data):
    """:return: ``data`` as JSON that cannot close a script or open a tag"""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    for char, escape in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"),
                         (" ", "\\u2028"), (" ", "\\u2029")):
        text = text.replace(char, escape)
    return text


def jsonld_for(site, page, lang=None):
    """:return: the JSON-LD text of ``page`` in one language (the site's main by default), escaped for a script
    element; one object, or ``{"@context", "@graph": [...]}`` for several; ``""`` when the page has none"""
    objects = jsonld_objects(site, page, lang)
    if not objects:
        return ""
    if len(objects) == 1:
        return dump(dict(objects[0], **{"@context": CONTEXT}))
    return dump({"@context": CONTEXT, "@graph": objects})


# ----------------------------------------------------------------------
# The head of the page
# ----------------------------------------------------------------------
def head_xml(site, page):
    """:return: ``<t t-set="head">`` with one JSON-LD script per language of the site (each shown only on its own
    language when the site has several), or ``""`` when the page has no structured data"""
    langs = site._langs()
    scripts = []
    for lang in langs:
        text = jsonld_for(site, page, lang)
        if not text:
            continue
        condition = ' t-if="request.env.lang == \'%s\'"' % lang.code if len(langs) > 1 else ""
        scripts.append('<script id="%s" type="application/ld+json"%s>%s</script>' % (SCRIPT_ID, condition, text))
    return '<t t-set="head">%s</t>' % "".join(scripts) if scripts else ""


def has_jsonld(arch):
    """:return: whether a page arch already carries the builder's structured data"""
    return SCRIPT_ID in (arch or "") and "ld+json" in (arch or "")


def set_head(arch, snippet):
    """:return: ``arch`` with the builder's structured data replaced by ``snippet`` (or taken out when it is empty);
    the rest of the head the page sets is kept"""
    tree = etree.fromstring(arch.encode() if isinstance(arch, str) else arch)
    for script in tree.xpath('//script[@id="%s"]' % SCRIPT_ID):
        parent = script.getparent()
        parent.remove(script)
        if parent.tag == "t" and parent.get("t-set") == "head" and not len(parent) and not (parent.text or "").strip():
            parent.getparent().remove(parent)
    if snippet:
        call = tree.xpath('//t[@t-call="website.layout"]')
        if call:
            node = etree.fromstring(snippet)
            existing = call[0].xpath('./t[@t-set="head"]')
            if existing:
                for script in node:
                    existing[0].append(script)
            else:
                call[0].insert(0, node)
    return etree.tostring(tree, encoding="unicode", method="xml")


def put_in_head(site, page):
    """Write the structured data of ``page`` (a ``nudge.builder.page``) into the head of its view. The script is
    not a translated term, so the translations of the page keep what they have; each language's script is shown
    only on that language. Nothing else of the page changes.

    :return: whether the view was written"""
    site.ensure_one()
    view = page.view_id
    if not view:
        return False
    compat.check_write(view)
    snippet = head_xml(site, page)
    source = view.with_context(lang="en_US").arch_db
    if not snippet and not has_jsonld(source):
        return False
    arch = set_head(source, snippet)
    if arch == source:
        return False
    view.with_context(lang="en_US").write({"arch_db": arch})
    return True

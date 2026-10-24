"""The check of a website: SEO, links, translations, performance, accessibility, legal and shop.

Runs on any website, built by Nudge or not. A check

* runs as the person who asked (their rights, their record rules) and only reads,
* never publishes and never deletes: the fixes below only fill what is empty or add
  an unpublished draft, and only after the person has seen the list of changes,
* finds what is deterministic, with no AI.

A finding is a dict: ``key``, ``group``, ``severity``, ``count``, ``model`` and ``ids`` (what
"Show me" opens), ``urls`` (the pages concerned) and ``fix`` (the key of the deterministic fix,
or False). ``finding_text`` writes its sentence in the language of whoever reads it.
"""
from odoo import _
import ipaddress
import logging
import re
import socket
import time
from urllib.parse import unquote, urljoin, urlparse

from lxml import etree

from odoo.exceptions import AccessError
from odoo.tools import html_escape, is_html_empty

from odoo.addons.nudge.tools import egress

_logger = logging.getLogger(__name__)

CRITICAL, WARNING, INFO = "critical", "warning", "info"
SEVERITIES = (CRITICAL, WARNING, INFO)

TITLE_MIN, TITLE_MAX = 10, 60
DESCRIPTION_MIN, DESCRIPTION_MAX = 50, 160
IMAGE_BYTES = 500 * 1024
MAX_SNIPPETS = 20
MAX_EXTERNAL = 50
EXTERNAL_TIMEOUT = 5
EXTERNAL_BUDGET = 60  # seconds for all the external links of one check
MAX_REDIRECTS = 3
MAX_IDS = 200
MIN_CONTRAST = 4.5
GROUPS = ("seo", "links", "translations", "performance", "accessibility", "legal", "shop")

DESIGNER_GROUP = "website.group_website_designer"

ASSET_URL = re.compile(r"/web/(?:image|content)/(?:ir\.attachment/)?(\d+)")
LEGAL_PRIVACY = ("privacy", "الخصوصية", "خصوصية")
LEGAL_TERMS = ("terms", "conditions", "الشروط", "الأحكام")
INTERNAL_SKIP = ("/web/", "/web#", "/website/", "/@/", "/odoo", "/my", "/shop/cart", "/shop/payment", "/jsonrpc")
FIELD_TYPES_SKIPPED = ("hidden", "submit", "button", "reset", "image")


def check_designer(env):
    """:raise AccessError: when the user may not design the website"""
    if not (env.user.has_group(DESIGNER_GROUP) or env.user.has_group("base.group_system")):
        raise AccessError(_("Only a website designer may check or fix a website."))


def finding(key, group, severity, count, model=None, ids=(), urls=(), fix=False, extra=None):
    """:return: one finding. ``ids`` are the records "Show me" opens (at most MAX_IDS)"""
    return {"key": key, "group": group, "severity": severity, "count": count, "model": model,
            "ids": list(ids)[:MAX_IDS], "urls": list(urls)[:MAX_IDS], "fix": fix, "extra": extra or {}}


# ----------------------------------------------------------------------
# What a page is made of
# ----------------------------------------------------------------------
class Page:
    """A page of the website, with its content parsed once"""

    def __init__(self, record):
        self.record = record
        self.id = record.id
        self.url = record.url or ""
        self.name = record.name or self.url
        self.tree = self._parse(record)

    @staticmethod
    def _parse(record):
        try:
            arch = record.view_id.arch or ""
            return etree.fromstring(arch.encode("utf-8"), etree.XMLParser(recover=True, resolve_entities=False))
        except Exception:  # noqa: BLE001
            return None

    def find(self, path):
        return self.tree.xpath(path) if self.tree is not None else []

    def text(self):
        return " ".join(self.tree.itertext()) if self.tree is not None else ""


def pages_of(env, website):
    """:return: the pages of the website: those of the website itself, and the generic ones its
    own page does not replace"""
    Page_ = env["website.page"].with_context(active_test=False)
    records = Page_.search([("website_id", "in", [False, website.id])], order="url, id")
    own = {record.url for record in records if record.website_id}
    seen, out = set(), []
    for record in records:
        if not record.website_id and record.url in own:
            continue
        if record.url in seen or not record.view_id:
            continue
        seen.add(record.url)
        out.append(Page(record))
    return out


def page_title(page, website):
    record = page.record
    return (record.website_meta_title or "").strip() or ((record.name or "").strip())


# ----------------------------------------------------------------------
# Colours
# ----------------------------------------------------------------------
def _rgb(hex_color):
    value = hex_color.lstrip("#")
    if len(value) == 3:
        value = "".join(char * 2 for char in value)
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))


def luminance(hex_color):
    def channel(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in _rgb(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(first, second):
    """:return: the contrast ratio of two colours, 1 to 21"""
    a, b = luminance(first), luminance(second)
    high, low = max(a, b), min(a, b)
    return (high + 0.05) / (low + 0.05)


def read_palette(env, website):
    """:return: ``{"o-color-1": "#rrggbb", ...}`` of the colours the website customised, else {}"""
    palette = {}
    try:
        attachments = env["ir.attachment"].search([
            ("url", "like", "%user_color_palette.scss"), ("website_id", "in", [False, website.id])], order="id")
        for attachment in attachments:
            content = (attachment.raw or b"").decode("utf-8", "ignore")
            for number, color in re.findall(r"o-color-(\d)['\"]?\s*:\s*(#[0-9a-fA-F]{3,8})", content):
                palette[f"o-color-{number}"] = color[:7]
    except AccessError:
        return {}
    return palette


def palette_pairs(palette):
    """:return: ``[(name, foreground, background)]`` that make text: the text colour on the
    backgrounds, and white on the colours of the buttons"""
    pairs = []
    text = palette.get("o-color-5")
    for number in ("o-color-4", "o-color-3"):
        if text and palette.get(number):
            pairs.append((f"text on {number}", text, palette[number]))
    for number in ("o-color-1", "o-color-2"):
        if palette.get(number):
            pairs.append((f"white on {number}", "#FFFFFF", palette[number]))
    return pairs


# ----------------------------------------------------------------------
# Links
# ----------------------------------------------------------------------
def _hrefs(page):
    return [a.get("href", "").strip() for a in page.find("//a[@href]")]


def _internal_known(env, website, known_urls, path):
    """:return: whether the internal ``path`` leads somewhere"""
    path = path.split("#")[0].split("?")[0] or "/"
    if path in known_urls or path.rstrip("/") in known_urls or path == "/":
        return True
    if env["website.rewrite"].search_count([("url_from", "=", path), ("website_id", "in", [False, website.id])]):
        return True
    try:
        rule, _args = env["ir.http"].routing_map().bind("localhost").match(path, method="GET", return_rule=True)
    except Exception as error:  # noqa: BLE001
        return type(error).__name__ != "NotFound"
    # The catch-all of the website serves a page: it has to exist
    return not str(rule.rule).startswith("/<path:")


def _safe_target(url):
    """:return: whether the address may be visited: a public one, never the server's own network"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        for info in socket.getaddrinfo(parsed.hostname, None):
            address = ipaddress.ip_address(info[4][0])
            if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
                return False
    except (OSError, ValueError):
        return False
    return True


def head_status(url, allowed=None):
    """:return: the HTTP status of a HEAD request (5 seconds), or 0 when nothing answered or the
    address, or any address it redirects to, is not a public one. Redirects are followed here,
    three at most, each one checked like the first: a public page cannot send the server to its
    own network, nor to a host the company did not allow (``allowed``)."""
    import requests  # noqa: PLC0415
    for _hop in range(MAX_REDIRECTS + 1):
        if not _safe_target(url) or (allowed and not allowed(url)):
            return 0
        try:
            response = requests.head(url, timeout=EXTERNAL_TIMEOUT, allow_redirects=False,
                                     headers={"User-Agent": "Mozilla/5.0 (Nudge site check)"})
        except requests.RequestException:
            return 0
        location = response.headers.get("Location")
        if response.status_code in (301, 302, 303, 307, 308) and location:
            url = urljoin(url, location)
            continue
        return response.status_code
    return 0


# ----------------------------------------------------------------------
# The checks
# ----------------------------------------------------------------------
def check_seo(env, website, pages, **_options):
    out = []
    missing_title, bad_title, missing_desc, bad_desc, no_h1, many_h1, no_alt = ([] for _ in range(7))
    titles = {}
    for page in pages:
        if page.tree is None:
            continue
        title = page_title(page, website)
        record = page.record
        if not (record.website_meta_title or "").strip() and not (record.name or "").strip():
            missing_title.append(page)
        elif title and not (TITLE_MIN <= len(title) <= TITLE_MAX):
            bad_title.append(page)
        if title:
            titles.setdefault(title.lower(), []).append(page)
        description = (record.website_meta_description or "").strip()
        if not description:
            missing_desc.append(page)
        elif not (DESCRIPTION_MIN <= len(description) <= DESCRIPTION_MAX):
            bad_desc.append(page)
        h1 = len(page.find("//h1"))
        if h1 == 0:
            no_h1.append(page)
        elif h1 > 1:
            many_h1.append(page)
        if any(not (img.get("alt") or "").strip() for img in page.find("//img")):
            no_alt.append(page)
    duplicates = [page for pages_ in titles.values() if len(pages_) > 1 for page in pages_]

    def add(key, severity, items, fix=False):
        if items:
            out.append(finding(key, "seo", severity, len(items), "website.page", [p.id for p in items],
                               [p.url for p in items], fix))
    add("seo_title_missing", CRITICAL, missing_title)
    add("seo_title_length", INFO, bad_title)
    add("seo_description_missing", WARNING, missing_desc, "meta_description")
    add("seo_description_length", INFO, bad_desc)
    add("seo_h1_missing", WARNING, no_h1)
    add("seo_h1_many", WARNING, many_h1)
    add("seo_alt_missing", WARNING, no_alt, "alt_text")
    add("seo_title_duplicate", WARNING, duplicates)
    return out


def check_links(env, website, pages, external=False, fetch=None, **_options):
    out = []
    known = {page.url for page in pages}
    known |= {menu.url for menu in env["website.menu"].search([("website_id", "in", [False, website.id])]) if menu.url}
    broken, outside = {}, {}
    for page in pages:
        for href in _hrefs(page):
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "sms:", "whatsapp:")):
                continue
            if href.startswith("//") or re.match(r"https?://", href):
                outside.setdefault(href, []).append(page)
                continue
            if not href.startswith("/") or href.startswith(INTERNAL_SKIP):
                continue
            if not _internal_known(env, website, known, href):
                broken.setdefault(href, []).append(page)
    if broken:
        pages_ = {p.id: p for lst in broken.values() for p in lst}
        out.append(finding("links_broken", "links", WARNING, len(broken), "website.page", list(pages_),
                           [p.url for p in pages_.values()], extra={"links": sorted(broken)[:MAX_IDS]}))
    if external and outside:
        fetch = fetch or head_status
        # NP-15: an address the company did not allow is not visited
        entries = env["nudge.egress"]._entries()

        def allowed(url):
            return not entries or egress.is_allowed(entries, url)

        bad, checked, started = {}, 0, time.monotonic()
        for href in sorted(outside):
            if checked >= MAX_EXTERNAL or time.monotonic() - started > EXTERNAL_BUDGET:
                break
            checked += 1
            full = "https:" + href if href.startswith("//") else href
            if fetch is head_status and not (_safe_target(full) and allowed(full)):
                continue
            status = head_status(full, allowed) if fetch is head_status else fetch(full)
            if status == 0 or status >= 400 and status not in (401, 403, 405, 429, 999):
                bad[href] = status
        if bad:
            pages_ = {p.id: p for href in bad for p in outside[href]}
            out.append(finding("links_external_broken", "links", WARNING, len(bad), "website.page", list(pages_),
                               [p.url for p in pages_.values()], extra={"links": sorted(bad)[:MAX_IDS]}))
        if len(outside) > checked:
            out.append(finding("links_external_capped", "links", INFO, len(outside) - checked))
    return out


def check_translations(env, website, pages, **_options):
    out = []
    languages = website.language_ids
    default = website.default_lang_id
    others = languages - default
    if not others:
        return out
    missing = {}
    for page in pages:
        if page.tree is None:
            continue
        view = page.record.view_id
        source = view.with_context(lang=default.code).arch_db or ""
        if not re.search(r">\s*[^<>\s]", source):
            continue
        for lang in others:
            translated = view.with_context(lang=lang.code).arch_db or ""
            if translated == source:
                missing.setdefault(lang.code, []).append(page)
    for code, items in sorted(missing.items()):
        out.append(finding("translations_missing", "translations", WARNING, len(items), "website.page",
                           [p.id for p in items], [p.url for p in items], extra={"lang": code}))
    return out


def check_performance(env, website, pages, **_options):
    out = []
    heavy, busy, inline = {}, [], []
    for page in pages:
        if page.tree is None:
            continue
        ids = {int(i) for i in ASSET_URL.findall(" ".join(
            (img.get("src") or "") + " " + (img.get("data-original-src") or "") for img in page.find("//img")))}
        for attachment in env["ir.attachment"].browse(ids).exists():
            if attachment.file_size > IMAGE_BYTES:
                heavy.setdefault(attachment.id, []).append(page)
        if len(page.find("//*[@data-snippet]")) > MAX_SNIPPETS:
            busy.append(page)
        if any(not script.get("src") for script in page.find("//script")):
            inline.append(page)
    if heavy:
        pages_ = {p.id: p for lst in heavy.values() for p in lst}
        out.append(finding("perf_images_heavy", "performance", WARNING, len(heavy), "ir.attachment", list(heavy),
                           [p.url for p in pages_.values()], extra={"pages": list(pages_)}))
    if busy:
        out.append(finding("perf_snippets_many", "performance", INFO, len(busy), "website.page",
                           [p.id for p in busy], [p.url for p in busy]))
    if inline:
        out.append(finding("perf_inline_scripts", "performance", INFO, len(inline), "website.page",
                           [p.id for p in inline], [p.url for p in inline]))
    return out


def check_accessibility(env, website, pages, palette=None, **_options):
    out = []
    palette = palette if palette is not None else read_palette(env, website)
    weak = [name for name, fg, bg in palette_pairs(palette) if contrast(fg, bg) < MIN_CONTRAST]
    if weak:
        out.append(finding("a11y_contrast", "accessibility", WARNING, len(weak), extra={"pairs": weak}))
    unlabeled = []
    for page in pages:
        if page.tree is None:
            continue
        labelled = {label.get("for") for label in page.find("//label[@for]")}
        for field in page.find("//input|//select|//textarea"):
            if (field.get("type") or "text") in FIELD_TYPES_SKIPPED:
                continue
            if field.get("id") in labelled or field.get("aria-label") or field.get("aria-labelledby"):
                continue
            if field.xpath("ancestor::label"):
                continue
            unlabeled.append(page)
            break
    if unlabeled:
        out.append(finding("a11y_labels", "accessibility", WARNING, len(unlabeled), "website.page",
                           [p.id for p in unlabeled], [p.url for p in unlabeled]))
    return out


def _legal_page(pages, words):
    for page in pages:
        haystack = f"{page.url} {page.name}".lower()
        if any(word in haystack for word in words):
            return page
    return None


def footer_text(env, website):
    """:return: the footer of the website and its menus as text, to find the links in it"""
    views = env["ir.ui.view"].with_context(active_test=False).search([
        ("key", "like", "website.footer%"), ("website_id", "in", [False, website.id]), ("active", "=", True)])
    text = " ".join(views.mapped("arch"))
    text += " ".join(env["website.menu"].search([("website_id", "in", [False, website.id])]).mapped("url") or [])
    return text


def check_legal(env, website, pages, **_options):
    out = []
    privacy, terms = _legal_page(pages, LEGAL_PRIVACY), _legal_page(pages, LEGAL_TERMS)
    missing = [key for key, page in (("privacy", privacy), ("terms", terms)) if not page]
    if missing:
        out.append(finding("legal_missing", "legal", WARNING, len(missing), fix="legal_pages",
                           extra={"missing": missing}))
    footer = footer_text(env, website)
    unlinked = [page for page in (privacy, terms) if page and page.url not in footer]
    if unlinked:
        out.append(finding("legal_unlinked", "legal", INFO, len(unlinked), "website.page",
                           [p.id for p in unlinked], [p.url for p in unlinked]))
    return out


def shop_available(env):
    return "product.public.category" in env and "is_published" in env["product.template"]._fields


def check_shop(env, website, pages, **_options):
    out = []
    if not shop_available(env):
        return out
    Product = env["product.template"]
    base = [("is_published", "=", True), ("sale_ok", "=", True), ("website_id", "in", [False, website.id])]

    def add(key, severity, domain, fix=False):
        records = Product.search(base + domain, limit=MAX_IDS)
        count = Product.search_count(base + domain, limit=1000)
        if count:
            out.append(finding(key, "shop", severity, count, "product.template", records.ids, fix=fix,
                               extra={"domain": base + domain, "capped": count >= 1000}))
    add("shop_no_image", WARNING, [("image_1920", "=", False)])
    add("shop_no_price", CRITICAL, [("list_price", "<=", 0)])
    description = "description_ecommerce" if "description_ecommerce" in Product._fields else "website_description"
    add("shop_no_description", INFO, [(description, "=", False)], "product_description")
    Category = env["product.public.category"]
    used = set()
    for product in Product.search([("public_categ_ids", "!=", False)] + base, limit=5000):
        used.update(product.public_categ_ids.ids)
    categories = Category.search([("website_id", "in", [False, website.id])])
    # a parent holds what its children hold
    parent_used = set(used)
    for category in categories:
        if category.id in used:
            node = category.parent_id
            while node:
                parent_used.add(node.id)
                node = node.parent_id
    empty = categories.filtered(lambda c: c.id not in parent_used)
    if empty:
        out.append(finding("shop_category_empty", "shop", INFO, len(empty), "product.public.category", empty.ids))
    return out


CHECKS = (
    ("seo", check_seo), ("links", check_links), ("translations", check_translations),
    ("performance", check_performance), ("accessibility", check_accessibility), ("legal", check_legal),
    ("shop", check_shop),
)


def check_site(env, website, external=False, groups=None, palette=None, fetch=None):
    """Check ``website`` as the user of ``env``. Nothing is changed.

    :param external: also visit the external links (HEAD, 5 seconds each, 50 at most)
    :return: ``(findings, skipped)``: the findings, and ``[{"group", "reason"}]`` of the checks that
        could not run
    :raise AccessError: when the user is not a website designer"""
    check_designer(env)
    pages = pages_of(env, website)
    found, skipped = [], []
    for group, function in CHECKS:
        if groups and group not in groups:
            continue
        try:
            with env.cr.savepoint():
                found += function(env, website, pages, external=external, palette=palette, fetch=fetch)
        except AccessError:
            skipped.append({"group": group, "reason": "access"})
        except Exception as error:  # noqa: BLE001 - one check must not stop the others
            _logger.warning("Site check %s failed: %s", group, error)
            skipped.append({"group": group, "reason": "error"})
    order = {severity: index for index, severity in enumerate(SEVERITIES)}
    found.sort(key=lambda item: (order[item["severity"]], item["key"]))
    return found, skipped


# ----------------------------------------------------------------------
# What a finding says
# ----------------------------------------------------------------------
def group_labels(env):
    return {
        "seo": _("Search engines (SEO)"), "links": _("Links"), "translations": _("Translations"),
        "performance": _("Speed"), "accessibility": _("Accessibility"), "legal": _("Legal pages"),
        "shop": _("Shop"),
    }


def finding_text(env, key, count, extra=None):
    """:return: the sentence of a finding, in the language of ``env``"""
    extra = extra or {}
    texts = {
        "seo_title_missing": _("%s pages have no title.", count),
        "seo_title_length": _("%s pages have a title shorter than 10 or longer than 60 characters.", count),
        "seo_description_missing": _("%s pages have no meta description.", count),
        "seo_description_length": _("%s pages have a meta description shorter than 50 or longer than 160 characters.", count),
        "seo_h1_missing": _("%s pages have no main heading (H1).", count),
        "seo_h1_many": _("%s pages have more than one main heading (H1).", count),
        "seo_alt_missing": _("%s pages have images without a text alternative (alt).", count),
        "seo_title_duplicate": _("%s pages share their title with another page.", count),
        "links_broken": _("%s internal links lead nowhere.", count),
        "links_external_broken": _("%s external links did not answer.", count),
        "links_external_capped": _("%s external links were not visited (only 50 are).", count),
        "translations_missing": _("%(count)s pages are not translated into %(lang)s.", count=count, lang=extra.get("lang", "")),
        "perf_images_heavy": _("%s images weigh more than 500 KB.", count),
        "perf_snippets_many": _("%s pages hold more than 20 blocks.", count),
        "perf_inline_scripts": _("%s pages hold a script written inside the page.", count),
        "a11y_contrast": _("%s colour pairs of the palette are hard to read (contrast under 4.5).", count),
        "a11y_labels": _("%s pages have form fields without a label.", count),
        "legal_missing": _("The website has no %s page.", " / ".join(
            _("privacy") if m == "privacy" else _("terms") for m in extra.get("missing", []))),
        "legal_unlinked": _("%s legal pages are not linked in the footer.", count),
        "shop_no_image": _("%s published products have no image.", count),
        "shop_no_price": _("%s published products have no price.", count),
        "shop_no_description": _("%s published products have no description.", count),
        "shop_category_empty": _("%s shop categories hold no product.", count),
    }
    return texts.get(key, key)


def fix_titles(env):
    return {
        "alt_text": _("Fill the missing alt text from the image names"),
        "meta_description": _("Write the missing meta description from the first paragraph"),
        "legal_pages": _("Create the missing legal pages as unpublished drafts"),
        "product_description": _("Copy the sales description into the website description of the products"),
    }


# ----------------------------------------------------------------------
# The fixes: a plan the person reads, then the same plan applied
# ----------------------------------------------------------------------
def humanize(source, fallback=""):
    """:return: a text alternative from the name of an image file"""
    if not source or source.startswith("data:"):
        return fallback
    name = unquote(urlparse(source).path.rsplit("/", 1)[-1])
    name = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", name)
    name = re.sub(r"[-_]+", " ", name)
    name = re.sub(r"\s*\b[0-9a-f]{6,}\b|\s*\b\d{3,}\b", "", name)
    name = re.sub(r"(\s+\d+)+$", "", name).strip()
    if len(name) < 3 or name.isdigit():
        return fallback
    return name[:1].upper() + name[1:]


def _alt_for(env, img, page_name):
    src = img.get("src") or img.get("data-original-src") or ""
    match = ASSET_URL.search(src)
    if match:
        attachment = env["ir.attachment"].browse(int(match.group(1))).exists()
        label = humanize(attachment.name, "") if attachment else ""
        if label:
            return label
    return humanize(src, page_name)


def first_paragraph(page):
    """:return: the first paragraph of the page that is a real sentence, cut for a meta description"""
    for paragraph in page.find("//p"):
        text = re.sub(r"\s+", " ", " ".join(paragraph.itertext())).strip()
        if len(text) >= 40:
            if len(text) > 155:
                text = text[:155].rsplit(" ", 1)[0].rstrip(",;:- ") + "..."
            return text
    return ""


def legal_texts(env, company):
    """:return: ``{"privacy": (name, url, html), "terms": (name, url, html)}``, drafts with the company's name,
    from the templates of the builder when they are there"""
    try:
        from .legal import legal_specs  # noqa: PLC0415
        facts = {"company": company.name or "", "address": company.partner_id._display_address(without_company=True)
                 if company.partner_id else "", "email": company.email or ""}
        code = (env.lang or "en_US").split("_")[0]
        out = {}
        for spec in legal_specs(facts):
            pick = lambda value: value.get(code) or value.get("en", "")  # noqa: E731
            html = "<h1>%s</h1>" % html_escape(pick(spec["name"]))
            for block in spec["blocks"][:-1][1:]:
                content = pick(block["copy"]) if isinstance(block["copy"], dict) else {}
                if content.get("title"):
                    html += "<h2>%s</h2>" % html_escape(content["title"])
                html += "<p>%s</p>" % html_escape(content.get("text", ""))
            html += "<p><em>%s</em></p>" % html_escape(pick(spec["blocks"][-1]["copy"]).get("text", ""))
            out["privacy" if spec["key"] == "legal_privacy" else "terms"] = (pick(spec["name"]), spec["url"], html)
        if len(out) == 2:
            return out
    except Exception:  # noqa: BLE001 - the builder's templates are optional here
        _logger.info("Legal templates of the builder not usable, using the plain drafts", exc_info=True)
    privacy = (
        _("Privacy Policy"), "/privacy",
        "<h1>%s</h1><p>%s</p><p>%s</p><p>%s</p>" % (
            html_escape(_("Privacy Policy")),
            html_escape(_("%s respects your privacy. This page explains which personal data we collect on this "
                              "website, why, and how long we keep it.", company.name or "")),
            html_escape(_("We collect only what you give us (for example in a form) and what the website needs to "
                              "work. We do not sell it. You may ask to see, correct or erase your data at any time.")),
            html_escape(_("To exercise your rights, write to us: %s. This is a draft: have it reviewed before "
                              "publishing.", company.email or ""))))
    terms = (
        _("Terms and Conditions"), "/terms",
        "<h1>%s</h1><p>%s</p><p>%s</p><p>%s</p>" % (
            html_escape(_("Terms and Conditions")),
            html_escape(_("By using this website you accept these terms. The website belongs to %s.", company.name or "")),
            html_escape(_("The content is provided for information. Prices, availability and delivery times are "
                              "confirmed when the order is accepted.")),
            html_escape(_("This is a draft: have it reviewed before publishing."))))
    return {"privacy": privacy, "terms": terms}


def plan_fix(env, website, fix, pages=None):
    """:return: the changes the fix ``fix`` would make, ``[{"kind", "label", ...}]``, as the user. Nothing is
    changed. ``kind`` is ``arch`` (write the arch of a view), ``page_field`` (fill a field of a page),
    ``product_field`` (fill a field of a product) or ``new_page`` (an unpublished draft)."""
    check_designer(env)
    pages = pages if pages is not None else pages_of(env, website)
    changes = []
    if fix == "alt_text":
        for page in pages:
            if page.tree is None:
                continue
            todo = [img for img in page.find("//img") if not (img.get("alt") or "").strip()]
            if not todo:
                continue
            names = []
            for img in todo:
                alt = _alt_for(env, img, page.name)
                img.set("alt", alt)
                names.append(alt)
            changes.append({
                "kind": "arch", "view_id": page.record.view_id.id, "page_id": page.id,
                "label": _("%(page)s: alt text for %(count)s images (%(names)s)", page=page.url, count=len(todo),
                               names=", ".join(sorted(set(names))[:4])),
                "arch": etree.tostring(page.tree, encoding="unicode")})
    elif fix == "meta_description":
        for page in pages:
            if page.tree is None or (page.record.website_meta_description or "").strip():
                continue
            text = first_paragraph(page)
            if text:
                changes.append({"kind": "page_field", "page_id": page.id, "field": "website_meta_description",
                                "value": text, "label": _("%(page)s: “%(text)s”", page=page.url, text=text)})
    elif fix == "legal_pages":
        existing = {"privacy": _legal_page(pages, LEGAL_PRIVACY), "terms": _legal_page(pages, LEGAL_TERMS)}
        texts = legal_texts(env, website.company_id or env.company)
        for key, page in existing.items():
            if not page:
                name, url, html = texts[key]
                changes.append({"kind": "new_page", "name": name, "url": url, "html": html,
                                "label": _("New unpublished page %(url)s: %(name)s", url=url, name=name)})
    elif fix == "product_description" and shop_available(env):
        Product = env["product.template"]
        field = "description_ecommerce" if "description_ecommerce" in Product._fields else "website_description"
        domain = [("is_published", "=", True), ("sale_ok", "=", True), ("website_id", "in", [False, website.id]),
                  ("description_sale", "!=", False)]
        for product in Product.search(domain, limit=MAX_IDS):
            if is_html_empty(product[field]) and (product.description_sale or "").strip():
                changes.append({"kind": "product_field", "product_id": product.id, "field": field,
                                "value": "<p>%s</p>" % html_escape(product.description_sale.strip()),
                                "label": product.display_name})
    return changes


def new_page(env, website, name, url, html, published=False):
    """Create a page of the website: a view and the page on it, unpublished unless asked.

    :return: the website.page"""
    env_site = env["website.page"].with_context(website_id=website.id)
    base = url if url.startswith("/") else "/" + url
    final, number = base, 1
    while env["website.page"].with_context(active_test=False).search_count(
            [("url", "=", final), ("website_id", "in", [False, website.id])]):
        number += 1
        final = f"{base}-{number}"
    key = "website.nudge_%s_%s" % (re.sub(r"[^a-z0-9]+", "_", final.lower()).strip("_"), website.id)
    arch = ('<t t-name="%s"><t t-call="website.layout"><div id="wrap" class="oe_structure oe_empty">'
            '<section class="s_text_block pt48 pb48"><div class="container">%s</div></section></div></t></t>') % (key, html)
    view = env["ir.ui.view"].with_context(website_id=website.id).create({
        "name": name, "type": "qweb", "key": key, "arch_db": arch, "website_id": website.id, "track": True})
    return env_site.create({"url": final, "view_id": view.id, "is_published": bool(published),
                            "website_id": website.id, "name": name})


def apply_plan(env, website, changes):
    """Write the changes the person has accepted. Only fills what is empty, only adds drafts.

    :return: the number of changes made"""
    check_designer(env)
    done = 0
    for change in changes:
        kind = change["kind"]
        if kind == "arch":
            view = env["ir.ui.view"].browse(change["view_id"]).exists()
            if view:
                view.with_context(lang="en_US", website_id=website.id).write({"arch_db": change["arch"]})
                done += 1
        elif kind == "page_field":
            page = env["website.page"].browse(change["page_id"]).exists()
            if page and not (page[change["field"]] or "").strip():
                page.with_context(lang="en_US").write({change["field"]: change["value"]})
                done += 1
        elif kind == "product_field":
            product = env["product.template"].browse(change["product_id"]).exists()
            if product and is_html_empty(product[change["field"]]):
                product.write({change["field"]: change["value"]})
                done += 1
        elif kind == "new_page":
            new_page(env, website, change["name"], change["url"], change["html"])
            done += 1
    return done


def show_action(env, website, item):
    """:return: the action "Show me" runs for a finding: the page in the editor, or the records listed"""
    model, ids = item.get("model"), item.get("ids") or []
    if model == "website.page" and len(ids) == 1:
        page = env["website.page"].browse(ids[0]).exists()
        if page:
            return {"type": "ir.actions.act_url", "url": f"{page.url}?enable_editor=1", "target": "self"}
    if model and ids:
        extra = item.get("extra") or {}
        domain = extra.get("domain") if model == "product.template" and extra.get("domain") else [("id", "in", ids)]
        return {"type": "ir.actions.act_window", "res_model": model, "view_mode": "list,form",
                "views": [(False, "list"), (False, "form")], "domain": domain,
                "name": env[model]._description}
    if item.get("key", "").startswith("legal"):
        return {"type": "ir.actions.act_window", "res_model": "website.page", "view_mode": "list,form",
                "views": [(False, "list"), (False, "form")], "name": _("Pages")}
    if item.get("key") == "a11y_contrast":
        return {"type": "ir.actions.act_url", "url": "/?enable_editor=1&theme=true", "target": "self"}
    return False



# ----------------------------------------------------------------------
# The tools Nudge offers in the chat
# ----------------------------------------------------------------------
from odoo.addons.nudge.tools import base  # noqa: E402


def _website(env, arguments):
    Website = env["website"]
    website = Website.browse(arguments["website_id"]).exists() if arguments.get("website_id") else Website.search([], limit=1)
    if not website:
        raise AccessError(_("There is no website."))
    return website


@base.register
class CheckSite(base.Tool):
    name = "check_site"
    group = "nudge.group_nudge_agent"
    modes = (base.MODE_AGENT,)
    description = (
        "Check a website the way a careful reviewer would: SEO (titles, descriptions, headings, image alt text), "
        "broken links, missing translations, heavy images, accessibility, legal pages and the shop (products "
        "without image, price or description). Returns the findings with their counts and keeps the report in "
        "Nudge > Website Builder > Site Check. Changes nothing.")
    input_schema = {"type": "object", "properties": {
        "website_id": {"type": "integer", "description": "The website; the first one when omitted."},
        "external_links": {"type": "boolean", "description": "Also visit the external links (slow). Off by default."}},
        "additionalProperties": False}

    def run(self, env, arguments, screen):
        try:
            website = _website(env, arguments)
            report = env["nudge.builder.check"].run_check(website, external=bool(arguments.get("external_links")))
        except AccessError as error:
            return base.ToolResult(str(error), is_error=True)
        lines = ["%s: %s" % (f.severity, f.text) for f in report.finding_ids]
        return base.ToolResult(
            "<data source=\"site_check\">%s</data>" % ("\n".join(lines) or _("No finding: the website is in good shape.")),
            event={"type": "open", "action": report.action_open()},
            records=("nudge.builder.check", report.ids, [report.display_name]))


@base.register
class FixSite(base.Action):
    name = "fix_site"
    kind = "write"
    group = "nudge.group_nudge_agent"
    description = (
        "Apply one deterministic fix of the site check, after the person has read the list of changes: "
        "alt_text, meta_description, legal_pages (unpublished drafts) or product_description. Only fills "
        "what is empty; never deletes, never publishes.")
    input_schema = {"type": "object", "properties": {
        "fix": {"type": "string", "enum": ["alt_text", "meta_description", "legal_pages", "product_description"]},
        "website_id": {"type": "integer"}},
        "required": ["fix"], "additionalProperties": False}

    def run(self, env, arguments, screen):
        website = _website(env, arguments)
        fix = arguments["fix"]
        changes = plan_fix(env, website, fix)
        if not changes:
            return base.ToolResult("There is nothing to change for this fix.", is_error=True)
        return base.ToolResult(
            "The person is being shown the changes.",
            pending={"summary": fix_titles(env).get(fix, fix), "res_model": "website.page", "res_ids": [],
                     "record_names": website.name[:500], "values": {"website_id": website.id, "changes": changes},
                     "is_sensitive": False,
                     "preview": {"records": [], "lines": [{"label": str(i), "value": c["label"]}
                                                          for i, c in enumerate(changes[:25], start=1)]}})

    def execute(self, env, step):
        values = step.values or {}
        website = env["website"].browse(values["website_id"]).exists()
        if not website:
            return base.ToolResult("The website was deleted since.", is_error=True)
        done = apply_plan(env, website, values["changes"])
        return base.ToolResult("Done: %s changes." % done, records=("website.page", [], []),
                               card_lines=[{"label": _("Changes made"), "value": str(done)}])

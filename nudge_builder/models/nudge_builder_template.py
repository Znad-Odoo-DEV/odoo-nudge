"""The template library of the website builder: the JSON files of data/templates read into a model.

The files are read at install and on every update (the data file calls ``_load_library``), checked
against the schema, and stored one record per template. A file that fails the check is logged
and skipped, so one bad file cannot stop a module update.
"""
import base64
import json
import logging
import os
import re

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

MODULE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_DIR = os.path.join(MODULE_DIR, "data", "templates")

KINDS = ("home", "about", "services", "projects", "team", "contact", "blog", "faq", "legal")
LANGS = ("en", "ar")
# Snippets present, under the same name, on Odoo 17, 18 and 19 Community.
# s_contact_info is missing on 17 (the core falls back to s_text_block there).
SNIPPETS = frozenset((
    "s_cover", "s_title", "s_text_block", "s_text_image", "s_image_text", "s_features",
    "s_three_columns", "s_numbers", "s_company_team", "s_references", "s_call_to_action",
    "s_faq_collapse", "s_contact_info", "s_banner", "s_picture", "s_comparisons",
    "s_quotes_carousel", "s_masonry_block", "s_image_gallery", "s_process_steps",
))
SHOP_SNIPPETS = frozenset(("s_product_list", "s_product_catalog"))
PLACEHOLDERS = frozenset((
    "building", "interior", "office", "team", "workshop", "product",
    "food", "fashion", "tech", "clinic", "school", "abstract",
))
# Fonts bundled with Odoo (web/static/fonts/google); Tajawal is the only Arabic one.
BUNDLED_FONTS = frozenset(("Fira Mono", "Montserrat", "Open Sans", "Oswald", "Raleway", "Roboto", "Tajawal"))
PALETTE_KEYS = ("primary", "secondary", "accent", "light", "dark")
HEX = re.compile(r"#[0-9a-f]{6}")
KEY = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
# Copy fields whose value is not translated text (same in every language).
PLAIN_FIELDS = frozenset(("button_url", "number"))
REQUIRED_PAGES = ("home", "about", "contact", "faq", "legal_privacy", "legal_terms", "team", "projects")


def _fail(path, message):
    raise ValueError("%s: %s" % (path, message))


def _check_langs(value, path):
    if not isinstance(value, dict) or any(not isinstance(value.get(code), str) or not value[code].strip() for code in LANGS):
        _fail(path, "needs a non-empty text in %s" % " and ".join(LANGS))


def _check_copy_value(en, ar, path, field=""):
    """The Arabic copy mirrors the English one slot by slot."""
    if isinstance(en, dict):
        if not isinstance(ar, dict) or set(en) != set(ar):
            _fail(path, "the fields of 'ar' differ from 'en'")
        for name in en:
            _check_copy_value(en[name], ar[name], "%s.%s" % (path, name), name)
    elif isinstance(en, list):
        if not isinstance(ar, list) or len(en) != len(ar) or not en:
            _fail(path, "'ar' needs the same number of items as 'en'")
        for index, (item_en, item_ar) in enumerate(zip(en, ar)):
            _check_copy_value(item_en, item_ar, "%s[%d]" % (path, index), field)
    elif isinstance(en, str):
        if not en.strip() or not isinstance(ar, str) or not ar.strip():
            _fail(path, "every slot needs a text in both languages")
        if field in PLAIN_FIELDS and en != ar:
            _fail(path, "a plain field must be identical in both languages")
    else:
        _fail(path, "unexpected value")


def validate_library_template(data, filename=None):
    """Check one template dict; :raise ValueError: with the path of the first fault."""
    if not isinstance(data, dict):
        _fail("$", "must be an object")
    if data.get("schema") != 1:
        _fail("$.schema", "must be 1")
    key = data.get("key")
    if not KEY.fullmatch(str(key or "")) or (filename and key != filename):
        _fail("$.key", "must be a lower case key equal to the file name")
    _check_langs(data.get("name"), "$.name")
    _check_langs(data.get("brand"), "$.brand")
    if not data.get("sector") or not isinstance(data.get("suggest"), list) or not data["suggest"]:
        _fail("$.suggest", "needs a sector and a list of words")
    palette = data.get("palette") or {}
    for name in PALETTE_KEYS:
        if not HEX.fullmatch(str(palette.get(name, ""))):
            _fail("$.palette.%s" % name, "must be a lower case #rrggbb colour")
    for entry in ("fonts", "fonts_ar"):
        fonts = data.get(entry) or {}
        for part in ("heading", "body"):
            if fonts.get(part) not in BUNDLED_FONTS:
                _fail("$.%s.%s" % (entry, part), "must be one of the fonts bundled with Odoo")
    if data["fonts_ar"]["body"] != "Tajawal":
        _fail("$.fonts_ar", "Arabic text needs Tajawal")
    pages = data.get("pages")
    if not isinstance(pages, list) or not pages:
        _fail("$.pages", "must be a list of pages")
    is_shop = bool(data.get("shop"))
    allowed = SNIPPETS | (SHOP_SNIPPETS if is_shop else frozenset())
    keys = []
    for index, page in enumerate(pages):
        path = "$.pages[%d]" % index
        if not KEY.fullmatch(str(page.get("key", ""))):
            _fail(path + ".key", "must be a lower case key")
        keys.append(page["key"])
        if page.get("kind") not in KINDS:
            _fail(path + ".kind", "must be one of %s" % ", ".join(KINDS))
        _check_langs(page.get("name"), path + ".name")
        seo = page.get("seo") or {}
        _check_langs(seo.get("title"), path + ".seo.title")
        _check_langs(seo.get("description"), path + ".seo.description")
        if len(seo["title"]["en"]) > 70 or len(seo["description"]["en"]) > 170:
            _fail(path + ".seo", "title up to 70 and description up to 170 characters")
        blocks = page.get("blocks")
        if not isinstance(blocks, list) or not blocks:
            _fail(path + ".blocks", "must be a list of blocks")
        for position, block in enumerate(blocks):
            bpath = "%s.blocks[%d]" % (path, position)
            if block.get("snippet") not in allowed:
                _fail(bpath + ".snippet", "%r is not a known snippet" % block.get("snippet"))
            if block.get("image") and block["image"] not in PLACEHOLDERS:
                _fail(bpath + ".image", "must be one of the placeholder keys")
            copy = block.get("copy") or {}
            if set(copy) != set(LANGS):
                _fail(bpath + ".copy", "needs exactly the languages %s" % ", ".join(LANGS))
            if copy["en"]:
                _check_copy_value(copy["en"], copy["ar"], bpath + ".copy")
            elif copy["ar"]:
                _fail(bpath + ".copy", "'ar' has fields that 'en' lacks")
    if len(set(keys)) != len(keys):
        _fail("$.pages", "page keys must be unique")
    for need in REQUIRED_PAGES + (("products",) if is_shop else ("services",)):
        if need not in keys:
            _fail("$.pages", "the page %r is missing" % need)
    if not data.get("menu") or any(key not in keys for key in data["menu"]):
        _fail("$.menu", "must list pages of the template")
    if is_shop:
        shop = data["shop"]
        if not isinstance(shop, dict) or not shop.get("categories"):
            _fail("$.shop.categories", "needs a category structure")

        def walk(categories, path):
            for i, category in enumerate(categories):
                _check_langs(category.get("name"), "%s[%d].name" % (path, i))
                if not category.get("keywords"):
                    _fail("%s[%d].keywords" % (path, i), "needs keywords")
                walk(category.get("children") or [], "%s[%d].children" % (path, i))
        walk(shop["categories"], "$.shop.categories")
        if len(shop.get("banners") or []) != 3:
            _fail("$.shop.banners", "needs exactly 3 promo banners")
        for i, banner in enumerate(shop["banners"]):
            for part in ("title", "text", "button"):
                _check_langs(banner.get(part), "$.shop.banners[%d].%s" % (i, part))
        if len(shop.get("filters") or []) < 3:
            _fail("$.shop.filters", "needs at least 3 filters")
        for i, flt in enumerate(shop["filters"]):
            _check_langs(flt.get("name"), "$.shop.filters[%d].name" % i)
        _check_langs(shop.get("product_page", {}).get("layout"), "$.shop.product_page.layout")
        _check_langs(shop.get("cart_checkout", {}).get("notes"), "$.shop.cart_checkout.notes")
        if not (shop.get("layout") or {}).get("product_page_image_layout"):
            _fail("$.shop.layout", "needs the product page image layout")
    return data


def read_library_files(directory=None):
    """:return: list of (key, dict) for every template file of the library (generic.json is the core's)."""
    directory = directory or TEMPLATE_DIR
    found = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json") or name == "generic.json":
            continue
        with open(os.path.join(directory, name), encoding="utf-8") as handle:
            found.append((name[:-5], json.load(handle)))
    return found


class NudgeBuilderTemplate(models.Model):
    _name = "nudge.builder.template"
    _description = "Website builder template"
    _order = "shop, sequence, key"

    key = fields.Char(required=True, index=True)
    name_en = fields.Char("Name (English)", required=True)
    name_ar = fields.Char("Name (Arabic)", required=True)
    name = fields.Char(compute="_compute_name")
    sector = fields.Char(required=True)
    shop = fields.Boolean("Shop template")
    sequence = fields.Integer(default=10)
    data = fields.Json(help="The whole template as read from its file.")
    page_count = fields.Integer(compute="_compute_page_count")
    thumbnail = fields.Binary(attachment=True)
    hero_image = fields.Binary(attachment=True)

    if hasattr(models, "Constraint"):
        _key_uniq = models.Constraint("unique(key)", "A template key must be unique.")
    else:  # Odoo 17 and 18
        _sql_constraints = [("key_uniq", "unique(key)", "A template key must be unique.")]

    @api.depends("name_en", "name_ar")
    @api.depends_context("lang")
    def _compute_name(self):
        arabic = (self.env.context.get("lang") or "").startswith("ar")
        for record in self:
            record.name = record.name_ar if arabic else record.name_en

    @api.depends("data")
    def _compute_page_count(self):
        for record in self:
            record.page_count = len((record.data or {}).get("pages") or [])

    @api.model
    def _read_binary(self, relative):
        path = os.path.join(MODULE_DIR, relative or "")
        if relative and os.path.isfile(path):
            with open(path, "rb") as handle:
                return base64.b64encode(handle.read())
        return False

    @api.model
    def _load_library(self, directory=None):
        """Read the files into records (create or update by key). :return: the keys loaded."""
        loaded = []
        for position, (key, data) in enumerate(read_library_files(directory), start=1):
            try:
                validate_library_template(data, key)
            except ValueError as error:
                _logger.error("Website builder template %s skipped: %s", key, error)
                continue
            values = {
                "key": key,
                "name_en": data["name"]["en"],
                "name_ar": data["name"]["ar"],
                "sector": data["sector"],
                "shop": bool(data.get("shop")),
                "sequence": position * 10,
                "data": data,
                "thumbnail": self._read_binary(data.get("thumbnail")),
                "hero_image": self._read_binary(data.get("hero_image")),
            }
            record = self.with_context(active_test=False).search([("key", "=", key)], limit=1)
            if record:
                record.write(values)
            else:
                self.create(values)
            loaded.append(key)
        return loaded

    def action_get_template(self):
        """The template dict, ready for ``site._apply_template``."""
        self.ensure_one()
        return json.loads(json.dumps(self.data))

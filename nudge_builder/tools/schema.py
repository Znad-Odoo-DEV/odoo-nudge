"""The template JSON a template file of the library follows, and its check."""
import re

from .safe import safe_url

KINDS = ("home", "about", "services", "projects", "team", "contact", "blog", "faq", "legal")
PLACEHOLDERS = (
    "building", "interior", "office", "team", "workshop", "product",
    "food", "fashion", "tech", "clinic", "school", "abstract",
)
HEX = re.compile(r"#[0-9a-fA-F]{6}")
PALETTE_KEYS = ("primary", "secondary", "accent", "light", "dark")
KEY = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
SNIPPET = re.compile(r"^[a-z][a-z0-9_.]{1,80}$")


def _fail(path, message):
    raise ValueError(f"{path}: {message}")


def _check_palette(palette, path):
    if not isinstance(palette, dict):
        _fail(path, "must be an object")
    for key in PALETTE_KEYS:
        if not HEX.fullmatch(str(palette.get(key, ""))):
            _fail(f"{path}.{key}", "must be a #rrggbb colour")


def _check_fonts(fonts, path):
    if not isinstance(fonts, dict) or not all(
            isinstance(fonts.get(key), str) and fonts[key] for key in ("heading", "body")):
        _fail(path, "needs a heading and a body font name")


def _check_names(names, path, required=True):
    if not isinstance(names, dict) or (required and not isinstance(names.get("en"), str)):
        _fail(path, "must be an object of language code to text with an 'en' entry")
    for code, text in names.items():
        if not isinstance(text, str) or not re.match(r"^[a-z]{2,3}$", code):
            _fail(f"{path}.{code}", "language codes are 2 letters and the text a string")


def _check_copy(copy, path):
    if not isinstance(copy, dict) or not isinstance(copy.get("en"), dict):
        _fail(path, "must be an object of language code to fields with an 'en' entry")
    for code, fields in copy.items():
        if not re.match(r"^[a-z]{2,3}$", code) or not isinstance(fields, dict):
            _fail(f"{path}.{code}", "language codes are 2 letters and the fields an object")
        for key in ("title", "text", "button", "button_url"):
            if key in fields and not isinstance(fields[key], str):
                _fail(f"{path}.{code}.{key}", "must be a string")
        if fields.get("button_url") and not safe_url(fields["button_url"]):
            _fail(f"{path}.{code}.button_url", "must be /path, https://, mailto:, tel: or #anchor")
        for number, item in enumerate(fields.get("items") or []):
            if isinstance(item, dict) and item.get("image") and not safe_url(item["image"]):
                _fail(f"{path}.{code}.items[{number}].image", "must be /path or https://")
        if "items" in fields and not (isinstance(fields["items"], list) and all(isinstance(i, dict) for i in fields["items"])):
            _fail(f"{path}.{code}.items", "must be a list of objects")


def validate_template(template):
    """:raise ValueError: with the path of the first fault"""
    if not isinstance(template, dict):
        _fail("$", "must be an object")
    if template.get("schema") != 1:
        _fail("$.schema", "must be 1")
    if not KEY.fullmatch(str(template.get("key", ""))):
        _fail("$.key", "must be a lower case key")
    _check_names(template.get("name"), "$.name")
    _check_palette(template.get("palette"), "$.palette")
    _check_fonts(template.get("fonts"), "$.fonts")
    for index, direction in enumerate(template.get("directions") or []):
        path = f"$.directions[{index}]"
        _check_names(direction.get("name"), f"{path}.name")
        _check_palette(direction.get("palette"), f"{path}.palette")
        _check_fonts(direction.get("fonts"), f"{path}.fonts")
    if template.get("directions") and len(template["directions"]) != 3:
        _fail("$.directions", "must hold 3 directions or none")
    pages = template.get("pages")
    if not isinstance(pages, list) or not pages:
        _fail("$.pages", "must be a list of pages")
    keys = []
    for index, page in enumerate(pages):
        path = f"$.pages[{index}]"
        if not KEY.fullmatch(str(page.get("key", ""))):
            _fail(f"{path}.key", "must be a lower case key")
        keys.append(page["key"])
        if page.get("kind") not in KINDS:
            _fail(f"{path}.kind", f"must be one of {', '.join(KINDS)}")
        _check_names(page.get("name"), f"{path}.name")
        blocks = page.get("blocks")
        if not isinstance(blocks, list) or not blocks:
            _fail(f"{path}.blocks", "must be a list of blocks")
        for position, block in enumerate(blocks):
            bpath = f"{path}.blocks[{position}]"
            if not SNIPPET.fullmatch(str(block.get("snippet", ""))):
                _fail(f"{bpath}.snippet", "must be the name of an Odoo snippet template, without 'website.'")
            _check_copy(block.get("copy"), f"{bpath}.copy")
            if block.get("image") and block["image"] not in PLACEHOLDERS:
                _fail(f"{bpath}.image", f"must be one of {', '.join(PLACEHOLDERS)}")
    if len(set(keys)) != len(keys):
        _fail("$.pages", "page keys must be unique")
    for key in template.get("menu") or []:
        if key not in keys:
            _fail("$.menu", f"{key} is not a page of the template")
    return template

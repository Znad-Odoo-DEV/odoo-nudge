"""Products by what people call them, in any trade.

"Bolt 8 mm stainless", "red t-shirt XL", "laptop 16 GB", "برغي 8 ملم ستانلس":
the words are looked for in the names, codes, barcodes, descriptions, the
values of the variants (size, colour), the categories and the names the
suppliers give a product; the sizes are read with their units, in any of
them (25.4 mm is 1 inch), and a size that differs pushes a product down; a
word misspelt still finds what it meant, by its letters. No vocabulary of a
trade is built in: what a catalogue holds is what is found.

Everything is read as the person: what they cannot open is not found, a
price or a quantity they may not read is not shown. Each result says why it
came: the code, the size, the words, the letters.
"""
from odoo import _
import difflib
import re

from odoo.exceptions import AccessError
from odoo.tools import SQL

from ..profile.retrieval import _fold
from . import base
from .records import has_field_access  # noqa: E402
from .records import Refused, get_model

MAX_RESULTS = 8
MAX_POOL = 300
PER_WORD = 60
MAX_QUERY = 200
MIN_SCORE = 10
TRIGRAM = 0.45
WORD = re.compile(r"[^\W_]+(?:[.,/][^\W_]+)*", re.UNICODE)

# Words that carry no product, in Arabic and English
STOPWORDS = frozenset("""
the a an of for with and or to in on at is are we have do does any some please show find need want me my our
looking search product products item items similar like same
بدي بدنا عندنا عنا في فيه من على عن مع او و ال شو ايش هل لو ممكن جيبلي لاقيلي دورلي شوفلي منتج منتجات صنف اصناف
شي شبيه يشبه مثل زي متل نفس
""".split())

# Units by family, to a base unit of the family
UNITS = {
    "length": {"mm": 1, "ملم": 1, "مم": 1, "مللي": 1, "cm": 10, "سم": 10, "m": 1000, "متر": 1000, "م": 1000,
               "km": 1_000_000, "in": 25.4, "inch": 25.4, "inches": 25.4, '"': 25.4, "انش": 25.4, "إنش": 25.4,
               "بوصه": 25.4, "بوصة": 25.4, "ft": 304.8, "feet": 304.8, "قدم": 304.8},
    "weight": {"mg": 0.001, "g": 1, "gr": 1, "gram": 1, "grams": 1, "غ": 1, "غرام": 1, "جرام": 1, "kg": 1000,
               "كغ": 1000, "كيلو": 1000, "كجم": 1000, "t": 1_000_000, "ton": 1_000_000, "طن": 1_000_000,
               "lb": 453.6, "lbs": 453.6, "oz": 28.35},
    "volume": {"ml": 1, "مل": 1, "cl": 10, "l": 1000, "lt": 1000, "ltr": 1000, "liter": 1000, "litre": 1000,
               "لتر": 1000, "ل": 1000, "gal": 3785.4, "gallon": 3785.4, "جالون": 3785.4},
    "power": {"w": 1, "واط": 1, "kw": 1000, "كيلوواط": 1000, "hp": 745.7, "حصان": 745.7},
    "voltage": {"v": 1, "فولت": 1, "kv": 1000},
    "current": {"a": 1, "امبير": 1, "أمبير": 1, "ma": 0.001, "mah": 0.001},
    "storage": {"mb": 1, "gb": 1024, "جيجا": 1024, "tb": 1024 * 1024, "تيرا": 1024 * 1024},
    "pressure": {"bar": 1, "بار": 1, "psi": 0.0689},
}
UNIT_FAMILY = {unit: family for family, units in UNITS.items() for unit in units}
# Longest first, so that "mm" is read before "m" and "kg" before "g"
UNIT_PATTERN = "|".join(sorted((re.escape(unit) for unit in UNIT_FAMILY), key=len, reverse=True))
NUMBER = r"\d+(?:[.,]\d+)?(?:\s*/\s*\d+)?"
SPEC = re.compile(rf"(?<![\w.])({NUMBER})\s*({UNIT_PATTERN})(?![^\W\d_])", re.IGNORECASE | re.UNICODE)
DIMENSIONS = re.compile(rf"(?<![\w.])({NUMBER})\s*[x×*]\s*({NUMBER})(?:\s*[x×*]\s*({NUMBER}))?", re.IGNORECASE)
# Sizes of clothes
SIZES = ("xxs", "xs", "s", "m", "l", "xl", "xxl", "xxxl", "2xl", "3xl", "4xl")
SIZE_WORD = re.compile(r"(?<![\w])(xxs|xs|xxxl|xxl|xl|2xl|3xl|4xl|s|m|l)(?![\w])", re.IGNORECASE)


def _without_measures(text):
    """:return: ``text`` with its measures taken out, which are read apart"""
    return DIMENSIONS.sub(" ", SPEC.sub(" ", text))


def _number(text):
    text = text.replace(",", ".").replace(" ", "")
    if "/" in text:
        whole, _sep, fraction = text.partition("/")
        try:
            return float(whole) / float(fraction)
        except (ValueError, ZeroDivisionError):
            return None
    try:
        return float(text)
    except ValueError:
        return None


def specs_of(text):
    """:return: the measures written in ``text``: ``{family: [values in the
    base unit]}``, sizes of clothes under "size", dimensions under "dimensions\""""
    found = {}
    lowered = (text or "").lower()
    for number, unit in SPEC.findall(lowered):
        value = _number(number)
        family = UNIT_FAMILY.get(unit.lower())
        if value is not None and family:
            found.setdefault(family, []).append(round(value * UNITS[family][unit.lower()], 4))
    for match in DIMENSIONS.findall(lowered):
        values = tuple(sorted(_number(part) for part in match if part and _number(part) is not None))
        if len(values) >= 2:
            found.setdefault("dimensions", []).append(values)
    # A letter that ends a measure is its unit, not a size: "1.5 L"
    for size in SIZE_WORD.findall(_without_measures(text or "")):
        # One letter is a size only when it stands as one: "M" in "Shirt M", not "m" of metres
        if len(size) > 1 or size.isupper():
            found.setdefault("size", []).append(size.lower().replace("2xl", "xxl").replace("3xl", "xxxl"))
    return found


def words_of(text):
    """:return: the words that carry the product, folded"""
    words = []
    for word in WORD.findall(_without_measures(text or "").lower()):
        word = _fold(word)
        if len(word) < 2 or word in STOPWORDS or word in UNIT_FAMILY or word in SIZES:
            continue
        # A size is read as a size; a short number alone says nothing, a long
        # one may be a model (iPhone 15 is two words, 2024 one)
        if word.replace(".", "").replace(",", "").isdigit() and len(word) < 3:
            continue
        if SPEC.fullmatch(word):
            continue
        words.append(word)
    return words


def _same(a, b):
    return abs(a - b) <= max(abs(a), abs(b)) * 0.01 + 1e-9


def compare_specs(asked, has):
    """:return: ``(matches, conflicts)``: lists of ``(family, asked value)``"""
    matches, conflicts = [], []
    for family, values in asked.items():
        if family not in has:
            continue
        for value in values:
            if any(value == other if family in ("size", "dimensions") else _same(value, other)
                   for other in has[family]):
                matches.append((family, value))
            else:
                conflicts.append((family, value))
    return matches, conflicts


def _shown(family, value):
    if family == "size":
        return value.upper()
    if family == "dimensions":
        return "×".join(f"{part:g}" for part in value)
    base = {"length": "mm", "weight": "g", "volume": "ml", "power": "W", "voltage": "V", "current": "A",
            "storage": "MB", "pressure": "bar"}[family]
    if family == "length" and value >= 1000:
        return f"{value / 1000:g} m"
    if family == "weight" and value >= 1000:
        return f"{value / 1000:g} kg"
    if family == "volume" and value >= 1000:
        return f"{value / 1000:g} L"
    if family == "storage" and value >= 1024:
        return f"{value / 1024:g} GB"
    return f"{value:g} {base}"


# ---------------------------------------------------------------- candidates

def _readable(record, name):
    field = record._fields.get(name)
    return field is not None and has_field_access(record, field, "read")


def _variant_value_field(Product):
    return "product_template_variant_value_ids" if "product_template_variant_value_ids" in Product._fields else None


def _pool(env, Product, text, words):
    found = Product.browse()
    # A code or a barcode, as it is written
    for token in text.split():
        if len(token) >= 3:
            found |= Product.search(["|", ("default_code", "=ilike", token), ("barcode", "=", token)], limit=5)
    # A translated field is searched in the language of the person: the
    # names in the other languages of the database are searched too
    languages = _languages(env)
    readers = [Product.with_context(lang=code) for code in languages] or [Product]
    for reader in readers:
        found |= Product.browse([id_ for id_, _name in reader.name_search(text, limit=40)])
    variant_values = _variant_value_field(Product)
    for word in words[:8]:
        # A word misspelt at its end still finds its stem
        stems = {word} | ({word[:-1]} if len(word) >= 5 else set())
        for stem in stems:
            domain = ["|", "|", "|", ("name", "ilike", stem), ("default_code", "ilike", stem),
                      ("description_sale", "ilike", stem), ("categ_id.complete_name", "ilike", stem)]
            if variant_values:
                domain = ["|", (f"{variant_values}.name", "ilike", stem)] + domain
            for reader in readers:
                found |= reader.search(domain, limit=PER_WORD)
        if len(found) >= MAX_POOL:
            break
    found |= _by_letters(env, Product, text)
    found |= _by_supplier(env, text, words)
    return found[:MAX_POOL]


def _by_letters(env, Product, text):
    """Names that share most of their letters with what was asked: a word
    misspelt anywhere. With PostgreSQL's trigrams when they are there."""
    env.cr.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
    if not env.cr.fetchone():
        return Product.browse()
    lang = env.lang or "en_US"
    env.cr.execute(SQL(
        """SELECT id FROM product_template
            WHERE active AND GREATEST(word_similarity(%(text)s, name->>'en_US'),
                                      word_similarity(%(text)s, COALESCE(name->>%(lang)s, ''))) > %(limit)s
         ORDER BY GREATEST(word_similarity(%(text)s, name->>'en_US'),
                           word_similarity(%(text)s, COALESCE(name->>%(lang)s, ''))) DESC
            LIMIT %(count)s""",
        text=text, lang=lang, limit=TRIGRAM, count=PER_WORD))
    ids = [row[0] for row in env.cr.fetchall()]
    # Through the ORM again: what this person may not see stays out
    return Product.search([("product_tmpl_id", "in", ids)], limit=PER_WORD) if ids else Product.browse()


def _by_supplier(env, text, words):
    if "product.supplierinfo" not in env:
        return env["product.product"].browse()
    try:
        Supplier = get_model(env, "product.supplierinfo")
    except Refused:
        return env["product.product"].browse()
    found = env["product.product"].browse()
    try:
        for asked in [text] + words[:4]:
            for seller in Supplier.search(["|", ("product_name", "ilike", asked), ("product_code", "ilike", asked)],
                                          limit=20):
                found |= seller.product_id or seller.product_tmpl_id.product_variant_ids
    except AccessError:
        pass
    return found


# ---------------------------------------------------------------- scoring

def _languages(env):
    """:return: the codes of the languages of this database: a product is
    named in each, and a person asks in any"""
    codes = env["res.lang"].sudo().get_installed()
    return [code for code, _name in codes] or ["en_US"]


def _names_of(product):
    """:return: what a product is called: its names in every language, its
    code and barcode, the names and codes its suppliers give it"""
    names = [product.display_name, product.default_code or "", product.barcode or ""]
    template = product.product_tmpl_id
    for code in _languages(product.env):
        names.append(template.with_context(lang=code).name or "")
    field = _variant_value_field(product)
    if field:
        names.extend(product[field].mapped("name"))
    try:
        names.extend(seller.product_name or "" for seller in product.seller_ids[:5])
        names.extend(seller.product_code or "" for seller in product.seller_ids[:5])
    except AccessError:
        pass
    return [name for name in names if name]


def _more_of(product):
    """:return: what else is written of a product: its descriptions, its category"""
    parts = []
    for name in ("description_sale", "description"):
        if _readable(product, name) and product[name]:
            parts.append(str(product[name]))
    if product.categ_id:
        parts.append(product.categ_id.complete_name or "")
    return " ".join(parts)


def score(env, product, text, words, asked_specs):
    """:return: ``(score, reasons)``; reasons are in the person's language"""
    reasons = []
    total = 0.0
    lowered = text.strip().lower()
    code = (product.default_code or "").lower()
    if code and code in {token.lower() for token in text.split()}:
        total += 60
        reasons.append(_("Same code: %s", product.default_code))
    elif product.barcode and product.barcode in text.split():
        total += 60
        reasons.append(_("Same barcode"))
    names = _names_of(product)
    more = _more_of(product)
    name_words = set(words_of(" ".join(names)))
    more_words = set(words_of(more)) - name_words
    hits, mentioned, close = [], [], []
    for word in words:
        if word in name_words:
            # In what the product is called: what a person means by it
            hits.append(word)
            total += 14
        elif word in more_words:
            # In a description or a category: a hint, not a name
            mentioned.append(word)
            total += 5
        elif len(word) >= 6 and any(other.startswith(word[:5]) for other in name_words):
            # The same stem, a different ending: "screens" for "screen", not "screw"
            close.append(word)
            total += 7
        elif len(word) >= 4 and difflib.get_close_matches(word, name_words, n=1, cutoff=0.8):
            close.append(word)
            total += 6
    if hits:
        reasons.append(_("Words: %s", ", ".join(hits)))
        # The thing itself comes first in a name ("chair, office" or "office chair"):
        # a name that starts with the first word asked is more likely the thing
        if any(words_of(name)[:1] == words[:1] for name in names):
            total += 4
    if close:
        reasons.append(_("Close to: %s", ", ".join(close)))
    if mentioned:
        reasons.append(_("Mentioned: %s", ", ".join(mentioned)))
    if asked_specs:
        matches, conflicts = compare_specs(asked_specs, specs_of(" ".join(names) + " " + more))
        for family, value in matches:
            total += 25
            reasons.append(_("Same size: %s", _shown(family, value)))
        total -= 30 * len(conflicts)
    likeness = max(difflib.SequenceMatcher(None, lowered, name.lower()).ratio() for name in names[:1] + names[3:])
    # Most of the letters in order: "laptp" for "laptop"; "screw" is not "screen"
    if likeness >= 0.75:
        total += 15 * likeness
        if not hits:
            reasons.append(_("Name close to what was asked"))
    return total, reasons


def _money(env, amount, currency):
    try:
        return currency.format(amount) if currency else f"{amount:.2f}"
    except Exception:  # noqa: BLE001
        return f"{amount:.2f}"


def describe(env, product, warehouses):
    """:return: what a card shows of ``product``, as the person may read it"""
    item = {
        "id": product.id,
        "name": product.display_name,
        "code": product.default_code or "",
        "category": product.categ_id.complete_name if product.categ_id else "",
        "image": f"/web/image/product.product/{product.id}/image_128",
    }
    currency = product.currency_id if "currency_id" in product._fields else env.company.currency_id
    if _readable(product, "lst_price"):
        item["price"] = _money(env, product.lst_price, currency)
    if _readable(product, "standard_price"):
        item["cost"] = _money(env, product.standard_price, currency)
    if "qty_available" in product._fields and product.type not in ("service", "combo") and _readable(product, "qty_available"):
        try:
            item["on_hand"] = product.qty_available
            item["unit"] = product.uom_id.name if "uom_id" in product._fields else ""
            item["by_warehouse"] = [
                {"name": warehouse.name, "quantity": quantity}
                for warehouse in warehouses
                for quantity in [product.with_context(warehouse_id=warehouse.id).qty_available]
                if quantity
            ]
        except AccessError:
            item.pop("on_hand", None)
    return item


def find(env, text, limit=MAX_RESULTS):
    """:return: ``{"query", "items"}``: the products most like ``text``, the
    best first, each with why it came"""
    text = " ".join(str(text or "").split())[:MAX_QUERY]
    found = {"query": text, "items": []}
    if len(text) < 2 or "product.product" not in env:
        return found
    try:
        Product = get_model(env, "product.product")
    except Refused:
        return found
    words = words_of(text)
    asked_specs = specs_of(text)
    scored = []
    for product in _pool(env, Product, text, words):
        value, reasons = score(env, product, text, words, asked_specs)
        if value >= MIN_SCORE and reasons:
            scored.append((value, product, reasons))
    scored.sort(key=lambda item: (-item[0], item[1].display_name))
    # Without the Inventory app there is no warehouse, and no stock to tell
    warehouses = []
    if "stock.warehouse" in env:
        try:
            warehouses = get_model(env, "stock.warehouse").search([], limit=10)
        except Refused:
            pass
    for value, product, reasons in scored[:limit]:
        item = describe(env, product, warehouses)
        item.update(score=round(value, 1), reasons=reasons)
        found["items"].append(item)
    return found


@base.register
class SearchProducts(base.Tool):
    name = "search_products"
    terminal = True
    summary = "Looked for products"
    description = (
        "Find the products of this database most like a name or a description, in any trade: "
        "\"bolt 8 mm stainless\", \"red t-shirt XL\", \"laptop 16 GB\". Sizes are read with their "
        "units and compared in any unit; a word misspelt still finds what it meant; names, codes, "
        "barcodes, descriptions, variants, categories and supplier names are searched. The person "
        "is shown cards: the picture, the code, the price, the cost and the stock where they may "
        "see them. Use it for any question of what products there are, their stock or their price, "
        "and for a message that starts with /product. With a picture, describe what it shows in "
        "words: the kind of thing, brand, model, size with its unit, colour, material, any text "
        "printed on it."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["words"],
        "properties": {
            "words": {"type": "string", "minLength": 2, "maxLength": MAX_QUERY},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_RESULTS},
        },
    }

    def run(self, env, arguments, screen):
        found = find(env, arguments["words"], arguments.get("limit") or MAX_RESULTS)
        if not found["items"]:
            return base.ToolResult(
                f"No product looks like {found['query']!r}. Say so, and ask for another word, a code "
                "or a size.",
                event={"type": "products", "query": found["query"], "items": []})
        lines = [f"{len(found['items'])} product(s) shown as cards to the person:"]
        for item in found["items"]:
            stock = f", on hand {item['on_hand']:g} {item.get('unit', '')}" if "on_hand" in item else ""
            price = f", price {item['price']}" if "price" in item else ""
            lines.append(f"- {item['name']}{price}{stock} ({'; '.join(item['reasons'])})")
        return base.ToolResult(
            "\n".join(lines),
            event={"type": "products", "query": found["query"], "items": found["items"]},
            records=("product.product", [item["id"] for item in found["items"]],
                     [item["name"] for item in found["items"]]),
        )

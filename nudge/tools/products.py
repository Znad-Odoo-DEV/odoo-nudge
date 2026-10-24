"""Products by the names a document gives them, and what is in stock.

A delivery note or a quotation names products its own way: "Steel pipe 2in",
"coper wire 2.5". Each name is matched against the products the person may
see, by name, internal reference, barcode and the names suppliers give them,
and every likely one is returned with its match and its quantities, per
warehouse, as the person sees them. The person chooses; nothing is picked
for them unless the match is exact.
"""
import difflib
import re

from odoo.exceptions import AccessError

from . import base
from .records import Refused, cut, get_model, refusing

MAX_NAMES = 20
MAX_OPTIONS = 5
PER_LOOKUP = 12
MAX_WAREHOUSES = 15
MIN_MATCH = 0.3
WORD = re.compile(r"[\w.]+", re.UNICODE)


def _words(text):
    return [word for word in WORD.findall((text or "").lower()) if len(word) >= 2]


def _ratio(asked, candidate):
    """:return: how much ``candidate`` looks like ``asked``, 0 to 1: the
    letters in order, and the words shared"""
    a, b = asked.lower().strip(), (candidate or "").lower().strip()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    letters = difflib.SequenceMatcher(None, a, b).ratio()
    words_a, words_b = set(_words(a)), set(_words(b))
    shared = len(words_a & words_b) / len(words_a) if words_a else 0.0
    contained = 0.9 if a in b or b in a else 0.0
    return max(letters, contained, 0.5 * letters + 0.5 * shared)


def _candidates(env, Product, asked):
    """:return: the products that may be meant, by name, reference, barcode,
    or the name a supplier gives them"""
    found = Product.browse([id_ for id_, _name in Product.name_search(asked, limit=PER_LOOKUP)])
    for word in _words(asked)[:6]:
        found |= Product.search([
            "|", "|", ("name", "ilike", word), ("default_code", "ilike", word),
            ("barcode", "=", word),
        ], limit=PER_LOOKUP)
    if "product.supplierinfo" in env:
        try:
            Supplier = get_model(env, "product.supplierinfo")
            sellers = Supplier.search([
                "|", ("product_name", "ilike", asked), ("product_code", "ilike", asked)], limit=PER_LOOKUP)
            for seller in sellers:
                found |= seller.product_id or seller.product_tmpl_id.product_variant_ids
        except (Refused, AccessError):
            pass
    return found


def _names_of(product):
    names = [product.display_name, product.name, product.default_code or ""]
    try:
        names.extend(seller.product_name or "" for seller in product.seller_ids[:5])
        names.extend(seller.product_code or "" for seller in product.seller_ids[:5])
    except AccessError:
        pass
    return [name for name in names if name]


def _warehouses(env):
    if "stock.warehouse" not in env:
        return env["res.partner"].browse()  # nothing, typed as a recordset
    try:
        return get_model(env, "stock.warehouse").search([], limit=MAX_WAREHOUSES)
    except Refused:
        return env["stock.warehouse"].browse()


def _quantities(product, warehouses):
    """:return: what is in stock of ``product``, as the person sees it"""
    if "qty_available" not in product._fields:
        return {}
    if "is_storable" in product._fields and not product.is_storable:
        # Odoo 19 keeps no quantity for what is not storable: 0 on hand would read as out of stock
        return {"stock": "not tracked: this product has no stock quantity"}
    try:
        found = {
            "on_hand": product.qty_available,
            "free": product.free_qty if "free_qty" in product._fields else product.qty_available,
            "unit": product.uom_id.name if "uom_id" in product._fields else "",
        }
        if warehouses:
            found["by_warehouse"] = {
                warehouse.name: product.with_context(warehouse_id=warehouse.id).qty_available
                for warehouse in warehouses
            }
    except AccessError:
        return {}  # no right on the stock: no figures, and no error
    return found


@base.register
class FindProducts(base.Tool):
    name = "find_products"
    group = "nudge.group_nudge_agent"
    modes = (base.MODE_AGENT,)
    description = (
        "Find the products a document or the person names, as they name them, and what is in "
        "stock of each: the names need not match the catalogue, \"coper wire 2.5\" finds the "
        "copper wires. For every name, the likely products with how well they match, their "
        "reference, and the quantity on hand and free, per warehouse, as the person sees them. "
        "Give all the names at once. Show the person every likely option with its quantity: "
        "choose for them only when a match is exact."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["names"],
        "properties": {
            "names": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_NAMES,
                "items": {"type": "string", "minLength": 2, "maxLength": 120},
                "description": "The products as the document or the person names them.",
            },
        },
    }

    @refusing
    def run(self, env, arguments, screen):
        Product = get_model(env, "product.product")
        warehouses = _warehouses(env)
        stocked = "qty_available" in Product._fields
        products = []
        for asked in arguments["names"]:
            asked = asked.strip()
            scored = []
            for product in _candidates(env, Product, asked):
                match = max(_ratio(asked, name) for name in _names_of(product))
                scored.append((match, product))
            scored.sort(key=lambda item: (-item[0], item[1].id))
            kept = [item for item in scored if item[0] >= MIN_MATCH] or scored[:3]
            options = []
            for match, product in kept[:MAX_OPTIONS]:
                option = {
                    "id": product.id,
                    "name": cut(product.display_name, 120),
                    "match": round(match, 2),
                }
                if product.default_code:
                    option["reference"] = product.default_code
                option.update(_quantities(product, warehouses))
                options.append(option)
            entry = {"asked": asked, "options": options}
            if not options:
                entry["note"] = "No product looks like this one, among those the person may see."
            elif options[0]["match"] >= 0.98:
                entry["note"] = "An exact match: the first one."
            elif len(options) > 1:
                entry["note"] = "Likely ones: let the person choose."
            products.append(entry)
        result = {"products": products}
        if not stocked:
            result["note"] = "Inventory is not installed: no quantities."
        elif not warehouses:
            result["note"] = "Quantities over every location: no warehouse the person can see."
        return base.ToolResult(base.dump(result))

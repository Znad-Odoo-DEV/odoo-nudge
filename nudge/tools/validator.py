"""The input validator: warnings at entry time, in the form before it is saved.

Deterministic, no AI: each check looks at the values the person has typed and
not saved yet (the browser sends them the way the snapshot of the screen
describes a form, see ``static/src/validator``) and says what looks wrong in
a sentence. A warning never blocks anything: the person decides.

* every check runs as the person, over what their rights and the record rules
  let them read (``health.visible``), never as the superuser;
* a check is switched on or off for the company, and muted by each person;
  only the duplicates, the format of a tax number and a bill number already
  used are on from the start;
* nothing here writes. The input is a client's: its type and size are checked
  before anything is looked at.
"""
import logging
import re
import time
from datetime import date, datetime

from odoo import fields, tools as odoo_tools
from odoo.exceptions import AccessError

from . import outliers
from .health import MAX_IDS, budget, partner_key_sql, text_of, visible

_logger = logging.getLogger(__name__)

# Seconds a check may spend in the database, and all the checks of one form together
CHECK_SECONDS = 2
WHOLE_SECONDS = 1.5
# Contacts above which the name is compared exactly instead of by similarity (a setting)
NAME_ROWS_PARAM = "nudge.validator_name_rows"
NAME_ROWS = 20000
# Rows of a list of lines read from the form, and names of a warning
MAX_LINES = 60
MAX_TEXT = 500
MAX_NAMES = 3
# A far price is 15% over the median per step of the sensitivity
PRICE_STEP = outliers.PRICE_STEP
# Records read to find the usual quantity of a product
USUAL_ROWS = 5000

WARNING, INFO = "warning", "info"

DEADLINE_FIELDS = ("date_deadline", "commitment_date", "date_planned", "scheduled_date", "validity_date",
                   "invoice_date_due")
EMAIL_FIELDS = ("email", "work_email", "email_from", "partner_email")
PHONE_FIELDS = ("phone", "mobile")
BILL_TYPES = ("in_invoice", "in_refund")


# ----------------------------------------------------------------------
# What the form sends
# ----------------------------------------------------------------------
def clean(payload):
    """:return: the payload of a client reduced to what is expected of it:
    ``{"model", "id", "is_new", "changed", "fields": {name: value}, "lines": {name: [row]}}``"""
    payload = payload if isinstance(payload, dict) else {}

    def scalar(value):
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            return value[:MAX_TEXT]
        if isinstance(value, dict) and isinstance(value.get("id"), int) and not isinstance(value.get("id"), bool):
            return {"id": value["id"], "name": str(value.get("name") or "")[:MAX_TEXT]}
        return None

    values = {str(name)[:80]: scalar(value) for name, value in list((payload.get("fields") or {}).items())[:200]
              if isinstance(payload.get("fields"), dict)}
    lines = {}
    if isinstance(payload.get("lines"), dict):
        for name, rows in list(payload["lines"].items())[:10]:
            if isinstance(rows, list):
                lines[str(name)[:80]] = [
                    {str(key)[:80]: scalar(value) for key, value in list(row.items())[:20]}
                    for row in rows[:MAX_LINES] if isinstance(row, dict)]
    record_id = payload.get("id")
    return {
        "model": str(payload.get("model") or "")[:80],
        "id": record_id if isinstance(record_id, int) and not isinstance(record_id, bool) else None,
        "is_new": bool(payload.get("is_new")),
        "changed": bool(payload.get("changed")),
        "fields": values,
        "lines": lines,
    }


def text(values, name):
    value = values.get(name)
    if isinstance(value, dict):
        value = value.get("name")
    return value.strip() if isinstance(value, str) else ""


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def related_id(values, name):
    value = values.get(name)
    return value["id"] if isinstance(value, dict) else None


def warning(key, message, severity=WARNING, field=None, model=None, ids=None, figure=0.0):
    return {"key": key, "severity": severity, "message": message, "field": field, "model": model,
            "ids": list(ids or [])[:MAX_IDS], "figure": figure}


def names(records):
    return ", ".join(records[:MAX_NAMES].mapped("display_name")) + ("..." if len(records) > MAX_NAMES else "")


# ----------------------------------------------------------------------
# The checks
# ----------------------------------------------------------------------
class Check:
    """One kind of mistake. ``models`` are those it looks at ('' for any model that has
    the fields it needs); ``default`` whether it is on until the company switches it"""

    key = ""
    models = ()
    default = False

    def spec(self, env, model):
        """:return: ``({fields}, {line field: [columns]})`` this check needs from a form
        of ``model``, or ``None`` when it does not apply to it"""
        return None

    def run(self, env, payload):
        """:return: the warnings, none when all is well"""
        raise NotImplementedError

    def applies(self, env, model):
        return model in env.registry and (not self.models or model in self.models)


class PartnerDuplicate(Check):
    key = "partner_duplicate"
    models = ("res.partner",)
    default = True

    def spec(self, env, model):
        names_ = [n for n in ("name", "email", "phone", "mobile", "vat", "parent_id") if n in env[model]._fields]
        return set(names_), {}

    def run(self, env, payload):
        values, Partner = payload["fields"], env["res.partner"]
        if related_id(values, "parent_id"):
            return []  # a contact below a company shares the company's details
        sub, params = visible(Partner)
        out = []
        asked = (
            ("email", "email", text(values, "email").lower() or None,
             lambda: Partner.env._("Another contact already uses the email %(value)s: %(names)s.")),
            ("vat", "vat", re.sub(r"[\W_]+", "", text(values, "vat")).upper() or None,
             lambda: Partner.env._("Another contact already uses the tax number %(value)s: %(names)s.")),
        )
        for kind, column, value, sentence in asked:
            if value and column in Partner._fields:
                if kind == "email" and "email_normalized" in Partner._fields:
                    # The stored, indexed form of the address: no scan of the table
                    value = odoo_tools.email_normalize(value) or value
                    ids = self._ids(env, sub, params, "t.email_normalized", value, payload["id"])
                else:
                    ids = self._ids(env, sub, params, partner_key_sql(kind), value, payload["id"])
                if ids:
                    out.append(self._warning(env, kind, column, sentence(), text(values, column), ids))
        for column in PHONE_FIELDS:
            digits = re.sub(r"\D", "", text(values, column))
            if len(digits) >= 7 and column in Partner._fields:
                ids = self._ids(env, sub, params, partner_key_sql("phone", column), digits[-9:], payload["id"])
                if ids:
                    out.append(self._warning(
                        env, "phone", column,
                        env._("Another contact already uses the phone number %(value)s: %(names)s."),
                        text(values, column), ids))
        name = re.sub(r"[\W_]+", "", text(values, "name").lower())
        if len(name) >= 4:
            if self._big_table(env):
                # Too many contacts to compare every name: the same name, as the index sees it
                ids = self._ids(env, sub, params, "t.name", text(values, "name").strip(), payload["id"])
            else:
                ids = self._ids(env, sub, params, partner_key_sql("name"), name, payload["id"])
            if ids:
                out.append(self._warning(
                    env, "name", "name", env._("A contact with almost the same name already exists: %(names)s."),
                    text(values, "name"), ids))
        return out

    @staticmethod
    def _big_table(env):
        """Whether the contacts are more than the setting allows for the name similarity (the planner's count)"""
        env.cr.execute("SELECT reltuples::bigint FROM pg_class WHERE oid = 'res_partner'::regclass")
        rows = env.cr.fetchone()[0]
        try:
            limit = int(env["ir.config_parameter"].sudo().get_param(NAME_ROWS_PARAM) or NAME_ROWS)
        except ValueError:
            limit = NAME_ROWS
        return rows >= limit

    def _ids(self, env, sub, params, expression, value, record_id):
        env.cr.execute(
            f"SELECT t.id FROM res_partner t WHERE t.id IN ({sub}) AND t.parent_id IS NULL "
            f"AND ({expression}) = %s AND t.id <> %s ORDER BY t.id LIMIT %s",
            [*params, value, record_id or 0, MAX_NAMES + 1])
        return [row[0] for row in env.cr.fetchall()]

    def _warning(self, env, kind, field, sentence, shown, ids):
        found = env["res.partner"].browse(ids)
        return warning(f"{self.key}.{kind}", sentence % {"value": shown, "names": names(found)}, field=field,
                       model="res.partner", ids=ids)


class ProductDuplicate(Check):
    key = "product_duplicate"
    models = ("product.template", "product.product")
    default = True

    def spec(self, env, model):
        names_ = [n for n in ("name", "default_code", "barcode", "categ_id") if n in env[model]._fields]
        return set(names_), {}

    def run(self, env, payload):
        values, model = payload["fields"], payload["model"]
        Product, Template = env["product.product"], env["product.template"]
        sub, params = visible(Product)
        own = "t.product_tmpl_id" if model == "product.template" else "t.id"
        template_id = payload["id"] if model == "product.template" else Product.browse(payload["id"] or 0).product_tmpl_id.id
        out = []
        for column, label in (("default_code", env._("internal reference")), ("barcode", env._("barcode"))):
            value = text(values, column)
            if not value or column not in Product._fields:
                continue
            env.cr.execute(
                f"SELECT t.id FROM product_product t WHERE t.id IN ({sub}) AND btrim(coalesce(t.{column}, '')) = %s "
                f"AND {own} <> %s ORDER BY t.id LIMIT %s",
                [*params, value, payload["id"] or 0, MAX_NAMES + 1])
            ids = [row[0] for row in env.cr.fetchall()]
            if ids:
                found = Product.browse(ids)
                out.append(warning(
                    f"{self.key}.{column}",
                    env._("Another product already has the %(what)s %(value)s: %(names)s.",
                          what=label, value=value, names=names(found)),
                    field=column, model="product.product", ids=ids))
        name = text(values, "name").lower()
        if name:
            sub_t, params_t = visible(Template)
            categ = related_id(values, "categ_id")
            label = text_of(env, Template, "name")
            env.cr.execute(
                f"SELECT t.id FROM product_template t WHERE t.id IN ({sub_t}) "
                f"AND lower(btrim(coalesce({label}, ''))) = %s AND coalesce(t.categ_id, 0) = %s "
                f"AND t.id <> %s ORDER BY t.id LIMIT %s",
                [*params_t, name, categ or 0, template_id or 0, MAX_NAMES + 1])
            ids = [row[0] for row in env.cr.fetchall()]
            if ids:
                out.append(warning(
                    f"{self.key}.name",
                    env._("A product with the same name already exists in this category: %(names)s.",
                          names=names(Template.browse(ids))),
                    field="name", model="product.template", ids=ids))
        return out


VAT_RULES = {
    "AE": lambda number: bool(re.fullmatch(r"\d{15}", number)),
    "SA": lambda number: bool(re.fullmatch(r"3\d{14}", number)),
}


def vat_rule_text(env, code):
    return {
        "AE": env._("15 digits"),
        "SA": env._("15 digits, the first one a 3"),
    }.get(code) or env._("check the digits and the country prefix")


class VatFormat(Check):
    key = "vat_format"
    models = ("res.partner", "res.company")
    default = True

    def spec(self, env, model):
        return {n for n in ("vat", "country_id") if n in env[model]._fields}, {}

    def run(self, env, payload):
        values = payload["fields"]
        vat = re.sub(r"[\s.\-/]", "", text(values, "vat")).upper()
        if not vat or vat == "/":
            return []
        country = env["res.country"].browse(related_id(values, "country_id")).exists() or env.company.country_id
        prefix = vat[:2] if vat[:2].isalpha() else ""
        code = prefix or (country.code or "")
        body = vat[2:] if prefix else vat
        if not code:
            return []
        if code == "EL":
            code = "GR"
        problem = None
        if code in VAT_RULES:
            if not VAT_RULES[code](body):
                problem = vat_rule_text(env, code)
        elif self._known(env, code, body) is False:
            problem = vat_rule_text(env, code)
        if not problem:
            return []
        country_name = env["res.country"].search([("code", "=", code)], limit=1).name or code
        return [warning(
            self.key,
            env._("The tax number %(vat)s does not look like a number of %(country)s: %(rule)s.",
                  vat=text(values, "vat"), country=country_name, rule=problem),
            field="vat")]

    def _known(self, env, code, body):
        """:return: whether Odoo's own check (``base_vat``) accepts the number, ``None`` when it has no opinion"""
        Partner = env["res.partner"]
        try:
            if hasattr(Partner, "_check_vat_number"):
                return bool(Partner._check_vat_number(code, body))
            if hasattr(Partner, "simple_vat_check"):
                return bool(Partner.simple_vat_check(code.lower(), body))
        except Exception:  # noqa: BLE001 - an unknown country is no mistake of the person
            return None
        return None


def phone_invalid(env, number, region):
    """:return: whether ``number`` cannot be a phone number of ``region``; ``None`` when it
    cannot be told (no ``phone_validation`` module or no ``phonenumbers`` library)"""
    if "phone.blacklist" not in env.registry:
        return None
    try:
        import phonenumbers  # noqa: PLC0415
    except ImportError:
        return None
    try:
        parsed = phonenumbers.parse(number, region or None)
    except phonenumbers.NumberParseException:
        return True
    return not phonenumbers.is_valid_number(parsed)


class PhoneFormat(Check):
    key = "phone_format"
    models = ("res.partner",)

    def spec(self, env, model):
        return {n for n in PHONE_FIELDS + ("country_id",) if n in env[model]._fields}, {}

    def run(self, env, payload):
        values = payload["fields"]
        country = env["res.country"].browse(related_id(values, "country_id")).exists() or env.company.country_id
        out = []
        for column in PHONE_FIELDS:
            number_ = text(values, column)
            if number_ and phone_invalid(env, number_, country.code) is True:
                out.append(warning(
                    f"{self.key}.{column}",
                    env._("The phone number %(value)s is not a valid number for %(country)s.",
                          value=number_, country=country.name or env._("the country")), field=column))
        return out


class EmailSyntax(Check):
    key = "email_syntax"

    def applies(self, env, model):
        return model in env.registry and any(n in env[model]._fields for n in EMAIL_FIELDS)

    def spec(self, env, model):
        return {n for n in EMAIL_FIELDS if n in env[model]._fields}, {}

    def run(self, env, payload):
        out = []
        for column in EMAIL_FIELDS:
            value = text(payload["fields"], column)
            if not value:
                continue
            parts = [part for part in re.split(r"[;,]", value) if part.strip()]
            bad = [part.strip() for part in parts
                   if not (odoo_tools.email_normalize(part) and "." in odoo_tools.email_normalize(part).split("@")[-1])]
            if bad:
                out.append(warning(
                    f"{self.key}.{column}",
                    env._("%(value)s does not look like an email address: it needs a name, an @ and a domain "
                          "such as example.com.", value=bad[0]), field=column))
        return out


class PriceBelowCost(Check):
    key = "price_below_cost"
    models = ("sale.order",)

    def spec(self, env, model):
        return {n for n in ("currency_id",) if n in env[model]._fields}, {
            "order_line": ["product_id", "price_unit", "discount", self.uom_field(env)]}

    @staticmethod
    def uom_field(env):
        return "product_uom_id" if "product_uom_id" in env["sale.order.line"]._fields else "product_uom"

    def run(self, env, payload):
        rows = [row for row in payload["lines"].get("order_line", [])
                if isinstance(row.get("product_id"), dict) and number(row.get("price_unit")) is not None]
        if not rows:
            return []
        currency = related_id(payload["fields"], "currency_id")
        if currency and currency != env.company.currency_id.id:
            return []
        products = env["product.product"].browse({row["product_id"]["id"] for row in rows}).exists()
        try:
            costs = {product.id: product.standard_price for product in products}
            units = {product.id: product.uom_id for product in products}
        except AccessError:
            return []
        low = []
        uom_field = self.uom_field(env)
        for row in rows:
            cost = costs.get(row["product_id"]["id"])
            # the cost is per unit of the product, the price per unit of the line: a dozen is not one
            line_uom = row.get(uom_field)
            if cost and isinstance(line_uom, dict) and units.get(row["product_id"]["id"]):
                cost = units[row["product_id"]["id"]]._compute_price(cost, env["uom.uom"].browse(line_uom["id"]))
            price = row["price_unit"] * (1 - (number(row.get("discount")) or 0) / 100)
            if cost and 0 <= price < cost:
                low.append((row["product_id"]["id"], price, cost))
        if not low:
            return []
        shown = products.browse([item[0] for item in low])
        worst = max(cost / price if price else 0.0 for _id, price, cost in low)
        return [warning(
            self.key,
            env._("%(count)s lines sell under the cost of the product: %(names)s.", count=len(low),
                  names=names(shown)), figure=round(worst, 1))]


class PriceAboveLastPurchases(Check):
    key = "price_above_last_purchase"
    models = ("purchase.order",)

    def spec(self, env, model):
        return {n for n in ("currency_id",) if n in env[model]._fields}, {
            "order_line": ["product_id", "price_unit"]}

    def run(self, env, payload):
        rows = [row for row in payload["lines"].get("order_line", [])
                if isinstance(row.get("product_id"), dict) and (number(row.get("price_unit")) or 0) > 0]
        if not rows or "purchase.order.line" not in env.registry:
            return []
        Line = env["purchase.order.line"]
        sub, params = visible(Line)
        k = outliers.sensitivity(env)
        currency = related_id(payload["fields"], "currency_id") or env.company.currency_id.id
        far = []
        for product_id in {row["product_id"]["id"] for row in rows}:
            env.cr.execute(
                f"""SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY x.price_unit), count(*)
                    FROM (SELECT y.price_unit FROM purchase_order_line y
                          JOIN purchase_order yo ON yo.id = y.order_id
                          WHERE y.product_id = %s AND y.price_unit > 0 AND yo.currency_id = %s
                            AND y.order_id <> %s AND y.id IN ({sub}) ORDER BY y.id DESC LIMIT %s) x""",
                [product_id, currency, payload["id"] or 0, *params, outliers.LAST_PURCHASES])
            median, count = env.cr.fetchone()
            if not median or count < outliers.MIN_LAST_PURCHASES:
                continue
            for row in rows:
                if row["product_id"]["id"] == product_id and row["price_unit"] > median * (1 + PRICE_STEP * k):
                    far.append((product_id, row["price_unit"] / median))
        if not far:
            return []
        shown = env["product.product"].browse([item[0] for item in far])
        return [warning(
            self.key,
            env._("%(count)s lines pay far more than the last purchases of the same product: %(names)s.",
                  count=len(far), names=names(shown)), figure=round(max(item[1] for item in far), 1))]


class QuantityFarAbove(Check):
    key = "quantity_far_above"
    # model: (line field, quantity column, model of the lines, SQL condition of the lines that count)
    sources = {
        "sale.order": ("order_line", "product_uom_qty", "sale.order.line", "t.state <> 'cancel'"),
        "purchase.order": ("order_line", "product_qty", "purchase.order.line", "t.state <> 'cancel'"),
        "stock.picking": ("move_ids", "product_uom_qty", "stock.move", "t.state = 'done'"),
    }

    @property
    def models(self):
        return tuple(self.sources)

    def spec(self, env, model):
        field, column, line_model = self.sources[model][:3]
        if field not in env[model]._fields or line_model not in env.registry:
            return None
        return set(), {field: ["product_id", column]}

    def run(self, env, payload):
        field, column, line_model, where = self.sources[payload["model"]]
        rows = [row for row in payload["lines"].get(field, [])
                if isinstance(row.get("product_id"), dict) and (number(row.get(column)) or 0) > 0]
        k = outliers.sensitivity(env)
        far = []
        for row in rows:
            usual = env["nudge.validator"]._usual_quantity(
                line_model, column, where, row["product_id"]["id"], str(fields.Date.today()))
            if not usual:
                continue
            median, spread = usual
            score = outliers.score_against(row[column], median, spread, floor=1.0)
            if score is not None and score >= k:
                far.append((row["product_id"]["id"], row[column] / median if median else 0.0, median))
        if not far:
            return []
        shown = env["product.product"].browse([item[0] for item in far])
        top = max(far, key=lambda item: item[1])
        usual = f"{top[2]:g}"
        return [warning(
            self.key,
            env._("%(count)s lines have a quantity far above the usual for the product: %(names)s (usual: %(usual)s).",
                  count=len(far), names=names(shown), usual=usual), figure=round(top[1], 1))]


class DateInThePast(Check):
    key = "date_in_the_past"

    def applies(self, env, model):
        return model in env.registry and any(n in env[model]._fields for n in DEADLINE_FIELDS)

    def spec(self, env, model):
        return {n for n in DEADLINE_FIELDS if n in env[model]._fields}, {}

    def run(self, env, payload):
        if not (payload["is_new"] or payload["changed"]):
            return []  # an old date nobody touched is not a mistake of today
        today, out = fields.Date.today(), []
        Model = env[payload["model"]]
        for name in DEADLINE_FIELDS:
            value = text(payload["fields"], name)
            if not value or name not in Model._fields:
                continue
            try:
                day = datetime.fromisoformat(value[:19].replace("Z", "")).date() if len(value) > 10 else date.fromisoformat(value)
            except ValueError:
                continue
            if day < today:
                out.append(warning(
                    f"{self.key}.{name}",
                    env._("%(field)s is %(date)s, a day that has already gone by.",
                          field=Model._fields[name]._description_string(env), date=value[:10]), field=name))
        return out


class BillNumberReuse(Check):
    key = "bill_number_reuse"
    models = ("account.move",)
    default = True

    def spec(self, env, model):
        return {n for n in ("ref", "partner_id", "move_type") if n in env[model]._fields}, {}

    def run(self, env, payload):
        values = payload["fields"]
        move_type = text(values, "move_type")
        reference, partner = text(values, "ref"), related_id(values, "partner_id")
        if move_type not in BILL_TYPES or not reference or not partner:
            return []
        found = env["account.move"].search([
            ("move_type", "in", BILL_TYPES), ("partner_id", "=", partner), ("ref", "=ilike", reference),
            ("id", "!=", payload["id"] or 0), ("state", "!=", "cancel")], limit=MAX_NAMES + 1)
        if not found:
            return []
        return [warning(
            self.key,
            env._("This supplier already has a bill numbered %(ref)s: %(names)s.", ref=reference, names=names(found)),
            field="ref", model="account.move", ids=found.ids)]


def user_groups(env):
    """:return: the groups of the user and those they imply"""
    user = env.user
    groups = user.group_ids if "group_ids" in user._fields else user.groups_id
    implied = groups.all_implied_ids if "all_implied_ids" in groups._fields else groups.trans_implied_ids
    return groups | implied


class PolicyRequired(Check):
    key = "policy_required"
    default = True

    def policies(self, env, model):
        if "nudge.validator.policy" not in env.registry:
            return env["nudge.validator.policy"]
        # sudo once found: the field of a policy is read by people with no right on ir.model.fields
        return env["nudge.validator.policy"].search([("model_name", "=", model)]).sudo()

    def applies(self, env, model):
        return model in env.registry and bool(self.policies(env, model))

    def spec(self, env, model):
        return {policy.field_id.name for policy in self.policies(env, model)}, {}

    def run(self, env, payload):
        out = []
        for policy in self.policies(env, payload["model"]):
            exempt = policy.exempt_group_ids
            if exempt and env.user.group_ids & exempt if "group_ids" in env.user._fields else \
                    exempt and env.user.groups_id & exempt:
                continue
            name = policy.field_id.name
            if name not in payload["fields"]:
                continue  # the field is not on this screen
            value = payload["fields"][name]
            if value in (None, "", False) and policy.field_id.ttype != "boolean":
                message = policy.message or env._(
                    "%(field)s must be filled in.", field=policy.field_id.field_description)
                out.append(warning(f"{self.key}.{name}", message, field=name))
        return out


CHECKS = (
    PartnerDuplicate(), ProductDuplicate(), VatFormat(), PhoneFormat(), EmailSyntax(), PriceBelowCost(),
    PriceAboveLastPurchases(), QuantityFarAbove(), DateInThePast(), BillNumberReuse(), PolicyRequired(),
)
BY_KEY = {check.key: check for check in CHECKS}
DEFAULTS = {check.key: check.default for check in CHECKS}


def labels(env):
    """:return: ``{check key: its name}`` in the language of ``env``"""
    return {
        "partner_duplicate": env._("Duplicate contact"),
        "product_duplicate": env._("Duplicate product"),
        "vat_format": env._("Tax number format"),
        "phone_format": env._("Phone number format"),
        "email_syntax": env._("Email address"),
        "price_below_cost": env._("Sale price under cost"),
        "price_above_last_purchase": env._("Purchase price far above the last ones"),
        "quantity_far_above": env._("Quantity far above the usual"),
        "date_in_the_past": env._("Deadline in the past"),
        "bill_number_reuse": env._("Bill number already used"),
        "policy_required": env._("Fields the company requires"),
    }


# ----------------------------------------------------------------------
# Which checks are on, for whom
# ----------------------------------------------------------------------
def company_switches(company):
    """:return: ``{key: bool}`` the company chose, those it did not touch left out"""
    value = company.nudge_validator_switches
    return value if isinstance(value, dict) else {}


def is_on(env, key):
    chosen = company_switches(env.company).get(key)
    return DEFAULTS.get(key, False) if chosen is None else bool(chosen)


def muted_by(env):
    settings = env["res.users.settings"]._find_or_create_for_user(env.user)
    return {key for key in (settings.nudge_validator_muted or "").split(",") if key}


def active_checks(env, model):
    """:return: the checks that look at ``model`` for this person: on for the company,
    not muted by them, applicable to the model"""
    muted = muted_by(env)
    return [check for check in CHECKS
            if is_on(env, check.key) and check.key not in muted and check.applies(env, model)]


def spec(env, model):
    """:return: ``{"checks", "fields", "lines"}`` the form of ``model`` has to send"""
    names_, lines, keys = set(), {}, []
    for check in active_checks(env, model):
        needs = check.spec(env, model)
        if needs is None:
            continue
        keys.append(check.key)
        names_ |= needs[0]
        for field, columns in needs[1].items():
            lines[field] = sorted(set(lines.get(field, [])) | set(columns))
    return {"checks": keys, "fields": sorted(names_), "lines": lines}


def run(env, payload):
    """The warnings for the form described by ``payload``, as the user of ``env``.

    :return: ``[warning]``, each with the key of its check"""
    payload = clean(payload)
    model = payload["model"]
    if model not in env.registry or model not in env:
        return []
    try:
        if hasattr(env[model], "check_access"):
            env[model].check_access("read")
        else:
            env[model].check_access_rights("read")
    except AccessError:
        return []
    out = []
    started = time.monotonic()
    for check in active_checks(env, model):
        if time.monotonic() - started > WHOLE_SECONDS:
            break  # the person is typing: what was found so far is enough
        try:
            with budget(env, CHECK_SECONDS):
                found = check.run(env, payload)
        except AccessError:
            continue
        except Exception as error:  # noqa: BLE001 - one check must not stop the others
            _logger.warning("Nudge validator %s failed: %s", check.key, error)
            continue
        out.extend(found)
    return out

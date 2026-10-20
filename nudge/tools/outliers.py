"""Unusual figures: what is far from the usual, found with robust statistics.

No AI. For a group of records (the payslips of one employee, the bills of one
supplier, the moves of one product) the usual is the median and the spread
is the MAD, the median of the distances to the median, scaled by 1.4826 so it
reads like a standard deviation. A figure is unusual when it is at least
``k`` of those spreads from the median, ``k`` being 2, 3 or 4 (the
sensitivity). Neither one is moved by the very outlier being looked for, which
a mean and a standard deviation are.

The work is done by the database in one statement per rule (``percentile_cont``
over a capped number of rows), over the records the person may read: the rights
and the record rules go through the ORM sub-query of ``health.visible``. A
group with fewer than ``MIN_SAMPLE`` records is never judged. The checks here
are part of the health check: they only read, never change anything.
"""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError

from .health import CRITICAL, INFO, MAX_IDS, WARNING, Check, finding, scan, visible

# Records in a group before it is judged
MIN_SAMPLE = 8
# The sensitivities offered: the number of spreads from the median
SENSITIVITIES = ("2", "3", "4")
DEFAULT_SENSITIVITY = "3"
# MAD to standard deviation, for a normal distribution
SCALE = 1.4826
# Records read to find the usual of a rule, the newest first
HISTORY_ROWS = 20000
# Findings looked at before the count reads "1000+"
FOUND_CAP = 1000
HISTORY_DAYS = 365
REPORT_DAYS = 90
# Ratio to the usual price above which a purchase price is far above, per sensitivity step
PRICE_STEP = 0.15
LAST_PURCHASES = 5
MIN_LAST_PURCHASES = 3

FAMILIES = ("payslips", "invoices", "stock", "expenses", "sales", "purchases", "attendance")


# ----------------------------------------------------------------------
# The statistics, in pure Python, for the callers that hold a few numbers
# ----------------------------------------------------------------------
def median(values):
    """:return: the median of a list of numbers, ``None`` when it is empty"""
    ordered = sorted(values)
    count = len(ordered)
    if not count:
        return None
    middle = count // 2
    return ordered[middle] if count % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def mad(values, centre=None):
    """:return: the scaled median absolute deviation of the numbers"""
    centre = median(values) if centre is None else centre
    if centre is None:
        return None
    return SCALE * median([abs(value - centre) for value in values])


def score_against(value, centre, spread, floor=0.0, relative=0.05):
    """:return: how many spreads ``value`` is from ``centre``, signed. The spread never
    falls under ``floor`` nor ``relative`` of the median, so a group of equal numbers
    does not make a cent a scandal."""
    spread = max(spread, floor, relative * abs(centre))
    return (value - centre) / spread if spread else None


def distance(value, values, floor=0.0, relative=0.05):
    """:return: how many spreads ``value`` is from the median of ``values``, signed;
    ``None`` when there are fewer than MIN_SAMPLE"""
    if len(values) < MIN_SAMPLE:
        return None
    centre = median(values)
    return score_against(value, centre, mad(values, centre), floor, relative)


def sensitivity(env):
    """:return: the number of spreads that makes a figure unusual, as the
    company chose it (``nudge_outlier_k`` in the context wins)"""
    wanted = env.context.get("nudge_outlier_k")
    if not wanted:
        wanted = getattr(env.company, "nudge_outlier_sensitivity", None) or DEFAULT_SENSITIVITY
    try:
        wanted = int(wanted)
    except (TypeError, ValueError):
        wanted = int(DEFAULT_SENSITIVITY)
    return wanted if wanted in (2, 3, 4) else int(DEFAULT_SENSITIVITY)


def severity_of(score, k):
    """:return: the severity of a figure ``score`` spreads from the usual"""
    ratio = abs(score) / k
    return CRITICAL if ratio >= 2 else WARNING if ratio >= 1.5 else INFO


# ----------------------------------------------------------------------
# The rules
# ----------------------------------------------------------------------
class Rule:
    """One comparison. ``columns`` must be stored fields of ``model``; ``needs``
    are other models that must exist"""

    key = ""
    family = ""
    model = ""
    columns = ()
    needs = ()
    side = "high"
    floor = 0.0
    history_days = HISTORY_DAYS
    report_days = REPORT_DAYS

    def available(self, env):
        if self.model not in env.registry or any(name not in env.registry for name in self.needs):
            return False
        fields_ = env[self.model]._fields
        return all(name in fields_ and fields_[name].store for name in self.columns)

    def run(self, env, k):
        """:return: a finding or ``None``"""
        raise NotImplementedError


class Statistical(Rule):
    """A figure against the median of its group, in one statement.

    ``value``, ``group`` and ``day`` are SQL expressions on the alias ``t``;
    ``source`` may replace the base query, which must return ``id, g, v, d``"""

    value = ""
    group = ""
    day = ""
    where = "TRUE"
    min_sample = MIN_SAMPLE

    def base(self, model, sub, sub_params, since):
        table = model._table
        sql = (
            f'SELECT t.id AS id, ({self.group})::text AS g, ({self.value})::float8 AS v, ({self.day}) AS d '
            f'FROM "{table}" t WHERE t.id IN ({sub}) AND ({self.where}) AND ({self.group}) IS NOT NULL '
            f"AND ({self.value}) IS NOT NULL AND ({self.day}) >= %s ORDER BY t.id DESC LIMIT %s")
        return sql, [*sub_params, since, HISTORY_ROWS]

    def run(self, env, k):
        model = env[self.model]
        sub, sub_params = visible(model)
        today = fields.Date.today()
        since = today - timedelta(days=self.history_days)
        report = today - timedelta(days=self.report_days)
        base_sql, base_params = self.base(model, sub, sub_params, since)
        test = "abs(s.score) >= %s" if self.side == "both" else "s.score >= %s"
        sql = f"""
            WITH base AS ({base_sql}),
            stat AS (
                SELECT g, count(*) AS n, percentile_cont(0.5) WITHIN GROUP (ORDER BY v) AS med
                FROM base GROUP BY g HAVING count(*) >= %s),
            dev AS (
                SELECT b.g, percentile_cont(0.5) WITHIN GROUP (ORDER BY abs(b.v - st.med)) AS mad
                FROM base b JOIN stat st ON st.g = b.g GROUP BY b.g),
            scored AS (
                SELECT b.id, b.v, b.d, st.med,
                       (b.v - st.med) / NULLIF(GREATEST(%s * dev.mad, %s, 0.05 * abs(st.med)), 0) AS score
                FROM base b JOIN stat st ON st.g = b.g JOIN dev ON dev.g = b.g)
            SELECT s.id, s.v, s.med, s.score FROM scored s
            WHERE s.d >= %s AND {test}
            ORDER BY abs(s.score) DESC, s.id DESC LIMIT %s"""
        env.cr.execute(sql, [*base_params, self.min_sample, SCALE, self.floor, report, k, FOUND_CAP])
        rows = env.cr.fetchall()
        if not rows:
            return None
        top = rows[0]
        ratio = round(top[1] / top[2], 1) if top[2] else 0.0
        return finding(
            self.key, severity_of(top[3], k), len(rows), len(rows) >= FOUND_CAP, self.model,
            ids=[row[0] for row in rows[:MAX_IDS]], figure=ratio)


class PayslipNetOwn(Statistical):
    key, family, model = "payslip_net_own", "payslips", "hr.payslip"
    columns = ("net_wage", "date_from", "employee_id", "state")
    value, group, day = "t.net_wage", "t.employee_id", "t.date_from"
    where = "t.state <> 'cancel'"
    side = "both"
    floor = 1.0
    history_days = 183
    report_days = 62


class PayslipGrossOwn(PayslipNetOwn):
    key = "payslip_gross_own"
    columns = ("gross_wage", "date_from", "employee_id", "state")
    value = "t.gross_wage"


class PayslipNetPeers(PayslipNetOwn):
    """The same month, the same job (or department): peers are paid alike"""
    key = "payslip_net_peers"
    needs = ("hr.employee",)
    history_days = 62
    group = ("(SELECT COALESCE(e.job_id, e.department_id)::text FROM hr_employee e WHERE e.id = t.employee_id) "
             "|| '#' || to_char(t.date_from, 'YYYY-MM')")


class OvertimeHours(Statistical):
    key, family, model = "overtime_hours", "payslips", "hr.attendance"
    columns = ("overtime_hours", "employee_id", "check_in")
    value, group, day = "t.overtime_hours", "t.employee_id", "t.check_in::date"
    where = "t.overtime_hours > 0"
    floor = 0.5


class BillAmount(Statistical):
    key, family, model = "bill_amount", "invoices", "account.move"
    columns = ("amount_total", "partner_id", "invoice_date", "move_type", "state")
    value, group, day = "t.amount_total", "t.partner_id", "t.invoice_date"
    where = "t.move_type = 'in_invoice' AND t.state = 'posted'"


class InvoiceAmount(BillAmount):
    key = "invoice_amount"
    where = "t.move_type = 'out_invoice' AND t.state = 'posted'"


class BillUnitPrice(Statistical):
    key, family, model = "bill_unit_price", "invoices", "account.move.line"
    columns = ("price_unit", "product_id", "date", "display_type", "parent_state", "currency_id", "move_id")
    value, group, day = "t.price_unit", "t.product_id::text || '#' || coalesce(t.currency_id, 0)::text", "t.date"
    kind = "in_invoice"

    @property
    def where(self):
        return ("t.display_type = 'product' AND t.parent_state = 'posted' AND t.price_unit > 0 AND EXISTS "
                f"(SELECT 1 FROM account_move m WHERE m.id = t.move_id AND m.move_type = '{self.kind}')")


class InvoiceUnitPrice(BillUnitPrice):
    key = "invoice_unit_price"
    kind = "out_invoice"
    side = "both"


class BillVersusOrder(Rule):
    """The bills of a purchase order add up to more than the order itself, beyond a tolerance
    of 5% per step of sensitivity: a bill for a partial delivery is smaller, never larger"""

    key, family, model = "bill_vs_order", "invoices", "account.move"
    needs = ("purchase.order", "purchase.order.line")
    columns = ("move_type", "state")

    def available(self, env):
        return super().available(env) and "purchase_line_id" in env["account.move.line"]._fields

    def run(self, env, k):
        Move, Order = env["account.move"], env["purchase.order"]
        move_sub, move_params = visible(Move)
        order_sub, order_params = visible(Order)
        since = fields.Date.today() - timedelta(days=REPORT_DAYS)
        tolerance = 0.05 * k
        env.cr.execute(f"""
            SELECT po.id, po.amount_untaxed, sum(l.price_subtotal) AS billed, array_agg(DISTINCT m.id) AS bills
            FROM account_move m
            JOIN account_move_line l ON l.move_id = m.id
            JOIN purchase_order_line pl ON pl.id = l.purchase_line_id
            JOIN purchase_order po ON po.id = pl.order_id
            WHERE m.id IN ({move_sub}) AND po.id IN ({order_sub}) AND m.move_type = 'in_invoice'
              AND m.state = 'posted' AND l.display_type = 'product'
              AND m.currency_id = po.currency_id  -- amounts of two currencies are not compared
            GROUP BY po.id, po.amount_untaxed
            HAVING po.amount_untaxed > 0 AND sum(l.price_subtotal) > po.amount_untaxed * %s
               AND max(m.invoice_date) >= %s
            ORDER BY sum(l.price_subtotal) / po.amount_untaxed DESC LIMIT %s""",
            [*move_params, *order_params, 1 + tolerance, since, FOUND_CAP])
        rows = env.cr.fetchall()
        if not rows:
            return None
        ratio = round(rows[0][2] / rows[0][1], 1)
        bills = []
        for row in rows:
            bills.extend(row[3])
        return finding(
            self.key, CRITICAL if ratio >= 2 else WARNING, len(rows), len(rows) >= FOUND_CAP, "account.move",
            ids=sorted(set(bills), reverse=True), figure=ratio)


class StockMoveQuantity(Statistical):
    key, family, model = "move_quantity", "stock", "stock.move"
    columns = ("quantity", "product_id", "state", "date")
    value, group, day = "t.quantity", "t.product_id", "t.date"
    where = "t.state = 'done' AND t.quantity > 0"


class StockMoveCost(Rule):
    """Goods received at no cost, or at a negative one: the valuation is wrong from there"""

    key, family, model = "move_cost", "stock", "stock.move"
    columns = ("price_unit", "state", "location_id", "date")
    needs = ("stock.location",)

    def run(self, env, k):
        since = fields.Date.today() - timedelta(days=REPORT_DAYS)
        inbound = ("t.state = 'done' AND t.date >= %s AND EXISTS (SELECT 1 FROM stock_location l "
                   "WHERE l.id = t.location_id AND l.usage = 'supplier')")
        Move = env[self.model]
        negative = scan(env, Move, f"{inbound} AND t.price_unit < 0", [since])
        free = scan(env, Move, f"{inbound} AND COALESCE(t.price_unit, 0) = 0", [since])
        count = negative[0] + free[0]
        if not count:
            return None
        ids = (negative[2] + free[2])[:MAX_IDS]
        return finding(self.key, CRITICAL if negative[0] else WARNING, count, negative[1] or free[1],
                       self.model, ids=ids)


class ExpenseAmount(Statistical):
    key, family, model = "expense_amount", "expenses", "hr.expense"
    columns = ("total_amount", "product_id", "date", "state")
    value, group, day = "t.total_amount", "t.product_id", "t.date"
    where = "t.state <> 'refused'"


class SaleDiscountProduct(Statistical):
    key, family, model = "discount_product", "sales", "sale.order.line"
    columns = ("discount", "product_id", "create_date", "display_type")
    value, group, day = "t.discount", "t.product_id", "t.create_date::date"
    where = "t.display_type IS NULL"
    floor = 5.0


class SaleDiscountCustomer(SaleDiscountProduct):
    key = "discount_customer"
    columns = ("discount", "order_partner_id", "create_date", "display_type")
    group = "t.order_partner_id"


class SalePriceBelowCost(Rule):
    """The unit price, after the discount, is under what the product costs. Cost is
    company-dependent, so the ORM reads it: a sample of the latest lines is looked at"""

    key, family, model = "price_below_cost", "sales", "sale.order.line"
    columns = ("price_unit", "product_id", "discount", "create_date", "currency_id")

    def run(self, env, k):
        Line = env[self.model]
        since = fields.Datetime.now() - timedelta(days=REPORT_DAYS)
        lines = Line.search([
            ("product_id", "!=", False), ("price_unit", ">", 0), ("create_date", ">=", since),
            ("display_type", "=", False)], order="id desc", limit=2000)
        bad = []
        worst = 0.0
        for line in lines:
            cost = line.product_id.standard_price
            if not cost or line.currency_id != line.company_id.currency_id:
                continue
            price = line.price_unit * (1 - (line.discount or 0) / 100)
            if price < cost:
                bad.append(line.id)
                worst = max(worst, cost / price if price else 0.0)
        if not bad:
            return None
        return finding(self.key, WARNING, len(bad), False, self.model, ids=bad[:MAX_IDS],
                       sample=len(lines) if len(lines) >= 2000 else 0, figure=round(worst, 1))


class PurchasePriceAboveLast(Rule):
    """A purchase price far above the median of the last five purchases of the product
    (same currency): 15% per step of sensitivity over it, at least three purchases before"""

    key, family, model = "price_above_last", "purchases", "purchase.order.line"
    columns = ("price_unit", "product_id", "order_id", "create_date")

    def run(self, env, k):
        Line = env[self.model]
        sub, sub_params = visible(Line)
        since = fields.Datetime.now() - timedelta(days=REPORT_DAYS)
        env.cr.execute(f"""
            SELECT t.id, t.price_unit, p.med FROM purchase_order_line t
            JOIN purchase_order po ON po.id = t.order_id
            CROSS JOIN LATERAL (
                SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY x.price_unit) AS med, count(*) AS n
                FROM (SELECT y.price_unit FROM purchase_order_line y
                      JOIN purchase_order yo ON yo.id = y.order_id
                      WHERE y.product_id = t.product_id AND y.id < t.id AND y.price_unit > 0
                        AND yo.currency_id = po.currency_id AND y.id IN ({sub})
                      ORDER BY y.id DESC LIMIT %s) x) p
            WHERE t.id IN ({sub}) AND t.product_id IS NOT NULL AND t.price_unit > 0 AND t.create_date >= %s
              AND p.n >= %s AND t.price_unit > p.med * %s
            ORDER BY t.price_unit / p.med DESC, t.id DESC LIMIT %s""",
            [*sub_params, LAST_PURCHASES, *sub_params, since, MIN_LAST_PURCHASES, 1 + PRICE_STEP * k, FOUND_CAP])
        rows = env.cr.fetchall()
        if not rows:
            return None
        ratio = round(rows[0][1] / rows[0][2], 1)
        return finding(self.key, CRITICAL if ratio >= 2 else WARNING, len(rows), len(rows) >= FOUND_CAP,
                       self.model, ids=[row[0] for row in rows[:MAX_IDS]], figure=ratio)


class AttendanceDay(Statistical):
    """The hours worked in a day, added up per person, against that person's usual day"""

    key, family, model = "attendance_hours", "attendance", "hr.attendance"
    columns = ("worked_hours", "employee_id", "check_in", "check_out")
    side = "both"
    floor = 1.0

    def base(self, model, sub, sub_params, since):
        sql = (
            f'SELECT min(t.id) AS id, t.employee_id::text AS g, sum(t.worked_hours)::float8 AS v, '
            f't.check_in::date AS d FROM "{model._table}" t WHERE t.id IN ({sub}) AND t.check_out IS NOT NULL '
            f"AND t.check_in::date >= %s GROUP BY t.employee_id, t.check_in::date ORDER BY d DESC LIMIT %s")
        return sql, [*sub_params, since, HISTORY_ROWS]


RULES = (
    PayslipNetOwn(), PayslipGrossOwn(), PayslipNetPeers(), OvertimeHours(),
    BillAmount(), InvoiceAmount(), BillUnitPrice(), InvoiceUnitPrice(), BillVersusOrder(),
    StockMoveQuantity(), StockMoveCost(),
    ExpenseAmount(),
    SaleDiscountProduct(), SaleDiscountCustomer(), SalePriceBelowCost(),
    PurchasePriceAboveLast(),
    AttendanceDay(),
)
BY_RULE = {rule.key: rule for rule in RULES}


# ----------------------------------------------------------------------
# The checks of the health check: one per family
# ----------------------------------------------------------------------
class UnusualFigures(Check):
    """The unusual figures of one family of records"""

    family = ""

    @property
    def key(self):
        return f"outliers_{self.family}"

    def rules(self, env):
        return [rule for rule in RULES if rule.family == self.family and rule.available(env)]

    def available(self, env):
        return bool(self.rules(env))

    def run(self, env, clock):
        k = sensitivity(env)
        found, denied, rules = [], 0, self.rules(env)
        for rule in rules:
            if clock.over():
                break
            try:
                with env.cr.savepoint():
                    item = rule.run(env, k)
            except AccessError:
                denied += 1
                continue
            if item:
                found.append(item)
        if rules and denied == len(rules):
            raise AccessError(env._("Your rights do not let you read what this looks at."))
        return found

    def describe(self, env, item):
        return self.texts(env, item["key"], item["count"], item.get("sample") or 0, item.get("figure") or 0.0)

    def texts(self, env, key, count, sample, figure=0.0):
        cr, uid, context = env.cr, env.uid, env.context  # noqa: F841 - where Odoo 17's _() reads the language
        sentence = TEXTS[key](env, count)
        if figure:
            return sentence + " " + env._("The furthest is %(ratio)s× the usual.", ratio=figure)
        return sentence


def _texts():
    def payslip_net_own(env, count):
        return env._("%(count)s payslips have a net pay far from what the same employee usually gets.", count=count)

    def payslip_gross_own(env, count):
        return env._("%(count)s payslips have a gross pay far from what the same employee usually gets.", count=count)

    def payslip_net_peers(env, count):
        return env._("%(count)s payslips pay an employee far from the others of the same job or department "
                     "that month.", count=count)

    def overtime_hours(env, count):
        return env._("%(count)s days show overtime far above what the same person usually works.", count=count)

    def bill_amount(env, count):
        return env._("%(count)s vendor bills are far above the usual for this supplier.", count=count)

    def invoice_amount(env, count):
        return env._("%(count)s customer invoices are far from the usual for this customer.", count=count)

    def bill_unit_price(env, count):
        return env._("%(count)s vendor bill lines have a unit price far above the usual for the same product.",
                     count=count)

    def invoice_unit_price(env, count):
        return env._("%(count)s customer invoice lines have a unit price far from the usual for the same product.",
                     count=count)

    def bill_vs_order(env, count):
        return env._("%(count)s purchase orders are billed for more than they were ordered for.", count=count)

    def move_quantity(env, count):
        return env._("%(count)s stock moves have a quantity far above the usual move of the same product.",
                     count=count)

    def move_cost(env, count):
        return env._("%(count)s goods receipts have a zero or negative cost, so stock is valued wrongly.",
                     count=count)

    def expense_amount(env, count):
        return env._("%(count)s expenses are far above the usual for their category.", count=count)

    def discount_product(env, count):
        return env._("%(count)s sales order lines give a discount far above the usual for the same product.",
                     count=count)

    def discount_customer(env, count):
        return env._("%(count)s sales order lines give a discount far above the usual for the same customer.",
                     count=count)

    def price_below_cost(env, count):
        return env._("%(count)s sales order lines sell under the cost of the product.", count=count)

    def price_above_last(env, count):
        return env._("%(count)s purchase lines pay far more than the last purchases of the same product.",
                     count=count)

    def attendance_hours(env, count):
        return env._("%(count)s days of attendance are far from the hours the same person usually works.",
                     count=count)

    return {fn.__name__: fn for fn in (
        payslip_net_own, payslip_gross_own, payslip_net_peers, overtime_hours, bill_amount, invoice_amount,
        bill_unit_price, invoice_unit_price, bill_vs_order, move_quantity, move_cost, expense_amount,
        discount_product, discount_customer, price_below_cost, price_above_last, attendance_hours)}


TEXTS = _texts()


def family_checks():
    checks = []
    for family in FAMILIES:
        check = UnusualFigures()
        check.family = family
        checks.append(check)
    return tuple(checks)


CHECKS = family_checks()


def labels(env):
    """:return: ``{check key: name}`` of the families, in the language of ``env``"""
    return {
        "outliers_payslips": env._("Unusual figures: payslips and overtime"),
        "outliers_invoices": env._("Unusual figures: bills and invoices"),
        "outliers_stock": env._("Unusual figures: stock moves"),
        "outliers_expenses": env._("Unusual figures: expenses"),
        "outliers_sales": env._("Unusual figures: sales lines"),
        "outliers_purchases": env._("Unusual figures: purchase lines"),
        "outliers_attendance": env._("Unusual figures: attendance"),
    }


def family_labels(env):
    """:return: ``{family: name}`` for choosing what a task watches"""
    names = labels(env)
    return {family: names[f"outliers_{family}"] for family in FAMILIES}

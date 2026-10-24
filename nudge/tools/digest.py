"""The numbers of the morning digest, counted by Odoo as the person.

Each block exists only when its app is installed and the person belongs to
its group; each line is counted with their own rights and record rules, so
nothing is told that they may not read. Nothing here calls an AI: the
provider, when there is one, only writes a few sentences around these
numbers.
"""
from datetime import datetime, time, timedelta

import pytz

from odoo import _, fields

# The groups that open a block, and the managers that get the condensed view
GROUPS = {
    "sales": ("sales_team.group_sale_salesman",),
    "purchase": ("purchase.group_purchase_user",),
    "accounting": ("account.group_account_invoice", "account.group_account_user"),
    "inventory": ("stock.group_stock_user",),
    "project": ("project.group_project_user",),
    "hr": ("hr_holidays.group_hr_holidays_responsible", "hr.group_hr_user",
           "hr_attendance.group_hr_attendance_officer"),
}
MANAGERS = (
    "sales_team.group_sale_manager", "purchase.group_purchase_manager", "account.group_account_manager",
    "stock.group_stock_manager", "project.group_project_manager", "hr.group_hr_manager")
# A manager reads the headline of each area, not every line
CONDENSED_LINES = 3
MAX_NAMES = 5


def has_group(env, xmlid):
    try:
        return bool(env.user.has_group(xmlid))
    except Exception:  # noqa: BLE001
        return False


def can_read(env, model):
    if model not in env:
        return False
    records = env[model]
    try:
        if hasattr(records, "has_access"):
            return bool(records._nudge_has_access("read"))
        return bool(records.check_access_rights("read", raise_exception=False))
    except Exception:  # noqa: BLE001
        return False


def is_manager(env):
    return any(has_group(env, xmlid) for xmlid in MANAGERS)


def _money(env, amount):
    currency = env.company.currency_id
    try:
        return currency.format(amount)
    except Exception:  # noqa: BLE001
        return f"{amount:,.2f}"


def _bounds(env, today):
    """:return: the start and the end of the day ``today`` of the person, as the UTC text of the datetime columns:
    what is "today" is the whole local day, not the instant the digest runs"""
    try:
        zone = pytz.timezone(env.context.get("tz") or env.user.tz or "UTC")
    except pytz.UnknownTimeZoneError:
        zone = pytz.utc
    start = zone.localize(datetime.combine(today, time.min)).astimezone(pytz.utc).replace(tzinfo=None)
    end = zone.localize(datetime.combine(today + timedelta(days=1), time.min)).astimezone(pytz.utc).replace(tzinfo=None)
    return fields.Datetime.to_string(start), fields.Datetime.to_string(end)


def currency_field_of(field, model):
    """:return: the name of the field that holds the currency of a monetary field, or None"""
    if hasattr(field, "get_currency_field"):
        return field.get_currency_field(model)
    return field.currency_field or ("currency_id" if "currency_id" in model._fields else None)


def _sum(env, records, domain, amount):
    """:return: the sum of a monetary field in the money of the company: documents in another currency are
    converted at today's rate, so the figure is one currency and not a mixture"""
    field = records._fields[amount]
    target = env.company.currency_id
    name = currency_field_of(field, records) if field.type == "monetary" else None
    if name and name in records._fields and records._fields[name].store:
        today = fields.Date.context_today(env["res.users"])
        total = 0.0
        for currency, value in records._read_group(domain, [name], [f"{amount}:sum"]):
            value = value or 0.0
            if currency and currency != target:
                value = currency._convert(value, target, env.company, today)
            total += value
        return total
    [(total,)] = records._read_group(domain, [], [f"{amount}:sum"])
    return total or 0.0


def _line(env, label, model, domain, amount=None, name=None, key=None, sign=1):
    """:return: ``{"label", "count", "amount", "model", "domain", "name"}`` or None when
    nothing matches, the model is missing or the person may not read it"""
    if not can_read(env, model):
        return None
    records = env[model]
    fields_ = records._fields
    if any(isinstance(term, (tuple, list)) and term[0].split(".")[0] not in fields_ for term in domain):
        return None
    if amount and amount not in fields_:
        amount = None
    count = records.search_count(domain)
    if not count and not env.context.get("nudge_keep_zero"):
        return None
    text = ""
    if amount and count:
        text = _money(env, _sum(env, records, domain, amount) * sign)
    return {"label": label, "count": count, "amount": text, "model": model, "domain": domain,
            "name": name or label, "change": None, "key": key}


def _change(env, label, model, domain_now, domain_before, amount, name, key=None):
    """Money of this week against the week before."""
    if not can_read(env, model) or amount not in env[model]._fields:
        return None
    records = env[model]
    now, before = _sum(env, records, domain_now, amount), _sum(env, records, domain_before, amount)
    if not now and not before:
        return None
    line = {"label": label, "count": records.search_count(domain_now), "amount": _money(env, now),
            "model": model, "domain": domain_now, "name": name, "change": None, "key": key}
    if before:
        line["change"] = round((now - before) / before * 100)
    return line


def _names(env, model, domain, field="display_name"):
    """:return: the names of a few of the records, to say who"""
    if not can_read(env, model):
        return []
    return [getattr(record, field) for record in env[model].search(domain, limit=MAX_NAMES)]


def _sales(env, now, today, since):
    week = fields.Datetime.to_string(now - timedelta(days=7))
    two_weeks = fields.Datetime.to_string(now - timedelta(days=14))
    confirmed = [("state", "=", "sale")]
    lines = [
        _line(env, _("New leads in the last day"), "crm.lead", [("create_date", ">=", since)],
              name=_("New leads"), key="new_leads"),
        _line(env, _("Quotations awaiting an answer"), "sale.order", [("state", "in", ["draft", "sent"])],
              amount="amount_total", name=_("Quotations"), key="quotations"),
        _line(env, _("Orders still to deliver"), "sale.order",
              confirmed + [("delivery_status", "in", ["pending", "started", "partial"])],
              amount="amount_total", name=_("Orders to deliver"), key="orders_to_deliver"),
        _change(env, _("Confirmed sales in the last 7 days"), "sale.order",
                confirmed + [("date_order", ">=", week)],
                confirmed + [("date_order", ">=", two_weeks), ("date_order", "<", week)],
                "amount_total", _("Confirmed sales"), "confirmed_sales"),
    ]
    return lines


def _purchase(env, now, today, since):
    return [
        _line(env, _("Purchase orders waiting for approval"), "purchase.order", [("state", "=", "to approve")],
              amount="amount_total", name=_("Purchase orders to approve"), key="purchase_orders_to_approve"),
        _line(env, _("Purchase orders still to receive"), "purchase.order",
              [("state", "=", "purchase"), ("receipt_status", "in", ["pending", "partial"])],
              name=_("Purchase orders to receive"), key="purchase_orders_to_receive"),
        _line(env, _("Vendor bills to pay"), "account.move",
              [("move_type", "=", "in_invoice"), ("state", "=", "posted"),
               ("payment_state", "in", ["not_paid", "partial"])],
              amount="amount_residual_signed", sign=-1, name=_("Vendor bills"), key="vendor_bills"),
    ]


def _accounting(env, now, today, since):
    day = fields.Date.to_string(today)
    return [
        _line(env, _("Customer invoices overdue"), "account.move",
              [("move_type", "=", "out_invoice"), ("state", "=", "posted"),
               ("payment_state", "in", ["not_paid", "partial"]), ("invoice_date_due", "<", day)],
              amount="amount_residual_signed", name=_("Overdue invoices"), key="overdue_invoices"),
        _line(env, _("Payments received in the last day"), "account.payment",
              [("payment_type", "=", "inbound"), ("state", "not in", ["draft", "canceled", "rejected"]),
               ("date", ">=", fields.Date.to_string(today - timedelta(days=1)))],
              amount="amount", name=_("Payments received"), key="payments_received"),
        _line(env, _("Bank lines to reconcile"), "account.bank.statement.line",
              [("is_reconciled", "=", False)], name=_("Bank lines to reconcile"), key="bank_lines_to_reconcile"),
    ]


def _inventory(env, now, today, since):
    end_of_day = _bounds(env, today)[1]
    open_states = ["confirmed", "waiting", "assigned"]
    return [
        _line(env, _("Products with negative stock"), "stock.quant",
              [("location_id.usage", "=", "internal"), ("quantity", "<", 0)],
              name=_("Negative stock"), key="negative_stock"),
        _line(env, _("Receipts due today or before"), "stock.picking",
              [("picking_type_code", "=", "incoming"), ("state", "in", open_states),
               ("scheduled_date", "<", end_of_day)], name=_("Receipts due"), key="receipts_due"),
        _line(env, _("Deliveries late"), "stock.picking",
              [("picking_type_code", "=", "outgoing"), ("state", "in", open_states),
               ("scheduled_date", "<", fields.Datetime.to_string(now))], name=_("Late deliveries"), key="late_deliveries"),
    ]


def _project(env, now, today, since):
    mine = [("user_ids", "in", [env.uid])]
    open_ = [("state", "not in", ["1_done", "1_canceled"])]
    start, end = _bounds(env, today)
    return [
        _line(env, _("Your tasks overdue"), "project.task",
              mine + open_ + [("date_deadline", "<", start)],
              name=_("Overdue tasks"), key="overdue_tasks"),
        _line(env, _("Your tasks due today"), "project.task",
              mine + open_ + [("date_deadline", ">=", start), ("date_deadline", "<", end)],
              name=_("Tasks due today"), key="tasks_due_today"),
        _line(env, _("Your tasks that changed stage in the last day"), "project.task",
              mine + [("date_last_stage_update", ">=", since)], name=_("Stage changes"), key="stage_changes"),
    ]


def _hr(env, now, today, since):
    start, end = _bounds(env, today)
    lines = [
        _line(env, _("Time off requests waiting for you"), "hr.leave", [("state", "=", "confirm")],
              name=_("Time off to approve"), key="time_off_to_approve"),
    ]
    off = _line(env, _("People off today"), "hr.leave",
                [("state", "=", "validate"), ("date_from", "<", end), ("date_to", ">", start)],
                name=_("People off today"), key="people_off_today")
    if off:
        names = _names(env, "hr.leave", off["domain"], "employee_id.name") if "employee_id" in env["hr.leave"]._fields else []
        if names:
            off["label"] = f"{off['label']} ({', '.join(names)})"
        lines.append(off)
    lines.append(_line(
        env, _("Open attendances since before today"), "hr.attendance",
        [("check_out", "=", False), ("check_in", "<", start)],
        name=_("Attendance to check"), key="attendance_to_check"))
    return lines


BLOCKS = (
    ("sales", "Sales", _sales),
    ("purchase", "Purchases", _purchase),
    ("accounting", "Accounting", _accounting),
    ("inventory", "Inventory", _inventory),
    ("project", "Projects", _project),
    ("hr", "People", _hr),
)


def gather(env):
    """:return: ``[{"key", "title", "lines"}]``, the blocks of the person's digest, in
    their environment; empty when nothing needs them. A manager reads an overview
    first and the headline lines of each area."""
    now = fields.Datetime.now()
    today = fields.Date.context_today(env["res.users"])
    since = fields.Datetime.to_string(now - timedelta(days=1))
    out = []
    titles = {
        "sales": _("Sales"), "purchase": _("Purchases"), "accounting": _("Accounting"),
        "inventory": _("Inventory"), "project": _("Projects"), "hr": _("People")}
    for key, _title, build in BLOCKS:
        if not any(has_group(env, xmlid) for xmlid in GROUPS[key]):
            continue
        try:
            lines = [line for line in build(env, now, today, since) if line]
        except Exception:  # noqa: BLE001 - one app's trouble never costs the person the rest
            lines = []
        if lines:
            out.append({"key": key, "title": titles[key], "lines": lines})
    if out and is_manager(env):
        overview = [dict(block["lines"][0]) for block in out]
        out = [{"key": "overview", "title": _("At a glance"), "lines": overview}] + [
            dict(block, lines=block["lines"][:CONDENSED_LINES]) for block in out]
    return out


def sentence(env, line):
    """:return: one line of a block in words"""
    text = f"{line['label']}: {line['count']:,}"
    if line["amount"]:
        text += f" ({line['amount']})"
    if line.get("change") is not None:
        change = f"{line['change']:+d}"
        text += _(", %(change)s%% against the week before", change=change)
    return text

"""The health check of a database: small checks, deterministic, no AI.

Each check is a class in ``CHECKS``; to add one, write a class and add it to
the list. A check

* runs as the person who asked, over the records their rights and the record
  rules of their companies let them read (``visible`` builds the sub-query),
* is only offered when the models it looks at are installed (never a
  dependency),
* never changes anything,
* does not hang a large database: SQL with LIMITs, a statement timeout per
  check, samples where a full scan would be needed. Nothing is iterated in
  Python over all the records.

A check returns findings: dicts with the severity, the count, and what
"Show me" opens: a domain, or a few ids. The sentence of a finding is not
stored: ``describe`` writes it in the language of whoever reads the report.
"""
import logging
import re
import time
from contextlib import contextmanager
from datetime import timedelta

from odoo import _, fields
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)

CRITICAL, WARNING, INFO = "critical", "warning", "info"
SEVERITIES = (CRITICAL, WARNING, INFO)

# Records behind a "Show me" that is not a domain
MAX_IDS = 200
# Groups or records counted before the count reads "1000+"
COUNT_CAP = 1000
# Records read in a sample, where a full scan would be too much
SAMPLE = 2000
# Seconds a check may spend in the database, statement by statement
CHECK_SECONDS = 8
# Days without a login before an active user is reported
IDLE_DAYS = 90
# Hours a mail may wait in the outgoing queue
STUCK_HOURS = 24

LANG = re.compile(r"[A-Za-z]{2,3}(_[A-Za-z0-9]+)?")


# ----------------------------------------------------------------------
# What the checks are made of
# ----------------------------------------------------------------------
class Clock:
    """The time a check has left: loops over many small queries stop on it"""

    def __init__(self, seconds):
        self.end = time.monotonic() + seconds

    def over(self):
        return time.monotonic() > self.end


class Skipped(Exception):
    """A check that could not run, and why: ``timeout``, ``access`` or ``error``"""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def finding(key, severity, count, capped=False, model=None, domain=None, ids=None, sample=0, figure=0.0):
    """:return: one finding. ``domain`` is what "Show me" opens, else ``ids``
    (at most MAX_IDS); ``sample`` is the size of the sample when it is one;
    ``figure`` is the number a sentence quotes (how many times the usual)"""
    return {"key": key, "severity": severity, "count": count, "capped": bool(capped), "model": model,
            "domain": domain, "ids": list(ids or [])[:MAX_IDS], "sample": sample, "figure": figure}


def count_text(count, capped):
    return f"{count}+" if capped else str(count)


def visible(model, domain=None):
    """:return: ``(sql, params)`` selecting the ids of the records of ``model``
    the user of its environment may read: access rights and record rules
    included, through the ORM and not around it"""
    query = model._search(domain or [])
    selected = query.select(f'"{model._table}"."id"')
    if isinstance(selected, tuple):  # Odoo 17
        return selected[0], list(selected[1])
    return selected.code, list(selected.params)  # Odoo 18 and later


def scan(env, model, where="TRUE", params=(), domain=None, cap=COUNT_CAP):
    """The records of ``model`` the user may read for which the SQL condition
    ``where`` (on the alias ``t``) holds.

    :return: ``(count up to cap, whether it stopped at cap, some ids)``"""
    sub, sub_params = visible(model, domain)
    table = model._table
    tail = f'FROM "{table}" t WHERE t.id IN ({sub}) AND ({where})'
    env.cr.execute(f"SELECT count(*) FROM (SELECT 1 {tail} LIMIT %s) x", [*sub_params, *params, cap])
    count = env.cr.fetchone()[0]
    ids = []
    if count:
        env.cr.execute(f"SELECT t.id {tail} ORDER BY t.id DESC LIMIT %s", [*sub_params, *params, MAX_IDS])
        ids = [row[0] for row in env.cr.fetchall()]
    return count, count >= cap, ids


def groups(env, model, expression, where="TRUE", params=(), cap=COUNT_CAP):
    """The groups of records of ``model`` the user may read that share the
    value of the SQL ``expression`` (on the alias ``t``), a null one never.

    :return: ``(number of groups up to cap, whether it stopped, ids of the records)``"""
    sub, sub_params = visible(model)
    env.cr.execute(
        f'SELECT array_agg(t.id ORDER BY t.id) FROM "{model._table}" t '
        f"WHERE t.id IN ({sub}) AND ({where}) AND ({expression}) IS NOT NULL "
        f"GROUP BY {expression} HAVING count(*) > 1 LIMIT %s",
        [*sub_params, *params, cap])
    rows = env.cr.fetchall()
    ids = []
    for row in rows:
        ids.extend(row[0])
        if len(ids) >= MAX_IDS:
            break
    return len(rows), len(rows) >= cap, ids


def counted(model, domain, cap=COUNT_CAP):
    """:return: ``(count up to cap, whether it stopped)`` of the records of
    ``model`` found by ``domain`` for the user, by the ORM"""
    count = model.search_count(domain, limit=cap)
    return count, count >= cap


def text_of(env, model, name):
    """:return: the SQL of a text column on the alias ``t``, in the language
    of the person when the column is translated (one value per language)"""
    column = f't."{name}"'
    if not model._fields[name].translate:
        return column
    lang = env.lang if LANG.fullmatch(env.lang or "") else "en_US"
    return f"COALESCE({column}->>'en_US', {column}->>'{lang}')"


@contextmanager
def budget(env, seconds):
    """Statements of the block are cancelled by the database after
    ``seconds``; what they did is rolled back to the start of the block"""
    cr = env.cr
    cr.execute("SHOW statement_timeout")
    before = cr.fetchone()[0]
    with cr.savepoint():
        cr.execute("SELECT set_config('statement_timeout', %s, true)", [f"{int(seconds * 1000)}ms"])
        yield
        cr.execute("SELECT set_config('statement_timeout', %s, true)", [before])


def group_ids(env, xmlid):
    """:return: ids of the group ``xmlid`` and of every group it implies"""
    group = env.ref(xmlid, raise_if_not_found=False)
    if not group:
        return []
    implied = group.all_implied_ids if "all_implied_ids" in group._fields else group.trans_implied_ids
    return (group | implied).ids


# ----------------------------------------------------------------------
# The checks
# ----------------------------------------------------------------------
class Check:
    """A kind of problem. ``needs`` are the models that must be installed"""

    key = ""
    needs = ()

    def available(self, env):
        return all(name in env.registry for name in self.needs)

    def run(self, env, clock):
        """:return: the findings, none when all is well"""
        raise NotImplementedError

    def texts(self, env, key, count, sample):
        """:return: the sentence of the finding ``key``, in the language of ``env``"""
        cr, uid, context = env.cr, env.uid, env.context  # 17: what _() looks for
        cr, uid, context = env.cr, env.uid, env.context  # noqa: F841 - where Odoo 17's _() reads the language
        raise NotImplementedError

    def describe(self, env, item):
        return self.texts(env, item["key"], count_text(item["count"], item.get("capped")), item.get("sample") or 0)


# How two contacts are told to be the same: the SQL of the value they share, on the alias ``t``.
# The input validator asks the same question of a form not yet saved.
def partner_key_sql(kind, column=None):
    """:return: the SQL of the value two contacts share for ``kind``, a null one meaning none"""
    column = column or {"phone": "phone"}.get(kind, kind)
    if kind == "email":
        return f"NULLIF(lower(btrim(coalesce(t.{column}, ''))), '')"
    if kind == "vat":
        return f"NULLIF(upper(regexp_replace(coalesce(t.{column}, ''), '[^[:alnum:]]', '', 'g')), '')"
    if kind == "phone":
        # The last nine digits: +971 50 123 4567 and 050 123 4567 meet
        digits = f"regexp_replace(coalesce(t.{column}, ''), '[^0-9]', '', 'g')"
        return f"CASE WHEN length({digits}) >= 7 THEN right({digits}, 9) END"
    return ("CASE WHEN length(regexp_replace(lower(coalesce(t.name, '')), '[^[:alnum:]]', '', 'g')) >= 4 "
            "THEN regexp_replace(lower(t.name), '[^[:alnum:]]', '', 'g') END")


class PartnerDuplicates(Check):
    key = "partner_duplicates"
    needs = ("res.partner",)

    def run(self, env, clock):
        Partner = env["res.partner"]
        kinds = (
            # An address of a contact below a company is the company's own
            ("email", WARNING), ("vat", WARNING), ("phone", INFO), ("name", INFO))
        found = []
        for key, severity in kinds:
            if key == "vat" and "vat" not in Partner._fields:
                continue
            if key == "phone" and "phone" not in Partner._fields:
                continue
            count, capped, ids = groups(env, Partner, partner_key_sql(key), "t.parent_id IS NULL")
            if count:
                found.append(finding(key, severity, count, capped, "res.partner", ids=ids))
        return found

    def texts(self, env, key, count, sample):
        cr, uid, context = env.cr, env.uid, env.context  # 17: what _() looks for
        cr, uid, context = env.cr, env.uid, env.context  # noqa: F841 - where Odoo 17's _() reads the language
        return {
            "email": _("%(count)s email addresses are used by more than one contact.", count=count),
            "vat": _("%(count)s tax numbers are used by more than one contact.", count=count),
            "phone": _("%(count)s phone numbers are used by more than one contact, whatever the spaces and the prefix.", count=count),
            "name": _("%(count)s names are written twice or more for contacts, apart from capitals, spaces and signs.", count=count),
        }[key]


class ProductDuplicates(Check):
    key = "product_duplicates"
    needs = ("product.product",)

    def run(self, env, clock):
        Product = env["product.product"]
        Template = env["product.template"]
        found = []
        for key, name in (("code", "default_code"), ("barcode", "barcode")):
            if name not in Product._fields:
                continue
            count, capped, ids = groups(env, Product, f"NULLIF(btrim(coalesce(t.\"{name}\", '')), '')")
            if count:
                found.append(finding(key, CRITICAL if key == "code" else WARNING, count, capped, "product.product", ids=ids))
        if "categ_id" in Template._fields:
            label = text_of(env, Template, "name")
            expression = f"NULLIF(lower(btrim(coalesce({label}, ''))), '') || '#' || coalesce(t.categ_id::text, '')"
            count, capped, ids = groups(env, Template, expression)
            if count:
                found.append(finding("name", INFO, count, capped, "product.template", ids=ids))
        return found

    def texts(self, env, key, count, sample):
        cr, uid, context = env.cr, env.uid, env.context  # 17: what _() looks for
        cr, uid, context = env.cr, env.uid, env.context  # noqa: F841 - where Odoo 17's _() reads the language
        return {
            "code": _("%(count)s internal references are used by more than one product.", count=count),
            "barcode": _("%(count)s barcodes are used by more than one product.", count=count),
            "name": _("%(count)s product names are used twice or more in the same category.", count=count),
        }[key]


class ProductData(Check):
    key = "product_data"
    needs = ("product.product",)

    def run(self, env, clock):
        Template, Product = env["product.template"], env["product.product"]
        found = []
        checks = []
        if "categ_id" in Template._fields:
            checks.append(("no_category", WARNING, Template, [("categ_id", "=", False)]))
        if "sale_ok" in Template._fields:
            checks.append(("no_sale_price", WARNING, Template, [("sale_ok", "=", True), ("list_price", "=", 0)]))
        if "purchase_ok" in Template._fields:
            checks.append(("no_cost", INFO, Product, [("purchase_ok", "=", True), ("standard_price", "=", 0)]))
        for key, severity, model, domain in checks:
            count, capped = counted(model, domain)
            if count:
                found.append(finding(key, severity, count, capped, model._name, domain=domain))
        return found

    def texts(self, env, key, count, sample):
        cr, uid, context = env.cr, env.uid, env.context  # 17: what _() looks for
        cr, uid, context = env.cr, env.uid, env.context  # noqa: F841 - where Odoo 17's _() reads the language
        return {
            "no_category": _("%(count)s products have no category.", count=count),
            "no_sale_price": _("%(count)s products can be sold but have no sale price.", count=count),
            "no_cost": _("%(count)s products can be purchased but have no cost.", count=count),
        }[key]


class AccountSetup(Check):
    key = "account_setup"
    needs = ("account.journal", "res.company")

    COMPANY_ACCOUNTS = ("income_account_id", "expense_account_id", "account_journal_suspense_account_id",
                        "transfer_account_id")

    def run(self, env, clock):
        Company, Journal = env["res.company"], env["account.journal"]
        found = []
        if "default_account_id" in Journal._fields:
            domain = [("type", "in", ("sale", "purchase", "bank", "cash")), ("default_account_id", "=", False)]
            count, capped = counted(Journal, domain)
            if count:
                found.append(finding("journal_account", CRITICAL, count, capped, "account.journal", domain=domain))
        names = [name for name in self.COMPANY_ACCOUNTS
                 if name in Company._fields and Company._fields[name].store
                 and not Company._fields[name].company_dependent]
        if names:
            # A company that has no chart of accounts yet has no accounts to miss
            chart = [("chart_template", "!=", False)] if "chart_template" in Company._fields else []
            domain = chart + ["|"] * (len(names) - 1) + [(name, "=", False) for name in names]
            count, capped = counted(Company, domain)
            if count:
                found.append(finding("company_account", CRITICAL, count, capped, "res.company", domain=domain))
        return found

    def texts(self, env, key, count, sample):
        cr, uid, context = env.cr, env.uid, env.context  # 17: what _() looks for
        cr, uid, context = env.cr, env.uid, env.context  # noqa: F841 - where Odoo 17's _() reads the language
        return {
            "journal_account": _("%(count)s journals have no default account.", count=count),
            "company_account": _("%(count)s companies miss a default account of their accounting.", count=count),
        }[key]


class TaxSetup(Check):
    key = "tax_setup"
    needs = ("account.tax", "product.template")

    def run(self, env, clock):
        Template, Partner = env["product.template"], env["res.partner"]
        found = []
        if "taxes_id" in Template._fields and env["account.tax"].search_count([("type_tax_use", "=", "sale")], limit=1):
            domain = [("sale_ok", "=", True), ("taxes_id", "=", False)]
            count, capped = counted(Template, domain)
            if count:
                found.append(finding("no_tax", WARNING, count, capped, "product.template", domain=domain))
        if "property_account_position_id" in Partner._fields and "country_id" in Partner._fields:
            # Read in a sample: the position is a value per company
            partners = Partner.search(
                [("property_account_position_id", "!=", False), ("country_id", "!=", False)], limit=SAMPLE)
            wrong = partners.filtered(lambda partner: partner.property_account_position_id.auto_apply
                                      and partner.property_account_position_id.country_id
                                      and partner.property_account_position_id.country_id != partner.country_id)
            if wrong:
                found.append(finding("position_country", WARNING, len(wrong), False, "res.partner",
                                     ids=wrong.ids, sample=len(partners)))
        return found

    def texts(self, env, key, count, sample):
        cr, uid, context = env.cr, env.uid, env.context  # 17: what _() looks for
        cr, uid, context = env.cr, env.uid, env.context  # noqa: F841 - where Odoo 17's _() reads the language
        return {
            "no_tax": _("%(count)s products can be sold but carry no sales tax.", count=count),
            "position_country": _(
                "%(count)s contacts, among %(sample)s looked at, have a fiscal position that applies to another country than theirs.",
                count=count, sample=sample),
        }[key]


class StockNegative(Check):
    key = "stock_negative"
    needs = ("stock.quant", "stock.location")

    def run(self, env, clock):
        Quant, Location = env["stock.quant"], env["stock.location"]
        found = []
        inside = "EXISTS (SELECT 1 FROM stock_location l WHERE l.id = t.location_id AND l.usage = 'internal')"
        count, capped, ids = scan(env, Quant, f"t.quantity < 0 AND {inside}")
        if count:
            found.append(finding("quants", CRITICAL, count, capped, "stock.quant", ids=ids))
            sub, params = visible(Quant)
            count, capped, ids = scan(
                env, Location,
                f"t.usage = 'internal' AND t.id IN (SELECT q.location_id FROM stock_quant q WHERE q.quantity < 0 "
                f"AND q.id IN ({sub}))", params)
            if count:
                found.append(finding("locations", WARNING, count, capped, "stock.location", ids=ids))
        return found

    def texts(self, env, key, count, sample):
        cr, uid, context = env.cr, env.uid, env.context  # 17: what _() looks for
        cr, uid, context = env.cr, env.uid, env.context  # noqa: F841 - where Odoo 17's _() reads the language
        return {
            "quants": _("%(count)s stock lines show a negative quantity in an internal location.", count=count),
            "locations": _("%(count)s locations hold a negative quantity of something.", count=count),
        }[key]


class UserAccess(Check):
    key = "user_access"
    needs = ("res.users",)

    def run(self, env, clock):
        Users = env["res.users"]
        rel = Users._fields["group_ids" if "group_ids" in Users._fields else "groups_id"]
        table, user_col, group_col = rel.relation, rel.column1, rel.column2
        found = []
        baseline = group_ids(env, "base.group_user")
        count, capped, ids = scan(
            env, Users,
            f"t.active AND NOT t.share AND t.id <> 1 AND NOT EXISTS (SELECT 1 FROM {table} r "
            f"WHERE r.{user_col} = t.id AND r.{group_col} <> ALL(%s))", [baseline])
        if count:
            found.append(finding("no_groups", WARNING, count, capped, "res.users", ids=ids))
        cutoff = fields.Datetime.now() - timedelta(days=IDLE_DAYS)
        count, capped, ids = scan(
            env, Users,
            "t.active AND NOT t.share AND t.id <> 1 AND t.create_date < %s AND NOT EXISTS ("
            "SELECT 1 FROM res_users_log g WHERE g.create_uid = t.id AND g.create_date >= %s)", [cutoff, cutoff])
        if count:
            found.append(finding("idle", INFO, count, capped, "res.users", ids=ids))
        admin = env.ref("base.group_system", raise_if_not_found=False)
        if admin:
            kept = [1] + [user.id for user in (env.ref("base.user_admin", raise_if_not_found=False) or Users)]
            count, capped, ids = scan(
                env, Users,
                f"t.active AND NOT t.share AND t.id <> ALL(%s) AND EXISTS (SELECT 1 FROM {table} r "
                f"WHERE r.{user_col} = t.id AND r.{group_col} = %s)", [kept, admin.id])
            if count:
                found.append(finding("admins", WARNING, count, capped, "res.users", ids=ids))
        return found

    def texts(self, env, key, count, sample):
        cr, uid, context = env.cr, env.uid, env.context  # 17: what _() looks for
        cr, uid, context = env.cr, env.uid, env.context  # noqa: F841 - where Odoo 17's _() reads the language
        return {
            "no_groups": _("%(count)s internal users have no access group beyond the basic one.", count=count),
            "idle": _("%(count)s users have not logged in for %(days)s days and are still active.",
                          count=count, days=IDLE_DAYS),
            "admins": _("%(count)s users have administration rights and are not the administrator.", count=count),
        }[key]


class CompanyConsistency(Check):
    key = "company_consistency"
    needs = ("res.company",)

    # Where Odoo requires the company of a record and of what it points at to agree
    LINKS = (
        ("account.move", "journal_id"), ("account.move", "partner_id"),
        ("sale.order", "partner_id"), ("sale.order", "warehouse_id"),
        ("purchase.order", "partner_id"), ("purchase.order", "picking_type_id"),
        ("stock.picking", "partner_id"), ("stock.picking", "picking_type_id"),
        ("account.payment", "journal_id"),
    )

    def run(self, env, clock):
        found = []
        for model_name, name in self.LINKS:
            if clock.over():
                break
            if model_name not in env.registry:
                continue
            model = env[model_name]
            field = model._fields.get(name)
            if (field is None or field.type != "many2one" or not field.store or field.company_dependent
                    or "company_id" not in model._fields or not model._fields["company_id"].store):
                continue
            target = env[field.comodel_name]
            company = target._fields.get("company_id")
            if company is None or not company.store:
                continue
            count, capped, ids = scan(
                env, model,
                f'EXISTS (SELECT 1 FROM "{target._table}" c WHERE c.id = t."{name}" AND c.company_id IS NOT NULL '
                f"AND t.company_id IS NOT NULL AND c.company_id <> t.company_id)")
            if count:
                found.append(finding(f"{model_name}.{name}", CRITICAL, count, capped, model_name, ids=ids))
        return found

    def texts(self, env, key, count, sample):
        cr, uid, context = env.cr, env.uid, env.context  # 17: what _() looks for
        cr, uid, context = env.cr, env.uid, env.context  # noqa: F841 - where Odoo 17's _() reads the language
        model_name, name = key.split(".", 1)
        model = env[model_name]
        return _("%(count)s %(model)s have a %(field)s that belongs to another company.",
                     count=count, model=model._description, field=model._fields[name]._description_string(env))


class ScheduledActions(Check):
    key = "scheduled_actions"
    needs = ("ir.cron",)

    def available(self, env):
        # Older versions do not record that a run failed
        return super().available(env) and "failure_count" in env["ir.cron"]._fields

    def run(self, env, clock):
        # A job stopped by its failures is archived: the archived ones count
        domain = [("failure_count", ">", 0), ("active", "in", (True, False))]
        count, capped = counted(env["ir.cron"], domain)
        return [finding("failing", WARNING, count, capped, "ir.cron", domain=domain)] if count else []

    def texts(self, env, key, count, sample):
        cr, uid, context = env.cr, env.uid, env.context  # 17: what _() looks for
        cr, uid, context = env.cr, env.uid, env.context  # noqa: F841 - where Odoo 17's _() reads the language
        return _("%(count)s scheduled actions failed their last runs.", count=count)


class MailQueue(Check):
    key = "mail_queue"
    needs = ("mail.mail",)

    def run(self, env, clock):
        Mail = env["mail.mail"]
        found = []
        domain = [("state", "=", "exception")]
        count, capped = counted(Mail, domain)
        if count:
            found.append(finding("exception", WARNING, count, capped, "mail.mail", domain=domain))
        cutoff = fields.Datetime.now() - timedelta(hours=STUCK_HOURS)
        domain = [("state", "=", "outgoing"), ("create_date", "<", fields.Datetime.to_string(cutoff)),
                  "|", ("scheduled_date", "=", False), ("scheduled_date", "<", fields.Datetime.to_string(cutoff))]
        count, capped = counted(Mail, domain)
        if count:
            found.append(finding("stuck", WARNING, count, capped, "mail.mail", domain=domain))
        return found

    def texts(self, env, key, count, sample):
        cr, uid, context = env.cr, env.uid, env.context  # 17: what _() looks for
        cr, uid, context = env.cr, env.uid, env.context  # noqa: F841 - where Odoo 17's _() reads the language
        return {
            "exception": _("%(count)s emails could not be sent.", count=count),
            "stuck": _("%(count)s emails have waited more than %(hours)s hours in the outgoing queue.",
                           count=count, hours=STUCK_HOURS),
        }[key]


class OrphanAttachments(Check):
    key = "orphan_attachments"
    needs = ("ir.attachment",)

    def run(self, env, clock):
        # The latest attachments only: a full scan of the attachments is not
        # worth the wait, and orphans are made as records are deleted
        # Odoo shows an attachment to whoever may read the record it is attached
        # to, so an orphan is shown to nobody: administrators look at them
        # with sudo, and only at ids and where they point, never at a content
        if not env.user.has_group("base.group_system"):
            raise AccessError("Only administrators look for orphan attachments.")
        Attachment = env["ir.attachment"].sudo()
        sub, params = visible(Attachment, [
            ("res_model", "!=", False), ("res_id", ">", 0), ("company_id", "in", [False, *env.companies.ids])])
        env.cr.execute(
            f"SELECT t.id, t.res_model, t.res_id FROM ir_attachment t WHERE t.id IN ({sub}) "
            f"ORDER BY t.id DESC LIMIT %s", [*params, SAMPLE])
        sampled = env.cr.fetchall()
        wanted = {}
        for attachment_id, res_model, res_id in sampled:
            wanted.setdefault(res_model, {}).setdefault(res_id, []).append(attachment_id)
        orphans = []
        for res_model, targets in wanted.items():
            if clock.over():
                break
            model = env.registry.get(res_model)
            if model is None or not model._auto or model._abstract or model._transient:
                continue
            env.cr.execute(f'SELECT id FROM "{model._table}" WHERE id = ANY(%s)', [list(targets)])
            alive = {row[0] for row in env.cr.fetchall()}
            for res_id, attachment_ids in targets.items():
                if res_id not in alive:
                    orphans.extend(attachment_ids)
        if not orphans:
            return []
        return [finding("orphans", INFO, len(orphans), False, "ir.attachment", ids=sorted(orphans, reverse=True),
                        sample=len(sampled))]

    def texts(self, env, key, count, sample):
        cr, uid, context = env.cr, env.uid, env.context  # 17: what _() looks for
        cr, uid, context = env.cr, env.uid, env.context  # noqa: F841 - where Odoo 17's _() reads the language
        return _("%(count)s files, among the last %(sample)s, belong to a record that no longer exists.",
                     count=count, sample=sample)


# The list to extend: a new check is a class above and a line here
CHECKS = (
    PartnerDuplicates(), ProductDuplicates(), ProductData(), AccountSetup(), TaxSetup(), StockNegative(),
    UserAccess(), CompanyConsistency(), ScheduledActions(), MailQueue(), OrphanAttachments(),
)
from . import outliers as _outliers  # noqa: E402 - after the names it takes from this file

CHECKS = CHECKS + _outliers.CHECKS
BY_KEY = {check.key: check for check in CHECKS}
# Functions ``env -> {key: name}`` of the checks other tools register (the upgrade check)
LABEL_PROVIDERS = []


def labels(env):
    """:return: ``{check key: its name}`` in the language of ``env``"""
    names = {
        "partner_duplicates": _("Duplicate contacts"),
        "product_duplicates": _("Duplicate products"),
        "product_data": _("Product data"),
        "account_setup": _("Accounting setup"),
        "tax_setup": _("Taxes"),
        "stock_negative": _("Negative stock"),
        "user_access": _("Users and rights"),
        "company_consistency": _("Companies in agreement"),
        "scheduled_actions": _("Scheduled actions"),
        "mail_queue": _("Email queue"),
        "orphan_attachments": _("Orphan files"),
        **_outliers.labels(env),
    }
    for provider in LABEL_PROVIDERS:
        names.update(provider(env))
    return names


def run_all(env, keys=None, checks=None):
    """Every check offered here (or those of ``keys``, or the list ``checks``), each within its time, as the user
    of ``env``.

    :return: ``(findings, skipped)``: findings with the key of their check,
        and ``[{"check", "reason"}]`` for those that could not run"""
    found, skipped = [], []
    for check in checks or CHECKS:
        if (keys is not None and check.key not in keys) or not check.available(env):
            continue
        clock = Clock(CHECK_SECONDS * 2)
        try:
            with budget(env, CHECK_SECONDS):
                items = check.run(env, clock)
        except AccessError:
            skipped.append({"check": check.key, "reason": "access"})
            continue
        except Exception as error:  # noqa: BLE001 - one check must not stop the others
            reason = "timeout" if type(error).__name__ == "QueryCanceled" else "error"
            if reason == "error":
                _logger.warning("Health check %s failed: %s", check.key, error)
            skipped.append({"check": check.key, "reason": reason})
            continue
        for item in items:
            item["check"] = check.key
            found.append(item)
    return found, skipped

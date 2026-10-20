"""The compliance checklist of a country: deterministic checks, no AI.

A country pack is a JSON file in ``data/compliance`` of this module, or in
``data/nudge_compliance`` of any installed module, so that a partner adds a
country (or a sector) without touching Nudge. A pack names the countries it
is for and lists checks; a check names one of the kinds of check below, the
parameters it gives it, a severity, a title, an explanation and the official
reference, in English and Arabic (a text is a string, or ``{"en": ..., "ar": ...}``).

A pack that names a kind of check that does not exist, a parameter a kind
does not take, or a severity that is not one is refused when it is loaded,
with the name of the file and of the check. A check never changes anything:
it runs as the person who asked, in the current company, and what it finds
is stored with the health reports (``nudge.health.report`` with its ``pack``).
"""
from odoo import _
import json
import logging
import os
import re
from collections import namedtuple

from lxml import etree


from . import health
from .health import CRITICAL, INFO, WARNING, Check, counted, finding

_logger = logging.getLogger(__name__)

PACKS_HERE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "compliance")
PACKS_OF_MODULES = os.path.join("data", "nudge_compliance")
SEVERITIES = (CRITICAL, WARNING, INFO)
MISSING = "|module"
NOFIELD = "|nofield"
MAX_PARTNER_SAMPLE = 2000
NESTED_QUANTIFIER = re.compile(r"[+*}]\)?[+*{]")
CHECK_KEY = re.compile(r"[a-z][a-z0-9_]{1,60}")
COUNTRY_CODE = re.compile(r"[A-Z]{2}")

Pack = namedtuple("Pack", "id name countries reference checks source")
Entry = namedtuple("Entry", "key check severity title explanation reference params")


class PackError(ValueError):
    """A pack that cannot be loaded, and why; the message names the file and the check."""


# ----------------------------------------------------------------------
# The kinds of check a pack may use: name -> (function, {parameter: type}, required parameters)
# ----------------------------------------------------------------------
KINDS = {}


def kind(name, params, required=()):
    def register(function):
        KINDS[name] = (function, params, tuple(required))
        return function
    return register


def installed(env, names):
    return env["ir.module.module"].sudo().search_count([("name", "in", list(names)), ("state", "=", "installed")]) > 0


def missing(modules, model=None, domain=None):
    """A finding that says a module is not installed: the checklist cannot look at what it adds"""
    return finding(MISSING, INFO, 1, model="ir.module.module", domain=[("name", "in", list(modules))])


def company_fields(env, names):
    return [name for name in names if name in env["res.company"]._fields]


@kind("module_installed", {"modules": list, "all": bool}, ["modules"])
def check_module_installed(env, company, params, clock):
    modules = params["modules"]
    names = env["ir.module.module"].sudo().search([("name", "in", modules), ("state", "=", "installed")]).mapped("name")
    ok = all(name in names for name in modules) if params.get("all") else bool(names)
    return [] if ok else [missing(modules)]


@kind("company_field", {"fields": list, "pattern": str, "modules": list}, ["fields"])
def check_company_field(env, company, params, clock):
    """The company holds a value (matching the pattern, when there is one) in the first of the fields it has"""
    names = company_fields(env, params["fields"])
    if not names:
        return [missing(params.get("modules") or [])] if params.get("modules") else []
    value = (company[names[0]] or "")
    value = re.sub(r"\s+", "", value) if isinstance(value, str) else value
    if value and (not params.get("pattern") or re.fullmatch(params["pattern"], value)):
        return []
    return [finding("field", CRITICAL, 1, model="res.company", ids=[company.id])]


@kind("company_custom_field", {"field": str}, ["field"])
def check_company_custom_field(env, company, params, clock):
    """A number Odoo has no field for: the company may have added one (it is looked for by its name)"""
    name = params["field"]
    if name not in env["res.company"]._fields:
        return [finding(NOFIELD, INFO, 1, model="res.company", ids=[company.id])]
    if company[name]:
        return []
    return [finding("empty", WARNING, 1, model="res.company", ids=[company.id])]


@kind("company_fields_all", {"fields": list, "modules": list, "needs": list}, ["fields", "modules"])
def check_company_fields_all(env, company, params, clock):
    """Payroll settings of the company: every field filled when the payroll of the country is there"""
    if params.get("needs") and not installed(env, params["needs"]):
        return []
    names = company_fields(env, params["fields"])
    if len(names) < len(params["fields"]):
        return [missing(params["modules"])]
    empty = [name for name in names if not company[name]]
    return [finding("field", WARNING, len(empty), model="res.company", ids=[company.id])] if empty else []


@kind("partner_field", {"field": str, "pattern": str, "customers_only": bool}, ["field"])
def check_partner_field(env, company, params, clock):
    """Companies of the country of the company that miss a value, or hold one that does not match"""
    Partner = env["res.partner"]
    name = params["field"]
    if name not in Partner._fields:
        return []
    domain = [("is_company", "=", True), ("active", "=", True)]
    if company.country_id:
        domain.append(("country_id", "=", company.country_id.id))
    if params.get("customers_only", True) and "customer_rank" in Partner._fields:
        domain.append(("customer_rank", ">", 0))
    partners = Partner.search(domain, limit=MAX_PARTNER_SAMPLE)
    pattern = params.get("pattern")
    wrong = partners.filtered(lambda partner: not partner[name] or (
        pattern and not re.fullmatch(pattern, re.sub(r"\s+", "", partner[name] or ""))))
    if not wrong:
        return []
    return [finding("field", WARNING, len(wrong), model="res.partner", ids=wrong.ids, sample=len(partners))]


@kind("tax_rate", {"rate": float, "use": str, "modules": list}, ["rate"])
def check_tax_rate(env, company, params, clock):
    """An active percent tax at the rate of the country, for sales"""
    if "account.tax" not in env:
        return [missing(params.get("modules") or ["account"])]
    domain = [("amount_type", "=", "percent"), ("amount", "=", params["rate"]), ("active", "=", True),
              ("type_tax_use", "=", params.get("use", "sale"))]
    if "company_id" in env["account.tax"]._fields:
        domain.append(("company_id", "in", [company.id, False]))
    if env["account.tax"].search_count(domain, limit=1):
        return []
    return [finding("none", CRITICAL, 1, model="account.tax", domain=[("type_tax_use", "=", params.get("use", "sale"))])]


@kind("tax_on_products", {"rate": float}, ["rate"])
def check_tax_on_products(env, company, params, clock):
    """Products that are sold and carry no tax at the rate"""
    if "account.tax" not in env or "taxes_id" not in env["product.template"]._fields:
        return []
    taxes = env["account.tax"].search([("amount_type", "=", "percent"), ("amount", "=", params["rate"]),
                                       ("type_tax_use", "=", "sale"), ("active", "=", True)])
    if not taxes:
        return []
    domain = [("sale_ok", "=", True), ("taxes_id", "not in", taxes.ids)]
    count, capped = counted(env["product.template"], domain)
    return [finding("products", WARNING, count, capped, "product.template", domain=domain)] if count else []


@kind("no_vat", {"modules": list}, [])
def check_no_vat(env, company, params, clock):
    """Where no VAT is in force: sales taxes with a rate would be charged to customers without a legal basis"""
    if "account.tax" not in env:
        return []
    domain = [("amount_type", "=", "percent"), ("amount", ">", 0), ("type_tax_use", "=", "sale"), ("active", "=", True)]
    count, capped = counted(env["account.tax"], domain)
    return [finding("taxes", WARNING, count, capped, "account.tax", domain=domain)] if count else []


@kind("company_currency", {"code": str}, ["code"])
def check_company_currency(env, company, params, clock):
    if company.currency_id.name == params["code"]:
        return []
    return [finding("currency", WARNING, 1, model="res.company", ids=[company.id])]


@kind("invoice_template", {"terms": list}, ["terms"])
def check_invoice_template(env, company, params, clock):
    """The printed invoice shows what the tax authority asks for: each term must appear in the template"""
    if "account.move" not in env:
        return [missing(["account"])]
    view = env.ref("account.report_invoice_document", raise_if_not_found=False)
    if not view:
        return []
    # the template as it is printed: the standard one and the views that extend it
    try:
        arch = etree.tostring(view.sudo()._get_combined_arch(), encoding="unicode")
    except Exception:  # noqa: BLE001
        arch = str(view.sudo().arch_db or "")
    absent = [term for term in params["terms"] if term not in arch]
    if not absent:
        return []
    return [finding("terms", WARNING, len(absent), model="ir.ui.view", ids=[view.id])]


@kind("field_present", {"model": str, "fields": list, "modules": list}, ["model", "fields", "modules"])
def check_field_present(env, company, params, clock):
    """A field the law needs exists in the database: it comes with a module that may not be installed"""
    model = env.get(params["model"])
    if model is None or not all(name in model._fields for name in params["fields"]):
        return [missing(params["modules"])]
    return []


@kind("invoices_with", {"fields": list, "modules": list}, ["fields", "modules"])
def check_invoices_with(env, company, params, clock):
    """Posted customer invoices that miss a value the e-invoicing module writes"""
    Move = env.get("account.move")
    if Move is None or not all(name in Move._fields and Move._fields[name].store for name in params["fields"]):
        return [missing(params["modules"])]
    domain = [("move_type", "in", ("out_invoice", "out_refund")), ("state", "=", "posted"),
              ("company_id", "=", company.id)]
    domain += ["|"] * (len(params["fields"]) - 1) + [(name, "=", False) for name in params["fields"]]
    count, capped = counted(Move, domain)
    return [finding("invoices", CRITICAL, count, capped, "account.move", domain=domain)] if count else []


@kind("language_active", {"prefix": str}, ["prefix"])
def check_language_active(env, company, params, clock):
    Lang = env["res.lang"].sudo()
    if Lang.search_count([("active", "=", True), ("code", "=like", params["prefix"] + "%")], limit=1):
        return []
    return [finding("language", WARNING, 1, model="res.lang", domain=[("code", "=like", params["prefix"] + "%")])]


@kind("employee_field", {"fields": list, "modules": list}, ["fields"])
def check_employee_field(env, company, params, clock):
    """Active employees of the company with nothing in the first of the fields the database has"""
    Employee = env.get("hr.employee")
    if Employee is None:
        return [missing(params.get("modules") or ["hr"])]
    names = [name for name in params["fields"] if name in Employee._fields]
    if not names:
        return [missing(params.get("modules") or ["hr"])]
    domain = [(names[0], "=", False), ("company_id", "=", company.id)]
    count, capped = counted(Employee, domain)
    return [finding("employees", WARNING, count, capped, "hr.employee", domain=domain)] if count else []


@kind("employees_of_module", {"fields": list, "modules": list}, ["fields", "modules"])
def check_employees_of_module(env, company, params, clock):
    """Employee fields of the payroll localisation: the module must be there, then the fields filled"""
    if not installed(env, params["modules"]):
        return [missing(params["modules"])]
    return check_employee_field(env, company, params, clock)


@kind("note", {}, [])
def check_note(env, company, params, clock):
    """Something the person has to know, that no data can show"""
    return [finding("note", INFO, 1)]


# ----------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------
def _text(value, where):
    if isinstance(value, str):
        return {"en": value}
    if isinstance(value, dict) and isinstance(value.get("en"), str) and all(isinstance(v, str) for v in value.values()):
        return value
    raise PackError(f"{where}: a text is a string, or {{\"en\": ..., \"ar\": ...}} with at least \"en\".")


def load_pack(data, source="pack"):
    """:return: the :class:`Pack` made from the JSON object ``data``
    :raise PackError: when anything in it is not as described above, naming ``source`` and the check"""
    if not isinstance(data, dict):
        raise PackError(f"{source}: a pack is a JSON object.")
    pack_id = data.get("id")
    if not isinstance(pack_id, str) or not CHECK_KEY.fullmatch(pack_id):
        raise PackError(f"{source}: the pack needs an \"id\" of lowercase letters, digits and underscores.")
    countries = data.get("countries")
    if not isinstance(countries, list) or not countries or not all(
            isinstance(code, str) and COUNTRY_CODE.fullmatch(code) for code in countries):
        raise PackError(f"{source}: \"countries\" is a list of two-letter country codes such as \"AE\".")
    checks = data.get("checks")
    if not isinstance(checks, list) or not checks:
        raise PackError(f"{source}: the pack lists no check.")
    entries, seen = [], set()
    for index, raw in enumerate(checks, start=1):
        if not isinstance(raw, dict):
            raise PackError(f"{source}: check {index} is not an object.")
        key = raw.get("key")
        where = f"{source}, check {key or index!r}"
        if not isinstance(key, str) or not CHECK_KEY.fullmatch(key):
            raise PackError(f"{where}: \"key\" is lowercase letters, digits and underscores.")
        if key in seen:
            raise PackError(f"{where}: the key is used twice in the pack.")
        seen.add(key)
        name = raw.get("check")
        if name not in KINDS:
            raise PackError(
                f"{where}: unknown check {name!r}. The kinds of check are: {', '.join(sorted(KINDS))}.")
        severity = raw.get("severity")
        if severity not in SEVERITIES:
            raise PackError(f"{where}: severity {severity!r} is not one of {', '.join(SEVERITIES)}.")
        _function, allowed, required = KINDS[name]
        params = raw.get("params") or {}
        if not isinstance(params, dict):
            raise PackError(f"{where}: \"params\" is an object.")
        for parameter, value in params.items():
            if parameter not in allowed:
                raise PackError(f"{where}: the check {name!r} takes no parameter {parameter!r} "
                                f"(it takes: {', '.join(allowed) or 'none'}).")
            expected = allowed[parameter]
            if expected is float and isinstance(value, (int, float)) and not isinstance(value, bool):
                continue
            if not isinstance(value, expected):
                raise PackError(f"{where}: the parameter {parameter!r} is a {expected.__name__}.")
        absent = [parameter for parameter in required if parameter not in params]
        if absent:
            raise PackError(f"{where}: the check {name!r} needs the parameter {', '.join(absent)}.")
        if "pattern" in params:
            if len(str(params["pattern"])) > 200 or NESTED_QUANTIFIER.search(str(params["pattern"])):
                raise PackError(f"{where}: the pattern is too long or repeats a repeat, which can stall a worker.")
            try:
                re.compile(params["pattern"])
            except re.error as error:
                raise PackError(f"{where}: the pattern is not valid: {error}.") from None
        entries.append(Entry(
            key, name, severity, _text(raw.get("title"), where + ", title"),
            _text(raw.get("explanation"), where + ", explanation"),
            _text(raw.get("reference", ""), where + ", reference") if raw.get("reference") else {"en": ""}, params))
    return Pack(pack_id, _text(data.get("name", pack_id), f"{source}, name"), [code for code in countries],
                _text(data.get("reference", ""), f"{source}, reference") if data.get("reference") else {"en": ""},
                entries, source)


def load_file(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as error:
        raise PackError(f"{os.path.basename(path)}: cannot be read as JSON ({error}).") from None
    return load_pack(data, os.path.basename(path))


_CACHE = {}


def _files(env):
    folders = [PACKS_HERE] + env["ir.module.module"]._nudge_pack_folders()
    files = []
    for folder in folders:
        try:
            files += [os.path.join(folder, name) for name in sorted(os.listdir(folder)) if name.endswith(".json")]
        except OSError:
            continue
    return files


def load_all(env):
    """:return: ``({pack id: Pack}, [(file, why it was refused)])``: every pack that loads, and the others"""
    packs, refused = {}, []
    for path in _files(env):
        try:
            stamp = os.path.getmtime(path)
        except OSError:
            continue
        cached = _CACHE.get(path)
        if not cached or cached[0] != stamp:
            try:
                cached = (stamp, load_file(path), None)
            except PackError as error:
                _logger.warning("Nudge compliance: %s", error)
                cached = (stamp, None, str(error))
            _CACHE[path] = cached
        if cached[1] is None:
            refused.append((os.path.basename(path), cached[2]))
        elif cached[1].id in packs:
            refused.append((os.path.basename(path), f"the pack id {cached[1].id!r} is used by another file."))
        else:
            packs[cached[1].id] = cached[1]
    return packs, refused


def packs_for(env, company=None):
    """:return: the packs for the country of the company, in the order of their names"""
    company = company or env.company
    code = company.country_id.code
    packs, _refused = load_all(env)
    return sorted((pack for pack in packs.values() if code and code in pack.countries), key=lambda pack: pack.id)


def pick(texts, env):
    """:return: a text of the pack in the language of ``env``, the English one when it has no other"""
    lang = (env.lang or "en_US").split("_")[0]
    return texts.get(lang) or texts.get("en") or next(iter(texts.values()), "")


# ----------------------------------------------------------------------
# Running
# ----------------------------------------------------------------------
def run_pack(env, pack_id):
    """Run the checks of a pack for the current company, as the person.

    :return: ``(findings, skipped)`` as ``health.run_all`` does; a finding has the key ``<pack>.<check>`` and, for
        a module that is not installed, ``<pack>.<check>|module``"""
    packs, _refused = load_all(env)
    pack = packs.get(pack_id)
    if pack is None:
        raise PackError(f"There is no pack {pack_id!r}.")
    company = env.company
    found, skipped = [], []
    for entry in pack.checks:
        function = KINDS[entry.check][0]
        clock = health.Clock(health.CHECK_SECONDS * 2)
        try:
            with health.budget(env, health.CHECK_SECONDS):
                items = function(env, company, dict(entry.params), clock)
        except health.AccessError:
            skipped.append({"check": "compliance", "reason": "access", "entry": f"{pack_id}.{entry.key}"})
            continue
        except Exception as error:  # noqa: BLE001 - one check must not stop the others
            _logger.warning("Compliance check %s.%s failed: %s", pack_id, entry.key, error)
            skipped.append({"check": "compliance", "reason": "error", "entry": f"{pack_id}.{entry.key}"})
            continue
        for item in items:
            suffix = item["key"] if item["key"] in (MISSING, NOFIELD) else ""
            item["check"] = "compliance"
            item["key"] = f"{pack_id}.{entry.key}{suffix}"
            item["severity"] = entry.severity if not suffix else INFO
            found.append(item)
    return found, skipped


class ComplianceCheck(Check):
    """How the findings of a pack are told: the title and the explanation of the check of the pack"""

    key = "compliance"

    def available(self, env):
        return True

    def run(self, env, clock):  # never run with the general check
        return []

    def _entry(self, env, key):
        pack_id, _dot, rest = key.partition(".")
        name = rest.removesuffix(MISSING).removesuffix(NOFIELD)
        packs, _refused = load_all(env)
        pack = packs.get(pack_id)
        entry = next((entry for entry in pack.checks if entry.key == name), None) if pack else None
        return pack, entry, rest.endswith(MISSING), rest.endswith(NOFIELD)

    def texts(self, env, key, count, sample):
        cr, uid, context = env.cr, env.uid, env.context  # noqa: F841 - where Odoo 17's _() reads the language
        pack, entry, is_missing, no_field = self._entry(env, key)
        if not entry:
            return key
        title = pick(entry.title, env)
        if no_field:
            return _("%(title)s — Odoo has no field %(field)s on the company: add one, or keep the number in the "
                         "notes of the company.", title=title, field=entry.params["field"])
        if is_missing:
            modules = ", ".join(self._modules(entry)) or "?"
            return _("%(title)s — cannot be checked: the module %(modules)s is not installed.",
                         title=title, modules=modules)
        if entry.check == "note":
            return title
        if entry.check in ("company_field", "company_custom_field", "company_currency", "language_active",
                           "tax_rate"):
            return _("%(title)s — not met.", title=title)
        return _("%(title)s — not met: %(count)s concerned.", title=title, count=count)

    @staticmethod
    def _modules(entry):
        return entry.params.get("modules") or ([entry.params["model"]] if "model" in entry.params else [])

    def detail(self, env, key):
        """:return: the explanation and the official reference, in the language of ``env``"""
        pack, entry, is_missing, _no_field = self._entry(env, key)
        if not entry:
            return ""
        parts = [pick(entry.explanation, env)]
        if is_missing:
            parts.append(_("Install: %s", ", ".join(self._modules(entry))))
        reference = pick(entry.reference, env) or pick(pack.reference, env)
        if reference:
            parts.append(_("Reference: %s", reference))
        parts.append(_("A checklist, not legal advice; confirm with the authority."))
        return "\n".join(part for part in parts if part)


health.BY_KEY["compliance"] = ComplianceCheck()

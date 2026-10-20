"""The first-setup assistant: a new database set up in a conversation.

On a database nobody has set up yet (one company with the default name, no
chart of accounts, few users) Nudge offers “Set up this database with me”.
The conversation collects, one step at a time, the company, its country
(which decides the fiscal localisation to install), currency, languages,
time zone, logo, the apps for the kind of business, other companies,
warehouses, users with their role, the fiscal year and the number formats.
Every step is one card that says exactly what will change; nothing is done
before the person says yes, and what can be taken back has an Undo (what
was made is archived, never deleted). Nudge never configures the outgoing
mail server: it points at it.

Two ways through the same tools: the provider, which reads free text and
calls ``prepare_setup_step`` itself, and a path with no AI (``converse``),
which asks, reads the answer with rules and shows the same cards.
"""
import base64
import calendar
import datetime
import re

import pytz

from odoo import Command
from odoo.exceptions import UserError

from ..llm import arabic
from . import base, setup_sentence
from .records import Refused, refusing

DEFAULT_NAMES = {"my company", "yourcompany", "your company"}
PARAM_DONE = "nudge.setup_finished"
FEW_USERS = 3
MAX_ITEMS = 10
MAX_LOGO_BYTES = 2 * 1024 * 1024

ORDER = ("company", "country", "currency", "languages", "timezone", "logo", "apps", "companies", "warehouses",
         "users", "fiscal", "formats")
# The steps the assistant can do by itself with what it knows, when the person says “the rest, with the defaults”
DEFAULTED = ("languages", "timezone", "apps", "fiscal", "formats")
FIRST_THREE = ("company", "country", "currency")

# Country: the currency, the time zone and the languages it usually works in
COUNTRIES = {
    "AE": {"currency": "AED", "tz": "Asia/Dubai", "langs": ["ar_001"]},
    "SA": {"currency": "SAR", "tz": "Asia/Riyadh", "langs": ["ar_001"]},
    "QA": {"currency": "QAR", "tz": "Asia/Qatar", "langs": ["ar_001"]},
    "KW": {"currency": "KWD", "tz": "Asia/Kuwait", "langs": ["ar_001"]},
    "BH": {"currency": "BHD", "tz": "Asia/Bahrain", "langs": ["ar_001"]},
    "OM": {"currency": "OMR", "tz": "Asia/Muscat", "langs": ["ar_001"]},
    "EG": {"currency": "EGP", "tz": "Africa/Cairo", "langs": ["ar_001"]},
    "JO": {"currency": "JOD", "tz": "Asia/Amman", "langs": ["ar_001"]},
    "LB": {"currency": "USD", "tz": "Asia/Beirut", "langs": ["ar_001"]},
    "IQ": {"currency": "IQD", "tz": "Asia/Baghdad", "langs": ["ar_001"]},
    "TR": {"currency": "TRY", "tz": "Europe/Istanbul", "langs": ["tr_TR"]},
    "IN": {"currency": "INR", "tz": "Asia/Kolkata", "langs": []},
    "PK": {"currency": "PKR", "tz": "Asia/Karachi", "langs": []},
    "GB": {"currency": "GBP", "tz": "Europe/London", "langs": []},
    "US": {"currency": "USD", "tz": "America/New_York", "langs": []},
}
# The module of the fiscal localisation when its name is not l10n_<country>
LOCALISATION_EXCEPTIONS = {"GB": "l10n_uk"}
# Countries whose year ends elsewhere than on 31 December: (day, month)
FISCAL_YEAR_ENDS = {"IN": (31, 3), "PK": (30, 6), "JP": (31, 3), "AU": (30, 6), "NZ": (31, 3)}
# Date format of the countries that write the month first
MONTH_FIRST = {"US", "PH", "CA"}

# What a role is allowed to do: the groups of the apps installed (a group of an app that is not there is left out)
ROLES = {
    "salesperson": ("sales_team.group_sale_salesman",),
    "accountant": ("account.group_account_user",),
    "warehouse": ("stock.group_stock_user",),
    "hr": ("hr.group_hr_user",),
    "manager": ("sales_team.group_sale_manager", "purchase.group_purchase_manager", "account.group_account_manager",
                "stock.group_stock_manager", "hr.group_hr_manager", "project.group_project_manager"),
    "admin": ("base.group_system",),
}

# The apps: module, what it brings
APPS = {
    "sale_management": "Sales", "purchase": "Purchase", "stock": "Inventory", "account": "Invoicing",
    "project": "Project", "hr": "Employees", "website": "Website", "crm": "CRM", "mrp": "Manufacturing",
    "point_of_sale": "Point of Sale", "hr_holidays": "Time Off",
}
KIND_APPS = {
    "trading": ("sale_management", "purchase", "stock", "account"),
    "services": ("sale_management", "project", "account", "hr"),
    "retail": ("point_of_sale", "stock", "purchase", "account"),
    "manufacturing": ("mrp", "stock", "purchase", "sale_management", "account"),
    "contracting": ("project", "purchase", "account", "hr"),
    "restaurant": ("point_of_sale", "stock", "purchase", "account"),
    "online": ("website", "sale_management", "stock", "account"),
}
DEFAULT_KIND = "trading"
APP_WORDS = {
    "sale_management": ("sales", "sale", "quotations", "مبيعات", "المبيعات", "عروض اسعار"),
    "purchase": ("purchase", "purchasing", "مشتريات", "المشتريات"),
    "stock": ("inventory", "stock", "warehouse", "مخزون", "المخزون", "مخازن", "مستودعات"),
    "account": ("accounting", "invoicing", "invoices", "accounts", "محاسبه", "المحاسبه", "فواتير", "الفواتير"),
    "project": ("project", "projects", "مشاريع", "المشاريع", "مشروع"),
    "hr": ("hr", "employees", "payroll", "موظفين", "الموظفين", "موارد بشريه", "الموارد البشريه"),
    "website": ("website", "موقع", "الموقع"),
    "crm": ("crm", "leads", "عملاء محتملين", "فرص"),
    "mrp": ("manufacturing", "mrp", "تصنيع", "التصنيع"),
    "point_of_sale": ("point of sale", "pos", "cashier", "نقطه بيع", "نقاط البيع", "كاشير"),
    "hr_holidays": ("time off", "leaves", "holidays", "اجازات", "الاجازات"),
}


# ----------------------------------------------------------------------
# Words for the person
# ----------------------------------------------------------------------
def app_brings(env):
    return {
        "sale_management": env._("quotations, sales orders, price lists and customer invoices"),
        "purchase": env._("requests for quotation, purchase orders and vendor bills"),
        "stock": env._("products, warehouses, receipts, deliveries and stock levels"),
        "account": env._("invoices, bills, payments, taxes and the accounting reports"),
        "project": env._("projects, tasks and timesheets"),
        "hr": env._("employees, departments and contracts"),
        "website": env._("a website, built with blocks, with pages and a contact form"),
        "crm": env._("leads and opportunities in a pipeline"),
        "mrp": env._("bills of materials and manufacturing orders"),
        "point_of_sale": env._("a till for a shop or a restaurant, working with stock and accounting"),
        "hr_holidays": env._("leave requests, approval and balances"),
    }


def role_labels(env):
    return {
        "salesperson": env._("Salesperson"), "accountant": env._("Accountant"), "warehouse": env._("Warehouse"),
        "hr": env._("HR"), "manager": env._("Manager of the apps"), "admin": env._("Administrator (all rights)"),
    }


def step_titles(env):
    return {
        "company": env._("Company"), "country": env._("Country and fiscal localisation"),
        "currency": env._("Currency"), "languages": env._("Languages"), "timezone": env._("Time zone"),
        "logo": env._("Company logo"), "apps": env._("Apps"), "companies": env._("Other companies"),
        "warehouses": env._("Warehouses"), "users": env._("Users"), "fiscal": env._("Fiscal year and lock date"),
        "formats": env._("Number and date formats"), "remaining": env._("All the remaining steps with the defaults"),
        "summary": env._("Finish the setup"),
    }


def _arrow(before, after):
    if before in (None, False, "") or before == after:
        return after
    return f"{before} → {after}"


def _line(label, value, before=None):
    return {"label": label, "value": _arrow(before, value) if before is not None else value}


def _guard(env):
    """:raise Refused: for anyone but an administrator the setup is open to"""
    if not env["nudge.conversation"]._can_administer():
        raise Refused("The setup of a database is done by an administrator, when the setup is open to them: "
                      "Nudge changes nothing for anyone else. Say so, and who can do it.")


# ----------------------------------------------------------------------
# Is the database a new one?
# ----------------------------------------------------------------------
def _plain_name(name):
    return re.sub(r"\s*\(.*\)\s*$", "", name or "").strip().lower()


def detect(env):
    """:return: ``{"fresh", "reasons"}``: a database that nobody has set up has one company with the
    default name, no chart of accounts and a few users. Only an administrator is told; for others
    it is never fresh"""
    user = env.user
    if not (user.has_group("base.group_system") and env["nudge.conversation"]._is_admin_allowed()):
        return {"fresh": False, "reasons": {}}
    # A database of several companies was set up: one query says so, before the rest is read
    if env["res.company"].with_context(active_test=False).search_count([], limit=2) != 1:
        return {"fresh": False, "reasons": {"one_company": False}}
    companies = env["res.company"].with_context(active_test=False).search([])
    name = _plain_name(companies[:1].name)
    if "chart_template" in env["res.company"]._fields:
        has_chart = bool(companies.filtered("chart_template"))
    else:
        has_chart = False
    users = env["res.users"].search_count([("share", "=", False)])
    reasons = {
        "one_company": len(companies) == 1,
        "default_name": name in DEFAULT_NAMES,
        "no_chart": not has_chart,
        "few_users": users <= FEW_USERS,
    }
    return {"fresh": all(reasons.values()), "reasons": reasons}


def offered(env):
    """:return: whether Nudge offers the setup: the database is a new one and the setup was not finished"""
    if env["ir.config_parameter"].sudo().get_param(PARAM_DONE) == "yes":
        return False
    return detect(env)["fresh"]


# ----------------------------------------------------------------------
# What is known
# ----------------------------------------------------------------------
def find_country(env, text):
    """:return: the ``res.country`` a name, a code or a city says, or an empty recordset"""
    Country = env["res.country"]
    text = (text or "").strip()
    if not text:
        return Country
    if re.fullmatch(r"[A-Za-z]{2}", text):
        found = Country.search([("code", "=", text.upper())], limit=1)
        if found:
            return found
    codes = setup_sentence.find_codes(text, setup_sentence.COUNTRY_WORDS)
    if codes:
        return Country.search([("code", "=", codes[0])], limit=1)
    found = Country.search([("name", "=ilike", text)], limit=1) or Country.search([("name", "ilike", text)], limit=2)
    return found if len(found) == 1 else Country


def find_currency(env, text, country=None):
    """:return: the ``res.currency`` the text says (a code, a name; “riyal” by the country), or empty"""
    Currency = env["res.currency"].with_context(active_test=False)
    text = (text or "").strip()
    if re.fullmatch(r"[A-Za-z]{3}", text):
        found = Currency.search([("name", "=", text.upper())], limit=1)
        if found:
            return found
    codes = setup_sentence.find_codes(text, setup_sentence.CURRENCY_WORDS)
    if country and codes:
        own = (COUNTRIES.get(country.code) or {}).get("currency")
        if own in codes:
            codes = [own]
    return Currency.search([("name", "=", codes[0])], limit=1) if codes else Currency


def find_languages(env, texts):
    """:return: ``(languages, not found)``: ``res.lang`` records for codes or names"""
    Lang = env["res.lang"].with_context(active_test=False)
    found, missing = Lang, []
    for item in texts:
        item = str(item).strip()
        codes = setup_sentence.find_codes(item, setup_sentence.LANGUAGE_WORDS) or [item]
        lang = Lang
        for code in codes:
            lang = Lang.search([("code", "=", code)], limit=1) or Lang.search([("iso_code", "=", code[:2])], limit=1) \
                or Lang.search([("name", "ilike", code)], limit=1)
            if lang:
                break
        if lang:
            found |= lang
        else:
            missing.append(item)
    return found, missing


def find_timezone(text, country=None):
    """:return: the time zone a place or a name says, or ''"""
    text = (text or "").strip()
    if text in pytz.all_timezones_set:
        return text
    for key in setup_sentence.find_codes(text, {name: (name,) for name in setup_sentence.CITY_ZONES}):
        return setup_sentence.CITY_ZONES[key]
    folded = arabic.fold(text).replace(" ", "_")
    for zone in pytz.all_timezones:
        if folded and zone.lower().endswith("/" + folded.lower()):
            return zone
    return ""


def country_zone(country):
    own = (COUNTRIES.get(country.code) or {}).get("tz")
    if own:
        return own
    zones = pytz.country_timezones.get(country.code or "", [])
    return zones[0] if zones else ""


def localisation_module(env, country):
    """:return: the module of the fiscal localisation of the country, or an empty recordset"""
    if not country:
        return env["ir.module.module"]
    name = LOCALISATION_EXCEPTIONS.get(country.code) or f"l10n_{country.code.lower()}"
    return env["ir.module.module"].search([("name", "=", name)], limit=1)


def app_modules(env, names):
    """:return: ``{technical name: module record}`` of the apps named that this Odoo has"""
    modules = env["ir.module.module"].search([("name", "in", list(names))])
    return {module.name: module for module in modules}


def installable(env, name):
    """:return: whether ``name`` is one of the apps of this assistant or the fiscal localisation of a country.
    No other name is ever installed from here, whoever asks."""
    if name in APPS:
        return True
    if name in LOCALISATION_EXCEPTIONS.values():
        return True
    found = re.fullmatch(r"l10n_([a-z]{2})", str(name))
    return bool(found) and bool(env["res.country"].search_count([("code", "=", found.group(1).upper())]))


def install_modules(env, names):
    """Install the modules, as the administrator, the way the Settings screen does. Odoo loads the
    chart of accounts of a localisation by itself when it is installed on a company that has none."""
    refused = [name for name in names if not installable(env, name)]
    if refused:
        raise UserError(env._("Nudge installs only the apps it offers and the fiscal localisation of a country: %s",
                              ", ".join(map(str, refused))))
    modules = env["ir.module.module"].search([("name", "in", list(names)), ("state", "=", "uninstalled")])
    if modules:
        modules.button_immediate_install()
    return modules.mapped("name")


def groups_field(env):
    return "group_ids" if "group_ids" in env["res.users"]._fields else "groups_id"


def snapshot(record, names):
    from .plans import snapshot as plans_snapshot  # noqa: PLC0415

    return plans_snapshot(record, names)


def _written(record, values):
    """:return: the undo entry of a write: what the fields held before, and after"""
    names = list(values)
    before = snapshot(record, names)
    record.write(values)
    return {"before": before, "after": snapshot(record, names)}


def _created(record):
    return {"created": True, "after": snapshot(record, ["write_date"])}


def _merge(undo, other):
    for model, entries in (other or {}).items():
        undo.setdefault(model, {}).update(entries)
    return undo


def logo_attachment(env, attachment_id=None):
    """:return: the picture attached to the conversation, or the one named: ``ir.attachment``"""
    Attachment = env["ir.attachment"]
    if attachment_id:
        attachment = Attachment.browse(int(attachment_id)).exists()
        if not attachment or not attachment.has_access("read"):
            raise Refused("There is no file with this id that this person can read.")
        return attachment
    conversation_id = env.context.get("nudge_conversation_id")
    message = env["nudge.message"].sudo().search([
        ("conversation_id", "=", conversation_id or 0), ("role", "=", "user"), ("attachment_id", "!=", False)],
        order="id desc", limit=1) if conversation_id else None
    return message.attachment_id if message else Attachment


# ----------------------------------------------------------------------
# The steps: each reads what is asked into a card (nothing is done), and applies it after the yes
# ----------------------------------------------------------------------
class Step:
    key = ""

    def read(self, env, answers):
        """:return: ``{"data": what is stored, "lines": the card, "names": short text}``
        :raise Refused: in words, when it cannot be done"""
        raise NotImplementedError

    def apply(self, env, data):
        """:return: ``{"text", "undo", "install": [modules to install once everything else is done]}``"""
        raise NotImplementedError

    def default(self, env, state):
        """:return: the answers of the defaults, or None when there is none"""
        return None

    def question(self, env, state):
        raise NotImplementedError

    def parse(self, env, text, state):
        """:return: the answers read from free text, or None"""
        raise NotImplementedError


def _company(env):
    return env.company


class CompanyStep(Step):
    key = "company"
    FIELDS = ("name", "vat", "street", "street2", "city", "zip", "phone", "email", "website", "company_registry")

    def question(self, env, state):
        return env._(
            "What is the name of your company? You can add, in the same message, its tax number (TRN or VAT), "
            "address, phone and email, for example: “Raghad Trading LLC, TRN 100123456700003, Sheikh Zayed Road, "
            "Dubai”.")

    def parse(self, env, text, state):
        return setup_sentence.company_answer(text)

    def read(self, env, answers):
        company = _company(env)
        values = {}
        for name in self.FIELDS:
            value = answers.get(name)
            if isinstance(value, str) and value.strip():
                values[name] = " ".join(value.split())
        if not values:
            raise Refused("Give at least the name of the company.")
        if "name" in values and not 2 <= len(values["name"]) <= 120:
            raise Refused("The name of the company is between 2 and 120 characters.")
        if "email" in values and not setup_sentence.EMAIL.fullmatch(values["email"]):
            raise Refused(f"{values['email']!r} is not an email address.")
        if "vat" in values:
            values["vat"] = re.sub(r"\s+", "", values["vat"]).upper()
        fields = company._fields
        labels = {name: fields[name].string for name in values if name in fields}
        values = {name: value for name, value in values.items() if name in fields}
        lines = [_line(labels[name], value, company[name] or None) for name, value in values.items()]
        return {"data": {"values": values}, "lines": lines, "names": values.get("name", company.name)}

    def apply(self, env, data):
        company = _company(env)
        undo = {"res.company": {str(company.id): _written(company, data["values"])}}
        return {"text": env._("The company is now %s.", company.name), "undo": undo}


class CountryStep(Step):
    key = "country"

    def question(self, env, state):
        return env._("In which country is the company? It decides the chart of accounts, the taxes and the "
                     "reports of the fiscal localisation, which I will ask you to confirm.")

    def parse(self, env, text, state):
        country = find_country(env, text)
        return {"country": country.code} if country else None

    def read(self, env, answers):
        company = _company(env)
        country = find_country(env, answers.get("country"))
        if not country:
            raise Refused("No country is named like this. Ask the person for the country.")
        module = localisation_module(env, country)
        chart = "chart_template" in company._fields and company.chart_template
        lines = [_line(env._("Country"), country.name, company.country_id.name)]
        install = False
        if chart:
            lines.append(_line(env._("Fiscal localisation"),
                               env._("A chart of accounts is already loaded: nothing is changed there.")))
        elif module and module.state == "installed":
            lines.append(_line(env._("Fiscal localisation"),
                               env._("%(module)s (%(name)s) is already installed.",
                                     module=module.shortdesc, name=module.name)))
        elif module:
            install = True
            lines.append(_line(
                env._("Fiscal localisation"),
                env._("Install %(module)s (%(name)s): Odoo loads the chart of accounts, taxes and reports of %(country)s "
                      "for this company.", module=module.shortdesc or module.name, name=module.name,
                      country=country.name)))
        else:
            lines.append(_line(env._("Fiscal localisation"),
                               env._("There is no fiscal localisation module for %s in this Odoo: the generic "
                                     "chart can be chosen in Settings > Accounting.", country.name)))
        currency = (COUNTRIES.get(country.code) or {}).get("currency")
        if currency:
            lines.append(_line(env._("Currency to use"), currency))
        return {"data": {"country_id": country.id, "module": module.name if install else ""},
                "lines": lines, "names": country.name}

    def apply(self, env, data):
        company = _company(env)
        undo = {"res.company": {str(company.id): _written(company, {"country_id": data["country_id"]})}}
        text = env._("The country is now %s.", env["res.country"].browse(data["country_id"]).name)
        install = [data["module"]] if data.get("module") else []
        if install:
            text += " " + env._("The fiscal localisation %s is being installed; the page reloads.", data["module"])
        return {"text": text, "undo": undo, "install": install}


class CurrencyStep(Step):
    key = "currency"

    def question(self, env, state):
        country = env.company.country_id
        usual = (COUNTRIES.get(country.code) or {}).get("currency")
        hint = env._(" The usual one here is %s: say “default” to take it.", usual) if usual else ""
        return env._("Which currency does the company keep its books in (a code such as AED, or its name)?") + hint

    def default(self, env, state):
        usual = (COUNTRIES.get(env.company.country_id.code) or {}).get("currency")
        return {"currency": usual} if usual else None

    def parse(self, env, text, state):
        currency = find_currency(env, text, env.company.country_id)
        return {"currency": currency.name} if currency else None

    def read(self, env, answers):
        company = _company(env)
        currency = find_currency(env, answers.get("currency"), company.country_id)
        if not currency:
            raise Refused("No currency is named like this. Ask the person for its code (AED, SAR, USD...).")
        if currency != company.currency_id and "account.move.line" in env \
                and env["account.move.line"].has_access("read") \
                and env["account.move.line"].search_count([("company_id", "=", company.id)], limit=1):
            raise Refused("The company has accounting entries already: its currency can no longer be "
                          "changed. Say so.")
        lines = [_line(env._("Company currency"), currency.name, company.currency_id.name)]
        if not currency.active:
            lines.append(_line(env._("Activated"), currency.name))
        return {"data": {"currency_id": currency.id}, "lines": lines, "names": currency.name}

    def apply(self, env, data):
        company = _company(env)
        currency = env["res.currency"].with_context(active_test=False).browse(data["currency_id"])
        if not currency.active:
            currency.active = True
        undo = {"res.company": {str(company.id): _written(company, {"currency_id": currency.id})}}
        return {"text": env._("The currency of the company is now %s.", currency.name), "undo": undo}


class LanguagesStep(Step):
    key = "languages"

    def question(self, env, state):
        return env._("Which languages should be available besides English (Arabic, French...)? I activate them "
                     "and load the translations of the installed apps.")

    def default(self, env, state):
        langs = (COUNTRIES.get(env.company.country_id.code) or {}).get("langs")
        return {"languages": langs} if langs else None

    def parse(self, env, text, state):
        langs, _missing = find_languages(env, setup_sentence.split_names(text) or [text])
        return {"languages": langs.mapped("code")} if langs else None

    def read(self, env, answers):
        wanted = answers.get("languages") or []
        if isinstance(wanted, str):
            wanted = [wanted]
        found, missing = find_languages(env, wanted[:MAX_ITEMS])
        if missing:
            raise Refused(f"No language is named {', '.join(missing)}. Ask the person.")
        todo = found.filtered(lambda lang: not lang.active)
        if not todo:
            raise Refused("These languages are all active already: there is nothing to do.")
        lines = [_line(env._("Activate and translate"), ", ".join(todo.mapped("name")))]
        lines.append(_line(env._("Translations"), env._("The terms of the installed apps are loaded in each: "
                                                      "it can take a minute.")))
        return {"data": {"codes": todo.mapped("code")}, "lines": lines, "names": ", ".join(todo.mapped("name"))}

    def apply(self, env, data):
        langs = env["res.lang"].with_context(active_test=False).search([("code", "in", data["codes"])])
        env["base.language.install"].create({"lang_ids": [Command.set(langs.ids)]}).lang_install()
        return {"text": env._("Languages activated: %s.", ", ".join(langs.mapped("name"))), "undo": {}}


class TimezoneStep(Step):
    key = "timezone"

    def question(self, env, state):
        return env._("Which time zone does the company work in? A city is enough (Dubai, Riyadh, Cairo...).")

    def default(self, env, state):
        zone = country_zone(env.company.country_id)
        return {"timezone": zone} if zone else None

    def parse(self, env, text, state):
        zone = find_timezone(text, env.company.country_id)
        return {"timezone": zone} if zone else None

    def read(self, env, answers):
        zone = find_timezone(answers.get("timezone"), env.company.country_id)
        if not zone:
            raise Refused("This is not a time zone Nudge knows. Ask the person for a city, or a name such as "
                          "Asia/Dubai.")
        company = _company(env)
        lines = [_line(env._("Time zone of the company"), zone, company.partner_id.tz),
                 _line(env._("Time zone of your user"), zone, env.user.tz)]
        return {"data": {"timezone": zone}, "lines": lines, "names": zone}

    def apply(self, env, data):
        company = _company(env)
        undo = {"res.partner": {str(company.partner_id.id): _written(company.partner_id, {"tz": data["timezone"]})},
                "res.users": {str(env.user.id): _written(env.user, {"tz": data["timezone"]})}}
        return {"text": env._("The time zone is now %s.", data["timezone"]), "undo": undo}


class LogoStep(Step):
    key = "logo"

    def question(self, env, state):
        return env._("Attach the logo of the company (a PNG or JPG picture) to your next message, or say skip.")

    def parse(self, env, text, state):
        attachment = logo_attachment(env)
        return {"attachment_id": attachment.id} if attachment else None

    def read(self, env, answers):
        attachment = logo_attachment(env, answers.get("attachment_id"))
        if not attachment:
            raise Refused("There is no picture: ask the person to attach the logo to their message.")
        raw = attachment.sudo().raw or b""
        if not (attachment.mimetype or "").startswith("image/") or not raw:
            raise Refused("The file is not a picture. Ask for a PNG or JPG.")
        if len(raw) > MAX_LOGO_BYTES:
            raise Refused("The picture is larger than 2 MB. Ask for a smaller one.")
        return {"data": {"attachment_id": attachment.id},
                "lines": [_line(env._("Logo"), f"{attachment.name} ({len(raw) // 1024} KB)")],
                "names": attachment.name}

    def apply(self, env, data):
        attachment = env["ir.attachment"].browse(data["attachment_id"]).exists()
        raw = attachment.sudo().raw if attachment else b""
        if not raw:
            raise Refused("The picture is gone.")
        company = _company(env)
        company.write({"logo": base64.b64encode(raw)})
        return {"text": env._("The logo of %s is changed.", company.name), "undo": {}}


class AppsStep(Step):
    key = "apps"

    def question(self, env, state):
        return env._("What does the company do? Trading, services, retail, manufacturing, contracting, a "
                     "restaurant, an online shop... I propose the apps that fit; or name the apps yourself.")

    def default(self, env, state):
        kind = ((state or {}).get("collected", {}).get("apps") or {}).get("kind") or DEFAULT_KIND
        return {"kind": kind}

    def parse(self, env, text, state):
        named = setup_sentence.find_codes(text, APP_WORDS)
        kinds = setup_sentence.find_codes(text, setup_sentence.KIND_WORDS)
        if kinds and not named:
            return {"kind": kinds[0]}
        if named:
            return {"apps": named}
        return None

    def read(self, env, answers):
        names = list(answers.get("apps") or [])
        kind = answers.get("kind")
        if kind and kind not in KIND_APPS:
            raise Refused(f"Kinds of business: {', '.join(KIND_APPS)}.")
        if kind:
            names = list(dict.fromkeys(list(KIND_APPS[kind]) + names))
        if not names:
            raise Refused("Give the kind of business, or the apps.")
        unknown = [name for name in names if name not in APPS]
        if unknown:
            raise Refused(f"Not apps Nudge sets up: {', '.join(unknown)}. Apps: {', '.join(APPS)}.")
        modules = app_modules(env, names)
        brings = app_brings(env)
        lines, todo = [], []
        for name in names[:MAX_ITEMS + 5]:
            module = modules.get(name)
            if not module:
                state = env._("not available in this Odoo")
            elif module.state == "installed":
                state = env._("already installed")
            else:
                state = env._("will be installed")
                todo.append(name)
            lines.append(_line(env._(APPS[name]), f"{brings[name]} — {state}"))
        if not todo:
            raise Refused("These apps are all installed already, or not available: there is nothing to install.")
        lines.append(_line(env._("Note"), env._("Nudge never uninstalls an app: that is done in Apps.")))
        return {"data": {"modules": todo}, "lines": lines, "names": ", ".join(todo)}

    def apply(self, env, data):
        return {"text": env._("These apps are being installed: %s. The page reloads.", ", ".join(data["modules"])),
                "undo": {}, "install": list(data["modules"])}


class CompaniesStep(Step):
    key = "companies"

    def question(self, env, state):
        return env._("Are there other companies in this database (for example Raghad Foods and Ebrahim Contracting)? "
                     "Give their names, or say skip.")

    def parse(self, env, text, state):
        names = setup_sentence.split_names(text)
        return {"companies": names} if names else None

    def read(self, env, answers):
        names = [" ".join(str(name).split()) for name in (answers.get("companies") or [])][:MAX_ITEMS]
        names = [name for name in dict.fromkeys(names) if name]
        if not names:
            raise Refused("Give the names of the other companies.")
        existing = env["res.company"].with_context(active_test=False).search([("name", "in", names)]).mapped("name")
        names = [name for name in names if name not in existing]
        if not names:
            raise Refused("These companies exist already.")
        lines = [_line(env._("New company"), name) for name in names]
        sees = []
        for item in (answers.get("visibility") or [])[:MAX_ITEMS]:
            user_text, _sep, companies_text = str(item).partition(":")
            user = self._user(env, user_text)
            wanted = [name for name in setup_sentence.split_names(companies_text) if name in names or name in existing]
            if not user:
                raise Refused(f"No user is named {user_text.strip()!r}. Users are made in the Users step.")
            sees.append({"user_id": user.id, "companies": wanted})
            lines.append(_line(env._("%s sees", user.name), ", ".join(wanted) or env._("only the current company")))
        lines.append(_line(env._("Your user"), env._("also works in the new companies, as Odoo does for who creates them")))
        return {"data": {"names": names, "sees": sees}, "lines": lines, "names": ", ".join(names)}

    @staticmethod
    def _user(env, text):
        text = (text or "").strip()
        Users = env["res.users"]
        return Users.search([("login", "=ilike", text)], limit=1) or Users.search([("name", "=ilike", text)], limit=1)

    def apply(self, env, data):
        created = env["res.company"].create([{"name": name} for name in data["names"]])
        undo = {"res.company": {str(company.id): _created(company) for company in created}}
        for item in data["sees"]:
            user = env["res.users"].browse(item["user_id"])
            wanted = env["res.company"].with_context(active_test=False).search(
                [("name", "in", item["companies"])]) if item["companies"] else env["res.company"]
            if wanted:
                undo.setdefault("res.users", {})[str(user.id)] = _written(
                    user, {"company_ids": [Command.link(company.id) for company in wanted]})
        return {"text": env._("Companies created: %s.", ", ".join(created.mapped("name"))), "undo": undo}


class WarehousesStep(Step):
    key = "warehouses"

    def question(self, env, state):
        return env._("Do you keep stock in more than one place? Give one name for each site or branch "
                     "(for example Dubai Main, Raghad Branch), or say skip.")

    def parse(self, env, text, state):
        names = setup_sentence.split_names(text)
        return {"warehouses": names} if names else None

    @staticmethod
    def code_of(env, name, taken):
        words = re.findall(r"[A-Za-z0-9]+", arabic.fold(name).upper() if name.isascii() else name)
        letters = "".join(word[0] for word in words) if len(words) > 1 else (words[0] if words else "WH")
        code = (re.sub(r"[^A-Z0-9]", "", letters.upper()) or "WH")[:5]
        base_code, number = code[:4], 1
        while code in taken:
            code, number = f"{base_code}{number}", number + 1
        return code

    def read(self, env, answers):
        if "stock.warehouse" not in env:
            raise Refused("The Inventory app is not installed: install it first (the Apps step).")
        names = [" ".join(str(name).split()) for name in (answers.get("warehouses") or [])][:MAX_ITEMS]
        names = [name for name in dict.fromkeys(names) if name]
        if not names:
            raise Refused("Give the names of the warehouses.")
        company = _company(env)
        Warehouse = env["stock.warehouse"].with_context(active_test=False)
        taken = set(Warehouse.search([("company_id", "=", company.id)]).mapped("code"))
        known = set(Warehouse.search([("company_id", "=", company.id)]).mapped("name"))
        items, lines = [], []
        for name in names:
            if name in known:
                continue
            code = self.code_of(env, name, taken)
            taken.add(code)
            items.append({"name": name, "code": code})
            lines.append(_line(env._("Warehouse"), f"{name} ({code})"))
        if not items:
            raise Refused("These warehouses exist already.")
        return {"data": {"items": items}, "lines": lines, "names": ", ".join(item["name"] for item in items)}

    def apply(self, env, data):
        created = env["stock.warehouse"].create([
            {"name": item["name"], "code": item["code"], "company_id": env.company.id} for item in data["items"]])
        undo = {"stock.warehouse": {str(warehouse.id): _created(warehouse) for warehouse in created}}
        return {"text": env._("Warehouses created: %s.", ", ".join(created.mapped("name"))), "undo": undo}


class UsersStep(Step):
    key = "users"

    def question(self, env, state):
        return env._(
            "Who will use it? One person a line with the email and the role (salesperson, accountant, warehouse, "
            "HR, manager, admin), for example: “Raghad raghad@example.com accountant”. Add “send invitations” if "
            "they should receive the email to choose a password; otherwise no email is sent.")

    def parse(self, env, text, state):
        users, invite = setup_sentence.users_answer(text)
        return {"users": users, "invite": invite} if users else None

    def read(self, env, answers):
        items = answers.get("users") or []
        if not items:
            raise Refused("Give the users: name, email and role.")
        labels = role_labels(env)
        lines, data, seen = [], [], set()
        for item in items[:MAX_ITEMS]:
            name, email = " ".join(str(item.get("name", "")).split()), str(item.get("email", "")).strip().lower()
            role = item.get("role") or "salesperson"
            if not name or not setup_sentence.EMAIL.fullmatch(email):
                raise Refused(f"{name or email!r}: a user needs a name and an email address.")
            if role not in ROLES:
                raise Refused(f"The role {role!r} does not exist. Roles: {', '.join(ROLES)}.")
            if email in seen or env["res.users"].with_context(active_test=False).search_count([("login", "=", email)]):
                raise Refused(f"A user with the login {email} exists already.")
            seen.add(email)
            groups, missing = self.groups_of(env, role)
            note = env._(" (not yet: %s)", ", ".join(missing)) if missing else ""
            lines.append(_line(f"{name} <{email}>", labels[role] + note))
            data.append({"name": name, "email": email, "role": role})
        invite = bool(answers.get("invite"))
        lines.append(_line(env._("Invitation email"),
                           env._("sent to each: they choose their password") if invite else env._("none is sent")))
        return {"data": {"users": data, "invite": invite}, "lines": lines,
                "names": ", ".join(item["name"] for item in data)}

    @staticmethod
    def groups_of(env, role):
        groups, missing = env["res.groups"], []
        for xmlid in ROLES[role]:
            group = env.ref(xmlid, raise_if_not_found=False)
            if group and group._name == "res.groups":
                groups |= group
            else:
                missing.append(xmlid.split(".")[0])
        return groups, sorted(set(missing))

    def apply(self, env, data):
        Users = env["res.users"].with_context(no_reset_password=True)
        internal = env.ref("base.group_user")
        created = env["res.users"]
        for item in data["users"]:
            groups, _missing = self.groups_of(env, item["role"])
            created |= Users.create({
                "name": item["name"], "login": item["email"], "email": item["email"],
                "company_id": env.company.id, "company_ids": [Command.link(env.company.id)],
                groups_field(env): [Command.link(group.id) for group in internal | groups]})
        text = env._("Users created: %s.", ", ".join(created.mapped("name")))
        if data.get("invite"):
            failed = []
            for user in created:
                try:
                    with env.cr.savepoint():
                        user.action_reset_password()
                except Exception:  # noqa: BLE001 - no mail server yet: the user exists, the invitation does not
                    failed.append(user.name)
            if failed:
                text += " " + env._("The invitation could not be sent to %s: set the outgoing mail server first.",
                                    ", ".join(failed))
        env.flush_all()
        created.invalidate_recordset(["write_date"])
        return {"text": text, "undo": {"res.users": {str(user.id): _created(user) for user in created}}}


class FiscalStep(Step):
    key = "fiscal"

    def question(self, env, state):
        return env._("When does the fiscal year end (for example 31 December)? You can add a lock date: no "
                     "accounting entry up to that day can be changed any more (“lock until 2025-12-31”).")

    def default(self, env, state):
        day, month = FISCAL_YEAR_ENDS.get(env.company.country_id.code, (31, 12))
        return {"day": day, "month": month}

    def parse(self, env, text, state):
        return setup_sentence.fiscal_answer(text)

    def read(self, env, answers):
        company = _company(env)
        values, lines = {}, []
        if answers.get("month"):
            month, day = int(answers["month"]), int(answers.get("day") or 0)
            if not 1 <= month <= 12 or not 1 <= day <= calendar.monthrange(2021, month)[1]:
                raise Refused("This is not a day of the year.")
            values.update(fiscalyear_last_day=day, fiscalyear_last_month=str(month))
            lines.append(_line(env._("Fiscal year ends"), f"{day:02d}/{month:02d}",
                               f"{company.fiscalyear_last_day:02d}/{int(company.fiscalyear_last_month):02d}"
                               if "fiscalyear_last_day" in company._fields else None))
        if answers.get("lock_date"):
            try:
                lock = datetime.date.fromisoformat(str(answers["lock_date"]))
            except ValueError:
                raise Refused("The lock date is written like 2025-12-31.") from None
            if "fiscalyear_lock_date" not in company._fields:
                raise Refused("A lock date needs the Invoicing app: install it first.")
            if lock > datetime.date.today():
                raise Refused("A lock date cannot be in the future.")
            values["fiscalyear_lock_date"] = lock
            lines.append(_line(env._("Locked until"), str(lock), str(company.fiscalyear_lock_date or "") or None))
        if not values:
            raise Refused("Give the day the fiscal year ends, or a lock date.")
        if "fiscalyear_last_day" not in company._fields:
            raise Refused("The fiscal year is set in the Invoicing app: install it first.")
        stored = {name: (value.isoformat() if isinstance(value, datetime.date) else value)
                  for name, value in values.items()}
        return {"data": {"values": stored}, "lines": lines, "names": ", ".join(line["value"] for line in lines)}

    def apply(self, env, data):
        company = _company(env)
        values = dict(data["values"])
        undo = {"res.company": {str(company.id): _written(company, values)}}
        return {"text": env._("The fiscal year is set."), "undo": undo}


class FormatsStep(Step):
    key = "formats"
    DATES = ("%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d")

    def question(self, env, state):
        return env._("How should dates and numbers be written? Give a sample, such as “31/12/2026 and 1,234.56”, "
                     "or say “default”.")

    def default(self, env, state):
        month_first = env.company.country_id.code in MONTH_FIRST
        return {"date_format": "%m/%d/%Y" if month_first else "%d/%m/%Y", "decimal_point": ".", "thousands_sep": ","}

    def parse(self, env, text, state):
        return setup_sentence.formats_answer(text)

    def read(self, env, answers):
        lang = env["res.lang"].search([("code", "=", env.lang or "en_US")], limit=1)
        if not lang:
            raise Refused("The language of the person is not active.")
        values = {name: answers[name] for name in ("date_format", "time_format", "decimal_point", "thousands_sep")
                  if name in answers and answers[name] is not None}
        if not values:
            raise Refused("Give a sample of a date and a number, or ask for the defaults.")
        date = values.get("date_format")
        if date and not (("%d" in date) and ("%m" in date) and ("%Y" in date or "%y" in date)):
            raise Refused("A date format holds the day, the month and the year (%d, %m, %Y).")
        decimal, thousands = values.get("decimal_point", lang.decimal_point), values.get("thousands_sep", lang.thousands_sep)
        if decimal not in (".", ",") or thousands not in (",", ".", " ", "'", ""):
            raise Refused("The decimal point is a dot or a comma; the thousands separator a comma, a dot, "
                          "a space, an apostrophe or nothing.")
        if decimal == thousands:
            raise Refused("The decimal point and the thousands separator cannot be the same.")
        values.update(decimal_point=decimal, thousands_sep=thousands)
        sample = datetime.date(2026, 12, 31).strftime(values.get("date_format") or lang.date_format)
        number = f"{1234567.89:,.2f}".translate(str.maketrans({",": thousands or "", ".": decimal}))
        lines = [_line(env._("Language"), lang.name),
                 _line(env._("Date"), sample, datetime.date(2026, 12, 31).strftime(lang.date_format)),
                 _line(env._("Number"), number),
                 _line(env._("To go back"), env._("Settings > Translations > Languages"))]
        return {"data": {"lang_id": lang.id, "values": values}, "lines": lines, "names": f"{sample} / {number}"}

    def apply(self, env, data):
        lang = env["res.lang"].browse(data["lang_id"])
        lang.write(data["values"])
        return {"text": env._("The formats of %s are changed.", lang.name), "undo": {}}


STEPS = {step.key: step for step in (
    CompanyStep(), CountryStep(), CurrencyStep(), LanguagesStep(), TimezoneStep(), LogoStep(), AppsStep(),
    CompaniesStep(), WarehousesStep(), UsersStep(), FiscalStep(), FormatsStep())}


# ----------------------------------------------------------------------
# All the rest, with the defaults; the summary
# ----------------------------------------------------------------------
def state_of(env):
    conversation = env["nudge.conversation"].browse(env.context.get("nudge_conversation_id") or 0).exists()
    return (conversation.sudo().setup_state if conversation else None) or {}


def save_state(env, state, conversation=None):
    conversation = conversation or env["nudge.conversation"].browse(env.context.get("nudge_conversation_id") or 0)
    if conversation.exists():
        conversation.sudo().setup_state = state


def mark(env, key, answers=None):
    """Remember that a step is done, and what was said for it"""
    state = dict(state_of(env) or {"active": True})
    state["done"] = sorted(set(state.get("done", [])) | {key})
    state["skipped"] = [item for item in state.get("skipped", []) if item != key]
    if answers:
        state.setdefault("collected", {})[key] = answers
    save_state(env, state)


def status(env):
    """:return: ``{step: {"done", "skipped"}}``: what the conversation did and what the database shows"""
    state = state_of(env)
    company = env.company
    done = set(state.get("done", []))
    if _plain_name(company.name) not in DEFAULT_NAMES:
        done.add("company")
    if "chart_template" in company._fields and company.chart_template and company.country_id:
        done.add("country")
    if env["res.lang"].search_count([("active", "=", True)]) > 1:
        done.add("languages")
    if env["res.company"].search_count([]) > 1:
        done.add("companies")
    if "stock.warehouse" in env and env["stock.warehouse"].search_count([("company_id", "=", company.id)]) > 1:
        done.add("warehouses")
    if env["res.users"].search_count([("share", "=", False)]) > FEW_USERS:
        done.add("users")
    return {key: {"done": key in done, "skipped": key in state.get("skipped", [])} for key in ORDER}


def next_step(env, state=None):
    """:return: the step to ask next, '' when there is none left"""
    state = state if state is not None else state_of(env)
    left = set(state.get("done", [])) | set(state.get("skipped", []))
    for key in ORDER:
        if key not in left:
            return key
    return ""


def remaining_plan(env, state):
    """The card of the steps that can be done by their defaults"""
    left = set(state.get("done", [])) | set(state.get("skipped", []))
    items, lines, installs = [], [], []
    for key in DEFAULTED:
        if key in left:
            continue
        step = STEPS[key]
        answers = step.default(env, state)
        if not answers:
            continue
        try:
            plan = step.read(env, answers)
        except Refused:
            continue
        items.append({"step": key, "data": plan["data"]})
        title = step_titles(env)[key]
        lines += [dict(line, label=f"{title}: {line['label']}") for line in plan["lines"]]
    if not items:
        raise Refused("There is nothing left that the defaults can do. Say so.")
    return {"data": {"steps": items}, "lines": lines, "names": ", ".join(item["step"] for item in items)}


def what_next(env):
    """:return: ``[{"title", "ask"}]``: what to do after the setup, as sentences Nudge answers with a tour"""
    items = []
    installed = lambda name: bool(env["ir.module.module"].search_count([("name", "=", name), ("state", "=", "installed")]))  # noqa: E731
    if installed("sale_management") or installed("sale"):
        items.append({"title": env._("Create your first quotation"), "ask": env._("How do I create a quotation?")})
    if installed("stock"):
        items.append({"title": env._("Add your products and receive stock"),
                      "ask": env._("How do I create a product and receive it in stock?")})
    if installed("account"):
        items.append({"title": env._("Send your first invoice"), "ask": env._("How do I create a customer invoice?")})
    if installed("purchase"):
        items.append({"title": env._("Order from a supplier"), "ask": env._("How do I create a purchase order?")})
    if installed("hr"):
        items.append({"title": env._("Add your employees"), "ask": env._("How do I create an employee?")})
    items.append({"title": env._("Import your customers and products"),
                  "ask": env._("How do I import customers from a file?")})
    return items


def mail_note(env):
    count = env["ir.mail_server"].search_count([]) if env["ir.mail_server"].has_access("read") else 0
    if count:
        return env._("Outgoing email: %s server(s) are set up.", count)
    return env._("Outgoing email is not set up. Nudge never configures it: open Settings > Technical > Outgoing "
                 "Mail Servers, or ask me to show you where.")


def summary_plan(env, state):
    company = env.company
    titles = step_titles(env)
    lines = [_line(env._("Company"), company.name), _line(env._("Country"), company.country_id.name or "-"),
             _line(env._("Currency"), company.currency_id.name)]
    lines.append(_line(env._("Languages"), ", ".join(env["res.lang"].search([("active", "=", True)]).mapped("name"))))
    modules = env["ir.module.module"].search([("name", "in", list(APPS)), ("state", "=", "installed")])
    lines.append(_line(env._("Apps"), ", ".join(env._(APPS[name]) for name in modules.mapped("name")) or "-"))
    left = [titles[key] for key in ORDER if key not in set(state.get("done", [])) | set(state.get("skipped", []))]
    if left:
        lines.append(_line(env._("Not done"), ", ".join(left)))
    lines.append(_line(env._("Outgoing email"), mail_note(env)))
    for item in what_next(env):
        lines.append(_line(env._("What to do next"), f"{item['title']} — “{item['ask']}”"))
    return {"data": {}, "lines": lines, "names": company.name}


# ----------------------------------------------------------------------
# The tools
# ----------------------------------------------------------------------
@base.register
class ReadSetup(base.Tool):
    name = "read_setup"
    group = "nudge.group_nudge_agent"
    modes = (base.MODE_AGENT,)
    description = (
        "Read where the first setup of this database stands: whether the database is a new one, which steps "
        "are done or skipped, the next one to ask, and the choices (countries with their localisation module, "
        "kinds of business with their apps, roles). Use it to start and to go on with the setup assistant: one "
        "question at a time, then prepare_setup_step for the answer."
    )
    input_schema = {"type": "object", "additionalProperties": False, "properties": {}}

    def is_available(self, env, mode=base.MODE_TALK):
        return super().is_available(env, mode) and env["nudge.conversation"]._can_administer()

    @refusing
    def run(self, env, arguments, screen):
        _guard(env)
        state = state_of(env)
        found = detect(env)
        shown = status(env)
        titles = step_titles(env)
        result = {
            "fresh_database": found["fresh"], "tests": found["reasons"],
            "steps": [{"step": key, "title": titles[key], **shown[key]} for key in ORDER],
            "next_step": next_step(env, state) or "summary",
            "defaults_offered_after": list(FIRST_THREE),
            "steps_the_defaults_can_do": list(DEFAULTED),
            "kinds_of_business": {kind: list(apps) for kind, apps in KIND_APPS.items()},
            "roles": list(ROLES), "apps": list(APPS),
            "localisation_modules": {code: (LOCALISATION_EXCEPTIONS.get(code) or f"l10n_{code.lower()}")
                                     for code in COUNTRIES},
            "outgoing_mail": mail_note(env),
        }
        return base.ToolResult(base.dump(result))


@base.register
class PrepareSetupStep(base.Action):
    name = "prepare_setup_step"
    kind = "write"
    description = (
        "One step of the first setup of the database, as a card the person says yes or no to: nothing changes "
        "before the yes. step: company (name, vat, street, city, zip, phone, email, website), country (it "
        "names the fiscal localisation module to install), currency, languages, timezone, logo (the picture "
        "attached to the message), apps (the kind of business, or the apps), companies (names of other "
        "companies, and which user sees which), warehouses, users (name, email, role; invitation emails only "
        "when the person asks), fiscal (the day the year ends, a lock date), formats (date and number), "
        "remaining (all the remaining steps by their defaults, only after company, country and currency), "
        "summary (the last card: what was done, the mail server note and what to do next). One step per call, "
        "the answers the person gave and nothing invented. Read where the setup stands with read_setup."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["step"],
        "properties": {
            "step": {"type": "string", "enum": list(ORDER) + ["remaining", "summary"]},
            "answers": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string", "maxLength": 120}, "vat": {"type": "string", "maxLength": 40},
                    "street": {"type": "string", "maxLength": 200}, "street2": {"type": "string", "maxLength": 200},
                    "city": {"type": "string", "maxLength": 100}, "zip": {"type": "string", "maxLength": 20},
                    "phone": {"type": "string", "maxLength": 40}, "email": {"type": "string", "maxLength": 120},
                    "website": {"type": "string", "maxLength": 200},
                    "company_registry": {"type": "string", "maxLength": 60},
                    "country": {"type": "string", "maxLength": 80},
                    "currency": {"type": "string", "maxLength": 40},
                    "languages": {"type": "array", "maxItems": MAX_ITEMS, "items": {"type": "string", "maxLength": 40}},
                    "timezone": {"type": "string", "maxLength": 60},
                    "attachment_id": {"type": "integer", "minimum": 1},
                    "kind": {"type": "string", "enum": list(KIND_APPS)},
                    "apps": {"type": "array", "maxItems": len(APPS), "items": {"type": "string", "enum": list(APPS)}},
                    "companies": {"type": "array", "maxItems": MAX_ITEMS, "items": {"type": "string", "maxLength": 120}},
                    "visibility": {"type": "array", "maxItems": MAX_ITEMS, "items": {
                        "type": "string", "maxLength": 300,
                        "description": "A user and the companies they see: \"raghad@example.com: Raghad Foods, Ebrahim Contracting\""}},
                    "warehouses": {"type": "array", "maxItems": MAX_ITEMS, "items": {"type": "string", "maxLength": 100}},
                    "users": {"type": "array", "maxItems": MAX_ITEMS, "items": {
                        "type": "object", "additionalProperties": False, "required": ["name", "email"],
                        "properties": {"name": {"type": "string", "maxLength": 100},
                                       "email": {"type": "string", "maxLength": 120},
                                       "role": {"type": "string", "enum": list(ROLES)}}}},
                    "invite": {"type": "boolean", "description": "Send the invitation email: only when the person asked."},
                    "month": {"type": "integer", "minimum": 1, "maximum": 12},
                    "day": {"type": "integer", "minimum": 1, "maximum": 31},
                    "lock_date": {"type": "string", "pattern": r"\d{4}-\d{2}-\d{2}"},
                    "date_format": {"type": "string", "maxLength": 20}, "time_format": {"type": "string", "maxLength": 20},
                    "decimal_point": {"type": "string", "maxLength": 1}, "thousands_sep": {"type": "string", "maxLength": 1},
                },
            },
        },
    }

    def is_available(self, env, mode=base.MODE_TALK):
        return super().is_available(env, mode) and env["nudge.conversation"]._can_administer()

    @refusing
    def run(self, env, arguments, screen):
        _guard(env)
        key, answers = arguments["step"], arguments.get("answers") or {}
        state = state_of(env)
        if key == "remaining":
            if not set(FIRST_THREE) <= set(state.get("done", [])) | set(state.get("skipped", [])) \
                    | {k for k, v in status(env).items() if v["done"]}:
                raise Refused("The defaults are offered once the company, the country and the currency are done. "
                              "Ask for those first.")
            plan = remaining_plan(env, state)
        elif key == "summary":
            plan = summary_plan(env, state)
        elif key in STEPS:
            plan = STEPS[key].read(env, answers)
        else:
            raise Refused("This is not a step of the setup.")
        title = step_titles(env)[key]
        return base.ToolResult(
            "The person is being shown what the setup is about to change.",
            pending={
                "summary": env._("Set up: %s", title),
                "res_model": "res.company",
                "res_ids": [],
                "record_names": (plan["names"] or "")[:500],
                "values": {"step": key, "data": plan["data"], "answers": answers},
                "is_sensitive": True,
                "preview": {"records": [], "lines": plan["lines"]},
            },
        )

    def execute(self, env, step):
        _guard(env)
        values = step.values or {}
        key = values.get("step")
        data = values.get("data") or {}
        if key == "remaining":
            parts = [(STEPS[item["step"]], item["data"], item["step"]) for item in data.get("steps", [])]
            # Installing an app restarts the registry: the apps come last
            parts.sort(key=lambda part: part[2] == "apps")
        elif key == "summary":
            env["ir.config_parameter"].set_param(PARAM_DONE, "yes")
            mark(env, "summary")
            return base.ToolResult("The setup is finished. Tell the person what was done and what to do next: "
                                   + "; ".join(item["title"] for item in what_next(env)) + ".",
                                   records=("res.company", [env.company.id], [env.company.name]))
        elif key in STEPS:
            parts = [(STEPS[key], data, key)]
        else:
            return base.ToolResult("This is not a step of the setup.", is_error=True)
        texts, undo, install = [], {}, []
        for step_object, part, name in parts:
            done = step_object.apply(env, part)
            texts.append(done["text"])
            _merge(undo, done.get("undo"))
            install += done.get("install") or []
            mark(env, name, values.get("answers") if key == name else None)
        step.values = dict(values, undo={model: entries for model, entries in undo.items() if entries})
        event = {"type": "changed", "model": "res.company", "ids": [env.company.id]}
        if install:
            # last: it reloads the registry
            install_modules(env, install)
            event["action"] = {"type": "ir.actions.client", "tag": "reload"}
        return base.ToolResult(" ".join(texts), event=event, records=("res.company", [env.company.id], [env.company.name]))


# ----------------------------------------------------------------------
# The conversation with no AI
# ----------------------------------------------------------------------
def new_state():
    return {"active": True, "done": [], "skipped": [], "collected": {}}


def asking(env, state):
    """:return: the question of the step to ask next, or '' when the summary is next"""
    key = next_step(env, state)
    while key:
        if key == "warehouses" and "stock.warehouse" not in env:
            state["skipped"] = state.get("skipped", []) + [key]
            key = next_step(env, state)
            continue
        break
    state["asking"] = key
    save_state(env, state)
    if not key:
        return ""
    titles = step_titles(env)
    position = ORDER.index(key) + 1
    text = env._("Step %(number)s of %(total)s — %(title)s.", number=position, total=len(ORDER), title=titles[key])
    text += "\n" + STEPS[key].question(env, state)
    if key in DEFAULTED + ("currency",) and STEPS[key].default(env, state):
        text += "\n" + env._("Say “default” and I use the usual choice.")
    if key not in FIRST_THREE:
        text += " " + env._("Say “skip” to leave it for later.")
    if set(FIRST_THREE) <= set(state.get("done", [])) | set(state.get("skipped", [])) and key not in FIRST_THREE:
        text += "\n" + env._("Or say “do the rest with the defaults”.")
    return text


def start(env):
    """:return: ``(state, text)``: the first question of the assistant"""
    state = new_state()
    found = detect(env)
    intro = env._("Let us set this database up together. I ask one thing at a time and show a card with the exact "
                  "changes: nothing happens before you say yes, and most steps can be undone.")
    if not found["fresh"]:
        intro += " " + env._("This database already holds some setup: I only suggest what is missing.")
    for key, shown in status(env).items():
        if shown["done"]:
            state["done"].append(key)
    return state, intro + "\n\n" + asking(env, state)


def converse(env, state, text, conversation=None):
    """The next turn of the assistant when no AI reads the answer.

    :return: ``(state, reply text, call)``: ``call`` is ``(tool, arguments)`` or None"""
    _guard(env)
    key = state.get("asking") or next_step(env, state)
    if setup_sentence.wants(text, setup_sentence.CANCEL, 8):
        state["active"] = False
        save_state(env, state)
        return state, env._("Stopped. Say “%s” to go on from here.", setup_sentence.SETUP_SENTENCE), None
    if not key:
        return state, env._("Everything has been asked. Say yes to the card, or ask me for something else."), \
            ("prepare_setup_step", {"step": "summary"})
    if setup_sentence.wants(text, setup_sentence.DEFAULTS, 8) and key not in FIRST_THREE:
        return state, env._("Here is what I would do with the defaults:"), ("prepare_setup_step", {"step": "remaining"})
    if setup_sentence.wants(text, setup_sentence.DEFAULTS, 8):
        return state, env._("I need the company, the country and the currency first; the rest I can do with the "
                            "defaults after that."), None
    if key not in FIRST_THREE and setup_sentence.wants(text, setup_sentence.SKIP):
        state["skipped"] = sorted(set(state.get("skipped", [])) | {key})
        save_state(env, state)
        question = asking(env, state)
        if not question:
            return state, env._("That was the last step. Here is the summary:"), ("prepare_setup_step", {"step": "summary"})
        return state, question, None
    step = STEPS[key]
    answers = None
    if key in DEFAULTED + ("currency",) and setup_sentence.wants(text, ("default", "defaults", "usual", "same", "ok", "yes",
                                                                         "افتراضي", "الافتراضي", "عادي", "نفسه", "تمام")):
        answers = step.default(env, state)
    if answers is None:
        if key == "logo":
            with_conversation = env(context=dict(env.context, nudge_conversation_id=conversation.id)) \
                if conversation else env
            answers = step.parse(with_conversation, text, state)
        else:
            answers = step.parse(env, text, state)
    if not answers:
        return state, env._("I did not understand that.") + "\n" + step.question(env, state), None
    return state, step_title_text(env, key), ("prepare_setup_step", {"step": key, "answers": answers})


def step_title_text(env, key):
    return env._("Here is what I would do for “%s”:", step_titles(env)[key])


def after_yes(env, conversation):
    """:return: the text that follows a yes in the conversation with no AI: the next question"""
    state = dict(conversation.sudo().setup_state or {})
    if not state.get("active"):
        return ""
    state["asking"] = ""
    shown = status(env(context=dict(env.context, nudge_conversation_id=conversation.id)))
    for key, item in shown.items():
        if item["done"] and key not in state.get("done", []):
            state.setdefault("done", []).append(key)
    question = asking(env(context=dict(env.context, nudge_conversation_id=conversation.id)), state)
    if question:
        return question
    save_state(env, state, conversation)
    return env._("That was the last step. Here is the summary:")

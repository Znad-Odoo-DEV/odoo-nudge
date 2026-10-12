"""Is this database fit for this kind of business?

"Are our settings and modules right for a contracting company?" A senior
implementer answers from what is installed, what was added, where the work
is, and what such a business usually needs in Odoo, each need with the page
of the official documentation about it. What the documentation does not
cover is said to be unknown, not guessed.
"""
import re

from ..profile import activity
from . import base

WORD = re.compile(r"[\w]+", re.UNICODE)

# What a kind of business usually runs in Odoo: the module, why, and the
# words that find its documentation page. The list is a starting point an
# implementer checks against the business, not a verdict.
BUSINESSES = {
    "contracting": {
        "words": ("contracting", "contractor", "construction", "مقاولات", "مقاول", "انشاءات", "إنشاءات", "بناء"),
        "needs": [
            ("project", "Projects for every site or contract, with tasks and stages", "project"),
            ("hr_timesheet", "Timesheets: hours of staff and labour per project", "timesheets"),
            ("analytic", "Analytic accounting: cost and revenue per project", "analytic accounting"),
            ("purchase", "Purchases of materials and subcontracting", "purchase"),
            ("purchase_requisition", "Purchase agreements with contractors and suppliers", "purchase agreements"),
            ("stock", "Inventory of materials, one warehouse per site store", "inventory"),
            ("account", "Invoicing with down payments and progress billing", "invoicing"),
            ("sale_project", "Sales orders that create projects and bill milestones", "milestones"),
            ("hr", "Employees, contracts and departments", "employees"),
            ("hr_payroll", "Payroll for staff and labour", "payroll"),
            ("hr_attendance", "Attendance of labour, by device or kiosk", "attendance"),
            ("fleet", "Vehicles and machinery", "fleet"),
            ("maintenance", "Maintenance of equipment", "maintenance"),
            ("documents", "Documents: drawings, contracts, certificates", "documents"),
        ],
    },
    "trading": {
        "words": ("trading", "trade", "wholesale", "distribution", "distributor", "تجارة", "تجاري", "جملة", "توزيع"),
        "needs": [
            ("sale", "Quotations, sales orders and pricelists", "sales"),
            ("purchase", "Purchase orders and vendor pricelists", "purchase"),
            ("stock", "Inventory with receipts, deliveries and reordering rules", "inventory"),
            ("account", "Customer invoices, vendor bills and payments", "invoicing"),
            ("crm", "Leads and opportunities", "crm"),
            ("product", "Products with variants, barcodes and units of measure", "product variants"),
            ("stock_landed_costs", "Landed costs on imports", "landed costs"),
            ("delivery", "Shipping methods and carriers", "shipping methods"),
            ("sale_margin", "Margins on sales", "margins"),
            ("stock_account", "Inventory valuation", "inventory valuation"),
        ],
    },
    "shipping": {
        "words": ("shipping", "logistics", "freight", "forwarding", "courier", "transport", "شحن", "لوجستي", "نقل", "توصيل"),
        "needs": [
            ("stock", "Inventory operations: receipts, transfers, deliveries", "inventory"),
            ("delivery", "Shipping methods, carriers and tracking", "shipping methods"),
            ("fleet", "Vehicles, drivers and fuel", "fleet"),
            ("project", "Shipments as projects or tasks when they take steps", "project"),
            ("sale", "Quotations for freight services", "sales"),
            ("account", "Invoicing and expenses per shipment", "invoicing"),
            ("analytic", "Analytic accounting: cost and revenue per shipment", "analytic accounting"),
            ("hr_expense", "Expenses of drivers and agents", "expenses"),
            ("stock_landed_costs", "Landed costs", "landed costs"),
            ("quality", "Quality checks on receipts", "quality"),
        ],
    },
    "manufacturing": {
        "words": ("manufacturing", "factory", "production", "تصنيع", "مصنع", "انتاج", "إنتاج"),
        "needs": [
            ("mrp", "Manufacturing orders and bills of materials", "manufacturing"),
            ("stock", "Inventory of raw materials and finished goods", "inventory"),
            ("purchase", "Purchases of raw materials", "purchase"),
            ("sale", "Sales orders", "sales"),
            ("account", "Invoicing and cost accounting", "invoicing"),
            ("quality", "Quality control points", "quality"),
            ("maintenance", "Maintenance of machines", "maintenance"),
            ("mrp_plm", "Engineering changes of products", "plm"),
            ("hr", "Employees and work centers", "employees"),
        ],
    },
    "retail": {
        "words": ("retail", "shop", "store", "supermarket", "تجزئة", "متجر", "محل", "سوبرماركت"),
        "needs": [
            ("point_of_sale", "Point of sale", "point of sale"),
            ("stock", "Inventory and replenishment", "inventory"),
            ("purchase", "Purchases", "purchase"),
            ("account", "Invoicing", "invoicing"),
            ("loyalty", "Loyalty programs and discounts", "loyalty"),
            ("product", "Products, barcodes, variants", "product variants"),
        ],
    },
    "services": {
        "words": ("services", "consulting", "agency", "خدمات", "استشارات", "وكالة"),
        "needs": [
            ("project", "Projects and tasks", "project"),
            ("hr_timesheet", "Timesheets", "timesheets"),
            ("sale_timesheet", "Billing time and milestones", "invoicing time"),
            ("helpdesk", "Helpdesk tickets", "helpdesk"),
            ("crm", "Leads and opportunities", "crm"),
            ("account", "Invoicing", "invoicing"),
            ("planning", "Planning of people", "planning"),
        ],
    },
    "real estate": {
        "words": ("real estate", "property", "properties", "rental", "عقار", "عقارات", "ايجار", "إيجار", "تأجير"),
        "needs": [
            ("sale_subscription", "Recurring rent as subscriptions", "subscriptions"),
            ("account", "Invoicing and payments", "invoicing"),
            ("project", "Properties or units as projects", "project"),
            ("maintenance", "Maintenance of properties", "maintenance"),
            ("documents", "Contracts and documents", "documents"),
            ("crm", "Leads of buyers and tenants", "crm"),
        ],
    },
}


def business_of(text):
    """:return: the key of the business ``text`` names, or None"""
    lowered = (text or "").lower()
    for key, business in BUSINESSES.items():
        if any(word in lowered for word in business["words"]):
            return key
    return None


def _page_for(env, words):
    """:return: the official page about ``words``, as {"title", "url"}, or None"""
    Page = env["nudge.doc.page"].sudo()
    for pattern in (words, words.split()[0] if words.split() else words):
        page = Page.search([("title", "=ilike", pattern)], limit=1) or Page.search(
            [("title", "ilike", pattern), ("category", "=", "applications")], order="id", limit=1)
        if page:
            return {"title": page.title, "url": page.url}
    return None


def edition(env):
    """:return: "Enterprise" or "Community", what this database runs"""
    Module = env["ir.module.module"].sudo()
    return "Enterprise" if Module.search_count([("state", "=", "installed"), ("license", "=", "OEEL-1")]) else "Community"


EDITION_NOTE = {
    "Community": (
        "This database runs Community: recommend only what Community has. Never say that a "
        "Community feature (several warehouses and moving goods between them, routes, "
        "manufacturing, point of sale, several companies) needs Enterprise. What is listed in "
        "enterprise_only is not available here: never propose it as a step; name it only as an "
        "Enterprise option, said as such."),
    "Enterprise": "This database runs Enterprise: its apps and those of Community are available.",
}


@base.register
class AssessDatabase(base.Tool):
    name = "assess_database"
    description = (
        "Judge whether this database, as it is set up, fits a kind of business: a contracting, "
        "trading, shipping, manufacturing, retail, services or real estate company. Returns what "
        "such a business usually runs in Odoo, which of it is installed here, which is in use "
        "(from the daily study of where the work is), what is custom, and what is missing, each "
        "with the page of the official documentation about it when there is one. Answer from "
        "this only: what is listed as missing may be proposed, with its link; what is not "
        "listed is unknown, say so. For a kind of business that is not known, say you do not "
        "have a reference for it and ask what the business does, step by step."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "business": {
                "type": "string",
                "minLength": 2,
                "maxLength": 80,
                "description": (
                    "The kind of business, only as the person named it: \"شركة مقاولات\", \"trading\". "
                    "Left out when they named none: never guessed."),
            },
        },
    }

    @staticmethod
    def _used(env):
        """:return: ``(installed, custom, used)`` module names, by the profile"""
        installed = set(env["ir.module.module"].sudo().search([("state", "=", "installed")]).mapped("name"))
        profile = env["nudge.profile"]._ensure_current().sudo()
        custom = {
            module["name"] for piece in profile.slice_ids.filtered(lambda piece: piece.key == "overview")
            for module in (piece.data or {}).get("custom_modules", [])
        }
        used = {(piece.data or {}).get("module") or "" for piece in profile.slice_ids
                if piece.kind == "activity" and (piece.data or {}).get("changed")}
        return installed, custom, used

    def _overview(self, env):
        """What is set up and in use, with no kind of business to judge it by"""
        installed, custom, used = self._used(env)
        apps = env["ir.module.module"].sudo().search([("state", "=", "installed"), ("application", "=", True)])
        return base.ToolResult(base.dump({
            "edition": edition(env),
            "edition_note": EDITION_NOTE[edition(env)],
            "apps": [{"module": app.name, "name": app.shortdesc, "in_use": app.name in used} for app in apps],
            "custom_modules": sorted(custom),
            "custom_modules_in_use": sorted(custom & used),
            "note": (
                "No kind of business was named: tell what is set up and what is in use, from this "
                "only, and ask what the business does to say what is missing. Known kinds: "
                + ", ".join(sorted(BUSINESSES)) + "."),
        }))

    def run(self, env, arguments, screen):
        business = (arguments.get("business") or "").strip()
        if not business:
            # A small model named a business the person never named, and judged by it
            return self._overview(env)
        key = business_of(business)
        if key is None:
            known = ", ".join(sorted(BUSINESSES))
            return base.ToolResult(
                f"No reference for {arguments['business']!r}. Known kinds: {known}. Tell the person "
                "you have no reference for this kind of business and ask what it does, step by "
                "step, rather than guessing.")
        installed = set(env["ir.module.module"].sudo().search([("state", "=", "installed")]).mapped("name"))
        profile = env["nudge.profile"]._ensure_current().sudo()
        custom = {
            module["name"] for piece in profile.slice_ids.filtered(lambda piece: piece.key == "overview")
            for module in (piece.data or {}).get("custom_modules", [])
        }
        used_modules = {}
        for piece in profile.slice_ids.filtered(lambda piece: piece.kind == "activity"):
            data = piece.data or {}
            if data.get("changed"):
                module = data.get("module") or ""
                used_modules[module] = max(used_modules.get(module, 0), activity.activity(data))
        found, missing, enterprise_only = [], [], []
        runs = edition(env)
        Module = env["ir.module.module"].sudo()
        for module, why, words in BUSINESSES[key]["needs"]:
            entry = {"module": module, "for": why}
            page = _page_for(env, words)
            if page:
                entry["documentation"] = page
            if module in installed:
                entry["installed"] = True
                entry["in_use"] = module in used_modules
                found.append(entry)
            elif runs == "Community" and Module.search([("name", "=", module)], limit=1).license in (False, "OEEL-1"):
                # Barcode and the shipping connectors were proposed to a Community database:
                # a module of Enterprise is not on its server at all
                enterprise_only.append(entry)
            else:
                missing.append(entry)
        apps_custom = sorted(custom)
        return base.ToolResult(base.dump({
            "business": key,
            "edition": runs,
            "edition_note": EDITION_NOTE[runs],
            "installed": found,
            "missing": missing,
            **({"enterprise_only": enterprise_only} if enterprise_only else {}),
            "custom_modules": apps_custom,
            "custom_modules_in_use": sorted(module for module in apps_custom if module in used_modules),
            "note": (
                "Installed and in use is good; installed and not in use is worth asking about; "
                "missing is a proposal, with its documentation page. Custom modules may cover a "
                "missing need their own way: look them up with search_database_profile before "
                "calling anything missing. Say what you do not know."
            ),
        }))

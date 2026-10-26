"""Build nudge/data/odoo_docs.jsonl.gz from a checkout of the Odoo documentation.

The documentation is at https://github.com/odoo/documentation, branch 19.0,
under content/applications, licensed CC BY-SA 4.0. A shallow, sparse clone
is enough:

    git clone -c core.longpaths=true --depth 1 --branch 19.0 --filter=blob:none --sparse \\
        https://github.com/odoo/documentation.git odoo_docs
    git -C odoo_docs sparse-checkout set content/applications content/developer

Then, from the folder of the module, with any Python 3.10+:

    python scripts/build_odoo_docs.py <path to odoo_docs> [19.0] [output file]

The 18.0 and 17.0 documentation goes to docs_data/odoo_docs_<version>.jsonl.gz
at the root of the repository (the ports copy it in): pass that path as the
third argument.

One line per page in the output, JSON: path, url, category, app, modules,
title, description, sections [{heading, anchor, text}]. Nudge loads it when
it is installed or updated, and when a manager asks.
"""
import gzip
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from knowledge.rst import read_page  # noqa: E402

SITE = "https://www.odoo.com/documentation/{version}/{section}/{path}.html"

# The technical modules a folder of the documentation is about: what lets
# Nudge offer the pages of the app on the screen
MODULES = {
    "essentials": ["base", "web"],
    "general": ["base", "web", "mail"],
    "general/companies": ["base"],
    "general/email_communication": ["mail"],
    "general/iot": ["iot"],
    "general/users": ["base"],
    "general/integrations": ["base"],
    "general/apps_modules": ["base"],
    "finance/accounting": ["account"],
    "finance/fiscal_localizations": ["account"],
    "finance/expenses": ["hr_expense"],
    "finance/payment_providers": ["payment"],
    "finance/documents": ["documents"],
    "finance/sign": ["sign"],
    "finance/spreadsheet": ["spreadsheet", "spreadsheet_dashboard"],
    "finance/esg": ["esg"],
    "marketing/marketing_card": ["marketing_card"],
    "productivity/appointments": ["appointment"],
    "productivity/phone": ["voip"],
    "hr/employees": ["hr"],
    "hr/attendances": ["hr_attendance"],
    "hr/payroll": ["hr_payroll"],
    "hr/time_off": ["hr_holidays"],
    "hr/recruitment": ["hr_recruitment"],
    "hr/fleet": ["fleet"],
    "hr/appraisals": ["hr_appraisal"],
    "hr/referrals": ["hr_referral"],
    "hr/frontdesk": ["frontdesk"],
    "hr/lunch": ["lunch"],
    "hr/skills": ["hr_skills"],
    "inventory_and_mrp/inventory": ["stock"],
    "inventory_and_mrp/manufacturing": ["mrp"],
    "inventory_and_mrp/purchase": ["purchase"],
    "inventory_and_mrp/quality": ["quality", "quality_control"],
    "inventory_and_mrp/maintenance": ["maintenance"],
    "inventory_and_mrp/plm": ["mrp_plm"],
    "inventory_and_mrp/repairs": ["repair"],
    "inventory_and_mrp/barcode": ["stock_barcode"],
    "inventory_and_mrp/subcontracting": ["mrp_subcontracting"],
    "sales/sales": ["sale", "sale_management"],
    "sales/crm": ["crm"],
    "sales/point_of_sale": ["point_of_sale"],
    "sales/subscriptions": ["sale_subscription"],
    "sales/rental": ["sale_renting"],
    "sales/members": ["membership"],
    "services/project": ["project"],
    "services/helpdesk": ["helpdesk"],
    "services/field_service": ["industry_fsm"],
    "services/planning": ["planning"],
    "services/timesheets": ["hr_timesheet"],
    "productivity/calendar": ["calendar"],
    "productivity/discuss": ["mail"],
    "productivity/knowledge": ["knowledge"],
    "productivity/voip": ["voip"],
    "productivity/whatsapp": ["whatsapp"],
    "productivity/documents": ["documents"],
    "productivity/sign": ["sign"],
    "productivity/spreadsheet": ["spreadsheet", "spreadsheet_dashboard"],
    "productivity/to_do": ["project_todo"],
    "productivity/data_cleaning": ["data_cleaning", "data_merge"],
    "productivity/ai": ["ai"],
    "productivity/dashboards": ["spreadsheet_dashboard"],
    "marketing/email_marketing": ["mass_mailing"],
    "marketing/sms_marketing": ["mass_mailing_sms"],
    "marketing/social_marketing": ["social"],
    "marketing/events": ["event"],
    "marketing/marketing_automation": ["marketing_automation"],
    "marketing/surveys": ["survey"],
    "websites/website": ["website"],
    "websites/ecommerce": ["website_sale"],
    "websites/blog": ["website_blog"],
    "websites/forum": ["website_forum"],
    "websites/livechat": ["im_livechat"],
    "websites/elearning": ["website_slides"],
    "studio": ["web_studio"],
}


def modules_of(path):
    parts = path.split("/")
    for depth in (2, 1):
        key = "/".join(parts[:depth])
        if key in MODULES:
            return MODULES[key]
    return []


def build(root, version="19.0"):
    """The user documentation (content/applications) and, when it is checked
    out, the developer documentation (content/developer)."""
    root = pathlib.Path(root)
    applications = root / "content" / "applications"
    if not applications.is_dir():
        raise SystemExit(f"No content/applications under {root}")
    pages = []
    for section in ("applications", "developer"):
        folder = root / "content" / section
        if not folder.is_dir():
            continue
        for file in sorted(folder.rglob("*.rst")):
            relative = file.relative_to(folder).with_suffix("").as_posix()
            page = read_page(file.read_text(encoding="utf-8"))
            if not page["title"]:
                continue
            parts = relative.split("/")
            if section == "applications":
                path, category, modules = relative, parts[0], modules_of(relative)
                app = parts[1] if len(parts) > 1 else parts[0]
            else:
                # howtos, reference, tutorials: about the framework, not an app
                path, category, modules, app = f"developer/{relative}", "developer", [], parts[0]
            pages.append({
                "path": path,
                "url": SITE.format(version=version, section=section, path=relative),
                "category": category,
                "app": app,
                "modules": modules,
                "title": page["title"],
                "description": page["description"],
                "sections": page["sections"],
            })
    return pages


def main(arguments):
    if not arguments:
        raise SystemExit(__doc__)
    version = arguments[1] if len(arguments) > 1 else "19.0"
    pages = build(arguments[0], version)
    if len(arguments) > 2:
        target = pathlib.Path(arguments[2]).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
    else:
        target = pathlib.Path(__file__).resolve().parent.parent / "data" / "odoo_docs.jsonl.gz"
    with gzip.open(target, "wt", encoding="utf-8", compresslevel=9) as output:
        output.write(json.dumps({"version": version, "pages": len(pages),
                                 "license": "CC BY-SA 4.0, https://github.com/odoo/documentation"},
                                ensure_ascii=False) + "\n")
        for page in pages:
            output.write(json.dumps(page, ensure_ascii=False) + "\n")
    sections = sum(len(page["sections"]) for page in pages)
    print(f"{len(pages)} pages, {sections} passages, {target.stat().st_size // 1024} KB -> {target}")


if __name__ == "__main__":
    main(sys.argv[1:])

"""Knowing standard Odoo: its official documentation, searched in the database."""
from . import base

APPS = (
    "accounting", "expenses", "payment_providers", "documents", "sign", "spreadsheet", "esg",
    "employees", "attendances", "payroll", "time_off", "recruitment", "fleet", "appraisals",
    "referrals", "frontdesk", "lunch", "skills",
    "inventory", "manufacturing", "purchase", "quality", "maintenance", "plm", "repairs", "barcode",
    "sales", "crm", "point_of_sale", "subscriptions", "rental", "members",
    "project", "helpdesk", "field_service", "planning", "timesheets",
    "calendar", "discuss", "knowledge", "voip", "whatsapp", "to_do", "data_cleaning", "ai",
    "appointments", "phone",
    "email_marketing", "sms_marketing", "social_marketing", "events", "marketing_automation",
    "surveys", "marketing_card",
    "website", "ecommerce", "blog", "forum", "livechat", "elearning",
    "essentials", "general", "studio",
    # The developer documentation: the framework, not an app
    "howtos", "reference", "tutorials",
)


@base.register
class SearchOdooDocumentation(base.Tool):
    name = "search_odoo_documentation"
    description = (
        "Search the official documentation of Odoo 19: every app of Community and Enterprise, for "
        "how something works or is done in standard Odoo (a setting, the steps of a task, what a "
        "field or a button is for), and the developer documentation (the ORM, fields, views, "
        "domains, security, automated actions, QWeb, the JavaScript framework; apps howtos, "
        "reference, tutorials) for technical questions and for writing code or a domain. Use it "
        "for \"how do I\", \"what is\" and \"how is it done in code\" questions before answering "
        "from memory. Search with a few English words, as the documentation writes them: "
        "\"quotation template\", \"reordering rules\", \"computed field\", \"record rules\"; give "
        "the app when you know it. Each result ends with the link of its page: give the link to "
        "the person at the end of your answer. What this database changes comes from "
        "search_database_profile, not from here, and wins where the two differ."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "minLength": 2, "maxLength": 120},
            "app": {
                "type": "string",
                "enum": list(APPS),
                "description": "To look in the documentation of one app only.",
            },
        },
    }

    def run(self, env, arguments, screen):
        Page = env["nudge.doc.page"]
        modules = Page._modules_of_model((screen or {}).get("model"))
        passages = env["nudge.doc.passage"]._search_text(
            arguments["query"], app=arguments.get("app"), modules=modules)
        if not passages:
            if not Page.sudo().search_count([]):
                return base.ToolResult(
                    "The documentation is not loaded in this database. Answer from what you "
                    "know, and say that the documentation could not be checked.")
            return base.ToolResult(
                "Nothing in the documentation matches these words. Try other English words, "
                "fewer or more general, or without the app.")
        return base.ToolResult(passages._render())

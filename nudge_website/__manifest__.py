{
    "name": "Nudge: Website Assistant",
    "summary": "Website and portal chat bubble from what is published",
    "description": """
A small chat bubble on the public website and in the portal.

A visitor with no login is answered only from what the company published:
the products of the shop (with the price the website shows, the availability
and the link of the page), the company files marked public, and the text of
the published pages. Nothing else is ever read, nothing is created, and what
is not covered is refused politely with the contact link. A customer who is
logged in is also answered about their own orders, invoices, deliveries and
tickets, with their own rights, and gets the link of the portal page.

"Talk to a person" opens Live Chat when it is installed, otherwise the
contact form; the conversation is attached to the request only if the
visitor agrees. Questions the assistant could not answer are listed apart, as Visitor
Questions, for a manager to read; they never become a lesson of Nudge.
Visitor lines are archived after the days set in Settings (30 by default).

Without an AI provider, or in private mode, products and files are still
found and shown, by rules alone. Installed from Apps by an administrator who
wants it, with Nudge; never by itself.
""",
    "category": "Website",
    "version": "19.0.1.0.3",
    "author": "Ibrahim Elzenad",
    "website": "https://znad-odoo-dev.github.io",
    "support": "https://github.com/Znad-Odoo-DEV/odoo-nudge/issues",
    "license": "OPL-1",
    "images": ["static/description/banner.png"],
    "depends": ["nudge", "website", "portal"],
    "data": [
        "security/nudge_website_security.xml",
        "security/ir.model.access.csv",
        "data/nudge_website_data.xml",
        "views/nudge_source_views.xml",
        "views/nudge_website_views.xml",
        "views/res_config_settings_views.xml",
        "views/templates.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "nudge_website/static/src/scss/bubble.scss",
            "nudge_website/static/src/js/bubble.js",
        ],
        "web.assets_unit_tests": [
            "nudge_website/static/tests/**/*",
        ],
    },
    "auto_install": False,
    "installable": True,
    "application": False,
}

{
    "name": "Nudge: Website Builder",
    "summary": "Interview, 3 designs, then a website of draft pages",
    "description": """
Nudge asks six questions about the business (what it is, which languages, the
colours of the logo, the pages wanted, a shop or not, the tone), draws three
design directions of the home page side by side, and, once one is picked,
builds the website: pages made only of Odoo's own snippets, the text written
by the AI provider in each language from the company's own facts (or the
sample copy of a template when no AI may be used), pictures from the
provider's picture model or bundled placeholders, the theme colours and fonts
through Odoo's own customisation, the menu, and the privacy and terms pages.

Nothing is published until the person says so, no existing page is edited and
nothing is deleted. It runs with the person's own rights: it is for a Nudge manager
who is also a website designer. The legal pages are a starting template; have a
lawyer read them before the site is published.

Installed from Apps by an administrator who wants it, with Nudge and Website;
never by itself.
""",
    "category": "Website",
    "version": "18.0.1.0.5",
    "author": "Ibrahim Elzenad",
    "website": "https://znad-odoo-dev.github.io",
    "support": "https://github.com/Znad-Odoo-DEV/odoo-nudge/issues",
    "license": "OPL-1",
    "images": ["static/description/banner.png"],
    "depends": ["nudge", "website"],
    "data": [
        "security/ir.model.access.csv",
        "security/nudge_builder_security.xml",
        "data/templates/load.xml",
        "data/nudge_builder_data.xml",
        "data/nudge_skill_data.xml",
        "views/nudge_builder_views.xml",
        "views/nudge_builder_check_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "nudge_builder/static/src/js/run.js",
            "nudge_builder/static/src/xml/run.xml",
        ],
        "web.assets_unit_tests": [
            "nudge_builder/static/tests/**/*",
        ],
    },
    "auto_install": False,
    "installable": True,
    "application": False,
}

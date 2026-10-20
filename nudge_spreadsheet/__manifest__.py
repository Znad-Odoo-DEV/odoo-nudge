{
    "name": "Nudge: Spreadsheets",
    "summary": "Reports by Nudge as spreadsheets; installs with Spreadsheet",
    "description": """
Ask Nudge for a comparison or a report and choose a spreadsheet: it builds one
from what it read, with a formatted table, totals and a chart, and opens it.
Installed by itself when Nudge and the Spreadsheet module are both there.
""",
    "category": "Productivity",
    "version": "18.0.1.0.8",
    "author": "Ibrahim Elzenad",
    "website": "https://znad-odoo-dev.github.io",
    "support": "https://github.com/Znad-Odoo-DEV/odoo-nudge/issues",
    "license": "OPL-1",
    "images": ["static/description/banner.png"],
    "depends": ["nudge", "spreadsheet"],
    "auto_install": True,
    "data": [
        "security/nudge_spreadsheet_security.xml",
        "security/ir.model.access.csv",
        "views/nudge_spreadsheet_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "nudge_spreadsheet/static/src/assets/**/*",
        ],
        "spreadsheet.o_spreadsheet": [
            "nudge_spreadsheet/static/src/bundle/**/*",
        ],
    },
}

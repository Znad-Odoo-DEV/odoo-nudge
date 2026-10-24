{
    "name": "Nudge for Documents",
    "summary": "Nudge sheets in Documents; installs itself (Enterprise)",
    "description": """
Requires Odoo Enterprise (documents_spreadsheet).

With the Documents app and its spreadsheets installed, a report Nudge makes as
a spreadsheet is kept in the person's Documents and opened in the Documents
spreadsheet editor: collaborative, versioned, shareable, downloadable.
Installed by itself when Nudge: Spreadsheets and Documents Spreadsheet are both there.
""",
    "category": "Productivity",
    "version": "18.0.1.0.8",
    "author": "Ibrahim Elzenad",
    "website": "https://znad-odoo-dev.github.io",
    "support": "https://github.com/Znad-Odoo-DEV/odoo-nudge/issues",
    "license": "OPL-1",
    "images": ["static/description/banner.png"],
    "depends": ["nudge_spreadsheet", "documents_spreadsheet"],
    "auto_install": True,
}

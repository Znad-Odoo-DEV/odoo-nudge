{
    "name": "Nudge: Emails",
    "summary": "Designed emails in four styles, read before they are sent",
    "description": """
In Developer mode, Nudge makes emails that look made by a designer: one of
four styles (classic, modern, minimal, formal), the company's colour, the
values of the record in a table, its lines, a box of what the reader must
answer, the logo and the name of the company under it. The card shows the
email as it will be read, for a real record, and a test goes to the
administrator's own address before anyone gets it.

The email goes by itself on an event, or from a button: the button opens a
window with the email ready for the record on screen, to read and change, and
Send does what the button it goes with does (approve, confirm, validate) and
sends it, with a real Cc. Fields can be required before Send, and the work
can be done without the email when the administrator allows it.

Installed from Apps by an administrator who wants it, with Nudge and Discuss;
never by itself.
""",
    "category": "Productivity",
    "version": "17.0.1.0.13",
    "author": "Ibrahim Elzenad",
    "website": "https://znad-odoo-dev.github.io",
    "support": "https://github.com/Znad-Odoo-DEV/odoo-nudge/issues",
    "license": "OPL-1",
    "images": ["static/description/banner.png"],
    "depends": ["nudge", "mail"],
    "data": [
        "security/ir.model.access.csv",
        "security/nudge_mail_security.xml",
        "data/nudge_mail_data.xml",
        "views/nudge_email_views.xml",
        "views/nudge_mail_agent_views.xml",
    ],
    "auto_install": False,
    "installable": True,
}

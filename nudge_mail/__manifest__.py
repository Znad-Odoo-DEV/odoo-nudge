{
    "name": "Nudge: Emails",
    "summary": "Designed emails in four styles, and emails read before they go from a button, built by Nudge",
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

Installed by itself when Nudge and Discuss are both there.
""",
    "category": "Productivity",
    "version": "17.0.1.0.8",
    "author": "Ibrahim Elzenad",
    "license": "OPL-1",
    "images": ["static/description/banner.png"],
    "depends": ["nudge", "mail"],
    "data": [
        "security/ir.model.access.csv",
        "views/nudge_email_views.xml",
    ],
    "auto_install": True,
    "installable": True,
}

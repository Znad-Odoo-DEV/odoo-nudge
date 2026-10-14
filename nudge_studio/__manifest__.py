{
    "name": "Nudge: Studio (Enterprise)",
    "summary": "Rules and guidance in Studio; installs itself (Enterprise)",
    "description": """
Requires Odoo Enterprise (web_studio).

With Odoo Studio installed, Nudge builds what Studio builds: approval rules on
the buttons of a form, made after a yes in Developer mode, and its pointer
shows the way inside Studio's editor, tab by tab and field by field, when the
person asks how to do something there. Installed by itself when Nudge and
Studio are both there.
""",
    "category": "Productivity",
    "version": "19.0.1.0.7",
    "author": "Ibrahim Elzenad",
    "license": "OPL-1",
    "images": ["static/description/banner.png"],
    "depends": ["nudge", "web_studio"],
    "auto_install": True,
    "assets": {
        "web.assets_backend": [
            "nudge_studio/static/src/**/*",
        ],
        "web.assets_unit_tests": [
            "nudge_studio/static/tests/**/*",
        ],
    },
}

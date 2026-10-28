{
    "name": "Nudge: Website Builder Connectors",
    "summary": "Pull pictures, places and reviews into a website from Google Drive, Google Business Profile and Instagram",
    "description": """
Website Builder Connectors
==========================
Each website designer connects their own Google account (Drive read-only and
Business Profile) and their own Instagram account through the consent screen of
the provider. Nothing is read with anybody else's sign-in.

* Google Drive: a folder, by its link, gives pictures.
* Google Business Profile: the places (address, phone, hours, map) and the reviews (first name, text and stars) of the business.
* Instagram: the latest pictures, with their caption kept only if you tick it.

Every pull opens a preview first. You tick what to take and nothing is written
before your yes. Pictures follow the limits of the Website Builder (size, type,
location data removed). Reviews are copied word for word; the AI never rewrites one.

The sign-in is the one of Nudge Connectors: the tokens are kept for the person who
signed in, renewed when they end, and never written to a log. An administrator sets
the OAuth clients once (Settings, Nudge, Connectors).

Installed from Apps by an administrator who wants it, with Website Builder and Connectors.
""",
    "category": "Website",
    "version": "18.0.1.0.0",
    "author": "Ibrahim Elzenad",
    "website": "https://znad-odoo-dev.github.io",
    "support": "id7sp22@gmail.com",
    "license": "OPL-1",
    "depends": ["nudge_builder", "nudge_connect"],
    "data": [
        "security/ir.model.access.csv",
        "data/nudge_builder_connect_data.xml",
        "views/nudge_builder_connect_views.xml",
    ],
    "auto_install": False,
    "installable": True,
    "application": False,
}

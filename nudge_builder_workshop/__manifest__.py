{
    "name": "Nudge: Website Builder Car Workshop",
    "summary": "Car workshop services on the website, with a booking request that reaches the shop",
    "description": """
Website Builder Car Workshop
============================
A plugin of the Website Builder. It adds a kind of content, "workshop service" (oil
change, brakes, tyres...), with a category, a duration and a price from, and a public
booking request page, /workshop-booking: the car, the service, the day wanted and how
to reach the visitor.

- The request becomes a lead in CRM when CRM is installed, and a to-do for the owner of the site when not.
- The page keeps the guards of the quote form of the Website Builder: a hidden field a robot fills,
  a minimum time between showing and sending, a limit per address and per website, and a consent box.
  A robot is thanked as a person is, and nothing is made for it.
- Services are the records you write: nothing is invented; with none, the section is not placed.
- A template, "Car workshop with booking", uses it. Site Check looks at the services.

Installed from Apps by an administrator who wants it, with Website Builder.
""",
    "category": "Website",
    "version": "18.0.1.0.0",
    "author": "Ibrahim Elzenad",
    "website": "https://znad-odoo-dev.github.io",
    "support": "id7sp22@gmail.com",
    "license": "OPL-1",
    "depends": ["nudge_builder"],
    "data": [
        "security/ir.model.access.csv",
        "security/nudge_builder_workshop_security.xml",
        "views/nudge_builder_workshop_views.xml",
        "views/booking_templates.xml",
    ],
    "auto_install": False,
    "installable": True,
    "application": False,
}

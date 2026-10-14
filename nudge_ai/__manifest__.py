{
    "name": "Nudge: Odoo AI",
    "version": "19.0.1.0.5",
    "category": "Productivity",
    "summary": "The AI app's sources, agents and keys; installs with AI",
    "description": """
Nudge: Odoo AI
==============
A bridge between Nudge and the AI app of Odoo Enterprise.

- The documents, links and articles given to the AI agents as sources are
  searched by Nudge when it answers, for the people who may read them.
- What the agents are told (their instructions and topics) is told to
  Nudge too, so that the company's own guidance applies.
- The server actions marked "Use in AI" are at the agent's hand, each one
  shown to the person before it runs.
- A Nudge provider can take the key set in the AI app: typed once.

Installs itself where both Nudge and the AI app are installed.
""",
    "author": "Ibrahim Elzenad",
    "license": "OPL-1",
    "images": ["static/description/banner.png"],
    "depends": ["nudge", "ai"],
    "data": [
        "views/nudge_provider_views.xml",
    ],
    "auto_install": True,
    "installable": True,
}

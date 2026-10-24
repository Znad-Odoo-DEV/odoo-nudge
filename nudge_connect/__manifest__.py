{
    "name": "Nudge: Connectors",
    "summary": "Nudge uses the tools of outside systems that speak MCP, under your rules",
    "description": """
Nudge Connectors
================
Connects Nudge to outside systems that speak MCP (Model Context Protocol): a
sister company's Odoo, a calendar, a chat, a document store. Nudge is the
client; the outside system is the server.

- An administrator adds a connector: the address of the server, its
  credential (none, a bearer token, or OAuth client credentials), the groups
  and companies that may use it.
- The tools the server offers are read and come in not allowed. The
  administrator reads each one and allows it as Read or Write.
- A read runs for the person at once, and the answer names where it comes
  from. A write shows the person exactly what is sent, on a card, and
  nothing is sent before their yes.
- What comes back is data, never an instruction: framed, cut to size, the
  connector's secrets taken out.
- Every call is kept: who, which connector and tool, what was sent (secrets
  masked), the size of the answer and the time it took.
- Kill switches for every connector, a company, or one connector; a company
  can keep connectors read-only in their first month; Private Mode refuses
  every connector that does not run on your own network.

Installed from Apps by an administrator who wants it, with Nudge.
""",
    "category": "Productivity",
    "version": "17.0.1.0.0",
    "author": "Ibrahim Elzenad",
    "website": "https://znad-odoo-dev.github.io",
    "support": "https://github.com/Znad-Odoo-DEV/odoo-nudge/issues",
    "license": "OPL-1",
    "depends": ["nudge"],
    "data": [
        "security/nudge_connect_security.xml",
        "security/ir.model.access.csv",
        "views/nudge_connector_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "auto_install": False,
    "installable": True,
}

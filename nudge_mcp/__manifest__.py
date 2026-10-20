{
    "name": "Nudge: MCP Server",
    "summary": "MCP server: Claude Desktop and ChatGPT use Nudge as you",
    "description": """
Nudge MCP Server
================
Opens Nudge to the assistants that speak MCP (Model Context Protocol): Claude
Desktop, ChatGPT and others. They look up records, read the documentation of
Odoo and the company files, and, when you allow it, prepare changes.

- Off until an administrator turns it on (Settings, Nudge).
- Each person makes a key of their own; the assistant acts as that person,
  with their access rights, record rules and companies. Nothing runs as
  an administrator.
- Reading and writing are separate. A change is never made at once: the
  assistant gets a token, shows the person what will change, and the change
  is made only when it calls confirm with that token.
- Everything lands in the Requests and Changes of Nudge, marked "MCP:" and
  the name of the assistant.
- Private mode, the budgets and the limit of answers at the same time of
  Nudge apply.

Installed from Apps by an administrator who wants it, with Nudge.
""",
    "category": "Productivity",
    "version": "19.0.1.0.3",
    "author": "Ibrahim Elzenad",
    "website": "https://znad-odoo-dev.github.io",
    "support": "https://github.com/Znad-Odoo-DEV/odoo-nudge/issues",
    "license": "OPL-1",
    "images": ["static/description/banner.png"],
    "depends": ["nudge"],
    "data": [
        "security/nudge_mcp_security.xml",
        "security/ir.model.access.csv",
        "views/nudge_mcp_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "assets": {
        "web.assets_unit_tests": [
            "nudge_mcp/static/tests/**/*",
        ],
    },
    "auto_install": False,
    "installable": True,
}

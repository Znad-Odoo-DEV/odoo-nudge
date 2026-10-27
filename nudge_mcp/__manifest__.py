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
- The assistant signs in with OAuth 2.1 (PKCE): the person allows it in
  Odoo, chooses what it may do (read, prepare changes, read what developers
  build) and can revoke it in their preferences. Keys made before (legacy)
  work until 2027-01-26 only. The assistant acts as that person, with their access
  rights, record rules and companies. Nothing runs as an administrator.
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
    "version": "18.0.1.1.2",
    "author": "Ibrahim Elzenad",
    "website": "https://znad-odoo-dev.github.io",
    "support": "id7sp22@gmail.com",
    "license": "OPL-1",
    "images": ["static/description/banner.png"],
    "depends": ["nudge"],
    "data": [
        "security/nudge_mcp_security.xml",
        "security/ir.model.access.csv",
        "views/nudge_mcp_views.xml",
        "views/res_config_settings_views.xml",
        "views/nudge_mcp_oauth_views.xml",
        "views/nudge_mcp_oauth_templates.xml",
    ],
    "assets": {
        "web.assets_unit_tests": [
            "nudge_mcp/static/tests/**/*",
        ],
    },
    "auto_install": False,
    "installable": True,
}

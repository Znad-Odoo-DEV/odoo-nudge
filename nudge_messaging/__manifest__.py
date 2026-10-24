{
    "name": "Nudge WhatsApp & Telegram",
    "summary": "Ask Nudge from WhatsApp or Telegram; you say yes in Odoo",
    "description": """
Nudge Messaging
===============
Talk to Nudge from your phone, on WhatsApp (Business Cloud API) or Telegram.

- Off until an administrator turns it on and enters the keys of the channel
  (Settings, Nudge, Messaging assistants). The keys are never shown again.
- Each person links their own phone with a one-time code from their
  Preferences. The messages of that phone run as that person: their rights,
  their companies. An unknown phone gets one polite line and nothing else.
- Questions are answered in the chat, short, with a link that opens the
  record in Odoo.
- A change is never made from the chat. Nudge prepares it and answers with a
  link: the person reads it and says yes inside Odoo.
- Voice notes are written out when the provider can, pictures of documents
  become draft records waiting for the same yes.
- Private mode, the budgets and the limit of answers at the same time of Nudge
  apply. Every message is a Request in What Nudge Did, marked WhatsApp or
  Telegram.

Installed from Apps by an administrator who wants it, with Nudge and Nudge MCP.
""",
    "category": "Productivity",
    "version": "18.0.1.0.5",
    "author": "Ibrahim Elzenad",
    "website": "https://znad-odoo-dev.github.io",
    "support": "https://github.com/Znad-Odoo-DEV/odoo-nudge/issues",
    "license": "OPL-1",
    "images": ["static/description/banner.png"],
    "depends": ["nudge", "nudge_mcp"],
    "data": [
        "security/nudge_messaging_security.xml",
        "security/ir.model.access.csv",
        "data/nudge_messaging_cron.xml",
        "data/nudge_messaging_data.xml",
        "views/nudge_messaging_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "uninstall_hook": "uninstall_hook",
    "auto_install": False,
    "installable": True,
}

"""The catalogue of connectors: ready-made starting points an administrator picks from.

A preset is a table row, not a promise: the address is a pattern to check, the remote may have moved,
and the handshake still reads the tools the server really offers. Every tool comes in Not Allowed,
except the ones a preset names in ``read``, and only when the server does not say it may delete. A
tool a preset names in ``write`` is never allowed by the preset: it is shown as a recommendation and
the administrator allows it after reading it.

Each row: ``key`` (the connector key proposed), ``label``, ``url`` (the address or its pattern),
``auth`` (none, bearer or oauth: what the module speaks), ``scopes`` (what to ask for when the
credential is made, words only), ``read`` and ``write`` (remote tool names), ``note`` (one line for
the administrator).
"""
PRESETS = (
    {
        "id": "odoo", "label": "Another Odoo database (sister company)", "key": "sister",
        "url": "https://odoo.example.com/nudge_mcp/mcp", "auth": "bearer", "scopes": "an API key of a user of that database, with the Nudge Agent group",
        "read": ("search_records", "search_odoo_documentation"), "write": ("prepare_create",),
        "note": "The other database runs Nudge MCP Server (nudge_mcp): its tools run with the rights of the key's user.",
    },
    {
        "id": "gcalendar", "label": "Google Calendar", "key": "gcal",
        "url": "https://calendarmcp.googleapis.com/mcp/v1", "auth": "bearer",
        "scopes": "calendar.readonly (add calendar.events only if events may be created)",
        "read": ("list_events", "get_event", "list_calendars", "search_events"),
        "write": ("create_event", "update_event", "delete_event"),
        "note": "Needs a token made for a Google account; the module does not run the Google sign-in itself.",
    },
    {
        "id": "gmail", "label": "Gmail", "key": "gmail",
        "url": "https://gmailmcp.googleapis.com/mcp/v1", "auth": "bearer",
        "scopes": "gmail.readonly (add gmail.compose only if drafts may be created)",
        "read": ("search_threads", "get_thread", "list_labels", "list_drafts"),
        "write": ("create_draft", "send_message", "send_email"),
        "note": "Mail is data about people: allow the groups that need it, and keep the sending tools on Write.",
    },
    {
        "id": "slack", "label": "Slack", "key": "slack",
        "url": "https://mcp.slack.com/mcp", "auth": "bearer",
        "scopes": "channels:history, channels:read, search:read (add chat:write for posting)",
        "read": ("search_messages", "read_channel", "list_channels", "read_thread"),
        "write": ("send_message", "post_message", "add_reaction"),
        "note": "Messages read here are data, never instructions to Nudge.",
    },
    {
        "id": "teams", "label": "Microsoft Teams", "key": "teams",
        "url": "https://teams-mcp.example.com/mcp", "auth": "oauth",
        "scopes": "ChannelMessage.Read.All, Team.ReadBasic.All (add ChannelMessage.Send for posting)",
        "read": ("list_teams", "list_channels", "read_channel_messages", "search_messages"),
        "write": ("send_channel_message", "reply_to_message"),
        "note": "There is no single public address: enter the one of the gateway your company runs, and its token address.",
    },
    {
        "id": "github", "label": "GitHub", "key": "github",
        "url": "https://api.githubcopilot.com/mcp/", "auth": "bearer",
        "scopes": "repo:read, issues:read, pull_requests:read (write scopes only for the tools on Write)",
        "read": ("search_repositories", "get_file_contents", "list_issues", "get_issue", "list_pull_requests",
                 "search_issues", "search_code"),
        "write": ("create_issue", "add_issue_comment", "create_pull_request", "create_or_update_file", "merge_pull_request"),
        "note": "Use a fine-grained token limited to the repositories Nudge may see.",
    },
    {
        "id": "notion", "label": "Notion", "key": "notion",
        "url": "https://mcp.notion.com/mcp", "auth": "bearer",
        "scopes": "read content (add insert and update content only for the tools on Write)",
        "read": ("search", "fetch", "get_users"),
        "write": ("create_pages", "update_page", "create_comment"),
        "note": "The integration sees only the pages shared with it.",
    },
    {
        "id": "generic", "label": "Another MCP server", "key": "",
        "url": "https://mcp.example.com/mcp", "auth": "none", "scopes": "", "read": (), "write": (),
        "note": "Nothing is assumed: read the tools and allow each one yourself.",
    },
)
BY_ID = {preset["id"]: preset for preset in PRESETS}
CHOICES = [(preset["id"], preset["label"]) for preset in PRESETS]


def get(preset_id):
    """:return: the row of ``preset_id``, or None"""
    return BY_ID.get(preset_id)


def read_names(preset_id):
    """:return: the remote tool names the preset marks Read"""
    return frozenset((BY_ID.get(preset_id) or {}).get("read", ()))

"""The catalogue of connectors: ready-made starting points an administrator picks from.

A preset is a table row, not a promise: the address is a pattern to check, the remote may have moved,
and the handshake still reads the tools the server really offers. Every tool comes in Not Allowed,
except the ones a preset names in ``read``, and only when the server does not say it may delete. A
tool a preset names in ``write`` is never allowed by the preset: it is shown as a recommendation and
the administrator allows it after reading it.

Each row: ``key`` (the connector key proposed), ``label``, ``url`` (the address or its pattern),
``auth`` (none, bearer, oauth or oauth_user: what the module speaks), optional ``scope``, ``authorize`` and ``token`` (the provider's own addresses, when they are fixed), ``scopes`` (what to ask for when the
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
        "id": "gcalendar", "label": "Google Calendar (Developer Preview)", "key": "gcal",
        "url": "https://calendarmcp.googleapis.com/mcp/v1", "auth": "oauth_user",
        "scope": "https://www.googleapis.com/auth/calendar.readonly",
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth", "token": "https://oauth2.googleapis.com/token",
        "scopes": "calendar.readonly (add calendar.events only if events may be created)",
        "read": ("list_events", "get_event", "list_calendars", "search_events"),
        "write": ("create_event", "update_event", "delete_event"),
        "note": "Developer Preview at Google. In your Google Cloud project enable the Calendar MCP API and create an OAuth client (web application) with the redirect address of this connector; each person then signs in with their own Google account.",
    },
    {
        "id": "gmail", "label": "Gmail (Developer Preview)", "key": "gmail",
        "url": "https://gmailmcp.googleapis.com/mcp/v1", "auth": "oauth_user",
        "scope": "https://www.googleapis.com/auth/gmail.readonly",
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth", "token": "https://oauth2.googleapis.com/token",
        "scopes": "gmail.readonly (add gmail.compose only if drafts may be created)",
        "read": ("search_threads", "get_thread", "list_labels", "list_drafts"),
        "write": ("create_draft", "send_message", "send_email"),
        "note": "Developer Preview at Google. In your Google Cloud project enable the Gmail MCP API and create an OAuth client (web application) with the redirect address of this connector. Mail is data about people: allow the groups that need it, and keep the sending tools on Write.",
    },
    {
        "id": "slack", "label": "Slack", "key": "slack",
        "url": "https://mcp.slack.com/mcp", "auth": "oauth_user",
        "scopes": "channels:history, channels:read, search:read (add chat:write for posting)",
        "read": ("search_messages", "read_channel", "list_channels", "read_thread"),
        "write": ("send_message", "post_message", "add_reaction"),
        "note": "Create a Slack app at api.slack.com, add the redirect address of this connector, and enter its client ID and secret here. Messages read here are data, never instructions to Nudge.",
    },
    {
        "id": "github", "label": "GitHub", "key": "github",
        "url": "https://api.githubcopilot.com/mcp/", "auth": "bearer",
        "scopes": "repo:read, issues:read, pull_requests:read (write scopes only for the tools on Write)",
        "read": ("search_repositories", "get_file_contents", "list_issues", "get_issue", "list_pull_requests",
                 "search_issues", "search_code"),
        "write": ("create_issue", "add_issue_comment", "create_pull_request", "create_or_update_file", "merge_pull_request"),
        "note": "A personal access token as Bearer (fine-grained, limited to the repositories Nudge may see) works at once; for a sign-in per person, change Authentication to OAuth Sign-In and enter the client of a GitHub OAuth app.",
    },
    {
        "id": "notion", "label": "Notion", "key": "notion",
        "url": "https://mcp.notion.com/mcp", "auth": "oauth_user",
        "scopes": "read content (add insert and update content only for the tools on Write)",
        "read": ("search", "fetch", "get_users"),
        "write": ("create_pages", "update_page", "create_comment"),
        "note": "Notion publishes how to sign in and registers clients by itself: press Find How to Sign In. Each person sees only what their own Notion account may see.",
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

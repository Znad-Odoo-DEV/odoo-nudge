# Nudge: MCP Server

Lets Claude Desktop, ChatGPT and other assistants that speak MCP (Model Context Protocol) use the
tools of [Nudge](../nudge/README.md), as the person whose key they hold: with that person's access
rights, record rules and companies. Nothing runs as an administrator.

Community and Enterprise. Odoo 17, 18 and 19 (one branch of the store for each). No new Python package. A key lasts 1, 3 or 12 months on
Odoo 18 and 19; Odoo 17 has no expiry on API keys, so there a key lasts until the person deletes it in their preferences.

## What an assistant can do

- **Look up**: records (`search_records`, `read_record`, `find_record`, `aggregate_records`), the
  models and fields (`describe_model`, `locate_data`), the screens and menus, the documentation of
  Odoo, the company files, the products.
- **Read** the same documentation and company files as MCP resources (`nudge://doc/<id>`,
  `nudge://file/<id>`); the files only for the groups they are shared with.
- **Prepare changes**, when the administrator allows it: `prepare_create`, `prepare_write`,
  `call_method`. Nothing is changed by them. They return what would change and a token; the
  person is asked in Odoo (a notification and Nudge, Assistant Approvals), and the change is made only when the
  assistant calls `confirm` with the token after the person has approved it there. A company may switch on
  "External assistants may confirm their own changes (unsafe)"; post, validate, pay and the like always wait. `decline` drops it. A token is for one person, once, for 30 minutes.
- The tools for developers (code, SQL, automations, views) are never offered.

## Switching it on

Off until an administrator turns it on, in Settings, Nudge, External Assistants (MCP):

| Setting | Meaning |
|---|---|
| Open Nudge to External Assistants | The endpoint answers. Off: it answers 403 to everyone. |
| What They May Use | Read only, or read and prepare changes. |
| Allow Changes from External Assistants | Per company. A change also needs the group **Agent User** for the person and Agent Mode of Nudge. |

Only people of the group **Nudge / External Assistant** can make a key. Take a person out of the
group and their keys stop working at once.

## Connecting

The page Nudge, External Assistants (also reached from Settings) shows the address, makes the key
(an API key of the scope `nudge_mcp`, valid for 1 month, 3 months or 1 year, shown once) and has the text to
paste:

```json
{"mcpServers": {"odoo-nudge": {
  "command": "npx",
  "args": ["-y", "mcp-remote", "https://odoo.example.com/nudge/mcp", "--header", "Authorization:${NUDGE_AUTH}"],
  "env": {"NUDGE_AUTH": "Bearer YOUR_NUDGE_KEY"}}}}
```

Assistants that take an address and a header use `https://odoo.example.com/nudge/mcp` and
`Authorization: Bearer <key>`. The Test button calls `initialize` with a pasted key, as an assistant
does.

## The endpoint

`POST /nudge/mcp`, JSON-RPC 2.0, MCP Streamable HTTP (2025-06-18; 2025-03-26 and 2024-11-05 are
answered too). Methods: `initialize`, `ping`, `tools/list`, `tools/call`, `resources/list`,
`resources/read`, `resources/templates/list`; notifications get `202`. The server answers each
request with JSON and opens no stream to the client (`GET` answers `405`), which the protocol
allows. The key travels in `Authorization: Bearer`; there is no cookie and no session of Odoo, so
there is nothing to forge across sites. Refused: no key or a wrong one `401`, Nudge closed `403`,
ten wrong keys in a minute from one address `429`, a message over 1 MB `413`.

## What is kept, and the limits of Nudge

- Every call is a request in **What Nudge Did**, Requests: person, `MCP: <name of the assistant>` as
  its origin, tool, duration, tokens (0: no AI provider is called), outcome. Every change made, and
  every one declined or expired, is in Changes, with the same origin, and can be undone as any change
  of Nudge. The log is written by Nudge only.
- Private mode of the company applies: MCP itself runs on your server and is allowed, a tool that
  would call an AI provider is refused when the provider does not run on your own server.
- The budgets of the person and of the company are checked on every call. The limit of answers at the
  same time (`nudge.max_streams`) counts the answers of Nudge and the calls of the assistants together.

## Tests

```
odoo-bin -d <db> -i nudge_mcp --test-tags /nudge_mcp --stop-after-init
```

# Nudge: MCP Server

Lets Claude Desktop, ChatGPT and other assistants that speak MCP (Model Context Protocol) use the
tools of [Nudge](../nudge/README.md), as the person who signed them in: with that person's access
rights, record rules and companies. Nothing runs as an administrator.

An assistant signs in with **OAuth 2.1** (authorization code with PKCE), as the MCP authorization
specification asks ([revision 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)):
the person allows it in Odoo and chooses what it may do. The API keys made before OAuth still work
and are marked **legacy**.

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
  assistant calls `confirm` with the token after the person has approved it there. `decline` drops it. A token
  is for one person, once, for 30 minutes. See "Changes an assistant may confirm alone" below for the only exception.
- **Read what developers build**, for administrators with Developer mode, when the administrator allows it:
  `read_automations`, `read_view`, `check_python_code`. The tools that build or run code (automations,
  server actions, Python, SQL, views) are never offered.
- **Prompts** (MCP `prompts/list`, `prompts/get`): announced only when a module adds prompts through
  `nudge.mcp._mcp_prompts()` (the prompt library of Nudge is the intended source). None are shipped here.

## Switching it on

Off until an administrator turns it on, in Settings, Nudge, External Assistants (MCP):

| Setting | Meaning |
|---|---|
| Open Nudge to External Assistants | The endpoint answers. Off: it answers 403 to everyone. |
| What They May Use | Read only; read and prepare changes; or also read what developers build (administrators). |
| Allow Changes from External Assistants | Per company. A change also needs the group **Agent User** for the person and Agent Mode of Nudge. |
| Changes They May Confirm Alone | The allow-list below. Empty by default. |
| Sign-in Registration | Assistants register themselves (default), or only those an administrator registered. |

Only people of the group **Nudge / External Assistant** can allow an assistant or make a key. Take a
person out of the group, or archive them, and their sign-ins and keys stop working at once.

## Connecting with sign-in (OAuth 2.1)

Give the assistant the address `https://odoo.example.com/nudge/mcp`: in Claude (claude.ai and
Claude Desktop) Settings, Connectors, Add custom connector; in Claude Code
`claude mcp add --transport http odoo-nudge https://odoo.example.com/nudge/mcp`; in other assistants,
an MCP server by its address. The assistant opens Odoo; the person signs in, reads which assistant
asks, where it will send them back, and what it may do, unticks what they do not want, and presses
Allow. The page cannot be framed and never sends anyone anywhere before they answered.

The address of Odoo (`web.base.url`) must be its public **https** address: it is the issuer of the
tokens and the start of the resource the tokens are for.

| Endpoint | What |
|---|---|
| `/.well-known/oauth-protected-resource/nudge/mcp` (and at the root) | RFC 9728 metadata: the resource, its authorization server, the scopes |
| `/.well-known/oauth-authorization-server` | RFC 8414 metadata: PKCE `S256` only, `iss` in the answer (RFC 9207) |
| `/nudge/oauth/register` | RFC 7591 dynamic registration (deprecated by the specification, but what Claude uses when there is no Client ID Metadata Document); a registration alone opens nothing |
| `/nudge/oauth/authorize` | The page of consent, as the person signed in to Odoo |
| `/nudge/oauth/token` | `authorization_code` with the PKCE verifier, `refresh_token` |
| `/nudge/oauth/revoke` | RFC 7009 |

- **Scopes**, one per group of tools: `nudge.read` (look up, resources, prompts), `nudge.write`
  (prepare changes, confirm, decline; includes reading), `nudge.developer` (read what developers build;
  includes reading). A person can give only what they could use themselves. A tool outside the token's
  scopes answers `403` with `WWW-Authenticate: Bearer error="insufficient_scope", scope="..."`, so the
  assistant asks again.
- **Tokens**: an access token lasts 60 minutes (`nudge_mcp.oauth_access_minutes`, 5 to 1440), a refresh
  token 30 days (`nudge_mcp.oauth_refresh_days`, 1 to 365) and changes at every use. A refresh token
  used twice ends the whole sign-in; a code is good once, for two minutes, for its client, address and
  PKCE verifier, and a code used twice takes back the tokens it gave. Only SHA-256 hashes of codes,
  tokens and client secrets are stored.
- **Redirect addresses**: https, or http on `localhost` / `127.0.0.1` (any port, RFC 8252). The page
  shows the host and warns when it is this computer.
- **Connected assistants**: each person sees theirs in their preferences, tab Connected Assistants,
  with what each may do, when it was last used and until when it stays signed in, and revokes one with
  a click. Administrators see and revoke all of them, and the registered assistants, from Settings.
- Not done: Client ID Metadata Documents (the server would have to fetch an address the assistant
  names; left for the egress allow-list of Nudge).

## Connecting with a key (legacy)

For an assistant that cannot sign in. The page Nudge, External Assistants (also reached from Settings)
shows the address, makes the key (an API key of the scope `nudge_mcp`, valid for 1 month, 3 months or
1 year, shown once) and has the text to paste. A key opens reading and preparing changes, as before;
it never opens the tools for developers, and a change prepared with it always waits for the person.

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
`resources/read`, `resources/templates/list`, and `prompts/list`, `prompts/get` when there are prompts;
notifications get `202`. The server answers each
request with JSON and opens no stream to the client (`GET` answers `405`), which the protocol
allows. The access token or the key travels in `Authorization: Bearer`; there is no cookie and no
session of Odoo, so there is nothing to forge across sites. Refused: no credential or a wrong or
expired one `401` with `WWW-Authenticate: Bearer resource_metadata="...", scope="..."` (where to sign
in), a tool outside the scopes `403`, Nudge closed `403`, ten wrong keys in a minute for one key id or
fifty from one address `429`, a message over 1 MB `413`.

## Changes an assistant may confirm alone

Off by default: every change waits for the person's yes in Odoo. Settings, Nudge, External Assistants,
Changes They May Confirm Alone lists the exceptions, per company: a tool (create, change, press a
button with its method) on one model, for any assistant signed in with OAuth or for one of them. An
assistant may then confirm the change it prepared itself, without the person; the step is marked
**Confirmed by the Assistant** in Changes. Never for a key (legacy), never for another assistant than
the one that prepared it, and never for a sensitive change: accounting, payments, stock, payslips,
expenses, users, groups, companies, `ir.*` and methods that post, validate, pay, confirm, cancel,
archive and the like are refused when the line is saved and again when the change is confirmed. This
replaces the company switch "External assistants may confirm their own changes (unsafe)" of 1.0.x,
which is gone: after the update every change waits for the person until an administrator lists one.

## What is kept, and the limits of Nudge

- Every call is a request in **What Nudge Did**, Requests: person, `MCP: <name of the assistant>` as
  its origin (for a sign-in, the registered name of the client and the client itself, not what the
  assistant says of itself), tool, duration, tokens (0: no AI provider is called), outcome. Every change made, and
  every one declined or expired, is in Changes, with the same origin, and can be undone as any change
  of Nudge. The log is written by Nudge only.
- Private mode of the company applies: MCP itself runs on your server and is allowed, a tool that
  would call an AI provider is refused when the provider does not run on your own server.
- The budgets of the person and of the company are checked on every call. The limit of answers at the
  same time (`nudge.max_streams`, and the limit of the provider) counts the answers of Nudge and the calls of the
  assistants together, and they share one line: a call takes a place for its work only (never while the
  person decides on a change) and, when none is free, gets a JSON-RPC error `-32000` with
  `data: {"reason": "busy", "retryAfter": <seconds>}` and a `Retry-After` header; it does not wait, and
  never goes before a question waiting in the panel.

## Tests

```
odoo-bin -d <db> -i nudge_mcp --test-tags /nudge_mcp --stop-after-init
```

`TestMcp` covers the endpoint with keys (legacy); `TestMcpOAuth` the whole sign-in over HTTP: metadata,
registration, consent, PKCE, code reuse, refresh rotation and reuse, revocation, scopes per tool, an
archived person, another person's form, multi-company, the allow-list, prompts.

# Nudge: Connectors

Lets [Nudge](../nudge/README.md) use the tools of outside systems that speak MCP (Model Context
Protocol): a sister company's Odoo, a calendar, a chat, a document store. Nudge is the MCP client
here; the outside system is the server. (The other way round, an assistant outside Odoo using Nudge,
is [Nudge: MCP Server](../nudge_mcp/README.md).)

Community and Enterprise. Odoo 17, 18 and 19. No new Python package (`requests`, already in Odoo).

## Adding a connector

Nudge, Configuration, Connectors (administrators, group **Settings**):

| Field | Meaning |
|---|---|
| Key | Small letters, digits and `_`. The tools reach the AI as `cx_<key>__<tool>`. |
| Server Address | The MCP endpoint (Streamable HTTP). HTTPS; HTTP only to this machine (`localhost`). |
| Runs on Your Own Network | The server is on this machine or your company's network: its address may be private, and it may be used with Private Mode on. Off: every address the name resolves to must be public. |
| Authentication | None, Bearer Token, or OAuth Client Credentials (token address, client ID and secret, scope). |
| Groups, Companies | Who may use it (also an **Agent User**) and in which companies. Empty: nobody, nowhere. |
| Time Allowed, Longest Answer | At most 60 seconds, never longer than the request it runs in; at most 5000 characters read by the AI. |

Then **Read the Tools**. Every tool the server offers comes in **Not Allowed**. Open each one, read
what the server says it does and what it takes, and allow it as:

- **Read**: runs for the person at once, in Agent mode. The answer names the connector it comes from.
- **Write (Asks First)**: the person sees a card with the connector, the tool, the address and the
  exact payload; nothing is sent before their yes. A payload too long to show in full is not sent.

**Activate** the connector. **Stop Now** cuts it off at once (a card waiting for a yes is refused too).

When a tool's description or arguments change on the server, the next **Read the Tools** takes its
permission back: it is offered again only once an administrator has allowed it again. A tool the
server no longer lists is kept, marked No Longer Offered, and is not offered.

## From the catalogue

On a new connector, pick **From Catalogue**: another Odoo database (through its own Nudge MCP server),
Google Calendar, Gmail, Slack, Microsoft Teams, GitHub, Notion or a generic MCP server. The address, the
kind of credential and a note with the recommended scopes are filled in (names and keys you already typed are
kept); on a saved connector the **Fill From Catalogue** button does the same. The table is `presets.py`.

A preset is a starting point, not a promise that the remote exists or still has that address. The handshake
reads the real tools; every one comes in Not Allowed, except a new tool the preset names as Read that the
server does not say writes or deletes. A preset never allows a Write tool.

## Budgets and the usage ledger

Every call that reaches a connector's server is a line of the usage ledger with the kind **Connector Call**
(no tokens, no cost, one call), visible in Settings, Nudge, What Was Spent. Settings, Connectors (MCP) holds the
limits **Connector Calls per Person and per Day** and **per Company and per Month** (0 is no limit). At a limit
or when the token or cost budget of Nudge is used up, the call is refused with the same kind of message as a
call to a provider, nothing is sent, and the refusal is in the connector's log.

## Remembering reads

**Remember Reads (Seconds)** on a connector (0 is off; 60 is a good start, 3600 at most) keeps the answer of a
Read tool for the same connector, tool, arguments, person and company, so that asking again in a conversation
does not reach the server. A kept answer is logged as a hit, is not a call for the budget, and is framed as data
exactly like a fresh one. Writes and failures are never kept; changing the connector or one of its tools forgets
what was kept; an hourly action forgets what ran out. Private Mode changes nothing here.

## Settings

Settings, Nudge, Connectors (MCP):

| Setting | Meaning |
|---|---|
| Stop Every Connector | The kill switch of the database: nothing is sent to any connector. |
| Connectors Off | For the company in use: no connector is used in it. |
| Connectors Read-Only in Their First Month | For the company in use, on by default: the write tools of a connector are offered 30 days after it was first made active. |

## The log

Nudge, What Nudge Did, Connector Calls (administrators and Nudge managers, in their companies): every
call and every refusal, with who asked, the connector and tool, what was sent (values of arguments
named like a password, token, secret or key masked), the size of the answer and the time it took.
The steps of the conversation are in Requests and Changes as for every tool.

## Protocol

Revision **2026-07-28** of MCP ([Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http),
[changes](https://modelcontextprotocol.io/specification/2026-07-28/changelog)): no session and no
handshake; every POST carries `MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name` (tool calls) and the
version, client and capabilities in `_meta`; arguments marked `x-mcp-header` are repeated in
`Mcp-Param-<name>`, and a tool whose annotation breaks the rules is left out; answers come as JSON or
as an event stream; a result asking for more input (`input_required`) is told as a failure. A server
of 2025-11-25, 2025-06-18 or 2025-03-26 is recognised by its answer and spoken to the old way
(`initialize`, `Mcp-Session-Id`); what the server speaks is shown on the connector and kept.

Not supported: stdio servers (nothing is started inside Odoo), the deprecated HTTP+SSE transport,
OAuth with a browser sign-in per person (client credentials only), resources, prompts and
subscriptions.

## Security statement

- **Off by default, tool by tool.** A connector starts in Draft; each tool starts Not Allowed and is
  allowed by an administrator who has read it. Its description and arguments are pinned by a hash.
- **Runs as the person.** Offered only to Agent Users of the connector's groups, in its companies,
  with Agent mode open; checked when the tool list is built and again at every call and at the yes.
- **Writes wait for the person.** A write is a Nudge Action: a card with the exact payload, nothing
  sent before the yes. A card also names the other connectors read in the same conversation, so data
  leaving one system for another is seen. A tool the server marks destructive gives a sensitive card.
  A read, which runs without a card, sends at most 1000 characters of arguments, so that records of
  Odoo cannot leave through it.
- **What comes back is data.** Every answer is framed as data, `<` escaped, cut to size, with the
  connector's secrets taken out, before the AI reads it; the description of a tool reaches the AI
  after Nudge's own words and marked as coming from outside.
- **Secrets.** The bearer token and the OAuth client ID and secret are readable by administrators
  only (group Settings); an OAuth access token is kept in the memory of the process, never in the
  database. A secret is sent to its own server only: never to the AI provider, never to the log (the
  log, the errors and the answers have it taken out; nothing is written to the server log).
- **Network.** HTTPS (HTTP to `localhost` only); no redirect followed; private, loopback and
  link-local addresses refused unless the connector runs on your own network; time and size limits on
  every answer.
- **Private Mode** refuses every connector that does not run on your own network.
- **Kill switches** for the database, the company and each connector, read at every call.
- **One database at a time.** The tools of a connector are offered only in a database where this
  module is installed and the connector is active, even when another database of the same server
  has a connector with the same name (EXT-01).

## Limits

- No AI, no use: a connector's tools are used by the AI. Without a provider an administrator can still
  test the connection and read the tools.
- A connector's tools are offered in Agent and Developer modes, not in Talk.

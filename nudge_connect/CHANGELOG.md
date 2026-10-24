# Changelog

## 19.0.1.59.1 (unreleased)

- Review: the answer of a token address is read as a stream, at most 256 KB and within one deadline for the whole answer (not per read), and the connection is closed; the client-credentials token request had no cap at all, the sign-in requests no total deadline.
- Review: the reads of one conversation send at most 5000 characters of arguments to outside systems in all (1000 each as before), so text smuggled in by an outside answer cannot loop reads to carry Odoo data out; past it a read is refused with "Nothing was sent".
- Review: two requests near the end of an OAuth token no longer both send its refresh token: the row is locked for the renewal, read again after the lock, and a request that finds it locked uses the token while it still lasts; a refusal of a refresh token already spent can no longer delete the token the first request just stored.
- Fix, found in the live trial: Test the Connection against a server that answers "method not found" to `server/discover` in an HTTP 200 JSON-RPC error (Nudge's own MCP server) now falls back to the standard `initialize` handshake, as it did for a 400, 404 or 405.
- Fix, found in the live trial: Read the Tools failed for the administrator ("not allowed to create Nudge Connector Tool") and a connector could not be deleted; the tools are written by Nudge in the sync (after checking the person may write the connector), and the Settings group may delete a connector with its tools.
- Review: the module has an icon, a banner and a store page (`static/description`) and the `images` key, as every other module.

## 19.0.1.59.0

- OAuth sign-in for connectors (authorization code with PKCE S256, discovery by RFC 9728 and 8414, registration by RFC 7591): one token per person and connector, readable by the Settings group only, renewed on expiry, redacted everywhere; a person without a token is told to sign in and never gets another person's; single-use ten-minute state bound to the person and connector; Teams preset removed (no official endpoint).
- S3, S4: a catalogue of connector presets (another Odoo, Google Calendar, Gmail, Slack, GitHub, Notion, generic) that fills the form and brings only its Read tools in allowed, never a Write; connector calls are a kind of the usage ledger with per-person and per-company limits of calls and the same refusal as a provider call; a Read answer can be kept per connector, tool, arguments, person and company for a time the administrator sets (default off), logged as a hit.
## 19.0.1.58.0

- 19.0.1.0.0 New companion: governed connectors (NP-07). An administrator adds an outside system that speaks MCP (Streamable HTTP, revision 2026-07-28, with the handshake of 2025-11-25 and before as a fallback), reads its tools and allows each one as Read or Write; reads run for the person and name their source, writes wait on a card with the exact payload; every call is logged with secrets masked; kill switches for the database, the company and each connector, read-only first month per company, Private Mode refuses what does not run on your own network; the tools are offered only in a database where the module is installed (EXT-01).
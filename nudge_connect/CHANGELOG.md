# Changelog

## 19.0.1.58.0 (unreleased)

- S3, S4: a catalogue of connector presets (another Odoo, Google Calendar, Gmail, Slack, Teams, GitHub, Notion, generic) that fills the form and brings only its Read tools in allowed, never a Write; connector calls are a kind of the usage ledger with per-person and per-company limits of calls and the same refusal as a provider call; a Read answer can be kept per connector, tool, arguments, person and company for a time the administrator sets (default off), logged as a hit.
- 19.0.1.0.0 New companion: governed connectors (NP-07). An administrator adds an outside system that speaks MCP (Streamable HTTP, revision 2026-07-28, with the handshake of 2025-11-25 and before as a fallback), reads its tools and allows each one as Read or Write; reads run for the person and name their source, writes wait on a card with the exact payload; every call is logged with secrets masked; kill switches for the database, the company and each connector, read-only first month per company, Private Mode refuses what does not run on your own network; the tools are offered only in a database where the module is installed (EXT-01).

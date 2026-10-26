# Changelog of Nudge: MCP Server

## 19.0.1.1.2 (unreleased)

- STU-11: the keys made before the sign-in with OAuth (legacy) end on 2027-01-26 (`LEGACY_KEYS_END`, compared with the UTC date of the server). Until then they work as before; from that day a call with one is answered 401 with the reason and where to connect with OAuth, and is not counted as a wrong key. Until then, Settings, Nudge shows the administrator a warning while at least one live key of the scope `nudge_mcp` exists. A key still never confirms a change by itself.

## 19.0.1.1.0 (unreleased)

- Review: the first tool call of a person counted nowhere against the stream cap: its log conversation was created by the request, not committed yet, so the mark written on another cursor found no row and the place was handed over at once. `_mark_call` now says whether it wrote the mark, and without one the place stays claimed, and counts, until the call ends. Its docstring says the call must not write the log's conversation after the mark (repeatable read refuses a row changed meanwhile).
- NP-04: a tool call takes a place in the same line as the answers of the panel (`nudge.slot`), under the limit of Nudge and of its provider, and gives it back at the end or on error; it never goes before a question that waits. When the line is full the assistant gets a JSON-RPC error (`-32000`, `data.reason` `busy`, `data.retryAfter` in seconds) and a `Retry-After` header. A change waiting for the yes of the person holds no place.
- NP-14: assistants sign in with OAuth 2.1 (MCP authorization, revision 2026-07-28): protected resource
  and authorization server metadata, dynamic registration, a page of consent in Odoo, PKCE with S256
  only, access tokens of 60 minutes and refresh tokens that change at every use (a reuse ends the
  sign-in), only hashes stored, revocation. Scopes per group of tools: `nudge.read`, `nudge.write`
  (prepare changes, each with its card), `nudge.developer` (read automations, views and code checks,
  administrators only); a tool outside the scopes answers 403 `insufficient_scope`. Each person sees
  their connected assistants in their preferences, with the last use, and revokes them; administrators
  see all. API keys keep working and are marked legacy.
- SEC-03: the company switch "External assistants may confirm their own changes (unsafe)" is replaced
  by an allow-list per company, tool, model (and method) and assistant, empty by default, refused for
  sensitive models and methods, never for a key, never for another assistant than the one that
  prepared the change; such steps are marked Confirmed by the Assistant. After the update every change
  waits for the person until an administrator lists one.
- EXT-14: MCP prompts (`prompts/list`, `prompts/get`) answered and announced when a module adds prompts
  through `nudge.mcp._mcp_prompts()`; none are shipped here.
- Review: (S8) An auditor past "Auditor Access Until" could still use an MCP key or an OAuth token until the hourly cron archived them (the key check and the grant check did not look at the date): both now refuse an expired auditor at once.

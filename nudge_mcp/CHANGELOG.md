# Changelog of Nudge: MCP Server

## 19.0.1.1.0 (unreleased)

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

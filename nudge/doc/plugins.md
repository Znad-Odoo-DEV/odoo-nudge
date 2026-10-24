# Nudge plugins

A plugin is an ordinary Odoo module that adds tools, skills, prompts and slash commands to Nudge. It works on
Odoo 17, 18 and 19, Community and Enterprise. `addons/nudge_plugin_example` is a complete one to copy: one read
tool, one skill, one prompt and the command `/contacts`, with its tests.

## The tools of a database (EXT-01)

The tools of Nudge live in one list for the whole Odoo process, and a process may serve several databases. Each
database offers only:

- the tools of Nudge and of its companions (`nudge_builder`, `nudge_mail`, `nudge_connect`...) whose module is
  installed in that database, and only when every module the tool `requires` is installed too
  (`requires = ("sale",)` on the tool class; a few tools of Nudge have it inferred, `tools/registry.py`);
- the tools of the plugins a system administrator allowed in that database.

A tool of any other module is never offered. What the plugins bring is read once per registry and kept; each
decision clears it, at once in the worker that took it and at their next request in the others.

## The convention

- The module is named `nudge_plugin_<name>` and depends on `nudge`.
- Its tools are classes of `odoo.addons.nudge.tools.base.Tool` (or `Action` for a tool that writes), registered
  with `@base.register` like the tools of Nudge.
- Its skills and prompts are data files (`nudge.skill`, `nudge.prompt`) with `origin` set to `plugin` and
  `active` set to False: Nudge publishes them when the plugin is allowed.
- Its manifest describes it under `nudge_plugin`:

```python
"nudge_plugin": {
    "api": 1,                                   # the plugin API this Nudge reads
    "publisher": "Raghad",
    "version": "1.0.0",
    "summary": "Counts the contacts the person may read, by country.",
    "nudge": ">=19.0.1.58",
    "odoo": ["17.0", "18.0", "19.0"],           # refused on any other version
    "tools": ["tools.contacts.CountContactsByCountry"],     # dotted paths, inside the module only
    "skills": ["data/nudge_plugin_example_skill.xml"],
    "prompts": ["data/nudge_plugin_example_prompt.xml"],
    "commands": [{"word": "/contacts", "prompt": "prompt_contacts_by_country"}],
    "permissions": {"models_read": ["res.partner"], "models_write": [], "scopes": []},
    "signature": {"key": "<Ed25519 public key, base64>", "value": "<signature, base64>"},  # optional
},
```

Odoo keeps keys it does not know: `get_manifest()` gives a dict on 17 and 18 and a read-only mapping on 19, and
Nudge reads both alike.

## Review and decisions

Installing a plugin records it in **Nudge ‣ Configuration ‣ Plugins** (system administrators) as **Pending**.
Nothing of it reaches anyone: its tools are not offered and a call to one is refused as if it did not exist, its
skills and prompts stay archived, its commands are not listed. The form shows what it brings: each tool with its
description, whether it writes, the models it touches, the modules it needs, its input; the full text of each
skill and prompt; each command; what the manifest asks for; the signature.

- **Allow** publishes its skills, shows its prompts and commands, and offers its tools.
- **On** (the switch of the plugin) and **Settings ‣ Nudge ‣ Plugins ‣ Every Plugin Off** take everything back
  at once, without losing the review.
- **Do Not Allow** takes it back and archives its skills and prompts (never deleted).
- A plugin whose manifest, tool descriptions, skills or prompts change after it was allowed is **Pending** again
  (a fingerprint of all of it is compared).
- **Look for Plugins** records a plugin installed before this version of Nudge.
- Removing the module removes its record; Odoo removes its skills and prompts with it.

Every decision is written in the History tab and in the server log.

## What a plugin can and cannot do

- A plugin tool runs as the person who asks, with their rights and record rules; a tool that writes is an
  `Action`, with the same confirmation card as every change, and nothing is done before their yes. A tool that
  writes while the manifest names no model it writes is pointed out in the review.
- A plugin never takes a name already taken: a tool of Nudge, a tool of another plugin, a command of Nudge. The
  refused name is shown in the review and never offered. Nudge takes its own name back from a plugin that was
  loaded first in the process.
- In a call, a plugin tool comes with the skills of the plugin (NP-03), not in every call; "Every Skill in Full"
  sends it with the rest.
- What a plugin tool returns is framed as data, like every tool result: an instruction written in a record is
  reported, never followed.
- A command answers with its prompt, filled with what follows the word; one click sends it to Nudge.
- Python code cannot be fenced inside Odoo: trust in a plugin comes from who installs it, from this review, and
  from the publisher's signature.

## Signature

When the manifest carries `signature`, Nudge checks the Ed25519 signature of
`{"module": "<module>", "nudge_plugin": <the declaration without its signature>}` (JSON, sorted keys, no spaces,
UTF-8). A valid signature by a key this database trusts shows **Verified publisher**; a valid signature by another
key shows "Signed, key not trusted"; a signature that does not match cannot be allowed. The trusted keys are one
per line in the system parameter `nudge.plugin_trusted_keys` (and the trust list of the recipe sources, when it
exists). `tools.registry.sign(module, declaration, private_key)` makes the signature.

## Tests

`/nudge:TestToolRegistry,/nudge:TestPluginManifest` (the registry, names, switches, manifests, signatures) and
`/nudge_plugin_example` (installed, allowed, switched off, changed, rights, cards, removed).

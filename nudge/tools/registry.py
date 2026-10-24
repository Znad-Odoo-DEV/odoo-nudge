"""The tools this database offers (EXT-01, roadmap L1) and the plugins that add to them (L3).

``base.TOOLS`` is one dict for the whole process: Odoo imports an addon once per process, the first
time any database loads it, so a server with several databases holds the tools of the modules of all
of them. What one database offers is decided here:

- a tool of Nudge or of one of its companions (``FIRST_PARTY``) is offered when its module is
  installed in this database (``registry._init_modules``, the modules this registry loaded) and every
  module it ``requires`` is too;
- a tool of a plugin (a module ``nudge_plugin_<name>``) is offered only when, besides, a system
  administrator allowed that plugin, its own switch and the global switch are on, and its manifest
  declares that tool; the state of the plugins is read once per registry and kept (ormcache), and every
  decision clears it, in this worker at once and in the others at their next request;
- a tool of any other module is never offered: an outside module brings tools as a plugin.

A plugin never takes a name already taken and never replaces a tool of Nudge (:func:`claim`).
"""
import base64
import binascii
import hashlib
import importlib
import json
import logging
import re
from collections.abc import Mapping

from odoo import release

from . import base

_logger = logging.getLogger(__name__)

# The modules whose tools are Nudge's own: offered wherever they are installed
FIRST_PARTY = frozenset({
    "nudge", "nudge_ai", "nudge_builder", "nudge_connect", "nudge_discuss", "nudge_documents", "nudge_mail",
    "nudge_mcp", "nudge_messaging", "nudge_spreadsheet", "nudge_studio", "nudge_website",
})
PLUGIN_PREFIX = "nudge_plugin_"
# The key of the manifest a plugin describes itself under, and the version of that description Nudge reads
PLUGIN_KEY = "nudge_plugin"
PLUGIN_API = 1
# Every plugin off at once, whatever was allowed
OFF_PARAM = "nudge.plugins_off"
# The public keys of the publishers this database trusts, one per line (Ed25519, PEM or 32 bytes in base64)
TRUSTED_KEYS_PARAM = "nudge.plugin_trusted_keys"

# Tools of Nudge that are of no use without a module, found from the model each one checks with
# ``in env`` before doing anything; a tool says it itself with ``requires`` (a plugin's tool too)
INFERRED_REQUIRES = {
    "test_import": ("base_import",),
    "match_import_columns": ("base_import",),
    "search_products": ("product",),
    "find_products": ("product",),
}

# What a registration refused, ``{module: {tool name: why}}``: shown when the plugin is reviewed
REFUSED = {}

COMMAND_WORD = re.compile(r"^/[a-z0-9_\-ء-ي]{2,30}$")
MAX_LISTED = 50


# ----------------------------------------------------------------- where a tool comes from

def module_of(tool):
    """:param tool: a tool, or its class
    :return: the addon it is written in (``odoo.addons.<addon>.tools.x``), or None outside an addon"""
    cls = tool if isinstance(tool, type) else type(tool)
    parts = (getattr(cls, "__module__", "") or "").split(".")
    return parts[2] if len(parts) > 2 and parts[0] == "odoo" and parts[1] == "addons" else None


def is_plugin_module(name):
    return bool(name) and name.startswith(PLUGIN_PREFIX) and len(name) > len(PLUGIN_PREFIX)


def is_trusted_module(name):
    """:return: whether the tools of the module ``name`` are Nudge's own; a tool made outside any addon
    (a test, a script) counts as Nudge's"""
    return name is None or name in FIRST_PARTY


def requires_of(tool):
    """:return: the modules ``tool`` needs besides its own"""
    return tuple(getattr(tool, "requires", ()) or ()) or INFERRED_REQUIRES.get(getattr(tool, "name", None), ())


def claim(tool_class):
    """Whether ``tool_class`` may take its name in ``base.TOOLS`` (called by ``base.register``).

    Nudge's own tools behave as before: the last one registered under a name has it. A tool of any other
    module takes only a free name: a plugin never replaces a tool of Nudge or of another plugin. A tool of
    Nudge takes its name back from a plugin that came first in this process.
    :return: True when the class is to be registered"""
    name = tool_class.name
    present = base.TOOLS.get(name)
    if present is None or type(present) is tool_class:
        return True
    new, old = module_of(tool_class), module_of(present)
    if is_trusted_module(new):
        if not is_trusted_module(old):
            REFUSED.setdefault(old, {})[name] = "Nudge has a tool of this name."
            _logger.warning("Nudge: the tool %s of %s gives way to the tool of Nudge of the same name", name, old)
        return True
    REFUSED.setdefault(new, {})[name] = "Another tool already has this name."
    _logger.warning("Nudge: the tool %s of %s is refused: %s already has that name", name, new, old)
    return False


# ----------------------------------------------------------------- what this database offers

def offered(env, tool):
    """:return: whether the database of ``env`` offers ``tool`` (EXT-01), whoever asks"""
    module = module_of(tool) or "nudge"
    loaded = env.registry._init_modules
    if module not in loaded or any(name not in loaded for name in requires_of(tool)):
        return False
    if module in FIRST_PARTY:
        return True
    if not is_plugin_module(module):
        return False
    return (tool.name, module) in plugin_tools(env)


def plugin_tools(env):
    """:return: ``{(tool name, plugin module)}``: the tools of the plugins in use in this database"""
    if "nudge.plugin" not in env:
        return frozenset()
    Plugin = env["nudge.plugin"]
    if not env.registry.ready:
        # Modules are loading: a plugin may be recorded at the next step, so nothing is kept yet
        return Plugin._nudge_live_tools_uncached()
    return Plugin._nudge_live_tools()


def tools_here(env):
    """:return: ``{name: tool}`` of every tool this database offers, before the person and the mode"""
    return {name: tool for name, tool in base.TOOLS.items() if offered(env, tool)}


def plugin_names(tools):
    """:return: of ``{name: tool}``, the names of the tools that come from plugins"""
    return {name for name, tool in tools.items() if is_plugin_module(module_of(tool))}


def invalidate(env):
    """The tools of the plugins are read again: in this worker at once, in the others at their next request
    (Odoo signals a cleared cache to every worker of the database)"""
    env.registry.clear_cache()


# ----------------------------------------------------------------- the manifest of a plugin

def _texts(raw, key, problems):
    value = raw.get(key) or []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        problems.append(f"The entry {key!r} is not a list.")
        return []
    return [str(item).strip() for item in value if isinstance(item, str) and item.strip()][:MAX_LISTED]


def declaration_of(manifest):
    """Read the plugin part of a manifest.

    :param manifest: what ``odoo.modules.module.get_manifest`` gives: a dict on Odoo 17 and 18, a read-only
        mapping (``Manifest``) on 19; both keep the keys Odoo does not know
    :return: ``(declaration, problems)``: the ``nudge_plugin`` entry with every part in one form (lists of
        texts, a dict of permissions), and what keeps it from being allowed, in a few words each;
        ``(None, [])`` when the manifest declares no plugin"""
    if not isinstance(manifest, Mapping):
        return None, []
    raw = manifest.get(PLUGIN_KEY)
    if raw is None:
        return None, []
    if not isinstance(raw, Mapping):
        return {}, ["The nudge_plugin entry of the manifest is not a dictionary."]
    problems = []
    api = raw.get("api", PLUGIN_API)
    if not isinstance(api, int) or isinstance(api, bool) or not 1 <= api <= PLUGIN_API:
        problems.append(f"It is made for the plugin API {api!r}; this Nudge reads version {PLUGIN_API}.")
    odoo = _texts(raw, "odoo", problems)
    if odoo and release.serie not in odoo:
        problems.append(f"It is made for Odoo {', '.join(odoo)}, not {release.serie}.")
    permissions = raw.get("permissions") or {}
    if not isinstance(permissions, Mapping):
        problems.append("The entry 'permissions' is not a dictionary.")
        permissions = {}
    commands = []
    for entry in raw.get("commands") or []:
        word = str(entry.get("word") or "").strip().lower() if isinstance(entry, Mapping) else ""
        prompt = str(entry.get("prompt") or "").strip() if isinstance(entry, Mapping) else ""
        if not COMMAND_WORD.match(word) or not prompt:
            problems.append(f"A command is not a /word with the prompt it sends: {entry!r}"[:200])
            continue
        commands.append({"word": word, "prompt": prompt})
    declaration = {
        "api": api,
        "publisher": str(raw.get("publisher") or manifest.get("author") or "").strip()[:120],
        "version": str(raw.get("version") or manifest.get("version") or "").strip()[:40],
        "summary": str(raw.get("summary") or manifest.get("summary") or "").strip()[:300],
        "nudge": str(raw.get("nudge") or "").strip()[:60],
        "odoo": odoo,
        "tools": _texts(raw, "tools", problems),
        "skills": _texts(raw, "skills", problems),
        "prompts": _texts(raw, "prompts", problems),
        "commands": commands[:MAX_LISTED],
        "permissions": {key: _texts(permissions, key, problems) for key in ("models_read", "models_write", "scopes")},
    }
    signature = raw.get("signature")
    if isinstance(signature, Mapping) and isinstance(signature.get("key"), str) \
            and isinstance(signature.get("value"), str):
        declaration["signature"] = {"key": signature["key"].strip(), "value": signature["value"].strip()}
    elif signature is not None:
        problems.append("The signature is not {'key': ..., 'value': ...}.")
    return declaration, problems


def resolve(module, path):
    """:param path: a dotted path the plugin ``module`` declares, ``tools.x.Class`` or ``odoo.addons.<module>.tools.x.Class``
    :return: ``(tool class, "")``, or ``(None, why not)``: a plugin declares only classes of its own module"""
    full = path if path.startswith("odoo.addons.") else f"odoo.addons.{module}.{path}"
    if not full.startswith(f"odoo.addons.{module}."):
        return None, "A plugin declares only tools of its own module."
    python_path, _dot, class_name = full.rpartition(".")
    try:
        python_module = importlib.import_module(python_path)
    except ImportError:
        _logger.warning("Nudge: the tool %s of the plugin %s names a Python module that is not there", path, module)
        return None, "Its Python module cannot be imported."
    except Exception:  # noqa: BLE001 - whatever the plugin's code raises, the tool is just not there
        _logger.warning("Nudge: the tool %s of the plugin %s cannot be imported", path, module, exc_info=True)
        return None, "Its Python module cannot be imported."
    cls = getattr(python_module, class_name, None)
    if not isinstance(cls, type) or not issubclass(cls, base.Tool) or not isinstance(cls.name, str) or not cls.name:
        return None, "It is not a tool class of Nudge."
    if module_of(cls) != module:
        return None, "A plugin declares only tools of its own module."
    return cls, ""


def adopt(module, tool_class):
    """Register a declared tool of a plugin, if it is not yet.
    :return: ``(status, note)``: "ok" when the name in ``base.TOOLS`` is this class, else "clash" and why"""
    if type(base.TOOLS.get(tool_class.name)) is not tool_class:
        base.register(tool_class)
    if type(base.TOOLS.get(tool_class.name)) is tool_class:
        return "ok", ""
    return "clash", REFUSED.get(module, {}).get(tool_class.name) or "Another tool already has this name."


# ----------------------------------------------------------------- signature and fingerprint

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def signed_text(module, declaration):
    """:return: the text a publisher signs: the module name and its declaration, without the signature"""
    body = {key: value for key, value in (declaration or {}).items() if key != "signature"}
    return canonical({"module": module, PLUGIN_KEY: body})


def sign(module, declaration, private_key):
    """:return: the ``signature`` entry of ``declaration`` made with the Ed25519 ``private_key`` object.
    For the publishers' own script and for the tests (a key that lives in memory only)."""
    from . import compliance_feed  # noqa: PLC0415 - the same crypto helpers as the signed feed
    _invalid, serialization, _key = compliance_feed._crypto()
    public = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    value = private_key.sign(signed_text(module, declaration).encode("utf-8"))
    return {"key": base64.b64encode(public).decode(), "value": base64.b64encode(value).decode()}


def check_signature(module, declaration, trusted_keys):
    """:param trusted_keys: public keys (texts) of the publishers this database trusts
    :return: "none" (not signed), "verified" (signed by a trusted key), "untrusted" (a valid signature of a
        key nobody here trusts) or "invalid" (the signature does not match the declaration)"""
    signature = (declaration or {}).get("signature")
    if not signature:
        return "none"
    from . import compliance_feed  # noqa: PLC0415
    try:
        invalid, serialization, _key = compliance_feed._crypto()
        key = compliance_feed.parse_public_key(signature["key"])
        value = base64.b64decode(signature["value"], validate=True)
    except (compliance_feed.FeedError, binascii.Error, ValueError, TypeError, KeyError):
        return "invalid"
    try:
        key.verify(value, signed_text(module, declaration).encode("utf-8"))
    except invalid:
        return "invalid"

    def raw(public_key):
        return public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    trusted = set()
    for text in trusted_keys or ():
        try:
            trusted.add(raw(compliance_feed.parse_public_key(text)))
        except compliance_feed.FeedError:
            continue
    return "verified" if raw(key) in trusted else "untrusted"


def fingerprint(module, declaration, items):
    """:param items: what the plugin brings, as the review shows it (tools with their descriptions, skills
        and prompts with their text)
    :return: a hash that changes whenever any of it changes: a plugin allowed for one content is not allowed
        for another"""
    return hashlib.sha256(canonical({"module": module, "declaration": declaration, "items": items}).encode()).hexdigest()

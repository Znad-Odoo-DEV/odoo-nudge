"""Plugins (roadmap L3): an Odoo module named ``nudge_plugin_<name>``, depending on ``nudge``, that brings tools,
skills, prompts and slash commands, and describes them in its manifest under ``nudge_plugin``.

Installing one records it here as Pending. A system administrator reads what it brings (each tool with its
description and whether it writes, the models it touches, the text of its skills and prompts, its commands) and
allows it; until then nothing of it reaches a prompt: its tools are not offered (tools/registry.py), its skills
and prompts stay archived, its commands are not listed. A switch per plugin and a global one take it all back at
once. A plugin whose manifest, tools, skills or prompts change after it was allowed is Pending again. Removing
the module removes its record here; Odoo removes its skills and prompts with the module.

What a plugin's tool does runs as the person, with their rights, and a tool that writes is an ``Action``: the
same confirmation card as every change. Python code cannot be fenced inside Odoo: the trust in a plugin comes
from who installs it, from this review and, when the manifest is signed, from the publisher's key.
"""
import json
import logging

from odoo import _, api, fields, models, tools
from odoo.exceptions import AccessError, UserError
from odoo.modules.module import get_manifest

from ..tools import base, commands, registry

_logger = logging.getLogger(__name__)

STATES = [("pending", "Pending"), ("allowed", "Allowed"), ("blocked", "Not Allowed")]
SIGNATURES = [
    ("none", "Not signed"), ("verified", "Verified publisher"), ("untrusted", "Signed, key not trusted"),
    ("invalid", "Signature not valid"),
]
ITEM_KINDS = [("tool", "Tool"), ("skill", "Skill"), ("prompt", "Prompt"), ("command", "Command")]
ITEM_STATES = [("ok", "Ready"), ("clash", "Name already taken"), ("refused", "Refused")]
# What a system administrator changes by hand; the rest is written by Nudge (as superuser) from the module
EDITABLE = frozenset({"enabled"})
MAX_HISTORY = 8000


class NudgePlugin(models.Model):
    _name = "nudge.plugin"
    _description = "Nudge Plugin"
    _order = "name"

    if hasattr(models, "Constraint"):
        _unique_name = models.Constraint("unique(name)", "A module is recorded once as a plugin.")
    else:  # Odoo 17 and 18
        _sql_constraints = [("unique_name", "unique(name)", "A module is recorded once as a plugin.")]

    name = fields.Char(string="Module", required=True, readonly=True, index=True)
    title = fields.Char(readonly=True)
    publisher = fields.Char(readonly=True)
    version = fields.Char(readonly=True)
    summary = fields.Char(readonly=True)
    state = fields.Selection(
        STATES, string="Status", default="pending", required=True, readonly=True, index=True,
        help="Allow only decides what reaches the AI: the code of a module runs as soon as it is installed, and the signature is informative.")
    enabled = fields.Boolean(
        string="Switched On", default=True,
        help="Switch it off to take every tool, skill, prompt and command of this plugin away at once, "
             "without losing the review.")
    live = fields.Boolean(string="In Use", compute="_compute_live",
                          help="Its tools, skills, prompts and commands reach the people of this database now.")
    signature_state = fields.Selection(SIGNATURES, string="Signature", default="none", required=True, readonly=True)
    can_allow = fields.Boolean(string="Can Be Allowed", readonly=True, default=True)
    writes = fields.Boolean(string="Has Tools That Write", readonly=True)
    models_read = fields.Char(string="Models It Reads", readonly=True)
    models_write = fields.Char(string="Models It Writes", readonly=True)
    scopes = fields.Char(readonly=True)
    problems = fields.Text(readonly=True)
    declaration = fields.Text(readonly=True, help="The nudge_plugin entry of its manifest, as Nudge read it.")
    fingerprint = fields.Char(readonly=True, help="Changes when what the plugin brings changes.")
    allowed_fingerprint = fields.Char(readonly=True, copy=False)
    allowed_by = fields.Many2one("res.users", readonly=True, copy=False)
    allowed_on = fields.Datetime(readonly=True, copy=False)
    history = fields.Text(readonly=True, copy=False)
    item_ids = fields.One2many("nudge.plugin.item", "plugin_id", string="What It Brings", readonly=True)
    tool_count = fields.Integer(string="Tools", compute="_compute_counts")
    skill_count = fields.Integer(string="Skills", compute="_compute_counts")
    prompt_count = fields.Integer(string="Prompts", compute="_compute_counts")

    # ------------------------------------------------------------------ state
    @api.model
    def _globally_off(self):
        value = self.env["ir.config_parameter"].sudo().get_param(registry.OFF_PARAM)
        return str(value).lower() in ("true", "1", "yes")

    def _is_live(self):
        self.ensure_one()
        return (self.state == "allowed" and self.enabled and self.can_allow
                and self.fingerprint == self.allowed_fingerprint
                and self.name in self.env.registry._init_modules and not self._globally_off())

    def _compute_live(self):
        for plugin in self:
            plugin.live = plugin._is_live()

    @api.depends("item_ids")
    def _compute_counts(self):
        for plugin in self:
            kinds = plugin.item_ids.mapped("kind")
            plugin.tool_count = kinds.count("tool")
            plugin.skill_count = kinds.count("skill")
            plugin.prompt_count = kinds.count("prompt")

    @api.model
    @tools.ormcache()
    def _nudge_live_tools(self):
        """:return: ``{(tool name, module)}`` of the plugins in use, read once for the registry; every
        decision clears it (``registry.invalidate``)"""
        return self._nudge_live_tools_uncached()

    @api.model
    def _nudge_live_tools_uncached(self):
        # sudo: which plugins are in use is Nudge's own record, the same for everyone in the database
        if self._globally_off():
            return frozenset()
        found = set()
        for plugin in self.sudo().search([("state", "=", "allowed"), ("enabled", "=", True)]):
            if plugin._is_live():
                found |= {(item.name, plugin.name) for item in plugin.item_ids
                          if item.kind == "tool" and item.status == "ok"}
        return frozenset(found)

    # ------------------------------------------------------------------ access
    def _check_system(self):
        if not self.env.su and not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only a system administrator decides on plugins."))

    def write(self, vals):
        # Only Nudge writes what a plugin brings and the decisions, in superuser mode after its own checks (the
        # buttons below); a context key would not do: a caller sets the context it likes
        if not self.env.su and set(vals) - EDITABLE:
            raise UserError(_("What a plugin brings is read from its module: only its switch is changed here."))
        self._check_system()
        switched = "enabled" in vals and not self.env.su
        result = super().write(vals)
        if switched:
            for plugin in self:
                plugin._log(_("Switched on") if plugin.enabled else _("Switched off"))
            self._apply()
        return result

    def _log(self, text):
        for plugin in self:
            line = f"{fields.Datetime.now()} {self.env.user.name}: {text}"
            plugin.sudo().history = (
                f"{line}\n{plugin.history or ''}")[:MAX_HISTORY]
            _logger.info("Nudge: plugin %s: %s (user %s)", plugin.name, text, self.env.uid)

    # ------------------------------------------------------------------ buttons
    def action_allow(self):
        """A system administrator read what the plugin brings and lets it reach the people of this database"""
        self._check_system()
        for plugin in self:
            if not plugin.can_allow:
                raise UserError(_("%(plugin)s cannot be allowed: %(why)s", plugin=plugin.name,
                                           why=plugin.problems or ""))
            plugin.sudo().write({
                "state": "allowed", "enabled": True, "allowed_fingerprint": plugin.fingerprint,
                "allowed_by": self.env.uid, "allowed_on": fields.Datetime.now()})
            plugin._log(_("Allowed (%s)", (plugin.fingerprint or "")[:12]))
        self._apply()
        return True

    def action_block(self):
        """Its tools leave every prompt at once; its skills and prompts are archived, never deleted"""
        self._check_system()
        self.sudo().write({"state": "blocked", "allowed_fingerprint": False})
        self._log(_("Not allowed"))
        self._apply()
        return True

    def action_review_again(self):
        self._check_system()
        self.sudo().write({"state": "pending", "allowed_fingerprint": False})
        self._log(_("Back to review"))
        self._apply()
        return True

    def action_find_plugins(self):
        """Look again at the installed modules: a plugin installed before this version of Nudge is recorded.
        Not api.model: the button of the list sends the selected ids, or none"""
        self._check_system()
        self.sudo()._sync_installed()
        return True

    # ------------------------------------------------------------------ reading a module
    @api.model
    def _trusted_keys(self):
        """:return: the public keys of the publishers this database trusts: the same Trusted Publishers as the
        signed recipes (an archived publisher is not trusted), plus any key given one per line in the system
        parameter ``nudge.plugin_trusted_keys``."""
        keys = [line.strip() for line in (self.env["ir.config_parameter"].sudo().get_param(
            registry.TRUSTED_KEYS_PARAM) or "").splitlines() if line.strip()]
        # sudo: the trust list is Nudge's own configuration, read to check a signature, never shown
        keys += [key for key in self.env["nudge.recipe.publisher"].sudo().search([]).mapped("public_key") if key]
        return keys

    @api.model
    def _own_records(self, module, model):
        """:return: the records of ``model`` the module made from its data files, archived ones too"""
        ids = self.env["ir.model.data"].sudo().search([("module", "=", module), ("model", "=", model)]).mapped("res_id")
        return self.env[model].sudo().with_context(active_test=False).browse(ids).exists()

    @api.model
    def _items_of(self, module, declaration):
        """:return: what the module brings, as dicts of ``nudge.plugin.item``, in a stable order"""
        items = []
        declared = set()
        for path in declaration.get("tools") or []:
            tool_class, why = registry.resolve(module, path)
            if tool_class is None:
                items.append({"kind": "tool", "name": path[:120], "status": "refused", "note": why})
                continue
            status, note = registry.adopt(module, tool_class)
            declared.add(tool_class.name)
            items.append({
                "kind": "tool", "name": tool_class.name, "status": status, "note": note,
                "description": str(tool_class.description or ""),
                "writes": issubclass(tool_class, base.Action) or tool_class.kind not in ("read", "show"),
                "models": ", ".join(getattr(tool_class, "models", ()) or ()),
                "requires": ", ".join(registry.requires_of(tool_class)),
                "group": tool_class.group or "", "modes": ",".join(tool_class.modes or ()),
                "detail": base.dump(tool_class.input_schema),
            })
        for name, tool in sorted(base.TOOLS.items()):
            if registry.module_of(tool) == module and name not in declared:
                items.append({"kind": "tool", "name": name, "status": "refused",
                              "note": "Not declared in the manifest: never offered."})
        for name, why in sorted(registry.REFUSED.get(module, {}).items()):
            if name not in declared:
                items.append({"kind": "tool", "name": name, "status": "clash", "note": why})
        own_tools = {item["name"] for item in items if item["kind"] == "tool" and item["status"] == "ok"}
        Skill = self.env["nudge.skill"].sudo().with_context(active_test=False)
        for skill in self._own_records(module, "nudge.skill").sorted("key"):
            taken = Skill.search_count([("id", "!=", skill.id), ("key", "=", skill.key), ("active", "=", True),
                                        ("company_id", "=", skill.company_id.id), ("origin", "!=", "plugin")])
            names = [name for name in (skill.tool_names or "").replace(",", " ").split() if name]
            foreign = [name for name in names if registry.is_plugin_module(registry.module_of(base.TOOLS.get(name)))
                       and name not in own_tools]
            items.append({
                "kind": "skill", "name": skill.key, "res_id": skill.id,
                "status": "clash" if taken else ("refused" if foreign else "ok"),
                "note": ("Another skill already has this key." if taken else
                         f"It names tools of another plugin: {', '.join(foreign)}" if foreign else ""),
                "description": skill.summary or "", "models": skill.model_names or "", "modes": skill.modes or "",
                "requires": ", ".join(names), "detail": skill.instructions or "",
            })
        prompts = self._own_records(module, "nudge.prompt").sorted(lambda prompt: (prompt.sequence, prompt.id))
        for prompt in prompts:
            items.append({
                "kind": "prompt", "name": prompt.name, "res_id": prompt.id, "status": "ok",
                "description": prompt.description or "", "models": prompt.model_names or "",
                "modes": prompt.mode or "", "detail": prompt.body or "",
            })
        taken_words = set(commands.BY_WORD)
        for entry in declaration.get("commands") or []:
            xmlid = entry["prompt"] if "." in entry["prompt"] else f"{module}.{entry['prompt']}"
            prompt = self.env.ref(xmlid, raise_if_not_found=False)
            ok_prompt = bool(prompt) and prompt._name == "nudge.prompt" and prompt in prompts
            status = "clash" if entry["word"] in taken_words else ("ok" if ok_prompt else "refused")
            items.append({
                "kind": "command", "name": entry["word"], "res_id": prompt.id if ok_prompt else False,
                "status": status,
                "note": ("Nudge already has this command." if status == "clash" else
                         "" if ok_prompt else "It names no prompt of this plugin."),
                "description": prompt.name if ok_prompt else "", "detail": prompt.body if ok_prompt else "",
            })
            taken_words.add(entry["word"])
        return items

    @api.model
    def _sync_module(self, module):
        """Record what the installed module ``module`` brings: a new plugin is Pending; one that changed since
        it was allowed is Pending again. :return: the ``nudge.plugin``"""
        manifest = get_manifest(module)
        declaration, problems = registry.declaration_of(manifest)
        if declaration is None:
            declaration, problems = {}, ["The manifest has no nudge_plugin entry: nothing of it is offered."]
        items = self._items_of(module, declaration)
        signature = registry.check_signature(module, declaration, self._trusted_keys())
        if signature == "invalid":
            problems.append("The signature of the manifest does not match what it declares.")
        can_allow = not problems
        tools_here = [item for item in items if item["kind"] == "tool" and item["status"] == "ok"]
        for item in tools_here:
            if item["writes"] and not declaration.get("permissions", {}).get("models_write"):
                problems.append(f"{item['name']} writes, and the manifest names no model it writes.")
        reviewed = [{key: item.get(key) for key in ("kind", "name", "status", "description", "writes", "group",
                                                    "modes", "requires", "detail")} for item in items]
        permissions = declaration.get("permissions") or {}
        values = {
            "title": (manifest.get("name") if manifest else "") or module,
            "publisher": declaration.get("publisher") or "",
            "version": declaration.get("version") or "",
            "summary": declaration.get("summary") or "",
            "signature_state": signature,
            "can_allow": can_allow,
            "writes": any(item.get("writes") for item in tools_here),
            "models_read": ", ".join(permissions.get("models_read") or []),
            "models_write": ", ".join(permissions.get("models_write") or []),
            "scopes": ", ".join(permissions.get("scopes") or []),
            "problems": "\n".join(problems),
            "declaration": json.dumps(declaration, ensure_ascii=False, indent=1, sort_keys=True),
            "fingerprint": registry.fingerprint(module, declaration, reviewed),
        }
        Plugin = self.sudo()
        plugin = Plugin.search([("name", "=", module)], limit=1)
        if not plugin:
            plugin = Plugin.create(dict(values, name=module, state="pending"))
            plugin._log(_("Installed: waiting for a system administrator"))
        else:
            again = plugin.state == "allowed" and plugin.allowed_fingerprint != values["fingerprint"]
            if again:
                values.update(state="pending", allowed_fingerprint=False)
            plugin.write(values)
            if again:
                plugin._log(_("Changed since it was allowed: waiting for a review again"))
        plugin.item_ids.sudo().unlink()
        self.env["nudge.plugin.item"].sudo().create([dict(item, plugin_id=plugin.id) for item in items])
        self._apply()
        return plugin

    @api.model
    def _sync_installed(self):
        """Record every installed plugin, and forget those that are no longer installed"""
        installed = self.env["ir.module.module"].sudo().search([
            ("state", "=", "installed"), ("name", "=like", registry.PLUGIN_PREFIX.replace("_", "\\_") + "%")])
        for module in installed:
            self._sync_module(module.name)
        self._forget(self.sudo().search([("name", "not in", installed.mapped("name"))]).mapped("name"))

    @api.model
    def _forget(self, names):
        """The modules ``names`` were removed: so is their record; their skills and prompts went with them"""
        plugins = self.sudo().search([("name", "in", list(names))])
        if plugins:
            _logger.info("Nudge: plugins removed: %s", ", ".join(plugins.mapped("name")))
            plugins.unlink()
        registry.invalidate(self.env)

    # ------------------------------------------------------------------ what reaches a prompt
    @api.model
    def _apply(self):
        """Give the people what the plugins in use bring, and take back the rest: skills published or archived,
        prompts shown or archived, tools read again by every worker."""
        # sudo: skills and prompts of a plugin are Nudge's own records; who may decide was checked before
        for plugin in self.sudo().search([]):
            live = plugin._is_live()
            ready = {(item.kind, item.res_id) for item in plugin.item_ids if item.status == "ok" and item.res_id}
            skills = self._own_records(plugin.name, "nudge.skill")
            wanted = skills.filtered(lambda skill: live and ("skill", skill.id) in ready)
            (skills - wanted).filtered("active").write({"active": False})
            wanted.filtered(lambda skill: not skill.active or skill.state != "published").write({
                "active": True, "state": "published", "approved_by": plugin.allowed_by.id or self.env.uid,
                "approved_on": plugin.allowed_on or fields.Datetime.now()})
            prompts = self._own_records(plugin.name, "nudge.prompt")
            shown = prompts.filtered(lambda prompt: live and ("prompt", prompt.id) in ready)
            (prompts - shown).filtered("active").write({"active": False})
            shown.filtered(lambda prompt: not prompt.active).write({"active": True})
        registry.invalidate(self.env)

    @api.model
    def _commands_for_user(self):
        """:return: ``[{"word", "prompt"}]``: the commands of the plugins in use whose prompt the current person
        may read (their rights and the rules of the prompts apply)"""
        if self._globally_off():
            return []
        found = []
        # sudo: the plugins in use are Nudge's own record; the prompt is then read as the person
        for plugin in self.sudo().search([("state", "=", "allowed"), ("enabled", "=", True)]):
            if not plugin._is_live():
                continue
            for item in plugin.item_ids:
                if item.kind == "command" and item.status == "ok" and item.res_id:
                    found.append((item.name, item.res_id))
        prompts = self.env["nudge.prompt"].search([("id", "in", [res_id for _word, res_id in found])])
        return [{"word": word, "prompt": prompts.browse(res_id)} for word, res_id in found if res_id in prompts.ids]


class NudgePluginItem(models.Model):
    _name = "nudge.plugin.item"
    _description = "Nudge Plugin Item"
    _order = "plugin_id, kind, name, id"

    plugin_id = fields.Many2one("nudge.plugin", string="Plugin", required=True, ondelete="cascade", index=True,
                                readonly=True)
    kind = fields.Selection(ITEM_KINDS, required=True, readonly=True)
    name = fields.Char(required=True, readonly=True)
    status = fields.Selection(ITEM_STATES, string="Status", required=True, default="ok", readonly=True)
    note = fields.Char(readonly=True)
    description = fields.Text(readonly=True)
    writes = fields.Boolean(readonly=True)
    models = fields.Char(string="Models", readonly=True)
    requires = fields.Char(string="Needs", readonly=True, help="Modules a tool needs; tools a skill names.")
    group = fields.Char(readonly=True)
    modes = fields.Char(readonly=True)
    detail = fields.Text(readonly=True, help="The input of a tool; the text of a skill or a prompt.")
    res_id = fields.Integer(string="Record ID", readonly=True)


class NudgeSkillPlugin(models.Model):
    _inherit = "nudge.skill"

    origin = fields.Selection(selection_add=[("plugin", "Comes with a plugin")], ondelete={"plugin": "set default"})


class NudgePromptPlugin(models.Model):
    _inherit = "nudge.prompt"

    origin = fields.Selection(selection_add=[("plugin", "Comes with a plugin")], ondelete={"plugin": "set default"})


class IrModuleModulePlugin(models.Model):
    _inherit = "ir.module.module"

    def write(self, vals):
        result = super().write(vals)
        state = vals.get("state")
        if state in ("installed", "uninstalled") and "nudge.plugin" in self.env:
            names = [module.name for module in self if registry.is_plugin_module(module.name)]
            if names:
                # sudo: what an installed module brings is recorded by Nudge, whoever installed it
                Plugin = self.env["nudge.plugin"].sudo()
                try:
                    if state == "installed":
                        for name in names:
                            Plugin._sync_module(name)
                    else:
                        Plugin._forget(names)
                except Exception:  # noqa: BLE001 - an unrecorded plugin offers nothing: the safe side
                    _logger.exception("Nudge: the plugins %s could not be recorded", names)
        return result


class NudgeQuickPlugin(models.AbstractModel):
    _inherit = "nudge.quick"

    @api.model
    def list_commands(self):
        return super().list_commands() + self._plugin_command_entries()

    @api.model
    def _plugin_command_entries(self):
        return [{
            "name": f"plugin{entry['word'].replace('/', '_')}", "words": [entry["word"]], "kind": commands.DIRECT,
            "hint": entry["prompt"].description or entry["prompt"].name, "example": entry["word"], "at_once": False,
        } for entry in self.env["nudge.plugin"]._commands_for_user()]

    @api.model
    def run_command(self, text, model_name=None, snapshot=None):
        """A command of a plugin in use answers with its prompt, filled with what follows the word: one click
        sends it to Nudge, as a question of the person; nothing is sent from here"""
        self._check_user()
        words = str(text or "").strip().split(None, 1)
        word = words[0].lower() if words else ""
        entry = next((entry for entry in self.env["nudge.plugin"]._commands_for_user() if entry["word"] == word),
                     None) if word.startswith("/") and word not in commands.BY_WORD else None
        if entry is None:
            return super().run_command(text, model_name=model_name, snapshot=snapshot)
        prompt = entry["prompt"]
        if not prompt._mode_allowed(prompt.mode):
            return {"command": f"plugin{word.replace('/', '_')}", "query": "", "focused": False,
                    "content": _("The command %s is not open to you.", word)}
        rest = words[1].strip() if len(words) > 1 else ""
        body = prompt._fill(prompt, prompt._default_values(), strict=False)["text"]
        message = f"{body}\n\n{rest}" if rest else body
        return {
            "command": f"plugin{word.replace('/', '_')}", "query": rest, "focused": False,
            "content": _("%s: send it to Nudge from here.", prompt.name),
            "commands": [{"name": f"plugin{word.replace('/', '_')}_send", "words": [message], "kind": commands.FOCUSED,
                          "hint": prompt.description or prompt.name, "example": "", "at_once": True}],
        }


class IrHttpPlugin(models.AbstractModel):
    _inherit = "ir.http"

    def _get_nudge_session_info(self):
        info = super()._get_nudge_session_info()
        if info is not None:
            info["commands"] = list(info.get("commands") or []) + self.env["nudge.quick"].with_context(
                lang=self.env.user.lang)._plugin_command_entries()
        return info


class ResConfigSettingsPlugin(models.TransientModel):
    _inherit = "res.config.settings"

    nudge_plugins_off = fields.Boolean(
        string="Every Plugin Off", config_parameter=registry.OFF_PARAM,
        help="No tool, skill, prompt or command of any plugin reaches anyone, whatever was allowed. "
             "What was allowed comes back when this is switched on again.")
    nudge_plugins_pending = fields.Integer(string="Plugins Waiting", compute="_compute_nudge_plugins_pending")

    def _compute_nudge_plugins_pending(self):
        count = self.env["nudge.plugin"].sudo().search_count([("state", "=", "pending")]) \
            if self.env.user.has_group("base.group_system") else 0
        for settings in self:
            settings.nudge_plugins_pending = count

    def set_values(self):
        super().set_values()
        # sudo: the switch is set by a system administrator (settings); what it changes is Nudge's own
        self.env["nudge.plugin"].sudo()._apply()

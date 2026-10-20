"""Shared recipes: the setup of one database, as one file another can read.

A recipe holds recorded tours, rule tasks, task presets, the health check, the
words a company added to the no-AI commands, remembered answers and the
company files. Every record carries a ``recipe_key`` made at the first export,
so a second import updates what the first one made instead of doubling it.
Nothing here runs as anybody but the person: no ``sudo`` on data.
"""
import base64
import hashlib
import hmac
import json
import uuid

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

FORMAT = 1
KINDS = [
    ("tour", "Recorded Tour"), ("rule", "Rule Task"), ("preset", "Task Preset"), ("health", "Health Check"),
    ("lexicon", "Command Word"), ("memory", "Remembered Answer"), ("file", "Company File"),
    ("screen", "Key Screen"),
]
MODELS = {
    "tour": "nudge.tour", "rule": "nudge.task", "preset": "nudge.task", "lexicon": "nudge.lexicon.entry",
    "memory": "nudge.memory", "file": "nudge.source", "screen": "nudge.coach.screen",
}
HEALTH_KEY = "nudge-health"


def canonical(body):
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def checksum_of(body):
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


class NudgeRecipeMixin(models.AbstractModel):
    _name = "nudge.recipe.mixin"
    _description = "Nudge Recipe Key"

    # Not required: the column is added to records that exist
    recipe_key = fields.Char(
        copy=False, index=True, readonly=True,
        help="Names this record in the recipes, so that importing a recipe again updates it.")

    def action_export_recipe(self):
        """The Export button of the lists: the records selected, or all of them, as a recipe."""
        return self.env["nudge.recipe.export"]._open_for(self)


class NudgeRecipe(models.AbstractModel):
    _name = "nudge.recipe"
    _description = "Nudge Recipes"

    # ------------------------------------------------------------------ keys
    @api.model
    def _key_of(self, kind, record):
        """:return: the ``recipe_key`` of ``record``, made and kept as the person when it has none"""
        if record.recipe_key:
            return record.recipe_key
        key = f"nudge-{kind}-{uuid.uuid4().hex[:12]}"
        try:
            with self.env.cr.savepoint():
                record.write({"recipe_key": key})
        except AccessError:
            # Not theirs to write: a key the same content always gets
            seed = f"{record._name}|{record.display_name}"
            key = f"nudge-{kind}-h{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:12]}"
        return key

    @api.model
    def _find(self, kind, key):
        """:return: the record of ``kind`` this key names, archived ones too"""
        model = MODELS.get(kind)
        if not model:
            return None
        domain = [("recipe_key", "=", key)]
        if kind in ("rule", "preset"):
            domain.append(("company_id", "=", self.env.company.id))
        found = self.env[model].with_context(active_test=False).search(domain, limit=1)
        return found or None

    # ------------------------------------------------------------ references
    @api.model
    def _groups_out(self, groups):
        out = []
        for group in groups:
            xmlid = group.get_external_id().get(group.id)
            out.append({"xmlid": xmlid or "", "name": group.full_name})
        return sorted(out, key=lambda item: (item["xmlid"], item["name"]))

    @api.model
    def _groups_in(self, refs):
        """:return: ``(groups, missing names)``: groups by external id, else by exact name"""
        found, missing = self.env["res.groups"], []
        for ref in refs or []:
            group = self.env.ref(ref["xmlid"], raise_if_not_found=False) if ref.get("xmlid") else None
            if not group or group._name != "res.groups":
                group = self.env["res.groups"].search([("full_name", "=", ref.get("name"))], limit=2)
                group = group if len(group) == 1 else None
            if group:
                found |= group
            else:
                missing.append(ref.get("name") or ref.get("xmlid"))
        return found, missing

    # ---------------------------------------------------------------- export
    @api.model
    def _data(self, kind, record, files=False):
        return getattr(self, f"_data_{kind}")(record, files)

    @api.model
    def _data_tour(self, tour, files=False):
        return {
            "name": tour.name, "sequence": tour.sequence, "phrases_en": tour.phrases_en or "",
            "phrases_ar": tour.phrases_ar or "", "action_xmlid": tour.action_xmlid or "",
            "module_name": tour.module_name or "", "groups": self._groups_out(tour.group_ids),
            "steps": [{
                "sequence": step.sequence, "kind": step.kind, "name": step.name, "label": step.label or "",
                "note": step.note or "", "res_model": step.res_model or "", "view_type": step.view_type or "",
                "action_xmlid": step.action_xmlid or "",
            } for step in tour.step_ids.sorted(lambda step: (step.sequence, step.id))],
        }

    @api.model
    def _data_task(self, task, files=False):
        data = {
            "name": task.name, "engine": task.engine, "kind": task.kind, "instruction": task.instruction or "",
            "interval_number": task.interval_number, "interval_type": task.interval_type, "hour": task.hour,
            "weekday": task.weekday or "", "model_name": task.model_name or "", "conditions": task.conditions or [],
            "match": task.match, "send_email": bool(task.send_email),
        }
        if task.engine == "rule":
            data["rule"] = {
                "domain": task.rule_domain or "[]", "measure": task.rule_measure,
                "field": task.rule_field_id.name or "", "groupby": task.rule_groupby_id.name or "",
                "condition": task.rule_condition, "threshold": task.rule_threshold,
            }
        return data

    _data_rule = _data_task
    _data_preset = _data_task

    @api.model
    def _data_memory(self, memory, files=False):
        return {"question": memory.question, "answer": memory.answer, "groups": self._groups_out(memory.group_ids)}

    @api.model
    def _data_lexicon(self, entry, files=False):
        return {"synonym": entry.synonym, "intent": entry.intent, "dialect": entry.dialect}

    @api.model
    def _data_screen(self, screen, files=False):
        return {"name": screen.name, "module_name": screen.module_name or "", "action_xmlid": screen.action_xmlid,
                "sequence": screen.sequence}

    @api.model
    def _data_file(self, source, files=False):
        data = {
            "name": source.name, "purpose": source.purpose or "", "file_name": source.file_name or "",
            "mimetype": source.mimetype or "", "groups": self._groups_out(source.group_ids),
        }
        if files and source.file:
            attachment = self.env["ir.attachment"].search([
                ("res_model", "=", source._name), ("res_id", "=", source.id), ("res_field", "=", "file"),
                ("public", "=", True)], limit=1)
            if attachment:
                data["file_b64"] = source.file.decode() if isinstance(source.file, bytes) else source.file
        return data

    @api.model
    def _data_health(self, company, files=False):
        user = company.nudge_health_user_id
        return {"weekly_user": {"login": user.login, "name": user.name} if user else None}

    @api.model
    def _records(self, kind, only=None):
        """:return: what the person may export of ``kind``: this company's, active, theirs to read"""
        env = self.env
        if only and only[0] == MODELS.get(kind):
            records = env[only[0]].browse(only[1]).exists()
            if kind in ("rule", "preset"):
                records = records.filtered(lambda task: (task.engine == "rule") == (kind == "rule"))
            return records
        company = env.company
        if kind == "tour":
            return env["nudge.tour"].search([("is_library", "=", False), ("company_id", "in", (False, company.id))])
        if kind in ("rule", "preset"):
            domain = [("company_id", "=", company.id)]
            domain.append(("engine", "=", "rule") if kind == "rule" else ("engine", "!=", "rule"))
            return env["nudge.task"].search(domain)
        if kind == "lexicon":
            return env["nudge.lexicon.entry"].search([("company_id", "=", company.id)])
        if kind == "screen":
            return env["nudge.coach.screen"].with_context(active_test=False).search([])
        if kind == "memory":
            return env["nudge.memory"].search([("state", "=", "approved"), ("company_id", "in", (False, company.id))])
        if kind == "file":
            return env["nudge.source"].search([])
        return company

    @api.model
    def _export(self, kinds, files=False, only=None):
        """:return: the recipe: ``{"recipe": {... "items": [...]}, "checksum": "..."}``"""
        self._check_manager()
        items = []
        for kind in [kind for kind, _label in KINDS if kind in kinds]:
            if kind == "health":
                if only:
                    continue
                items.append({"kind": "health", "key": HEALTH_KEY, "name": self.env._("Weekly health check"),
                              "data": self._data_health(self.env.company)})
                continue
            for record in self._records(kind, only):
                items.append({"kind": kind, "key": self._key_of(kind, record),
                              "name": record.display_name or "", "data": self._data(kind, record, files)})
        items.sort(key=lambda item: (item["kind"], item["key"]))
        body = {
            "format": FORMAT, "nudge_version": self._version(), "odoo_series": self._series(),
            "created": fields.Datetime.to_string(fields.Datetime.now()), "company": self.env.company.name,
            "items": items,
        }
        return {"recipe": body, "checksum": checksum_of(body)}

    @api.model
    def _version(self):
        try:
            from odoo.modules.module import get_manifest  # noqa: PLC0415
            return get_manifest("nudge").get("version", "")
        except Exception:  # noqa: BLE001 - a header only
            return ""

    @api.model
    def _series(self):
        from odoo import release  # noqa: PLC0415
        return release.series

    @api.model
    def _check_manager(self):
        if not self.env.user.has_group("nudge.group_nudge_manager"):
            raise AccessError(self.env._("Only Nudge managers share recipes."))

    # ---------------------------------------------------------------- reading
    @api.model
    def _read(self, text):
        """:return: the recipe body when ``text`` is a recipe of a version and a checksum we accept
        :raise UserError: when it is not"""
        try:
            package = json.loads(text)
            body, checksum = package["recipe"], package["checksum"]
            items = body["items"]
        except (ValueError, KeyError, TypeError):
            raise UserError(self.env._("This file is not a Nudge recipe.")) from None
        if not isinstance(body, dict) or not isinstance(items, list) or not isinstance(checksum, str):
            raise UserError(self.env._("This file is not a Nudge recipe."))
        if not hmac.compare_digest(checksum_of(body), checksum):
            raise UserError(self.env._("The checksum of this recipe does not match: the file was changed or damaged."))
        if not isinstance(body.get("format"), int) or body["format"] > FORMAT:
            raise UserError(self.env._(
                "This recipe was made by a newer Nudge (format %s): update Nudge to read it.", body.get("format")))
        known = {kind for kind, _label in KINDS}
        for item in items:
            if (not isinstance(item, dict) or item.get("kind") not in known or not isinstance(item.get("key"), str)
                    or not isinstance(item.get("data"), dict)):
                raise UserError(self.env._("This recipe has an item that cannot be read."))
        return body

    @api.model
    def _plan(self, body):
        """:return: a list of ``{kind, key, name, action, note, payload}``, what an import would do with each item"""
        lines = []
        for item in body["items"]:
            kind, key, data = item["kind"], item["key"], item["data"]
            existing = self.env.company if kind == "health" else self._find(kind, key)
            action, notes = ("update" if existing else "create"), []
            refusal = getattr(self, f"_check_{kind}")(data, notes)
            if refusal:
                action, notes = "skip", [refusal] + notes
            elif existing is not None and self._data(kind, existing, "file_b64" in data) == data:
                action, notes = "same", [self.env._("Nothing to change.")]
            if kind == "memory" and action != "skip":
                notes.append(self.env._("Answer (it waits for your approval): %s", str(data.get("answer") or "")[:500]))
            elif kind == "preset" and data.get("instruction") and action != "skip":
                notes.append(self.env._("Instruction (the task starts switched off): %s", str(data["instruction"])[:500]))
            lines.append({"kind": kind, "key": key, "name": item.get("name") or key, "action": action,
                          "note": "\n".join(notes), "payload": json.dumps(data, ensure_ascii=False)})
        return lines

    # ----------------------------------------------------------------- checks
    # Each returns a sentence when the item cannot be imported here, else None, and adds its notes
    @api.model
    def _check_tour(self, data, notes):
        _groups, missing = self._groups_in(data.get("groups"))
        if missing:
            notes.append(self.env._("Groups not found here (%s): the tour comes in archived.", ", ".join(missing)))
        absent = [step for step in data.get("steps", []) if step.get("res_model") and step["res_model"] not in self.env]
        if absent:
            notes.append(self.env._("%s step(s) skipped: the model they are on is not in this database.", len(absent)))
        return None

    @api.model
    def _check_task(self, data, notes):
        model = data.get("model_name")
        if data.get("engine") == "rule":
            if not model or model not in self.env:
                return self.env._("This database has no model %s.", model)
            rule = data.get("rule") or {}
            for key in ("field", "groupby"):
                if rule.get(key) and rule[key] not in self.env[model]._fields:
                    if key == "field" and rule.get("measure") in ("sum", "avg"):
                        return self.env._("The field %s is not on %s.", rule[key], model)
                    notes.append(self.env._("The field %s is not on %s: left out.", rule[key], model))
        elif model and model not in self.env:
            if data.get("kind") == "watch":
                return self.env._("This database has no model %s.", model)
            notes.append(self.env._("This database has no model %s: the record type is left out.", model))
        return None

    _check_rule = _check_task
    _check_preset = _check_task

    @api.model
    def _check_memory(self, data, notes):
        _groups, missing = self._groups_in(data.get("groups"))
        if missing:
            notes.append(self.env._("Groups not found here (%s): the answer comes in archived.", ", ".join(missing)))
        return None

    @api.model
    def _check_lexicon(self, data, notes):
        from ..tools.noai_commands import lexicon  # noqa: PLC0415
        if data.get("intent") not in lexicon.CATEGORIES:
            return self.env._("This Nudge does not know the meaning “%s”.", data.get("intent"))
        return None

    @api.model
    def _check_screen(self, data, notes):
        if not data.get("action_xmlid") or "." not in data["action_xmlid"]:
            return self.env._("A key screen needs an action such as sale.action_orders.")
        if not self.env.ref(data["action_xmlid"], raise_if_not_found=False):
            notes.append(self.env._(
                "The action %s is not on this database: the screen comes in and is counted where it exists.",
                data["action_xmlid"]))
        return None

    @api.model
    def _check_file(self, data, notes):
        _groups, missing = self._groups_in(data.get("groups"))
        if missing:
            notes.append(self.env._("Groups not found here (%s): the file comes in archived.", ", ".join(missing)))
        if not data.get("file_b64"):
            notes.append(self.env._("The file itself is not in the recipe: it comes in archived until you upload it."))
        return None

    @api.model
    def _check_health(self, data, notes):
        user = data.get("weekly_user")
        if user and not self.env["res.users"].search([("login", "=", user.get("login"))], limit=1):
            notes.append(self.env._(
                "No user with the login %s here: the weekly user is left as it is.", user.get("login")))
        return None

    # ----------------------------------------------------------------- import
    @api.model
    def _apply(self, kind, key, data):
        """Make or update the record, as the person.
        :return: ``(record, created)``"""
        self._check_manager()
        return getattr(self, f"_apply_{kind}")(key, data)

    @api.model
    def _apply_tour(self, key, data):
        groups, missing = self._groups_in(data.get("groups"))
        vals = {k: data.get(k) or False for k in ("name", "phrases_en", "phrases_ar", "action_xmlid", "module_name")}
        vals.update(sequence=data.get("sequence", 10), group_ids=[(6, 0, groups.ids)])
        tour = self._find("tour", key)
        steps = []
        for step in data.get("steps", []):
            line = {k: step.get(k) or False for k in ("label", "note", "res_model", "view_type", "action_xmlid")}
            line.update(sequence=step.get("sequence", 10), kind=step["kind"], name=step["name"], skip_reason=False)
            if step.get("res_model") and step["res_model"] not in self.env:
                line["skip_reason"] = self.env._("This database has no model %s.", step["res_model"])
            steps.append(line)
        if not tour:
            vals.update(recipe_key=key, company_id=self.env.company.id, active=not missing,
                        step_ids=[(0, 0, line) for line in steps])
            return self.env["nudge.tour"].create(vals), True
        if missing:
            vals["active"] = False
        tour.write(vals)
        # Never deleted: a step the recipe does not have stays; the others are matched by what they point at
        have = {(s.kind, s.name, s.res_model or ""): s for s in tour.step_ids}
        for line in steps:
            existing = have.get((line["kind"], line["name"], line["res_model"] or ""))
            if existing:
                existing.write(line)
            else:
                tour.write({"step_ids": [(0, 0, line)]})
        return tour, False

    @api.model
    def _apply_task(self, key, data):
        kind = "rule" if data.get("engine") == "rule" else "preset"
        vals = {k: data.get(k) for k in ("name", "engine", "kind", "instruction", "interval_number",
                                          "interval_type", "hour", "match", "send_email")}
        vals["weekday"] = data.get("weekday") or False
        vals["conditions"] = data.get("conditions") or False
        model = data.get("model_name")
        vals["model_name"] = model if model and model in self.env else False
        if kind == "rule":
            rule = data.get("rule") or {}
            Fields = self.env["ir.model.fields"]
            record_model = self.env["ir.model"].search([("model", "=", model)], limit=1)
            field = Fields.search([("model_id", "=", record_model.id), ("name", "=", rule.get("field"))], limit=1)
            groupby = Fields.search([("model_id", "=", record_model.id), ("name", "=", rule.get("groupby"))], limit=1)
            vals.update(rule_model_id=record_model.id, rule_domain=rule.get("domain") or "[]",
                        rule_measure=rule.get("measure") or "count", rule_field_id=field.id,
                        rule_groupby_id=groupby.id, rule_condition=rule.get("condition") or "always",
                        rule_threshold=rule.get("threshold") or 0.0)
        task = self._find(kind, key)
        if task:
            task.write(vals)
            return task, False
        vals.update(recipe_key=key, company_id=self.env.company.id, user_id=self.env.user.id)
        if kind == "preset":
            # An instruction that runs with the manager's tools starts switched off, to be read and switched on
            vals["active"] = False
        return self.env["nudge.task"].create(vals), True

    _apply_rule = _apply_task
    _apply_preset = _apply_task

    @api.model
    def _apply_memory(self, key, data):
        groups, missing = self._groups_in(data.get("groups"))
        vals = {"question": data["question"], "answer": data["answer"], "group_ids": [(6, 0, groups.ids)]}
        memory = self._find("memory", key)
        if memory:
            memory.write(vals | ({"active": False} if missing else {}))
            return memory, False
        # Proposed, not approved: an answer served to everybody is read and approved by a manager first
        vals.update(recipe_key=key, company_id=self.env.company.id, state="proposed",
                    proposed_by=self.env.uid, active=not missing)
        return self.env["nudge.memory"].create(vals), True

    @api.model
    def _apply_lexicon(self, key, data):
        vals = {"synonym": data["synonym"], "intent": data["intent"], "dialect": data.get("dialect") or "msa"}
        entry = self._find("lexicon", key)
        if entry:
            entry.write(vals)
            return entry, False
        vals.update(recipe_key=key, company_id=self.env.company.id)
        return self.env["nudge.lexicon.entry"].create(vals), True

    @api.model
    def _apply_screen(self, key, data):
        vals = {"name": data["name"], "module_name": data.get("module_name") or False,
                "action_xmlid": data["action_xmlid"], "sequence": data.get("sequence") or 10}
        screen = self._find("screen", key)
        if screen:
            screen.write(vals)
            return screen, False
        vals["recipe_key"] = key
        return self.env["nudge.coach.screen"].create(vals), True

    @api.model
    def _apply_file(self, key, data):
        groups, missing = self._groups_in(data.get("groups"))
        vals = {"name": data["name"], "purpose": data.get("purpose") or False,
                "file_name": data.get("file_name") or False, "group_ids": [(6, 0, groups.ids)]}
        if data.get("file_b64"):
            base64.b64decode(data["file_b64"], validate=True)
            vals["file"] = data["file_b64"]
        source = self._find("file", key)
        if source:
            source.write(vals | ({"active": False} if missing else {}))
            return source, False
        vals.update(recipe_key=key, active=not missing and bool(data.get("file_b64")))
        return self.env["nudge.source"].create(vals), True

    @api.model
    def _apply_health(self, key, data):
        company = self.env.company
        user = data.get("weekly_user")
        found = self.env["res.users"].search([("login", "=", user["login"])], limit=1) if user else None
        if found:
            company.write({"nudge_health_user_id": found.id})
        return company, False

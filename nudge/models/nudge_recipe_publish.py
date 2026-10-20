"""Publish ours: the local recipes as an index.json and the recipe files, in one ZIP to host anywhere.

Nothing is uploaded by Nudge. The ZIP holds ``index.json`` and ``recipes/<key>.json``, the format
``doc/recipes-index.md`` describes and the marketplace reads."""
import base64
import hashlib
import io
import json
import re
import zipfile

from odoo import api, fields, models, release
from odoo.exceptions import UserError

from .nudge_recipe import canonical
from .nudge_recipe_market import INDEX_FORMAT, KEY, MODULE
from .nudge_recipe_wizard import FLAGS

# What a kind of item is called in an index
INDEX_KIND_OF = {
    "tour": "tour", "rule": "rules", "preset": "rules", "lexicon": "lexicon", "memory": "answers",
    "health": "pack", "file": "pack", "screen": "pack",
}


class NudgeRecipePublish(models.TransientModel):
    _name = "nudge.recipe.publish"
    _description = "Publish Nudge Recipes"

    key = fields.Char(string="Key", required=True, help="Lower case letters, digits, dots, dashes: e.g. raghad-sales-tours.")
    name_en = fields.Char(string="Name (English)")
    name_ar = fields.Char(string="Name (Arabic)")
    description_en = fields.Text(string="Description (English)")
    description_ar = fields.Text(string="Description (Arabic)")
    author = fields.Char(default=lambda self: self.env.user.name)
    licence = fields.Char(default="LGPL-3")
    apps = fields.Char(string="Apps Required", help="Module names separated by commas, e.g. sale, purchase.")
    base_url = fields.Char(
        string="Where You Will Host It",
        help="Optional: the HTTPS folder the files will be hosted in. Empty keeps the addresses relative to the index.")
    split = fields.Boolean(string="One Recipe per Kind", help="Make a recipe for the tours, one for the rules, and so on.")
    with_tours = fields.Boolean(string="Recorded tours", default=True)
    with_rules = fields.Boolean(string="Rule tasks (no AI)", default=True)
    with_presets = fields.Boolean(string="Task and digest presets", default=False)
    with_health = fields.Boolean(string="Health check settings", default=False)
    with_lexicon = fields.Boolean(string="Command words of the company", default=True)
    with_memories = fields.Boolean(string="Remembered answers (approved)", default=False)
    with_files = fields.Boolean(string="Company files (their description)", default=False)
    with_screens = fields.Boolean(string="Key screens of the training of new users", default=False)
    include_files = fields.Boolean(string="Include the files themselves")
    file = fields.Binary(string="ZIP", readonly=True, attachment=False)
    file_name = fields.Char(readonly=True)
    recipe_count = fields.Integer(readonly=True)
    state = fields.Selection([("choose", "Choose"), ("done", "Done")], default="choose")

    @api.model
    def _check_values(self, values):
        if not KEY.match(values["key"] or ""):
            raise UserError(self.env._("The key is lower case letters, digits, dots and dashes, up to 64."))
        if not (values["name_en"] or values["name_ar"]):
            raise UserError(self.env._("Give the recipe a name, in English or in Arabic."))
        apps = [app.strip() for app in (values["apps"] or "").split(",") if app.strip()]
        if not all(MODULE.match(app) for app in apps):
            raise UserError(self.env._("Apps are technical module names separated by commas."))
        base = values["base_url"] or ""
        if base and not base.startswith("https://"):
            raise UserError(self.env._("The place you will host it must be an HTTPS address."))
        return apps

    def _groups(self, kinds):
        """:return: ``[(index kind, [kinds of items])]``: one recipe, or one per kind when split"""
        by_kind = {}
        for kind in kinds:
            by_kind.setdefault(INDEX_KIND_OF[kind], []).append(kind)
        if self.split:
            return list(by_kind.items())
        return [(next(iter(by_kind)) if len(by_kind) == 1 else "pack", kinds)] if by_kind else []

    def action_publish(self):
        self.ensure_one()
        Recipe = self.env["nudge.recipe"]
        Recipe._check_manager()
        values = {name: self[name] for name in ("key", "name_en", "name_ar", "apps", "base_url")}
        apps = self._check_values(values)
        kinds = [kind for flag, kind in FLAGS.items() if self[flag]]
        labels = {
            "tour": self.env._("Tours"), "rules": self.env._("Rules"), "lexicon": self.env._("Command words"),
            "answers": self.env._("Answers"), "pack": self.env._("Pack")}
        today = fields.Date.to_string(fields.Date.context_today(self))
        files, entries = {}, []
        for index_kind, group in self._groups(kinds):
            package = Recipe._export(group, files=self.include_files)
            if not package["recipe"]["items"]:
                continue
            key = f"{self.key}-{index_kind}" if self.split else self.key
            raw = canonical(package).encode("utf-8")
            path = f"recipes/{key}.json"
            files[path] = raw
            suffix = f" ({labels[index_kind]})" if self.split else ""
            entries.append({
                "key": key,
                "name": {"en": (self.name_en or "") + (suffix if self.name_en else ""),
                         "ar": (self.name_ar or "") + (suffix if self.name_ar else "")},
                "description": {"en": self.description_en or "", "ar": self.description_ar or ""},
                "kind": index_kind, "apps": apps, "odoo": [release.series],
                "author": self.author or "", "licence": self.licence or "",
                "url": (self.base_url.rstrip("/") + "/" + path) if self.base_url else path,
                "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw), "updated": today,
            })
        if not entries:
            raise UserError(self.env._("There is nothing to publish: the kinds you ticked hold no records."))
        index = {"format": INDEX_FORMAT, "updated": today, "recipes": entries}
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("index.json", json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True))
            for path, raw in sorted(files.items()):
                archive.writestr(path, raw)
        self.write({
            "file": base64.b64encode(buffer.getvalue()), "state": "done", "recipe_count": len(entries),
            "file_name": re.sub(r"[^a-z0-9._-]", "-", self.key) + "-recipes.zip"})
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id,
                "view_mode": "form", "target": "new", "name": self.env._("Publish our recipes")}

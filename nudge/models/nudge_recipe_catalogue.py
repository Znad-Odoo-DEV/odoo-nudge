"""The skill catalogue (S2): the skills of the marketplace, one line each.

Two places feed it: ``data/skill_catalogue.json``, shipped with the module (each skill is a recipe, signed with the
Nudge publisher key), and the entries of kind ``skills`` of the recipe sources an administrator enabled (the
existing marketplace, fetched through the egress allow-list and checked against the sha256 of its index). A line
shows the domain, languages, tools (flagged when this Nudge has no such tool: its module is not installed here),
version and publisher. Install and Update are the import of the recipe as a draft, through the same wizard and the
same checks as any recipe: a skill never grants a tool, and a draft reaches no model until a manager publishes it.
"""
import base64
import difflib
import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import file_open

from ..tools import base, skills as skill_rules
from .nudge_recipe import _version_key, canonical

_logger = logging.getLogger(__name__)

CATALOGUE_FILE = "nudge/data/skill_catalogue.json"
SIGNATURE_STATES = [
    ("signed", "Signed"), ("untrusted", "Signed by an unknown key"), ("unsigned", "Unsigned"),
    ("unchecked", "Checked when installed")]


class NudgeSkillMarket(models.Model):
    _name = "nudge.skill.market"
    _description = "Nudge Skill Catalogue"
    _order = "domain, key, id"

    key = fields.Char(required=True, index=True)
    source_kind = fields.Selection([("bundled", "Comes with Nudge"), ("remote", "Marketplace")], required=True)
    entry_id = fields.Many2one("nudge.recipe.entry", ondelete="cascade", index=True)
    active = fields.Boolean(default=True)
    name_en = fields.Char()
    name_ar = fields.Char()
    summary = fields.Text()
    domain = fields.Char()
    languages = fields.Char(help="Language codes separated by commas.")
    tool_names = fields.Char(string="Tools Used")
    version = fields.Char()
    publisher = fields.Char()
    signature_state = fields.Selection(SIGNATURE_STATES, default="unchecked")
    instructions = fields.Text(help="Known for the skills that come with Nudge; the others are read when installed.")
    package = fields.Text(help="The recipe of a bundled skill.")

    title = fields.Char(compute="_compute_title")
    signed_label = fields.Char(string="Signature", compute="_compute_title")
    tools_missing = fields.Char(string="Tools Not Installed Here", compute="_compute_tools")
    installed_skill_id = fields.Many2one("nudge.skill", compute="_compute_state")
    installed_version = fields.Char(compute="_compute_state")
    state = fields.Selection(
        [("new", "Available"), ("installed", "Installed"), ("update", "Update available")],
        compute="_compute_state", search="_search_state")
    diff = fields.Text(string="What Changes in the Instructions", compute="_compute_state")

    @api.depends_context("lang")
    @api.depends("name_en", "name_ar", "key", "signature_state", "publisher")
    def _compute_title(self):
        arabic = (self.env.lang or "").startswith("ar")
        for line in self:
            first, second = (line.name_ar, line.name_en) if arabic else (line.name_en, line.name_ar)
            line.title = first or second or line.key
            if line.signature_state == "signed":
                line.signed_label = _("Signed by %s", line.publisher or "-")
            elif line.signature_state == "unchecked":
                line.signed_label = _("Checked when installed")
            else:
                line.signed_label = _("Unsigned")

    @api.depends("tool_names")
    def _compute_tools(self):
        for line in self:
            line.tools_missing = ", ".join(n for n in skill_rules.split_names(line.tool_names) if n not in base.TOOLS)

    @api.depends_context("company")
    @api.depends("key", "version", "instructions")
    def _compute_state(self):
        for line in self:
            skill = self.env["nudge.skill"].search([
                ("key", "=", line.key), ("company_id", "in", (False, self.env.company.id))], limit=1)
            line.installed_skill_id = skill
            line.installed_version = skill.version or False
            line.diff = False
            if not skill:
                line.state = "new"
            elif _version_key(line.version) > _version_key(skill.version):
                line.state = "update"
                if line.instructions:
                    line.diff = "\n".join(difflib.unified_diff(
                        (skill.instructions or "").splitlines(), line.instructions.splitlines(),
                        fromfile=_("installed %s", skill.version or ""),
                        tofile=_("new %s", line.version or ""), lineterm="", n=2))
            else:
                line.state = "installed"

    def _search_state(self, operator, value):
        if operator not in ("=", "!=", "in", "not in"):
            raise UserError(_("This filter cannot be used on the state of a skill."))
        wanted = value if isinstance(value, (list, tuple)) else [value]
        ids = [line.id for line in self.search([]) if (line.state in wanted) == (operator in ("=", "in"))]
        return [("id", "in", ids)]

    # ------------------------------------------------------------------ listing
    @api.model
    def _bundled(self):
        """:return: ``[(package, text)]`` of the skills that come with the module; one that does not read or
        does not verify is left out and logged, never offered"""
        Recipe = self.env["nudge.recipe"]
        try:
            with file_open(CATALOGUE_FILE) as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            _logger.warning("Nudge: the skill catalogue file could not be read.")
            return []
        out = []
        for package in data.get("skills") or []:
            text = canonical(package)
            try:
                body = Recipe._read(text)
                trust = Recipe._verify(text)
                item = next(i for i in body["items"] if i["kind"] == "skill")
            except (UserError, StopIteration):
                _logger.warning("Nudge: a skill of the shipped catalogue is damaged and is not listed.")
                continue
            out.append((package, item, trust))
        return out

    @api.model
    def _sync(self):
        """Make the lines the ones of the shipped file and of the enabled sources; what is gone is archived."""
        # sudo: the lines are Nudge's own cache of public catalogue data; what a person does with one goes
        # through the import wizard, as the person
        Line = self.sudo().with_context(active_test=False)
        existing = {(r.source_kind, r.key, r.entry_id.id): r for r in Line.search([])}
        wanted = {}
        for package, item, trust in self._bundled():
            data = item["data"]
            title = data.get("title") or {}
            wanted[("bundled", data["key"], False)] = {
                "key": data["key"], "source_kind": "bundled", "entry_id": False,
                "name_en": title.get("en") or "", "name_ar": title.get("ar") or "",
                "summary": str(data.get("summary") or "")[:200], "domain": str(data.get("domain") or "")[:60],
                "languages": ",".join(str(w) for w in data.get("languages") or []),
                "tool_names": ",".join(str(w) for w in data.get("tools") or []),
                "version": str(data.get("version") or "1.0.0")[:20],
                "publisher": trust["publisher"] or "Nudge", "signature_state": trust["state"],
                "instructions": data.get("instructions") or "", "package": canonical(package)}
        for entry in self.env["nudge.recipe.entry"].search([("kind", "=", "skills"), ("source_id.enabled", "=", True)]):
            wanted[("remote", entry.key, entry.id)] = {
                "key": entry.key, "source_kind": "remote", "entry_id": entry.id,
                "name_en": entry.name_en or "", "name_ar": entry.name_ar or "",
                "summary": entry.description_en or entry.description_ar or "", "domain": entry.domain or "",
                "languages": entry.languages or "", "tool_names": entry.tools or "",
                "version": entry.version or (fields.Date.to_string(entry.updated) if entry.updated else ""),
                "publisher": entry.publisher or entry.author or "", "signature_state": "unchecked",
                "instructions": False, "package": False}
        for index, values in wanted.items():
            if index in existing:
                existing.pop(index).write(dict(values, active=True))
            else:
                Line.create(values)
        for gone in existing.values():
            if gone.active:
                gone.active = False

    @api.model
    def action_open(self):
        """The Skill Catalogue menu: list what is available, then show it."""
        self.env["nudge.recipe"]._check_manager()
        self._sync()
        return self.env["ir.actions.act_window"]._for_xml_id("nudge.nudge_skill_market_action")

    # ------------------------------------------------------------------ install
    def action_install(self):
        """Install or update: the recipe of this skill, read by the import wizard, which makes it a draft."""
        self.ensure_one()
        Recipe = self.env["nudge.recipe"]
        Recipe._check_manager()
        if self.tools_missing:
            raise UserError(_(
                "This skill uses tools this Nudge does not have (%s): install their module first.",
                self.tools_missing))
        if self.source_kind == "remote":
            return self.entry_id.action_install()
        Recipe._verify(self.package)  # the stored copy is checked again: it is what is imported
        wizard = self.env["nudge.recipe.import"].create({
            "file": base64.b64encode(self.package.encode("utf-8")), "file_name": f"{self.key}.json"})
        return wizard.action_read()

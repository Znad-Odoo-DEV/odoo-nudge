"""The recipe marketplace: a catalogue of shareable recipes fed from public indexes.

A source is the address of an ``index.json`` (see ``doc/recipes-index.md``). Refreshing it caches the
index in an attachment and lists its recipes. Installing one downloads the recipe file, checks its
sha256 against the index, and hands it to the import wizard, which previews it and makes everything
as the manager. Fetching an index is not an AI call; private mode refuses it unless the company
allowed it. Nothing is ever uploaded, and nothing is deleted: what goes away is archived.
"""
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import re
import socket
import time
from datetime import date, datetime
from urllib.parse import urljoin, urlparse

import requests

from odoo import api, fields, models, release, tools
from odoo.exceptions import UserError

from ..tools.deadline import cap as worker_cap
from .nudge_provider import _is_private_address

_logger = logging.getLogger(__name__)

INDEX_FORMAT = 1
INDEX_KINDS = [
    ("tour", "Recorded Tours"), ("rules", "Rules"), ("lexicon", "Command Words"),
    ("answers", "Remembered Answers"), ("pack", "Pack"), ("prompts", "Prompts"), ("skills", "Skill"),
]
DEFAULT_URL = "https://znad-odoo-dev.github.io/nudge/recipes/index.json"
CAP = 2 * 1024 * 1024
TIMEOUT = 10
# The whole of a download, redirects included: a server that sends a block every few seconds is not waited for
TOTAL_SECONDS = 20
MAX_REDIRECTS = 3
MAX_ENTRIES = 500
MAX_CRON_SOURCES = 20
CRON_BUDGET = 60  # seconds of fetching in one run of the weekly cron
KEY = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
MODULE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
ISO_DATE = re.compile(r"^\d{4}-\d\d-\d\d$")


class FetchError(Exception):
    """A fetch that was refused or failed; ``code`` says why, the model turns it into a sentence."""

    def __init__(self, code, detail=""):
        super().__init__(code)
        self.code = code
        self.detail = detail


def _public_only(host):
    """:raise FetchError: when ``host`` is, or resolves to, an address of this machine or a private network.
    The system resolver reads every shape an address can take (``127.1``, ``2130706433``, ``0``, a name
    that points inside), so the check is made on what it answers, not on how the host is written."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        raise FetchError("network") from None
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0].split("%")[0])
        except ValueError:
            raise FetchError("private") from None
        if not address.is_global or address.is_multicast:
            raise FetchError("private")


def _require_https(url):
    parsed = urlparse(url or "")
    if parsed.scheme != "https" or not parsed.hostname:
        raise FetchError("scheme")
    if _is_private_address(url):
        raise FetchError("private")
    _public_only(parsed.hostname)


def _download(url, cap=CAP, timeout=TIMEOUT):
    """:return: the bytes of ``url``, HTTPS only (redirects too), at most ``cap`` of them
    :raise FetchError: when it is refused, too big, slow or unreachable"""
    limit = time.monotonic() + worker_cap(TOTAL_SECONDS)
    for _hop in range(MAX_REDIRECTS + 1):
        _require_https(url)
        try:
            with requests.get(url, stream=True, timeout=timeout, allow_redirects=False,
                              headers={"User-Agent": "Nudge-recipes"}) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    url = urljoin(url, response.headers.get("Location") or "")
                    continue
                if response.status_code == 404:
                    raise FetchError("notfound")
                if response.status_code >= 400:
                    raise FetchError("http", response.status_code)
                declared = response.headers.get("Content-Length") or ""
                if declared.isdigit() and int(declared) > cap:
                    raise FetchError("toobig")
                data = bytearray()
                for block in response.iter_content(1 << 16):
                    data += block
                    if len(data) > cap:
                        raise FetchError("toobig")
                    if time.monotonic() > limit:
                        raise FetchError("timeout")
                return bytes(data)
        except requests.Timeout:
            raise FetchError("timeout") from None
        except requests.RequestException:
            raise FetchError("network") from None
    raise FetchError("redirects")


def _words(value):
    """``{en, ar}`` from a dict, or a plain string taken as English."""
    if isinstance(value, str):
        value = {"en": value}
    if not isinstance(value, dict):
        return {"en": "", "ar": ""}
    return {lang: value[lang].strip()[:2000] if isinstance(value.get(lang), str) else ""
            for lang in ("en", "ar")}


def _versions(values):
    out = []
    for value in values if isinstance(values, list) else []:
        text = str(value).strip()
        if re.fullmatch(r"\d{1,2}", text):
            text += ".0"
        if re.fullmatch(r"\d{1,2}\.\d", text) and text not in out:
            out.append(text)
    return out


def _refusal(item, key, seen, kinds, base_url):
    """:return: why this entry of an index is refused (a short English phrase), or None"""
    if not isinstance(item, dict):
        return "not an object"
    if not isinstance(key, str) or not KEY.match(key):
        return "bad key"
    if key in seen:
        return "key listed twice"
    if item.get("kind") not in kinds:
        return "unknown kind"
    if not isinstance(item.get("sha256"), str) or not SHA256.match(item["sha256"].lower()):
        return "bad sha256"
    name, apps = _words(item.get("name")), item.get("apps") or []
    if not (name["en"] or name["ar"]):
        return "no name"
    if not isinstance(apps, list) or not all(isinstance(app, str) and MODULE.match(app) for app in apps):
        return "bad apps"
    if urlparse(urljoin(base_url, str(item.get("url") or ""))).scheme != "https":
        return "the recipe address is not HTTPS"
    updated = str(item.get("updated") or "")[:10]
    try:
        if not ISO_DATE.match(updated):
            raise ValueError(updated)
        date.fromisoformat(updated)
    except ValueError:
        return "bad date"
    return None


def parse_index(raw, base_url=""):
    """Read an index.

    :param raw: the bytes (or text) of the index
    :param base_url: the address of the index, against which relative recipe addresses are resolved
    :return: ``(entries, problems)``: the valid entries, normalised, and one phrase per refused one
    :raise ValueError: when the index itself cannot be read"""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        raise ValueError("not a JSON index") from None
    if isinstance(data, dict):
        if isinstance(data.get("format"), int) and data["format"] > INDEX_FORMAT:
            raise ValueError("an index of a newer format")
        data = data.get("recipes")
    if not isinstance(data, list):
        raise ValueError("no list of recipes")
    entries, problems, seen = [], [], set()
    kinds = {kind for kind, _label in INDEX_KINDS}
    for number, item in enumerate(data[:MAX_ENTRIES], 1):
        key = item.get("key") if isinstance(item, dict) else None
        reason = _refusal(item, key, seen, kinds, base_url)
        if reason:
            problems.append(f"{key if isinstance(key, str) else '#%s' % number}: {reason}")
            continue
        seen.add(key)
        size = item.get("size")
        entries.append({
            "key": key, "name": _words(item.get("name")), "description": _words(item.get("description")),
            "kind": item["kind"], "apps": list(item.get("apps") or []), "odoo": _versions(item.get("odoo")),
            "author": str(item.get("author") or "")[:200], "licence": str(item.get("licence") or "")[:100],
            "domain": str(item.get("domain") or "")[:60], "version": str(item.get("version") or "")[:20],
            "publisher": str(item.get("publisher") or "")[:100],
            "languages": ",".join(w for w in (item.get("languages") or []) if isinstance(w, str) and re.fullmatch(r"[a-z]{2,3}", w))
            if isinstance(item.get("languages"), list) else "",
            "tools": ",".join(w for w in (item.get("tools") or []) if isinstance(w, str) and re.fullmatch(r"[a-z0-9_]{1,64}", w))
            if isinstance(item.get("tools"), list) else "",
            "url": urljoin(base_url, str(item["url"])), "sha256": item["sha256"].lower(),
            "size": size if isinstance(size, int) and size >= 0 else 0, "updated": str(item["updated"])[:10],
        })
    if len(data) > MAX_ENTRIES:
        problems.append(f"only the first {MAX_ENTRIES} recipes are read")
    return entries, problems


class NudgeRecipeSource(models.Model):
    _name = "nudge.recipe.source"
    _description = "Nudge Recipe Source"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    url = fields.Char(string="Address of the Index", required=True, help="An HTTPS address of an index.json.")
    enabled = fields.Boolean(default=True)
    active = fields.Boolean(default=True)
    weekly = fields.Boolean(string="Refresh Every Week", help="Read this index again once a week, by itself.")
    trusted = fields.Boolean(
        help="A source you trust. Recipes of a source that is not trusted cannot bring files with them.")
    last_fetched = fields.Datetime(readonly=True)
    index_checksum = fields.Char(string="Checksum of the Index", readonly=True)
    last_message = fields.Char(string="Last Result", readonly=True)
    index_file = fields.Binary(string="Cached Index", attachment=True, readonly=True)
    entry_ids = fields.One2many("nudge.recipe.entry", "source_id", string="Recipes", readonly=True)
    entry_count = fields.Integer(compute="_compute_entry_count")

    @api.depends("entry_ids")
    def _compute_entry_count(self):
        for source in self:
            source.entry_count = len(source.entry_ids)

    @api.constrains("url")
    def _check_url(self):
        for source in self:
            if urlparse(source.url or "").scheme != "https":
                raise UserError(self.env._("Only HTTPS addresses are accepted for a recipe index."))

    @api.model_create_multi
    def create(self, vals_list):
        sources = super().create(vals_list)
        self._sync_weekly_cron()
        return sources

    def write(self, vals):
        result = super().write(vals)
        if {"enabled", "weekly"} & vals.keys():
            self._sync_weekly_cron()
        return result

    @api.model
    def _sync_weekly_cron(self):
        """The weekly cron runs only while a source asked for it: with none, nothing is fetched by itself"""
        cron = self.env.ref("nudge.cron_recipe_index", raise_if_not_found=False)
        if cron:
            cron.sudo().active = bool(self.sudo().search_count([("enabled", "=", True), ("weekly", "=", True)]))

    # ------------------------------------------------------------- fetching
    @api.model
    def _check_fetch_allowed(self):
        """A fetch is not an AI call, but it leaves the server: refused in private mode unless allowed."""
        self.env["nudge.recipe"]._check_manager()
        if self.env["nudge.provider"]._is_private_mode() and not self.env.company.sudo().nudge_allow_recipe_index:
            raise UserError(self.env._(
                "Private mode is on: recipe indexes are not fetched. An administrator can allow it "
                "(Settings, Nudge: Allow fetching recipe indexes)."))

    @api.model
    def _get(self, url, cap=CAP):
        """:return: the bytes of ``url``, or raises a UserError with a sentence that says why not"""
        self.env["nudge.egress"].check(url, "recipes")  # NP-15
        try:
            return _download(url, cap)
        except FetchError as error:
            raise UserError(self._fetch_message(error)) from None

    def _fetch_message(self, error):
        messages = {
            "scheme": self.env._("Only HTTPS addresses are read."),
            "private": self.env._("This address is on your own network: it is not read."),
            "timeout": self.env._("The site did not answer within %s seconds.", TIMEOUT),
            "toobig": self.env._("The file is larger than %s MB: refused.", CAP // (1024 * 1024)),
            "notfound": self.env._("No index yet at this address."),
            "http": self.env._("No index yet: the site answered with an error."),
            "network": self.env._("No index yet: the address could not be reached."),
            "redirects": self.env._("Too many redirects: refused."),
        }
        return messages.get(error.code, self.env._("The address could not be read."))

    def _refresh_one(self):
        """Fetch the index of this source, cache it and list its recipes. A fetch that fails does not
        raise: the sentence is kept in ``last_message`` and what was cached stays.

        :return: whether the index was read"""
        self.ensure_one()
        try:
            raw = self._get(self.url)
        except UserError as error:
            self.last_message = error.args[0]
            return False
        try:
            entries, problems = parse_index(raw, self.url)
        except ValueError:
            self.last_message = self.env._("The index could not be read: it is not a list of recipes.")
            return False
        message = self.env._("%s recipes listed.", len(entries))
        if problems:
            message += " " + self.env._(
                "%(count)s refused: %(list)s", count=len(problems), list="; ".join(problems[:5]))
        self.write({
            "index_file": base64.b64encode(raw), "last_fetched": fields.Datetime.now(),
            "index_checksum": hashlib.sha256(raw).hexdigest(), "last_message": message[:1000]})
        self.env["nudge.recipe.entry"]._sync(self, entries)
        return True

    def action_refresh(self):
        self._check_fetch_allowed()
        done, messages = 0, []
        for source in self.filtered("enabled"):
            done += source._refresh_one()
            messages.append("%s: %s" % (source.name, source.last_message))
        if not messages:
            messages = [self.env._("Nothing to refresh: no source is enabled.")]
        self.env["nudge.recipe.entry"]._notes_for_new_matches()
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "title": self.env._("Recipe index"), "message": "\n".join(messages),
            "type": "success" if done else "warning", "sticky": False}}

    @api.model
    def _cron_refresh(self):
        """Weekly: the sources that asked for it, only when every company allows a fetch; the notes of
        each company are written as the manager who looks after its health check."""
        sources = self.search([("enabled", "=", True), ("weekly", "=", True)])
        companies = self.env["res.company"].sudo().search([])
        if not sources or any(c.nudge_private_mode and not c.nudge_allow_recipe_index for c in companies):
            return
        started = time.monotonic()
        # Each source is kept as it is done; the ones the budget did not reach come first next week
        for source in sources.sorted(lambda src: (src.last_fetched or datetime(2000, 1, 1), src.id))[:MAX_CRON_SOURCES]:
            if time.monotonic() - started > CRON_BUDGET:
                break
            try:
                with self.env.cr.savepoint():
                    source._refresh_one()
            except Exception:  # noqa: BLE001
                _logger.exception("Nudge: the recipe index %s could not be refreshed", source.id)
            if not self.env.context.get("nudge_keep_transaction") and not tools.config["test_enable"]:
                self.env.cr.commit()
        for company in companies.filtered("nudge_health_user_id"):
            user = company.nudge_health_user_id
            if user.active and company in user.company_ids and user.has_group("nudge.group_nudge_manager"):
                self.env["nudge.recipe.entry"].with_user(user).with_company(company)._notes_for_new_matches()


class NudgeRecipeEntry(models.Model):
    _name = "nudge.recipe.entry"
    _description = "Nudge Marketplace Recipe"
    _order = "source_id, key"

    source_id = fields.Many2one("nudge.recipe.source", required=True, ondelete="cascade", index=True)
    key = fields.Char(required=True, index=True)
    active = fields.Boolean(default=True)
    name_en = fields.Char()
    name_ar = fields.Char()
    description_en = fields.Text()
    description_ar = fields.Text()
    kind = fields.Selection(INDEX_KINDS, required=True)
    apps = fields.Char(help="Modules the recipe needs, separated by commas.")
    odoo_versions = fields.Char(string="Odoo Versions")
    author = fields.Char()
    licence = fields.Char()
    # Skills of a catalogue (S2): what the index says before the file is fetched; the file is what counts
    domain = fields.Char()
    languages = fields.Char(help="Language codes separated by commas.")
    tools = fields.Char(help="Tools the skill uses, separated by commas.")
    version = fields.Char()
    publisher = fields.Char(string="Publisher (as the Index Says)")
    url = fields.Char()
    sha256 = fields.Char(string="Checksum")
    size = fields.Integer()
    updated = fields.Date()

    title = fields.Char(compute="_compute_texts")
    summary = fields.Text(compute="_compute_texts")
    apps_missing = fields.Char(compute="_compute_texts")
    compatible = fields.Boolean(compute="_compute_texts")
    state = fields.Selection(
        [("new", "Available"), ("installed", "Installed"), ("update", "Update available")],
        compute="_compute_texts", search="_search_state")
    installed_version = fields.Char(compute="_compute_texts")

    @api.depends_context("lang", "company")
    @api.depends("name_en", "name_ar", "description_en", "description_ar", "apps", "odoo_versions", "updated")
    def _compute_texts(self):
        arabic = (self.env.lang or "").startswith("ar")
        # sudo: the names of the installed modules, a fact about the database and no data of it
        installed = set(self.env["ir.module.module"].sudo().search([("state", "=", "installed")]).mapped("name"))
        history = self.env["nudge.recipe.installed"].search([
            ("entry_key", "in", self.mapped("key")), ("company_id", "=", self.env.company.id)])
        for entry in self:
            first, second = (entry.name_ar, entry.name_en) if arabic else (entry.name_en, entry.name_ar)
            entry.title = first or second or entry.key
            first, second = ((entry.description_ar, entry.description_en) if arabic
                             else (entry.description_en, entry.description_ar))
            entry.summary = first or second or ""
            wanted = [app for app in (entry.apps or "").split(",") if app]
            entry.apps_missing = ", ".join(app for app in wanted if app not in installed)
            versions = [v for v in (entry.odoo_versions or "").split(",") if v]
            entry.compatible = not versions or release.series in versions
            last = history.filtered(lambda h, e=entry: h.source_id == e.source_id and h.entry_key == e.key)[:1]
            entry.installed_version = last.version or False
            if not last:
                entry.state = "new"
            elif entry.updated and last.version and fields.Date.to_string(entry.updated) > last.version:
                entry.state = "update"
            else:
                entry.state = "installed"

    def _search_state(self, operator, value):
        if operator not in ("=", "!=", "in", "not in"):
            raise UserError(self.env._("This filter cannot be used on the state of a recipe."))
        wanted = value if isinstance(value, (list, tuple)) else [value]
        ids = [entry.id for entry in self.search([]) if (entry.state in wanted) == (operator in ("=", "in"))]
        return [("id", "in", ids)]

    @api.model
    def _sync(self, source, entries):
        """Make the list of ``source`` the one of ``entries``; what is gone from the index is archived."""
        existing = {entry.key: entry for entry in self.with_context(active_test=False).search(
            [("source_id", "=", source.id)])}
        for item in entries:
            values = {
                "name_en": item["name"]["en"], "name_ar": item["name"]["ar"],
                "description_en": item["description"]["en"], "description_ar": item["description"]["ar"],
                "kind": item["kind"], "apps": ",".join(item["apps"]), "odoo_versions": ",".join(item["odoo"]),
                "author": item["author"], "licence": item["licence"], "url": item["url"],
                "domain": item["domain"], "languages": item["languages"], "tools": item["tools"],
                "version": item["version"], "publisher": item["publisher"],
                "sha256": item["sha256"], "size": item["size"], "updated": item["updated"], "active": True,
            }
            if item["key"] in existing:
                existing.pop(item["key"]).write(values)
            else:
                self.create(dict(values, source_id=source.id, key=item["key"]))
        for gone in existing.values():
            if gone.active:
                gone.active = False

    # --------------------------------------------------------------- install
    def action_install(self):
        """Download the recipe, check it against the index, and open the import wizard on it."""
        self.ensure_one()
        Source = self.env["nudge.recipe.source"]
        Source._check_fetch_allowed()
        if not self.compatible:
            raise UserError(self.env._(
                "This recipe is for Odoo %(versions)s; this database runs %(series)s.",
                versions=self.odoo_versions, series=release.series))
        raw = Source._get(self.url)
        if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), (self.sha256 or "").lower()):
            raise UserError(self.env._(
                "The checksum of the file does not match the index: it was changed, so it is not installed."))
        body = self.env["nudge.recipe"]._read(raw.decode("utf-8", errors="replace"))
        if not self.source_id.trusted and any("file_b64" in (item.get("data") or {}) for item in body["items"]):
            raise UserError(self.env._(
                "This source is not trusted, and the recipe brings files with it: it is not installed."))
        if not self.source_id.trusted and any(
                step.get("kind") == "button" for item in body["items"] if item.get("kind") == "tour"
                for step in (item.get("data") or {}).get("steps") or [] if isinstance(step, dict)):
            raise UserError(self.env._(
                "This source is not trusted, and the recipe has a tour that presses buttons: it is not installed."))
        wizard = self.env["nudge.recipe.import"].create({
            "file": base64.b64encode(raw), "file_name": f"{self.key}.json", "entry_id": self.id})
        return wizard.action_read()

    # ---------------------------------------------------------- business notes
    @api.model
    def _notes_for_new_matches(self):
        """A line in Business Notes (waiting for approval) for each recipe not installed yet that
        needs an app this company installed this month. The same recipe is told once."""
        month_start = fields.Date.context_today(self).replace(day=1)
        # sudo: names of modules and when they were installed, a fact about the database
        modules = self.env["ir.module.module"].sudo().search([
            ("state", "=", "installed"), ("write_date", ">=", fields.Datetime.to_datetime(month_start))])
        recent = set(modules.mapped("name"))
        if not recent:
            return
        Note = self.env["nudge.note"]
        for entry in self.search([("source_id.enabled", "=", True)]):
            match = sorted(recent.intersection(app for app in (entry.apps or "").split(",") if app))
            if not match or entry.state != "new" or not entry.compatible:
                continue
            name = self.env._("Recipe available: %s", entry.title)[:200]
            if Note.with_context(active_test=False).search_count([("name", "=", name)]):
                continue
            Note.create({
                "name": name, "kind": "lesson", "state": "proposed", "proposed_by": self.env.uid,
                "company_id": False,
                "body": self.env._(
                    "The recipe %(recipe)s fits %(app)s, which was installed this month. "
                    "Find it in Nudge / Configuration / Recipes / Marketplace.",
                    recipe=entry.title, app=match[0]),
                "reason": self.env._("From the recipe marketplace."),
            })


class NudgeRecipeInstalled(models.Model):
    """Which recipe of which source was installed, at which version, when and by whom. The history is
    kept: an update archives the line before it."""
    _name = "nudge.recipe.installed"
    _description = "Installed Nudge Recipe"
    _order = "installed_on desc, id desc"

    source_id = fields.Many2one("nudge.recipe.source", required=True, ondelete="restrict", index=True)
    entry_key = fields.Char(string="Key", required=True, index=True)
    name = fields.Char(required=True)
    version = fields.Char(string="Version (date of the index)", required=True)
    installed_on = fields.Datetime(default=fields.Datetime.now, required=True)
    user_id = fields.Many2one("res.users", string="Installed By", required=True, default=lambda self: self.env.uid)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    active = fields.Boolean(default=True)

    def init(self):
        # One active line per recipe, source and company, whatever happens at the same instant
        self.env.cr.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS nudge_recipe_installed_active_uniq "
            "ON nudge_recipe_installed (source_id, entry_key, company_id) WHERE active")

    @api.model
    def _record(self, entry):
        # Two installs of the same recipe at once are taken one after the other
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            [f"nudge.recipe.installed:{entry.source_id.id}:{entry.key}:{self.env.company.id}"])
        self.search([("source_id", "=", entry.source_id.id), ("entry_key", "=", entry.key),
                     ("company_id", "=", self.env.company.id)]).write({"active": False})
        # Written now, before the new line: the database allows one active line only
        self.flush_model(["active"])
        installed = self.create({
            "source_id": entry.source_id.id, "entry_key": entry.key, "name": entry.title,
            "version": fields.Date.to_string(entry.updated) if entry.updated else ""})
        # The state of the cards is read from what is installed
        self.env["nudge.recipe.entry"].invalidate_model(["state", "installed_version"])
        return installed

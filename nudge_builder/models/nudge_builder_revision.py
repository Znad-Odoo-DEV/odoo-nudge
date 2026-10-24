"""What the builder changed after the build, kept so each change can be undone: a publish, a section edit,
an SEO edit. A revision keeps what it replaced and a fingerprint of what it wrote; an undo is refused when the
page or the setting is not the way the change left it (someone edited it since)."""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..tools import compat, sections
from ..tools.safe import safe_text


class NudgeBuilderRevision(models.Model):
    _name = "nudge.builder.revision"
    _description = "Nudge Builder Revision"
    _order = "id desc"

    site_id = fields.Many2one("nudge.builder.site", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="site_id.company_id", store=True, index=True)
    kind = fields.Selection([("publish", "Publish"), ("section", "Section"), ("seo", "SEO")], required=True)
    page_id = fields.Many2one("nudge.builder.page", ondelete="set null")
    name = fields.Char(required=True, help="What was changed, in one line.")
    user_id = fields.Many2one("res.users", default=lambda self: self.env.user, readonly=True)
    state = fields.Selection([("done", "Done"), ("undone", "Undone")], default="done", required=True)
    before = fields.Json(help="What the change replaced.")
    after = fields.Json(help="What the change wrote: the fingerprint an undo checks first.")

    # ------------------------------------------------------------------ undo
    def action_undo(self):
        """Put back what this change replaced; refused when it was changed again since."""
        self.ensure_one()
        site = self.site_id
        site._check_designer()
        if self.state != "done":
            raise UserError(_("This change was already undone."))
        notes = getattr(self, "_undo_%s" % self.kind)()
        self.state = "undone"
        site._say(_("Undone: %s", self.name))
        for note in notes or []:
            site._say(note)
        return site._reopen()

    def _undo_page(self):
        page = self.page_id
        if not page or not page.view_id:
            raise UserError(_("The page of this change no longer exists."))
        compat.check_write(page.view_id)
        return page

    def _undo_section(self):
        page = self._undo_page()
        view = page.view_id
        current = sections.digest(view.with_context(lang="en_US").arch_db)
        if current != (self.after or {}).get("hash"):
            raise UserError(_(
                "The page %s was changed after this edit; undo is refused so that nothing newer is lost.", page.name))
        compat.write_arch(view, self.before["archs"], "en_US")
        _tree, wrap = compat.wrap_body(view.with_context(lang="en_US").arch_db)
        page.snippet_keys = ",".join(sections.keys_of(wrap))

    def _undo_seo(self):
        page = self._undo_page()
        current = self.site_id._seo_read(page.view_id, list(self.after))
        if current != self.after:
            raise UserError(_(
                "The SEO of the page %s was changed after this edit; undo is refused so that nothing newer is lost.",
                page.name))
        self.site_id._seo_write(page.view_id, self.before)

    def _undo_publish(self):
        data, site, website = self.before, self.site_id, self.site_id.website_id
        notes = []
        by_id = {item["id"]: item for item in data["pages"]}
        for page in self.env["website.page"].browse(list(by_id)).exists():
            was = by_id[page.id]["was"]
            if page.is_published == was:
                continue
            if page.is_published != self.after["published"]:
                notes.append(_("%s was changed since and keeps its state.", page.url))
                continue
            page.is_published = was
        if (data.get("home_before") or False) != (self.after.get("home") or False):
            if (website.homepage_url or False) == (self.after.get("home") or False):
                website.homepage_url = data.get("home_before") or False
            else:
                notes.append(_("The home page of the website was changed since and is left as it is."))
        for item in data.get("menus") or []:
            menu = self.env["website.menu"].browse(item["id"]).exists()
            if menu and (menu.sequence != item["sequence"] or menu.parent_id.id != item["parent_id"]):
                menu.write({"sequence": item["sequence"], "parent_id": item["parent_id"]})
        site.state = data.get("state_before") or "built"
        return notes


class NudgeBuilderSiteRevisions(models.Model):
    """The site's list of revisions and the way SEO is read and written"""
    _inherit = "nudge.builder.site"

    revision_ids = fields.One2many("nudge.builder.revision", "site_id", string="Revisions")
    last_publish_id = fields.Many2one("nudge.builder.revision", compute="_compute_last_publish")

    @api.depends("revision_ids.state", "revision_ids.kind")
    def _compute_last_publish(self):
        for site in self:
            site.last_publish_id = site.revision_ids.filtered(
                lambda revision: revision.kind == "publish" and revision.state == "done").sorted("id", reverse=True)[:1]

    def action_undo_publish(self):
        """Undo the last publish: pages, home page and menu go back as they were"""
        self.ensure_one()
        self._check_designer()
        if not self.last_publish_id:
            raise UserError(_("There is no publish to undo."))
        return self.last_publish_id.action_undo()

    # ------------------------------------------------------------------ SEO of a page
    def _seo_codes(self):
        """:return: the language codes whose SEO is kept: the source first, then the site's"""
        self.ensure_one()
        return ["en_US"] + [code for code in self._langs().mapped("code") if code != "en_US"]

    def _seo_read(self, view, names):
        """:return: ``{field: {lang code: value}}`` of the SEO fields of a page's view"""
        self.ensure_one()
        return {name: {code: view.with_context(lang=code)[name] or False for code in self._seo_codes()}
                for name in names if name in view._fields}

    def _seo_write(self, view, values):
        """Write ``{field: {lang code: value}}``; the source language goes first, as the arch does"""
        self.ensure_one()
        for name, per_lang in values.items():
            if name not in view._fields:
                continue
            for code in sorted(per_lang, key=lambda code: code != "en_US"):
                view.with_context(lang=code).write({name: per_lang[code] or False})

    def _write_seo(self, page, seo, image_url=None):
        """The SEO of a page the builder made, from its template: title and description in each language of the
        site (English where the template has no text in one), keywords and share picture when the template
        and this version have them.

        :param page: the ``website.page``
        :param dict seo: ``{"title": {lang: text}, "description": {...}, "keywords": {...}}``
        :return: what was written, ``{field: {lang code: value}}``"""
        self.ensure_one()
        seo = seo or {}
        view = page.view_id
        values = {}
        for name, key in (("website_meta_title", "title"), ("website_meta_description", "description"),
                          ("website_meta_keywords", "keywords")):
            texts = seo.get(key)
            if not texts or name not in view._fields:
                continue
            english = texts.get("en") or next(iter(texts.values()))
            values[name] = {
                code: safe_text(texts.get(compat.lang_code(code)) or english).strip() or False
                for code in self._seo_codes()}
        if image_url and "website_meta_og_img" in view._fields:
            values["website_meta_og_img"] = {"en_US": image_url}
        self._seo_write(view, values)
        return values

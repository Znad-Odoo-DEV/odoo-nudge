import hashlib
import logging

from lxml import etree, html

from odoo import api, fields, models

from odoo.addons.nudge.models.nudge_source import split_passages

_logger = logging.getLogger(__name__)

MIN_PAGE_CHARS = 40
SKIPPED_TAGS = ("script", "style", "noscript", "svg", "template")


class NudgeSource(models.Model):
    _inherit = "nudge.source"

    is_public = fields.Boolean(
        string="Public on the Website", default=False,
        help="The assistant of the website answers visitors from this file, with no login. "
             "Off: the file stays for the people who work here.")
    website_page_id = fields.Many2one(
        "website.page", string="Page of the Website", readonly=True, ondelete="cascade", index=True)
    website_id = fields.Many2one(
        "website", string="Website", readonly=True, ondelete="cascade",
        help="The website the page belongs to. Empty: all of them.")
    public_url = fields.Char(string="Address", readonly=True)
    page_hash = fields.Char(readonly=True)

    @api.model
    def _for_person(self):
        sources = super()._for_person()
        if self.env.context.get("nudge_website"):
            # The assistant of the website: only what is public, on this site
            site = self.env.context.get("nudge_website_id")
            return sources.filtered(
                lambda source: source.is_public and (not source.website_id or source.website_id.id == site))
        # The people who work here: their files, not the copies of the pages
        return sources.filtered(lambda source: not source.website_page_id)

    # ------------------------------------------------------------------
    # The published pages, read as files
    # ------------------------------------------------------------------
    @api.model
    def _page_langs(self, page):
        """:return: the codes of the languages the page is read in: those of its website (of every
        website with the assistant, for a page they share), the default one first"""
        sites = page.website_id or self.env["website"].sudo().search([("nudge_assistant", "=", True)])
        langs = self.env["res.lang"]
        for site in sites:
            langs |= site.default_lang_id | site.language_ids
        return langs.mapped("code") or [self.env.lang or "en_US"]

    @api.model
    def _page_text(self, page):
        """:return: the words of a page of the website as its visitors read them, in each language
        of the website: a visitor who asks in Arabic is answered from the Arabic page"""
        texts = []
        for code in self._page_langs(page):
            text = self._arch_text(page.view_id.with_context(lang=code).arch_db or "")
            if text and text not in texts:
                texts.append(text)
        return "\n\n".join(texts)

    @api.model
    def _arch_text(self, arch):
        try:
            root = html.fromstring(f"<div>{arch}</div>")
        except (etree.ParserError, ValueError):
            return ""
        for tag in SKIPPED_TAGS:
            for node in list(root.iter(tag)):
                node.drop_tree()
        # Blocks of text stay apart
        for node in root.iter("p", "h1", "h2", "h3", "h4", "li", "div", "section", "br"):
            node.tail = "\n\n" + (node.tail or "")
        return "\n\n".join(line.strip() for line in root.text_content().splitlines() if line.strip())

    @api.model
    def _page_is_public(self, page):
        """:return: whether anyone at all may read this page of the website: published,
        in date, visible to everyone (not signed-in only, not with a password, not
        restricted to a group)"""
        return bool(
            page.website_published and page.is_visible
            and not page.visibility and not page.group_ids)

    @api.model
    def _purge_page_sources(self, sources):
        """Put pages away and forget their text, so nothing of them can be found or embedded."""
        sources = sources.sudo().with_context(active_test=False)
        sources.mapped("passage_ids").unlink()
        sources.write({"active": False, "page_hash": False, "state": "empty", "chars": 0})

    @api.model
    def _purge_nonpublic_pages(self):
        """Forget the text of every page that is not public (any more), whatever its state."""
        Source = self.sudo().with_context(active_test=False)
        stale = Source.search([("website_page_id", "!=", False)]).filtered(
            lambda source: not self._page_is_public(source.website_page_id))
        self._purge_page_sources(stale)
        return len(stale)

    @api.model
    def _sync_website_pages(self):
        """Keep one public file per published page of the websites that have the
        assistant: new pages are read, changed ones read again, unpublished
        ones put away. The text of a page that is published and visible to everyone is public by definition; this
        runs as the system, for no one in particular, and only reads pages.

        :return: how many pages are known"""
        Source = self.sudo().with_context(active_test=False)
        sites = self.env["website"].sudo().search([("nudge_assistant", "=", True)])
        done = Source.browse()
        if sites:
            pages = self.env["website.page"].sudo().search([("website_published", "=", True)])
            for page in pages:
                if not self._page_is_public(page):
                    continue
                if page.website_id and page.website_id not in sites:
                    continue
                text = self._page_text(page)
                if len(text) < MIN_PAGE_CHARS:
                    continue
                digest = hashlib.sha1(text.encode()).hexdigest()
                source = Source.search([("website_page_id", "=", page.id)], limit=1)
                done |= self._store_page(source, page, text, digest)
        # What is no more published, or no more wanted, is put away, never deleted
        stale = Source.search([("website_page_id", "!=", False), ("id", "not in", done.ids)]).filtered(
            lambda source: source.active or source.passage_ids)
        self._purge_page_sources(stale)
        return len(done)

    @api.model
    def _store_page(self, source, page, text, digest):
        values = {
            "name": page.name or page.url, "purpose": page.url, "public_url": page.url,
            "website_id": page.website_id.id, "is_public": True, "active": True}
        if source and source.page_hash == digest:
            source.write(values)
            return source
        if not source:
            source = self.sudo().create(dict(values, website_page_id=page.id))
        else:
            source.write(values)
        source.passage_ids.unlink()
        passages = split_passages([(1, text)])
        self.env["nudge.source.passage"].sudo().create([
            {"source_id": source.id, "sequence": index, "page": 1, "body": body}
            for index, (_page, body) in enumerate(passages)])
        source.write({
            "state": "ready", "error": False, "mimetype": "text/html", "pages": 1,
            "chars": len(text), "page_hash": digest})
        return source

    @api.model
    def _cron_sync_website_pages(self):
        try:
            self._sync_website_pages()
        except Exception:  # noqa: BLE001
            _logger.exception("Nudge: the pages of the website could not be read")

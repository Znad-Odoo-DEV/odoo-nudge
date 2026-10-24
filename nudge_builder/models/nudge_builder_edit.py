"""Editing a built site: the sections of one page, and its SEO, each change kept as a revision to undo.

The person always runs it as themselves, and a page is written only when that person is a website designer and
may write the page's view. The words and addresses a person gives are data: they go through the same
cleaning as the words of the AI writer (``tools.safe``) before they reach a section. What a section edit
changes is that section; the other sections of the page are not read back or rewritten by hand.
"""
import copy as copymod

from lxml import etree

from odoo import _, models
from odoo.exceptions import AccessError, UserError

from ..tools import compat, pictures, sections, snippets
from .nudge_builder_template import SNIPPETS
from ..tools.safe import safe_text, safe_url

OPS = ("add", "replace", "move_up", "move_down", "remove", "edit")
SEO_LIMITS = {"website_meta_title": 70, "website_meta_description": 170, "website_meta_keywords": 200}


class NudgeBuilderSiteEdit(models.Model):
    _inherit = "nudge.builder.site"

    # ------------------------------------------------------------------ what may be written
    def _editable_page(self, page):
        """Raise unless the person may write this page of this site"""
        self.ensure_one()
        self._check_designer()
        page.ensure_one()
        if page.site_id != self:
            raise UserError(_("That page does not belong to this site."))
        if not page.view_id or not page.page_id:
            raise UserError(_("The page %s no longer exists on the website.", page.name))
        try:
            compat.check_write(page.view_id)
            compat.check_write(page.page_id)
        except AccessError:
            raise AccessError(_("You may not edit the page %s.", page.name)) from None

    def _page_archs(self, page):
        """:return: ``{lang code: arch}`` of the page in the source language and every language of the site"""
        self.ensure_one()
        codes = ["en_US"] + [code for code in self._langs().mapped("code") if code != "en_US"]
        return {code: page.view_id.with_context(lang=code).arch_db for code in codes}

    def _image_url(self, image, preview=False):
        """:return: the address of the picture asked for: one of the bundled pictures by its name, or a plain
        link; the bundled picture is stored only when the change is made, never to show a card"""
        self.ensure_one()
        image = (image or "").strip()
        if not image:
            return None
        if image in pictures.PLACEHOLDERS:
            if preview:
                return "/nudge_builder/placeholder/%s" % image
            return f"/web/image/{self._picture_for(None, image).id}"
        url = safe_url(image)
        if not url or url.startswith(("mailto:", "tel:", "#")):
            raise UserError(_("A picture must be one of the bundled ones (%s) or a /path or https:// address.",
                                       ", ".join(sorted(pictures.PLACEHOLDERS))))
        return url

    # ------------------------------------------------------------------ sections
    def _section_edit(self, page, op, position=None, snippet=None, content=None, image=None, preview=False):
        """Check a section edit and prepare it.

        :param int position: 1 for the first section; the place to insert at for ``add`` (the end by default)
        :param snippet: a snippet of the library, for ``add`` and ``replace``
        :param dict content: ``{"title", "text", "button", "button_url", "items"}`` or ``{"en": {...}, "ar": {...}}``
        :return: ``transform(wrap, lang code)`` that edits the ``#wrap`` of one language's arch"""
        self.ensure_one()
        if op not in OPS:
            raise UserError(_("Unknown edit %s.", op))
        content = content if isinstance(content, dict) else {}
        _tree, wrap = compat.wrap_body(page.view_id.with_context(lang="en_US").arch_db)
        count = len(sections.children(wrap))
        if op == "add" and position is None:
            pass
        elif not isinstance(position, int) or isinstance(position, bool):
            raise UserError(_("Say which section: its number on the page, from 1."))
        elif not (1 <= position <= (count + 1 if op == "add" else count)):
            raise UserError(_("The page %(page)s has %(count)s sections; there is no number %(position)s.",
                                       page=page.name, count=count, position=position))
        if op == "move_up" and position == 1:
            raise UserError(_("That section is already the first."))
        if op == "move_down" and position == count:
            raise UserError(_("That section is already the last."))
        if op == "remove" and count == 1:
            raise UserError(_("A page keeps at least one section."))
        if op in ("edit", "add", "replace") and not (content or image):
            raise UserError(_("Say what the new texts or picture are."))
        picture = self._image_url(image, preview)
        new = None
        if op in ("add", "replace"):
            if snippet not in SNIPPETS:
                raise UserError(_("%(snippet)s is not one of the sections the builder knows: %(known)s.",
                                           snippet=snippet, known=", ".join(sorted(SNIPPETS))))
            try:
                new = self._sections(snippet, content, picture)
            except (ValueError, KeyError):
                raise UserError(_("This version of Odoo has no section %s.", snippet)) from None
        source = self._source_lang().code
        langs = {lang.code: lang for lang in self.env["res.lang"].search([("code", "in", self._langs().mapped("code"))])}
        facts = self._company_contact()

        def transform(wrap, code):
            kids = sections.children(wrap)
            index = (position or 0) - 1
            if op in ("add", "replace"):
                section = copymod.deepcopy(new.get(code) if new.get(code) is not None else new[source])
            if op == "add":
                if position is None or index >= len(kids):
                    wrap.append(section)
                else:
                    kids[index].addprevious(section)
            elif op == "replace":
                wrap.replace(kids[index], section)
            elif op == "move_up":
                kids[index - 1].addprevious(kids[index])
            elif op == "move_down":
                kids[index + 1].addnext(kids[index])
            elif op == "remove":
                wrap.remove(kids[index])
            else:
                target = kids[index]
                lang = langs.get(code) or next(iter(langs.values()), None)
                words = self._content_for(content, lang) if lang else content
                words = dict(words or {})
                # the snippet filler works on an HTML element: the section goes out and comes back as markup
                section = compat.parse_section(compat.to_string(target))
                button = snippets.first(section, snippets.BUTTON)
                if button is not None and not words.get("button_url") and button.get("href"):
                    words["button_url"] = button.get("href")  # an edit of the words keeps where the button goes
                snippets.fill(section, sections.snippet_of(target), words, picture, facts)
                wrap.replace(target, etree.fromstring(compat.to_string(section)))
        return transform

    def _section_preview(self, page, op, position=None, snippet=None, content=None, image=None):
        """What a card shows for a section edit, from the page as it is; nothing is written.

        :return: ``{"before", "after", "hash", "summary"}``, the sections as plain text"""
        self.ensure_one()
        transform = self._section_edit(page, op, position, snippet, content, image, preview=True)
        source = page.view_id.with_context(lang="en_US").arch_db
        tree, wrap = compat.wrap_body(source)
        old = sections.children(wrap)
        before_keys = sections.keys_of(wrap)
        index = (position or 0) - 1
        old_text = sections.describe(old[index]) if 0 <= index < len(old) else ""
        transform(wrap, "en_US")
        new = sections.children(wrap)
        after_keys = sections.keys_of(wrap)
        if op == "add":
            at = len(new) - 1 if position is None else index
            before, after = "(nothing: a new section is added)", sections.describe(new[at])
        elif op in ("replace", "edit"):
            before, after = old_text, sections.describe(new[index])
        elif op == "remove":
            before, after = old_text, "(the section is removed)"
        else:
            before, after = sections.order_text(before_keys), sections.order_text(after_keys)
        return {"before": before, "after": after, "hash": sections.digest(source),
                "summary": self._section_summary(page, op, position, snippet, before_keys)}

    def _section_summary(self, page, op, position, snippet, keys):
        name = keys[position - 1] if isinstance(position, int) and 0 < position <= len(keys) else ""
        if op == "add":
            return _("Add %(snippet)s to the page %(page)s", snippet=snippet, page=page.name)
        if op == "replace":
            return _("Replace section %(n)s (%(name)s) of %(page)s with %(snippet)s",
                              n=position, name=name, snippet=snippet, page=page.name)
        if op == "edit":
            return _("Change the texts or picture of section %(n)s (%(name)s) of %(page)s",
                              n=position, name=name, page=page.name)
        if op == "remove":
            return _("Remove section %(n)s (%(name)s) from %(page)s", n=position, name=name, page=page.name)
        return _("Move section %(n)s (%(name)s) of %(page)s %(way)s", n=position, name=name, page=page.name,
                          way=_("up") if op == "move_up" else _("down"))

    def _section_apply(self, page, op, position=None, snippet=None, content=None, image=None, expect_hash=None):
        """Make a section edit as the person, and keep a revision that undoes it.

        :param expect_hash: the fingerprint of the page the person was shown; the edit is refused when it moved
        :return: the ``nudge.builder.revision``"""
        self.ensure_one()
        self._editable_page(page)
        view = page.view_id
        current = view.with_context(lang="en_US").arch_db
        if expect_hash and sections.digest(current) != expect_hash:
            raise UserError(_("The page %s changed since you were shown this card; ask again.", page.name))
        _tree, wrap = compat.wrap_body(current)
        summary = self._section_summary(page, op, position, snippet, sections.keys_of(wrap))
        transform = self._section_edit(page, op, position, snippet, content, image)
        before = self._page_archs(page)
        after = {}
        for code, arch in before.items():
            tree, wrap = compat.wrap_body(arch)
            transform(wrap, code)
            after[code] = compat.to_string_tree(tree)
        skipped = compat.write_arch(view, after, "en_US")
        written = view.with_context(lang="en_US").arch_db
        _tree, wrap = compat.wrap_body(written)
        page.snippet_keys = ",".join(sections.keys_of(wrap))
        if skipped:
            self._say(_("The translation of %s was left as it was: its structure differs.", ", ".join(skipped)))
        return self.env["nudge.builder.revision"].create({
            "site_id": self.id, "kind": "section", "page_id": page.id, "name": summary,
            "before": {"archs": before}, "after": {"hash": sections.digest(written)},
        })

    # ------------------------------------------------------------------ SEO
    def _seo_label(self, name):
        return {
            "website_meta_title": _("SEO title"), "website_meta_description": _("SEO description"),
            "website_meta_keywords": _("SEO keywords"),
        }[name]

    def _seo_values(self, changes):
        """:return: the cleaned SEO texts, or a UserError naming what is too long"""
        self.ensure_one()
        values = {}
        for name, text in changes.items():
            if name not in SEO_LIMITS or text is None:
                continue
            text = " ".join(safe_text(text).split())
            if not text:
                raise UserError(_("The %s cannot be empty.", self._seo_label(name)))
            if len(text) > SEO_LIMITS[name]:
                raise UserError(_("The %(what)s is up to %(max)s characters; this one has %(n)s.",
                                           what=self._seo_label(name), max=SEO_LIMITS[name], n=len(text)))
            values[name] = text
        return values

    def _seo_lang(self, lang):
        """:return: the code of the site's language for a 2 letter code, the source language by default"""
        self.ensure_one()
        if not lang:
            return self._source_lang().code
        for code in self._langs().mapped("code"):
            if compat.lang_code(code) == lang.lower():
                return code
        raise UserError(_("This site has no language %s.", lang))

    def _seo_preview(self, page, changes, lang=None):
        """:return: ``{"lines": [{label, value}], "hash"}`` for a card; nothing is written"""
        self.ensure_one()
        values = self._seo_values(changes)
        if not values:
            raise UserError(_("Say the new SEO title, description or keywords."))
        code = self._seo_lang(lang)
        view = page.view_id
        values = {name: text for name, text in values.items() if name in view._fields}
        if not values:
            raise UserError(_("This version of Odoo has no such SEO field."))
        current = self._seo_read(view, list(values))
        lines = []
        for name, text in values.items():
            lines.append({"label": _("%s: before", self._seo_label(name)),
                          "value": current[name].get(code) or _("(empty)")})
            lines.append({"label": _("%s: after", self._seo_label(name)), "value": text})
        return {"lines": lines, "hash": sections.digest(repr(sorted(current.items())))}

    def _seo_apply(self, page, changes, lang=None, expect_hash=None):
        """Change the SEO of a page as the person; the revision keeps what it was.

        :return: the ``nudge.builder.revision``"""
        self.ensure_one()
        self._editable_page(page)
        values = {name: text for name, text in self._seo_values(changes).items() if name in page.view_id._fields}
        if not values:
            raise UserError(_("Say the new SEO title, description or keywords."))
        code = self._seo_lang(lang)
        view = page.view_id
        before = self._seo_read(view, list(values))
        if expect_hash and sections.digest(repr(sorted(before.items()))) != expect_hash:
            raise UserError(_("The SEO of %s changed since you were shown this card; ask again.", page.name))
        write = {}
        for name, text in values.items():
            write[name] = {code: text}
            if not before[name].get("en_US"):
                write[name]["en_US"] = text  # the source language holds the text a translation falls back to
        self._seo_write(view, write)
        after = self._seo_read(view, list(values))
        return self.env["nudge.builder.revision"].create({
            "site_id": self.id, "kind": "seo", "page_id": page.id, "before": before, "after": after,
            "name": _("Change the %(what)s of the page %(page)s",
                               what=", ".join(self._seo_label(name) for name in values), page=page.name),
        })

"""Editing a built site: the sections of one page, and its SEO, each change kept as a revision to undo.

The person always runs it as themselves, and a page is written only when that person is a website designer and
may write the page's view. The words and addresses a person gives are data: they go through the same
cleaning as the words of the AI writer (``tools.safe``) before they reach a section. What a section edit
changes is that section; the other sections of the page are not read back or rewritten by hand.
"""
import copy as copymod
import hashlib
import re

from lxml import etree

from odoo import fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import html_escape

from ..tools import checks, compat, pictures, sections, snippets
from .nudge_builder_template import SNIPPETS
from ..tools.safe import safe_text, safe_url

OPS = ("add", "replace", "move_up", "move_down", "remove", "edit")
ARABIC_LETTERS = re.compile(r"[؀-ۿ]")
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
            raise UserError(self.env._("That page does not belong to this site."))
        if not page.view_id or not page.page_id:
            raise UserError(self.env._("The page %s no longer exists on the website.", page.name_shown))
        try:
            compat.check_write(page.view_id)
            compat.check_write(page.page_id)
        except AccessError:
            raise AccessError(self.env._("You may not edit the page %s.", page.name_shown)) from None

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
            raise UserError(self.env._("A picture must be one of the bundled ones (%s) or a /path or https:// address.",
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
            raise UserError(self.env._("Unknown edit %s.", op))
        content = content if isinstance(content, dict) else {}
        _tree, wrap = compat.wrap_body(page.view_id.with_context(lang="en_US").arch_db)
        count = len(sections.children(wrap))
        if op == "add" and position is None:
            pass
        elif not isinstance(position, int) or isinstance(position, bool):
            raise UserError(self.env._("Say which section: its number on the page, from 1."))
        elif not (1 <= position <= (count + 1 if op == "add" else count)):
            raise UserError(self.env._("The page %(page)s has %(count)s sections; there is no number %(position)s.",
                                       page=page.name_shown, count=count, position=position))
        if op == "move_up" and position == 1:
            raise UserError(self.env._("That section is already the first."))
        if op == "move_down" and position == count:
            raise UserError(self.env._("That section is already the last."))
        if op == "remove" and count == 1:
            raise UserError(self.env._("A page keeps at least one section."))
        if op in ("edit", "add", "replace") and not (content or image):
            raise UserError(self.env._("Say what the new texts or picture are."))
        picture = self._image_url(image, preview)
        new = None
        if op in ("add", "replace"):
            if snippet not in SNIPPETS:
                raise UserError(self.env._("%(snippet)s is not one of the sections the builder knows: %(known)s.",
                                           snippet=snippet, known=", ".join(sorted(SNIPPETS))))
            try:
                new = self._sections(snippet, content, picture)
            except (ValueError, KeyError):
                raise UserError(self.env._("This version of Odoo has no section %s.", snippet)) from None
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
                snippets.fill(section, sections.snippet_of(target), words, picture, facts, fresh=False)
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
            return self.env._("Add %(snippet)s to the page %(page)s", snippet=snippet, page=page.name_shown)
        if op == "replace":
            return self.env._("Replace section %(n)s (%(name)s) of %(page)s with %(snippet)s",
                              n=position, name=name, snippet=snippet, page=page.name_shown)
        if op == "edit":
            return self.env._("Change the texts or picture of section %(n)s (%(name)s) of %(page)s",
                              n=position, name=name, page=page.name_shown)
        if op == "remove":
            return self.env._("Remove section %(n)s (%(name)s) from %(page)s", n=position, name=name, page=page.name_shown)
        return self.env._("Move section %(n)s (%(name)s) of %(page)s %(way)s", n=position, name=name, page=page.name_shown,
                          way=self.env._("up") if op == "move_up" else self.env._("down"))

    def _section_apply(self, page, op, position=None, snippet=None, content=None, image=None, expect_hash=None):
        """Make a section edit as the person, and keep a revision that undoes it.

        :param expect_hash: the fingerprint of the page the person was shown; the edit is refused when it moved
        :return: the ``nudge.builder.revision``"""
        self.ensure_one()
        self._editable_page(page)
        view = page.view_id
        current = view.with_context(lang="en_US").arch_db
        if expect_hash and sections.digest(current) != expect_hash:
            raise UserError(self.env._("The page %s changed since you were shown this card; ask again.", page.name_shown))
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
            self._say(self.env._("The translation of %s was left as it was: its structure differs.", ", ".join(skipped)))
        return self.env["nudge.builder.revision"].create({
            "site_id": self.id, "kind": "section", "page_id": page.id, "name": summary,
            "before": {"archs": before}, "after": {"hash": sections.digest(written)},
        })

    # ------------------------------------------------------------------ SEO
    def _seo_label(self, name):
        return {
            "website_meta_title": self.env._("SEO title"), "website_meta_description": self.env._("SEO description"),
            "website_meta_keywords": self.env._("SEO keywords"),
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
                raise UserError(self.env._("The %s cannot be empty.", self._seo_label(name)))
            if len(text) > SEO_LIMITS[name]:
                raise UserError(self.env._("The %(what)s is up to %(max)s characters; this one has %(n)s.",
                                           what=self._seo_label(name), max=SEO_LIMITS[name], n=len(text)))
            values[name] = text
        return values

    def _seo_lang(self, lang, texts=None):
        """:return: the code of the site's language for a 2 letter code. Without one: the language the texts are
        written in (Arabic letters mean the site's Arabic), else the language the person is viewing when the site
        has it and it is not Arabic, else the source language"""
        self.ensure_one()
        if not lang:
            codes = self._langs().mapped("code")
            if any(ARABIC_LETTERS.search(text or "") for text in (texts or [])):
                arabic = [code for code in codes if compat.lang_code(code) == "ar"]
                if arabic:
                    return arabic[0]
            viewing = self.env.lang
            if texts and viewing in codes and compat.lang_code(viewing) != "ar":
                return viewing
            return self._source_lang().code
        for code in self._langs().mapped("code"):
            if compat.lang_code(code) == lang.lower():
                return code
        raise UserError(self.env._("This site has no language %s.", lang))

    def _seo_preview(self, page, changes, lang=None):
        """:return: ``{"lines": [{label, value}], "hash"}`` for a card; nothing is written"""
        self.ensure_one()
        values = self._seo_values(changes)
        if not values:
            raise UserError(self.env._("Say the new SEO title, description or keywords."))
        view = page.view_id
        values = {name: text for name, text in values.items() if name in view._fields}
        if not values:
            raise UserError(self.env._("This version of Odoo has no such SEO field."))
        code = self._seo_lang(lang, list(values.values()))
        current = self._seo_read(view, list(values))
        lines = []
        for name, text in values.items():
            lines.append({"label": self.env._("%s: before", self._seo_label(name)),
                          "value": current[name].get(code) or self.env._("(empty)")})
            lines.append({"label": self.env._("%s: after", self._seo_label(name)), "value": text})
        return {"lines": lines, "hash": sections.digest(repr(sorted(current.items())))}

    def _seo_apply(self, page, changes, lang=None, expect_hash=None):
        """Change the SEO of a page as the person; the revision keeps what it was.

        :return: the ``nudge.builder.revision``"""
        self.ensure_one()
        self._editable_page(page)
        values = {name: text for name, text in self._seo_values(changes).items() if name in page.view_id._fields}
        if not values:
            raise UserError(self.env._("Say the new SEO title, description or keywords."))
        code = self._seo_lang(lang, list(values.values()))
        view = page.view_id
        before = self._seo_read(view, list(values))
        if expect_hash and sections.digest(repr(sorted(before.items()))) != expect_hash:
            raise UserError(self.env._("The SEO of %s changed since you were shown this card; ask again.", page.name_shown))
        write = {}
        for name, text in values.items():
            write[name] = {code: text}
            if not before[name].get("en_US"):
                write[name]["en_US"] = text  # the source language holds the text a translation falls back to
        self._seo_write(view, write)
        after = self._seo_read(view, list(values))
        return self.env["nudge.builder.revision"].create({
            "site_id": self.id, "kind": "seo", "page_id": page.id, "before": before, "after": after,
            "name": self.env._("Change the %(what)s of the page %(page)s",
                               what=", ".join(self._seo_label(name) for name in values), page=page.name_shown),
        })


# ----------------------------------------------------------------------
# Website Builder 2.0: the records of a site and its contact details, changed from the chat
# ----------------------------------------------------------------------
ITEM_KINDS = ("project", "product", "service", "member", "testimonial", "faq", "location")
# the pages that show each kind of record (page key or kind)
KIND_PAGES = {
    "project": ("projects", "portfolio", "work", "listings", "home"),
    "product": ("shop", "products", "menu", "catalog", "catalogue", "home"),
    "service": ("services", "programmes", "home"),
    "member": ("team", "about", "doctors"),
    "testimonial": ("home", "testimonials", "about"),
    "faq": ("faq", "faqs"),
    "location": ("contact", "contact_us", "contactus", "locations", "home"),
}
# never written from the chat: links, owners and pictures have their own tools
PROTECTED_FIELDS = frozenset({
    "id", "site_id", "company_id", "picture_ids", "before_picture_id", "after_picture_id", "document_id",
    "product_tmpl_id", "currency_id", "state_id", "create_uid", "write_uid", "create_date", "write_date",
    "display_name", "maps_embed_src"})
URL_FIELDS = frozenset({"maps_url", "linkedin", "booking_url", "url"})
SIMPLE_TYPES = ("char", "text", "html", "integer", "float", "boolean", "selection")
TEXT_LIMIT = 2000
PHONE_CHARS = re.compile(r"^[+\d\s().-]{5,30}$")
EMAIL = re.compile(r"^[^@\s<>\"']+@[^@\s<>\"']+\.[^@\s<>\"']+$")
CONTACT_FIELDS = ("whatsapp", "whatsapp_text", "phone", "email", "hours", "booking_url")
CONTACT_LIMITS = {"whatsapp_text": 300, "hours": 600}


def _item_fingerprint(record):
    """:return: a short hash of what a person can type in a record: a change inside the same second is seen too"""
    values = {name: str(record[name]) for name, field in record._fields.items()
              if field.type in ("char", "text", "html", "integer", "float", "boolean", "selection")
              and not field.compute and not field.related and name not in ("id", "create_date", "write_date")}
    return hashlib.sha1(repr(sorted(values.items())).encode()).hexdigest()


class NudgeBuilderRevisionContent(models.Model):
    """Two more kinds of change that can be undone: a record added to the site and a change of its contact details"""
    _inherit = "nudge.builder.revision"

    kind = fields.Selection(selection_add=[("item", "Content record"), ("contact", "Contact details")],
                            ondelete={"item": "cascade", "contact": "cascade"})

    def _undo_item(self):
        after = self.after or {}
        model = after.get("model")
        record = self.env[model].browse(after.get("id")).exists() if model in self.env else None
        if not record:
            return [self.env._("The record was already removed.")]
        if (str(record.write_date) != after.get("write_date")
                or (after.get("fingerprint") and _item_fingerprint(record) != after["fingerprint"])):
            raise UserError(self.env._(
                "The record %s was changed after it was added; undo is refused so that nothing newer is lost.",
                record.display_name))
        record.unlink()

    def _undo_contact(self):
        site = self.site_id
        if site._contact_read(list(self.after or {})) != self.after:
            raise UserError(self.env._(
                "The contact details were changed after this edit; undo is refused so that nothing newer is lost."))
        site._contact_write(self.before or {})


class NudgeBuilderSiteContent(models.Model):
    """Adding records and setting the contact details of a site, with a before/after card and an undo"""
    _inherit = "nudge.builder.site"

    # ------------------------------------------------------------------ one record
    def _item_model(self, kind):
        self.ensure_one()
        if kind not in ITEM_KINDS:
            raise UserError(self.env._("Unknown kind %(kind)s; it is one of: %(known)s.",
                                       kind=kind, known=", ".join(ITEM_KINDS)))
        name = "nudge.builder.%s" % kind
        if name not in self.env:
            raise UserError(self.env._("This site cannot hold %s records.", kind))
        return self.env[name]

    def _item_fields(self, Model):
        """:return: the names of the fields a person may give for a record of ``Model``"""
        return sorted(name for name, field in Model._fields.items()
                      if name not in PROTECTED_FIELDS and not field.readonly and not field.compute
                      and not getattr(field, "automatic", False) and field.type in SIMPLE_TYPES)

    @staticmethod
    def _item_aliased(Model, fields_in):
        """:return: ``fields_in`` with the other names of a field (question, answer of a faq) put to the real ones"""
        aliases = {"question": "name", "answer": "description"} if Model._name == "nudge.builder.faq" else {}
        return {aliases.get(name, name): value for name, value in (fields_in or {}).items()}

    def _item_clean(self, Model, name, value):
        """:return: ``value`` ready to be written in the field ``name`` of ``Model``"""
        field = Model._fields[name]
        label = field.string or name
        if field.type == "boolean":
            return bool(value)
        if field.type in ("integer", "float", "monetary"):
            try:
                return int(value) if field.type == "integer" else float(value)
            except (TypeError, ValueError):
                raise UserError(self.env._("%s must be a number.", label)) from None
        if field.type == "selection":
            choices = dict(field.selection) if isinstance(field.selection, list) else {}
            if choices and value not in choices:
                raise UserError(self.env._("%(label)s is one of: %(choices)s.", label=label, choices=", ".join(choices)))
            return value
        if field.type not in ("char", "text", "html"):
            raise UserError(self.env._("%s cannot be set from the chat.", label))
        if name in URL_FIELDS:
            url = safe_url(value)
            if not url or not url.startswith("https://"):
                raise UserError(self.env._("%s must be an https:// address.", label))
            return url
        text = safe_text(value).strip() if field.type == "text" else " ".join(safe_text(value).split())
        text = text[:TEXT_LIMIT]
        if name == "whatsapp":
            return self._whatsapp(text)
        if name == "phone" and text and not PHONE_CHARS.match(text):
            raise UserError(self.env._("%s is not a phone number.", label))
        if name == "email" and text and not EMAIL.match(text):
            raise UserError(self.env._("%s is not an e-mail address.", label))
        if field.type == "html":
            return "<p>%s</p>" % html_escape(text) if text else False
        return text

    def _whatsapp(self, text):
        """:return: the WhatsApp number in the international form (+ and digits), or a UserError"""
        number = re.sub(r"[\s().-]", "", text or "")
        if number.startswith("00"):
            number = "+" + number[2:]
        if number and not number.startswith("+"):
            number = "+" + number
        if number and not checks.valid_whatsapp(number):
            raise UserError(self.env._("The WhatsApp number must be in the international form: + and 8 to 15 digits."))
        return number

    def _item_values(self, kind, fields_in):
        """:return: ``{field: cleaned value}`` of the fields the person gave for a new record of ``kind``"""
        Model = self._item_model(kind)
        allowed = self._item_fields(Model)
        values = {}
        for name, value in self._item_aliased(Model, fields_in).items():
            if name not in allowed:
                raise UserError(self.env._("%(name)s is not a field of a %(kind)s. Fields: %(allowed)s.",
                                           name=name, kind=kind, allowed=", ".join(allowed)))
            if value is None or value == "":
                continue
            values[name] = self._item_clean(Model, name, value)
        if not values:
            raise UserError(self.env._("Say the fields of the new %s.", kind))
        for name, field in Model._fields.items():
            if (field.required and name not in values and name not in PROTECTED_FIELDS and not field.automatic
                    and name not in Model.default_get([name])):
                raise UserError(self.env._("Say the %(field)s of the new %(kind)s.",
                                           field=field.string or name, kind=kind))
        return values

    def _item_translations(self, kind, translations):
        """:return: ``{lang code: {field: value}}`` for the other languages of the site"""
        Model = self._item_model(kind)
        allowed = self._item_fields(Model)
        out = {}
        for lang, fields_in in (translations or {}).items():
            code = self._seo_lang(lang)
            values = {name: self._item_clean(Model, name, value) for name, value in self._item_aliased(Model, fields_in).items()
                      if name in allowed and Model._fields[name].translate and value}
            if values:
                out[code] = values
        return out

    def _item_preview(self, kind, fields_in, translations=None):
        """What the card of an added record shows; nothing is written.

        :return: ``{"lines", "summary", "values", "translations"}``"""
        self.ensure_one()
        self._check_designer()
        values = self._item_values(kind, fields_in)
        other = self._item_translations(kind, translations)
        Model = self._item_model(kind)
        count = Model.search_count([("site_id", "=", self.id)])
        lines = [{"label": self.env._("Before"),
                  "value": self.env._("%(count)s %(kind)s records", count=count, kind=kind)}]
        for name, value in values.items():
            shown = (self.env._("Yes") if value else self.env._("No")) if isinstance(value, bool) else str(value)[:300]
            lines.append({"label": Model._fields[name].string or name, "value": shown})
        for code, per in other.items():
            lines.append({"label": code, "value": "; ".join("%s: %s" % (key, str(text)[:100])
                                                            for key, text in per.items())})
        return {"lines": lines, "values": values, "translations": other,
                "summary": self.env._("Add a %(kind)s to the site %(site)s", kind=kind, site=self.name)}

    def _item_add(self, kind, values, translations=None):
        """Add the record as the person and keep a revision that removes it. The pages that show this kind are
        drawn again when the builder knows how.

        :return: ``(revision, record, ids of the pages drawn again)``"""
        self.ensure_one()
        self._check_designer()
        Model = self._item_model(kind)
        values = self._item_values(kind, values)  # cleaned again: the card's values came through a stored step
        record = Model.create(dict(values, site_id=self.id))
        if translations:
            record.flush_recordset()  # a translated html field reads the stored terms: flush first
        for code, per in (translations or {}).items():
            record.with_context(lang=code).write(per)
        revision = self.env["nudge.builder.revision"].create({
            "site_id": self.id, "kind": "item",
            "name": self.env._("Add a %(kind)s: %(name)s", kind=kind, name=record.display_name),
            "before": {"model": Model._name, "id": False},
            "after": {"model": Model._name, "id": record.id, "write_date": str(record.write_date),
                      "fingerprint": _item_fingerprint(record)},
        })
        return revision, record, self._rerender_kind(kind).get("pages", [])

    def _page_shows(self, page, kind):
        words = KIND_PAGES.get(kind)
        if words is None:
            return True  # a plugin's kind: the drawing itself finds the pages that show it
        return (page.key or "").lower() in words or (page.kind or "").lower() in words

    def _rerender_kind(self, kind):
        """Draw again the sections of the pages that show ``kind`` from the records. The drawing itself is the
        hook ``_render_kind(page, kind)`` of the site (written with the sections that read records); without it
        the records are saved and the sections show them at the next build.

        :return: ``{"pages": [ids of the pages drawn again]}``"""
        self.ensure_one()
        hook = getattr(self, "_render_kind", None)
        done = []
        if hook:
            for page in self.page_ids.filtered(lambda one: self._page_shows(one, kind)):
                if page.view_id and hook(page, kind):
                    done.append(page.id)
        return {"pages": done}

    # ------------------------------------------------------------------ contact details
    def _contact_read(self, names):
        """:return: ``{name: value}`` of contact fields; ``social`` is a sorted list of ``[network, url]``"""
        self.ensure_one()
        out = {}
        for name in names:
            if name == "social":
                if "social_ids" in self._fields:
                    out[name] = sorted([line.network, line.url] for line in self.social_ids)
            elif name in self._fields:
                out[name] = self[name] or False
        return out

    def _contact_write(self, values):
        self.ensure_one()
        plain = {name: value for name, value in values.items() if name != "social" and name in self._fields}
        if plain:
            self.write(plain)
        if "social" in values and "social_ids" in self._fields:
            self.social_ids.unlink()
            self.write({"social_ids": [(0, 0, {"network": network, "url": url}) for network, url in values["social"]]})

    def _contact_values(self, arguments):
        """:return: the cleaned contact changes ``{name: value}`` (``social`` as the full new list)"""
        self.ensure_one()
        values = {}
        for name in CONTACT_FIELDS:
            if arguments.get(name) is None or name not in self._fields:
                continue
            text = arguments[name]
            if name == "whatsapp":
                values[name] = self._whatsapp(safe_text(text).strip()) or False
            elif name == "booking_url":
                url = safe_url(text) if text else ""
                if text and not url.startswith("https://"):
                    raise UserError(self.env._("The booking address must be an https:// address."))
                values[name] = url or False
            else:
                clean = safe_text(text).strip() if name == "hours" else " ".join(safe_text(text).split())
                clean = clean[:CONTACT_LIMITS.get(name, 200)]
                if name == "phone" and clean and not PHONE_CHARS.match(clean):
                    raise UserError(self.env._("That is not a phone number."))
                if name == "email" and clean and not EMAIL.match(clean):
                    raise UserError(self.env._("That is not an e-mail address."))
                values[name] = clean or False
        social = arguments.get("social")
        if social and "social_ids" in self._fields:
            if isinstance(social, dict):
                social = [{"network": key, "url": url} for key, url in social.items()]
            Social = self.env[self._fields["social_ids"].comodel_name]
            networks = {}
            if "network" in Social._fields and isinstance(Social._fields["network"].selection, list):
                networks = dict(Social._fields["network"].selection)
            current = dict(self._contact_read(["social"])["social"])
            for item in social:
                network, url = str(item.get("network", "")).strip().lower(), safe_url(item.get("url"))
                if networks and network not in networks:
                    raise UserError(self.env._("The network is one of: %s.", ", ".join(networks)))
                if not url.startswith("https://"):
                    raise UserError(self.env._("The %s address must be an https:// address.", network))
                current[network] = url
            values["social"] = sorted([network, url] for network, url in current.items())
        if not values:
            raise UserError(self.env._("Say the contact details to set: whatsapp, phone, email, hours, social."))
        return values

    def _contact_preview(self, arguments):
        """:return: ``{"lines", "summary", "values", "hash"}`` for a card; nothing is written"""
        self.ensure_one()
        self._check_designer()
        values = self._contact_values(arguments)
        before = self._contact_read(list(values))
        empty = self.env._("(empty)")
        lines = []
        for name, value in values.items():
            lines.append({"label": self.env._("%s: before", name), "value": str(before.get(name) or empty)[:300]})
            lines.append({"label": self.env._("%s: after", name), "value": str(value or empty)[:300]})
        return {"lines": lines, "values": values, "hash": sections.digest(repr(sorted(before.items()))),
                "summary": self.env._("Change the contact details of the site %s", self.name)}

    def _contact_apply(self, values, expect_hash=None):
        """:return: the revision"""
        self.ensure_one()
        self._check_designer()
        args = dict(values)
        if "social" in args:
            args["social"] = [{"network": network, "url": url} for network, url in args["social"]]
        values = self._contact_values(args)
        before = self._contact_read(list(values))
        if expect_hash and sections.digest(repr(sorted(before.items()))) != expect_hash:
            raise UserError(self.env._("The contact details changed since you were shown this card; ask again."))
        self._contact_write(values)
        return self.env["nudge.builder.revision"].create({
            "site_id": self.id, "kind": "contact", "before": before, "after": self._contact_read(list(values)),
            "name": self.env._("Change the contact details: %s", ", ".join(values)),
        })

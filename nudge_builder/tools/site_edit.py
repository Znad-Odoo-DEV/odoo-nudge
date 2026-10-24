"""The chat tools that edit a site the builder made: read its pages, change the SEO of a page, add, replace, move,
remove or reword a section, and undo any of that or a publish.

Every change shows a card (what the section or the SEO is now, and what it becomes) and is made only after the
person's yes, as that person: a website designer who may write the page. Each change keeps a revision.
The page's words reach the model inside a ``<data>`` frame: they are content, never instructions.
"""
from odoo.exceptions import AccessError, UserError

from odoo.addons.nudge.tools import base, groups

from ..models.nudge_builder_template import SNIPPETS
from . import compat, sections

TOOLS = ("read_site_page", "edit_page_section", "change_page_seo", "undo_site_change")


def _extend_group():
    """The tools come with the words of the website group, next to the builder's own"""
    group = groups.GROUPS.get("website")
    if group and not set(TOOLS) <= set(group["tools"]):
        group["tools"] = tuple(group["tools"]) + tuple(name for name in TOOLS if name not in group["tools"])


_extend_group()


def _frame(text):
    return "<data source=\"site_pages\">%s</data>" % str(text).replace("</data>", "").replace("<data", "")


def find_site(env, arguments):
    """:return: the site asked for, else the newest built site of the website (the first website by default)"""
    Site = env["nudge.builder.site"]
    built = [("state", "in", ("built", "published"))]
    if arguments.get("site_id"):
        site = Site.browse(arguments["site_id"]).exists()
    else:
        domain = list(built)
        if arguments.get("website_id"):
            domain.append(("website_id", "=", arguments["website_id"]))
        site = Site.search(domain, limit=1)
        if not site and arguments.get("website_id"):
            # The model often passes the id of the site (read_site_page names it) as website_id
            site = Site.search(built + [("id", "=", arguments["website_id"])], limit=1)
    if not site:
        known = Site.search(built, limit=10)
        if known:
            raise UserError(env._(
                "No site built by the Website Builder matches. site_id is the id of the site and website_id the id "
                "of its website. Sites: %(sites)s.",
                sites="; ".join("site_id %s (%s, website_id %s)" % (one.id, one.name, one.website_id.id)
                                for one in known)))
        raise UserError(env._("There is no site built by the Website Builder to edit."))
    return site


def find_page(env, site, text):
    """:return: the ``nudge.builder.page`` of ``site`` named by ``text``: its key, address or name"""
    text = (text or "").strip().lower()
    if not text:
        raise UserError(env._("Say which page."))
    pages = site.page_ids
    bare = text.strip("/")
    for match in (
        lambda page: (page.key or "").lower() == text,
        lambda page: (page.url or "").strip("/").lower() == bare,
        lambda page: (page.name or "").lower() == text,
        lambda page: text in (page.name or "").lower() or text in (page.key or "").lower(),
    ):
        found = pages.filtered(match)
        if len(found) == 1:
            return found
        if len(found) > 1:
            raise UserError(env._("Several pages match %(text)s: %(names)s. Say which.",
                                  text=text, names=", ".join(found.mapped("url"))))
    raise UserError(env._("The site %(site)s has no page %(text)s. Its pages: %(names)s.",
                          site=site.name, text=text, names=", ".join(pages.mapped("name"))))


def _locate(env, arguments):
    site = find_site(env, arguments)
    site._check_designer()
    return site, find_page(env, site, arguments.get("page"))


def _refuse(error):
    return base.ToolResult(str(error), is_error=True)


@base.register
class ReadSitePage(base.Tool):
    name = "read_site_page"
    group = "nudge.group_nudge_agent"
    modes = (base.MODE_AGENT,)
    description = (
        "Read a site made by the Website Builder: without a page, its pages (address, sections in order, SEO "
        "title and description, published or not) and its latest changes; with a page, its sections numbered "
        "from 1 with their words and pictures. Call it before editing a section, to know its number. Changes nothing.")
    input_schema = {"type": "object", "properties": {
        "page": {"type": "string", "description": "The page: its name, address or key. Omit to list the pages."},
        "site_id": {"type": "integer"}, "website_id": {"type": "integer"}},
        "additionalProperties": False}

    def run(self, env, arguments, screen):
        try:
            site = find_site(env, arguments)
            site._check_designer()
            if not arguments.get("page"):
                return self._pages(env, site)
            page = find_page(env, site, arguments["page"])
            _tree, wrap = compat.wrap_body(page.view_id.with_context(lang="en_US").arch_db)
        except (AccessError, UserError) as error:
            return _refuse(error)
        lines = []
        for number, section in enumerate(sections.children(wrap), start=1):
            lines.append("Section %s (%s):\n%s" % (number, sections.snippet_of(section), sections.describe(section)))
        return base.ToolResult(
            _frame("Page %s (%s)\n%s" % (page.name, page.url, "\n".join(lines))),
            records=("website.page", [page.page_id.id], [page.name]))

    @staticmethod
    def _pages(env, site):
        lines = ["Site %s (id %s), website %s" % (site.name, site.id, site.website_id.name)]
        for page in site.page_ids:
            view = page.view_id
            title = view.website_meta_title if view and "website_meta_title" in view._fields else ""
            description = view.website_meta_description if view and "website_meta_description" in view._fields else ""
            lines.append("- %s %s | %s | SEO title: %s | SEO description: %s | %s" % (
                page.name, page.url, page.snippet_keys or "", title or "-", description or "-",
                "published" if page.is_published else "draft"))
        for revision in site.revision_ids[:10]:
            lines.append("Change %s (%s): %s" % (revision.id, revision.state, revision.name))
        return base.ToolResult(_frame("\n".join(lines)))


def _card(site, page, lines):
    return [{"label": site.env._("Page"), "value": "%s (%s)" % (page.name, page.url)}] + lines


@base.register
class EditPageSection(base.Action):
    name = "edit_page_section"
    kind = "write"
    group = "nudge.group_nudge_agent"
    description = (
        "Edit one section of a page of a site made by the Website Builder, after the person has read the "
        "before and after on a card: op add (a new section from the builder's snippets, at a position or the "
        "end), replace (swap section N for another snippet), edit (change the texts, button or picture of "
        "section N, keeping its design), move_up, move_down, remove. The other sections are not touched. "
        "Read the page first (read_site_page) to know the numbers. Texts go in content as title, text, button, "
        "button_url, items (title and text each); give one object per language code of the site when it has "
        "several. Never deletes anything but the section, never publishes; every change can be undone.")
    input_schema = {"type": "object", "properties": {
        "page": {"type": "string", "description": "The page: its name, address or key."},
        "op": {"type": "string", "enum": ["add", "replace", "edit", "move_up", "move_down", "remove"]},
        "position": {"type": "integer", "minimum": 1,
                     "description": "The section's number from 1; for add, where the new one goes (the end if omitted)."},
        "snippet": {"type": "string", "enum": sorted(SNIPPETS), "description": "For add and replace."},
        "content": {"type": "object", "description": (
            "The texts: {title, text, button, button_url, items:[{title, text}]} or {en: {...}, ar: {...}}.")},
        "image": {"type": "string", "description": "A bundled picture name (building, office, team, product...) "
                                                   "or a /path or https:// address."},
        "site_id": {"type": "integer"}, "website_id": {"type": "integer"}},
        "required": ["page", "op"], "additionalProperties": False}

    def run(self, env, arguments, screen):
        try:
            site, page = _locate(env, arguments)
            site._editable_page(page)
            preview = site._section_preview(
                page, arguments["op"], arguments.get("position"), arguments.get("snippet"),
                arguments.get("content"), arguments.get("image"))
        except (AccessError, UserError) as error:
            return _refuse(error)
        lines = _card(site, page, [
            {"label": env._("Before"), "value": preview["before"]},
            {"label": env._("After"), "value": preview["after"]}])
        if arguments.get("image"):
            lines.append({"label": env._("Picture"), "value": str(arguments["image"])[:200]})
        return base.ToolResult(
            "The person is being shown the change.",
            pending={"summary": preview["summary"], "res_model": "website.page", "res_ids": [],
                     "record_names": page.name[:500], "is_sensitive": False,
                     "values": {"site_id": site.id, "page_id": page.id, "op": arguments["op"],
                                "position": arguments.get("position"), "snippet": arguments.get("snippet"),
                                "content": arguments.get("content") or {}, "image": arguments.get("image"),
                                "hash": preview["hash"]},
                     "preview": {"records": [], "lines": lines}})

    def execute(self, env, step):
        values = step.values or {}
        site = env["nudge.builder.site"].browse(values.get("site_id")).exists()
        page = env["nudge.builder.page"].browse(values.get("page_id")).exists()
        if not site or not page:
            return base.ToolResult("The site or the page was deleted since.", is_error=True)
        try:
            revision = site._section_apply(
                page, values["op"], values.get("position"), values.get("snippet"), values.get("content"),
                values.get("image"), expect_hash=values.get("hash"))
        except (AccessError, UserError) as error:
            return _refuse(error)
        return base.ToolResult(
            "Done: %s. Change %s keeps the page as it was; undo_site_change puts it back." % (revision.name, revision.id),
            records=("website.page", [page.page_id.id], [page.name]),
            card_lines=[{"label": env._("Changed"), "value": revision.name}])


@base.register
class ChangePageSeo(base.Action):
    name = "change_page_seo"
    kind = "write"
    group = "nudge.group_nudge_agent"
    description = (
        "Change the SEO title, meta description or keywords of a page of a site made by the Website Builder "
        "(\"change the SEO title of the About page to ...\"), after the person has read the before and after on a "
        "card. The text is written for one language of the site (its main one by default). Title up to 70 "
        "characters, description up to 170. It can be undone.")
    input_schema = {"type": "object", "properties": {
        "page": {"type": "string", "description": "The page: its name, address or key."},
        "title": {"type": "string", "maxLength": 200}, "description": {"type": "string", "maxLength": 400},
        "keywords": {"type": "string", "maxLength": 300},
        "lang": {"type": "string", "description": "2 letter code of the language the text is in; the site's main by default."},
        "site_id": {"type": "integer"}, "website_id": {"type": "integer"}},
        "required": ["page"], "additionalProperties": False}

    @staticmethod
    def _changes(arguments):
        return {"website_meta_title": arguments.get("title"), "website_meta_description": arguments.get("description"),
                "website_meta_keywords": arguments.get("keywords")}

    def run(self, env, arguments, screen):
        try:
            site, page = _locate(env, arguments)
            site._editable_page(page)
            preview = site._seo_preview(page, self._changes(arguments), arguments.get("lang"))
        except (AccessError, UserError) as error:
            return _refuse(error)
        return base.ToolResult(
            "The person is being shown the change.",
            pending={"summary": env._("Change the SEO of the page %s", page.name), "res_model": "website.page",
                     "res_ids": [], "record_names": page.name[:500], "is_sensitive": False,
                     "values": {"site_id": site.id, "page_id": page.id, "changes": self._changes(arguments),
                                "lang": arguments.get("lang"), "hash": preview["hash"]},
                     "preview": {"records": [], "lines": _card(site, page, preview["lines"])}})

    def execute(self, env, step):
        values = step.values or {}
        site = env["nudge.builder.site"].browse(values.get("site_id")).exists()
        page = env["nudge.builder.page"].browse(values.get("page_id")).exists()
        if not site or not page:
            return base.ToolResult("The site or the page was deleted since.", is_error=True)
        try:
            revision = site._seo_apply(page, values.get("changes") or {}, values.get("lang"),
                                       expect_hash=values.get("hash"))
        except (AccessError, UserError) as error:
            return _refuse(error)
        return base.ToolResult(
            "Done: %s. Change %s keeps what it was." % (revision.name, revision.id),
            records=("website.page", [page.page_id.id], [page.name]),
            card_lines=[{"label": env._("Changed"), "value": revision.name}])


@base.register
class UndoSiteChange(base.Action):
    name = "undo_site_change"
    kind = "write"
    group = "nudge.group_nudge_agent"
    description = (
        "Undo a change made to a site by the Website Builder (a section edit, an SEO change or Publish all "
        "pages): the latest one that is not undone, or the one named by revision_id (see read_site_page). "
        "Refused when the page was edited again since. The person reads what is undone and says yes.")
    input_schema = {"type": "object", "properties": {
        "revision_id": {"type": "integer"}, "site_id": {"type": "integer"}, "website_id": {"type": "integer"}},
        "additionalProperties": False}

    def run(self, env, arguments, screen):
        try:
            revision = self._revision(env, arguments)
        except (AccessError, UserError) as error:
            return _refuse(error)
        return base.ToolResult(
            "The person is being shown what would be undone.",
            pending={"summary": env._("Undo: %s", revision.name), "res_model": "website.page", "res_ids": [],
                     "record_names": (revision.page_id.name or revision.site_id.name or "")[:500],
                     "is_sensitive": False, "values": {"revision_id": revision.id},
                     "preview": {"records": [], "lines": [
                         {"label": env._("Change"), "value": revision.name},
                         {"label": env._("Made"), "value": str(revision.create_date)[:16]}]}})

    @staticmethod
    def _revision(env, arguments):
        Revision = env["nudge.builder.revision"]
        if arguments.get("revision_id"):
            revision = Revision.browse(arguments["revision_id"]).exists()
        else:
            site = find_site(env, arguments)
            revision = Revision.search([("site_id", "=", site.id), ("state", "=", "done")], limit=1)
        if not revision or revision.state != "done":
            raise UserError(env._("There is no change to undo."))
        revision.site_id._check_designer()
        return revision

    def execute(self, env, step):
        revision = env["nudge.builder.revision"].browse((step.values or {}).get("revision_id")).exists()
        if not revision:
            return base.ToolResult("The change was deleted since.", is_error=True)
        try:
            revision.site_id._check_designer()
            revision.action_undo()
        except (AccessError, UserError) as error:
            return _refuse(error)
        return base.ToolResult(
            "Undone: %s" % revision.name, records=("website.page", [], []),
            card_lines=[{"label": env._("Undone"), "value": revision.name}])

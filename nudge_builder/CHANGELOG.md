# Changelog

## 19.0.1.58.0 (unreleased)

- L5 website builder finished: the build writes the SEO title and description (and keywords or share picture when a template has them) of every page, shown in the pages list of the site; the chat changes them (`change_page_seo`) after a before/after card.
- L5 Publish all pages keeps a revision (pages, previous home page, menu) and **Undo publish** puts it back; a second publish makes a new revision; the revisions are listed on the site.
- L5 section editing in the chat: `read_site_page`, `edit_page_section` (add, replace, edit, move up or down, remove) and `undo_site_change`, each with a before/after card, a revision per edit, the website designer right checked at the yes, and typed content kept as data; Arabic in i18n/ar.po.

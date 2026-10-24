# Changelog

## 19.0.1.59.2 (unreleased)

- Review: (S9, S10) The picture fetch of the builder followed redirects to hosts the allowed destinations of the company were not asked about, and called the picture provider and the external links of the site check without asking the list at all. The picture address is no longer followed, the provider is checked, and a link (or a redirect of one) to a host that is not allowed is not visited.

## 19.0.1.59.1

- Fix, found in the live trial: the site tools take the id of the site given as `website_id` for the site (they answered that nothing was built), and a site that is not found names the sites there are with both ids; the SEO description card is checked to show the new text on its "after" line.
## 19.0.1.59.0

- L5 website builder finished: the build writes the SEO title and description (and keywords or share picture when a template has them) of every page, shown in the pages list of the site; the chat changes them (`change_page_seo`) after a before/after card.
- L5 Publish all pages keeps a revision (pages, previous home page, menu) and **Undo publish** puts it back; a second publish makes a new revision; the revisions are listed on the site.
- L5 section editing in the chat: `read_site_page`, `edit_page_section` (add, replace, edit, move up or down, remove) and `undo_site_change`, each with a before/after card, a revision per edit, the website designer right checked at the yes, and typed content kept as data; Arabic in i18n/ar.po.
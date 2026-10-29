# Changelog

## 19.0.1.2.2

- A database from before 1.1.0 gets the tools add_site_item, add_location and set_contact in the skill Building a website or shop (the skill is kept from updates; the update loads it again where nobody changed it). The seven sector skills have narrower trigger words: small talk brings none.

## 19.0.1.2.1

- The records added after the build (content tabs, pictures pulled from Documents, rows imported from a file) reach the built pages without a new build and without AI: the button Show the records on the site, and the same after an import and after Pull from Documents. Tests: `/nudge_builder:TestShowRecords`.

## 19.0.1.2.0

- Website Builder 2.2: plugin points. Another module can add a kind of content (model, renderer, layouts), a check of Site Check and a template through the abstract model `nudge.builder.plugin`, without editing this module; with no plugin installed nothing changes. Two example plugins: `nudge_builder_realestate` (listings with filters) and `nudge_builder_workshop` (car workshop services with a booking request). Tests: `/nudge_builder:TestPluginPoints`.

## 19.0.1.59.5 (unreleased)

- Review: the check of outside links connects to the address it checked (S11, DNS rebinding), and a picture the images endpoint gives as an address is fetched from a public address only, unless the provider runs on our own server. That download had no check at all: a provider could point it at 169.254.169.254 and the answer would have become a picture of the site. Tests: `/nudge_builder:TestBuilderRebinding`.

## 19.0.1.59.3 (unreleased)

- Trial: L5, the SEO card read "SEO description: after" with a sentence about the change: the model (Gemini 3.5 Flash-Lite) had put "Update SEO title and meta description for the Services page" in the argument named `description`, and it was written as the meta description of the page. The argument is `meta_description` now, with a note to give the text word for word; a call with `description` is refused before any card, and the result tells the model the texts written. Not the plan card: an SEO change is never a step of a plan.

## 19.0.1.59.2 (unreleased)

- Review: (S9, S10) The picture fetch of the builder followed redirects to hosts the allowed destinations of the company were not asked about, and called the picture provider and the external links of the site check without asking the list at all. The picture address is no longer followed, the provider is checked, and a link (or a redirect of one) to a host that is not allowed is not visited.

## 19.0.1.59.1

- Fix, found in the live trial: the site tools take the id of the site given as `website_id` for the site (they answered that nothing was built), and a site that is not found names the sites there are with both ids; the SEO description card is checked to show the new text on its "after" line.
## 19.0.1.59.0

- L5 website builder finished: the build writes the SEO title and description (and keywords or share picture when a template has them) of every page, shown in the pages list of the site; the chat changes them (`change_page_seo`) after a before/after card.
- L5 Publish all pages keeps a revision (pages, previous home page, menu) and **Undo publish** puts it back; a second publish makes a new revision; the revisions are listed on the site.
- L5 section editing in the chat: `read_site_page`, `edit_page_section` (add, replace, edit, move up or down, remove) and `undo_site_change`, each with a before/after card, a revision per edit, the website designer right checked at the yes, and typed content kept as data; Arabic in i18n/ar.po.
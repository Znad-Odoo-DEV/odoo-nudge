# Nudge: Website Builder

Six questions about the business, three directions of the home page to look at side by side, then a website of
unpublished pages made only of Odoo's own snippets. Needs [Nudge](../nudge/README.md) and Website
(Website Sale is optional). Odoo 19 for now; 17 and 18 follow in a later store update.

It is for a Nudge manager who is also a website designer: it runs with that person's own rights and companies.
Nothing is published until the person says so, no existing page is edited and nothing is deleted.

## What a person gets

- **The interview**: one form with six groups: the business (or a sector template), the languages, the colours
  and fonts (the colours are read from the logo when there is one), the pages wanted, a shop or not, and the tone.
- **Three directions**: the home page drawn three ways with the company's colours and fonts, on the sample text of the template; the words of the company are written when the site is built. Pick one or
  ask again; only then **Build this website** makes the pages.
- **The pages**: made only of Odoo's own snippets, the text written by the AI provider in each language from the
  company's own facts (or the sample copy of the template when no AI may be used), the menu, the theme colours
  and fonts through Odoo's own customisation, and the privacy and terms pages.
- **Twelve sector templates**, in Arabic and English, each with a thumbnail: contracting, real estate, clinic,
  restaurant, school, law firm, workshop, services company, fashion shop, electronics shop, building materials
  shop, food products shop. Names in them are placeholders (Your Company, Raghad, Ebrahim) to replace.
- **The shop** (with Website Sale): a card shows what would be arranged (categories, filters, layout, banners,
  menu) and waits for a yes; a readiness list says what is left to do, such as payment and delivery.
- **Site Check**: reads any website, built by Nudge or not, with no AI: titles and descriptions, links,
  translations, image weight, contrast and alt text, privacy and terms, the shop. Each finding has a severity and
  **Show me**; the fixes only fill what is empty or add an unpublished draft, after the person saw the list.

## Pictures

With a picture model set (system parameter `nudge_builder.image_model`, an OpenAI-style `/images/generations`
endpoint) the pictures come from it; otherwise from the placeholders drawn for this module (abstract gradients,
CC0). Private mode refuses the picture model and the AI writing before anything leaves the server.

## Legal pages

The privacy and terms pages are a starting template. Have a lawyer read them before the site is published.

## Rights

Access rows: a website designer reads and writes sites, pages and Site Check reports; an administrator has all.
Record rules keep the sites and reports of one company from the designers of another.

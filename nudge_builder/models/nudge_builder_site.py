"""The website designer: an interview, three directions to look at, then a
build of unpublished pages made only of Odoo's own snippets.

The person always runs it as themselves: every create below is a create of
that person, who must be a website designer. Nothing is published, edited in
place or deleted by the build; an earlier page of the same address keeps it
and the new page takes the next free one.
"""
import base64
import json
import logging
import time
import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.nudge.models.nudge_run import register_job
from odoo.addons.nudge.tools import deadline as worker_time

from ..tools import compat, library, pictures, preview, snippets
from ..tools import copy as copywriter
from ..tools.legal import legal_specs
from ..tools.schema import KINDS

_logger = logging.getLogger(__name__)

# The pages of a site are made one after the other by the scheduled action. A step is not begun with
# less than this left of the piece (a page asks the writer once per language), and a provider is not
# called with less than this left of the worker: the sample copy and the placeholder picture stand in.
STEP_MARGIN = 40
PROVIDER_MARGIN = 30
# Advisory lock class: one build at a time on a website
LOCK_CLASS = 7_102_017

DESIGNER = "website.group_website_designer"
COLOUR = re.compile(r"#[0-9a-fA-F]{6}")
FONT = re.compile(r"[A-Za-z0-9 \-]{1,40}")
PICTURE_SNIPPETS = ("s_cover", "s_text_image", "s_image_text", "s_banner", "s_picture")
HEADERS = {"a": ("default", "descriptive"), "b": ("stretch", "links"), "c": ("search", "centered")}
MAX_PRODUCTS = 15


class NudgeBuilderSite(models.Model):
    _name = "nudge.builder.site"
    _description = "Nudge Website Builder"
    _order = "id desc"

    name = fields.Char(required=True, default=lambda self: _("New site"))
    website_id = fields.Many2one(
        "website", required=True, ondelete="cascade", default=lambda self: self.env["website"].get_current_website())
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    state = fields.Selection(
        [("draft", "Interview"), ("previewed", "Directions"), ("built", "Built"), ("published", "Published")],
        default="draft", required=True, copy=False)
    # 1. The business
    kind = fields.Char(string="What is your business?", help="Say it in your own words, in any language.")
    template_key = fields.Selection(lambda self: self._template_selection(), string="Closest template", default="generic")
    template_json = fields.Json(copy=False, help="A template given by code; the library is used when empty.")
    # 2. Languages
    lang_ids = fields.Many2many("res.lang", string="Languages", domain="[('active', '=', True)]")
    # 3. Colours and fonts
    color_primary = fields.Char(default="#1f3a5f")
    color_secondary = fields.Char(default="#c9a227")
    color_accent = fields.Char(default="#e07a1f")
    color_light = fields.Char(default="#f6f4ef")
    color_dark = fields.Char(default="#14213d")
    font_heading = fields.Char(default="Montserrat")
    font_body = fields.Char(default="Montserrat")
    colors_from_logo = fields.Boolean(readonly=True, help="The colours were read from the company logo.")
    # 4. Pages
    kind_ids = fields.Many2many("nudge.builder.page.kind", string="Pages wanted")
    allow_projects = fields.Boolean(string="Show our project names", help="The names of the projects are sent to the writer.")
    allow_team = fields.Boolean(string="Show our team", help="The public name and job title of employees are sent to the writer.")
    # 5. Shop
    shop = fields.Boolean(string="Online shop")
    shop_available = fields.Boolean(compute="_compute_shop_available")
    shop_plan = fields.Json(copy=False, help="The shop plan the person confirmed; the shop is built only when it is set.")
    # 6. Tone
    tone = fields.Selection(
        [("formal", "Formal"), ("friendly", "Friendly"), ("short", "Short")], default="friendly", required=True)
    # Directions
    direction = fields.Selection([("a", "A"), ("b", "B"), ("c", "C")])
    directions = fields.Json(copy=False)
    preview_html = fields.Html(sanitize=False, readonly=True, copy=False)
    private = fields.Boolean(compute="_compute_private", help="No text or picture is written by an AI.")
    # Build
    page_ids = fields.One2many("nudge.builder.page", "site_id", string="Pages")
    steps = fields.Json(copy=False)
    step_index = fields.Integer(copy=False)
    progress = fields.Float(copy=False)
    progress_text = fields.Char(copy=False)
    stopped = fields.Boolean(copy=False)
    log = fields.Text(copy=False)

    # ------------------------------------------------------------------ defaults
    @api.model
    def _template_selection(self):
        names = {key: template["name"].get("en", key) for key, template in library.all_templates().items()}
        return [(key, names.get(key, key)) for key in library.keys()]

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        company = self.env.company
        langs = self.env["res.lang"].search([("active", "=", True)])
        website = self.env["website"].get_current_website()
        wanted = website.language_ids or langs
        values.setdefault("lang_ids", [(6, 0, wanted.ids)])
        values["name"] = _("Website of %s", company.name)
        kinds = self.env["nudge.builder.page.kind"].search([("key", "in", ["home", "about", "services", "contact", "legal"])])
        values.setdefault("kind_ids", [(6, 0, kinds.ids)])
        industry = company.partner_id.industry_id.name if "industry_id" in company.partner_id._fields else False
        if industry and not values.get("kind"):
            values["kind"] = industry
            values["template_key"] = library.pick(industry)
        logo = company.logo
        if logo:
            found = pictures.dominant_colors(base64.b64decode(logo))
            if found:
                palette = pictures.palette_from(found)
                values.update({f"color_{key}": value for key, value in palette.items()})
                values["colors_from_logo"] = True
        values["shop"] = self._shop_installed()
        return values

    @api.onchange("kind")
    def _onchange_kind(self):
        if self.kind:
            self.template_key = library.pick(self.kind)

    @api.depends("state")
    def _compute_shop_available(self):
        installed = self._shop_installed()
        for site in self:
            site.shop_available = installed

    @api.constrains("color_primary", "color_secondary", "color_accent", "color_light", "color_dark",
                    "font_heading", "font_body")
    def _check_look(self):
        """The colours and fonts go into the preview page and the SCSS of the website: only what is safe there"""
        for site in self:
            for name in ("color_primary", "color_secondary", "color_accent", "color_light", "color_dark"):
                if site[name] and not COLOUR.fullmatch(site[name]):
                    raise ValidationError(_("%s must be a colour like #1f3a5f.", site._fields[name]._description_string(self.env)))
            for name in ("font_heading", "font_body"):
                if site[name] and not FONT.fullmatch(site[name]):
                    raise ValidationError(_("%s must be a font name: letters, digits, spaces.", site._fields[name]._description_string(self.env)))

    @api.depends("state", "company_id")
    def _compute_private(self):
        usable = self._provider_usable()
        for site in self:
            site.private = not usable

    @api.model
    def _provider(self):
        Provider = self.env["nudge.provider"].sudo()
        provider = Provider._get_for_mode("agent") or Provider._get_default()
        if not provider or not provider.is_configured or not provider.active:
            return Provider.browse()
        if Provider._is_private_mode() and not provider.runs_locally:
            return Provider.browse()
        return provider

    @api.model
    def _provider_usable(self):
        return bool(self._provider())

    # ------------------------------------------------------------------ helpers
    def _check_designer(self):
        if not self.env.user.has_group(DESIGNER):
            raise AccessError(_("Building a website needs the Website Designer right."))

    def _langs(self):
        """:return: the languages of the site, the main one first; English when none is chosen"""
        self.ensure_one()
        langs = self.lang_ids or self.env["res.lang"].search([("code", "=", "en_US")], limit=1)
        return langs

    def _source_lang(self):
        """:return: the res.lang whose text is the source of the arch: English when
        the site speaks it, the main language otherwise"""
        langs = self._langs()
        english = langs.filtered(lambda lang: lang.code == "en_US")
        return english or langs[:1]

    def _palette(self):
        self.ensure_one()
        return {key: self[f"color_{key}"] or pictures.DEFAULT_PALETTE[key] for key in pictures.DEFAULT_PALETTE}

    def _fonts(self):
        self.ensure_one()
        # Odoo bundles neither Cairo nor any other Arabic face; Tajawal is for Arabic, Montserrat for Latin
        default = "Tajawal" if self._langs()[:1].code.startswith("ar") else "Montserrat"
        return {"heading": self.font_heading or default, "body": self.font_body or default}

    def _load_template(self):
        self.ensure_one()
        if self.template_json:
            from ..tools.schema import validate_template
            return validate_template(self.template_json)
        try:
            return library.load(self.template_key or library.FALLBACK)
        except ValueError:
            return library.load(library.FALLBACK)

    def _say(self, text):
        self.ensure_one()
        self.log = ((self.log or "") + text + "\n")

    def _company_contact(self):
        company = self.company_id
        partner = company.partner_id
        address = ", ".join(part for part in (partner.street, partner.city, partner.country_id.name) if part)
        return {"phone": company.phone, "email": company.email, "address": address}

    def _company_facts(self):
        """:return: the facts about the company that the writer may use, in a
        ``<data>`` frame, with only what the person allowed"""
        self.ensure_one()
        company = self.company_id
        contact = self._company_contact()
        lines = [f"Company: {company.name}"]
        if self.kind:
            lines.append(f"Business: {self.kind}")
        if contact["address"]:
            lines.append(f"Address: {contact['address']}")
        if self.template_key:
            lines.append(f"Template: {self.template_key}")
        lines += self._names("Products and services", "product.template", [("sale_ok", "=", True)], MAX_PRODUCTS)
        if self.allow_projects:
            lines += self._names("Projects", "project.project", [], 10)
        if self.allow_team:
            lines += self._team_lines()
        body = "\n".join(lines).replace("</data>", "").replace("<data>", "")
        return f"<data>\n{body}\n</data>"

    def _names(self, label, model, domain, limit):
        if model not in self.env:
            return []
        try:
            records = self.env[model].search(domain, limit=limit)
            names = [name for name in records.mapped("display_name") if name]
        except AccessError:
            return []
        return [f"{label}: " + "; ".join(names)] if names else []

    def _team_lines(self):
        if "hr.employee.public" not in self.env:
            return []
        try:
            people = self.env["hr.employee.public"].search([], limit=12)
            rows = [f"{person.name} ({person.job_title})" if person.job_title else person.name for person in people]
        except AccessError:
            return []
        return ["Team: " + "; ".join(rows)] if rows else []

    # ------------------------------------------------------------------ the writer and the painter
    def _write_copy(self, page_spec, lang_code):
        """The content hook: the words of every block of a page in one language.

        :param dict page_spec: a page of a template
        :param str lang_code: the 2 letter code
        :return: ``{block index: {"title", "text", "button", "items"}}``; empty when
            no provider may be asked (none set up, Private Mode) or it failed: the
            caller then keeps the sample copy of the template"""
        self.ensure_one()
        if not self._provider_usable() or self._short_of_time():
            return {}
        lang = self.env["res.lang"].search([("code", "=like", f"{lang_code}\\_%")], limit=1)
        prompt = copywriter.prompt_for(
            self._company_facts(), page_spec, lang.name or lang_code, self.tone)
        answer = self.env["nudge.provider"]._complete(
            copywriter.SYSTEM, prompt, self.env.user, self.env.company, timeout=120)
        return copywriter.parse(answer, len(page_spec.get("blocks") or []))

    @api.model
    def _short_of_time(self):
        """:return: whether the worker has too little time left to ask a provider for anything"""
        left = worker_time.remaining()
        return left is not None and left < PROVIDER_MARGIN

    def _picture_for(self, prompt, placeholder_key):
        """:return: a public attachment: the picture model's drawing of ``prompt`` when one is
        configured and answers, else the bundled placeholder ``placeholder_key``"""
        self.ensure_one()
        data, name = None, None
        provider = self._provider()
        model = self.env["ir.config_parameter"].sudo().get_param(pictures.IMAGE_MODEL_PARAM)
        if provider and model and prompt and not self._short_of_time():
            data = pictures.draw_picture(provider, model, prompt, timeout=worker_time.cap(60))
            name = "nudge_builder_%s.png" % compat.unique_id()
        key = placeholder_key if placeholder_key in pictures.PLACEHOLDERS else "abstract"
        if not data:
            name = f"nudge_builder_{key}.jpg"
            existing = self.env["ir.attachment"].search([
                ("res_model", "=", self._name), ("res_id", "=", self.id), ("name", "=", name)], limit=1)
            if existing:
                return existing
            data = pictures.placeholder_bytes(key)
        return self.env["ir.attachment"].create({
            "name": name, "datas": base64.b64encode(data), "public": True,
            "res_model": self._name, "res_id": self.id,
        })

    # ------------------------------------------------------------------ the theme
    def _set_theme(self, colors, fonts):
        """Write the palette and the font pair through the website's own customisation"""
        self.ensure_one()
        self._check_designer()
        compat.set_theme_values(self.env, colors, fonts)
        header, footer = HEADERS.get(self.direction or "a", HEADERS["a"])
        try:
            compat.set_layout(self.env, self.website_id, header, footer)
        except Exception:  # noqa: BLE001 - the layout is a nicety; the pages do not depend on it
            _logger.info("Nudge Builder: the header and footer layout was not set", exc_info=True)
            self._say(_("The header and footer layout was left as it is."))

    # ------------------------------------------------------------------ snippets and pages
    def _content_for(self, content, lang):
        """:return: the fields for one language out of a language map or a flat dict"""
        content = content or {}
        short = compat.lang_code(lang.code)
        mapped = {key: value for key, value in content.items() if isinstance(value, dict) and len(key) in (2, 3)}
        if mapped:
            return mapped.get(short) or mapped.get("en") or next(iter(mapped.values()))
        return content

    def _sections(self, snippet_key, content, picture_url=None):
        """:return: ``{lang code: lxml section}`` of the snippet filled in every language of the site"""
        facts = self._company_contact()
        result = {}
        for lang in self._langs():
            # Rendered in the language, so Odoo's own words in the snippet speak it too
            markup = compat.render_snippet(self.with_context(lang=lang.code).env, snippet_key)
            section = compat.parse_section(markup)
            snippets.fill(section, snippet_key, self._content_for(content, lang), picture_url, facts)
            result[lang.code] = section
        return result

    def _arch_by_lang(self, name, key, sections_by_lang):
        source = self._source_lang().code
        arch = {}
        for code, sections in sections_by_lang.items():
            arch[code] = compat.page_arch(name, key, "".join(compat.to_string(section) for section in sections))
        return arch, source

    def _unique_url(self, url):
        website = self.website_id
        url = "/" + (url or "page").strip("/")
        taken = set(self.env["website.page"].search([("website_id", "in", [website.id, False])]).mapped("url"))
        candidate, index = url, 1
        while candidate in taken:
            index += 1
            candidate = f"{url}-{index}"
        return candidate

    def _spec_picture(self, spec, block):
        """:return: the url of the picture of a block, or None when the snippet keeps its own"""
        snippet = block["snippet"].split(".")[-1]
        if not (block.get("image") or block.get("image_prompt")) and snippet not in PICTURE_SNIPPETS:
            return None
        if snippet == "s_picture" and not block.get("image"):
            return None
        attachment = self._picture_for(block.get("image_prompt"), block.get("image") or "abstract")
        return f"/web/image/{attachment.id}"

    def _build_page(self, spec):
        """Build one page, unpublished, as a new view and a new website.page.

        :param dict spec: a page of a template, plus ``menu`` True to add it to the menu
        :return: the ``nudge.builder.page``"""
        self.ensure_one()
        self._check_designer()
        langs = self._langs()
        key_short = spec["key"]
        names = spec.get("name") or {}
        generated = {}
        if not self.private and not spec.get("legal"):
            for lang in langs:
                generated[compat.lang_code(lang.code)] = self._write_copy(spec, compat.lang_code(lang.code))
        columns = {lang.code: [] for lang in langs}
        for index, block in enumerate(spec.get("blocks") or []):
            picture = self._spec_picture(spec, block)
            sample = block.get("copy") or {}
            per_lang_content = {}
            for lang in langs:
                short = compat.lang_code(lang.code)
                written = (generated.get(short) or {}).get(index)
                base = sample.get(short) or sample.get("en") or {}
                per_lang_content[short] = {**base, **(written or {})} if written else base
            sections = self._sections(block["snippet"], per_lang_content, picture)
            for code, section in sections.items():
                columns[code].append(section)
        name_en = names.get("en") or key_short
        view_key = f"website.nb{self.id}_{compat.slug(self.env, key_short)}_{compat.unique_id()}"
        arch_by_lang, source = self._arch_by_lang(name_en, view_key, columns)
        view = self.env["ir.ui.view"].with_context(lang="en_US").create({
            "name": name_en, "type": "qweb", "key": view_key, "website_id": self.website_id.id,
            "arch_db": arch_by_lang[source],
        })
        compat.write_arch(view, arch_by_lang, source)
        url = self._unique_url(spec.get("url") or f"/{compat.slug(self.env, name_en)}")
        page = self.env["website.page"].create({
            "url": url, "view_id": view.id, "website_id": self.website_id.id,
            "is_published": False, "track": True,
        })
        record = self.env["nudge.builder.page"].create({
            "site_id": self.id, "page_id": page.id, "view_id": view.id, "key": key_short,
            "kind": spec.get("kind"), "name": name_en, "url": url,
            "snippet_keys": ",".join(block["snippet"].split(".")[-1] for block in spec.get("blocks") or []),
            "is_legal": bool(spec.get("legal")), "sequence": 10 * (len(self.page_ids) + 1),
        })
        self._write_seo(page, spec.get("seo") or self._derived_seo(spec, names), self._seo_image(spec))
        if spec.get("menu"):
            self._add_menu(record, names)
        return record

    def _derived_seo(self, spec, names):
        """:return: a title (the page name and the company) and a description (the first words of the page) for a
        page whose template has no SEO of its own, in each language the page has words in"""
        self.ensure_one()
        seo = {"title": {}, "description": {}}
        company = self.company_id.name or ""
        block = next(iter(spec.get("blocks") or []), None) or {}
        for lang in self._langs():
            short = compat.lang_code(lang.code)
            name = names.get(short) or names.get("en") or spec["key"]
            seo["title"][short] = (f"{name} | {company}" if company else name)[:70]
            copy = (block.get("copy") or {}).get(short) or (block.get("copy") or {}).get("en") or {}
            words = " ".join(str(copy.get("text") or copy.get("title") or "").split())
            if len(words) > 160:
                words = words[:160].rsplit(" ", 1)[0].rstrip(",.;: ") + "..."
            if words:
                seo["description"][short] = words
        return {key: value for key, value in seo.items() if value}

    def _seo_image(self, spec):
        """:return: the share picture of a page when its template names one of the bundled pictures"""
        key = (spec.get("seo") or {}).get("image")
        if key not in pictures.PLACEHOLDERS:
            return None
        return f"/web/image/{self._picture_for(None, key).id}"

    def _add_menu(self, record, names):
        website = self.website_id
        menu = self.env["website.menu"].create({
            "name": names.get("en") or record.name, "url": record.url, "page_id": record.page_id.id,
            "parent_id": website.menu_id.id, "website_id": website.id,
            "sequence": record.sequence + 100,
        })
        for lang in self._langs():
            short = compat.lang_code(lang.code)
            if lang.code != "en_US" and names.get(short):
                menu.with_context(lang=lang.code).name = names[short]
        record.menu_id = menu

    def _place_snippet(self, page, snippet_key, content, position=None):
        """Put one more of Odoo's snippets on a page the builder made.

        :param page: the ``nudge.builder.page``
        :param snippet_key: ``s_cover`` or ``module.xmlid``
        :param content: ``{"en": {...}, "ar": {...}}`` or one flat dict for every language
        :param position: index among the sections of the page, the end by default
        :return: the section placed, in the source language"""
        self.ensure_one()
        self._check_designer()
        page.ensure_one()
        sections = self._sections(snippet_key, content)
        source = self._source_lang().code
        view = page.view_id.with_context(lang="en_US")
        tree, wrap = compat.wrap_body(view.arch_db)
        marked = sections[source]
        if position is None or position >= len(wrap):
            wrap.append(marked)
        else:
            wrap.insert(position, marked)
        view.write({"arch_db": compat.to_string_tree(tree)})
        compat.map_terms(view, compat.to_string(sections[source]), {
            code: compat.to_string(section) for code, section in sections.items() if code != source})
        page.snippet_keys = ",".join(page._snippet_list() + [snippet_key.split(".")[-1]])
        return sections[source]

    # ------------------------------------------------------------------ directions
    def action_preview(self):
        """Draw the three design directions of the home page"""
        self.ensure_one()
        self._check_designer()
        if not self.kind_ids:
            raise UserError(_("Choose at least one page."))
        template = self._load_template()
        palette, fonts = self._palette(), self._fonts()
        arabic = any(compat.lang_code(code) == "ar" for code in self._langs().mapped("code"))
        own = template.get("directions")
        if own and palette == template["palette"] and fonts == template["fonts"]:
            directions = [
                {"key": key, "palette": entry["palette"], "fonts": entry["fonts"], "hero": preview.HEROES[index],
                 "label": entry["name"]}
                for index, (key, entry) in enumerate(zip("abc", own))]
        else:
            directions = preview.derive_directions(palette, fonts, arabic)
        labels = {"a": _("A - Classic"), "b": _("B - Bold"), "c": _("C - Soft")}
        home = next((page for page in template["pages"] if page["kind"] == "home"), template["pages"][0])
        lang = self._source_lang()
        short = compat.lang_code(lang.code)
        hero = home["blocks"][0]["copy"].get(short) or home["blocks"][0]["copy"]["en"]
        feature_block = next((b for b in home["blocks"] if (b.get("copy", {}).get("en") or {}).get("items")), None)
        features = [
            item.get("title") or item.get("question") or ""
            for item in ((feature_block["copy"].get(short) or feature_block["copy"]["en"]).get("items") or [])
        ] if feature_block else []
        cards = []
        for direction in directions:
            label = labels[direction["key"]]
            if isinstance(direction.get("label"), dict):
                label = f"{direction['key'].upper()} - " + (direction["label"].get(short) or direction["label"]["en"])
            direction["label"] = label
            document = preview.card_document(
                direction, hero.get("title", self.company_id.name), hero.get("text", ""), hero.get("button", ""),
                features, rtl=short == "ar")
            cards.append(preview.card_html(direction, label, document))
        self.write({
            "directions": directions, "preview_html": preview.cards_html(cards),
            "direction": self.direction or "a", "state": "previewed",
            "steps": False, "step_index": 0, "progress": 0, "stopped": False,
        })
        return self._reopen()

    def action_pick_direction(self, key):
        self.ensure_one()
        if key not in ("a", "b", "c"):
            raise UserError(_("Unknown direction."))
        self.direction = key
        return True

    def _chosen(self):
        self.ensure_one()
        for direction in self.directions or []:
            if direction["key"] == (self.direction or "a"):
                return direction
        return {"palette": self._palette(), "fonts": self._fonts()}

    def _reopen(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id,
            "view_mode": "form", "target": "current",
        }

    # ------------------------------------------------------------------ the build
    def _wanted_kinds(self):
        self.ensure_one()
        return set(self.kind_ids.mapped("key"))

    def _plan(self, template, kinds=None):
        """:return: the list of steps that builds the site"""
        self.ensure_one()
        kinds = kinds if kinds is not None else self._wanted_kinds()
        steps = [{"kind": "theme", "text": _("Theme: colours and fonts")}]
        blog = self._module_installed("website_blog")
        for page in template["pages"]:
            if page["kind"] not in kinds or page["kind"] == "legal":
                continue
            if page["kind"] == "blog" and blog:
                continue
            steps.append({"kind": "page", "key": page["key"], "text": _("Page: %s", page["name"].get("en"))})
        if "legal" in kinds:
            for spec in legal_specs({"company": "x", "address": "x", "email": "x"}):
                steps.append({"kind": "legal", "key": spec["key"], "text": _("Page: %s", spec["name"]["en"])})
        steps.append({"kind": "menu", "text": _("Menu")})
        if self.shop_plan and self.shop and self.shop_available and hasattr(self, "_build_shop"):
            steps.append({"kind": "shop", "text": _("Shop")})
        if hasattr(self, "_run_checks"):
            steps.append({"kind": "checks", "text": _("Checks")})
        return steps

    @api.model
    def _shop_installed(self):
        return "product.public.category" in self.env and self._module_installed("website_sale")

    @api.model
    def _module_installed(self, name):
        return bool(self.env["ir.module.module"].search_count([("name", "=", name), ("state", "=", "installed")]))

    def _legal_spec(self, key):
        contact = self._company_contact()
        facts = {"company": self.company_id.name, "address": contact["address"] or "-", "email": contact["email"] or "-"}
        return next(spec for spec in legal_specs(facts) if spec["key"] == key)

    def _run_step(self, template, step):
        """Do one step of the plan"""
        self.ensure_one()
        kind = step["kind"]
        if kind == "theme":
            chosen = self._chosen()
            self._set_theme(chosen["palette"], chosen["fonts"])
        elif kind == "page":
            spec = next(page for page in template["pages"] if page["key"] == step["key"])
            self._build_page(spec)
        elif kind == "legal":
            self._build_page(self._legal_spec(step["key"]))
        elif kind == "menu":
            self._build_menu(template)
        elif kind == "shop":
            self._build_shop(self.shop_plan)
        elif kind == "checks":
            self._run_checks()

    def _build_menu(self, template):
        """Add the menu entries of the template's order for the pages built"""
        website = self.website_id
        built = {page.key: page for page in self.page_ids}
        names = {page["key"]: page["name"] for page in template["pages"]}
        order = [key for key in (template.get("menu") or []) if key in built or key == "blog"]
        for index, key in enumerate(order):
            if key == "blog" and "blog" not in built and self._module_installed("website_blog"):
                self.env["website.menu"].create({
                    "name": names.get("blog", {}).get("en", "Blog"), "url": "/blog",
                    "parent_id": website.menu_id.id, "website_id": website.id, "sequence": 200 + index})
                continue
            record = built[key]
            if not record.menu_id:
                record.sequence = 10 * (index + 1)
                self._add_menu(record, names.get(key, {}))
        for record in self.page_ids.filtered("is_legal"):
            if not record.menu_id:
                self._add_menu(record, {"en": record.name})
                record.menu_id.sequence = 900

    def _apply_template(self, template, kinds=None):
        """Build the whole site from a template, without the pause between steps.

        :param dict template: a template of the library (``schema.validate_template``)
        :param kinds: set of page kinds to build; the site's pages wanted by default
        :return: the pages built"""
        self.ensure_one()
        self._check_designer()
        from ..tools.schema import validate_template
        self.template_json = validate_template(template)
        if not self.direction:
            self.direction = "a"
        before = self.page_ids
        for step in self._plan(template, kinds):
            self._run_step(template, step)
        self.write({"state": "built", "progress": 100})
        return self.page_ids - before

    def action_build(self):
        """Prepare the plan, and leave the work to the scheduled action: the pages are made one step at a time,
        each committed, in the background. The screen shows the bar, and Stop is heard between two steps. A
        build that was stopped goes on where it stopped."""
        self.ensure_one()
        self._check_designer()
        if self.state not in ("previewed", "built"):
            raise UserError(_("Look at the three directions first."))
        if (self.shop and self.shop_available and hasattr(self, "_shop_plan")
                and not self.env.context.get("shop_decided") and not (self.steps and self.stopped)):
            return self.env["nudge.builder.shop.confirm"]._open_for(self)
        if not (self.steps and self.stopped):
            self.write({"steps": self._plan(self._load_template()), "step_index": 0, "progress": 0, "log": False})
        return self._run_action()

    def _run_action(self):
        self.stopped = False
        Run = self.env["nudge.run"]
        if not Run._job_open("builder_site", self.id):
            busy = Run.sudo().search([("job", "=", "builder_site"), ("state", "in", ("queued", "running"))]).filtered(
                lambda run: self.browse((run.job_args or {}).get("id")).website_id == self.website_id)
            if busy:
                raise UserError(_("Another site is being built on this website. Wait until it is done."))
            Run._start_job("builder_site", _("Build the website %s", self.name), {"id": self.id})
        return {
            "type": "ir.actions.client", "tag": "nudge_builder_run",
            "params": {"site_id": self.id}, "name": _("Building the website"),
        }

    def _website_lock(self):
        """Take, for this transaction, the right to build on the website; False when another build holds it"""
        self.env.cr.execute("SELECT pg_try_advisory_xact_lock(%s, %s)", [LOCK_CLASS, self.website_id.id])
        return bool(self.env.cr.fetchone()[0])

    def _do_step(self, template, step):
        """Do the step at ``step_index`` and go to the next, as one unit: what a step that fails had
        made is undone with it, so no half page is left behind. A person's mistake is told to them."""
        self.ensure_one()
        steps = self.steps or []
        try:
            with self.env.cr.savepoint():
                self._run_step(template, step)
        except UserError:
            raise
        except Exception as exc:  # noqa: BLE001 - one step failing must not hide the rest
            _logger.exception("Nudge Builder: step %s failed", step)
            self._say(_("%(step)s failed: %(error)s", step=step.get("text"), error=exc))
        self.step_index += 1
        self.progress = round(100.0 * self.step_index / (len(steps) + 1), 1)
        self.progress_text = step.get("text") or ""

    def action_build_step(self):
        """Run ONE step of the plan now, as the person (what the scheduled action does, step after step).

        :return: ``{"done", "progress", "text", "stopped"}``"""
        self.ensure_one()
        self._check_designer()
        steps = self.steps or []
        if self.stopped:
            return {"done": False, "stopped": True, "progress": self.progress, "text": self.progress_text or ""}
        if self.step_index >= len(steps):
            return self._finish()
        if not self._website_lock():
            raise UserError(_("Another site is being built on this website. Wait until it is done."))
        self._do_step(self._load_template(), steps[self.step_index])
        if self.step_index >= len(steps):
            return self._finish()
        return {"done": False, "stopped": False, "progress": self.progress, "text": self.progress_text}

    def _build_piece(self, run, deadline):
        """The scheduled action's part: steps until the time of the piece is up, each committed.

        :return: whether the build is over (done or stopped)"""
        self.ensure_one()
        steps = self.steps or []
        template = self._load_template()
        while self.step_index < len(steps):
            self.invalidate_recordset(["stopped"])
            if self.stopped or run._job_stopped():
                return True
            if time.monotonic() > deadline - STEP_MARGIN:
                return False  # the next piece goes on from the step it reached
            if not self._website_lock():
                return False
            self._do_step(template, steps[self.step_index])
            run.progress = self.progress_text
            run._tell("progress")
            run._commit()
        self._finish()
        run._say(_("The website %s is built. Nothing is published yet.", self.name))
        return True

    def action_build_status(self):
        """What the screen asks every few seconds while the scheduled action builds.

        :return: ``{"done", "progress", "text", "stopped", "failed"}``"""
        self.ensure_one()
        self._check_designer()
        steps = self.steps or []
        run = self.env["nudge.run"]._job_latest("builder_site", self.id)
        failed = bool(run) and run.state == "failed"
        done = self.state in ("built", "published") and bool(steps) and self.step_index >= len(steps)
        return {"done": done, "stopped": bool(self.stopped), "failed": failed,
                "progress": 100 if done else self.progress, "text": self.progress_text or "",
                "error": _("The build stopped on an error: look at the log of the site.") if failed else ""}

    def _finish(self):
        self.ensure_one()
        self.write({"state": "built", "progress": 100, "progress_text": _("Done: nothing is published yet.")})
        return {"done": True, "stopped": False, "progress": 100, "text": self.progress_text}

    def action_stop(self):
        """Stop after the step that is running; what is built stays, unpublished"""
        self.ensure_one()
        self.write({"stopped": True})
        self.env["nudge.run"]._job_open("builder_site", self.id).action_stop()
        self._say(_("Stopped by the person at step %s.", self.step_index))
        return self._reopen()

    def action_continue(self):
        """Go on from the step where the build was stopped"""
        self.ensure_one()
        return self.action_build()

    # ------------------------------------------------------------------ publish
    def action_publish(self):
        """Publish every page of the site, and make its home the home of the website. What it changes is kept
        as a revision (the pages that were not published, the home page before, the menu): Undo publish puts it back."""
        self.ensure_one()
        self._check_designer()
        if self.state not in ("built", "published"):
            raise UserError(_("Build the site first."))
        pages = self.page_ids.page_id
        website = self.website_id
        home = self.page_ids.filtered(lambda page: page.key == "home")[:1]
        before = {
            "pages": [{"id": page.id, "was": bool(page.is_published)} for page in pages],
            "home_before": website.homepage_url or False,
            "menus": [{"id": menu.id, "sequence": menu.sequence, "parent_id": menu.parent_id.id}
                      for menu in self.page_ids.menu_id],
            "state_before": self.state,
        }
        pages.write({"is_published": True})
        if home:
            old = website.homepage_url
            website.homepage_url = home.url
            self._say(_("Home page changed from %(old)s to %(new)s.", old=old or "/", new=home.url))
        self.env["nudge.builder.revision"].create({
            "site_id": self.id, "kind": "publish", "before": before,
            "name": _("Published %s pages", len(pages)),
            "after": {"published": True, "home": website.homepage_url or False},
        })
        self.state = "published"
        return self._reopen()

    def action_open_editor(self):
        self.ensure_one()
        page = self.page_ids.filtered(lambda p: p.key == "home")[:1] or self.page_ids[:1]
        return page.action_open_editor() if page else self._reopen()


register_job("builder_site", "nudge.builder.site", "_build_piece")

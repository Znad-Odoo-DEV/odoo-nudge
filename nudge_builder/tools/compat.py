"""Everything that differs between Odoo 17, 18 and 19, in one place."""
import re
import uuid

from lxml import etree, html

COLOR_URL = "/website/static/src/scss/options/colors/user_color_palette.scss"
THEME_COLOR_URL = "/website/static/src/scss/options/colors/user_theme_color_palette.scss"
VALUES_URL = "/website/static/src/scss/options/user_values.scss"
BUNDLE = "web.assets_frontend"
# The snippets some versions do not ship, and what stands in for each
SNIPPET_FALLBACK = {
    "s_contact_info": "s_text_block",
    "s_faq_collapse": "s_text_block",
    "s_company_team": "s_text_block",
    "s_numbers": "s_text_block",
}


def assets_model(env):
    """:return: the model that writes the theme customisation: website.assets
    from 19, web_editor.assets before"""
    return env["website.assets"] if "website.assets" in env else env["web_editor.assets"]


def set_theme_values(env, colors, fonts):
    """The palette and the font pair, written the way the website editor does.

    :param colors: ``{"primary", "secondary", "accent", "light", "dark"}``
    :param fonts: ``{"heading", "body"}``"""
    assets = assets_model(env)
    # colours are Sass literals, unquoted as the editor writes them; only font names and
    # template keys are strings
    # o-color-1 primary, 2 secondary, 3 light, 4 white, 5 dark: the order of the editor
    assets.make_scss_customization(COLOR_URL, {
        "o-color-1": colors["primary"],
        "o-color-2": colors["secondary"],
        "o-color-3": colors["light"],
        "o-color-4": "#FFFFFF",
        "o-color-5": colors["dark"],
    })
    assets.make_scss_customization(THEME_COLOR_URL, {"info": colors["accent"]})
    families = []
    for name in (fonts["heading"], fonts["body"]):
        if name not in families:
            families.append(name)
    values = {
        "font": f"'{fonts['body']}'",
        "headings-font": f"'{fonts['heading']}'",
        "navbar-font": f"'{fonts['heading']}'",
        "buttons-font": f"'{fonts['body']}'",
        "google-fonts": "(" + ", ".join(f"'{name.replace(' ', '+')}'" for name in families) + ")",
    }
    assets.make_scss_customization(VALUES_URL, values)


def render_snippet(env, key):
    """:param key: ``s_cover`` or a full ``module.xmlid``
    :return: the HTML of the snippet as Odoo's own template renders it"""
    xmlid = key if "." in key else f"website.{key}"
    if xmlid.split(".")[-1] in SNIPPET_FALLBACK and not env.ref(xmlid, raise_if_not_found=False):
        # a snippet this version does not ship: the closest one it has stands in
        xmlid = "website." + SNIPPET_FALLBACK[xmlid.split(".")[-1]]
    rendered = env["ir.ui.view"]._render_template(xmlid)
    if isinstance(rendered, bytes):
        rendered = rendered.decode()
    return str(rendered).strip()


def parse_section(markup):
    """:return: the lxml element of the first element of a snippet's HTML"""
    nodes = html.fragments_fromstring(markup)
    nodes = [node for node in nodes if not isinstance(node, str)]
    return nodes[0]


def to_string(element):
    # XML, as the editor saves it: Odoo 17 validates a page arch as XML, and a void <img> needs its slash
    return etree.tostring(element, encoding="unicode", method="xml")


PAGE_ARCH = (
    '<t name="{name}" t-name="{key}">'
    '<t t-call="website.layout"><div id="wrap" class="oe_structure oe_empty">{body}</div></t></t>'
)


def page_arch(name, key, body=""):
    return PAGE_ARCH.format(name=_attr(name), key=key, body=body)


def _attr(text):
    return (text or "").replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def wrap_body(arch):
    """:return: (tree, the #wrap element) of a page arch"""
    tree = etree.fromstring(arch.encode() if isinstance(arch, str) else arch)
    wrap = tree.xpath('//div[@id="wrap"]')[0]
    return tree, wrap


def unique_id():
    return uuid.uuid4().hex[:8]


def slug(env, text):
    """:return: a url-safe form of ``text``; ``ir.http._slugify`` before Odoo 18 is a function of http_routing"""
    if hasattr(env["ir.http"], "_slugify"):
        return env["ir.http"]._slugify(text or "", max_length=100)
    from odoo.addons.http_routing.models.ir_http import slugify
    return slugify(text or "", max_length=100)


def map_terms(view, source_markup, translated):
    """Give the terms of ``source_markup`` the translation found in the same
    place of each other language's markup.

    :param translated: ``{lang code: markup}`` with the structure of ``source_markup``
    :return: the language codes whose structure did not match, and so were left untranslated"""
    field = view._fields["arch_db"]
    source_terms = field.get_trans_terms(source_markup)
    skipped = []
    for code, markup in translated.items():
        terms = field.get_trans_terms(markup)
        if len(terms) != len(source_terms):
            skipped.append(code)
            continue
        mapping = {old: new for old, new in zip(source_terms, terms) if old != new}
        if mapping:
            view.update_field_translations("arch_db", {code: mapping})
    return skipped


def write_arch(view, arch_by_lang, default_lang="en_US"):
    """Write one arch as the source and the translation of each other language.

    :param arch_by_lang: ``{lang code: arch}``; the entry for ``default_lang`` is the source
    :return: the languages left untranslated (their arch had another structure)"""
    view = view.with_context(lang="en_US")
    view.write({"arch_db": arch_by_lang[default_lang]})
    return map_terms(view, arch_by_lang[default_lang], {c: a for c, a in arch_by_lang.items() if c != default_lang})


def lang_code(text):
    """:return: the 2 letter code of ``ar_001``, ``en_US``"""
    return (text or "en").split("_")[0].lower()


def to_string_tree(tree):
    return etree.tostring(tree, encoding="unicode", method="xml")


HEADER_FAMILY = ("default", "hamburger", "stretch", "vertical", "search", "sales_one", "sales_two", "sales_three")
FOOTER_FAMILY = ("descriptive", "centered", "links", "minimalist", "contact", "call_to_action", "headline", "mega")


def set_layout(env, website, header, footer):
    """The header and footer templates of the website, the way the editor picks them.

    Views of the website itself are written (Odoo copies the generic one for it),
    so no other website changes."""
    assets_model(env).make_scss_customization(
        VALUES_URL, {"header-template": f"'{header}'", "footer-template": f"'{footer}'"})
    View = env["ir.ui.view"].with_context(website_id=website.id, active_test=False)
    for family, prefix, chosen in ((HEADER_FAMILY, "template_header_", header), (FOOTER_FAMILY, "template_footer_", footer)):
        for name in family:
            view = env.ref(f"website.{prefix}{name}", raise_if_not_found=False)
            if not view:
                continue
            view = View.browse(view.id)
            want = name == chosen
            specific = view.search([("key", "=", view.key), ("website_id", "=", website.id)], limit=1)
            current = (specific or view).active
            if current != want:
                view.write({"active": want})

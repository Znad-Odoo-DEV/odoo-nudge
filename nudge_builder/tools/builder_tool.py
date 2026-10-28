"""Nudge's door to the builder: "build me a website", in any language or dialect.

The tool opens the interview with what the person already said; it creates
nothing and publishes nothing. The six questions are answered on the screen.
"""
from odoo import _
from odoo.addons.nudge.tools import base, groups

from . import library

WORDS = (
    "website", "web site", "webpage", "web page", "landing page", "online shop", "online store",
    "موقع", "موقعي", "سايت", "ويب سايت", "صفحة ويب", "صفحات ويب", "متجر الكتروني", "متجر إلكتروني", "صفحة هبوط",
)

GROUP = {
    "about": "design and build a website or an online shop for the company, with its pages, texts and pictures",
    "tools": ("start_website_builder",),
    "words": WORDS,
}


ARABIC = ("عربي", "العربي", "بالعربي", "عربى", "العربى", "بالعربى", "العربية", "بالعربية", "arabic", "arab")
ENGLISH = ("إنجليزي", "انجليزي", "الانجليزي", "الإنجليزي", "بالانجليزي", "بالإنجليزي", "انجليزى", "إنجليزى",
           "الانجليزية", "الإنجليزية", "بالانجليزية", "بالإنجليزية", "انكليزي", "الانكليزي", "english")


def languages_named(*texts):
    """:return: the language codes (``ar``, ``en``) the person names in their words, in the order named"""
    import re
    text = " ".join(t or "" for t in texts).lower()
    found = []
    for code, words in (("ar", ARABIC), ("en", ENGLISH)):
        positions = [m.start() for word in words for m in re.finditer(re.escape(word), text)]
        if positions:
            found.append((min(positions), code))
    return [code for _pos, code in sorted(found)]


def _register_group():
    """The tool comes with the words 'website' and 'موقع' in the question"""
    if "website_builder" in groups.GROUPS:
        return
    groups.GROUPS["website_builder"] = GROUP
    forms = tuple(groups._forms(word) for word in WORDS)
    groups.FORMS["website_builder"] = forms
    groups.TRIGGERS.update(form for form in forms if " " not in form)


_register_group()


@base.register
class StartWebsiteBuilder(base.Tool):
    name = "start_website_builder"
    group = "website.group_website_designer"
    terminal = True
    kind = "show"
    description = (
        "Open the Website Builder interview for the person who asks to build, make, design or create a "
        "website, a web site, a landing page or an online shop (أبي موقع، بدي موقع، اعمللي موقع، "
        "أريد متجر إلكتروني), in any language or dialect. It asks six questions on screen with the company's "
        "data already filled in, draws three designs to pick from, and builds unpublished pages. "
        "Pass what the person already said; do not ask them the questions yourself."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["text"],
        "properties": {
            "business": {"type": "string", "maxLength": 200,
                         "description": "What the business is, in the person's words, if they said."},
            "shop": {"type": "boolean", "description": "True if they want to sell online."},
            "languages": {"type": "array", "items": {"type": "string", "enum": ["ar", "en"]},
                          "description": "The languages of the site the person named (Arabic, English)."},
            "text": {"type": "string", "minLength": 1, "maxLength": 300,
                     "description": "One sentence in the language the person wrote in."},
        },
    }

    @staticmethod
    def _language_context(env, named):
        """The languages to pre-select: those named; else the person's own and English"""
        shorts = named or [(env.user.lang or "en_US").split("_")[0], "en"]
        langs = env["res.lang"]
        for short in shorts:
            candidates = env["res.lang"].search([("active", "=", True)]).filtered(
                lambda lang: lang.code.split("_")[0] == short)
            langs |= candidates.sorted(lambda lang: lang.code not in ("en_US", "ar_001"))[:1]
        return {"default_lang_ids": [(6, 0, langs.ids)]} if langs else {}

    def run(self, env, arguments, screen):
        if "nudge.builder.site" not in env:
            return base.ToolResult("The website builder is not installed.", is_error=True)
        context = {}
        business = (arguments.get("business") or "").strip()
        if business:
            context["default_kind"] = business
            context["default_template_key"] = library.pick(business, env["nudge.builder.plugin"].plugin_template_paths())
        named = [code for code in (arguments.get("languages") or []) if code in ("ar", "en")]
        named = named or languages_named(business, arguments.get("text"))
        context.update(self._language_context(env, named))
        if "shop" in arguments:
            context["default_shop"] = bool(arguments["shop"])
        action = {
            "type": "ir.actions.act_window", "name": _("Website Builder"), "res_model": "nudge.builder.site",
            "views": [[False, "form"]], "view_mode": "form", "target": "current", "context": context,
        }
        return base.ToolResult(
            "The Website Builder interview is being opened.",
            event={"type": "open_action", "action": action, "text": arguments["text"].strip()},
        )

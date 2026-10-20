"""Nudge's door to the builder: "build me a website", in any language or dialect.

The tool opens the interview with what the person already said; it creates
nothing and publishes nothing. The six questions are answered on the screen.
"""
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
            "text": {"type": "string", "minLength": 1, "maxLength": 300,
                     "description": "One sentence in the language the person wrote in."},
        },
    }

    def run(self, env, arguments, screen):
        if "nudge.builder.site" not in env:
            return base.ToolResult("The website builder is not installed.", is_error=True)
        context = {}
        business = (arguments.get("business") or "").strip()
        if business:
            context["default_kind"] = business
            context["default_template_key"] = library.pick(business)
        if "shop" in arguments:
            context["default_shop"] = bool(arguments["shop"])
        action = {
            "type": "ir.actions.act_window", "name": env._("Website Builder"), "res_model": "nudge.builder.site",
            "views": [[False, "form"]], "view_mode": "form", "target": "current", "context": context,
        }
        return base.ToolResult(
            "The Website Builder interview is being opened.",
            event={"type": "open_action", "action": action, "text": arguments["text"].strip()},
        )

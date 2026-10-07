"""Nudge before any AI provider is set up: a search, no conversation.

What the person types is looked for in the menus they can open, the settings
of the apps (for an administrator) and the official documentation; a menu or
a setting found is shown on the screen by the pointer. Nothing here calls a
provider, and nothing here is offered once one is ready: the conversation
takes over.
"""
from odoo import api, models
from odoo.exceptions import AccessError

from ..tools.configuration import find_app, settings_layout, settings_steps
from ..tools.navigation import _visible_menus, menu_path, menu_steps
from ..tools.records import Refused

MAX_WORDS = 80
MAX_MENUS = 6
MAX_SETTINGS = 6
MAX_PAGES = 5
MAX_SNIPPET = 160


class NudgeQuick(models.AbstractModel):
    _name = "nudge.quick"
    _description = "Nudge Search Without AI"

    @api.model
    def _check_user(self):
        if not self.env.user.has_group("nudge.group_nudge_user"):
            raise AccessError(self.env._("Nudge is not open to you."))

    @api.model
    def find(self, words):
        """:return: ``{"menus", "settings", "pages"}`` that match every word"""
        self._check_user()
        text = " ".join(str(words or "").split())[:MAX_WORDS]
        terms = [term.casefold() for term in text.split()]
        found = {"menus": [], "settings": [], "pages": []}
        if not terms or len(text) < 2:
            return found
        found["menus"] = self._find_menus(terms)
        if self.env.user.has_group("base.group_system"):
            found["settings"] = self._find_settings(terms)
        found["pages"] = self._find_pages(text)
        return found

    @api.model
    def guide(self, kind, ref):
        """:param kind: "menu" or "setting"
        :param ref: the menu id, or ``{"app", "field"}``
        :return: the steps of the pointer, empty when the way is not theirs"""
        self._check_user()
        try:
            if kind == "menu":
                menu = _visible_menus(self.env).filtered(lambda candidate: candidate.id == int(ref))
                path = menu_path(self.env, menu) if menu else []
                return menu_steps(self.env, path, []) if path else []
            if kind == "setting" and isinstance(ref, dict):
                app = find_app(settings_layout(self.env), str(ref.get("app") or ""))
                field = str(ref.get("field") or "")
                label = next((setting["label"] for setting in app["settings"] if field in setting["fields"]), field)
                text = self.env._("Here is “%s”: set it as you need.", label)
                return settings_steps(self.env, app, [{"field": field, "text": text}])
        except (Refused, ValueError, TypeError):
            return []
        return []

    def _find_menus(self, terms):
        menus = _visible_menus(self.env)
        english = dict(zip(menus.ids, menus.with_context(lang="en_US").mapped("complete_name")))
        found = []
        for menu in menus:
            text = f"{menu.complete_name or ''} {english.get(menu.id) or ''}".casefold()
            if all(term in text for term in terms):
                found.append(menu)
        found.sort(key=lambda menu: (len(menu.complete_name or ""), menu.complete_name or ""))
        return [{"id": menu.id, "path": menu.complete_name} for menu in found[:MAX_MENUS]]

    def _find_settings(self, terms):
        found = []
        for app in settings_layout(self.env):
            for setting in app["settings"]:
                if setting["fields"] and all(term in setting["words"] for term in terms):
                    found.append({
                        "app": app["app"],
                        "app_label": app["label"],
                        "label": setting["label"],
                        "help": setting["help"][:MAX_SNIPPET],
                        "field": setting["fields"][0],
                    })
                    if len(found) >= MAX_SETTINGS:
                        return found
        return found

    def _find_pages(self, text):
        # The documentation is public: read for anyone who may use Nudge
        passages = self.env["nudge.doc.passage"].sudo()._search_text(text, limit=MAX_PAGES * 3)
        pages, seen = [], set()
        for passage in passages:
            if passage.page_id.id in seen:
                continue
            seen.add(passage.page_id.id)
            title = passage.title + (f" ▸ {passage.heading}" if passage.heading else "")
            pages.append({
                "title": title,
                "url": passage._source(),
                "snippet": " ".join((passage.body or "").split())[:MAX_SNIPPET],
            })
            if len(pages) >= MAX_PAGES:
                break
        return pages

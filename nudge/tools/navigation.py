"""Taking the person to another screen.

Only menus are offered as destinations, and only the ones this person can
open: what Nudge can reach is what they can reach by clicking.
"""
from odoo import _
from . import base

MAX_MENUS = 25


def _visible_menus(env):
    Menu = env["ir.ui.menu"]
    return Menu.browse(Menu._visible_menu_ids()).filtered("action")


def _describe(menu):
    return {"menu_id": menu.id, "path": menu.complete_name}


def _find_by_words(env, words):
    """:return: the menu with an action, among those the person can open, whose
    path holds every word; the shortest path first"""
    words = [word.lower() for word in words.split() if word]
    found = [
        menu for menu in _visible_menus(env)
        if words and all(word in (menu.complete_name or "").lower() for word in words)
    ]
    found.sort(key=lambda menu: (len(menu.complete_name), menu.complete_name))
    return found[0] if found else env["ir.ui.menu"]


def menu_path(env, menu):
    """:return: the menus from the app down to ``menu``, each one the person can
    see; empty when one of them is not for them"""
    visible = set(env["ir.ui.menu"]._visible_menu_ids())
    chain = []
    while menu:
        if menu.id not in visible:
            return []
        chain.append(menu)
        menu = menu.parent_id
    return list(reversed(chain))


@base.register
class FindScreen(base.Tool):
    name = "find_screen"
    description = (
        "Look for a screen of this database by the words of its menu, among the menus the person "
        "can open. Returns menu ids with their path, for instance \"Sales/Orders/Quotations\". "
        "Use it before show_menu_path (to show them the way there) or open_screen (to take them "
        "there), or to tell the person where something is. Menus are named in "
        "the person's interface language."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["words"],
        "properties": {
            "words": {
                "type": "string",
                "minLength": 1,
                "maxLength": 80,
                "description": "One or a few words of the menu, such as \"quotations\" or \"warehouse\".",
            },
        },
    }

    def run(self, env, arguments, screen):
        words = arguments["words"].lower().split()
        found = [
            menu for menu in _visible_menus(env)
            if all(word in (menu.complete_name or "").lower() for word in words)
        ]
        if not found:
            return base.ToolResult(
                "No menu the person can open has these words. Try fewer or other words: "
                "menus are named in their interface language."
            )
        found.sort(key=lambda menu: (len(menu.complete_name), menu.complete_name))
        return base.ToolResult(base.dump([_describe(menu) for menu in found[:MAX_MENUS]]))


@base.register
class ShowMenuPath(base.Tool):
    name = "show_menu_path"
    terminal = True
    description = (
        "Show the person, on their screen, the way through the menus to another screen: "
        "Nudge's pointer goes to the app, then to each menu, step by step, as they click. Use it "
        "when they ask how to open or find a screen, a report, a list, or how to get somewhere. "
        "Give menu_id from find_screen, or words of the menu path in the language of the "
        "interface, such as attendance reporting. texts, one per menu from the app down, says "
        "what to click and why, in the language the person wrote in; without texts each step "
        "names its menu."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "menu_id": {"type": "integer", "minimum": 1},
            "words": {
                "type": "string",
                "minLength": 2,
                "maxLength": 80,
                "description": "When no menu_id: words of the menu path, as the menus are named.",
            },
            "texts": {
                "type": "array",
                "maxItems": 6,
                "items": {"type": "string", "maxLength": 300},
                "description": "One text per menu, from the app down to the screen.",
            },
        },
    }

    def run(self, env, arguments, screen):
        Menu = env["ir.ui.menu"]
        if arguments.get("menu_id"):
            menu = _visible_menus(env).filtered(lambda menu: menu.id == arguments["menu_id"])
        elif arguments.get("words"):
            menu = _find_by_words(env, arguments["words"])
        else:
            menu = Menu
        path = menu_path(env, menu) if menu else []
        if not path:
            return base.ToolResult(
                "No menu the person can open was found. Use find_screen with other words.",
                is_error=True,
            )
        texts = [text.strip() for text in arguments.get("texts") or [] if text and text.strip()]
        steps = []
        for position, item in enumerate(path):
            text = texts[position] if position < len(texts) else _("Click “%s”.", item.name)
            parent = path[position - 1] if position else None
            steps.append({
                "target": {
                    "type": "menu",
                    "name": str(item.id),
                    "xmlid": item.get_external_id().get(item.id, ""),
                    "label": item.name,
                    "app": position == 0,
                    "parent": str(parent.id) if parent else "",
                    "parent_label": parent.name if parent else "",
                },
                "text": text,
                "advance": "auto",
            })
        return base.ToolResult(
            f"The way to {path[-1].complete_name!r} is being shown on the person's screen, "
            f"{len(steps)} step(s).",
            event={"type": "guidance", "steps": steps},
        )


@base.register
class OpenScreen(base.Tool):
    name = "open_screen"
    terminal = True
    description = (
        "Open a screen for the person, given a menu id returned by find_screen. Use it when they "
        "ask to go somewhere or to be taken to a screen. Say in \"text\" where you are taking them."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["menu_id", "text"],
        "properties": {
            "menu_id": {"type": "integer", "minimum": 1},
            "text": {
                "type": "string",
                "minLength": 1,
                "maxLength": 300,
                "description": "In the language the person wrote in.",
            },
        },
    }

    def run(self, env, arguments, screen):
        menu = _visible_menus(env).filtered(lambda menu: menu.id == arguments["menu_id"])
        if not menu:
            return base.ToolResult(
                "This menu does not exist or the person cannot open it. Use find_screen.",
                is_error=True,
            )
        return base.ToolResult(
            f"The screen {menu.complete_name!r} is being opened.",
            event={
                "type": "navigate",
                "menu_id": menu.id,
                "path": menu.complete_name,
                "text": arguments["text"].strip(),
            },
        )

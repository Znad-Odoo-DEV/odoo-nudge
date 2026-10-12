"""Nudge before any AI provider is set up: a search, no conversation.

What the person types is looked for in the menus they can open, the settings
of the apps (for an administrator) and the official documentation; a menu or
a setting found is shown on the screen by the pointer. Nothing here calls a
provider, and nothing here is offered once one is ready: the conversation
takes over.

The commands of the message box that need no AI are answered here too, with
or without a provider: /screen, /setting, /doc, /file, /find, /product,
/stock, /rules, /sql, /help.
"""
import json
import logging
import re

from odoo import api, models
from odoo.exceptions import AccessError

from ..tools import base, commands, product_search
from ..tools.configuration import find_app, settings_layout, settings_steps
from ..tools.finding import REFERENCE_FIELDS, _score, candidate_models, look_in
from ..tools.navigation import _visible_menus, menu_path, menu_steps
from ..tools.records import MODEL_PATTERN, Refused, cut, get_model

_logger = logging.getLogger(__name__)

MAX_WORDS = 80
MAX_MENUS = 6
MAX_SETTINGS = 6
MAX_PAGES = 5
MAX_PRODUCTS = 5
MAX_SNIPPET = 160
MAX_COMMAND_CHARS = 2000
MAX_RECORDS = 8
MAX_PASSAGES = 5
MAX_PASSAGE_CHARS = 400
# Kinds of records a bare reference is looked for in, the most filled first
MAX_REFERENCE_MODELS = 40
# What a small model is given with a question
MAX_GROUND_SCREENS = 3
MAX_GROUND_PAGES = 2
MAX_GROUND_PASSAGES = 2
MAX_GROUND_CHARS = 500
# The words of a question that say nothing of what it is about, as fold() writes them
GROUND_STOP_WORDS = frozenset("""
how do does did can could would should the and for with what where when which who why this that
these those our your their have has are was were get make want need please there here from into
about show tell use using new add see find me my you we it its is an a to of in on at by be or not
\u0643\u064a\u0641 \u0634\u0648 \u0648\u064a\u0646 \u0644\u064a\u0634 \u0644\u0634\u0648
\u0628\u062f\u064a \u0628\u062f\u0643 \u0647\u0644 \u0641\u064a \u0639\u0644\u064a
\u0645\u0646 \u0627\u0644\u064a \u0639\u0646 \u0645\u0639 \u0647\u0627\u062f
\u0647\u064a\u062f\u0627 \u0647\u064a \u0647\u0648 \u0627\u0646\u0627 \u0627\u0646\u062a
\u064a\u0644\u064a \u0644\u064a \u0627\u0632\u0627\u064a \u0639\u0627\u064a\u0632
\u0645\u0645\u0643\u0646 \u0644\u0627\u0632\u0645 \u0643\u0645\u0627\u0646 \u0647\u0644\u0642
\u0634\u0644\u0648\u0646 \u0645\u0627\u0630\u0627 \u0643\u064a\u0641\u064a\u0647 \u0644\u0627
\u0628\u0639\u0645\u0644 \u0627\u0639\u0645\u0644 \u0628\u0633\u0648\u064a
""".split())



# A name in quotes is what is to be made or written, not what it is about:
# "add a field 'Project site' on the quotation"
QUOTED = re.compile(r"['\"\u2018\u2019\u201c\u201d\u00ab\u00bb][^'\"\u2018\u2019\u201c\u201d\u00ab\u00bb]{1,80}"
                    r"['\"\u2018\u2019\u201c\u201d\u00ab\u00bb]")


def without_names(text):
    """:return: ``text`` without what it names in quotes"""
    return QUOTED.sub(" ", text or "")

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
        terms = text.split()
        found = {"menus": [], "settings": [], "pages": [], "products": []}
        if not terms or len(text) < 2:
            return found
        found["menus"] = self._find_menus(terms)
        if self.env.user.has_group("base.group_system"):
            found["settings"] = self._find_settings(terms)
        found["pages"] = self._find_pages(text)
        found["products"] = product_search.find(self.env, text, limit=MAX_PRODUCTS)["items"]
        return found

    @api.model
    def find_products(self, text):
        """/product, typed in the panel: the products most like ``text``,
        found without any AI, as the person may see them
        :return: ``{"query", "items"}``"""
        self._check_user()
        return product_search.find(self.env, text)

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

    @api.model
    def list_commands(self):
        """:return: the commands the person may type, for the message box"""
        self._check_user()
        return commands.listed(self.env)

    @api.model
    def run_command(self, text, model_name=None):
        """A command typed in the message box, answered without any AI.

        :param text: the message, starting with the command
        :param model_name: model of the screen the person is on, for /rules
        :return: ``{"command", "query", "focused", "content", ...}`` with what
            was found: ``menus``, ``settings``, ``pages``, ``records``,
            ``passages``, ``products``, ``code``, ``table``, ``commands``;
            ``command`` is False when the text is no command; a focused
            command is left to the conversation
        """
        self._check_user()
        command, rest = commands.parse(str(text or "")[:MAX_COMMAND_CHARS])
        if command is None:
            return {"command": False}
        answer = {"command": command.name, "query": rest, "focused": command.kind == commands.FOCUSED,
                  "content": ""}
        if not commands.is_open(self.env, command.name):
            answer["content"] = self.env._("The command %s is not open to you.", command.words[0])
            return answer
        if command.kind == commands.FOCUSED:
            return answer
        needs_words = command.name not in ("help", "rules")
        if needs_words and len(rest) < 2:
            example = commands.texts(self.env)[command.name][1]
            answer["content"] = self.env._(
                "Write what to look for after %(command)s, for example: %(example)s",
                command=command.words[0], example=example)
            return answer
        getattr(self, f"_command_{command.name}")(rest, answer, model_name)
        if not answer["content"] and not any(
                answer.get(key) for key in ("menus", "settings", "pages", "records", "passages",
                                            "products", "code", "table", "commands")):
            answer["content"] = self.env._("Nothing found for “%s”. Try other words.", rest)
        return answer

    def _command_help(self, rest, answer, model_name):
        answer["commands"] = commands.listed(self.env)
        answer["content"] = self.env._(
            "Start a message with one of these words to tell me where to look. Those marked AI ask "
            "the AI with a few tools only; the others answer at once, without any AI.")

    def _command_screen(self, rest, answer, model_name):
        answer["menus"] = self._find_menus(rest.split())
        if not answer["menus"]:
            answer["content"] = self.env._("No screen you can open is called “%s”.", rest)

    def _command_setting(self, rest, answer, model_name):
        answer["settings"] = self._find_settings(rest.split())
        if not answer["settings"]:
            answer["content"] = self.env._("No setting is called “%s”. The settings are named in English.", rest)

    def _command_doc(self, rest, answer, model_name):
        answer["pages"] = self._find_pages(rest)
        if not answer["pages"]:
            answer["content"] = self.env._(
                "No page of the documentation speaks of “%s”. It is written in English.", rest)

    def _command_file(self, rest, answer, model_name):
        passages = self.env["nudge.source.passage"]._search_text(rest, limit=MAX_PASSAGES)
        answer["passages"] = [{
            "id": passage.id,
            "file": passage.source_id.name,
            "page": passage.page,
            "text": cut(" ".join((passage.body or "").split()), MAX_PASSAGE_CHARS),
            "url": f"/web/content/nudge.source/{passage.source_id.id}/file",
        } for passage in passages]
        if not passages:
            answer["content"] = self.env._(
                "Nothing in the company files matches “%s”. Try the words the files would use.", rest)

    def _command_find(self, rest, answer, model_name):
        # "invoice: INV/2026/0042": the kind first, when the person knows it
        topic, colon, name = rest.partition(":")
        if not colon or not name.strip() or len(topic) > 40:
            topic, name = "", rest
        topic, name = topic.strip(), name.strip()
        rest = name
        found = []
        try:
            target = self._rules_target(topic) if topic else {}
            if target.get("model"):
                models_ = [get_model(self.env, target["model"])]
            else:
                models_ = candidate_models(self.env, topic, name)
        except (Refused, AccessError):
            models_ = []
        for rank, model in enumerate(models_):
            for record in look_in(model, rest):
                if self._is_called(record, rest):
                    found.append((-_score(record, rest), rank, model, record))
        if not found and not topic and any(char.isdigit() for char in rest):
            # A number or a reference alone: it is looked for as it is written
            # in every kind of record that holds some
            found = self._find_reference(rest)
        found.sort(key=lambda item: item[:2])
        records = []
        for _score_, _rank, model, record in found[:MAX_RECORDS]:
            entry = {"model": model._name, "kind": model._description, "id": record.id,
                     "name": cut(record.display_name or "", 120)}
            field = model._fields.get("state")
            if field is not None and field.type == "selection" and product_search._readable(model, "state"):
                entry["state"] = dict(field._description_selection(self.env)).get(record.state, record.state or "")
            records.append(entry)
        answer["records"] = records
        if not records:
            answer["content"] = self.env._("No record you may read is called “%s”.", rest)

    def _ground(self, text):
        """For a small model, what Nudge finds for a question by itself, before
        the model is asked: the screens whose names hold its words, in the
        language of the person and in English, the pages of the documentation
        about them, the passages of the company files. A small model answers
        from what it is given far better than from what it remembers.

        :return: ``{"screens", "documentation", "company_files"}``, without
            the empty ones; empty when nothing was found"""
        found = {}
        text = without_names(text)
        screens = self._ground_screens(text)
        if screens:
            found["screens"] = screens
        # The documentation is written in English: the screens found say in
        # English what an Arabic question is about
        terms = [screen["path_en"].split("/")[-1] for screen in found.get("screens", [])[:1]]
        terms += [word for word in re.findall(r"[A-Za-z]{3,}", text) if word.casefold() not in GROUND_STOP_WORDS][:4]
        if terms:
            pages = [dict(page, snippet=page["snippet"][:200]) for page in self._find_pages(" or ".join(terms))]
            if pages:
                found["documentation"] = pages[:MAX_GROUND_PAGES]
        # Any of the words that say what it is about, as the person wrote them
        plain = [word for word in re.findall(r"\w+", text)
                 if len(word) >= 3 and commands.fold(word) not in GROUND_STOP_WORDS][:6]
        passages = self.env["nudge.source.passage"]._search_text(" or ".join(plain), limit=MAX_GROUND_PASSAGES)
        if passages:
            found["company_files"] = [{
                "file": passage.source_id.name,
                "page": passage.page,
                "text": cut(" ".join((passage.body or "").split()), MAX_GROUND_CHARS),
            } for passage in passages]
        return found

    def _ground_screens(self, text):
        """:return: the screens the person may open whose names hold the
        words of ``text``, in their language or in English, the closest
        first: ``[{"menu_id", "path", "path_en", "model"}]``"""
        return [screen for _rank, screen in self._scored_screens(text)][:MAX_GROUND_SCREENS]

    def _scored_screens(self, text):
        """:return: ``[(rank, screen)]``, the closest first: the words of the
        path met, those of the screen's own name, how much of that name they
        cover ("عروض الأسعار" all of it, "قوالب عرض السعر" two words of three)"""
        words = [word for word in commands.fold(text).split()
                 if len(word) >= 3 and word not in GROUND_STOP_WORDS]
        roots = {commands.root_of(word) for word in words} - {""}
        if not roots:
            return []
        scored = []
        for menu in _visible_menus(self.env):
            english_menu = menu.with_context(lang="en_US")
            own, english = menu._get_full_name(), english_menu._get_full_name()
            leaf = {commands.root_of(word) for word in commands.fold(f"{menu.name} {english_menu.name}").split()}
            path = {commands.root_of(word) for word in commands.fold(f"{own} {english}").split()}
            # The screen itself is about it, not only an app above it
            if not roots & leaf:
                continue
            covered = round(len(roots & leaf) / len(leaf), 2)
            scored.append(((-len(roots & path), -len(roots & leaf), -covered), len(own), own, english, menu.id))
        scored.sort(key=lambda item: item[:2])
        screens = []
        for rank, _length, own, english, menu_id in scored[:MAX_GROUND_SCREENS * 2]:
            screen = {"menu_id": menu_id, "path": own, "path_en": english}
            action = self.env["ir.ui.menu"].browse(menu_id).sudo().action
            if action and action._name == "ir.actions.act_window" and action.res_model:
                # The kind of record of the screen: what a model reads, changes and builds on
                screen["model"] = action.res_model
            screens.append((rank, screen))
        return screens

    def _ground_work(self, text):
        """For a small model doing the work: the screens the question names,
        with the kind of record behind each. Short: the context of the work
        is taken by its tools.

        :return: ``{"screens"}``, or empty"""
        scored = [(rank, screen) for rank, screen in self._scored_screens(without_names(text)) if screen.get("model")]
        if not scored:
            return {}
        # The best only, and what ties with it: a small model takes what it is given
        best = scored[0][0]
        return {"screens": [screen for rank, screen in scored if rank == best][:MAX_GROUND_SCREENS]}

    def _find_reference(self, text):
        """:return: the records whose name or reference is ``text`` exactly,
        among the kinds of records of this database, the most filled first"""
        profile = self.env["nudge.profile"]._ensure_current()
        pieces = [piece for piece in profile.sudo().slice_ids
                  if piece.kind == "activity" and piece.model_name and (piece.data or {}).get("records")]
        pieces.sort(key=lambda piece: -(piece.data or {}).get("records", 0))
        found = []
        for rank, piece in enumerate(pieces[:MAX_REFERENCE_MODELS]):
            try:
                model = get_model(self.env, piece.model_name)
            except Refused:
                continue
            names = [name for name in REFERENCE_FIELDS
                     if name in model._fields and model._fields[name].type == "char"
                     and product_search._readable(model, name)]
            if not names:
                continue
            domain = ["|"] * (len(names) - 1) + [(name, "=ilike", text) for name in names]
            try:
                records = model.search(domain, limit=MAX_RECORDS)
            except (AccessError, ValueError):
                continue
            found.extend((-3, rank, model, record) for record in records)
            if len(found) >= MAX_RECORDS:
                break
        return found

    @staticmethod
    def _is_called(record, text):
        """:return: whether every word of ``text`` is in what ``record`` is
        called: its name or a reference; a search may find more, it is not
        shown without an AI to judge it"""
        values = [record.display_name or ""]
        for name in REFERENCE_FIELDS:
            field = record._fields.get(name)
            if field is not None and field.type == "char" and product_search._readable(record, name):
                values.append(record[name] or "")
        called = " ".join(values).casefold()
        return all(word in called for word in text.casefold().split())

    def _command_product(self, rest, answer, model_name):
        answer["products"] = product_search.find(self.env, rest)["items"]
        if not answer["products"]:
            answer["content"] = self.env._(
                "No product looks like “%s”. Try another word, a code or a size.", rest)

    def _command_stock(self, rest, answer, model_name):
        items = product_search.find(self.env, rest)["items"]
        answer["products"] = [item for item in items if "on_hand" in item]
        if not answer["products"]:
            answer["content"] = self.env._("No product kept in stock looks like “%s”.", rest)

    def _command_rules(self, rest, answer, model_name):
        arguments = self._rules_target(rest or model_name or "")
        if not arguments:
            answer["content"] = self.env._(
                "Write the kind of record after /rules, its technical name or its name: /rules sale.order")
            return
        env = self.with_context(nudge_mode=base.MODE_DEV).env
        result = base.call(env, "read_automations", arguments, None, base.MODE_DEV)
        if result.is_error:
            answer["content"] = result.content
        else:
            answer["code"] = result.content

    def _rules_target(self, text):
        """:return: the arguments of read_automations for ``text``: a model
        by its technical name or its name, else the words"""
        text = text.strip()
        if not text:
            return {}
        if re.fullmatch(MODEL_PATTERN, text) and text in self.env:
            return {"model": text}
        models_ = self.env["ir.model"].search(["|", ("name", "=ilike", text), ("model", "=ilike", text)], limit=2)
        if len(models_) != 1:
            # Its name in any spelling, in the language of the person or in English
            terms = commands.fold(text).split()
            Model = self.env["ir.model"]
            english = dict(Model.with_context(lang="en_US").search_fetch([], ["name"]).mapped(lambda m: (m.id, m.name)))
            models_ = Model.search_fetch([], ["name", "model"]).filtered(
                lambda m: terms and commands.matches(terms, f"{m.name} {english.get(m.id, '')}"))
            if len(models_) > 1:
                # The one called exactly so, else the shortest name
                exact = models_.filtered(lambda m: commands.fold(m.name) == " ".join(terms)
                                         or commands.fold(english.get(m.id, "")) == " ".join(terms))
                models_ = exact[:1] or models_.sorted(lambda m: len(m.name or ""))[:1]
        if len(models_) == 1 and models_.model in self.env:
            return {"model": models_.model}
        return {"words": text[:80]} if len(text) >= 2 else {}

    def _command_sql(self, rest, answer, model_name):
        _logger.info("Nudge: /sql by user %s: %s", self.env.uid, rest[:500])
        result = base.call(self.env, "run_sql", {"query": rest, "purpose": "/sql"}, None, base.MODE_AGENT)
        if result.is_error:
            answer["content"] = result.content
            return
        read = json.loads(result.content)
        answer["table"] = {"columns": read["columns"], "rows": read["rows"], "note": read.get("note", "")}
        if not read["rows"]:
            answer["content"] = self.env._("The query returned no row.")

    def _find_menus(self, terms):
        terms = commands.fold(" ".join(terms)).split()
        menus = _visible_menus(self.env)
        found = []
        for menu in menus:
            # The path is built in each language: the full name of a menu is
            # kept in one language only once it was read
            own = menu._get_full_name()
            english = menu.with_context(lang="en_US")._get_full_name()
            if terms and commands.matches(terms, f"{own} {english}"):
                found.append((own, menu))
        found.sort(key=lambda item: (len(item[0]), item[0]))
        return [{"id": menu.id, "path": path} for path, menu in found[:MAX_MENUS]]

    def _find_settings(self, terms):
        terms = commands.fold(" ".join(terms)).split()
        found = []
        for app in settings_layout(self.env):
            for setting in app["settings"]:
                if setting["fields"] and terms and commands.matches(terms, setting["words"]):
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

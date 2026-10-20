"""Recorded tours: the pointer, replayed without any AI.

A manager records a way of doing something once, clicking through it: every
menu, button, tab and field he presses becomes a step, named the way the
pointer already understands (a menu, a button name, a field name, a tab
name), never by a path in the page. Anyone who may see the tour replays it:
the pointer opens the start screen and goes from step to step with the
manager's notes. No provider is needed, and the AI may pick a stored tour
instead of improvising one.

A step whose target is not on this database (an app not installed, a field a
module does not add) is skipped with a polite note, never an error.
"""
import re

from lxml import etree

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from ..tools import commands
from ..tools.guidance import CONTROLS
from ..tools.navigation import _visible_menus, menu_path, menu_steps

KINDS = [
    ("menu", "Menu"),
    ("button", "Button"),
    ("field", "Field"),
    ("tab", "Tab"),
    ("control", "Control"),
]
MAX_STEPS = 60
MAX_NOTE = 400
MAX_TOURS = 8
NAME = re.compile(r"[A-Za-z0-9_.%()\- ]{1,120}")
XMLID = re.compile(r"[A-Za-z0-9_.]{1,128}")
VIEW_TYPES = ("form", "list", "tree", "kanban", "search", "calendar", "pivot", "graph", "activity", "map", "gantt")


def _phrases(text):
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


class NudgeTour(models.Model):
    _name = "nudge.tour"
    _inherit = ["nudge.recipe.mixin"]
    _description = "Nudge Recorded Tour"
    _order = "sequence, name, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    phrases_en = fields.Text(
        string="Phrases in English", help="What people may type to find this tour, one phrase per line.")
    phrases_ar = fields.Text(
        string="Phrases in Arabic", help="What people may type to find this tour, one phrase per line.")
    step_ids = fields.One2many("nudge.tour.step", "tour_id", string="Steps", copy=True)
    step_count = fields.Integer(compute="_compute_step_count")
    action_xmlid = fields.Char(
        string="Start Screen",
        help="External id of the action the tour opens first, such as sale.action_quotations_with_onboarding. "
             "Empty: the tour starts on the screen the person is on.")
    module_name = fields.Char(
        string="Needs the App",
        help="Technical name of the app the tour is about. The tour is only offered where it is installed.")
    group_ids = fields.Many2many(
        "res.groups", string="For the Groups",
        help="Who may replay this tour. Empty: everybody who uses Nudge.")
    company_id = fields.Many2one(
        "res.company", string="Company", help="Empty: all the companies.")
    is_library = fields.Boolean(string="Ready-Made", readonly=True, copy=False)

    @api.depends("step_ids")
    def _compute_step_count(self):
        for tour in self:
            tour.step_count = len(tour.step_ids)

    # ------------------------------------------------------------------ finding

    @api.model
    def _check_user(self):
        if not self.env.user.has_group("nudge.group_nudge_user"):
            raise AccessError(_("Nudge is not open to you."))

    @api.model
    def _installed_modules(self):
        # sudo: an ordinary user may not read the list of apps, and only the
        # names of the installed ones are used here
        modules = self.env["ir.module.module"].sudo().search([("state", "=", "installed")])
        return set(modules.mapped("name"))

    def _is_offered(self, installed):
        """:return: whether the tour is about an app this database has"""
        self.ensure_one()
        return not self.module_name or self.module_name in installed

    @api.model
    def _find_in(self, tours, words):
        """:return: the ``tours`` that answer ``words`` (all when empty), best first.

        The words are compared folded: lower case, no marks, one form of alif,
        the Arabic article and the plural taken off; a tour answers when every
        word is in its name or in one of its phrases."""
        terms = commands.fold(words).split()
        roots = [commands.root_of(term) for term in terms]
        found = []
        for tour in tours:
            texts = [tour.name, tour.with_context(lang="en_US").name]
            texts += _phrases(tour.phrases_en) + _phrases(tour.phrases_ar)
            folded = [commands.fold(text) for text in texts if text]
            blob = " ".join(folded)
            blob_roots = {commands.root_of(word) for word in blob.split()}
            if not terms:
                found.append((0, tour))
                continue
            score = 0
            # A phrase said whole, or the start of a phrase
            query = " ".join(terms)
            if any(query == phrase for phrase in folded):
                score += 100
            elif any(phrase and (phrase in query or query in phrase) for phrase in folded):
                score += 60
            if all(term in blob or root in blob_roots for term, root in zip(terms, roots)):
                score += 30
            if score:
                found.append((score, tour))
        found.sort(key=lambda item: (-item[0], item[1].sequence, item[1].name or "", item[1].id))
        return [tour for _score, tour in found]

    @api.model
    def _replayable(self):
        """:return: the tours the person may replay here: theirs by the record
        rules (groups, company), about an app this database has"""
        installed = self._installed_modules()
        return self.search([]).filtered(lambda tour: tour._is_offered(installed))

    @api.model
    def find_tours(self, words=""):
        """:return: ``[{"id", "name", "steps"}]`` the person may replay and
        that answer ``words``, best first; all of them when ``words`` is empty"""
        self._check_user()
        tours = self._find_in(self._replayable(), str(words or "")[:200])
        return [{"id": tour.id, "name": tour.name, "steps": tour.step_count} for tour in tours[:MAX_TOURS]]

    # ------------------------------------------------------------------ replay

    @api.model
    def play(self, tour_id):
        """:return: ``{"id", "name", "action", "steps", "skipped"}`` for the
        pointer: the start screen to open (an external id), the steps that
        exist on this database, and the notes for those that do not.
        Empty when the tour is not the person's."""
        self._check_user()
        # A search, not a browse: the record rules (groups, company) apply
        tour = self.search([("id", "=", int(tour_id or 0))])
        if not tour or not tour._is_offered(self._installed_modules()):
            return {}
        steps, skipped = tour._pointer_steps()
        return {
            "id": tour.id,
            "name": tour.name,
            "action": tour._start_action_xmlid(),
            "steps": steps,
            "skipped": skipped,
        }

    def _start_action_xmlid(self):
        self.ensure_one()
        xmlid = self.action_xmlid
        if xmlid and XMLID.fullmatch(xmlid) and self.env.ref(xmlid, raise_if_not_found=False):
            return xmlid
        return False

    def _way_to_start(self):
        """:return: the steps of the pointer through the menus to the start
        screen, for a reader that cannot open it by itself (the AI); empty
        when the start screen is in no menu the person can open"""
        self.ensure_one()
        action = self._start_action_xmlid() and self.env.ref(self.action_xmlid)
        if not action:
            return []
        menu = _visible_menus(self.env).filtered(lambda candidate: candidate.action == action)[:1]
        path = menu_path(self.env, menu) if menu else []
        return menu_steps(self.env, path, []) if path else []

    def _pointer_steps(self):
        """:return: ``(steps, skipped)``: the steps in the form of the pointer,
        and, for each step that cannot be shown here, a sentence saying so"""
        self.ensure_one()
        steps, skipped = [], []
        for step in self.step_ids.sorted(lambda candidate: (candidate.sequence, candidate.id)):
            shown, reason = step._pointer_steps()
            if shown:
                steps.extend(shown)
            else:
                skipped.append(reason)
        return steps, skipped

    # --------------------------------------------------------------- recording

    @api.model
    def record(self, name, steps, phrases_en="", phrases_ar="", start=None, group_ids=None):
        """Save what a manager recorded.

        :param steps: ``[{"kind", "name", "note", "label", "res_model",
            "view_type", "action", "menu_id"}]``
        :param start: ``{"xml_id", "id"}`` of the action the recording began on
        :return: the id of the tour"""
        if not self.env.user.has_group("nudge.group_nudge_manager"):
            raise AccessError(_("Only Nudge managers record tours."))
        name = " ".join(str(name or "").split())[:120]
        if not name:
            raise UserError(_("A tour needs a name."))
        if not isinstance(steps, list) or not steps:
            raise UserError(_("Nothing was recorded: click through the screens first."))
        clean = []
        for position, step in enumerate(steps[:MAX_STEPS], start=1):
            made = self.env["nudge.tour.step"]._from_record(step, position * 10)
            if made:
                clean.append((0, 0, made))
        if not clean:
            raise UserError(_("Nothing that can be shown again was recorded."))
        start = start if isinstance(start, dict) else {}
        values = {
            "name": name,
            "phrases_en": "\n".join(_phrases(str(phrases_en or "")[:1000])),
            "phrases_ar": "\n".join(_phrases(str(phrases_ar or "")[:1000])),
            "step_ids": clean,
            "action_xmlid": self._xmlid_of_action(start),
            "company_id": self.env.company.id,
        }
        if group_ids:
            values["group_ids"] = [(6, 0, [int(group) for group in group_ids if isinstance(group, int)])]
        return self.create(values).id

    @api.model
    def _xmlid_of_action(self, start):
        xmlid = str(start.get("xml_id") or "")
        if xmlid and XMLID.fullmatch(xmlid) and self.env.ref(xmlid, raise_if_not_found=False):
            return xmlid
        action_id = start.get("id")
        if isinstance(action_id, int):
            # sudo: only the external id of the action the manager has open is read.
            # It is kept under the kind of action (act_window, server...)
            data = self.env["ir.model.data"].sudo().search(
                [("model", "=like", "ir.actions.%"), ("res_id", "=", action_id)], limit=1)
            return f"{data.module}.{data.name}" if data else ""
        return ""


class NudgeTourStep(models.Model):
    _name = "nudge.tour.step"
    _description = "Nudge Recorded Tour Step"
    _order = "sequence, id"

    tour_id = fields.Many2one("nudge.tour", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    kind = fields.Selection(KINDS, required=True, default="button")
    name = fields.Char(
        string="Target", required=True,
        help="The external id (or the path) of a menu, the name of a button, a field or a tab, "
             "or the name of a standard control.")
    label = fields.Char(string="Label", translate=True, help="What the person sees on the element.")
    note = fields.Text(string="Note", translate=True, help="What the pointer says at this step.")
    res_model = fields.Char(string="Model", help="The model of the screen the step is on.")
    view_type = fields.Char(string="View", help="The kind of view the step is on: form, list...")
    action_xmlid = fields.Char(string="Screen", help="The action the step is on, when it has an external id.")
    # Not required: the column is added to steps that exist
    skip_reason = fields.Char(
        string="Skipped", copy=False,
        help="Why the step is not shown: filled in when a recipe came from a database that has what this one lacks.")

    # --------------------------------------------------------------- recording

    @api.model
    def _from_record(self, step, sequence):
        """:return: the values of a step as recorded by the browser, or an
        empty dict when it is not one that can be shown again. Nothing the
        browser sends is trusted: names are checked against fixed patterns."""
        if not isinstance(step, dict):
            return {}
        kind = step.get("kind")
        name = str(step.get("name") or "")
        label = " ".join(str(step.get("label") or "").split())[:120]
        if kind == "menu":
            name = self._menu_reference(step.get("menu_id"), step.get("xmlid"))
        elif kind == "control":
            if name not in CONTROLS:
                return {}
        elif kind in ("button", "field", "tab"):
            if not NAME.fullmatch(name):
                return {}
        else:
            return {}
        if not name:
            return {}
        res_model = str(step.get("res_model") or "")
        view_type = str(step.get("view_type") or "")
        action = str(step.get("action") or "")
        return {
            "sequence": sequence,
            "kind": kind,
            "name": name,
            "label": label,
            "note": " ".join(str(step.get("note") or "").split())[:MAX_NOTE],
            "res_model": res_model if re.fullmatch(r"[a-z0-9_.]{1,128}", res_model) else "",
            "view_type": view_type if view_type in VIEW_TYPES else "",
            "action_xmlid": action if XMLID.fullmatch(action) else "",
        }

    @api.model
    def _menu_reference(self, menu_id, xmlid=""):
        """:return: how a menu is kept in a tour: its external id, else its
        path in English; empty for a menu the person cannot open"""
        xmlid = str(xmlid or "")
        try:
            menu_id = int(menu_id)
        except (TypeError, ValueError):
            found = self.env.ref(xmlid, raise_if_not_found=False) if XMLID.fullmatch(xmlid) else None
            menu_id = found.id if found is not None and found._name == "ir.ui.menu" else 0
        menu = _visible_menus(self.env).filtered(lambda candidate: candidate.id == menu_id)
        if not menu:
            return ""
        return menu.get_external_id().get(menu.id) or menu.with_context(lang="en_US").complete_name or ""

    # ------------------------------------------------------------------ replay

    def _find_menu(self):
        """:return: the menu the person may open that this step names"""
        self.ensure_one()
        Menu = self.env["ir.ui.menu"]
        menus = _visible_menus(self.env)
        if "." in self.name and XMLID.fullmatch(self.name):
            menu = self.env.ref(self.name, raise_if_not_found=False)
            if menu and menu._name == "ir.ui.menu" and menu in menus:
                return menu
            return Menu
        return menus.filtered(
            lambda candidate: candidate.with_context(lang="en_US").complete_name == self.name)[:1]

    def _on_screen(self):
        """:return: whether what the step names is in the screen it was
        recorded on, on this database; unknown counts as yes, the pointer
        tells a person that something is not there"""
        self.ensure_one()
        model = self.res_model
        if not model:
            return True
        if model not in self.env:
            return False
        if self.kind == "field":
            return self.name in self.env[model]._fields
        if self.kind in ("button", "tab") and self.view_type:
            try:
                arch = self.env[model].get_view(view_type=self.view_type)["arch"]
                tag, attribute = ("button", "name") if self.kind == "button" else ("page", "name")
                nodes = etree.fromstring(arch).xpath(f"//{tag}[@{attribute}=$name]", name=self.name)
            except Exception:  # noqa: BLE001 - a view that cannot be read is not a reason to skip
                return True
            return bool(nodes)
        return True

    def _pointer_steps(self):
        """:return: ``(steps, reason)``: the steps of the pointer for this one,
        or no step and the sentence that says why it is skipped"""
        self.ensure_one()
        env = self.env
        label = self.label or self.name
        note = (self.note or "").strip()
        if self.skip_reason:
            return [], _("Skipped “%s”: %s", label, self.skip_reason)
        if self.kind == "menu":
            menu = self._find_menu()
            path = menu_path(env, menu) if menu else []
            if not path:
                return [], _("Skipped the menu “%s”: it is not on this database, or not open to you.", label)
            steps = menu_steps(env, path, [])
            if note:
                steps[-1]["text"] = note
            return steps, ""
        if self.kind == "control":
            if self.name not in CONTROLS:
                return [], _("Skipped a control that does not exist here.")
            return [self._step(note or _("Press this."))], ""
        if not self._on_screen():
            return [], _("Skipped “%s”: this database does not have it.", label)
        if not note:
            if self.kind == "button":
                note = _("Click “%s”.", label)
            elif self.kind == "field":
                note = _("Fill in “%s”.", label)
            else:
                note = _("Open the tab “%s”.", label)
        return [self._step(note)], ""

    def _step(self, text):
        return {"target": {"type": self.kind, "name": self.name}, "text": text, "advance": "auto"}

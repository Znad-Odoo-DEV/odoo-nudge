"""The coach of a new user, no AI.

For a user whose first login is within the last 14 days, or whom a manager puts in training, Nudge keeps
a small state: the action ids of the key screens opened (a set, no time per visit), the tours required and
the tours taken. The panel shows it as "Your first week"; the managers see the progress of everyone in
training under Nudge / Training. A plain user reads their own row only.
"""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from ..tools import coach


class NudgeCoachScreen(models.Model):
    _name = "nudge.coach.screen"
    _inherit = ["nudge.recipe.mixin"]
    _description = "Nudge Key Screen"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True, help="What the screen is called in the card of the new user.")
    module_name = fields.Char(string="App", help="Technical name of the app the screen belongs to, such as sale.")
    action_xmlid = fields.Char(
        string="Action", required=True,
        help="External id of the window action, such as sale.action_quotations_with_onboarding. "
             "The screen is only counted for a person who has a menu that opens it.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    @api.constrains("action_xmlid")
    def _check_action(self):
        for screen in self:
            if " " in (screen.action_xmlid or "") or "." not in (screen.action_xmlid or ""):
                raise UserError(_("An action is written module.name, such as sale.action_orders."))


class NudgeCoach(models.Model):
    _name = "nudge.coach"
    _description = "Nudge Coaching State"
    _order = "state, id desc"
    _rec_name = "user_id"

    user_id = fields.Many2one("res.users", required=True, ondelete="cascade", index=True)
    state = fields.Selection(
        [("training", "In training"), ("trained", "Trained")], default="training", required=True)
    is_manual = fields.Boolean(
        string="Put in training by a manager", help="Otherwise the coach only follows the first 14 days.")
    first_login = fields.Datetime(readonly=True)
    seen_action_ids = fields.Json(
        string="Action ids opened", default=list, copy=False,
        help="The action ids of the key screens the person opened. No time is kept for a visit.")
    required_tour_ids = fields.Many2many(
        "nudge.tour", "nudge_coach_required_tour_rel", "coach_id", "tour_id", string="Tours to take")
    done_tour_ids = fields.Many2many(
        "nudge.tour", "nudge_coach_done_tour_rel", "coach_id", "tour_id", string="Tours taken")
    last_activity = fields.Date(readonly=True)
    trained_on = fields.Date(readonly=True)
    progress = fields.Integer(string="Progress %", compute="_compute_progress")
    opened_count = fields.Integer(string="Screens opened", compute="_compute_progress")
    screen_count = fields.Integer(string="Key screens", compute="_compute_progress")
    tours_taken = fields.Char(string="Tours taken (count)", compute="_compute_progress")

    if hasattr(models, "Constraint"):
        _user_unique = models.Constraint("unique(user_id)", "Each user has one coaching state.")
    else:  # Odoo 17 and 18
        _sql_constraints = [("user_unique", "unique(user_id)", "Each user has one coaching state.")]

    # ------------------------------------------------------------------ rows

    def _screens(self, user):
        """:return: the key screens of the role of ``user``"""
        return coach.key_screens(self.env, user, self.env["nudge.coach.screen"].search([]))

    @api.depends("user_id", "seen_action_ids", "done_tour_ids", "required_tour_ids")
    def _compute_progress(self):
        for row in self:
            if not row.user_id:
                row.progress = row.opened_count = row.screen_count = 0
                row.tours_taken = ""
                continue
            screens = row._screens(row.user_id)
            row.opened_count, row.screen_count, row.progress = coach.progress(row.seen_action_ids, screens)
            required = row.required_tour_ids
            taken = len(row.done_tour_ids & required) if required else len(row.done_tour_ids)
            row.tours_taken = f"{taken}/{len(required)}" if required else str(taken)

    def _is_open(self):
        """:return: whether the coach follows this person now"""
        self.ensure_one()
        if not self.env.company.sudo().nudge_coach_enabled:
            return False
        return self.state == "training" and (self.is_manual or (
            bool(self.first_login) and coach.is_new(self.env, self.user_id)))

    # ------------------------------------------------------------------ the person

    @api.model
    def _own(self, create=False):
        """:return: the row of the person; made at the first call within their 14 days when ``create``"""
        user = self.env.user
        if not user._is_internal():
            # The coach follows people of the company; a portal or public user has no row and gets none
            return self.browse()
        row = self.search([("user_id", "=", user.id)], limit=1)
        if row or not create:
            return row
        first = coach.first_login(self.env, user)
        if not first or not coach.is_new(self.env, user):
            return row
        # sudo: a plain user cannot create the row, which holds nothing but their own progress
        return self.sudo().create({"user_id": user.id, "first_login": first})

    @api.model
    def status(self):
        """:return: whether the coach follows the person; cheap, asked once when the page opens"""
        row = self._own(create=True)
        return {"active": bool(row) and row._is_open()}

    @api.model
    def get_card(self):
        """:return: what the card "Your first week" shows; ``{"active": False}`` when the coach does
        not follow the person"""
        row = self._own(create=True)
        if not row or not row._is_open():
            return {"active": False}
        screens = self._screens(self.env.user)
        seen = set(row.seen_action_ids or [])
        tours = self._tours()
        opened, total, percent = coach.progress(seen, screens)
        step = coach.suggest(screens, seen, row.required_tour_ids.ids, row.done_tour_ids.ids, tours)
        apps = {}
        for screen in screens:
            apps.setdefault(screen["app"], []).append(
                {"name": screen["name"], "seen": screen["action_id"] in seen})
        return {
            "active": True,
            "opened": opened, "total": total, "percent": percent,
            "next": self._next_line(step),
            "apps": [{"name": name, "screens": items} for name, items in apps.items()],
            "tours": [dict(tour, done=tour["id"] in row.done_tour_ids.ids)
                      for tour in tours if tour["id"] in row.required_tour_ids.ids],
        }

    @api.model
    def _tours(self):
        """:return: the tours the person may replay, for the suggestions"""
        return [{"id": tour.id, "name": tour.name, "steps": tour.step_count,
                 "xmlid": tour.action_xmlid or "", "module": tour.module_name or ""}
                for tour in self.env["nudge.tour"]._replayable()]

    def _next_line(self, step):
        if not step:
            return None
        tour, screen = step["tour"], step["screen"]
        if step["kind"] == "tour":
            text = _("Take the tour “%(tour)s”: your manager asks for it.", tour=tour["name"])
        elif tour:
            text = _(
                "You have not opened %(screen)s yet: take the %(minutes)s-minute tour.",
                screen=screen["name"], minutes=coach.minutes_of(tour))
        else:
            text = _("You have not opened %(screen)s yet: open it and have a look.", screen=screen["name"])
        return {"text": text, "tour_id": step["tour_id"], "xmlid": screen["xmlid"] if screen else "",
                "required": step["required"]}

    @api.model
    def record_visits(self, action_ids):
        """The panel tells which actions the person opened. Only the key screens of their role are kept.

        :return: the number of key screens opened so far"""
        row = self._own()
        if not row or not row._is_open():
            return 0
        wanted = {value for value in (action_ids if isinstance(action_ids, list) else [])[:coach.MAX_VISITS]
                  if isinstance(value, int) and not isinstance(value, bool) and value > 0}
        known = {screen["action_id"] for screen in self._screens(self.env.user)}
        seen = set(row.seen_action_ids or [])
        fresh = (wanted & known) - seen
        if fresh:
            # sudo: the row is the person's own bookkeeping; they may not write it directly
            row.sudo().write({
                "seen_action_ids": sorted(seen | fresh)[:coach.MAX_SEEN],
                "last_activity": fields.Date.context_today(self),
            })
        return len(seen | fresh)

    @api.model
    def tour_done(self, tour_id):
        """The person went through a tour to its end."""
        row = self._own()
        if not row or not row._is_open():
            return False
        # A search: the record rules of the tour apply to the person
        tour = self.env["nudge.tour"].search([("id", "=", int(tour_id or 0))])
        if not tour:
            return False
        row.sudo().write({"done_tour_ids": [(4, tour.id)], "last_activity": fields.Date.context_today(self)})
        return True

    @api.model
    def finish(self):
        """“Done with training”: the card goes away."""
        row = self._own()
        if row:
            row.sudo()._mark_trained()
        return True

    def _mark_trained(self):
        self.write({"state": "trained", "trained_on": fields.Date.context_today(self)})

    # ------------------------------------------------------------------ the manager

    @api.model
    def _check_manager(self):
        if not self.env.user.has_group("nudge.group_nudge_manager"):
            raise AccessError(_("Only a Nudge manager does this."))

    def action_mark_trained(self):
        self._check_manager()
        self._mark_trained()
        return True

    def action_back_to_training(self):
        self._check_manager()
        self.write({"state": "training", "is_manual": True, "trained_on": False})
        return True

    @api.model_create_multi
    def create(self, vals_list):
        manager = self.env.user.has_group("nudge.group_nudge_manager")
        for vals in vals_list:
            if manager and "is_manual" not in vals:
                vals["is_manual"] = True
            if not vals.get("first_login") and vals.get("user_id"):
                vals["first_login"] = coach.first_login(self.env, self.env["res.users"].browse(vals["user_id"]))
        return super().create(vals_list)


class NudgeCoachAssign(models.TransientModel):
    _name = "nudge.coach.assign"
    _description = "Assign Tours to Users in Training"

    coach_ids = fields.Many2many("nudge.coach", string="Users")
    tour_ids = fields.Many2many("nudge.tour", string="Tours to take")

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if self.env.context.get("active_model") == "nudge.coach":
            values["coach_ids"] = [(6, 0, self.env.context.get("active_ids") or [])]
        return values

    def action_assign(self):
        self.ensure_one()
        self.env["nudge.coach"]._check_manager()
        self.coach_ids.write({"required_tour_ids": [(4, tour.id) for tour in self.tour_ids]})
        return {"type": "ir.actions.act_window_close"}

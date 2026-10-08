"""What Nudge does by itself, on a schedule or when records appear.

"Every morning at 8, send me the overdue invoices", "when a purchase order
above 50,000 is created, compare its prices with the last ones": the person
asks once, and Nudge does it again and again, as them, in the background.

A task reads; it never changes anything by itself. What it finds worth
changing waits as a card for its owner, like any request worked on in the
background. Before the AI is asked, the conditions of the task are counted:
when nothing matches, or nothing is new, nothing is spent.
"""
import logging
from datetime import timedelta

import pytz
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import plaintext2html

_logger = logging.getLogger(__name__)

TASKS_PARAM = "nudge.tasks_per_user"
DEFAULT_TASKS = 5
# A conversation of a task is started again before it grows too long to read
MAX_TASK_MESSAGES = 60
# Records named to the model when a watched kind of record has new ones
MAX_NEW_IDS = 50
UNITS = {
    "hours": "hours",
    "days": "days",
    "weeks": "weeks",
    "months": "months",
}


class NudgeTask(models.Model):
    _name = "nudge.task"
    _description = "Nudge Scheduled Task"
    _order = "active desc, next_run, id"

    name = fields.Char(required=True)
    instruction = fields.Text(
        required=True,
        help="What Nudge is asked each time, in the words of the person.")
    user_id = fields.Many2one(
        "res.users", string="Owner", required=True, index=True, readonly=True,
        default=lambda self: self.env.user, ondelete="cascade")
    company_id = fields.Many2one(
        "res.company", required=True, readonly=True, default=lambda self: self.env.company)
    active = fields.Boolean(default=True)
    kind = fields.Selection(
        [("schedule", "On a Schedule"), ("watch", "When Records Appear")],
        required=True, default="schedule")
    interval_number = fields.Integer(string="Every", default=1)
    interval_type = fields.Selection(
        [("hours", "Hours"), ("days", "Days"), ("weeks", "Weeks"), ("months", "Months")],
        string="Unit", default="days")
    hour = fields.Integer(
        string="At", default=8, help="The hour of the day, in the time zone of the owner.")
    weekday = fields.Selection(
        [("0", "Monday"), ("1", "Tuesday"), ("2", "Wednesday"), ("3", "Thursday"),
         ("4", "Friday"), ("5", "Saturday"), ("6", "Sunday")],
        help="For a weekly task: the day it runs on.")
    model_name = fields.Char(
        string="Records Of",
        help="When records appear: the kind of record watched. On a schedule: the kind of "
             "record counted first, and nothing is asked of the AI when none matches.")
    conditions = fields.Json(
        help="The conditions the records must meet, as the agent reads them.")
    match = fields.Selection([("all", "All"), ("any", "Any")], default="all")
    conditions_text = fields.Char(string="Conditions", compute="_compute_conditions_text")
    send_email = fields.Boolean(
        string="Email Me", help="The answer is also sent to the owner by email.")
    next_run = fields.Datetime(index=True, readonly=True)
    last_run = fields.Datetime(readonly=True)
    last_check = fields.Datetime(
        readonly=True, help="When records appear: records created after it are new.")
    last_result = fields.Text(readonly=True)
    run_count = fields.Integer(string="Runs", readonly=True)
    skip_count = fields.Integer(
        string="Nothing to Do", readonly=True,
        help="Times the conditions matched nothing: the AI was not asked, nothing was spent.")
    conversation_id = fields.Many2one("nudge.conversation", readonly=True, ondelete="set null")

    @api.depends("conditions", "match")
    def _compute_conditions_text(self):
        for task in self:
            parts = [f"{entry.get('field')} {entry.get('operator')} {entry.get('value')}"
                     for entry in task.conditions or [] if isinstance(entry, dict)]
            task.conditions_text = (" or " if task.match == "any" else " and ").join(parts)

    @api.constrains("interval_number", "interval_type", "hour", "kind")
    def _check_schedule(self):
        for task in self:
            if task.kind == "schedule" and (task.interval_number or 0) < 1:
                raise ValidationError(_("A task runs every 1 unit of time or more."))
            if not 0 <= (task.hour or 0) <= 23:
                raise ValidationError(_("The hour is between 0 and 23."))
            if task.kind == "watch" and not task.model_name:
                raise ValidationError(_("A task that watches records needs their kind."))

    # ------------------------------------------------------------------
    # Made by the person
    # ------------------------------------------------------------------
    @api.model
    def _max_per_user(self):
        raw = self.env["ir.config_parameter"].sudo().get_param(TASKS_PARAM)
        try:
            return max(0, int(raw)) if raw not in (None, False, "") else DEFAULT_TASKS
        except ValueError:
            return DEFAULT_TASKS

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            if not self.env["nudge.conversation"]._can_use_agent():
                raise AccessError(_("Scheduled tasks are for those who may use Agent mode."))
            limit = self._max_per_user()
            mine = self.with_context(active_test=False).search_count([("user_id", "=", self.env.uid)])
            if mine + len(vals_list) > limit:
                raise UserError(_(
                    "You have %(count)s tasks: the limit set by your administrator is %(limit)s. "
                    "Delete one first.", count=mine, limit=limit))
            for vals in vals_list:
                # A task is its owner's: it runs with their rights only
                vals.pop("user_id", None)
                vals.pop("company_id", None)
        tasks = super().create(vals_list)
        now = fields.Datetime.now()
        for task in tasks:
            task.sudo().write({"next_run": task._next_after(now), "last_check": now})
        return tasks

    def write(self, vals):
        if not self.env.su and ({"user_id", "company_id"} & set(vals)):
            raise AccessError(_("A task stays with the one who made it."))
        result = super().write(vals)
        if {"interval_number", "interval_type", "hour", "weekday", "kind", "active"} & set(vals):
            now = fields.Datetime.now()
            for task in self.filtered("active"):
                task.sudo().next_run = task._next_after(now)
        return result

    def _next_after(self, moment):
        """:return: when the task runs next after ``moment``, in UTC"""
        self.ensure_one()
        if self.kind == "watch":
            # Watched records are looked at whenever the scheduled action passes
            return moment
        zone = pytz.timezone(self.user_id.tz or "UTC")
        local = pytz.utc.localize(moment).astimezone(zone)
        number = max(1, self.interval_number or 1)
        if self.interval_type == "hours":
            following = local + relativedelta(hours=number)
        else:
            step = relativedelta(**{UNITS[self.interval_type or "days"]: number})
            following = local.replace(hour=self.hour or 0, minute=0, second=0, microsecond=0)
            if self.interval_type == "weeks" and self.weekday:
                following += timedelta(days=(int(self.weekday) - following.weekday()) % 7)
            while following <= local:
                following += step
        return following.astimezone(pytz.utc).replace(tzinfo=None)

    def action_run_now(self):
        for task in self:
            task.sudo().next_run = fields.Datetime.now()
        self.env.ref("nudge.cron_tasks").sudo()._trigger()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"type": "info", "message": _(
                "The task runs in a moment. Nudge tells you when it is done.")},
        }

    def action_open_conversation(self):
        self.ensure_one()
        return self.conversation_id.get_formview_action() if self.conversation_id else False

    # ------------------------------------------------------------------
    # Run by the scheduled action, as the owner
    # ------------------------------------------------------------------
    @api.model
    def _cron_run(self):
        """Start the tasks whose time has come, each as a request in the
        background of its owner."""
        now = fields.Datetime.now()
        due = self.sudo().search([("next_run", "<=", now)], order="next_run, id", limit=50)
        for task in due:
            try:
                with self.env.cr.savepoint():
                    task._start(now)
            except Exception as error:  # noqa: BLE001
                _logger.info("Nudge: task %s could not start: %s", task.id, error)
                task.write({
                    "last_result": _("Could not start: %s", error),
                    "next_run": task._next_after(now),
                })

    def _as_owner(self):
        """:return: the task in the environment of its owner: their rights,
        their companies, their language"""
        self.ensure_one()
        user = self.sudo().user_id
        context = dict(user.with_user(user).context_get())
        context["allowed_company_ids"] = ((self.company_id & user.company_ids) | user.company_id).ids
        return self.env(user=user.id, su=False, context=context)

    def _start(self, now):
        """Count first; when there is something to do, ask Nudge as the owner."""
        self.ensure_one()
        from ..tools.records import build_domain, get_model  # noqa: PLC0415

        task = self.sudo()
        env = task._as_owner()
        values = {"next_run": task._next_after(now), "last_run": now}
        if not task.user_id.active or not env["nudge.conversation"]._can_use_agent():
            task.write(dict(values, active=False, last_result=_(
                "Stopped: the owner may no longer use Agent mode.")))
            return
        note = ""
        if task.model_name:
            model = get_model(env, task.model_name)
            domain = build_domain(model, task.conditions or [], task.match)
            if task.kind == "watch":
                # What was created since the last look, among which what matches
                created = model.search([("create_date", ">", task.last_check or now)], order="id")
                found = created.filtered_domain(domain)
                values["last_check"] = now
            else:
                found = model.search(domain, limit=MAX_NEW_IDS + 1)
            if not found:
                task.write(dict(values, skip_count=task.skip_count + 1,
                                last_result=_("Nothing matched: the AI was not asked.")))
                return
            if task.kind == "watch":
                more = f" (and more: {len(found)} in all)" if len(found) > MAX_NEW_IDS else ""
                note = (f"\n\n[New {model._name} records since the last check: ids "
                        f"{', '.join(map(str, found[:MAX_NEW_IDS].ids))}{more}]")
        conversation = task.conversation_id
        if not conversation or conversation.message_count >= MAX_TASK_MESSAGES or \
                conversation.user_id != task.user_id:
            conversation = env["nudge.conversation"].create({"name": task.name})
        conversation = env["nudge.conversation"].browse(conversation.id)
        conversation._chat_prepare(task.instruction + note, mode="agent", background=True)
        conversation.sudo().task_id = task
        task.write(dict(values, conversation_id=conversation.id, run_count=task.run_count + 1,
                        last_result=_("Started; Nudge tells you when it is done.")))

    def _deliver(self, run):
        """The request of a run of this task is over: its answer reaches the
        owner where they asked for it, besides the news of the top bar."""
        self.ensure_one()
        task = self.sudo()
        answer = self.env["nudge.message"].sudo().search([
            ("conversation_id", "=", run.conversation_id.id),
            ("role", "=", "assistant"),
            ("content", "!=", False),
        ], order="id desc", limit=1)
        text = answer.content or ""
        task.last_result = text[:2000] or task.last_result
        if task.send_email and text and "mail.mail" in self.env and task.user_id.email:
            self.env["mail.mail"].sudo().create({
                "subject": _("Nudge: %s", task.name),
                "email_to": task.user_id.email_formatted,
                "body_html": plaintext2html(text),
                "auto_delete": True,
            })

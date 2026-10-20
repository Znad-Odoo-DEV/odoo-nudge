"""What Nudge does by itself, on a schedule or when records appear.

"Every morning at 8, send me the overdue invoices", "when a purchase order
above 50,000 is created, compare its prices with the last ones": the person
asks once, and Nudge does it again and again, as them, in the background.

A task reads; it never changes anything by itself. What it finds worth
changing waits as a card for its owner, like any request worked on in the
background. Before the AI is asked, the conditions of the task are counted:
when nothing matches, or nothing is new, nothing is spent.
"""
import ast
import logging
import time
from datetime import timedelta

import pytz
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, release
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import plaintext2html

_logger = logging.getLogger(__name__)

TASKS_PARAM = "nudge.tasks_per_user"
DEFAULT_TASKS = 5
# A conversation of a task is started again before it grows too long to read
MAX_TASK_MESSAGES = 60
# Records named to the model when a watched kind of record has new ones
MAX_NEW_IDS = 50
# Rows of a grouped measure kept in the answer of a rule
MAX_RULE_ROWS = 25
# Seconds a pass of the scheduled action works before the rest waits for the next pass
CRON_SECONDS = 60
# Digests of one hour are spread over this many minutes, by user
DIGEST_SPREAD_MINUTES = 50
UNITS = {
    "hours": "hours",
    "days": "days",
    "weeks": "weeks",
    "months": "months",
}


class NudgeTask(models.Model):
    _name = "nudge.task"
    _inherit = ["nudge.recipe.mixin"]
    _description = "Nudge Scheduled Task"
    _order = "active desc, next_run, id"

    name = fields.Char(required=True)
    # Not required in the database: a rule has no instruction (checked for AI tasks below)
    instruction = fields.Text(
        help="What Nudge is asked each time, in the words of the person.")
    engine = fields.Selection(
        [("ai", "AI"), ("rule", "Rule (no AI)")], default="ai", string="Done By",
        help="A rule counts or adds up records by itself and never asks an AI provider.")
    rule_model_id = fields.Many2one(
        "ir.model", string="Records Of", ondelete="set null", domain="[('id', 'in', available_model_ids)]",
        help="The kind of records the rule looks at. Only those its owner may read.")
    available_model_ids = fields.Many2many(
        "ir.model", compute="_compute_available_models",
        help="The kinds of records the person may build a rule on: not transient, readable by them.")
    rule_domain = fields.Char(
        string="Records Matching", default="[]",
        help="The records the rule looks at, as in any filter. Read as the owner: "
             "what they may not see is not counted.")
    rule_measure = fields.Selection(
        [("count", "Count"), ("sum", "Sum"), ("avg", "Average")], default="count", string="Measure")
    rule_field_id = fields.Many2one(
        "ir.model.fields", string="Of the Field", ondelete="set null",
        domain="[('model_id', '=', rule_model_id), ('ttype', 'in', ['integer', 'float', 'monetary']), "
               "('store', '=', True)]")
    rule_groupby_id = fields.Many2one(
        "ir.model.fields", string="Group By", ondelete="set null",
        domain="[('model_id', '=', rule_model_id), ('store', '=', True), ('ttype', 'in', "
               "['many2one', 'selection', 'boolean', 'date', 'datetime', 'char'])]")
    rule_condition = fields.Selection(
        [("always", "Always"), ("above", "Above a Threshold"), ("below", "Below a Threshold"),
         ("changed", "Changed Since the Last Run")], default="always", string="Report")
    rule_threshold = fields.Float(string="Threshold")
    rule_last = fields.Json(string="Last Values", readonly=True, copy=False)
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
        string="Record Type",
        help="When records appear: the kind of record watched. On a schedule: the kind of "
             "record counted first, and nothing is asked of the AI when none matches.")
    conditions = fields.Json(
        string="Condition Data",
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

    def _compute_available_models(self):
        from ..tools.records import is_closed  # noqa: PLC0415
        # sudo: the list of models is read to be filtered; what is offered is
        # only what the person may read (rights, and the models Nudge keeps closed)
        models = self.env["ir.model"].sudo().search([("transient", "=", False)])
        offered = models.filtered(lambda line: line.model in self.env
                                  and not self.env[line.model]._abstract
                                  and not is_closed(self.env, line.model)
                                  and self.env[line.model].has_access("read"))
        for task in self:
            task.available_model_ids = offered

    @api.onchange("rule_model_id")
    def _onchange_rule_model(self):
        for task in self:
            task.model_name = task.rule_model_id.model or False
            task.rule_domain = "[]"
            task.rule_field_id = task.rule_groupby_id = False

    @api.constrains("engine", "instruction", "model_name", "rule_domain", "rule_measure", "rule_field_id")
    def _check_rule(self):
        for task in self:
            if task.engine in ("digest", "outliers"):
                continue
            if task.engine != "rule":
                if not (task.instruction or "").strip():
                    raise ValidationError(self.env._("A task needs its instruction."))
                continue
            if not task.rule_model_id or task.model_name != task.rule_model_id.sudo().model:
                raise ValidationError(self.env._("A rule needs the kind of records it looks at."))
            from ..tools.records import Refused, get_model  # noqa: PLC0415
            try:
                get_model(task.env, task.model_name)
            except Refused as error:
                raise ValidationError(str(error)) from None
            if task.rule_measure in ("sum", "avg") and not task.rule_field_id:
                raise ValidationError(self.env._("Choose the field to add up or average."))
            task._rule_domain_list()

    def _rule_domain_list(self):
        """:return: the domain of the rule as a list, checked
        :raise ValidationError: when it cannot be read or reaches a secret field"""
        from ..tools.records import is_secret, target_field  # noqa: PLC0415
        self.ensure_one()
        try:
            domain = ast.literal_eval(self.rule_domain or "[]")
            if not isinstance(domain, list):
                raise ValueError
        except (ValueError, SyntaxError):
            raise ValidationError(self.env._("The records of the rule could not be read.")) from None
        if self.model_name and self.model_name in self.env:
            model = self.env[self.model_name]
            for term in domain:
                if isinstance(term, (tuple, list)) and len(term) == 3:
                    try:
                        secret = is_secret(target_field(model, term[0]))
                    except Exception:  # noqa: BLE001
                        raise ValidationError(self.env._("Unknown field: %s", term[0])) from None
                    if secret:
                        raise ValidationError(self.env._("Nothing can be looked for in %s.", term[0]))
        return domain

    @api.constrains("interval_number", "interval_type", "hour", "kind")
    def _check_schedule(self):
        for task in self:
            if task.kind == "schedule" and (task.interval_number or 0) < 1:
                raise ValidationError(self.env._("A task runs every 1 unit of time or more."))
            if not 0 <= (task.hour or 0) <= 23:
                raise ValidationError(self.env._("The hour is between 0 and 23."))
            if task.kind == "watch" and not task.model_name:
                raise ValidationError(self.env._("A task that watches records needs their kind."))

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
                raise AccessError(self.env._("Scheduled tasks are for those who may use Agent mode."))
            limit = self._max_per_user()
            mine = self.with_context(active_test=False).search_count([("user_id", "=", self.env.uid)])
            if mine + len(vals_list) > limit:
                raise UserError(self.env._(
                    "You have %(count)s tasks: the limit set by your administrator is %(limit)s. "
                    "Delete one first.", count=mine, limit=limit))
            for vals in vals_list:
                # A task is its owner's: it runs with their rights only
                vals.pop("user_id", None)
                vals.pop("company_id", None)
        self._sync_model(vals_list)
        tasks = super().create(vals_list)
        now = fields.Datetime.now()
        for task in tasks:
            task.sudo().write({"next_run": task._next_after(now), "last_check": now})
        return tasks

    def write(self, vals):
        if not self.env.su and ({"user_id", "company_id"} & set(vals)):
            raise AccessError(self.env._("A task stays with the one who made it."))
        self._sync_model([vals])
        result = super().write(vals)
        if {"interval_number", "interval_type", "hour", "weekday", "kind", "active"} & set(vals):
            now = fields.Datetime.now()
            for task in self.filtered("active"):
                task.sudo().next_run = task._next_after(now)
        return result

    @api.model
    def _sync_model(self, vals_list):
        """The kind of records of a rule is chosen as a model; the technical
        name the rest of the task reads follows it."""
        for vals in vals_list:
            if vals.get("rule_model_id"):
                # sudo: a name only; the model itself is checked as the owner by the constraint
                vals["model_name"] = self.env["ir.model"].sudo().browse(vals["rule_model_id"]).model

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
            # The digests of one hour do not all start in the same pass: each owner has their
            # own minute inside the hour, so the provider is not asked for all at once
            minute = self.user_id.id % DIGEST_SPREAD_MINUTES if self.engine == "digest" else 0
            following = local.replace(hour=self.hour or 0, minute=minute, second=0, microsecond=0)
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
            "params": {"type": "info", "message": self.env._(
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
        Cron = self.env["ir.cron"]
        now = fields.Datetime.now()
        started = time.monotonic()
        tried = set()
        while True:
            due = self.sudo().search(
                [("next_run", "<=", now), ("id", "not in", list(tried))], order="next_run, id", limit=50)
            if not due:
                break
            task = due[0]
            tried.add(task.id)
            # One task at a time, in its own savepoint and its own transaction: a provider
            # call can take many seconds, and one failure must not undo the others
            try:
                with self.env.cr.savepoint():
                    task._start(now)
            except Exception as error:  # noqa: BLE001
                _logger.info("Nudge: task %s could not start: %s", task.id, error)
                task.write({
                    "last_result": self.env._("Could not start: %s", error),
                    "next_run": task._next_after(now),
                })
            Cron._commit_progress(1, remaining=max(len(due) - 1, 0))
            if time.monotonic() - started > CRON_SECONDS and len(due) > 1:
                # What is left is taken up by the next pass
                self.env.ref("nudge.cron_tasks").sudo()._trigger()
                break

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
        if task.engine == "rule":
            # No provider, so Agent mode is not needed to run; the owner must be there
            if not task.user_id.active:
                task.write(dict(values, active=False, last_result=self.env._("Stopped: the owner is archived.")))
                return
            return task._start_rule(env, now, values)
        if task.engine == "digest":
            if not task.user_id.active:
                task.write(dict(values, active=False, last_result=self.env._("Stopped: the owner is archived.")))
                return
            return task._start_digest(env, now, values)
        if task.engine == "outliers":
            if not task.user_id.active:
                task.write(dict(values, active=False, last_result=self.env._("Stopped: the owner is archived.")))
                return
            return task._start_outliers(env, now, values)
        if not task.user_id.active or not env["nudge.conversation"]._can_use_agent():
            task.write(dict(values, active=False, last_result=self.env._(
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
                                last_result=self.env._("Nothing matched: the AI was not asked.")))
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
                        last_result=self.env._("Started; Nudge tells you when it is done.")))

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
                "subject": self.env._("Nudge: %s", task.name),
                "email_to": task.user_id.email_formatted,
                "body_html": plaintext2html(text),
                "auto_delete": True,
            })

    # ------------------------------------------------------------------
    # A rule: counted by Odoo, as the owner; no provider is ever called
    # ------------------------------------------------------------------
    def _rule_link(self):
        """:return: the address that opens the records of the rule, filtered"""
        self.ensure_one()
        base = (self.env["ir.config_parameter"].sudo().get_param("web.base.url") or "").rstrip("/")
        action = self.env.ref("nudge.nudge_task_records_action").sudo().id
        if release.version_info[0] >= 18:
            return f"{base}/odoo/{self.id}/action-{action}"
        return f"{base}/web#action={action}&active_id={self.id}"

    def action_open_records(self):
        """:return: the list of the records of the rule, as the one who opens it"""
        self.ensure_one()
        from ..tools.records import get_model  # noqa: PLC0415
        model = get_model(self.env, self.model_name)
        domain = self._rule_domain_list()
        if self.kind == "watch" and self.last_run:
            domain = domain + [("create_date", ">", self.last_check or self.last_run)]
        return {
            "type": "ir.actions.act_window",
            "name": self.name,
            "res_model": model._name,
            "view_mode": "list,form",
            "views": [(False, "list"), (False, "form")],
            "domain": domain,
        }

    def _rule_label(self, field, value):
        if value in (None, False) and field.type != "boolean":
            return self.env._("Undefined")
        if field.type == "many2one":
            return value.display_name
        if field.type == "selection":
            return dict(field._description_selection(self.env)).get(value, str(value))
        if field.type == "boolean":
            return self.env._("Yes") if value else self.env._("No")
        return str(value)

    def _rule_measure(self):
        """:return: ``{label: value}`` of the rule; self is in the environment of the owner"""
        from ..tools.records import get_model, is_secret  # noqa: PLC0415
        self.ensure_one()
        model = get_model(self.env, self.model_name)
        domain = self._rule_domain_list()
        if self.kind == "watch":
            domain = domain + [("create_date", ">", self.last_check or fields.Datetime.now())]
        measure, field = self.rule_measure or "count", self.rule_field_id
        if measure != "count":
            if not field or field.name not in model._fields or is_secret(model._fields[field.name]):
                raise UserError(self.env._("The field to add up cannot be read."))
            aggregate = f"{field.name}:{'sum' if measure == 'sum' else 'avg'}"
        else:
            aggregate = "__count"
        group = self.rule_groupby_id
        total = self.env._("Total")
        if not group:
            if measure == "count":
                return {total: model.search_count(domain)}
            row = model._read_group(domain, [], [aggregate])
            return {total: (row[0][0] if row else 0) or 0}
        if group.name not in model._fields or is_secret(model._fields[group.name]):
            raise UserError(self.env._("The field to group by cannot be read."))
        gfield = model._fields[group.name]
        spec = f"{group.name}:month" if gfield.type in ("date", "datetime") else group.name
        out = {}
        for row in model._read_group(domain, [spec], [aggregate], limit=500):
            label = self._rule_label(gfield, row[0])
            out[label] = out.get(label, 0) + (row[1] or 0)
        return dict(sorted(out.items(), key=lambda item: -item[1])[:MAX_RULE_ROWS])

    def _start_rule(self, env, now, values):
        """Measure, decide whether it is worth reporting, and say it where the
        tasks of the AI say theirs."""
        self.ensure_one()
        task = self
        try:
            measured = task.with_env(env)._rule_measure()
        except Exception as error:  # noqa: BLE001
            task.write(dict(values, last_result=self.env._("Could not run: %s", error)))
            return
        previous = task.rule_last if isinstance(task.rule_last, dict) else None
        values["rule_last"] = measured
        if task.kind == "watch":
            values["last_check"] = now
        shown, before = dict(measured), ""
        condition, limit = task.rule_condition or "always", task.rule_threshold
        if condition == "above":
            shown = {k: v for k, v in measured.items() if v > limit}
        elif condition == "below":
            shown = {k: v for k, v in measured.items() if v < limit}
        elif condition == "changed" and previous is not None:
            if previous == measured:
                shown = {}
            else:
                before = self.env._("Before: %s", ", ".join(f"{k} {v}" for k, v in previous.items()) or "-")
        if not shown:
            task.write(dict(values, skip_count=task.skip_count + 1,
                            last_result=self.env._("Nothing to report.")))
            return
        lines = [task.name]
        for label, value in shown.items():
            value = f"{value:,.2f}".rstrip("0").rstrip(".") if isinstance(value, float) else f"{value:,}"
            lines.append(f"- {label}: {value}")
        if before:
            lines.append(before)
        lines.append(self.env._("Open the list: %s", task._rule_link()))
        return task._publish("\n".join(lines), env, values)

    def _publish(self, text, env, values):
        """Say ``text`` to the owner where the tasks say theirs: a message of
        their conversation and a run that ends, which tells the top bar and
        sends the email."""
        self.ensure_one()
        task = self
        # The conversation is the owner's; its answer and run are written by the
        # system (the log of Nudge is written by Nudge only)
        conversation = task.conversation_id
        if not conversation or conversation.message_count >= MAX_TASK_MESSAGES or \
                conversation.user_id != task.user_id:
            conversation = env["nudge.conversation"].create({"name": task.name})
        conversation = conversation.sudo()
        conversation.task_id = task
        self.env["nudge.message"].sudo().create({
            "conversation_id": conversation.id, "role": "assistant", "content": text, "mode": "agent"})
        run = self.env["nudge.run"].sudo().create({
            "name": task.name, "conversation_id": conversation.id, "user_id": task.user_id.id,
            "company_id": task.company_id.id, "state": "running", "is_background": True})
        task.write(dict(values, conversation_id=conversation.id, run_count=task.run_count + 1,
                        last_result=text[:2000]))
        conversation.run_id = run
        run._end("done")

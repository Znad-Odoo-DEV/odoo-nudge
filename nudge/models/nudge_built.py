"""What Nudge built, in one list: made by Developer mode (and by the builders that work with it), tagged
the way views and fields always were (an xml id of the module ``nudge_custom``) and written here with who
asked, when and in which step. Nothing is ever deleted from here: a thing is switched off or archived,
after a card, and the log keeps the values before and after."""
import logging
import re
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

# Where what Nudge makes is registered
CUSTOM_MODULE = "nudge_custom"
KINDS = [
    ("automation", "Automation Rule"),
    ("server_action", "Server Action"),
    ("scheduled_action", "Scheduled Action"),
    ("email_template", "Email Template"),
    ("view", "View"),
    ("button", "Button"),
    ("field", "Field"),
    ("report", "Report"),
    ("approval_rule", "Approval Rule"),
]
MODEL_KINDS = {
    "base.automation": "automation",
    "ir.actions.server": "server_action",
    "ir.cron": "scheduled_action",
    "mail.template": "email_template",
    "ir.ui.view": "view",
    "ir.model.fields": "field",
    "ir.actions.report": "report",
    "studio.approval.rule": "approval_rule",
}
# Records edited later than this after they were made count as edited by someone
EDIT_GRACE = timedelta(seconds=10)


def kind_of(record, step=None):
    """:return: the kind of ``record``, a button when the step that made the view is prepare_button"""
    if record._name == "ir.ui.view" and getattr(step, "tool", "") == "prepare_button":
        return "button"
    return MODEL_KINDS.get(record._name)


class NudgeBuilt(models.Model):
    """One thing Nudge built: a pointer to the record, not the record."""
    _name = "nudge.built"
    _description = "Made by Nudge"
    _order = "made_on desc, id desc"

    name = fields.Char(required=True, readonly=True)
    kind = fields.Selection(KINDS, required=True, readonly=True, index=True)
    res_model = fields.Char(string="Model", required=True, readonly=True, index=True)
    res_id = fields.Integer(string="Record", required=True, readonly=True, index=True)
    user_id = fields.Many2one("res.users", string="Asked by", readonly=True, ondelete="set null", index=True)
    company_id = fields.Many2one("res.company", readonly=True, ondelete="cascade", index=True)
    made_on = fields.Datetime(string="Made on", required=True, readonly=True, default=fields.Datetime.now)
    run_id = fields.Many2one("nudge.run", string="Request", readonly=True, ondelete="set null")
    step_id = fields.Many2one("nudge.step", string="Step", readonly=True, ondelete="set null")
    conversation_id = fields.Many2one("nudge.conversation", string="Conversation", readonly=True, ondelete="set null")
    source = fields.Selection(
        [("tool", "Made through Developer mode"), ("log", "Found in the log of steps"),
         ("tag", "Found by its tag")], required=True, default="tool", readonly=True)
    switched_off_by = fields.Many2one("res.users", readonly=True, ondelete="set null")
    switched_off_on = fields.Datetime(readonly=True)
    log_ids = fields.One2many("nudge.built.log", "built_id", string="Log")
    state = fields.Selection(
        [("active", "Active"), ("archived", "Switched off"), ("gone", "Deleted")],
        compute="_compute_state", string="Status")
    can_switch_off = fields.Boolean(compute="_compute_state")
    can_undo = fields.Boolean(compute="_compute_state", string="Undo available")

    _sql_constraints = [("target_unique", "unique(res_model, res_id)", "Each thing is listed once.")]

    # ------------------------------------------------------------------ registry
    @api.model
    def _note(self, record, step=None, kind=None, source="tool", made_on=None, user=None):
        """List ``record`` as made by Nudge, once.

        :param step: the step that made it, a ``nudge.step`` or any object holding ``run_id``
        :return: the row"""
        kind = kind or kind_of(record, step)
        if not kind or not record:
            return self.browse()
        # The registry belongs to Nudge, whoever asked: written as the server, like the log of steps
        Built = self.sudo()
        found = Built.search([("res_model", "=", record._name), ("res_id", "=", record.id)], limit=1)
        if found:
            return found
        run = getattr(step, "run_id", None)
        run = run if run and getattr(run, "_name", "") == "nudge.run" else self.env["nudge.run"]
        step_record = step if getattr(step, "_name", "") == "nudge.step" and step.id else self.env["nudge.step"]
        company = step_record.sudo().company_id if step_record else self.env.company
        user = user or (run.sudo().user_id if run else None) or self.env.user
        return Built.create({
            "name": (record.display_name or str(record.id))[:200],
            "kind": kind, "res_model": record._name, "res_id": record.id,
            "user_id": user.id, "company_id": company.id if company else False,
            "made_on": made_on or fields.Datetime.now(),
            "run_id": run.id if run else False,
            "step_id": step_record.id or False,
            "conversation_id": (run.sudo().conversation_id.id if run else False)
            or (step_record.sudo().conversation_id.id if step_record else False),
            "source": source,
        })

    @api.model
    def _tag(self, record, name):
        """Give ``record`` an xml id of ``nudge_custom``, once."""
        Data = self.env["ir.model.data"].sudo()
        if Data.search_count([("module", "=", CUSTOM_MODULE), ("model", "=", record._name), ("res_id", "=", record.id)]):
            return
        slug = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")[:60] or "record"
        Data.create({
            "module": CUSTOM_MODULE, "name": f"{slug}_{record.id}", "model": record._name,
            "res_id": record.id, "noupdate": True})

    @api.model
    def _sync(self, tag=True):
        """List what was made before the list existed, or by a module that does not write here: from the
        steps that were done (never by name), and from what already carries the tag.

        :return: the number of rows added"""
        before = self.sudo().search_count([])
        Step = self.env["nudge.step"].sudo()
        known = {(row.res_model, row.res_id) for row in self.sudo().search([])}
        for step in Step.search([("state", "=", "done"), ("kind", "=", "create"),
                                 ("res_model", "in", list(MODEL_KINDS))], order="id"):
            if step.res_model not in self.env or not step.res_ids:
                continue
            records = self.env[step.res_model].sudo().with_context(active_test=False).browse(
                [int(i) for i in step.res_ids]).exists()
            for record in records:
                if (record._name, record.id) in known:
                    continue
                if record._name == "ir.ui.view" and record.type == "qweb":
                    continue
                known.add((record._name, record.id))
                if tag:
                    self._tag(record, f"{kind_of(record, step)}_{record.display_name}")
                self._note(record, step, source="log", made_on=step.create_date, user=step.sudo().user_id)
        for data in self.env["ir.model.data"].sudo().search(
                [("module", "=", CUSTOM_MODULE), ("model", "in", list(MODEL_KINDS))]):
            if (data.model, data.res_id) in known or data.model not in self.env:
                continue
            record = self.env[data.model].sudo().with_context(active_test=False).browse(data.res_id).exists()
            if not record or (record._name == "ir.ui.view" and record.type == "qweb"):
                continue
            known.add((record._name, record.id))
            self._note(record, None, source="tag", made_on=record.create_date, user=record.create_uid)
        return self.sudo().search_count([]) - before

    # ------------------------------------------------------------------ the thing itself
    def _target(self):
        """:return: the record this row points at, archived or not (read as the server: only its
        state and its name are shown, the rights are checked when the person acts on it)"""
        self.ensure_one()
        if self.res_model not in self.env:
            return self.env["res.users"].browse()
        return self.env[self.res_model].sudo().with_context(active_test=False).browse(self.res_id).exists()

    def _is_archivable(self, target):
        return "active" in target._fields

    @api.depends("res_model", "res_id", "switched_off_on", "step_id.state")
    def _compute_state(self):
        for built in self:
            target = built._target() if built.res_model in self.env else None
            if not target:
                built.state, built.can_switch_off, built.can_undo = "gone", False, False
                continue
            if "active" in target._fields:
                off = not target.active
            else:
                off = bool(built.switched_off_on)
            built.state = "archived" if off else "active"
            built.can_switch_off = not off and built.res_model != "mail.template"
            built.can_undo = bool(
                built.step_id and built.step_id.sudo().state == "done" and built.step_id.kind == "create"
                and built.res_model in ("base.automation", "ir.actions.server", "mail.template", "ir.cron",
                                        "ir.ui.view", "ir.model.fields", "ir.actions.report"))

    def _edited_since(self):
        """:return: ``(user, when)`` of a change made to the thing after it was made, or ``None``"""
        self.ensure_one()
        target = self._target()
        if not target or not target.write_date or not self.made_on:
            return None
        if target.write_date > self.made_on + EDIT_GRACE:
            return target.write_uid, target.write_date
        return None

    def _off_plan(self):
        """:return: ``[(record, values, what is said on the card)]`` that switching this off does"""
        from ..tools import developer  # noqa: PLC0415
        self.ensure_one()
        target = self._target().with_env(self.env)
        target = target.with_context(active_test=False)
        if not target:
            return []
        if "active" in target._fields:
            return [(target, {"active": False}, self.env._("Archived: it no longer runs, nothing is deleted."))]
        plan = []
        Views = self.env["ir.ui.view"].with_context(active_test=False)
        if target._name == "ir.actions.server":
            if target.binding_model_id:
                plan.append((target, {"binding_model_id": False}, self.env._("Taken out of the Actions menu.")))
            views = Views.search([("type", "!=", "qweb"), ("arch_db", "like", f'name="{target.id}"')])
            views = views.filtered(lambda view: developer._buttons_running(view, target)[1] and view.active)
            for view in views.filtered(lambda view: self._is_ours(view)):
                plan.append((view, {"active": False},
                             self.env._("Button view %s archived: the button goes off the form.", view.name)))
        elif target._name == "ir.actions.report":
            if target.binding_model_id:
                plan.append((target, {"binding_model_id": False}, self.env._("Taken out of the Print menu.")))
            for view in Views.search([("type", "=", "qweb"), ("key", "=", target.report_name)]):
                if view.active and self._is_ours(view):
                    plan.append((view, {"active": False}, self.env._("Template archived.")))
        elif target._name == "ir.model.fields":
            for view in Views.search([("model", "=", target.model), ("arch_db", "like", f'name="{target.name}"')]):
                if view.active and view.inherit_id and self._is_ours(view):
                    plan.append((view, {"active": False}, self.env._(
                        "View %s archived: the field is no longer shown. The field and its values stay.", view.name)))
        return plan

    def _is_ours(self, record):
        """Only what carries the tag of Nudge is switched off with the thing: never the view of a module."""
        return bool(self.env["ir.model.data"].sudo().search_count([
            ("module", "=", CUSTOM_MODULE), ("model", "=", record._name), ("res_id", "=", record.id)]))

    def _card_lines(self):
        """:return: what the card says for each thing, with the changes made to it since"""
        lines = []
        for built in self:
            edited = built._edited_since()
            changes = [what for _record, _values, what in built._off_plan()] or [
                self.env._("Nothing in Odoo can be switched off for this one.")]
            lines.append({
                "built": built,
                "label": f"{dict(KINDS)[built.kind]}: {built.name}",
                "changes": changes,
                "edited": (edited[0].name, edited[1]) if edited else None,
            })
        return lines

    def _switch_off(self):
        """Switch off what the person confirmed, with their own rights, and write the log.

        :raise AccessError: when the person may not change it"""
        for built in self:
            if not built.can_switch_off:
                raise UserError(self.env._("%s cannot be switched off.", built.name))
            plan = built._off_plan()
            if not plan:
                raise UserError(self.env._("%s cannot be switched off from here.", built.name))
            before, after = {}, {}
            for record, values, _what in plan:
                key = f"{record._name},{record.id}"
                before[key] = {name: _plain(record[name]) for name in values}
                record.write(values)
                after[key] = {name: _plain(record[name]) for name in values}
            built.sudo().write({"switched_off_by": self.env.user.id, "switched_off_on": fields.Datetime.now()})
            self.env["nudge.built.log"].sudo().create({
                "built_id": built.id, "user_id": self.env.user.id, "company_id": built.company_id.id,
                "action": "switch_off", "summary": built.name[:200], "before": before, "after": after})
            self.env.registry.clear_cache("templates")

    # ------------------------------------------------------------------ the screens
    @api.model
    def action_open(self):
        """:return: the list; what was made before it existed, or by a companion, is found first"""
        if self.env.user.has_group("nudge.group_nudge_manager") or self.env.user.has_group("base.group_system"):
            self._sync()
        action = self.env["ir.actions.actions"]._for_xml_id("nudge.nudge_built_action")
        return action

    def action_open_record(self):
        self.ensure_one()
        target = self._target()
        if not target:
            raise UserError(self.env._("It was deleted since."))
        return {
            "type": "ir.actions.act_window", "res_model": target._name, "res_id": target.id,
            "view_mode": "form", "views": [(False, "form")], "target": "current",
            "context": {"active_test": False},
        }

    def action_open_step(self):
        self.ensure_one()
        if not self.step_id:
            raise UserError(self.env._("No step is kept for this one."))
        return {"type": "ir.actions.act_window", "res_model": "nudge.step", "res_id": self.step_id.id,
                "view_mode": "form", "views": [(False, "form")]}

    def action_switch_off(self):
        """:return: the card that asks first"""
        things = self.filtered("can_switch_off")
        if not things:
            raise UserError(self.env._("Nothing here can be switched off."))
        wizard = self.env["nudge.built.switch"].create({"built_ids": [(6, 0, things.ids)]})
        return {
            "type": "ir.actions.act_window", "res_model": "nudge.built.switch", "res_id": wizard.id,
            "view_mode": "form", "views": [(False, "form")], "target": "new",
        }

    def unlink(self):
        if not self.env.context.get("module_uninstall"):
            raise AccessError(self.env._("The list of what Nudge built is kept: switch off what you do not want."))
        return super().unlink()


def _plain(value):
    """:return: ``value`` as the log keeps it"""
    if isinstance(value, models.BaseModel):
        return value.ids if len(value) != 1 else value.id
    return value


class NudgeBuiltLog(models.Model):
    """What was done to a thing Nudge built, with the values before and after."""
    _name = "nudge.built.log"
    _inherit = "nudge.log"
    _description = "Made by Nudge: Log"
    _order = "id desc"

    built_id = fields.Many2one("nudge.built", required=True, ondelete="cascade", index=True)
    user_id = fields.Many2one("res.users", ondelete="set null")
    company_id = fields.Many2one("res.company", ondelete="cascade")
    action = fields.Selection([("switch_off", "Switched off"), ("uninstall", "Switched off by the uninstall")],
                              required=True)
    summary = fields.Char()
    before = fields.Json()
    after = fields.Json()


class NudgeBuiltSwitch(models.TransientModel):
    """The card that asks before something is switched off."""
    _name = "nudge.built.switch"
    _description = "Switch Off What Nudge Built"

    built_ids = fields.Many2many("nudge.built", string="Things", readonly=True)
    summary = fields.Html(compute="_compute_summary", sanitize=False)
    edited = fields.Boolean(compute="_compute_summary")
    understood = fields.Boolean(string="I know it was changed since Nudge made it")

    @api.depends("built_ids")
    def _compute_summary(self):
        from odoo.tools import html_escape  # noqa: PLC0415
        for wizard in self:
            parts, edited = [], False
            for line in wizard.built_ids._card_lines():
                items = "".join(f"<li>{html_escape(change)}</li>" for change in line["changes"])
                warning = ""
                if line["edited"]:
                    edited = True
                    warning = "<p class='text-warning'>%s</p>" % html_escape(self.env._(
                        "Changed since by %(user)s on %(date)s.", user=line["edited"][0],
                        date=fields.Datetime.to_string(line["edited"][1])))
                parts.append(f"<p><b>{html_escape(line['label'])}</b></p><ul>{items}</ul>{warning}")
            wizard.summary = "".join(parts)
            wizard.edited = edited

    def action_confirm(self):
        self.ensure_one()
        if not self.env.user.has_group("nudge.group_nudge_manager") and not self.env.user.has_group("base.group_system"):
            raise AccessError(self.env._("Only a Nudge manager or an administrator can switch this off."))
        if self.edited and not self.understood:
            raise UserError(self.env._("It was changed since Nudge made it: tick the box to switch it off anyway."))
        self.built_ids._switch_off()
        return {"type": "ir.actions.act_window_close"}

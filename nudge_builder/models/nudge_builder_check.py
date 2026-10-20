import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..tools import checks

# Reports kept for a website: older ones are removed when a new one is made
KEEP_REPORTS = 10


class NudgeBuilderCheck(models.Model):
    """The result of one run of the site check, kept per website so the next run shows what changed.
    A check never modifies anything."""

    _name = "nudge.builder.check"
    _description = "Website Check Report"
    _order = "date desc, id desc"

    website_id = fields.Many2one("website", required=True, readonly=True, index=True, ondelete="cascade")
    company_id = fields.Many2one(related="website_id.company_id", store=True)
    user_id = fields.Many2one("res.users", string="Run By", required=True, readonly=True)
    date = fields.Datetime(default=fields.Datetime.now, required=True, readonly=True)
    external = fields.Boolean(string="External Links Visited", readonly=True)
    finding_ids = fields.One2many("nudge.builder.check.finding", "report_id", string="Findings", readonly=True)
    skipped_json = fields.Text(readonly=True, default="[]")
    critical_count = fields.Integer(readonly=True)
    warning_count = fields.Integer(readonly=True)
    info_count = fields.Integer(readonly=True)
    previous_id = fields.Many2one("nudge.builder.check", string="Compared With", readonly=True, ondelete="set null")
    summary = fields.Char(compute="_compute_summary")
    skipped_text = fields.Text(string="Not Checked", compute="_compute_summary")

    @api.depends("critical_count", "warning_count", "info_count", "skipped_json")
    @api.depends_context("lang")
    def _compute_summary(self):
        for report in self:
            labels = checks.group_labels(report.env)
            report.summary = _(
                "%(critical)s critical, %(warning)s to look at, %(info)s to know",
                critical=report.critical_count, warning=report.warning_count, info=report.info_count)
            report.skipped_text = "\n".join(
                "- %s: %s" % (labels.get(item["group"], item["group"]), _("it could not run"))
                for item in json.loads(report.skipped_json or "[]"))

    @api.depends("website_id", "date")
    def _compute_display_name(self):
        for report in self:
            report.display_name = "%s - %s" % (report.website_id.name, fields.Datetime.to_string(report.date))

    @api.model
    def run_check(self, website, external=False, groups=None, palette=None, fetch=None):
        """Check ``website`` as the current user and keep the report. Nothing but the report is written.

        :return: the report
        :raise AccessError: when the user is not a website designer"""
        checks.check_designer(self.env)
        found, skipped = checks.check_site(self.env, website, external=external, groups=groups, palette=palette,
                                           fetch=fetch)
        previous = self.search([("website_id", "=", website.id)], limit=1)
        before = {(f.key, f.extra_lang or ""): f.count for f in previous.finding_ids}
        counts = {s: sum(1 for item in found if item["severity"] == s) for s in checks.SEVERITIES}
        report = self.create({
            "website_id": website.id, "user_id": self.env.user.id, "previous_id": previous.id, "external": external,
            "skipped_json": json.dumps(skipped),
            "critical_count": counts[checks.CRITICAL], "warning_count": counts[checks.WARNING],
            "info_count": counts[checks.INFO],
            "finding_ids": [(0, 0, {
                "key": item["key"], "check_group": item["group"], "severity": item["severity"],
                "count": item["count"], "model_name": item["model"] or False,
                "ids_json": json.dumps(item["ids"]), "urls_json": json.dumps(item["urls"]),
                "extra_json": json.dumps(item["extra"]), "fix": item["fix"] or False,
                "is_new": (item["key"], item["extra"].get("lang", "")) not in before,
                "delta": item["count"] - before.get((item["key"], item["extra"].get("lang", "")), 0),
            }) for item in found],
        })
        self.search([("website_id", "=", website.id)], offset=KEEP_REPORTS).unlink()
        return report

    def action_open(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id,
                "view_mode": "form", "views": [(False, "form")], "target": "current"}


class NudgeBuilderCheckFinding(models.Model):
    _name = "nudge.builder.check.finding"
    _description = "Website Check Finding"
    _order = "severity_rank, id"

    report_id = fields.Many2one("nudge.builder.check", required=True, ondelete="cascade", index=True)
    website_id = fields.Many2one(related="report_id.website_id")
    company_id = fields.Many2one(related="report_id.company_id", store=True, index=True)
    key = fields.Char(required=True)
    check_group = fields.Selection([(g, g.capitalize()) for g in checks.GROUPS], string="Check", required=True)
    severity = fields.Selection(
        [("critical", "Critical"), ("warning", "To Look At"), ("info", "To Know")], required=True)
    severity_rank = fields.Integer(compute="_compute_severity_rank", store=True)
    count = fields.Integer()
    is_new = fields.Boolean(string="New")
    delta = fields.Integer(string="Since Last Time")
    model_name = fields.Char()
    ids_json = fields.Text(default="[]")
    urls_json = fields.Text(default="[]")
    extra_json = fields.Text(default="{}")
    extra_lang = fields.Char(compute="_compute_extra_lang")
    fix = fields.Char()
    text = fields.Char(string="Finding", compute="_compute_text")
    fix_title = fields.Char(compute="_compute_text")
    group_label = fields.Char(string="Area", compute="_compute_text")

    @api.depends("severity")
    def _compute_severity_rank(self):
        order = {s: i for i, s in enumerate(checks.SEVERITIES)}
        for item in self:
            item.severity_rank = order.get(item.severity, 9)

    @api.depends("extra_json")
    def _compute_extra_lang(self):
        for item in self:
            item.extra_lang = json.loads(item.extra_json or "{}").get("lang", "")

    @api.depends("key", "count", "extra_json", "fix")
    @api.depends_context("lang")
    def _compute_text(self):
        titles = checks.fix_titles(self.env)
        groups = checks.group_labels(self.env)
        for item in self:
            item.text = checks.finding_text(self.env, item.key, item.count, json.loads(item.extra_json or "{}"))
            item.fix_title = titles.get(item.fix, "")
            item.group_label = groups.get(item.check_group, "")

    def _item(self):
        self.ensure_one()
        return {"key": self.key, "model": self.model_name, "ids": json.loads(self.ids_json or "[]"),
                "extra": json.loads(self.extra_json or "{}")}

    def action_show(self):
        """Open the page in the editor, or the records listed"""
        self.ensure_one()
        checks.check_designer(self.env)
        action = checks.show_action(self.env, self.website_id, self._item())
        if not action:
            raise UserError(_("There is nothing to open for this finding."))
        return action

    def action_fix(self):
        """Show what the fix would change; nothing is done before the person says yes"""
        self.ensure_one()
        checks.check_designer(self.env)
        changes = checks.plan_fix(self.env, self.website_id, self.fix)
        if not changes:
            raise UserError(_("There is nothing left to change for this fix."))
        wizard = self.env["nudge.builder.check.fix"].create({
            "finding_id": self.id, "fix": self.fix, "changes_json": json.dumps(changes)})
        return wizard._reopen()


class NudgeBuilderCheckFix(models.TransientModel):
    """The confirmation card of a fix: the list of changes, then Apply"""

    _name = "nudge.builder.check.fix"
    _description = "Website Check Fix"

    finding_id = fields.Many2one("nudge.builder.check.finding", readonly=True, ondelete="cascade")
    website_id = fields.Many2one(related="finding_id.website_id")
    fix = fields.Char(readonly=True)
    changes_json = fields.Text(readonly=True)
    done = fields.Boolean(readonly=True)
    title = fields.Char(compute="_compute_text")
    lines = fields.Text(string="Changes", compute="_compute_text")

    @api.depends("fix", "changes_json", "done")
    @api.depends_context("lang")
    def _compute_text(self):
        titles = checks.fix_titles(self.env)
        for wizard in self:
            changes = json.loads(wizard.changes_json or "[]")
            wizard.title = titles.get(wizard.fix, "")
            shown = "\n".join("- " + change["label"] for change in changes[:50])
            if len(changes) > 50:
                shown += "\n" + _("... and %s more", len(changes) - 50)
            wizard.lines = shown

    def _reopen(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id, "view_mode": "form",
                "views": [(False, "form")], "target": "new"}

    def action_apply(self):
        self.ensure_one()
        checks.check_designer(self.env)
        if self.done:
            raise UserError(_("This fix was applied already."))
        changes = json.loads(self.changes_json or "[]")
        count = checks.apply_plan(self.env, self.website_id, changes)
        self.done = True
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "title": _("Done"),
            "message": _("%s changes were made. Check the site again to see the result.", count),
            "type": "success", "next": {"type": "ir.actions.act_window_close"}}}


class NudgeBuilderCheckRun(models.TransientModel):
    """Site Check: choose the website, run"""

    _name = "nudge.builder.check.run"
    _description = "Site Check"

    website_id = fields.Many2one(
        "website", required=True, default=lambda self: self.env["website"].search([], limit=1))
    external = fields.Boolean(
        string="Also check external links",
        help="Visits each external link once (5 seconds at most, 50 links at most). Off by default: it is slow.")

    def action_run(self):
        self.ensure_one()
        return self.env["nudge.builder.check"].run_check(self.website_id, external=self.external).action_open()

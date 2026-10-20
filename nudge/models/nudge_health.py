import json
import logging
from datetime import timedelta

import odoo
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .. import tools

_logger = logging.getLogger(__name__)

# Reports kept for a company: older ones are removed when a new one is made
KEEP_REPORTS = 10
# Days between two runs of the weekly check
WEEK = 7


class NudgeHealthReport(models.Model):
    """The result of one run of the health check, kept per company so the next
    run shows what changed. A check never modifies anything."""

    _name = "nudge.health.report"
    _description = "Database Health Report"
    _order = "date desc, id desc"

    company_id = fields.Many2one("res.company", required=True, readonly=True, index=True)
    user_id = fields.Many2one("res.users", string="Run By", required=True, readonly=True)
    date = fields.Datetime(default=fields.Datetime.now, required=True, readonly=True)
    findings_json = fields.Text(readonly=True, default="[]")
    resolved_json = fields.Text(readonly=True, default="[]")
    skipped_json = fields.Text(readonly=True, default="[]")
    finding_ids = fields.One2many("nudge.health.finding", "report_id", string="Findings", readonly=True)
    critical_count = fields.Integer(readonly=True)
    warning_count = fields.Integer(readonly=True)
    info_count = fields.Integer(readonly=True)
    previous_id = fields.Many2one("nudge.health.report", string="Compared With", readonly=True, ondelete="set null")
    summary = fields.Char(compute="_compute_summary")
    resolved_text = fields.Text(string="Solved Since the Last Time", compute="_compute_texts")
    skipped_text = fields.Text(string="Not Checked", compute="_compute_texts")
    weekly = fields.Boolean(compute="_compute_weekly")
    # The checklist of a country this report is the result of; empty for the general check of the database
    pack = fields.Char(readonly=True, index=True)
    pack_name = fields.Char(compute="_compute_pack_name")

    @api.depends("pack")
    @api.depends_context("lang")
    def _compute_pack_name(self):
        packs, _refused = tools.compliance.load_all(self.env)
        for report in self:
            pack = packs.get(report.pack)
            report.pack_name = tools.compliance.pick(pack.name, report.env) if pack else False
    scope = fields.Selection(
        [("health", "Health Check"), ("upgrade", "Before an Upgrade")], default="health", required=True,
        readonly=True, index=True)
    target = fields.Char(string="Target Series", readonly=True)

    @api.depends("critical_count", "warning_count", "info_count")
    @api.depends_context("lang")
    def _compute_summary(self):
        for report in self:
            report.summary = _(
                "%(critical)s critical, %(warning)s to look at, %(info)s to know",
                critical=report.critical_count, warning=report.warning_count, info=report.info_count)

    @api.depends("resolved_json", "skipped_json")
    @api.depends_context("lang")
    def _compute_texts(self):
        reasons = {
            "access": _("your rights do not let you read what it looks at"),
            "timeout": _("it took too long"),
            "error": _("it failed"),
        }
        labels = tools.health.labels(self.env)
        for report in self:
            lines = []
            for item in json.loads(report.resolved_json or "[]"):
                check = tools.health.BY_KEY.get(item["check"])
                if check:
                    lines.append("- " + check.describe(report.env, {**item, "count": item["was"]}))
            report.resolved_text = "\n".join(lines)
            report.skipped_text = "\n".join(
                f"- {labels.get(item['check']) or _('Compliance checklist')}: {reasons.get(item['reason'], '')}"
                for item in json.loads(report.skipped_json or "[]") if item["check"] in tools.health.BY_KEY)

    def _compute_weekly(self):
        for report in self:
            report.weekly = report.company_id.nudge_health_user_id == report.env.user

    @api.model
    def _check_manager(self):
        if not self.env.user.has_group("nudge.group_nudge_manager"):
            raise AccessError(_("Only a Nudge manager may run the health check."))

    @api.model
    def run_check(self, pack=False):
        """Run every check offered here for the current company, as the current
        user, and keep the report. Nothing in the database is changed but the
        report itself.

        :param pack: the checklist of a country to run instead of the general check
        :return: the report
        """
        self._check_manager()
        return self._run("health", pack=pack)

    @api.model
    def run_upgrade_check(self, target):
        """The check before an upgrade to the series ``target``: administrators
        only, as themselves, nothing changed but the report.

        :return: the report
        """
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only an administrator may check the database before an upgrade."))
        if not tools.upgrade.known_target(target):
            raise UserError(_("Choose the series the database is going to."))
        return self._run("upgrade", target)

    @api.model
    def _run(self, scope, target=False, pack=False):
        company = self.env.company
        # Only the records of this company are looked at, and only those the user may read
        env = self.with_context(allowed_company_ids=[company.id]).env
        scoped = [("company_id", "=", company.id), ("scope", "=", scope), ("target", "=", target),
                  ("pack", "=", pack or False)]
        previous = self.search(scoped, limit=1)
        if pack:
            found, skipped = tools.compliance.run_pack(env, pack)
        elif scope == "upgrade":
            found, skipped = tools.upgrade.run_upgrade(env, target)
        else:
            found, skipped = tools.health.run_all(env)
        before = {(item["check"], item["key"]): item for item in json.loads(previous.findings_json or "[]")}
        now = {(item["check"], item["key"]) for item in found}
        not_run = {item["check"] for item in skipped if not item.get("entry")}
        # a checklist that could not run one of its checks keeps what that check found before
        blocked = {item["entry"] for item in skipped if item.get("entry")}
        # Solved: reported last time, the check ran now and finds nothing
        resolved = [
            {"check": check, "key": key, "was": item["count"], "capped": item.get("capped"),
             "sample": item.get("sample", 0), "figure": item.get("figure", 0.0)}
            for (check, key), item in before.items() if (check, key) not in now and check not in not_run
            and key.split("|")[0] not in blocked
        ]
        counts = {severity: sum(1 for item in found if item["severity"] == severity)
                  for severity in tools.health.SEVERITIES}
        report = self.create({
            "company_id": company.id,
            "pack": pack or False,
            "user_id": self.env.user.id,
            "scope": scope,
            "target": target,
            "previous_id": previous.id,
            "findings_json": json.dumps(found),
            "resolved_json": json.dumps(resolved),
            "skipped_json": json.dumps(skipped),
            "critical_count": counts[tools.health.CRITICAL],
            "warning_count": counts[tools.health.WARNING],
            "info_count": counts[tools.health.INFO],
            "finding_ids": [(0, 0, {
                "check": item["check"],
                "key": item["key"],
                "severity": item["severity"],
                "count": item["count"],
                "capped": item["capped"],
                "sample": item["sample"],
                "figure": item.get("figure", 0.0),
                "model_name": item["model"] or False,
                "domain_json": json.dumps(item["domain"]) if item["domain"] else False,
                "ids_json": json.dumps(item["ids"]),
                "is_new": (item["check"], item["key"]) not in before,
                "delta": item["count"] - before.get((item["check"], item["key"]), {}).get("count", 0),
            }) for item in found],
        })
        # Only the latest reports of a company are kept
        self.search(scoped, offset=KEEP_REPORTS).unlink()
        return report

    def action_run(self):
        """:return: the form of a new report"""
        self.ensure_one()
        if self.scope == "upgrade":
            return self.run_upgrade_check(self.target).action_open()
        return self.run_check().action_open()

    def action_run_pack(self):
        """:return: the form of a new report of the same checklist"""
        self.ensure_one()
        return self.run_check(pack=self.pack).action_open()

    def action_open(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
        }

    def action_toggle_weekly(self):
        """Run the check every week as the current user, or stop doing so."""
        self.ensure_one()
        self._check_manager()
        company = self.company_id
        user = self.env.user
        if company not in user.company_ids:
            raise AccessError(_("You do not work in this company."))
        # sudo: a manager may not write a company; this writes only who runs the weekly check, on their own say
        company.sudo().nudge_health_user_id = False if company.nudge_health_user_id == user else user
        return True

    @api.model
    def _cron_run(self):
        """Run the weekly check of the companies that asked for it, each as
        the manager who asked: with their rights, not the superuser's."""
        companies = self.env["res.company"].sudo().search([("nudge_health_user_id", "!=", False)])
        for company in companies:
            user = company.nudge_health_user_id
            if (not user.active or company not in user.company_ids
                    or not user.has_group("nudge.group_nudge_manager")):
                continue
            Report = self.with_user(user).with_context(allowed_company_ids=[company.id])
            last = Report.search([("company_id", "=", company.id), ("pack", "=", False), ("scope", "=", "health")],
                                 limit=1)
            if last and last.date > fields.Datetime.now() - timedelta(days=WEEK, hours=1):
                continue
            # One company at a time: a failure in one does not undo the checks of the others
            try:
                with self.env.cr.savepoint():
                    Report.with_company(company).run_check()
            except Exception:  # noqa: BLE001
                _logger.exception("Nudge: the weekly health check of company %s failed", company.id)
            if not self.env.context.get("nudge_keep_transaction"):
                self.env.cr.commit()


class NudgeHealthFinding(models.Model):
    _name = "nudge.health.finding"
    _description = "Database Health Finding"
    _order = "severity_rank, id"

    report_id = fields.Many2one("nudge.health.report", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="report_id.company_id", store=True)
    check = fields.Char(required=True)
    key = fields.Char(required=True)
    severity = fields.Selection(
        [("critical", "Critical"), ("warning", "To Look At"), ("info", "To Know")], required=True)
    severity_rank = fields.Integer(compute="_compute_severity_rank", store=True)
    count = fields.Integer()
    capped = fields.Boolean()
    sample = fields.Integer()
    figure = fields.Float(help="The number the sentence quotes, such as how many times the usual a figure is.")
    is_new = fields.Boolean(string="New")
    delta = fields.Integer(string="Since Last Time")
    model_name = fields.Char()
    domain_json = fields.Text()
    ids_json = fields.Text()
    message = fields.Char(compute="_compute_message")
    count_text = fields.Char(string="Found", compute="_compute_message")
    # What it means, and the official reference, for the findings of a country's checklist
    detail = fields.Text(string="What It Means", compute="_compute_detail")

    @api.depends("check", "key")
    @api.depends_context("lang")
    def _compute_detail(self):
        for finding in self:
            check = tools.health.BY_KEY.get(finding.check)
            finding.detail = check.detail(finding.env, finding.key) if check and hasattr(check, "detail") else False

    @api.depends("severity")
    def _compute_severity_rank(self):
        rank = {severity: index for index, severity in enumerate(tools.health.SEVERITIES)}
        for finding in self:
            finding.severity_rank = rank.get(finding.severity, 9)

    @api.depends("check", "key", "count", "capped", "sample", "figure")
    @api.depends_context("lang")
    def _compute_message(self):
        for finding in self:
            check = tools.health.BY_KEY.get(finding.check)
            item = {"key": finding.key, "count": finding.count, "capped": finding.capped, "sample": finding.sample,
                    "figure": finding.figure}
            try:
                finding.message = check.describe(finding.env, item) if check else finding.key
            except KeyError:
                finding.message = finding.key
            finding.count_text = tools.health.count_text(finding.count, finding.capped)

    def action_show(self):
        """:return: the list of the records of the finding, as the one who opens
        it: the filter is a domain, or the records found, and Odoo's own rules
        decide what is shown"""
        self.ensure_one()
        if not self.model_name or self.model_name not in self.env:
            raise UserError(_("There is nothing to show for this one."))
        model = self.env[self.model_name]
        domain = json.loads(self.domain_json) if self.domain_json else [("id", "in", json.loads(self.ids_json or "[]"))]
        return {
            "type": "ir.actions.act_window",
            "name": self.message,
            "res_model": model._name,
            "view_mode": "list,form",
            "views": [(False, "list"), (False, "form")],
            "domain": domain,
        }


class NudgeCompliancePack(models.TransientModel):
    """The checklists for the country of the company, to run one by one. The rows are made when the list is opened,
    from the pack files: nothing of them is kept."""

    _name = "nudge.compliance.pack"
    _description = "Compliance Checklist of a Country"
    _order = "name"

    pack = fields.Char(readonly=True)
    name = fields.Char(readonly=True)
    countries = fields.Char(readonly=True)
    check_count = fields.Integer(string="Checks", readonly=True)
    reference = fields.Text(readonly=True)
    error = fields.Char(string="Not Loaded Because", readonly=True)
    report_id = fields.Many2one("nudge.health.report", string="Last Report", readonly=True)
    last_date = fields.Datetime(related="report_id.date", string="Last Run")
    last_summary = fields.Char(related="report_id.summary", string="Last Result")

    @api.model
    def action_open(self):
        """:return: the list of the checklists of the country of the current company"""
        self.env["nudge.health.report"]._check_manager()
        company = self.env.company
        self.search([("create_uid", "=", self.env.uid)]).unlink()
        packs, refused = tools.compliance.load_all(self.env)
        mine = tools.compliance.packs_for(self.env, company)
        Report = self.env["nudge.health.report"]
        rows = []
        for pack in mine:
            rows.append({
                "pack": pack.id, "name": tools.compliance.pick(pack.name, self.env),
                "countries": ", ".join(pack.countries), "check_count": len(pack.checks),
                "reference": tools.compliance.pick(pack.reference, self.env),
                "report_id": Report.search([("company_id", "=", company.id), ("pack", "=", pack.id)], limit=1).id,
            })
        for name, why in refused:
            rows.append({"name": name, "error": why[:500]})
        self.create(rows)
        title = _("Compliance checklists")
        if not company.country_id:
            title = _("Compliance checklists: set the country of the company first")
        elif not mine:
            title = _("No checklist for %s yet", company.country_id.name)
        return {
            "type": "ir.actions.act_window",
            "name": "%s. %s" % (title, _("A checklist, not legal advice; confirm with the authority.")),
            "res_model": self._name,
            "view_mode": "list",
            "views": [(False, "list")],
            "target": "current",
            "domain": [("create_uid", "=", self.env.uid)],
        }

    def action_run(self):
        """:return: the form of the report of the checklist, made now"""
        self.ensure_one()
        if not self.pack:
            raise UserError(self.error or _("This checklist could not be loaded."))
        return self.env["nudge.health.report"].run_check(pack=self.pack).action_open()

    def action_last(self):
        self.ensure_one()
        if not self.report_id:
            raise UserError(_("This checklist has not been run yet."))
        return self.report_id.action_open()
class NudgeUpgradeWizard(models.TransientModel):
    """The question before the upgrade check: which series is the database going to"""
    _name = "nudge.upgrade.wizard"
    _description = "Check Before an Upgrade"
    target = fields.Selection(
        [("18.0", "17 to 18"), ("19.0", "18 to 19"), ("20.0", "19 to 20")], string="Upgrade", required=True,
        default=lambda self: tools.upgrade.default_target(odoo.release.series))
    known = fields.Boolean(compute="_compute_known")
    @api.depends("target")
    def _compute_known(self):
        for wizard in self:
            wizard.known = bool(tools.upgrade.rules_for(wizard.target))
    def action_check(self):
        """:return: the form of the report, run as the one who clicks"""
        return self.env["nudge.health.report"].run_upgrade_check(self.target).action_open()

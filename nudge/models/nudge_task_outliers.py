"""The task kind Outliers: one family of unusual figures, watched every day.

It runs the checks of ``tools/outliers`` as the owner, with their rights, and
no AI. It says something only when a finding is new or has grown since the
last run, so a figure that stays unusual is not repeated every morning. Each
line has a "Show me" that opens the records, read as the person who opens it.
"""
from odoo import api, fields, models, release
from odoo.exceptions import AccessError, UserError

from .. import tools

FAMILY_SELECTION = [
    ("all", "All Families"),
    ("payslips", "Payslips and Overtime"),
    ("invoices", "Bills and Invoices"),
    ("stock", "Stock Moves"),
    ("expenses", "Expenses"),
    ("sales", "Sales Lines"),
    ("purchases", "Purchase Lines"),
    ("attendance", "Attendance"),
]


class NudgeTaskOutliers(models.Model):
    _inherit = "nudge.task"

    engine = fields.Selection(
        selection_add=[("outliers", "Unusual Figures")], ondelete={"outliers": "set default"})
    # Not required in the database: only a task of this kind has them
    outlier_family = fields.Selection(
        FAMILY_SELECTION, string="Watch", default="all",
        help="The kind of figures looked at, without any AI, as the owner of the task.")
    outlier_sensitivity = fields.Selection(
        [("2", "High (2 spreads)"), ("3", "Normal (3 spreads)"), ("4", "Low (4 spreads)")],
        string="Sensitivity", default="3",
        help="How far from the usual a figure must be, in spreads from the median of its group.")

    @api.model
    def action_setup_outliers(self, family="all", sensitivity="3", send_email=True):
        """Watch a family of unusual figures every day, as the person who asks.

        :return: ``{"id"}`` of the task"""
        if not self.env.user.has_group("nudge.group_nudge_user"):
            raise AccessError(self.env._("Nudge is not available to you."))
        if family not in dict(FAMILY_SELECTION) or str(sensitivity) not in tools.outliers.SENSITIVITIES:
            raise UserError(self.env._("Choose one of the families and one of the sensitivities offered."))
        user = self.env.user
        Task = self.sudo().with_context(active_test=False)
        task = Task.search([("user_id", "=", user.id), ("engine", "=", "outliers"),
                            ("outlier_family", "=", family)], limit=1)
        label = dict(self._fields["outlier_family"]._description_selection(self.env))[family]
        values = {
            "name": self.env._("Unusual figures: %s", label), "engine": "outliers", "kind": "schedule",
            "interval_number": 1, "interval_type": "days", "hour": 7, "send_email": bool(send_email),
            "outlier_family": family, "outlier_sensitivity": str(sensitivity), "active": True}
        if task:
            task.write(values)
        else:
            task = Task.create(dict(values, user_id=user.id, company_id=self.env.company.id))
        return {"id": task.id}

    def _outlier_keys(self):
        if self.outlier_family in (False, "all"):
            return {check.key for check in tools.outliers.CHECKS}
        return {f"outliers_{self.outlier_family}"}

    def _outlier_findings(self, env):
        """:return: ``(findings, skipped)`` of the family, as the owner (``env`` is theirs)"""
        self.ensure_one()
        return tools.health.run_all(
            env(context=dict(env.context, nudge_outlier_k=int(self.outlier_sensitivity or 3))), self._outlier_keys())

    def _start_outliers(self, env, now, values):
        self.ensure_one()
        task = self
        found, _skipped = task._outlier_findings(env)
        previous = task.rule_last if isinstance(task.rule_last, dict) else {}
        current = {f"{item['check']}.{item['key']}": item["count"] for item in found}
        values["rule_last"] = current
        fresh = [item for item in found if item["count"] > previous.get(f"{item['check']}.{item['key']}", 0)]
        if not fresh:
            task.write(dict(values, skip_count=task.skip_count + 1,
                            last_result=self.env._("Nothing new to report.")))
            return
        links = task._outlier_links(fresh)
        lines = [task.name]
        for item in sorted(fresh, key=lambda item: tools.health.SEVERITIES.index(item["severity"])):
            check = tools.health.BY_KEY[item["check"]]
            lines.append(f"- {check.describe(env, item)} - " + self.env._("Show me: %s", links[id(item)]))
        return task._publish("\n".join(lines), env, values)

    def _outlier_links(self, items):
        """Remember what each finding opens, for the owner only.

        :return: ``{id(item): url}``"""
        self.ensure_one()
        Link = self.env["nudge.digest.link"].sudo()
        Link.search([("task_id", "=", self.id)]).unlink()
        action = self.env.ref("nudge.nudge_digest_link_action").sudo().id
        base = (self.env["ir.config_parameter"].sudo().get_param("web.base.url") or "").rstrip("/")
        urls = {}
        for item in items:
            link = Link.create({
                "task_id": self.id, "user_id": self.sudo().user_id.id,
                "name": self.name, "res_model": item["model"],
                "domain": repr([("id", "in", item["ids"])])})
            urls[id(item)] = (f"{base}/odoo/{link.id}/action-{action}" if release.version_info[0] >= 18
                              else f"{base}/web#action={action}&active_id={link.id}")
        return urls

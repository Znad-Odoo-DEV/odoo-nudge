"""NP-16: the outside auditor. The group is given on the user, the date it ends is kept here, a daily cron ends
the access and the web request itself refuses a session past the date (``ir_http``)."""
import logging

from odoo import api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

AUDITOR_GROUP = "nudge.group_nudge_auditor"
# What an auditor can never be: someone Nudge acts for, builds for or manages
NEVER_AUDITOR = ("nudge.group_nudge_agent", "nudge.group_nudge_manager", "base.group_system")


class ResUsers(models.Model):
    _inherit = "res.users"

    nudge_auditor_until = fields.Date(
        string="Auditor Access Until", groups="base.group_system", copy=False,
        help="The last day an auditor can sign in. The day after, the access ends: the user is archived.")
    nudge_auditor_ended_on = fields.Datetime(
        string="Auditor Access Ended", groups="base.group_system", copy=False, readonly=True)
    nudge_auditor_questions = fields.Integer(
        string="Questions Asked", compute="_compute_nudge_auditor_questions", groups="base.group_system")

    def _compute_nudge_auditor_questions(self):
        counts = {}
        if self:
            # An administrator reads the questions of an auditor on the form of the user
            groups = self.env["nudge.message"].sudo()._read_group(
                [("user_id", "in", self.ids), ("role", "=", "user")], ["user_id"], ["__count"])
            counts = {user.id: count for user, count in groups}
        for user in self:
            user.nudge_auditor_questions = counts.get(user.id, 0)

    # -- the rule: an auditor is read-only, and has an end ---------------------
    @api.model_create_multi
    def create(self, vals_list):
        users = super().create(vals_list)
        users._nudge_check_auditor()
        return users

    def write(self, vals):
        result = super().write(vals)
        self._nudge_check_auditor()
        return result

    def _nudge_check_auditor(self):
        for user in self:
            if not user.has_group(AUDITOR_GROUP):
                continue
            for xml_id in NEVER_AUDITOR:
                if user.has_group(xml_id):
                    raise ValidationError(self.env._(
                        "%(user)s is a Nudge Auditor: the auditor is read-only and cannot also be an "
                        "Agent User, a Nudge Manager or an administrator.", user=user.name))
            if not user.sudo().nudge_auditor_until:
                raise ValidationError(self.env._(
                    "Give the date an auditor can sign in until: the access of an auditor always has an end."))

    # -- the end ----------------------------------------------------------------
    @api.model
    def _nudge_auditor_expired(self, user_id):
        """:return: whether the user is an auditor whose last day has passed (checked on each request)"""
        until = self.sudo().browse(user_id).nudge_auditor_until
        return bool(until) and until < fields.Date.today()

    def _nudge_end_auditor(self, reason):
        """Archive the auditors of ``self`` and write why in the log of the server."""
        now = fields.Datetime.now()
        for user in self.sudo().filtered("active"):
            user.write({"active": False, "nudge_auditor_ended_on": now})
            _logger.info("Nudge: the access of the auditor %s (id %s) ended: %s", user.login, user.id, reason)

    def action_nudge_revoke_auditor(self):
        """Revoke the access of the auditor now (administrators)."""
        if not self.env.user.has_group("base.group_system"):
            raise ValidationError(self.env._("Only an administrator ends the access of an auditor."))
        self.filtered(lambda user: user.has_group(AUDITOR_GROUP))._nudge_end_auditor("revoked by %s" % self.env.user.login)
        return True

    @api.model
    def _cron_nudge_end_auditors(self):
        """Daily: archive the auditors whose last day has passed.

        :return: how many were ended"""
        # Nudge's own infrastructure: the cron ends the access of people, whoever they are
        expired = self.sudo().search([("nudge_auditor_until", "<", fields.Date.today())])
        expired._nudge_end_auditor("the date was reached")
        return len(expired)

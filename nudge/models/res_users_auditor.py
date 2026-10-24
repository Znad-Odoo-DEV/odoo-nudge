"""NP-16: the outside auditor. The group is given on the user, the date it ends is kept here, an hourly cron ends
the access, the web request and the credential check (password or API key, over RPC too) refuse the user past
the date."""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessDenied, ValidationError

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
                    raise ValidationError(_(
                        "%(user)s is a Nudge Auditor: the auditor is read-only and cannot also be an "
                        "Agent User, a Nudge Manager or an administrator.", user=user.name))
            if not user.sudo().nudge_auditor_until:
                raise ValidationError(_(
                    "Give the date an auditor can sign in until: the access of an auditor always has an end."))

    # -- the end ----------------------------------------------------------------
    @api.model
    def _nudge_auditor_expired(self, user_id):
        """:return: whether the user is an auditor whose last day has passed (checked on each request)"""
        user = self.sudo().browse(user_id)
        # the group first (cached): the date is read only for the few who are auditors
        if not user.has_group(AUDITOR_GROUP):
            return False
        until = user.nudge_auditor_until
        return bool(until) and until < fields.Date.today()

    def _check_credentials(self, credential, env):
        """An auditor past the last day cannot sign in with a password or an API key either, over the web or
        RPC: this is the check all of them go through (the argument is a dict on 18 and 19, the password on 17)."""
        for user in self:
            if self._nudge_auditor_expired(user.id):
                _logger.info("Nudge: the expired auditor %s tried to sign in", user.login)
                raise AccessDenied()
        return super()._check_credentials(credential, env)

    def _nudge_end_auditor(self, reason):
        """Archive the auditors of ``self`` and write why in the log of the server."""
        now = fields.Datetime.now()
        for user in self.sudo().filtered("active"):
            user.write({"active": False, "nudge_auditor_ended_on": now})
            _logger.info("Nudge: the access of the auditor %s (id %s) ended: %s", user.login, user.id, reason)

    def action_nudge_revoke_auditor(self):
        """Revoke the access of the auditor now (administrators)."""
        if not self.env.user.has_group("base.group_system"):
            raise ValidationError(_("Only an administrator ends the access of an auditor."))
        self.filtered(lambda user: user.has_group(AUDITOR_GROUP))._nudge_end_auditor("revoked by %s" % self.env.user.login)
        return True

    @api.model
    def _cron_nudge_end_auditors(self):
        """Hourly: archive the auditors whose last day has passed.

        :return: how many were ended"""
        # Nudge's own infrastructure: the cron ends the access of people, whoever they are
        expired = self.sudo().search([("nudge_auditor_until", "<", fields.Date.today())])
        expired._nudge_end_auditor("the date was reached")
        return len(expired)


class ResGroups(models.Model):
    _inherit = "res.groups"

    @api.model
    def _nudge_users_field(self):
        """:return: the name of the field of the users of a group (it changed in Odoo 19)"""
        return "user_ids" if "user_ids" in self._fields else "users"

    def write(self, vals):
        result = super().write(vals)
        # Giving the auditor group a forbidden right (implied_ids) or the group to users goes through here,
        # not through res.users
        if "implied_ids" in vals or self._nudge_users_field() in vals:
            group = self.env.ref(AUDITOR_GROUP, raise_if_not_found=False)
            if group:
                group.sudo()[self._nudge_users_field()]._nudge_check_auditor()
        return result

    def _nudge_implied(self):
        """:return: these groups and everything they imply, one level after the other"""
        found = self
        todo = self
        while todo:
            todo = todo.mapped("implied_ids") - found
            found |= todo
        return found

    def _nudge_write_rights_beyond(self, baseline):
        """:return: ``{group: [model names]}``: the models these groups (with what they imply) can create, write
        or delete, that the groups of ``baseline`` (with what they imply) cannot"""
        # sudo: reads the access rights of the groups to judge them, as the administrator who asks would
        Access = self.env["ir.model.access"].sudo()
        domain = ["|", "|", ("perm_create", "=", True), ("perm_write", "=", True), ("perm_unlink", "=", True)]
        allowed = {access.model_id.id for access in Access.search(
            domain + [("group_id", "in", baseline._nudge_implied().ids), ("active", "=", True)])}
        result = {}
        for group in self:
            accesses = Access.search(domain + [("group_id", "in", group._nudge_implied().ids), ("active", "=", True)])
            models_ = accesses.model_id.filtered(lambda model: model.id not in allowed)
            if models_:
                result[group] = sorted(models_.mapped("model"))
        return result

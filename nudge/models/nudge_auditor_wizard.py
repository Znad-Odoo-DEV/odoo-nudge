"""NP-16: a small wizard for administrators to invite an auditor: name, login, companies, the last day. The
user is made with the auditor group and the basic internal user group, and nothing else."""
import logging
from datetime import timedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .res_users_auditor import AUDITOR_GROUP, NEVER_AUDITOR

_logger = logging.getLogger(__name__)
DEFAULT_DAYS = 30


class NudgeAuditorWizard(models.TransientModel):
    _name = "nudge.auditor.wizard"
    _description = "Add a Nudge Auditor"

    name = fields.Char(required=True)
    login = fields.Char(string="Login (e-mail)", required=True)
    company_ids = fields.Many2many(
        "res.company", string="Companies", required=True,
        default=lambda self: self.env.company,
        help="The auditor sees the log and the data of these companies only.")
    until = fields.Date(
        string="Access Until", required=True,
        default=lambda self: fields.Date.today() + timedelta(days=DEFAULT_DAYS),
        help="The last day the auditor can sign in.")
    read_group_ids = fields.Many2many(
        "res.groups", string="Read-only Rights",
        help="Optional: the read-only rights of the apps to question, such as the read-only accounting "
             "rights. Nothing that acts, builds or manages can be given to an auditor.")
    send_invitation = fields.Boolean(
        string="Send the Invitation", default=True,
        help="Send the e-mail with the link to choose a password.")

    @api.model
    def _groups_field(self):
        """:return: the name of the field of the groups of a user (it changed in Odoo 19)"""
        return "group_ids" if "group_ids" in self.env["res.users"]._fields else "groups_id"

    def action_create(self):
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only an administrator adds an auditor."))
        if self.until < fields.Date.today():
            raise UserError(_("The last day of the access cannot be in the past."))
        forbidden = self.env.ref(NEVER_AUDITOR[0]) | self.env.ref(NEVER_AUDITOR[1]) | self.env.ref(NEVER_AUDITOR[2])
        for group in self.read_group_ids:
            # A group that implies one of them is refused by the user itself (res.users)
            if group in forbidden:
                raise UserError(_("%s acts, builds or manages: it cannot be given to an auditor.", group.display_name))
        # Any other group is judged by its access rights (and those of the groups it implies): an auditor reads,
        # so nothing that creates, writes or deletes beyond the rights of an internal user and of the auditor
        baseline = self.env.ref("base.group_user") | self.env.ref(AUDITOR_GROUP)
        for group, models_ in self.read_group_ids._nudge_write_rights_beyond(baseline).items():
            raise UserError(_(
                "%(group)s lets its users create, change or delete records (%(models)s): it cannot be given to "
                "an auditor, who only reads.", group=group.display_name, models=", ".join(models_[:5])))
        groups = self.env.ref(AUDITOR_GROUP) | self.env.ref("base.group_user") | self.read_group_ids
        user = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": self.name, "login": self.login.strip(), "email": self.login.strip(),
            "company_id": self.company_ids[0].id, "company_ids": [Command.set(self.company_ids.ids)],
            "nudge_auditor_until": self.until,
            self._groups_field(): [Command.set(groups.ids)],
        })
        if self.send_invitation:
            try:
                user.action_reset_password()
            except Exception as error:  # noqa: BLE001 - no outgoing mail server must not undo the user
                _logger.warning("Nudge: the invitation of the auditor %s was not sent: %s", user.login, error)
        return {"type": "ir.actions.act_window", "res_model": "res.users", "res_id": user.id,
                "view_mode": "form", "views": [(False, "form")]}

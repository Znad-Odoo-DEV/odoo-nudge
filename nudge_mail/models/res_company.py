from odoo import api, fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    # Not required: the columns are added to companies that exist
    nudge_mail_agent_on = fields.Boolean(
        string="Mail Agent", copy=False,
        help="Nudge reads the new messages of a shared mailbox and proposes what to make of each: a lead, "
             "a ticket, a vendor bill, a reply. Nothing is made or sent before a manager says yes.")
    nudge_mail_agent_alias = fields.Char(
        string="Mailbox Alias", copy=False,
        help="The part before the @ of the address that receives the mail to sort, for example "
             "inbox. The incoming mail server of Odoo must deliver the mailbox to it.")
    nudge_mail_agent_alias_id = fields.Many2one("mail.alias", copy=False, readonly=True, ondelete="set null")
    nudge_mail_agent_project = fields.Selection(
        selection="_nudge_mail_agent_projects", string="Project for Tickets", copy=False,
        help="Where a ticket is made as a task when the Helpdesk app is not installed.")

    @api.model
    def _nudge_mail_agent_projects(self):
        if "project.project" not in self.env:
            return []
        projects = self.env["project.project"].sudo().search(
            [("company_id", "in", [False, self.env.company.id])], limit=200)
        return [(str(project.id), project.display_name) for project in projects]

    def write(self, vals):
        result = super().write(vals)
        if {"nudge_mail_agent_on", "nudge_mail_agent_alias"} & vals.keys():
            self._nudge_mail_agent_sync_alias()
        if "nudge_mail_agent_on" in vals:
            self._nudge_mail_agent_sync_cron()
        return result

    @api.model
    def _nudge_mail_agent_sync_cron(self):
        """The cron that sorts the mail which waited for the provider runs only while a company has the
        Mail Agent on"""
        cron = self.env.ref("nudge_mail.ir_cron_nudge_mail_classify", raise_if_not_found=False)
        if cron:
            cron.sudo().active = bool(self.sudo().search_count([("nudge_mail_agent_on", "=", True)]))

    def _nudge_mail_agent_sync_alias(self):
        """The alias of the mailbox delivers to the proposals of this company; switched off, it delivers to
        nobody. An alias is never deleted."""
        model = self.env["ir.model"].sudo()._get("nudge.mail.proposal")
        for company in self.sudo():
            name = (company.nudge_mail_agent_alias or "").strip().lower() or False
            alias = company.nudge_mail_agent_alias_id
            if company.nudge_mail_agent_on and name:
                values = {"alias_name": name, "alias_model_id": model.id, "alias_contact": "everyone",
                          "alias_defaults": repr({"company_id": company.id}), "alias_force_thread_id": False}
                if alias:
                    alias.write(values)
                else:
                    company.nudge_mail_agent_alias_id = self.env["mail.alias"].sudo().create(values)
            elif alias:
                alias.write({"alias_name": False})

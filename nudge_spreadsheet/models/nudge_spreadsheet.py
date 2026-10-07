import json

from odoo import api, fields, models


class NudgeSpreadsheet(models.Model):
    """A spreadsheet Nudge made for one person: a report or a comparison,
    from what it read, with its chart. Theirs: they open it, change it,
    download it."""

    _name = "nudge.spreadsheet"
    _description = "Nudge Spreadsheet"
    _inherit = ["spreadsheet.mixin"]
    _order = "id desc"

    name = fields.Char(required=True)
    user_id = fields.Many2one(
        "res.users", string="For", required=True, default=lambda self: self.env.user,
        readonly=True, index=True, ondelete="cascade")
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, readonly=True)
    conversation_id = fields.Many2one("nudge.conversation", ondelete="set null", readonly=True)

    def action_open(self):
        """:return: the client action showing this spreadsheet"""
        self.ensure_one()
        return {
            "type": "ir.actions.client",
            "tag": "nudge_spreadsheet.open",
            "name": self.name,
            "params": {"spreadsheet_id": self.id},
        }

    @api.model
    def _store_for_nudge(self, title, data, conversation_id=False):
        """Keep a spreadsheet Nudge made for the current person, where this
        database keeps spreadsheets: here, unless another module knows a
        better place (Documents).

        :param str title: the name the person sees
        :param dict data: the workbook, as o-spreadsheet keeps it
        :return: (action opening it, where it is, in words for the model)"""
        spreadsheet = self.create({
            "name": title,
            "spreadsheet_data": json.dumps(data),
            "conversation_id": conversation_id,
        })
        return spreadsheet.action_open(), "under Nudge ▸ Spreadsheets"

    @api.model
    def get_spreadsheet(self, spreadsheet_id):
        """:return: what the client action needs, as this person"""
        spreadsheet = self.browse(spreadsheet_id).exists()
        spreadsheet.check_access("read")
        return {
            "name": spreadsheet.name,
            "data": spreadsheet.spreadsheet_data or "",
            "can_write": spreadsheet.has_access("write"),
        }

    def save_spreadsheet(self, data):
        """Keep what the person changed in the editor."""
        self.ensure_one()
        self.check_access("write")
        self.spreadsheet_data = data
        return True

import json

from odoo import api, models


class NudgeSpreadsheet(models.Model):
    """A spreadsheet Nudge makes goes to Documents when the person may use
    it: the editor of Documents, its versions, its sharing. Otherwise it is
    kept as before, under Nudge."""

    _inherit = "nudge.spreadsheet"

    @api.model
    def _store_for_nudge(self, title, data, conversation_id=False):
        Document = self.env["documents.document"]
        if not Document.has_access("create"):
            return super()._store_for_nudge(title, data, conversation_id)
        values = {
            "name": title,
            "handler": "spreadsheet",
            "mimetype": "application/o-spreadsheet",
            "spreadsheet_data": json.dumps(data),
        }
        # In the person's own drive, when Documents has one
        if "owner_id" in Document._fields:
            values["owner_id"] = self.env.uid
        document = Document.create(values)
        return document.action_open_spreadsheet(), "in Documents, in the person's drive"

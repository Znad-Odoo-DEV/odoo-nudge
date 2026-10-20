"""/import without a file: what to do. With a file it never gets here: the panel
sends the file to /nudge/import/file."""
from odoo import _, api, models


class NudgeQuickImport(models.AbstractModel):
    _inherit = "nudge.quick"

    @api.model
    def _command_import(self, rest, answer, model_name):
        answer["content"] = _(
            "Attach the CSV or Excel file to your message (the paperclip), then write /import again. "
            "I match its columns to the fields of the kind of record you are on, you correct what is "
            "doubtful, and I try the file: nothing is saved.")

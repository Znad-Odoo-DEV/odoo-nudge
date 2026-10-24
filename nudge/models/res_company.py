from odoo import fields, models

# The quick actions of the panel on a phone, in the order they are shown: field name, the model of the
# app each needs (or None) and the label for the settings
MOBILE_TILES = (
    ("clock", "nudge_mobile_tile_clock", "hr.attendance"),
    ("photo", "nudge_mobile_tile_photo", None),
    ("tasks", "nudge_mobile_tile_tasks", "project.task"),
    ("record", "nudge_mobile_tile_record", None),
    ("note", "nudge_mobile_tile_note", None),
)


def app_installed(env, model_name):
    """:return: whether the app that owns ``model_name`` is installed in this database"""
    return model_name in env


class ResCompany(models.Model):
    _inherit = "res.company"

    # Not required: the column is added to companies that exist
    nudge_private_mode = fields.Boolean(
        string="Private Mode", copy=False,
        help="Never send data to an external AI provider: only providers marked as running on "
             "your own server are asked anything.")

    nudge_translation_glossary = fields.Text(
        string="Translation Glossary", copy=False,
        help="Terms that module translations by Nudge must leave as they are, one per line.")

    # Not required: the column is added to companies that exist
    nudge_private_browser_speech = fields.Boolean(
        string="Allow Browser Speech in Private Mode", copy=False,
        help="In private mode, let the speech recognition and the voices of the browser be used when no AI "
             "provider on your own server listens. Chrome and Edge may send the sound to their maker; "
             "Nudge cannot prevent it.")

    # Not required: the column is added to companies that exist
    nudge_allow_recipe_index = fields.Boolean(
        string="Allow Fetching Recipe Indexes", copy=False,
        help="In private mode, let Nudge read the recipe indexes of the marketplace and download recipe files "
             "(HTTPS only, checked against the index). It is not an AI call, but the address of the index "
             "sees your server's address.")

    # Not required: the column is added to companies that exist
    nudge_health_user_id = fields.Many2one(
        "res.users", string="Weekly Health Check By", copy=False, ondelete="set null",
        help="The Nudge manager the weekly health check of this company runs as: it looks at what they may read.")

    # Not required: the column is added to companies that exist
    nudge_coach_enabled = fields.Boolean(
        string="New-User Coach", default=True, copy=False,
        help="Show the card Your first week to people in their first 14 days, or whom a manager put in "
             "training: the key screens of their role they opened and the tours left. No AI is used. "
             "Switched off, nobody is followed.")

    # Not required: the column is added to companies that exist
    nudge_outlier_sensitivity = fields.Selection(
        [("2", "High: 2 spreads from the usual"), ("3", "Normal: 3 spreads from the usual"),
         ("4", "Low: 4 spreads from the usual")],
        string="Unusual Figures Sensitivity", default="3", copy=False,
        help="How far from the usual a figure must be to be reported, in spreads (the median absolute "
             "deviation) from the median of its group. 2 reports more, 4 only the clearest.")

    # Not required: the column is added to companies that exist
    nudge_validator_switches = fields.Json(
        string="Entry Warnings Switched", copy=False,
        help="Which warnings at entry time the company switched on or off, by check. A check not listed "
             "keeps its default: only duplicates, the format of a tax number and a bill number already "
             "used are on.")
    # Quick actions of the panel on a phone. Not required: the columns are added to companies that exist
    nudge_mobile_tile_clock = fields.Boolean(
        string="Clock in/out", default=True, copy=False,
        help="Opens the page of the Attendances app where the person checks in or out. Needs that app.")
    nudge_mobile_tile_photo = fields.Boolean(
        string="Photo of a bill", default=True, copy=False,
        help="Takes a photo and makes a draft vendor bill from it, after a yes.")
    nudge_mobile_tile_tasks = fields.Boolean(
        string="My tasks today", default=True, copy=False,
        help="Asks Nudge for the tasks of the person that are due today. Needs the Project app.")
    nudge_mobile_tile_record = fields.Boolean(
        string="Ask about this record", default=True, copy=False,
        help="Shown while a record is open: asks Nudge about it.")
    nudge_mobile_tile_note = fields.Boolean(
        string="Record a note", default=True, copy=False,
        help="Records what the person says, to be written down as a note.")
    def _nudge_mobile_tiles(self):
        """:return: the quick actions this company offers on a phone, in order, those whose app is not
        installed left out"""
        self.ensure_one()
        return [tile for tile, field, model_name in MOBILE_TILES
                if self[field] and (not model_name or app_installed(self.env, model_name))]

    # NP-13. Not required: the column is added to companies that exist
    nudge_redact_pii = fields.Boolean(
        string="Redact Personal Data", copy=False,
        help="Before a question goes to an AI provider that does not run on your own server, emails, phone "
             "numbers, IBANs, Emirates ID, national numbers and card numbers become tokens such as EMAIL_1. "
             "The answer gets the real values back here. It reduces what is sent; it cannot promise to catch "
             "every number.")

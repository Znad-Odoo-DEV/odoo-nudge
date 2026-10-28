from odoo import models


class NudgeBuilderSite(models.Model):
    _inherit = "nudge.builder.site"

    def _open_pull(self, source):
        self.ensure_one()
        self._check_designer()
        return self.env["nudge.builder.connect.pull"]._open_for(self, source)

    def action_pull_drive(self):
        """Pull pictures from a Google Drive folder (a preview first)"""
        return self._open_pull("drive")

    def action_pull_business(self):
        """Pull the places and the reviews of the Google Business Profile (a preview first)"""
        return self._open_pull("business")

    def action_pull_instagram(self):
        """Pull the latest pictures of the Instagram account (a preview first)"""
        return self._open_pull("instagram")

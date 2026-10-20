"""The tours in the search of the panel and in the /tour command: no AI."""
from odoo import _, api, models

from .nudge_tour import MAX_TOURS


class NudgeQuickTour(models.AbstractModel):
    _inherit = "nudge.quick"

    @api.model
    def find(self, words):
        found = super().find(words)
        text = " ".join(str(words or "").split())
        found["tours"] = self.env["nudge.tour"].find_tours(text)[:MAX_TOURS] if len(text) >= 2 else []
        return found

    @api.model
    def _command_tour(self, rest, answer, model_name):
        answer["tours"] = self.env["nudge.tour"].find_tours(rest)
        # A manager may record one from the answer
        answer["can_record"] = self.env.user.has_group("nudge.group_nudge_manager")
        if answer["tours"]:
            return
        answer["content"] = (
            _("No tour is called “%s”. Try other words.", rest) if rest
            else _("There is no tour yet."))

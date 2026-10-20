"""The commands that explain the screen without any AI: /why, /field, /here.

They are commands of ``nudge.quick`` like /screen or /doc: answered by the
server, with or without a provider, at no cost. The logic is in
``tools/explainer.py``.
"""
from odoo import models

from ..llm import snapshot as screen
from ..tools import explainer


class NudgeQuickExplain(models.AbstractModel):
    _inherit = "nudge.quick"

    def _explained_screen(self, answer):
        """:return: the cleaned snapshot sent with the command, or None, in
        which case ``answer`` says why"""
        snapshot = screen.sanitize(self.env.context.get("nudge_snapshot"))
        if not snapshot or not self.env["nudge.conversation"]._can_share_screen():
            answer["content"] = self.env._(
                "I cannot read the screen: Nudge reads it in the browser, and reading it may be turned off.")
            return None
        return snapshot

    def _command_why(self, rest, answer, model_name):
        snapshot = self._explained_screen(answer)
        if snapshot:
            answer["explain"] = explainer.why_cannot_save(self.env, snapshot)

    def _command_field(self, rest, answer, model_name):
        snapshot = self._explained_screen(answer)
        if snapshot:
            answer["explain"] = explainer.what_is_field(self.env, snapshot, rest)

    def _command_here(self, rest, answer, model_name):
        snapshot = self._explained_screen(answer)
        if snapshot:
            answer["explain"] = explainer.what_is_here(self.env, snapshot)

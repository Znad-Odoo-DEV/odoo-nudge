from odoo import models

from ..tools import connectors


class NudgeConversation(models.Model):
    _inherit = "nudge.conversation"

    def _chat_tools(self, mode, tool_set):
        """The tools of the connectors this database allows are given their place first; a small
        model, which gets the essential tools only, gets them too: an administrator chose them one by one"""
        connectors.sync(self.env)
        definitions = super()._chat_tools(mode, tool_set)
        if tool_set == "essential":
            named = {definition["name"] for definition in definitions}
            definitions += [tool.definition() for tool in connectors.offered(self.env, mode)
                            if tool.name not in named]
        return definitions

    def _agent_decide(self, step_id, decision):
        # A yes may come to a worker that has not built a list since the tool was allowed
        connectors.sync(self.env)
        return super()._agent_decide(step_id, decision)

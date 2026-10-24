"""S8: fewer tokens and more of them read from the cache of the provider, with no AI.

- The overview of the database is sent once in a conversation: the system prompt of every call keeps
  its lines every answer needs and the notes of the administrators, the same from one question and one
  conversation to the next; the rest goes in the context of the first question, and of the question
  after which it changed (``prompt.split_overview``).
- The tools every question of the mode is offered come first in a request, in their order, and the
  last of them is marked (``base.CACHE_POINT``): what a question adds comes after a prefix that stays
  the same, which every provider that caches by prefix reads at a lower price."""
import hashlib

from odoo import fields, models

from ..llm import base, prompt
from ..tools import base as tool_base, groups


def stable_first(definitions, mode):
    """:param definitions: the tools of a request, sorted by name (``tools.base.available``)
    :return: those every question of ``mode`` is offered (the core of the mode and every tool of no
        group) first, the last of them marked, then those of the question, each part in its order"""
    routed = groups.routed_tools() - tool_base.CORE_BY_MODE.get(mode, tool_base.CORE_BY_MODE[tool_base.MODE_TALK])
    stable = [definition for definition in definitions if definition["name"] not in routed]
    rest = [definition for definition in definitions if definition["name"] in routed]
    if stable and rest:
        stable[-1] = dict(stable[-1], **{base.CACHE_POINT: True})
    return stable + rest


class NudgeConversation(models.Model):
    _inherit = "nudge.conversation"

    overview_hash = fields.Char(
        readonly=True, copy=False, help="A digest of the overview of the database last given in full")
    overview_question_id = fields.Many2one(
        "nudge.message", readonly=True, copy=False, ondelete="set null",
        help="The question the overview of the database was last given in full with")
    overview_rest = fields.Text(
        readonly=True, copy=False,
        help="What that question was given of the overview beyond what every call is given")

    def _chat_overview(self):
        """What every call of the conversation is given of the overview: the lines every answer needs
        and the notes. The rest goes with the first question of the conversation, and with the question
        after which the overview changed (:meth:`_chat_build_turns`); a short overview whole, as before."""
        overview = super()._chat_overview()
        if len(self) != 1 or not (overview or "").strip():
            return overview
        kept, rest = prompt.split_overview(overview)
        if not rest:
            return overview
        conversation = self.sudo()
        digest = hashlib.sha256(overview.encode()).hexdigest()[:32]
        if conversation.overview_hash != digest or not conversation.overview_question_id:
            question = self.env["nudge.message"].sudo().search([
                ("conversation_id", "=", self.id), ("role", "=", "user")], order="id desc", limit=1)
            if question:
                conversation.write({"overview_hash": digest, "overview_question_id": question.id, "overview_rest": rest})
        return kept

    def _chat_build_turns(self):
        """The rest of the overview goes in the context of the question it was given with, in each of
        its rounds; a later question is not given it again, as it is not given the old screens (PERF-08).
        A summary of the earlier turns (NP-22) changes nothing: what every call is given stays."""
        turns = super()._chat_build_turns()
        conversation = self.sudo()
        given = conversation.overview_question_id
        if not given or not conversation.overview_rest:
            return turns
        latest = self.env["nudge.message"].sudo().search([
            ("conversation_id", "=", self.id), ("role", "=", "user")], order="id desc", limit=1)
        if latest != given:
            return turns
        for turn in reversed(turns):
            if turn["role"] == "user":
                turn["context"] = "\n".join(
                    part for part in (turn.get("context"), prompt.render_overview_rest(conversation.overview_rest)) if part)
                break
        return turns

    def _chat_tools(self, mode, tool_set):
        return stable_first(super()._chat_tools(mode, tool_set), mode)

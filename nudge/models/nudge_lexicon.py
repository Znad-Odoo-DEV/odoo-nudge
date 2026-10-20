from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..tools.noai_commands import lexicon, normalise


class NudgeLexiconEntry(models.Model):
    _name = "nudge.lexicon.entry"
    _inherit = ["nudge.recipe.mixin"]
    _description = "Nudge Command Word"
    _order = "intent, synonym, id"
    _rec_name = "synonym"

    synonym = fields.Char(
        string="Word or Phrase", required=True,
        help="What people of your company say, in any dialect: Nudge understands it like the words it knows.")
    intent = fields.Selection(
        lambda self: self._intents(), string="Means", required=True,
        help="The word of Nudge's own list this one means the same as.")
    dialect = fields.Selection(
        lambda self: self._dialects(), string="Dialect", required=True, default="msa",
        help="Only to keep the list tidy: a word of any dialect means the same in every sentence.")
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True)
    active = fields.Boolean(default=True)

    @api.model
    def _intents(self):
        return [(name, name) for name in sorted(lexicon.CATEGORIES)]

    @api.model
    def _dialects(self):
        return [(name, name) for name in sorted(lexicon.GROUPS)]

    @api.constrains("synonym")
    def _check_synonym(self):
        for entry in self:
            if not normalise.phrase_key(entry.synonym or ""):
                raise ValidationError(self.env._("Write a word or a phrase."))

    @api.model
    def _pairs(self):
        """:return: ``(phrase, category)`` for each word the companies of the person added"""
        entries = self.search([("company_id", "in", self.env.companies.ids)])
        return [(entry.synonym, entry.intent) for entry in entries]

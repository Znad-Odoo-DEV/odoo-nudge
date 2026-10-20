from odoo import fields, models


class NudgeBuilderPageKind(models.Model):
    """A kind of page the person can ask for in the interview"""
    _name = "nudge.builder.page.kind"
    _description = "Nudge Builder Page Kind"
    _order = "sequence, id"

    key = fields.Char(required=True, index=True)
    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)

    if hasattr(models, "Constraint"):
        _key_unique = models.Constraint("unique(key)", "A page kind has one key.")
    else:  # Odoo 17 and 18
        _sql_constraints = [("key_unique", "unique(key)", "A page kind has one key.")]

def migrate(cr, version):
    """The language of a remembered answer became a Char (any language, not only Arabic and English).
    Its old selection values go before the update: Odoo 17 calls ``ondelete`` on the field when it removes them,
    which a Char does not have, and the whole update stopped there."""
    cr.execute("""
        DELETE FROM ir_model_data
         WHERE model = 'ir.model.fields.selection'
           AND res_id IN (SELECT s.id FROM ir_model_fields_selection s
                            JOIN ir_model_fields f ON f.id = s.field_id
                           WHERE f.model = 'nudge.memory' AND f.name = 'language')""")
    cr.execute("""
        DELETE FROM ir_model_fields_selection
         WHERE field_id IN (SELECT id FROM ir_model_fields
                             WHERE model = 'nudge.memory' AND name = 'language')""")

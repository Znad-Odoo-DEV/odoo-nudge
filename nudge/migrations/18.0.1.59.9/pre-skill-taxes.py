def migrate(cr, version):
    """The skill of taxes says where Odoo 19 maps a tax under a fiscal position (on the tax, Fiscal Position and
    Replaces): the model answered with the tab of 17 and 18. Skills are kept from updates, so this one follows the
    module again for this update, unless someone other than the superuser changed it."""
    cr.execute("""
        UPDATE ir_model_data d SET noupdate = FALSE
         WHERE d.module = 'nudge' AND d.model = 'nudge.skill' AND d.name = 'skill_acc_taxes'
           AND NOT EXISTS (SELECT 1 FROM nudge_skill_revision r WHERE r.skill_id = d.res_id AND r.user_id != 1)""")

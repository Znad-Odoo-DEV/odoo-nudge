def migrate(cr, version):
    """A saved report no longer has a menu of its own (Odoo caches the visible menus by groups,
    not by person, so one person's menu could vanish for its owner): drop those menus, the
    rule that hid them and the columns that linked them; "My Reports" lists the reports now."""
    cr.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'ir_ui_menu' AND column_name = 'nudge_user_id'")
    if cr.fetchone():
        cr.execute("DELETE FROM ir_ui_menu WHERE nudge_user_id IS NOT NULL")
        cr.execute("ALTER TABLE ir_ui_menu DROP COLUMN nudge_user_id")
    cr.execute("""
        DELETE FROM ir_rule WHERE id IN (
            SELECT res_id FROM ir_model_data
             WHERE module = 'nudge' AND name = 'nudge_report_menu_rule_owner' AND model = 'ir.rule')""")
    cr.execute("DELETE FROM ir_model_data WHERE module = 'nudge' AND name = 'nudge_report_menu_rule_owner'")
    # A database older than the saved reports (57.17) has no such table yet
    cr.execute("ALTER TABLE IF EXISTS nudge_report DROP COLUMN IF EXISTS menu_id")

def migrate(cr, version):
    """Odoo 17 runs a scheduled action ``numbercall`` times (1 by default) and then archives it, so every Nudge
    cron stopped after its first run. They run without a limit again. A cron archived by hand keeps numbercall 1,
    so only the ones the mechanism switched off (numbercall 0) are switched on. Odoo 18 and 19 have no such column."""
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'ir_cron' AND column_name = 'numbercall'""")
    if not cr.fetchone():
        return
    cr.execute("""
        UPDATE ir_cron SET numbercall = -1, active = TRUE
         WHERE numbercall = 0
           AND id IN (SELECT res_id FROM ir_model_data WHERE model = 'ir.cron' AND module LIKE 'nudge%')""")

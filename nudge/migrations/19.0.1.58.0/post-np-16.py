def migrate(cr, version):
    """NP-16: the cron that ends the access of auditors runs every hour, not every day (its data record is
    noupdate, so a database that already has it is changed here)."""
    cr.execute("""
        UPDATE ir_cron SET interval_type = 'hours'
         WHERE id IN (SELECT res_id FROM ir_model_data WHERE module = 'nudge' AND name = 'cron_end_auditors'
                         AND model = 'ir.cron')
           AND interval_type = 'days' AND interval_number = 1
    """)

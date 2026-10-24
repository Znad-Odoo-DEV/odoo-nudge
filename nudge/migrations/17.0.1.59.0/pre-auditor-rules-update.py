def migrate(cr, version):
    """The first auditor rules were kept from updates and let an auditor read the rows of no company. They follow
    the module again, so this update writes the rules that show an auditor only the rows of their companies."""
    cr.execute("""
        UPDATE ir_model_data SET noupdate = FALSE
         WHERE module = 'nudge' AND model = 'ir.rule'
           AND name IN ('nudge_run_rule_auditor', 'nudge_step_rule_auditor', 'nudge_built_log_rule_auditor')""")

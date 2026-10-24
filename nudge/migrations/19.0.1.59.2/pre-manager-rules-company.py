def migrate(cr, version):
    """The rules of a Nudge manager on conversations, messages, runs, steps, tasks, summaries and coaching read
    every company and were kept from updates. They follow the module again, so this update writes the rules
    that show a manager the rows of their companies only."""
    cr.execute("""
        UPDATE ir_model_data SET noupdate = FALSE
         WHERE module = 'nudge' AND model = 'ir.rule'
           AND name IN ('nudge_conversation_rule_manager', 'nudge_message_rule_manager', 'nudge_task_rule_manager',
                        'nudge_run_rule_manager', 'nudge_step_rule_manager', 'nudge_history_rule_manager',
                        'nudge_coach_rule_manager')""")

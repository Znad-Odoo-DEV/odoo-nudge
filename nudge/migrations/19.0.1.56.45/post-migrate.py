def migrate(cr, version):
    """A rule chooses its kind of records as a model: fill it from the name."""
    cr.execute("""UPDATE nudge_task t SET rule_model_id = m.id FROM ir_model m
                   WHERE t.engine = 'rule' AND t.rule_model_id IS NULL AND m.model = t.model_name""")

def migrate(cr, version):
    """Two installs of one recipe at the same instant could leave two active lines: the newest stays, so
    that the unique index of the model can be made."""
    cr.execute("""
        UPDATE nudge_recipe_installed SET active = false
        WHERE active AND id NOT IN (
            SELECT max(id) FROM nudge_recipe_installed WHERE active
            GROUP BY source_id, entry_key, company_id)
    """)

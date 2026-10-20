def migrate(cr, version):
    """Tasks made before rules existed are AI tasks: say so, so no row reads empty."""
    cr.execute("UPDATE nudge_task SET engine = 'ai' WHERE engine IS NULL")

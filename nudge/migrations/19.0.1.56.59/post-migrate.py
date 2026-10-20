def migrate(cr, version):
    """The version of the search index lives in a sequence now, not in a parameter that
    cleared the cache of every worker whenever it moved."""
    cr.execute("DELETE FROM ir_config_parameter WHERE key = 'nudge.embedding_version'")

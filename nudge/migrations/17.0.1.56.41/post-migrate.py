def migrate(cr, version):
    """The remembered answers are a new table, created by the update itself:
    nothing to move. The folder is here so that the version is one a build
    of Odoo.sh upgrades."""

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """The documentation of Odoo comes with this version: a database updated
    from an earlier one gets it now, not at the next pass of the cron."""
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["nudge.doc.page"]._load_bundled()

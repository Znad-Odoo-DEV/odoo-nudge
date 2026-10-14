from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """The developer documentation joins the pages: loaded now rather than at
    the next pass of the cron."""
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["nudge.doc.page"]._load_bundled()

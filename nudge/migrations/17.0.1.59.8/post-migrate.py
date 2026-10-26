from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """The packages of 18 and 17 carry the documentation of their own version: loaded now rather than at
    the next pass of the cron. Where the file did not change (19), nothing is done."""
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["nudge.doc.page"]._load_bundled()

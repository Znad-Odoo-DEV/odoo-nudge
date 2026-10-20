from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Answer memory is on by default, but since 57.0 its defaults were no longer loaded at the
    install: set them where nothing is set."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["nudge.memory"]._set_defaults()

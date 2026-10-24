from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """List what Developer mode built before "Made by Nudge" existed, and tag the automations, server actions,
    scheduled actions and email templates the way views and fields always were. They are recognised by the
    steps of the log that made them (a done step that created them), or by the tag they already carry;
    never by their name."""
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["nudge.built"]._sync(tag=True)

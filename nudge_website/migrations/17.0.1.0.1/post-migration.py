"""Pages that are not public (signed-in only, password, group, not yet published) were read in
earlier versions: forget their text now, so nothing of them is found or embedded."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["nudge.source"]._purge_nonpublic_pages()

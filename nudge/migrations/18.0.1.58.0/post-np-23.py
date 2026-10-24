def migrate(cr, version):
    """The colour of Nudge follows the primary colour of Odoo by default (UI-03). The people who never chose a
    colour hold the old default, violet; they move to "Odoo colour". Teal, amber, rose and slate were chosen."""
    if not version:
        return
    cr.execute("UPDATE res_users_settings SET nudge_theme = 'odoo' WHERE nudge_theme = 'violet'")

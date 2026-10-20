from odoo import models

from ..tools.health import partner_key_sql


class ResPartner(models.Model):
    _inherit = "res.partner"

    def init(self):
        """The input validator asks "does another contact use this?" at every pause of the typing:
        each question has an index, so a table of a hundred thousand contacts answers in milliseconds."""
        super().init()
        cr = self.env.cr
        columns = [name for name in ("email_normalized", "phone", "mobile", "vat") if name in self._fields
                   and self._fields[name].store]
        for name in columns:
            if name == "email_normalized":
                expression = "email_normalized"
            elif name == "vat":
                expression = partner_key_sql("vat").replace("t.", "")
            else:
                expression = partner_key_sql("phone", name).replace("t.", "")
            cr.execute(f'CREATE INDEX IF NOT EXISTS "nudge_partner_{name}_key_idx" ON res_partner (({expression}))')
        if "name" in self._fields:
            cr.execute(
                'CREATE INDEX IF NOT EXISTS "nudge_partner_name_key_idx" ON res_partner '
                f"(({partner_key_sql('name').replace('t.', '')})) WHERE parent_id IS NULL")

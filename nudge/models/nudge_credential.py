"""NP-17: credential and certificate expiry watch.

Odoo and its e-invoicing modules store the expiry date of some certificates (the ZATCA production CSID on the
journal, the ``certificate.certificate`` of Odoo 18 and 19). Which of them exist depends on the version and on the
modules installed, so every source is looked up at run time: a model or a field that is not there is skipped.
Credentials Odoo does not store (an ETA token, a Peppol registration, a provider contract) are entered here by hand.

Only the date, the name and the company are ever read. The certificate body, the private key and the password are
never asked for: the fields of every read are the three named by the source.
"""
from collections import namedtuple

from odoo import api, fields, models

Source = namedtuple("Source", "key model date_field company_field name_field")

# The date field is the only one that carries information; the others say whose it is and what it is called.
SOURCES = (
    Source("zatca", "account.journal", "l10n_sa_production_csid_validity", "company_id", "name"),
    Source("certificate", "certificate.certificate", "date_end", "company_id", "name"),
    Source("manual", "nudge.credential", "expiry_date", "company_id", "name"),
)


class NudgeCredential(models.Model):
    """A credential entered by hand: a name, a company and the day it stops working."""

    _name = "nudge.credential"
    _description = "Credential Expiry"
    _order = "expiry_date, id"

    name = fields.Char(string="Credential", required=True,
                       help="For example: ETA token, Peppol registration. Never type the secret itself.")
    company_id = fields.Many2one("res.company", string="Company", required=True, index=True,
                                 default=lambda self: self.env.company)
    expiry_date = fields.Date(string="Expires on", required=True)
    active = fields.Boolean(default=True)
    days_left = fields.Integer(compute="_compute_days_left")

    @api.depends("expiry_date")
    def _compute_days_left(self):
        today = fields.Date.context_today(self)
        for credential in self:
            credential.days_left = (credential.expiry_date - today).days if credential.expiry_date else 0

    @api.model
    def _sources(self):
        """:return: the sources that exist in this database: the model is installed and has the fields"""
        found = []
        for source in SOURCES:
            model = self.env.get(source.model)
            if model is None:
                continue
            wanted = (source.date_field, source.company_field, source.name_field)
            if all(name in model._fields for name in wanted):
                found.append(source)
        return found

    @api.model
    def _expiries(self, company):
        """:return: ``[(source key, {"id", "date", "name"})]`` for ``company``, dates as ``date``"""
        rows = []
        # the ZATCA certificate of a journal is also a certificate.certificate on 18 and 19: told once, as ZATCA
        journals = self.env.get("account.journal")
        has_link = journals is not None and "l10n_sa_production_csid_certificate_id" in journals._fields
        already = set()
        if has_link:
            # sudo: ids only, to avoid telling the same certificate twice
            already = set(journals.sudo().search([("company_id", "=", company.id)]).mapped(
                "l10n_sa_production_csid_certificate_id").ids)
        for source in self._sources():
            # sudo: a daily job of Nudge reads the expiry date of other modules' records for the managers it tells
            model = self.env[source.model].sudo()
            # only these two fields are read (the company is in the domain): never the certificate or the key
            fields_read = [source.date_field, source.name_field]
            domain = [(source.company_field, "=", company.id), (source.date_field, "!=", False)]
            records = model.search_read(domain, fields_read)
            if source.key == "certificate":
                records = self._certificates_in_use(model, records, source)
            for record in records:
                value = record[source.date_field]
                if not value or (source.key == "certificate" and record["id"] in already):
                    continue
                day = value.date() if hasattr(value, "date") else value
                rows.append((source.key, {"id": record["id"], "date": day, "name": record[source.name_field]}))
        return rows

    @api.model
    def _certificates_in_use(self, model, records, source):
        """:return: ``records`` without the certificates already replaced: a company keeps the old
        certificates it renewed, and those are not the ones to warn about. A certificate is replaced when
        another one of the same kind (scope and subject) ends later."""
        kind_fields = [name for name in ("scope", "subject_common_name") if name in model._fields]
        kinds = {}
        if kind_fields:
            kinds = {row["id"]: tuple(row[name] for name in kind_fields)
                     for row in model.browse([record["id"] for record in records]).read(kind_fields)}
        latest = {}
        for record in records:
            if record[source.date_field]:
                kind = kinds.get(record["id"])
                latest[kind] = max(latest.get(kind, record[source.date_field]), record[source.date_field])
        return [record for record in records
                if not record[source.date_field] or record[source.date_field] >= latest[kinds.get(record["id"])]]

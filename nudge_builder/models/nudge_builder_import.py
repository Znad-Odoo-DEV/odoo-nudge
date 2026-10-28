"""The "Import content" wizard of the website builder: the content records of a site (projects, products,
services, team, testimonials, FAQ, locations, contact) from the Odoo records of the company or from an Excel/CSV
sheet, with a preview and a choice per row. Everything runs as the person: nothing is read or written with sudo,
so a row is offered only if the person may read its source, and created only if they may create the record."""
import base64
import binascii
import json

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..tools import importer

LIMIT = 200
KINDS = [
    ("project", "Projects"), ("product", "Products"), ("service", "Services"), ("member", "Team"),
    ("testimonial", "Testimonials"), ("faq", "FAQ"), ("location", "Locations"), ("contact", "Contact details")]
# The field that names a record of each kind, used to find the record that a row would update
NAME_FIELD = {"testimonial": "author", "faq": "question"}


class NudgeBuilderImport(models.TransientModel):
    _name = "nudge.builder.import"
    _description = "Website Builder: import content"

    site_id = fields.Many2one("nudge.builder.site", required=True, ondelete="cascade")
    state = fields.Selection([("setup", "Choose"), ("preview", "Preview")], default="setup")
    source = fields.Selection([("odoo", "Odoo records"), ("file", "Excel / CSV file")], default="odoo", required=True)
    kind = fields.Selection(KINDS, default="project", required=True)
    file = fields.Binary(string="Excel / CSV file", attachment=False)
    filename = fields.Char()
    picture_attachment_ids = fields.Many2many(
        "ir.attachment", "nudge_builder_import_picture_rel", "wizard_id", "attachment_id", string="Pictures",
        help="Pictures matched to the rows by file name: the name of the record, or the file name written in the row.")
    documents_available = fields.Boolean(compute="_compute_documents_available")
    documents_folder = fields.Selection(selection="_selection_folders", string="Documents folder")
    template_file = fields.Binary(attachment=False, readonly=True)
    template_name = fields.Char(readonly=True)
    line_ids = fields.One2many("nudge.builder.import.line", "wizard_id", string="Rows")
    notes = fields.Text(readonly=True)

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------
    def _documents_on(self):
        return "documents.document" in self.env.registry and "folder_id" in self.env["documents.document"]._fields

    def _compute_documents_available(self):
        on = self._documents_on()
        for wizard in self:
            wizard.documents_available = on

    @api.model
    def _selection_folders(self):
        if not self._documents_on():
            return []
        try:
            folders = self.env["documents.document"].search([("type", "=", "folder")], limit=100, order="name")
            return [(str(folder.id), folder.display_name) for folder in folders]
        except AccessError:
            return []

    # ------------------------------------------------------------------
    # Opening, template
    # ------------------------------------------------------------------
    @api.model
    def _open_for(self, site):
        site._check_designer()
        wizard = self.create({"site_id": site.id})
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": wizard.id,
                "view_mode": "form", "views": [(False, "form")], "target": "new",
                "name": self.env._("Import content")}

    def _reopen(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id,
                "view_mode": "form", "views": [(False, "form")], "target": "new",
                "name": self.env._("Import content")}

    def action_download_template(self):
        """The sheet of the chosen kind with the headers in English and Arabic"""
        self.ensure_one()
        if self.kind == "contact":
            raise UserError(self.env._("Contact details come from the company only: there is no sheet for them."))
        data, name, _mime = importer.build_template(self.kind)
        self.write({"template_file": base64.b64encode(data), "template_name": name})
        return {"type": "ir.actions.act_url", "target": "self",
                "url": "/web/content/%s/%s/template_file/%s?download=true" % (self._name, self.id, name)}

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------
    def _model(self):
        self.ensure_one()
        name = importer.MODELS.get(self.kind)
        if name and name not in self.env.registry:
            raise UserError(self.env._("This site cannot hold %s yet.", dict(KINDS)[self.kind]))
        return self.env[name] if name else None

    def action_preview(self):
        self.ensure_one()
        self.site_id._check_designer()
        if self.source == "file":
            if self.kind == "contact":
                raise UserError(self.env._("Contact details come from the company only."))
            candidates, notes = self._rows_from_file()
        else:
            candidates, notes = self._rows_from_odoo()
        pictures = self._picture_index()
        Model = self._model()
        lines = []
        for item in candidates[:LIMIT]:
            record = dict(item["values"])
            name = record.get(NAME_FIELD.get(self.kind, "name")) or ""
            ref = item.get("picture")
            found = importer.match_picture(pictures, record.get("picture"), name)
            if found:
                ref = found
            existing = self.site_id if Model is None else self._existing(Model, record)
            lines.append((0, 0, {
                "action": "update" if existing else "create",
                "name": name or self.env._("(no name)"),
                "info": item.get("info") or "",
                "values_json": json.dumps(record),
                "picture_ref": ref or "",
                "existing_id": existing.id if existing else 0,
            }))
        if len(candidates) > LIMIT:
            notes.append(self.env._("Only the first %s rows are shown.", LIMIT))
        if not lines:
            notes.append(self.env._("Nothing to import."))
        self.line_ids.unlink()
        self.write({"line_ids": lines, "state": "preview", "notes": "\n".join(notes)})
        return self._reopen()

    def action_back(self):
        self.write({"state": "setup"})
        return self._reopen()

    def _existing(self, Model, record):
        field = NAME_FIELD.get(self.kind, "name")
        value = record.get(field)
        if not value or field not in Model._fields:
            return Model.browse()
        return Model.search([("site_id", "=", self.site_id.id), (field, "=", value)], limit=1)

    def _rows_from_file(self):
        self.ensure_one()
        if not self.file:
            raise UserError(self.env._("Choose a file first."))
        try:
            raw = base64.b64decode(self.file)
            rows = importer.read_file(raw, self.filename)
        except (ValueError, binascii.Error) as error:
            raise UserError(self.env._("The file could not be read: %s", error)) from error
        records, notes = importer.rows_to_records(self.kind, rows)
        return [{"values": record} for record in records], notes

    # Odoo records: (model, domain, mapper). The search runs as the person: a model they may not read is a
    # plain message, never a sudo.
    def _rows_from_odoo(self):
        self.ensure_one()
        kind, env = self.kind, self.env
        company = self.site_id.company_id
        notes = []
        if kind == "contact":
            return [{"values": {k: v for k, v in (("phone", company.phone), ("email", company.email)) if v},
                     "info": company.name}], notes
        if kind in ("testimonial", "faq", "service"):
            raise UserError(env._("There are no Odoo records for this kind: use a sheet."))
        source = {"project": "project.project", "product": "product.template", "member": "hr.employee",
                  "location": "res.partner"}[kind]
        if source not in env.registry:
            raise UserError(env._("The app for %s is not installed.", dict(KINDS)[kind]))
        Source = env[source]
        domain = []
        if "company_id" in Source._fields:
            domain.append(("company_id", "in", [False, company.id]))
        if kind == "product":
            domain.append(("sale_ok", "=", True))
        if kind == "location":
            partner = company.partner_id
            domain = [("id", "in", (partner | partner.child_ids).ids), ("street", "!=", False)]
        try:
            records = Source.search(domain, limit=LIMIT + 1, order="id")
            rows = [self._odoo_row(kind, rec) for rec in records[:LIMIT + 1]]
        except AccessError as error:
            raise UserError(env._("You may not read these records: %s", Source._description)) from error
        return rows, notes

    def _odoo_row(self, kind, rec):
        value = {"name": rec.display_name or rec.name}
        info = ""
        picture = ""
        if kind == "project":
            if "partner_id" in rec._fields and rec.partner_id:
                value["client"] = rec.partner_id.display_name
            if "description" in rec._fields and rec.description:
                value["description"] = importer.clean(rec.description)
            if "date" in rec._fields and rec.date:
                value["year"] = rec.date.year
            info = value.get("client", "")
        elif kind == "product":
            value["price"] = rec.list_price
            if rec.categ_id:
                value["category"] = rec.categ_id.name
            if rec.description_sale:
                value["description"] = importer.clean(rec.description_sale)
            if rec.image_1920:
                picture = "%s,%s" % (rec._name, rec.id)
            info = "%s" % rec.list_price
        elif kind == "member":
            if rec.job_title:
                value["role"] = rec.job_title
            if rec.image_1920:
                picture = "%s,%s" % (rec._name, rec.id)
            info = rec.job_title or ""
        elif kind == "location":
            for key, field in (("street", "street"), ("city", "city"), ("zip", "zip"), ("phone", "phone"),
                               ("email", "email")):
                if rec[field]:
                    value[key] = rec[field]
            if rec.country_id:
                value["country"] = rec.country_id.name
            info = ", ".join(x for x in (rec.street, rec.city) if x)
        return {"values": {k: v for k, v in value.items() if v not in (False, None, "")}, "info": info,
                "picture": picture}

    def _picture_index(self):
        """:return: ``{stem of file name: "model,id"}`` of the uploaded pictures and of the Documents folder"""
        self.ensure_one()
        index = {}
        for att in self.picture_attachment_ids[:LIMIT]:
            if att.mimetype in importer.PICTURE_TYPES:
                index.setdefault(importer.stem(att.name), "ir.attachment,%s" % att.id)
        if self.documents_folder and self._documents_on():
            try:
                docs = self.env["documents.document"].search(
                    [("folder_id", "=", int(self.documents_folder)), ("mimetype", "in", list(importer.PICTURE_TYPES))],
                    limit=LIMIT)
            except (AccessError, ValueError):
                docs = self.env["documents.document"].browse()
            for doc in docs:
                index.setdefault(importer.stem(doc.name), "documents.document,%s" % doc.id)
        return index

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------
    def _picture_data(self, ref):
        """:return: ``(base64 bytes, file name, document id or 0)`` of a picture reference, read as the person"""
        if not ref:
            return None
        model, _sep, rid = ref.partition(",")
        if model not in ("ir.attachment", "documents.document", "product.template", "hr.employee") or not rid.isdigit():
            return None
        rec = self.env[model].browse(int(rid)).exists()
        if not rec:
            return None
        if model == "ir.attachment":
            data, name = rec.datas, rec.name
        elif model == "documents.document":
            att = rec.attachment_id
            data, name = (att.datas if att else rec.datas if "datas" in rec._fields else None), rec.name
        else:
            data, name = rec.image_1920, rec.display_name
        if not data or len(data) > importer.MAX_PICTURE * 4 // 3:
            return None
        return data, name, rec.id if model == "documents.document" else 0

    def _arabic_code(self):
        lang = self.env["res.lang"].search([("code", "=like", "ar%")], limit=1)
        return lang.code if lang else None

    def _values_for(self, Model, record):
        """:return: the values of a record of ``Model`` from a row; only fields the model has, texts as escaped HTML
        where the field is HTML, the country by name, a quote flag as boolean"""
        site = self.site_id
        values = {}
        for key, value in record.items():
            if key == "picture" or key.endswith("_ar"):
                continue
            if key == "country":
                country = self.env["res.country"].search([("name", "=ilike", value)], limit=1)
                if country and "country_id" in Model._fields:
                    values["country_id"] = country.id
                continue
            if key not in Model._fields:
                continue
            if isinstance(value, str) and Model._fields[key].type == "html":
                value = importer.plain_to_html(value)
            values[key] = value
        if "name" in Model._fields and not values.get("name"):
            values["name"] = values.get(NAME_FIELD.get(self.kind, "name")) or self.env._("Imported")
        if "currency_id" in Model._fields and "price" in values:
            values["currency_id"] = site.company_id.currency_id.id
        values["site_id"] = site.id
        return values

    def _arabic_values(self, Model, record):
        values = {}
        for key, value in record.items():
            if key.endswith("_ar") and key[:-3] in Model._fields and isinstance(value, str):
                field = Model._fields[key[:-3]]
                values[key[:-3]] = importer.plain_to_html(value) if field.type == "html" else value
        return values

    def _picture_vals(self, Model, ref, label):
        data = self._picture_data(ref)
        if not data or "picture_ids" not in Model._fields:
            return None
        Picture = self.env[Model._fields["picture_ids"].comodel_name]
        vals = {"name": (data[1] or label or "")[:200], "image": data[0], "alt": (label or "")[:200]}
        if data[2] and "document_id" in Picture._fields:
            vals["document_id"] = data[2]
        return {k: v for k, v in vals.items() if k in Picture._fields}

    def action_import(self):
        self.ensure_one()
        site = self.site_id
        site._check_designer()
        if self.state != "preview":
            raise UserError(self.env._("Preview the rows first."))
        done = {"create": 0, "update": 0, "skip": 0}
        if self.kind == "contact":
            for line in self.line_ids:
                if line.action == "skip":
                    done["skip"] += 1
                    continue
                record = json.loads(line.values_json or "{}")
                site.write({k: v for k, v in record.items() if k in ("phone", "email") and k in site._fields})
                done["update"] += 1
            return self._finish(done)
        Model = self._model()
        arabic = self._arabic_code()
        for line in self.line_ids:
            if line.action == "skip":
                done["skip"] += 1
                continue
            record = json.loads(line.values_json or "{}")
            values = self._values_for(Model, record)
            picture = self._picture_vals(Model, line.picture_ref, line.name)
            existing = Model.browse(line.existing_id).exists() if line.action == "update" and line.existing_id else None
            if existing and existing.site_id == site:
                values.pop("site_id", None)
                existing.write(values)
                if picture and "picture_ids" in Model._fields and not existing.picture_ids:
                    existing.write({"picture_ids": [(0, 0, picture)]})
                rec = existing
                done["update"] += 1
            else:
                if picture:
                    values["picture_ids"] = [(0, 0, picture)]
                rec = Model.create(values)
                done["create"] += 1
            if arabic:
                extra = self._arabic_values(Model, record)
                if extra:
                    rec.with_context(lang=arabic).write(extra)
        return self._finish(done)

    def _finish(self, done):
        message = self.env._(
            "%(created)s created, %(updated)s updated, %(skipped)s skipped.",
            created=done["create"], updated=done["update"], skipped=done["skip"])
        return {"type": "ir.actions.client", "tag": "display_notification",
                "params": {"title": self.env._("Import content"), "message": message, "type": "success",
                           "next": {"type": "ir.actions.act_window_close"}}}


class NudgeBuilderImportLine(models.TransientModel):
    _name = "nudge.builder.import.line"
    _description = "Website Builder: row to import"
    _order = "id"

    wizard_id = fields.Many2one("nudge.builder.import", required=True, ondelete="cascade")
    action = fields.Selection([("create", "Create"), ("update", "Update"), ("skip", "Skip")], default="create",
                              required=True)
    name = fields.Char()
    info = fields.Char()
    values_json = fields.Text()
    picture_ref = fields.Char()
    has_picture = fields.Boolean(compute="_compute_has_picture")
    existing_id = fields.Integer()

    @api.depends("picture_ref")
    def _compute_has_picture(self):
        for line in self:
            line.has_picture = bool(line.picture_ref)

import re

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import email_split

from odoo.addons.nudge.models.nudge_reply import text_to_html
from odoo.addons.nudge.tools.records import Refused, get_model, get_records

from .nudge_email_flow import check_method


class NudgeEmailReview(models.TransientModel):
    """The email of a button, shown before it goes.

    Filled in from the template for the record, every part of it can be
    changed. Send does what the button it goes with does, then sends the
    email to the addresses as written: a real To and a real Cc, which Odoo's
    composer has not (it turns every address into a recipient of one kind).
    When the button cannot do its work, nothing is sent.
    """
    _name = "nudge.email.review"
    _description = "Email to Read Before It Goes"

    # Empty for a reply Nudge drafted: there is no button behind it, and no template
    flow_id = fields.Many2one("nudge.email.flow", readonly=True, ondelete="cascade")
    res_model = fields.Char(string="Kind of Record", readonly=True)
    res_id = fields.Integer(string="Record", required=True, readonly=True)
    record_name = fields.Char(string="For", readonly=True)
    email_from = fields.Char(string="From", readonly=True)
    email_to = fields.Char(string="To")
    email_cc = fields.Char(string="Cc")
    subject = fields.Char()
    body = fields.Html(string="Contents", sanitize_style=True)
    attachment_ids = fields.Many2many("ir.attachment", string="Attachments")
    missing = fields.Char(compute="_compute_missing", string="Still To Fill In")
    without_email = fields.Boolean(related="flow_id.without_email")
    method_label = fields.Char(related="flow_id.method_label")

    # ------------------------------------------------------------ filled in
    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        flow = self.env["nudge.email.flow"].browse(values.get("flow_id")).exists()
        if not flow or not values.get("res_id"):
            return values
        record = self.env[flow.model].browse(values["res_id"]).exists()
        flow.check_open_to(record)
        values["record_name"] = record.display_name
        values.update(self._rendered(flow, record))
        return values

    @api.model
    def _rendered(self, flow, record):
        """The template, rendered for the record.

        As the system: the email shows what the administrator who designed it
        put in it, to the person the button is open to, about a record they
        can read; a field behind a group they lack must not leave the window
        blank."""
        values = {"email_from": self.env.user.email_formatted or ""}
        template = flow.template_id.sudo()
        for name, field in (("subject", "subject"), ("email_to", "email_to"),
                            ("email_cc", "email_cc"), ("body", "body_html")):
            values[name] = template._render_field(field, record.ids).get(record.id) or False
        # The contacts of the template are written as their addresses
        partner_ids = template._render_field("partner_to", record.ids).get(record.id) or ""
        partners = self.env["res.partner"].sudo().browse(
            int(part) for part in re.split(r"[,\s]+", partner_ids) if part.strip().isdigit()).exists()
        if (not partners and not values["email_to"] and template.use_default_to
                and hasattr(record, "_message_get_default_recipients")):
            # A template of Odoo names nobody: it goes to the recipients of the
            # record, the customer of a quotation, as the record's own Send has it
            recipients = record.sudo()._message_get_default_recipients().get(record.id) or {}
            partners = self.env["res.partner"].sudo().browse(recipients.get("partner_ids") or []).exists()
            values["email_to"] = recipients.get("email_to") or False
        addresses = [partner.email_formatted for partner in partners if partner.email]
        values["email_to"] = ", ".join(filter(None, [values["email_to"] or ""] + addresses))
        return values

    @api.depends("flow_id", "res_id")
    def _compute_missing(self):
        for review in self:
            review.missing = ", ".join(review._missing_labels()) or False

    def _record(self):
        self.ensure_one()
        model = self.flow_id.model or self.res_model
        record = self.env[model].browse(self.res_id).exists() if model in self.env else None
        if not record:
            raise UserError(_("The record of this email is gone."))
        return record

    def _check_open_to(self, record):
        """Checked at Send, not only when the window opened: the window comes from the browser.
        :raise UserError: when the person may not send this email for ``record``"""
        if self.flow_id:
            self.flow_id.check_open_to(record)
            return
        # A reply: any record the person may read, for a person who has Nudge
        if not self.env.user.has_group("nudge.group_nudge_user"):
            raise UserError(_("You may not answer on this record."))
        if self.res_model != record._name:
            raise UserError(_("This email is not for this record."))
        if record._name == "nudge.mail.proposal":
            # A mail the mail agent proposed: it is not a model Nudge opens to the panel, and only a
            # manager, who decides on that mail, answers it
            if not self.env.user.has_group("nudge.group_nudge_manager"):
                raise UserError(_("You may not answer on this record."))
            record._nudge_check_access("read")
            return
        try:
            get_model(self.env, record._name)
            record._nudge_check_access("read")
        except (Refused, AccessError) as error:
            raise UserError(_("You may not answer on this record.")) from error

    def _missing_labels(self):
        if not self.flow_id or not self.res_id:
            return []
        record = self.env[self.flow_id.model].browse(self.res_id).exists()
        # A record the person cannot read tells them nothing, not even what it
        # lacks: the window may have been made by hand, with any res_id
        if not record or not record._nudge_has_access("read"):
            return []
        # What must be filled is the administrator's setting: read as the
        # system, as the values are, so that a field behind a group counts
        record = record.sudo()
        return [field.field_description for field in self.flow_id.sudo().required_field_ids
                if field.name in record._fields and not record[field.name]]

    # ------------------------------------------------------------------ sent
    def _addresses(self, written, label):
        """The addresses written, or a refusal naming the line."""
        written = (written or "").strip()
        if not written:
            return []
        found = email_split(written)
        parts = [part for part in re.split(r"[,;]+", written) if part.strip()]
        if len(found) != len(parts):
            raise UserError(_(
                "%(label)s: \"%(text)s\" is not a list of email addresses separated by commas.",
                label=label, text=written))
        return found

    def _run_button(self, record):
        """What the button it goes with does, as the person: their rights
        decide. :return: what it returned, an action to open or nothing"""
        method = self.flow_id.method
        if not method:
            return None
        check_method(record, method)
        result = getattr(record, method)()
        return result if isinstance(result, dict) and result.get("type") else None

    def _note(self, record, body):
        if hasattr(record, "message_post"):
            record.message_post(body=body, message_type="comment", subtype_xmlid="mail.mt_note")

    def action_send(self):
        self.ensure_one()
        record = self._record()
        # Checked here, not only when the window opened: flow_id and res_id
        # come from the browser, and a window made by hand names any record
        self._check_open_to(record)
        sender = self.env.user
        if not sender.email:
            raise UserError(_(
                "Your user has no email address, and the email is sent from it. Set it in your "
                "preferences, then send again."))
        to = self._addresses(self.email_to, _("To"))
        cc = self._addresses(self.email_cc, _("Cc"))
        if not to:
            raise UserError(_("The email has nobody in To."))
        if not (self.subject or "").strip():
            raise UserError(_("The email has no subject."))
        missing = self._missing_labels()
        if missing:
            raise UserError(_(
                "Fill in %s on the record first: the email cannot go without it.", ", ".join(missing)))

        # The button first: what it may not do (no right to, done already by
        # somebody else) is refused here, before anything is written or sent
        opened = self._run_button(record)

        # The attachments were named by the browser too: only those the person
        # may read go with the email and onto the record
        self.attachment_ids._nudge_check_access("read")
        files = self.attachment_ids.sudo()
        # Re-pointing a file at the record needs the right to write the record, and only a
        # file the person made themselves (or this window holds) moves; a file that belongs to
        # another record is copied, so that it stays where it is for those who read it there
        if files and record._nudge_has_access("write"):
            moving = files.filtered(lambda file: file.create_uid == self.env.user
                                    and file.res_model in (self._name, False))
            copied = files - moving
            moving.write({"res_model": record._name, "res_id": record.id})
            if copied:
                files = moving | self.env["ir.attachment"].sudo().create([{
                    "name": file.name, "datas": file.datas, "mimetype": file.mimetype,
                    "res_model": record._name, "res_id": record.id,
                } for file in copied])
        header = Markup("<p><strong>%s</strong><br/>%s %s<br/>%s %s</p>") % (
            _("Email sent: %s", self.subject),
            _("To:"), ", ".join(to),
            _("Cc:"), ", ".join(cc) or "-")
        if hasattr(record, "message_post"):
            record.message_post(body=header + Markup(self.body or ""), message_type="comment",
                                subtype_xmlid="mail.mt_note", attachment_ids=files.ids)
        # As the system: outgoing emails are closed to anyone but administrators,
        # and the person may send this one, as checked above, with what was
        # checked too: addresses read by email_split, a subject and a body of
        # their own, attachments they may read
        mail = self.env["mail.mail"].sudo().create({
            "subject": self.subject,
            "body_html": self.body or "",
            "email_from": sender.email_formatted,
            "reply_to": sender.email_formatted,
            "email_to": ", ".join(to),
            "email_cc": ", ".join(cc),
            "attachment_ids": [(4, file.id) for file in files],
            "auto_delete": True,
        })
        # Raised, not swallowed: an email that did not leave must not leave
        # the record moved on as if it had. The whole of it is undone.
        mail.send(raise_exception=True)
        return opened or {"type": "ir.actions.act_window_close"}

    def action_without_email(self):
        """What the button does, and no email: the person's call, kept in
        the history so that nobody looks for an email that never went."""
        self.ensure_one()
        flow = self.flow_id
        record = self._record()
        flow.check_open_to(record)
        if not flow.without_email:
            raise UserError(_("This email cannot be skipped."))
        opened = self._run_button(record)
        self._note(record, _("%s, without the email.", flow.method_label or flow.name))
        return opened or {"type": "ir.actions.act_window_close"}

    # ----------------------------------------------------------- a drafted reply
    @api.model
    def _footer(self):
        """:return: the company under the signature, as the emails of this person carry it"""
        company = self.env.company
        parts = [company.name, company.phone, company.email, company.website]
        return Markup("<p>%s</p>") % " | ".join(part for part in parts if part)

    @api.model
    def action_draft_reply(self, model, res_id, tone="friendly", refresh=False):
        """What the button "Draft a reply" of the panel runs: Nudge writes the reply to the last
        message of the customer, from the thread, and the window of the emails opens with it. It
        is sent only when the person presses Send: signature and company under it as in their
        own emails, the notes between employees never quoted.
        :return: the window"""
        record = get_records(get_model(self.env, model), [res_id])
        return self._reply_window(record, tone, refresh)

    @api.model
    def _reply_window(self, record, tone="friendly", refresh=False):
        """The window of the reply to the last message of the customer on ``record``, filled in.
        :return: the action that opens it"""
        draft = self.env["nudge.reply"]._draft(record, tone, refresh)
        user = self.env.user
        body = text_to_html(draft["text"]) + Markup("<br/>") + Markup(user.signature or "") + self._footer()
        review = self.create({
            "res_model": record._name,
            "res_id": record.id,
            "record_name": record.display_name,
            "email_from": user.email_formatted or "",
            "email_to": draft["to"],
            "subject": draft["subject"],
            "body": body,
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("Reply"),
            "res_model": self._name,
            "res_id": review.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "new",
        }

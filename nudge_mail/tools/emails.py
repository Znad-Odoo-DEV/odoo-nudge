"""Designed emails, in Developer mode: made, tried on the person's own
address, and sent by themselves on an event or from a button with a window
to read them first.

A designed email is a mail template like any other: an automation rule sends
it, the chatter's composer offers it, Settings ‣ Technical lists it. What is
new is how it looks, and the window: the person who presses the button reads
the email for the record on screen, changes it, and Send does what the
button does and sends it.
"""
import re

from lxml import etree

from odoo.exceptions import UserError, ValidationError
from odoo.tools import html2plaintext

from odoo.addons.nudge.tools import base
from odoo.addons.nudge.tools.building import DryRun, RELOAD, make_view, preview_change, register, view_of
from odoo.addons.nudge.tools.developer import (
    CUSTOM_MODULES, DELETABLE, EMAIL_SCHEMA, Building, addressed, check_developing, ir_model, read_recipients,
)
from odoo.addons.nudge.tools.records import (
    MODEL_PATTERN, PATH_PATTERN, Refused, cut, get_model, get_records, model_label, refusing,
    target_field,
)

from ..models.nudge_email_flow import FORBIDDEN_METHODS, METHOD
from . import designs

FLOW = "nudge.email.flow"
MAX_ROWS = 20
MAX_COLUMNS = 8
MAX_ITEMS = 8
MAX_TEXT = 3000
MAX_PREVIEW_TEXT = 700
TEST_PREFIX = "[Test] "

# A flow Nudge made goes like everything else it made
if FLOW not in DELETABLE:
    DELETABLE.append(FLOW)

PATH = {"type": "string", "pattern": PATH_PATTERN}
ROW = {
    "type": "object",
    "additionalProperties": False,
    "required": ["field"],
    "properties": {
        "field": dict(PATH, description="A field of the record, or a path such as employee_id.department_id."),
        "label": {"type": "string", "maxLength": 80, "description": "The label shown; the field's own otherwise."},
    },
}
DESIGN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "model", "style", "to", "subject", "intro"],
    "properties": {
        "name": {"type": "string", "minLength": 2, "maxLength": 120},
        "model": {"type": "string", "pattern": MODEL_PATTERN},
        "style": {
            "type": "string",
            "enum": list(designs.STYLES),
            "description": "; ".join(f"{key}: {text}" for key, text in designs.STYLES.items()),
        },
        "to": EMAIL_SCHEMA["properties"]["to"],
        "cc": EMAIL_SCHEMA["properties"]["cc"],
        "subject": {"type": "string", "minLength": 1, "maxLength": 200,
                    "description": "{{ object.field }} puts a value of the record in."},
        "title": {"type": "string", "maxLength": 200, "description": "The headline of the email; the subject otherwise."},
        "greeting": {"type": "string", "maxLength": 200, "description": "Dear HR Team, / Dear {{ object.partner_id.name }},"},
        "intro": {"type": "string", "minLength": 1, "maxLength": MAX_TEXT,
                  "description": "The text; an empty line starts a paragraph; {{ object.field }} for values."},
        "details": {
            "type": "object",
            "additionalProperties": False,
            "required": ["rows"],
            "properties": {
                "title": {"type": "string", "maxLength": 80},
                "rows": {"type": "array", "minItems": 1, "maxItems": MAX_ROWS, "items": ROW},
            },
            "description": "The values of the record, as a table of labels and values.",
        },
        "lines": {
            "type": "object",
            "additionalProperties": False,
            "required": ["field", "columns"],
            "properties": {
                "title": {"type": "string", "maxLength": 80},
                "field": dict(PATH, description="The one2many or many2many of the record."),
                "columns": {"type": "array", "minItems": 1, "maxItems": MAX_COLUMNS, "items": ROW},
            },
            "description": "The lines of the record (order lines, products), as a table.",
        },
        "box": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "title": {"type": "string", "maxLength": 120},
                "items": {"type": "array", "maxItems": MAX_ITEMS, "items": {"type": "string", "maxLength": 300}},
            },
            "description": "What the reader must answer or do, set apart: 'Requested from HR', numbered.",
        },
        "closing": {"type": "string", "maxLength": 300, "description": "Best regards,"},
        "signature": {"type": "boolean", "description": "The name of the person who sends it, under the closing (default true)."},
        "button": {
            "type": "object",
            "additionalProperties": False,
            "required": ["label"],
            "properties": {"label": {"type": "string", "maxLength": 40}},
            "description": "A button that opens the record in Odoo, for readers who have a login.",
        },
        "company_footer": {"type": "boolean",
                           "description": "The logo and the name of the company under the email (default true)."},
        "template_id": {"type": "integer", "minimum": 1,
                        "description": "To change a designed email made before (its style, its text) rather than make one."},
        "record_id": {"type": "integer", "minimum": 1, "description": "The record the card shows the email for."},
    },
}


# ------------------------------------------------------------------ helpers

def mail_installed(env):
    if "mail.template" not in env or FLOW not in env:
        raise Refused("Designed emails need the app Discuss (mail) and Nudge: Emails.")


def made_here(record):
    module = (record.get_external_id().get(record.id) or "").split(".")[0]
    return module in CUSTOM_MODULES


def sample_of(model, record_id=None):
    """:return: the record an email is shown for: the one asked, else the newest"""
    if record_id:
        return get_records(model, [record_id])
    return model.search([], order="id desc", limit=1)


def render(env, template_values, record):
    """Render a template that does not exist yet, in a savepoint rolled back.
    :return: ``(subject, body)``
    :raise Refused: with what Odoo refuses"""
    rendered = {}
    try:
        with env.cr.savepoint():
            try:
                template = env["mail.template"].create(template_values)
                rendered["subject"] = template._render_field("subject", record.ids)[record.id]
                rendered["body"] = template._render_field("body_html", record.ids)[record.id]
            except (ValidationError, UserError, ValueError) as error:
                raise Refused(f"Odoo refuses this email: {cut(' '.join(str(error).split()), 500)}") from None
            except Exception as error:  # noqa: BLE001
                raise Refused(f"This email cannot be rendered: {cut(' '.join(str(error).split()), 500)}") from None
            raise DryRun()
    except DryRun:
        pass
    finally:
        env.invalidate_all()
    return rendered.get("subject") or "", str(rendered.get("body") or "")


def as_text(body):
    return cut(" ".join(html2plaintext(re.sub(r"<img\b[^>]*>", " ", body, flags=re.IGNORECASE)).split()),
               MAX_PREVIEW_TEXT)


class Mailing(Building):
    """A tool of Developer mode that needs Nudge: Emails installed here."""

    def is_available(self, env, mode=base.MODE_TALK):
        return FLOW in env and super().is_available(env, mode)


# ------------------------------------------------------------------ design

@base.register
class PrepareEmailDesign(Mailing):
    name = "prepare_email_design"
    kind = "create"
    description = (
        "Make a designed email: a mail template laid out for mail clients, in the style the "
        "person chose, with the company's colour, a title, the text, the values of the record "
        "in a table, its lines, a box of what the reader must answer, a closing signed by the "
        "sender, a button to the record, and the logo and the name of the company under it "
        "(company_footer, on unless they say otherwise). Ask the style, who receives it and who "
        "is in copy; propose the rest in their words. Text is plain: {{ object.field }} puts a "
        "value of the record in, formatted in the reader's language. Arabic text makes the "
        "email right to left. The card shows the email for a real record (record_id, else the "
        "newest). Nothing is made by this call: the person says yes or no. Once made: offer "
        "send_test_email; then prepare_automation with its template_id to send it on an event, "
        "or prepare_email_review to send it from a button after reading it. template_id changes "
        "a designed email made before."
    )
    input_schema = DESIGN_SCHEMA

    @refusing
    def run(self, env, arguments, screen):
        check_developing(env)
        mail_installed(env)
        model = get_model(env, arguments["model"])
        existing = None
        if arguments.get("template_id"):
            existing = get_records(get_model(env, "mail.template"), [arguments["template_id"]], "write")
            if existing.model != model._name:
                raise Refused(f"Template #{existing.id} is for {existing.model}, not {model._name}.")
            if not made_here(existing):
                raise Refused(
                    f"Template #{existing.id} {existing.name!r} was not designed here: it is changed in "
                    "Settings ‣ Technical ‣ Email Templates, or a new designed email is made.")
        design = dict(arguments)
        design.setdefault("signature", True)
        design.setdefault("company_footer", True)
        box = design.get("box") or {}
        design["right_to_left"] = designs.is_right_to_left(
            design["subject"], design.get("title"), design.get("greeting"), design["intro"],
            design.get("closing"), box.get("title"), *(box.get("items") or []))
        partner_to, email_to, email_cc, recipient_lines = read_recipients(env, model, design["to"], design.get("cc"))
        values = {
            "name": design["name"].strip(),
            "model_id": ir_model(env, model).id,
            "subject": designs.subject(model, design["subject"]),
            "body_html": designs.build_body(model, design),
            "partner_to": partner_to,
            "email_to": email_to,
            "email_cc": email_cc,
            "auto_delete": True,
        }
        lines = [
            {"label": env._("Model"), "value": model_label(model)},
            {"label": env._("Style"), "value": designs.style_text(env, design["style"])},
        ] + recipient_lines
        sample = sample_of(model, arguments.get("record_id"))
        if sample:
            subject, body = render(env, values, sample)
            lines += [
                {"label": env._("Subject"), "value": subject},
                {"label": env._("As %s reads it", sample.display_name), "value": as_text(body), "html": body},
            ]
        else:
            lines += [
                {"label": env._("Subject"), "value": values["subject"]},
                {"label": env._("Preview"), "value": env._("No %s yet: the email is shown for the first one.", model_label(model))},
            ]
        lines.append({"label": env._("Under it"), "value": env._("The logo and the name of the company")
                      if design["company_footer"] else env._("Nothing: no logo, no company name")})
        summary = (env._("Change: email “%s”", existing.name) if existing
                   else env._("Create: email “%s”", values["name"]))
        return base.ToolResult(
            "The person is being shown the email, as it will be read.",
            pending={
                "summary": summary,
                "res_model": "mail.template",
                "res_ids": existing.ids if existing else [],
                "record_names": values["name"],
                "values": {"template": values, "template_id": existing.id if existing else False},
                "is_sensitive": False,
                "preview": {"records": [], "lines": lines},
            },
        )

    def execute(self, env, step):
        check_developing(env)
        values = dict((step.values or {}).get("template") or {})
        if not values.get("name"):
            return base.ToolResult("This email cannot be made.", is_error=True)
        template_id = (step.values or {}).get("template_id")
        if template_id:
            template = get_records(get_model(env, "mail.template"), [template_id], "write")
            if not made_here(template):
                return base.ToolResult("This template was not designed here.", is_error=True)
            values.pop("model_id", None)
            template.write(addressed(values))
            told = f"Changed: email template #{template.id} {template.name!r}."
        else:
            template = env["mail.template"].create(addressed(values))
            register(env, template, f"email_{values['name']}")
            told = (f"Made: email template #{template.id} {template.name!r}. Offer a test to their own "
                    "address (send_test_email), then send it on an event (prepare_automation with "
                    "template_id) or from a button (prepare_email_review).")
        return base.ToolResult(
            told + " It is in Settings ‣ Technical ‣ Email Templates.",
            event={"type": "changed", "model": "mail.template", "ids": template.ids},
            records=("mail.template", template.ids, [template.name]),
        )


# -------------------------------------------------------------------- test

@base.register
class SendTestEmail(Mailing):
    name = "send_test_email"
    kind = "method"
    summary = "Sent a test email"
    description = (
        "Send an email template, rendered for one record (record_id, else the newest), to the "
        "person's own address only, its subject marked [Test]: to see it in their mail client "
        "before anyone gets it. The real recipients are not written to; the card says who they "
        "would be. Nothing is sent by this call: the person says yes or no."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["template_id"],
        "properties": {
            "template_id": {"type": "integer", "minimum": 1},
            "record_id": {"type": "integer", "minimum": 1},
        },
    }

    @staticmethod
    def _read(env, template_id, record_id):
        template = get_records(get_model(env, "mail.template"), [template_id])
        if not template.model or template.model not in env:
            raise Refused(f"Template #{template.id} is not on a model of this database.")
        record = sample_of(get_model(env, template.model), record_id)
        if not record:
            raise Refused(f"There is no {template.model_id.name} yet to render it for.")
        if not env.user.email:
            raise Refused("Your user has no email address: set it in your preferences, the test goes there.")
        return template, record

    @refusing
    def run(self, env, arguments, screen):
        check_developing(env)
        mail_installed(env)
        template, record = self._read(env, arguments["template_id"], arguments.get("record_id"))
        would_go = ", ".join(filter(None, (template.partner_to and env._("the contacts of the record"),
                                           template.email_to, template.email_cc))) or "?"
        return base.ToolResult(
            "The person is being asked whether the test goes to them.",
            pending={
                "summary": env._("Send a test of “%s” to you", template.name),
                "res_model": "mail.template",
                "res_ids": template.ids,
                "record_names": template.name,
                "values": {"template_id": template.id, "record_id": record.id},
                "is_sensitive": False,
                "preview": {"records": [], "lines": [
                    {"label": env._("To"), "value": env.user.email_formatted},
                    {"label": env._("For"), "value": record.display_name},
                    {"label": env._("Not written to"), "value": would_go},
                ]},
            },
        )

    def execute(self, env, step):
        check_developing(env)
        values = step.values or {}
        try:
            template, record = self._read(env, values.get("template_id"), values.get("record_id"))
        except Refused as refusal:
            return base.ToolResult(str(refusal), is_error=True)
        subject = template._render_field("subject", record.ids)[record.id] or template.name
        body = template._render_field("body_html", record.ids)[record.id] or ""
        mail = env["mail.mail"].sudo().create({
            "subject": TEST_PREFIX + subject,
            "body_html": body,
            "email_from": env.user.email_formatted,
            "email_to": env.user.email_formatted,
            "auto_delete": True,
        })
        mail.send(raise_exception=False)
        mail = mail.exists()
        if mail and mail.state == "exception":
            return base.ToolResult(
                f"The test did not leave: {cut(mail.failure_reason or 'the mail server refused it', 300)}. "
                "Settings ‣ Technical ‣ Emails shows it; the outgoing mail server may need a look.",
                is_error=True)
        return base.ToolResult(
            f"Sent to {env.user.email}: {TEST_PREFIX}{subject}, rendered for {record.display_name}.",
            records=("mail.template", template.ids, [template.name]),
        )


# ------------------------------------------------------------------ review

def form_methods(arch):
    """:return: ``{method: label}`` of the buttons of a form that run a method"""
    found = {}
    for node in arch.xpath("//button[@type='object'][not(ancestor::field)]"):
        name = node.get("name")
        if name and name not in found:
            found[name] = (node.get("string") or (node.text or "").strip() or name).strip()
    return found


def review_arch(action_id, label, method=None, originals=(), hide=False, invisible="", primary=True, form=None):
    """:return: the arch of the extension view that puts the button on the form"""
    data = etree.Element("data")

    def new_button(parent, copied=None):
        attributes = {"name": str(action_id), "type": "action", "string": label}
        if copied is not None:
            for key in ("invisible", "groups", "class"):
                if copied.get(key):
                    attributes[key] = copied.get(key)
        if invisible:
            attributes["invisible"] = invisible
        if primary and "oe_highlight" not in attributes.get("class", ""):
            attributes["class"] = (attributes.get("class", "") + " oe_highlight").strip()
        return etree.SubElement(parent, "button", attributes)

    if method:
        for index, original in enumerate(originals, start=1):
            path = f"(//button[@name='{method}'][@type='object'][not(ancestor::field)])[{index}]"
            new_button(etree.SubElement(data, "xpath", expr=path, position="after"), original)
        if hide:
            for index, _original in enumerate(originals, start=1):
                path = f"(//button[@name='{method}'][@type='object'][not(ancestor::field)])[{index}]"
                xpath = etree.SubElement(data, "xpath", expr=path, position="attributes")
                etree.SubElement(xpath, "attribute", name="invisible").text = "1"
    elif form is not None and form.find(".//header") is not None:
        new_button(etree.SubElement(data, "xpath", expr="//header", position="inside"))
    else:
        where = "//sheet" if form is not None and form.find(".//sheet") is not None else "//form/*[1]"
        header = etree.SubElement(etree.SubElement(data, "xpath", expr=where, position="before"), "header")
        new_button(header)
    return etree.tostring(data, encoding="unicode")


def server_action_values(env, model, flow_name, flow_id):
    return {
        "name": f"{flow_name} (email)",
        "model_id": ir_model(env, model).id,
        "state": "code",
        "usage": "ir_actions_server",
        "code": f'action = env["{FLOW}"].browse({int(flow_id)}).open_review(records)',
    }


def template_of(env, model):
    """:return: the email template of ``model``, when it has one only
    :raise Refused: naming those it has, or saying it has none
    A small model asked for a review button invented a template id."""
    templates = get_model(env, "mail.template").search([("model", "=", model._name)], order="id desc")
    if len(templates) == 1:
        return templates
    if not templates:
        raise Refused(f"{model._name} has no email template: make one first (prepare_email_design), "
                      "then give its template_id.")
    listed = ", ".join(f"#{template.id} {template.name}" for template in templates[:10])
    raise Refused(f"{model._name} has several email templates; give the template_id of the one meant: {listed}.")


@base.register
class PrepareEmailReview(Mailing):
    name = "prepare_email_review"
    kind = "create"
    description = (
        "Send an email from a button, after reading it: a button on the form opens a window "
        "with the email ready for the record (from a template, a designed one best), every part "
        "of it can be changed, and Send does what the button it goes with does (method: "
        "action_approve, action_confirm...) and sends the email with a real Cc; closing the "
        "window does neither. Ask whether the original button is hidden behind the new one "
        "(hide_original), which fields must be filled before Send (required_fields), and whether "
        "it may be done without the email (without_email). With no method, the button only "
        "sends the email. The new button is shown when, and to whom, the original is. Nothing is "
        "made by this call: the person is shown the button, the difference it makes to the "
        "form, and says yes or no."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "model", "label"],
        "properties": {
            "name": {"type": "string", "minLength": 2, "maxLength": 120},
            "model": {"type": "string", "pattern": MODEL_PATTERN},
            "template_id": {"type": "integer", "minimum": 1,
                            "description": "Left out: the email template of the model, when it has one."},
            "label": {"type": "string", "minLength": 1, "maxLength": 40, "description": "The new button: Approve."},
            "method": {"type": "string", "pattern": r"[a-z][a-z0-9_]*",
                       "description": "The method of the button it goes with, from the form."},
            "hide_original": {"type": "boolean"},
            "required_fields": {"type": "array", "maxItems": 10, "items": {"type": "string", "pattern": r"[a-z_][a-z0-9_]*"}},
            "without_email": {"type": "boolean"},
            "invisible": {"type": "string", "maxLength": 200,
                          "description": "With no method: when the button is hidden, state != 'draft'."},
        },
    }

    @refusing
    def run(self, env, arguments, screen):
        check_developing(env)
        mail_installed(env)
        model = get_model(env, arguments["model"])
        if arguments.get("template_id"):
            template = get_records(get_model(env, "mail.template"), [arguments["template_id"]])
        else:
            template = template_of(env, model)
        if template.model != model._name:
            raise Refused(f"Template #{template.id} is for {template.model}, not {model._name}.")
        root, arch_before, _view_type = view_of(env, model, {"view_type": "form"})
        form = root.sudo()._get_combined_arch()
        method = (arguments.get("method") or "").strip()
        originals = []
        method_label = ""
        groups = ""
        if method:
            if not METHOD.match(method) or method in FORBIDDEN_METHODS:
                raise Refused(f"{method} is not a button an email may go with.")
            originals = form.xpath(f"//button[@name='{method}'][@type='object'][not(ancestor::field)]")
            if not originals:
                buttons = form_methods(form)
                raise Refused(
                    f"The form of {model._description} has no button running {method}. Its buttons: "
                    + (", ".join(f"{label} ({name})" for name, label in buttons.items()) or "none") + ".")
            method_label = (originals[0].get("string") or method).strip()
            groups = originals[0].get("groups") or ""
        elif arguments.get("hide_original") or arguments.get("without_email"):
            raise Refused("hide_original and without_email go with a method: the button the email goes with.")
        fields = env["ir.model.fields"]
        for name in arguments.get("required_fields") or []:
            field = target_field(model, name)
            if field.type == "boolean":
                raise Refused(f"{name} is a checkbox: it is never empty. Use a choice or a number.")
            fields |= env["ir.model.fields"].sudo()._get(model._name, name)
        label = arguments["label"].strip()
        flow_values = {
            "name": arguments["name"].strip(),
            "model_id": ir_model(env, model).id,
            "template_id": template.id,
            "method": method or False,
            "method_label": method_label or False,
            "button_groups": groups or False,
            "required_field_ids": fields.ids,
            "without_email": bool(arguments.get("without_email")),
        }
        review = {
            "label": label,
            "method": method,
            "hide": bool(arguments.get("hide_original")),
            "invisible": (arguments.get("invisible") or "").strip(),
            "root_id": root.id,
        }

        def make_arch():
            flow = env[FLOW].create(dict(flow_values, required_field_ids=[(6, 0, fields.ids)]))
            action = env["ir.actions.server"].create(server_action_values(env, model, flow.name, flow.id))
            return review_arch(action.id, label, method, originals, review["hide"], review["invisible"], form=form)

        name = env._("Button %(label)s with an email, on %(model)s", label=label, model=model_label(model))
        view_values, diff = preview_change(env, model, root, "form", arch_before, name, make_arch=make_arch)
        lines = [
            {"label": env._("Model"), "value": model_label(model)},
            {"label": env._("Button"), "value": label},
            {"label": env._("Opens"), "value": env._("the email “%s”, to read and change", template.name)},
            {"label": env._("Send"), "value": env._("does what “%s” does, then sends the email", method_label)
             if method else env._("sends the email")},
        ]
        if method:
            lines.append({"label": env._("“%s”", method_label), "value": env._("hidden: the new button takes its place")
                          if review["hide"] else env._("kept, next to the new button")})
            lines.append({"label": env._("Without the email"), "value": env._("offered in the window")
                          if flow_values["without_email"] else env._("not offered")})
        if fields:
            lines.append({"label": env._("Filled before Send"), "value": ", ".join(fields.mapped("field_description"))})
        lines.append({"label": env._("Result"), "value": "\n".join(diff) or env._("No visible difference"), "code": True})
        return base.ToolResult(
            "The person is being shown the button, the window it opens and the difference it makes to the form.",
            pending={
                "summary": env._("Add the button “%s” with its email", label),
                "res_model": FLOW,
                "res_ids": [],
                "record_names": flow_values["name"],
                "values": {"flow": flow_values, "review": review, "view": {
                    key: view_values[key] for key in ("name", "model", "type", "inherit_id", "mode", "priority")}},
                "is_sensitive": False,
                "preview": {"records": [], "lines": lines},
            },
        )

    def execute(self, env, step):
        check_developing(env)
        mail_installed(env)
        values = step.values or {}
        flow_values, review, view = values.get("flow") or {}, values.get("review") or {}, values.get("view") or {}
        if not flow_values.get("name") or not view.get("inherit_id"):
            return base.ToolResult("This button cannot be made.", is_error=True)
        model = get_model(env, view["model"])
        root = get_records(get_model(env, "ir.ui.view"), [view["inherit_id"]])
        form = root.sudo()._get_combined_arch()
        method = review.get("method") or ""
        originals = form.xpath(f"//button[@name='{method}'][@type='object'][not(ancestor::field)]") if method else []
        if method and not originals:
            return base.ToolResult(f"The form has no button running {method} any more.", is_error=True)
        flow = env[FLOW].create(dict(flow_values, required_field_ids=[(6, 0, flow_values.get("required_field_ids") or [])]))
        action = env["ir.actions.server"].create(server_action_values(env, model, flow.name, flow.id))
        arch = review_arch(action.id, review["label"], method, originals, review.get("hide"), review.get("invisible"), form=form)
        view_record = make_view(env, dict(view, arch_db=arch))
        flow.write({"action_id": action.id, "view_id": view_record.id})
        register(env, flow, f"email_flow_{flow.name}")
        register(env, action, f"email_flow_action_{flow.name}")
        return base.ToolResult(
            f"Made: the button {review['label']!r} on {model._description}, which opens the email "
            f"{flow.template_id.name!r} to read before it goes. It is listed in Nudge ‣ Configuration ‣ "
            "Emails with Review; the form is reloaded.",
            event={"type": "changed", "model": FLOW, "ids": flow.ids, "action": RELOAD},
            records=(FLOW, flow.ids, [flow.name]),
        )


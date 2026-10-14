from odoo import SUPERUSER_ID, api

# Where what Nudge makes is registered
CUSTOM_MODULE = "nudge_custom"
RECIPIENT_FIELDS = ("partner_to", "email_to", "email_cc")


def made_templates(env):
    """:return: the email templates Nudge made: designed ones, by their xml
    id; the others, by the steps that made them or the action they came with"""
    Template = env["mail.template"].with_context(active_test=False)
    templates = Template.browse(env["ir.model.data"].search([
        ("module", "=", CUSTOM_MODULE), ("model", "=", "mail.template")]).mapped("res_id"))
    steps = env["nudge.step"].search([
        ("state", "=", "done"), ("kind", "=", "create"),
        ("res_model", "in", ["mail.template", "ir.actions.server", "base.automation"])])
    for step in steps:
        if step.res_model not in env or not step.res_ids:
            continue
        records = env[step.res_model].with_context(active_test=False).browse(step.res_ids).exists()
        if step.res_model == "base.automation":
            records = records.action_server_ids
        if records._name == "mail.template":
            templates |= records
        elif "template_id" in records._fields:
            templates |= records.template_id
    return templates.exists()


def migrate(cr, version):
    """A template Nudge made for the people it names was left with Odoo 19's
    Default Recipients on: Odoo's own sending (the email of an automation
    rule, the composer) wrote to the contact of the record instead. Run at
    the end of the update, when the models of Odoo's mail are there."""
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    if "mail.template" not in env or "use_default_to" not in env["mail.template"]._fields:
        return
    templates = made_templates(env)
    xml_ids = templates.get_external_id()
    for template in templates:
        # What a module made is the module's: an update would bring it back
        module = (xml_ids.get(template.id) or "").split(".")[0]
        if module not in ("", CUSTOM_MODULE, "__export__"):
            continue
        if template.use_default_to and any(template[name] for name in RECIPIENT_FIELDS):
            template.use_default_to = False

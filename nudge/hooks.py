import logging

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """Read the database once the installation is over: other modules may be
    installed by the same operation, after this one."""
    cron = env.ref("nudge.cron_profile_refresh", raise_if_not_found=False)
    if cron:
        cron._trigger()
    # The documentation of Odoo that comes with the module
    env["nudge.doc.page"]._load_bundled()
    # Answer memory is on after an install
    env["nudge.memory"]._set_defaults()


# What Nudge leaves in the database that no model owns: the sequence of the embeddings and the indexes of the
# duplicate warning on contacts (models/nudge_embedding.py, models/res_partner.py)
LEFTOVER_SEQUENCES = ("nudge_embedding_version_seq",)
LEFTOVER_INDEXES = (
    "nudge_partner_email_normalized_key_idx", "nudge_partner_name_key_idx",
    "nudge_partner_phone_key_idx", "nudge_partner_mobile_key_idx", "nudge_partner_vat_key_idx",
)


def uninstall_hook(env):
    """Uninstalling Nudge keeps the business data and the things it built, switched off.

    * What Nudge built (automations, scheduled actions, views, buttons, reports) is switched off: archived,
      or, for what Odoo cannot archive (server actions, reports), taken out of the menus. Nothing is deleted:
      the fields, the email templates and all values stay, and the tag ``nudge_custom`` stays on them.
    * Its own leftovers go: the sequence, the indexes on contacts and the ``nudge.*`` system parameters.
    """
    from .models.nudge_built import _plain  # noqa: PLC0415
    built = env["nudge.built"].sudo()
    built._sync()
    for thing in built.search([]):
        if not thing.can_switch_off:
            continue
        try:
            with env.cr.savepoint():
                plan = thing._off_plan()
                before, after = {}, {}
                for record, values, _what in plan:
                    key = f"{record._name},{record.id}"
                    before[key] = {name: _plain(record[name]) for name in values}
                    record.write(values)
                    after[key] = {name: _plain(record[name]) for name in values}
                if plan:
                    env["nudge.built.log"].create({
                        "built_id": thing.id, "company_id": thing.company_id.id, "action": "uninstall",
                        "summary": thing.name[:200], "before": before, "after": after})
        except Exception:  # noqa: BLE001 - one thing that cannot be switched off must not stop the uninstall
            _logger.warning("Nudge uninstall: %s %s could not be switched off", thing.res_model, thing.res_id,
                            exc_info=True)
    cr = env.cr
    for name in LEFTOVER_SEQUENCES:
        cr.execute(f'DROP SEQUENCE IF EXISTS "{name}"')
    for name in LEFTOVER_INDEXES:
        cr.execute(f'DROP INDEX IF EXISTS "{name}"')
    cr.execute("DELETE FROM ir_config_parameter WHERE key LIKE 'nudge.%'")
    env.registry.clear_cache()

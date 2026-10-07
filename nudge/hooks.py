def post_init_hook(env):
    """Read the database once the installation is over: other modules may be
    installed by the same operation, after this one."""
    cron = env.ref("nudge.cron_profile_refresh", raise_if_not_found=False)
    if cron:
        cron._trigger()
    # The documentation of Odoo that comes with the module
    env["nudge.doc.page"]._load_bundled()

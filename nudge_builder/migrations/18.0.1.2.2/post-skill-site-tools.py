from odoo import SUPERUSER_ID, api
from odoo.tools import convert_file

# The skills of the module that changed after they were first shipped: file -> the xml ids it holds
FILES = {
    "data/nudge_skill_data.xml": ("skill_website_builder",),
    "data/nudge_skill_data_sectors.xml": (
        "skill_site_construction_project", "skill_site_product_descriptions", "skill_site_clinic_services",
        "skill_site_restaurant_menu", "skill_site_real_estate_listing", "skill_site_seo_gulf",
        "skill_site_content_interview"),
}


def migrate(cr, version):
    """The website builder skill brings add_site_item, add_location and set_contact since 1.1.0, and the sector
    skills have narrower trigger words and serve Agent mode only. Skills are kept from updates (the files are
    noupdate, and Odoo skips a record of such a file whatever its flag), so a database from before still holds the
    old ones: each file is loaded again in init mode, which rewrites its records, unless someone other than the
    superuser changed one of its skills (then that file is left as it is)."""
    cr.execute("SELECT to_regclass('nudge_skill_revision')")
    if not cr.fetchone()[0]:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    for filename, names in FILES.items():
        cr.execute("""
            SELECT 1 FROM ir_model_data d
              JOIN nudge_skill_revision r ON r.skill_id = d.res_id
             WHERE d.module = 'nudge_builder' AND d.model = 'nudge.skill' AND d.name IN %s AND r.user_id != 1
             LIMIT 1""", (names,))
        if not cr.fetchone():
            convert_file(env, "nudge_builder", filename, {}, mode="init", noupdate=True, kind="data")

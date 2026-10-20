/** @odoo-module **/

import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";

/**
 * Opens Nudge and starts the setup of the database with the administrator, from the button of
 * Settings > Nudge. The sentence is the one the panel offers on a new database.
 *
 * @param {Object} env
 */
export async function startSetupAction(env) {
    const nudge = env.services.nudge;
    if (!nudge) {
        return;
    }
    nudge.open();
    await nudge.sendWithoutAi(_t("Set up this database with me"));
}

registry.category("actions").add("nudge_setup_start", startSetupAction);

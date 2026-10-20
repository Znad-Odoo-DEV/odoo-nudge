/** @odoo-module **/

import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";

/**
 * Record a tour, in the user's own menu: for Nudge managers only.
 */
export function nudgeTourMenuItem(env) {
    const nudge = env.services.nudge;
    return {
        type: "item",
        id: "nudge_record_tour",
        description: _t("Record a tour"),
        callback: () => env.services.nudge_tours.start(),
        sequence: 37,
        show: () => Boolean(nudge?.state.isAvailable && nudge.state.isManager),
    };
}

registry.category("user_menuitems").add("nudge_record_tour", nudgeTourMenuItem, { sequence: 37 });

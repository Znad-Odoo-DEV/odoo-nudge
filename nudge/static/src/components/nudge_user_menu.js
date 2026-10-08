import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";

/**
 * Nudge: On / Off, in the user's own menu, next to the other switches of the
 * person: nobody needs the Settings app to put Nudge away or bring it back.
 * Shown only to whoever may use Nudge.
 */
export function nudgeMenuItem(env) {
    const nudge = env.services.nudge;
    const available = Boolean(nudge?.state.isAvailable);
    return {
        type: "item",
        id: "nudge_toggle",
        description: available && nudge.state.isEnabled ? _t("Nudge: On") : _t("Nudge: Off"),
        callback: () => nudge.setEnabled(!nudge.state.isEnabled),
        sequence: 36,
        show: () => available,
    };
}

registry.category("user_menuitems").add("nudge_toggle", nudgeMenuItem, { sequence: 36 });

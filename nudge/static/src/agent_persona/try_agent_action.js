import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";

/**
 * Try it, from the form of an agent: open the Nudge panel for that agent, in rehearsal.
 * The panel then passes nudge_agent_id and nudge_rehearsal with every question.
 */
async function tryAgent(env, action) {
    const { agent_id: id, agent_name: name, rehearsal = true } = action.params || {};
    const nudge = env.services.nudge;
    if (!nudge?.state.isAvailable || !id) {
        env.services.notification.add(_t("Nudge is not available to you."), {
            type: "warning",
        });
        return;
    }
    nudge.openForAgent({ id, name, rehearsal });
}

registry.category("actions").add("nudge_try_agent", tryAgent);

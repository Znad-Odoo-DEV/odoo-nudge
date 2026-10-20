import { reactive } from "@odoo/owl";

import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";

// At most one call in thirty seconds, whatever the person opens in between
const FLUSH_MS = 30000;

/**
 * The coach of a new user, no AI: remembers which screens of their role they
 * opened (the action ids, nothing else, no time) and tells the server. The
 * server keeps what matters and ignores the rest; for somebody the coach does
 * not follow it answers once that it does not, and nothing more is sent.
 */
export const nudgeCoachService = {
    dependencies: ["orm", "action"],

    start(env, { orm, action }) {
        const state = reactive({ isLoaded: false, card: null });
        const pending = new Set();
        const sent = new Set();
        let following = null; // null: not known yet
        let timer = null;
        let asking = null;

        async function isFollowed() {
            if (following === null) {
                asking ||= orm
                    .call("nudge.coach", "status", [])
                    .then((status) => status?.active === true)
                    .catch(() => false);
                following = await asking;
            }
            return following;
        }

        async function flush() {
            timer = null;
            if (!(await isFollowed()) || !pending.size) {
                pending.clear();
                return;
            }
            const ids = [...pending];
            pending.clear();
            ids.forEach((id) => sent.add(id));
            try {
                await orm.call("nudge.coach", "record_visits", [ids]);
            } catch {
                ids.forEach((id) => sent.delete(id));
            }
            if (state.isLoaded) {
                load();
            }
        }

        function onScreen() {
            const id = action.currentController?.action?.id;
            if (typeof id !== "number" || id <= 0 || sent.has(id) || following === false) {
                return;
            }
            pending.add(id);
            if (timer === null) {
                timer = browser.setTimeout(flush, FLUSH_MS);
            }
        }
        env.bus.addEventListener("ACTION_MANAGER:UI-UPDATED", onScreen);

        async function load() {
            try {
                state.card = await orm.call("nudge.coach", "get_card", []);
            } catch {
                state.card = { active: false };
            }
            state.isLoaded = true;
            following = state.card.active;
            return state.card;
        }

        async function tourDone(tourId) {
            if (!(await isFollowed())) {
                return;
            }
            try {
                await orm.call("nudge.coach", "tour_done", [tourId]);
            } catch {
                return;
            }
            if (state.isLoaded) {
                load();
            }
        }

        async function finish() {
            await orm.call("nudge.coach", "finish", []);
            following = false;
            state.card = { active: false };
        }

        return { state, load, tourDone, finish };
    },
};

registry.category("services").add("nudge_coach", nudgeCoachService);

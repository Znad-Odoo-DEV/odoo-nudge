import { registry } from "@web/core/registry";
import { session } from "@web/session";

import { frontView, summarize, takeSnapshot } from "./snapshot";

/**
 * Knows which views are open, and describes the screen on request.
 *
 * Nothing is collected in the background: a snapshot is taken only when
 * `capture` is called, which happens when the person summons Nudge or sends
 * it a message.
 */
export const nudgeContextService = {
    dependencies: ["action"],

    start(env, { action }) {
        const views = new Set();
        const isEnabled = Boolean(session.nudge?.can_share_screen);

        /**
         * @param {{ type: string, controller: Object }} entry
         * @returns {() => void} to call when the view goes away
         */
        function register(entry) {
            if (!isEnabled) {
                return () => {};
            }
            views.add(entry);
            return () => views.delete(entry);
        }

        /**
         * @returns {{ snapshot: Object, summary: string } | null}
         */
        function capture() {
            if (!isEnabled) {
                return null;
            }
            try {
                const snapshot = takeSnapshot({ action, views: [...views] });
                return { snapshot, summary: summarize(snapshot) };
            } catch {
                // A screen that cannot be described is a screen not shared
                return null;
            }
        }

        return { isEnabled, register, capture, frontView: () => frontView([...views]) };
    },
};

registry.category("services").add("nudge_context", nudgeContextService);

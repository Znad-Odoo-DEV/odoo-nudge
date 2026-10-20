import { registry } from "@web/core/registry";

/**
 * Starts the recorder of tours from a button of the server (the list of recurring
 * questions), with the question as the name of the tour and as the phrase to find it by.
 *
 * @param {Object} env
 * @param {{params?: {name?: string, phrases_en?: string, phrases_ar?: string}}} action
 */
export function recordTourAction(env, action) {
    const params = action.params || {};
    env.services.nudge_tours.start({
        name: params.name,
        phrasesEn: params.phrases_en,
        phrasesAr: params.phrases_ar,
    });
}

registry.category("actions").add("nudge_record_tour", recordTourAction);

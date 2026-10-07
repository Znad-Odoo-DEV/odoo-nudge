/** @odoo-module **/

import { reactive } from "@odoo/owl";

import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";

import { isShown, resolveTarget } from "./target_resolver";

const WATCH_DELAY = 250;
// Time given to the screen to show an element before it is told missing
const MISSING_AFTER = 8;
// Time given to Odoo to react to a click before the next step is shown
const ADVANCE_DELAY = 350;
// Silence after which someone typing in a field is taken to be done
const TYPING_PAUSE = 1200;

/**
 * Walks the person through steps on their screen: shows one, waits for them
 * to do it, shows the next.
 */
export const nudgeGuidanceService = {
    dependencies: ["nudge_context", "nudge_voice", "menu"],

    start(env, { nudge_context: context, nudge_voice: voice, menu }) {
        const state = reactive({
            steps: [],
            index: 0,
            isActive: false,
            // What is pointed at now; may be a detour on the way to the target
            element: null,
            isDetour: false,
            detourText: "",
            isMissing: false,
        });
        let timer = null;
        // Changes each time a step is shown
        let showing = 0;
        let misses = 0;
        let watched = null;
        let stopWatching = null;
        let fieldValue = null;

        function currentStep() {
            return state.steps[state.index] || null;
        }

        function readField(step) {
            if (step?.target.type !== "field") {
                return undefined;
            }
            const record = context.frontView()?.controller.model?.root;
            const value = record?.data?.[step.target.name];
            if (value && typeof value === "object") {
                return JSON.stringify(value.id ?? value.resIds ?? value.currentIds ?? String(value));
            }
            return String(value);
        }

        function unwatch() {
            stopWatching?.();
            stopWatching = null;
            watched = null;
        }

        function watch(element, step, isDetour) {
            if (watched === element) {
                return;
            }
            unwatch();
            watched = element;
            if (step.advance === "manual" && !isDetour) {
                return;
            }
            let pause = null;
            const shown = showing;
            const onDone = () => {
                unwatch();
                browser.setTimeout(() => {
                    // The same step can be found done in more than one way:
                    // it is left once
                    if (shown !== showing || !state.isActive) {
                        return;
                    }
                    // A detour leads to the same step, now reachable
                    return isDetour ? look() : next();
                }, ADVANCE_DELAY);
            };
            // Someone typing is done when they leave the field, or stop typing
            const onTyping = () => {
                browser.clearTimeout(pause);
                pause = browser.setTimeout(onDone, TYPING_PAUSE);
            };
            const isField = !isDetour && step.target.type === "field";
            const kind = isField ? "change" : "click";
            element.addEventListener(kind, onDone, { capture: true });
            if (isField) {
                element.addEventListener("input", onTyping, { capture: true });
            }
            stopWatching = () => {
                browser.clearTimeout(pause);
                element.removeEventListener(kind, onDone, { capture: true });
                element.removeEventListener("input", onTyping, { capture: true });
            };
        }

        function look() {
            const step = currentStep();
            if (!state.isActive || !step) {
                return;
            }
            const found = resolveTarget(step.target, context.frontView());
            if (!found || !isShown(found.element)) {
                misses++;
                state.isMissing = misses >= MISSING_AFTER;
                if (state.isMissing) {
                    state.element = null;
                    unwatch();
                }
                return;
            }
            misses = 0;
            state.isMissing = false;
            state.isDetour = Boolean(found.isDetour);
            state.detourText = found.text || "";
            if (state.element !== found.element) {
                state.element = found.element;
                found.element.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
            }
            watch(found.element, step, state.isDetour);
            // A field changed by something else than typing: a choice in a list
            if (!state.isDetour && step.advance !== "manual") {
                const value = readField(step);
                if (fieldValue !== null && value !== undefined && value !== fieldValue) {
                    unwatch();
                    next();
                }
            }
        }

        function show(index) {
            unwatch();
            showing++;
            misses = 0;
            state.index = index;
            state.element = null;
            state.isMissing = false;
            fieldValue = readField(currentStep()) ?? null;
            // Said only to those who asked for answers to be read to them
            voice.speak(currentStep()?.text, { replace: true });
            look();
        }

        function stop() {
            if (state.isActive) {
                voice.stopSpeaking();
            }
            unwatch();
            showing++;
            browser.clearInterval(timer);
            timer = null;
            Object.assign(state, {
                steps: [],
                index: 0,
                isActive: false,
                element: null,
                isDetour: false,
                detourText: "",
                isMissing: false,
            });
        }

        function next() {
            if (!state.isActive) {
                return;
            }
            if (state.index + 1 >= state.steps.length) {
                stop();
            } else {
                show(state.index + 1);
            }
        }

        function previous() {
            if (state.isActive && state.index > 0) {
                show(state.index - 1);
            }
        }

        /**
         * Take back what the step being shown did, when it did something.
         */
        async function undo() {
            const takeBack = currentStep()?.undo;
            stop();
            await takeBack?.();
        }

        /**
         * @param {Object[]} steps
         * @param {{ type: string, name: string }} steps[].target
         * @param {string} steps[].text
         * @param {string} steps[].advance
         * @param {() => Promise} [steps[].undo] for a step that shows what Nudge did
         */
        /**
         * The way through the menus starts from the app: a person already in
         * it goes straight to its menus.
         */
        function fromWhereTheyAre(steps) {
            const first = steps[0]?.target;
            const current = menu?.getCurrentApp?.();
            if (first?.type === "menu" && first.app && steps.length > 1 && current
                && String(current.id) === String(first.name)) {
                return steps.slice(1);
            }
            return steps;
        }

        function begin(steps) {
            stop();
            if (!Array.isArray(steps) || !steps.length) {
                return;
            }
            steps = fromWhereTheyAre(steps);
            state.steps = steps;
            state.isActive = true;
            // The screen moves under the steps: tabs open, dialogs come and go
            timer = browser.setInterval(look, WATCH_DELAY);
            show(0);
        }

        return { state, begin, next, previous, stop, undo };
    },
};

registry.category("services").add("nudge_guidance", nudgeGuidanceService);

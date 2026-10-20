import { reactive } from "@odoo/owl";

import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";

import { CONTROLS, isShown } from "../guidance/target_resolver";

const OWN_UI = ".o_nudge, .o_nudge_tour_bar, .o_nudge_guide";
const NUMERIC = /^\d{1,9}$/;

/**
 * What a click was on, in the words the pointer understands: a menu, a tab,
 * a standard control, a button or a field, by its name. Never a path in the
 * page: a tour has to work on another database and another version.
 *
 * @param {Element} target
 * @returns {{ kind: string, name?: string, label: string, menu_id?: string, xmlid?: string }|null}
 */
export function describeClick(target) {
    if (!(target instanceof Element) || target.closest(OWN_UI)) {
        return null;
    }
    const label = (element) => (element.textContent || "").replace(/\s+/g, " ").trim().slice(0, 80);
    const menu = target.closest("[data-section], [data-menu-xmlid]");
    if (menu) {
        const id = menu.dataset.section;
        return {
            kind: "menu",
            menu_id: NUMERIC.test(id || "") ? id : "",
            xmlid: menu.dataset.menuXmlid || "",
            label: label(menu),
        };
    }
    const tab = target.closest(".o_notebook_headers .nav-link");
    if (tab) {
        const name = tab.getAttribute("name") || tab.dataset.name;
        return name ? { kind: "tab", name, label: label(tab) } : null;
    }
    for (const [name, selector] of Object.entries(CONTROLS)) {
        const element = target.closest(selector);
        if (element && isShown(element)) {
            return { kind: "control", name, label: label(element) || name };
        }
    }
    const button = target.closest("button[name], a[name]");
    if (button) {
        return { kind: "button", name: button.getAttribute("name"), label: label(button) };
    }
    const field = target.closest(".o_field_widget[name]");
    if (field) {
        // The label of the form, when the field has one
        const input = field.querySelector("input, textarea, select");
        const caption = input?.id && document.querySelector(`label[for="${CSS.escape(input.id)}"]`);
        return { kind: "field", name: field.getAttribute("name"), label: caption ? label(caption) : "" };
    }
    return null;
}

/**
 * Records the clicks of a manager as the steps of a tour, and replays a
 * stored tour with the pointer. Neither needs an AI provider.
 */
export const nudgeToursService = {
    dependencies: ["orm", "action", "notification", "nudge_guidance", "nudge_context"],

    start(env, { orm, action, notification, nudge_guidance: guidance, nudge_context: context }) {
        const state = reactive({
            isRecording: false,
            // "recording" while clicking, "saving" while the tour is named
            stage: "recording",
            steps: [],
            start: null,
            name: "",
            phrasesEn: "",
            phrasesAr: "",
            isSaving: false,
        });

        function screenOf() {
            const view = context.frontView();
            const controller = view?.controller;
            const props = controller?.props || {};
            const current = action.currentController?.action;
            return {
                res_model: props.resModel || "",
                view_type: controller?.env?.config?.viewType || props.type || "",
                action: (typeof current?.xml_id === "string" && current.xml_id) || "",
            };
        }

        function onClick(event) {
            const described = describeClick(event.target);
            if (!described) {
                return;
            }
            const last = state.steps.at(-1);
            // Several clicks in the same field, or on the same button, are one step
            if (
                last &&
                last.kind === described.kind &&
                (last.name || last.menu_id || last.xmlid) ===
                    (described.name || described.menu_id || described.xmlid)
            ) {
                return;
            }
            state.steps.push({ ...described, ...screenOf(), note: "" });
        }

        /**
         * @param {{name?: string, phrasesEn?: string, phrasesAr?: string}} [prefill] what the
         *      name and the phrases of the tour start with (the question of a manager's list)
         */
        function start(prefill = {}) {
            if (state.isRecording) {
                return;
            }
            env.services.nudge?.close?.();
            const current = action.currentController?.action;
            Object.assign(state, {
                isRecording: true,
                stage: "recording",
                steps: [],
                name: prefill.name || "",
                phrasesEn: prefill.phrasesEn || "",
                phrasesAr: prefill.phrasesAr || "",
                isSaving: false,
                start: current
                    ? { xml_id: typeof current.xml_id === "string" ? current.xml_id : "", id: current.id || 0 }
                    : null,
            });
            document.addEventListener("click", onClick, { capture: true });
        }

        function finish() {
            document.removeEventListener("click", onClick, { capture: true });
            state.isRecording = false;
            state.steps = [];
        }

        function stop() {
            if (state.steps.length) {
                state.stage = "saving";
            } else {
                finish();
            }
        }

        function resume() {
            state.stage = "recording";
        }

        function removeStep(index) {
            state.steps.splice(index, 1);
        }

        function setNote(index, text) {
            if (state.steps[index]) {
                state.steps[index].note = text;
            }
        }

        async function save() {
            const name = state.name.trim();
            if (!name || state.isSaving) {
                return false;
            }
            state.isSaving = true;
            try {
                await orm.call("nudge.tour", "record", [
                    name,
                    state.steps.map((step) => ({ ...step })),
                    state.phrasesEn,
                    state.phrasesAr,
                    state.start,
                ]);
            } catch {
                notification.add(_t("The tour could not be saved."), { type: "danger" });
                return false;
            } finally {
                state.isSaving = false;
            }
            finish();
            notification.add(_t("The tour “%s” is saved.", name), { type: "success" });
            return true;
        }

        /**
         * Replay a stored tour: open its start screen, then point at its steps.
         *
         * @param {number} tourId
         * @returns {Promise<boolean>} whether the pointer has something to show
         */
        async function play(tourId) {
            let data = null;
            try {
                data = await orm.call("nudge.tour", "play", [tourId]);
            } catch {
                data = null;
            }
            if (!data?.steps?.length) {
                notification.add(_t("This tour cannot be shown here."), { type: "warning" });
                return false;
            }
            env.services.nudge?.close?.();
            if (data.action) {
                try {
                    await action.doAction(data.action, { clearBreadcrumbs: true });
                } catch {
                    // The tour goes on from the screen the person is on
                }
            }
            if (data.skipped?.length) {
                notification.add(data.skipped.join(" "), {
                    title: _t("Some steps are not on this database"),
                    type: "info",
                });
            }
            guidance.begin(data.steps, () => env.services.nudge_coach?.tourDone(tourId));
            return true;
        }

        return { state, start, stop, resume, finish, removeStep, setNote, save, play };
    },
};

registry.category("services").add("nudge_tours", nudgeToursService);

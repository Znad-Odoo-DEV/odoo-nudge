/** @odoo-module **/

import { reactive } from "@odoo/owl";

import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";

const LIST_VIEWS = ["list", "kanban"];
const SPREADSHEET = /\.(csv|tsv|xlsx|xls)$/i;
const NO_AI_WORDS = /\b(without|no|not\s+using)\s+(any\s+)?(ai|llm)\b|بدون\s+(ال)?ذكاء/i;

/** @returns {boolean} whether the file is one this service reads */
export function isSpreadsheet(file) {
    return Boolean(file && SPREADSHEET.test(file.name || ""));
}

/**
 * @param {string} text what was typed
 * @returns {boolean} whether the person asks for the file without AI
 */
export function asksWithoutAi(text) {
    return NO_AI_WORDS.test(text || "");
}

function emptyState() {
    return {
        isOpen: false, busy: false, error: "", file: null, name: "", model: "", modelLabel: "",
        choices: null, columns: [], fields: [], rows: null, missing: [], check: null,
    };
}

/**
 * A CSV or Excel file matched to the fields of a model, and tried, without
 * any AI. The file stays in the browser and goes along with each request; the
 * server keeps nothing and saves nothing.
 */
export const nudgeImportService = {
    dependencies: ["action", "notification"],

    start(env, { action, notification }) {
        const state = reactive(emptyState());
        let changes = {};

        /** The model of the screen in front, when it is a list: what is imported into */
        function screenModel() {
            const controller = action.currentController;
            const type = controller?.props?.type;
            const model = controller?.props?.resModel || controller?.action?.res_model;
            return model && LIST_VIEWS.includes(type) ? model : "";
        }

        async function request(step) {
            const body = new FormData();
            body.append("csrf_token", odoo.csrf_token);
            body.append("file", state.file, state.file.name);
            body.append("step", step);
            if (state.model) {
                body.append("model", state.model);
            }
            body.append(
                "mapping",
                JSON.stringify(Object.entries(changes).map(([index, field]) => ({ index: Number(index), field })))
            );
            const response = await browser.fetch("/nudge/import/file", {
                method: "POST",
                body,
                credentials: "same-origin",
            });
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            return response;
        }

        async function run(step) {
            state.busy = true;
            state.error = "";
            try {
                const result = await (await request(step)).json();
                if (result.error) {
                    state.error = result.error;
                    return;
                }
                if (result.choose_model) {
                    state.choices = result.choose_model;
                    state.columns = [];
                    state.check = null;
                    return;
                }
                state.choices = null;
                state.model = result.model;
                state.modelLabel = result.model_label;
                state.columns = result.columns;
                state.fields = result.fields;
                state.rows = result.rows;
                state.missing = result.missing;
                state.check = result.check || null;
            } catch {
                state.error = _t("The file could not be read.");
            } finally {
                state.busy = false;
            }
        }

        /**
         * @param {File} file
         * @param {string} [model] the model, when the person said it
         */
        async function start(file, model = "") {
            changes = {};
            Object.assign(state, emptyState(), {
                isOpen: true, file, name: file.name, model: model || screenModel(),
            });
            await run("match");
            if (state.error && state.model) {
                // The screen is not somewhere a file can be imported into: ask instead
                state.model = "";
                await run("match");
            }
        }

        function pick(model) {
            changes = {};
            state.model = model;
            return run("match");
        }

        function change(index, field) {
            changes[index] = field;
            const column = state.columns[index];
            column.field = field || false;
            column.confidence = field ? 100 : 0;
            column.level = field ? "chosen" : "none";
            column.reason = field ? _t("chosen by you") : "";
            state.check = null;
        }

        function check() {
            return run("check");
        }

        async function downloadCsv() {
            state.busy = true;
            state.error = "";
            try {
                const response = await request("csv");
                const type = response.headers.get("Content-Type") || "";
                if (type.includes("json")) {
                    state.error = (await response.json()).error || _t("The file could not be read.");
                    return;
                }
                const url = URL.createObjectURL(await response.blob());
                const link = document.createElement("a");
                link.href = url;
                link.download = state.name.replace(/\.[^.]+$/, "") + "_matched.csv";
                document.body.appendChild(link);
                link.click();
                link.remove();
                browser.setTimeout(() => URL.revokeObjectURL(url), 10000);
                notification.add(
                    _t("The corrected file is downloaded: attach it on Odoo's Import screen."),
                    { type: "success" }
                );
            } catch {
                state.error = _t("The file could not be read.");
            } finally {
                state.busy = false;
            }
        }

        /** Odoo's own Import screen for the model: the corrected file is chosen there */
        function openImporter() {
            env.services.nudge.close();
            return action.doAction({
                type: "ir.actions.client",
                tag: "import",
                params: { active_model: state.model, context: {} },
            });
        }

        function close() {
            Object.assign(state, emptyState());
            changes = {};
        }

        return { state, start, pick, change, check, downloadCsv, openImporter, close };
    },
};

registry.category("services").add("nudge_import", nudgeImportService);

/** @odoo-module **/

import { Component, useState } from "@odoo/owl";

import { useService } from "@web/core/utils/hooks";
import { useRecordObserver } from "@web/model/relational_model/utils";
import { useDebounced } from "@web/core/utils/timing";
import { session } from "@web/session";

import { collectPayload } from "./validator_payload";

// Milliseconds after the last keystroke before the form is looked at
export const DEBOUNCE = 600;

// What each kind of form needs the server to look at, kept for the page
const specs = new Map();

export function forgetSpecs() {
    specs.clear();
}

/**
 * The small strip under the title of a form that says what looks wrong in the
 * values not saved yet. It informs and never blocks: nothing is disabled, the
 * person saves or not. The AI is asked only when the person clicks.
 */
export class NudgeFormStrip extends Component {
    static template = "nudge.FormStrip";
    static props = { record: Object };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ warnings: [], muted: [], answers: {}, asking: null });
        this.last = null;
        this.token = 0;
        this.enabled = Boolean(session.nudge);
        this.schedule = useDebounced(() => this.look(), DEBOUNCE);
        if (this.enabled) {
            // What is read of the record in the callback is what makes it run again when it changes
            useRecordObserver((record) => {
                this.touch(record);
                this.schedule();
            });
        }
    }

    get record() {
        return this.props.record;
    }

    /** Read the values of the form, so that a change of any of them is noticed */
    touch(record) {
        const seen = [record.dirty, record.isNew];
        for (const name of Object.keys(record.activeFields)) {
            const value = record.data[name];
            if (value?.records) {
                for (const line of value.records) {
                    for (const column of Object.keys(line.activeFields || {})) {
                        seen.push(line.data[column]);
                    }
                }
            } else {
                seen.push(value);
            }
        }
        return seen;
    }

    async spec(model) {
        if (!specs.has(model)) {
            specs.set(
                model,
                this.orm.silent
                    .call("nudge.validator", "get_spec", [model])
                    .catch(() => ({ checks: [], fields: [], lines: {} }))
            );
        }
        return specs.get(model);
    }

    async payload() {
        const record = this.record;
        if (!record || !record.dirty) {
            return null;
        }
        const spec = await this.spec(record.resModel);
        return spec.checks?.length ? collectPayload(record, spec) : null;
    }

    async look() {
        const token = ++this.token;
        let payload;
        try {
            payload = await this.payload();
        } catch {
            return;
        }
        if (token !== this.token) {
            return;
        }
        if (!payload) {
            this.last = null;
            this.state.warnings = [];
            return;
        }
        const sent = JSON.stringify(payload);
        if (sent === this.last) {
            return;
        }
        this.last = sent;
        this.payloadSent = payload;
        try {
            const { warnings } = await this.orm.silent.call("nudge.validator", "check", [payload]);
            if (token === this.token) {
                this.state.warnings = warnings;
            }
        } catch {
            // The form works without Nudge
        }
    }

    /** Open the records the warning is about, beside the form: the form is left as it is */
    show(warning) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: warning.label,
            res_model: warning.model,
            views: [[false, "list"]],
            domain: [["id", "in", warning.ids]],
            target: "new",
        });
    }

    async mute(warning) {
        await this.orm.silent.call("nudge.validator", "mute", [warning.check, true]);
        specs.delete(this.record.resModel);
        this.state.warnings = this.state.warnings.filter((item) => item.check !== warning.check);
        if (!this.state.muted.some((item) => item.check === warning.check)) {
            this.state.muted.push({ check: warning.check, label: warning.label });
        }
    }

    async unmute(item) {
        await this.orm.silent.call("nudge.validator", "mute", [item.check, false]);
        specs.delete(this.record.resModel);
        this.state.muted = this.state.muted.filter((other) => other.check !== item.check);
        this.last = null;
        this.look();
    }

    /** The second opinion of the AI, on the click of the person only */
    async ask(warning) {
        this.state.asking = warning.key;
        try {
            const { text } = await this.orm.call("nudge.validator", "ask", [
                this.payloadSent,
                warning.check,
            ]);
            this.state.answers[warning.key] = text || "";
        } finally {
            this.state.asking = null;
        }
    }
}

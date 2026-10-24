import { Component, onWillDestroy, reactive, useEffect, useRef, useState } from "@odoo/owl";

import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";

/**
 * S11: what did not end as asked, written as a request the person can send to set it again.
 *
 * @param {Array<{label: string, record: string, asked: any}>} fields the differences read after a change
 * @returns {string}
 */
export function fixSentence(fields) {
    return (fields || [])
        .map((entry) =>
            _t("Set %(field)s of “%(record)s” to %(value)s again.", {
                field: entry.label,
                record: entry.record,
                value: entry.asked === null || entry.asked === undefined ? "—" : String(entry.asked),
            })
        )
        .join(" ");
}

/**
 * The request to set it again goes in the message box: nothing is sent before the person sends it.
 *
 * @param {Object} env the environment of a component
 * @param {Object} verify what was read again after a change
 */
export function draftFix(env, verify) {
    const nudge = env.services.nudge;
    if (!nudge || !verify?.fields?.length) {
        return;
    }
    nudge.state.draft = fixSentence(verify.fields);
    nudge.state.focusRequest++;
}

/**
 * S10: several changes asked at once, as one card with numbered steps. Before the yes, a step can
 * be taken out or a value of its own corrected; after it, each step shows its own card in turn (or
 * the one yes did them all, when the administrator chose it), and the card says how far it went.
 */
export class NudgePlanCard extends Component {
    static template = "nudge.PlanCard";
    static props = {
        card: Object,
        cardState: { type: String, optional: true },
        onDecide: { type: Function, optional: true },
        onUndo: { type: Function, optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.edit = useState({ n: 0, field: "", value: "", busy: false });
        // The stream of the plan may end without a last word (stopped, the page left): read it again
        this.nudgeState = useState(this.env.services.nudge?.state || { isStreaming: false });
        this.alive = true;
        onWillDestroy(() => {
            this.alive = false;
        });
        const input = useRef("editInput");
        useEffect(
            (el) => el?.focus(),
            () => [input.el]
        );
        useEffect(
            (streaming, state) => {
                if (!streaming && state === "working") {
                    this.refresh();
                }
            },
            () => [this.nudgeState.isStreaming, this.props.cardState]
        );
    }

    /** The card, watched to its depth: a step taken out or a value corrected draws it again */
    get card() {
        if (this.watchedSource !== this.props.card) {
            this.watchedSource = this.props.card;
            this.watched = reactive(this.props.card, () => this.alive && this.render());
        }
        return this.watched;
    }

    get phase() {
        return this.card.plan_phase;
    }

    get canChange() {
        return this.props.cardState === "pending" && this.phase === "proposed";
    }

    get waitsForStep() {
        return this.props.cardState === "pending" && this.phase === "running" && Boolean(this.card.current);
    }

    get stopped() {
        return this.props.cardState === "pending" && this.phase === "stopped";
    }

    get running() {
        return this.props.cardState === "working" || this.props.cardState === "deciding";
    }

    get todoCount() {
        return this.card.plan_steps.filter((step) => step.state === "todo").length;
    }

    get intro() {
        return this.card.one_yes
            ? _t("One yes does every step below, as it is shown. Take out a step, or correct a value, first.")
            : _t("After your yes, each change shows its own card before it is done. Take out a step, or correct a value, first.");
    }

    get confirmLabel() {
        return this.card.one_yes
            ? _t("Yes, do the %s steps", this.todoCount)
            : _t("Yes, go through the plan");
    }

    get outcome() {
        const card = this.card;
        const total = card.plan_steps.filter((step) => step.state !== "removed").length;
        if (this.running) {
            return _t("Working on it: %(done)s of %(total)s steps done.", { done: card.done_count, total });
        }
        if (card.undone) {
            return _t("Undone");
        }
        const failed = card.plan_steps.find((step) => step.state === "failed");
        const unlike = card.plan_steps.some((step) => step.verify && !step.verify.ok);
        switch (this.props.cardState) {
            case "done":
                return unlike
                    ? _t("Done, but not everything is as asked: look at the steps marked.")
                    : _t("All %s steps done.", card.done_count);
            case "failed":
                return _t("%(done)s of %(total)s steps done. Step %(step)s did not work; the steps after it were not done.", {
                    done: card.done_count,
                    total,
                    step: failed ? failed.n : "?",
                });
            case "declined":
                return _t("You said no");
            case "cancelled":
                return _t("Left undecided: %(done)s of %(total)s steps done.", { done: card.done_count, total });
        }
        if (this.stopped) {
            return _t("Stopped: %(done)s of %(total)s steps done. What is done stays; resume to go on.", {
                done: card.done_count,
                total,
            });
        }
        return "";
    }

    /** @returns {string} where a step is, said for a screen reader too */
    stepStateLabel(step) {
        return (
            {
                todo: _t("To do"),
                waiting: _t("Waiting for your yes"),
                done: _t("Done"),
                failed: _t("Did not work"),
                removed: _t("Taken out"),
            }[step.state] || ""
        );
    }

    stepIcon(step) {
        if (this.running && step.state === "todo" && step === this.card.plan_steps.find((each) => each.state === "todo")) {
            return "fa-circle-o-notch fa-spin";
        }
        return (
            {
                todo: "fa-circle-o",
                waiting: "fa-hand-o-right",
                done: step.verify && !step.verify.ok ? "fa-exclamation-circle" : "fa-check",
                failed: "fa-times",
                removed: "fa-ban",
            }[step.state] || "fa-circle-o"
        );
    }

    removeLabel(step) {
        return _t("Take step %s out of the plan", step.n);
    }

    editLabel(line) {
        return _t("Correct %s", line.label);
    }

    removedText(step) {
        return step.removed_with
            ? _t("Taken out with step %s: it uses what that step makes.", step.removed_with)
            : _t("Taken out: it will not be done.");
    }

    refsText(step) {
        return _t("Uses what step %s makes", step.refs.join(", "));
    }

    waitsFor(step) {
        return this.waitsForStep && step.n === this.card.current;
    }

    canEdit(step, line) {
        return this.canChange && !this.edit.busy && !line.lines && !line.ref && step.editable.includes(line.field);
    }

    isEditing(step, line) {
        return this.edit.n === step.n && this.edit.field === line.field;
    }

    /**
     * @param {any} value as the server read it for the field
     * @returns {string} as the person reads it
     */
    show(value) {
        if (value === true) {
            return _t("Yes");
        }
        if (value === false) {
            return _t("No");
        }
        if (value === null || value === undefined || value === "") {
            return "—";
        }
        return String(value);
    }

    decide(decision) {
        this.props.onDecide?.(this.card.step_id, decision);
    }

    fix(verify) {
        draftFix(this.env, verify);
    }

    async call(method, args) {
        this.edit.busy = true;
        try {
            const card = await this.orm.call("nudge.step", method, args);
            Object.assign(this.card, card);
            return true;
        } catch (error) {
            this.notification.add(error?.data?.message || error?.message || _t("This could not be changed."), {
                type: "danger",
            });
            return false;
        } finally {
            this.edit.busy = false;
        }
    }

    async remove(step) {
        await this.call("plan_remove_step", [this.card.step_id, step.n]);
    }

    startEdit(step, line) {
        Object.assign(this.edit, { n: step.n, field: line.field, value: line.value === null ? "" : String(line.value) });
    }

    cancelEdit() {
        Object.assign(this.edit, { n: 0, field: "", value: "" });
    }

    async saveEdit() {
        const { n, field, value } = this.edit;
        if (await this.call("plan_edit_step", [this.card.step_id, n, field, value])) {
            this.cancelEdit();
        }
    }

    onEditKeydown(event) {
        if (event.key === "Enter") {
            event.preventDefault();
            this.saveEdit();
        } else if (event.key === "Escape") {
            event.preventDefault();
            this.cancelEdit();
        }
    }

    async refresh() {
        try {
            const card = await this.orm.silent.call("nudge.step", "plan_card", [this.card.step_id]);
            if (this.alive) {
                Object.assign(this.card, card);
            }
        } catch {
            // The card stays as it was; the page read again shows it as it is
        }
    }
}

import { Component, useEffect, useRef, useState } from "@odoo/owl";

import { _t } from "@web/core/l10n/translation";

import { renderMarkdown } from "../core/markdown";
import { NudgeChart } from "./nudge_chart";

/**
 * Receives plain values rather than the message object: the answer grows
 * while it is displayed, and a changed value is what triggers a new rendering.
 */
export class NudgeMessage extends Component {
    static template = "nudge.Message";
    static components = { NudgeChart };
    static props = {
        role: String,
        content: String,
        state: String,
        error: { type: String, optional: true },
        // What Nudge was shown with this message, in a few words
        screen: { type: String, optional: true },
        // Steps the answer points at on the screen
        guidance: { type: Array, optional: true },
        onShowGuidance: { type: Function, optional: true },
        // How the person wants the steps: "pending" until they say, then "text" or "screen"
        choice: { type: String, optional: true },
        onChoose: { type: Function, optional: true },
        // The message was said, not typed
        spoken: { type: Boolean, optional: true },
        attachment: { type: String, optional: true },
        // Said by the person about the answer: "up", "down" or nothing yet
        feedback: { type: String, optional: true },
        ratable: { type: Boolean, optional: true },
        // Answered from the memory of answers, with no AI; the AI can be asked instead
        remembered: { type: Boolean, optional: true },
        onAskAi: { type: Function, optional: true },
        onRate: { type: Function, optional: true },
        // What Nudge is about to do, or did: shown before anything is done
        card: { type: [Object, { value: null }], optional: true },
        // What came of it: given apart, because it changes while the card is shown
        cardState: { type: String, optional: true },
        cardError: { type: String, optional: true },
        onDecide: { type: Function, optional: true },
        // What was written can be taken back once
        cardCanUndo: { type: Boolean, optional: true },
        onUndo: { type: Function, optional: true },
        // Worked on in the background: what Nudge is doing now
        runId: { type: [Number, { value: null }], optional: true },
        progress: { type: String, optional: true },
        onStop: { type: Function, optional: true },
        // Products found for the person: cards with their picture, price and stock
        products: { type: [Array, { value: null }], optional: true },
        onOpenProduct: { type: Function, optional: true },
        // What a command of the message box found without any AI
        found: { type: [Object, { value: null }], optional: true },
        // A chart drawn from the figures of the business
        chart: { type: [Object, { value: null }], optional: true },
        // Who changed a record: the changes, each with a button to put the old value back
        changes: { type: [Object, { value: null }], optional: true },
        onRestore: { type: Function, optional: true },
        onFound: { type: Function, optional: true },
    };

    setup() {
        this.feedback = useState({ asking: false, note: "" });
        // A no asks why: the cursor waits in the box
        const note = useRef("note");
        useEffect(
            (el) => el?.focus(),
            () => [note.el]
        );
    }

    get canRate() {
        return Boolean(this.isAssistant && this.props.ratable && this.props.onRate && this.props.state === "complete");
    }

    rate(value) {
        this.feedback.asking = value === "down";
        this.props.onRate(value, "");
    }

    onNoteKeydown(event) {
        if (event.key === "Enter") {
            event.preventDefault();
            this.sendNote();
        }
    }

    sendNote() {
        const note = this.feedback.note.trim();
        this.feedback.asking = false;
        if (note) {
            this.props.onRate("down", note);
        }
    }

    get isAssistant() {
        return this.props.role === "assistant";
    }

    get isWaiting() {
        return this.props.state === "streaming" && !this.props.content && !this.props.card;
    }

    /** @returns {string} the cell of a query as it is read */
    cell(value) {
        return value === null || value === undefined ? "" : String(value);
    }

    get isInBackground() {
        return this.props.state === "queued";
    }

    get html() {
        return renderMarkdown(this.props.content);
    }

    get outcome() {
        const outcomes = {
            deciding: _t("Working on it…"),
            working: _t("Being done in the background: I'll tell you when it is over."),
            done: _t("Done"),
            failed: _t("It did not work"),
            declined: _t("You said no"),
            cancelled: _t("Left undecided"),
        };
        const card = this.props.card;
        if (card?.undone) {
            // Before the count of a plan: "5 done" stayed on a card that was undone
            return _t("Undone");
        }
        if (card?.plan && this.props.cardState === "done") {
            if (!card.failed_count) {
                return _t("%s done", card.done_count || 0);
            }
            return _t("%(done)s done · %(failed)s did not go through", {
                done: card.done_count || 0,
                failed: card.failed_count,
            });
        }
        return outcomes[this.props.cardState] || "";
    }

    get passingLabel() {
        return _t("Only the %s that go through", this.props.card?.plan?.passing || 0);
    }

    /** @returns {string} how many records a plan takes, and how many go through */
    get planCount() {
        const plan = this.props.card?.plan;
        if (!plan) {
            return "";
        }
        if (plan.passing === plan.total) {
            return _t("%s records: all go through, as tried just now.", plan.total);
        }
        return _t("%(total)s records: %(passing)s go through, %(failing)s do not, as tried just now.", {
            total: plan.total,
            passing: plan.passing,
            failing: plan.total - plan.passing,
        });
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
        this.props.onDecide?.(this.props.card.step_id, decision);
    }
}

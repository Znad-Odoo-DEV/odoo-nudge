import { Component, useEffect, useRef, useState } from "@odoo/owl";

import { _t } from "@web/core/l10n/translation";

import { renderMarkdown } from "../core/markdown";

/**
 * Receives plain values rather than the message object: the answer grows
 * while it is displayed, and a changed value is what triggers a new rendering.
 */
export class NudgeMessage extends Component {
    static template = "nudge.Message";
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
        onRate: { type: Function, optional: true },
        // What Nudge is about to do, or did: shown before anything is done
        card: { type: [Object, { value: null }], optional: true },
        // What came of it: given apart, because it changes while the card is shown
        cardState: { type: String, optional: true },
        cardError: { type: String, optional: true },
        onDecide: { type: Function, optional: true },
        // Worked on in the background: what Nudge is doing now
        runId: { type: [Number, { value: null }], optional: true },
        progress: { type: String, optional: true },
        onStop: { type: Function, optional: true },
        // Products found for the person: cards with their picture, price and stock
        products: { type: [Array, { value: null }], optional: true },
        onOpenProduct: { type: Function, optional: true },
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

    get isInBackground() {
        return this.props.state === "queued";
    }

    get html() {
        return renderMarkdown(this.props.content);
    }

    get outcome() {
        const outcomes = {
            deciding: _t("Working on it…"),
            done: _t("Done"),
            failed: _t("It did not work"),
            declined: _t("You said no"),
            cancelled: _t("Left undecided"),
        };
        return outcomes[this.props.cardState] || "";
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

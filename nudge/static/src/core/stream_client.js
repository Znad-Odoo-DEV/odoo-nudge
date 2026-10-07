/** @odoo-module **/

import { browser } from "@web/core/browser/browser";

import { NdjsonParser } from "./ndjson";

export class NudgeStreamError extends Error {}

/**
 * Post a form and report the answer, a stream of JSON lines, as it comes.
 *
 * @param {string} url
 * @param {Object} fields what is posted; what is empty is left out
 * @param {Object} params
 * @param {AbortSignal} [params.signal]
 * @param {(event: Object) => void} params.onEvent
 */
export async function streamRequest(url, fields, { signal, onEvent }) {
    // The token of an "http" route is read from the form, not from a JSON body
    const body = new FormData();
    body.append("csrf_token", odoo.csrf_token);
    for (const [name, value] of Object.entries(fields)) {
        if (value !== undefined && value !== null && value !== false && value !== "") {
            // A File goes as it is, with its name
            body.append(name, value === true ? "1" : value);
        }
    }
    const response = await browser.fetch(url, {
        method: "POST",
        body,
        signal,
        credentials: "same-origin",
    });
    if (!response.ok) {
        throw new NudgeStreamError(`HTTP ${response.status}`);
    }
    const parser = new NdjsonParser(onEvent);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    for (;;) {
        const { done, value } = await reader.read();
        if (done) {
            break;
        }
        parser.push(decoder.decode(value, { stream: true }));
    }
    parser.push(decoder.decode());
    parser.flush();
}

/**
 * Send a message and report the answer as it is generated.
 *
 * @param {Object} params
 * @param {string} params.message
 * @param {number|null} [params.conversationId]
 * @param {Object|null} [params.snapshot] description of the screen, when shared
 * @param {boolean} [params.spoken] the message was said, not typed
 * @param {boolean} [params.readAloud] the answer will be read to the person
 * @param {string} [params.mode] "agent" when Nudge is asked to do the work
 * @param {boolean} [params.background] the work is done while the person
 *   does something else
 * @param {AbortSignal} [params.signal]
 * @param {(event: Object) => void} params.onEvent
 */
export function streamChat({
    message,
    conversationId,
    snapshot,
    spoken,
    readAloud,
    mode,
    background,
    explain,
    file,
    report,
    signal,
    onEvent,
}) {
    return streamRequest(
        "/nudge/chat/stream",
        {
            message,
            conversation_id: conversationId,
            snapshot: snapshot ? JSON.stringify(snapshot) : null,
            spoken: Boolean(spoken),
            read_aloud: Boolean(readAloud),
            mode: mode === "agent" ? "agent" : null,
            background: Boolean(background),
            explain: explain || null,
            file: file || null,
            report: report || null,
        },
        { signal, onEvent }
    );
}

/**
 * Say yes or no to what Nudge is about to do, and report what comes of it.
 *
 * @param {Object} params
 * @param {number} params.conversationId
 * @param {number} params.stepId
 * @param {"confirm"|"decline"} params.decision
 * @param {AbortSignal} [params.signal]
 * @param {(event: Object) => void} params.onEvent
 */
export function streamDecision({ conversationId, stepId, decision, signal, onEvent }) {
    return streamRequest(
        "/nudge/agent/decide",
        { conversation_id: conversationId, step_id: stepId, decision },
        { signal, onEvent }
    );
}

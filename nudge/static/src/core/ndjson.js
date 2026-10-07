/** @odoo-module **/

/**
 * Incremental parser for a stream of JSON lines. Network chunks can end in
 * the middle of a line, so the tail is kept until its end arrives.
 */
export class NdjsonParser {
    /**
     * @param {(event: Object) => void} onEvent
     */
    constructor(onEvent) {
        this.onEvent = onEvent;
        this.buffer = "";
    }

    /**
     * @param {string} chunk
     */
    push(chunk) {
        this.buffer += chunk;
        const lines = this.buffer.split("\n");
        this.buffer = lines.pop();
        for (const line of lines) {
            this._emit(line);
        }
    }

    flush() {
        const line = this.buffer;
        this.buffer = "";
        this._emit(line);
    }

    _emit(line) {
        line = line.trim();
        if (!line) {
            return;
        }
        let event;
        try {
            event = JSON.parse(line);
        } catch {
            return;
        }
        if (event && typeof event === "object") {
            this.onEvent(event);
        }
    }
}

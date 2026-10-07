/** @odoo-module **/

import { Component, useState } from "@odoo/owl";

import { usePosition } from "@web/core/position_hook";
import { useService } from "@web/core/utils/hooks";

import { shortName } from "./languages";

/**
 * Shown next to the field being dictated in: tells that Nudge is listening,
 * what it is hearing, and how to end.
 *
 * Nothing in it takes the focus: the cursor stays in the field, where the
 * words go.
 */
export class NudgeDictation extends Component {
    static template = "nudge.Dictation";
    static props = {};

    setup() {
        this.voice = useService("nudge_voice");
        this.state = useState(this.voice.state);
        // Above the end of the field: where lines of text are most often empty
        usePosition("pill", () => (this.isDictating ? this.state.target : null), {
            position: "top-end",
            margin: 8,
        });
    }

    get isDictating() {
        return this.state.mode === "dictation" && Boolean(this.state.target?.isConnected);
    }

    get languageName() {
        return shortName(this.state.language);
    }
}

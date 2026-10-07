import { Component } from "@odoo/owl";

export class NudgeFace extends Component {
    static template = "nudge.Face";
    static props = {
        isThinking: { type: Boolean, optional: true },
    };
}

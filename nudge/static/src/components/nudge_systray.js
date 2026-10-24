import { Component, useState } from "@odoo/owl";

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { session } from "@web/session";

import { NudgeFace } from "./nudge_face";

/**
 * Second way in, next to the floating avatar: on a small screen the avatar
 * can sit on top of the button someone wants to press.
 */
export class NudgeSystray extends Component {
    static template = "nudge.Systray";
    static components = { NudgeFace };
    static props = {};

    setup() {
        this.nudge = useService("nudge");
        this.state = useState(this.nudge.state);
    }
}

registry.category("systray").add(
    "nudge.systray",
    {
        Component: NudgeSystray,
        isDisplayed: () => Boolean(session.nudge),
    },
    { sequence: 30 }
);

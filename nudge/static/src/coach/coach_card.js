import { Component, onWillStart, useState } from "@odoo/owl";

import { useService } from "@web/core/utils/hooks";

/** "Your first week": how far a new user is, what to do next, and when they are done */
export class NudgeCoachCard extends Component {
    static template = "nudge.CoachCard";
    static props = {};

    setup() {
        this.coach = useService("nudge_coach");
        this.state = useState(this.coach.state);
        this.tours = useService("nudge_tours");
        this.action = useService("action");
        this.nudge = useService("nudge");
        onWillStart(() => this.coach.load());
    }

    get card() {
        return this.state.card;
    }

    async takeTour(tourId) {
        await this.tours.play(tourId);
    }

    async openScreen(xmlid) {
        this.nudge.close?.();
        await this.action.doAction(xmlid, { clearBreadcrumbs: true });
    }

    async done() {
        await this.coach.finish();
    }
}

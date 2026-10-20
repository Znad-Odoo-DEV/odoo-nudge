import { Component, useState } from "@odoo/owl";

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const ICONS = {
    menu: "fa-bars",
    button: "fa-hand-pointer-o",
    field: "fa-pencil-square-o",
    tab: "fa-folder-open-o",
    control: "fa-sliders",
};

/**
 * The bar shown while a manager records a tour: the steps captured so far,
 * a note for each, and Stop. Then the tour is named and saved.
 */
export class NudgeTourBar extends Component {
    static template = "nudge.TourBar";
    static props = {};

    setup() {
        this.tours = useService("nudge_tours");
        this.state = useState(this.tours.state);
    }

    get canSave() {
        return Boolean(this.state.name.trim()) && !this.state.isSaving;
    }

    icon(kind) {
        return ICONS[kind] || "fa-circle-o";
    }
}

registry.category("main_components").add("NudgeTourBar", { Component: NudgeTourBar });

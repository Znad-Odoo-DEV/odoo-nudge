import { Component, useState } from "@odoo/owl";

import { useService } from "@web/core/utils/hooks";

/** The columns of a file matched to fields, to correct, then tried */
export class NudgeImportCard extends Component {
    static template = "nudge.ImportCard";
    static props = {};

    setup() {
        this.importer = useService("nudge_import");
        this.state = useState(this.importer.state);
    }

    onChange(index, event) {
        this.importer.change(index, event.target.value);
    }
}

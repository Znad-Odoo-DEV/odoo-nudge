import { Component, onWillStart, useState } from "@odoo/owl";

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

/**
 * The tools an agent may use, as a list to tick (nudge.agent.tool_keys is a comma list).
 * Developer tools are not listed. Nothing ticked means the reading tools only.
 */
export class AgentToolsField extends Component {
    static template = "nudge.AgentToolsField";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.state = useState({ tools: [] });
        onWillStart(async () => {
            this.state.tools = await this.orm.call("nudge.agent", "tool_catalogue", []);
        });
    }

    get chosen() {
        return new Set((this.props.record.data[this.props.name] || "").split(",").map((n) => n.trim()).filter(Boolean));
    }

    isChosen(name) {
        return this.chosen.has(name);
    }

    toggle(name) {
        if (this.props.readonly) {
            return;
        }
        const chosen = this.chosen;
        if (!chosen.delete(name)) {
            chosen.add(name);
        }
        this.props.record.update({ [this.props.name]: [...chosen].sort().join(",") });
    }
}

registry.category("fields").add("nudge_agent_tools", {
    component: AgentToolsField,
    supportedTypes: ["char"],
});

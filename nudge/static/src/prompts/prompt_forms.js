/** @odoo-module **/

import { Component, useState } from "@odoo/owl";

import { AutoComplete } from "@web/core/autocomplete/autocomplete";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";

const PLACEHOLDER = /\{\{\s*([A-Za-z_][A-Za-z0-9_]{0,39})\s*\}\}/g;

/**
 * The small form of a chip that has variables (or that comes from a colleague): the text is read here,
 * the words that change are filled in, then it is sent as the person's own message. The text is made by
 * the server, which checks every value and reads the records with the rights of the person.
 */
export class NudgePromptFill extends Component {
    static template = "nudge.PromptFill";
    static components = { AutoComplete };
    static props = {
        chip: Object,
        onSend: Function,
        onCancel: Function,
    };

    setup() {
        this.orm = useService("orm");
        const values = {};
        const shown = {};
        for (const variable of this.props.chip.variables) {
            values[variable.name] = variable.value ?? (variable.type === "boolean" ? false : "");
            shown[variable.name] = variable.display || "";
        }
        this.state = useState({ values, shown, error: "", busy: false });
    }

    /** The text with the words typed so far in place of the placeholders: to read, not what is sent */
    get preview() {
        return (this.props.chip.body || "").replace(PLACEHOLDER, (match, name) => {
            const variable = this.props.chip.variables.find((entry) => entry.name === name);
            if (!variable) {
                return match;
            }
            const shown = variable.type === "many2one" ? this.state.shown[name] : this.state.values[name];
            return shown === "" || shown === false || shown === null || shown === undefined
                ? `{{${variable.label}}}`
                : String(shown);
        });
    }

    sourcesOf(variable) {
        return [
            {
                options: async (request) => {
                    const found = await this.orm.call(variable.model, "name_search", [], {
                        name: request,
                        limit: 8,
                    });
                    return found.map(([id, label]) => ({ label, id }));
                },
            },
        ];
    }

    pick(variable, option) {
        this.state.values[variable.name] = option.id;
        this.state.shown[variable.name] = option.label;
    }

    typed(variable, value) {
        // Typing again unpicks the record: a record is only what was chosen from the list
        this.state.shown[variable.name] = value;
        this.state.values[variable.name] = "";
    }

    async send() {
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        this.state.error = "";
        try {
            const result = await this.orm.call("nudge.prompt", "render", [[this.props.chip.id], this.state.values]);
            this.props.onSend(result);
        } catch (error) {
            // The server says which value does not fit
            this.state.error = error?.data?.message || _t("This prompt could not be filled in.");
        } finally {
            this.state.busy = false;
        }
    }
}

/**
 * "Keep this as a prompt": the message that was sent, with the parts that may change proposed as
 * variables. Nothing of a message stays in a prompt shared with others: every part found becomes a variable.
 */
export class NudgePromptSave extends Component {
    static template = "nudge.PromptSave";
    static props = {
        text: String,
        mode: String,
        canShare: Boolean,
        onSaved: Function,
        onCancel: Function,
    };

    setup() {
        this.orm = useService("orm");
        this.state = useState({
            name: this.props.text.slice(0, 60),
            scope: "private",
            skip: [],
            proposal: null,
            error: "",
            busy: false,
        });
        this.propose();
    }

    get shared() {
        return this.state.scope !== "private";
    }

    async propose() {
        try {
            this.state.proposal = await this.orm.call("nudge.prompt", "propose", [
                this.props.text,
                this.shared,
                this.state.skip,
            ]);
        } catch (error) {
            this.state.error = error?.data?.message || _t("This message cannot be kept.");
        }
    }

    toggle(item) {
        if (this.shared) {
            return;
        }
        const skip = this.state.skip.filter((name) => name !== item.name);
        if (item.used) {
            skip.push(item.name);
        }
        this.state.skip = skip;
        return this.propose();
    }

    setScope(scope) {
        this.state.scope = scope;
        return this.propose();
    }

    async save() {
        const proposal = this.state.proposal;
        if (!proposal || !this.state.name.trim() || this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            await this.orm.create("nudge.prompt", [
                {
                    name: this.state.name.trim(),
                    body: proposal.body,
                    variables: proposal.variables,
                    mode: this.props.mode,
                    scope: this.state.scope,
                },
            ]);
            this.props.onSaved();
        } catch (error) {
            this.state.error = error?.data?.message || _t("The prompt could not be kept.");
        } finally {
            this.state.busy = false;
        }
    }
}

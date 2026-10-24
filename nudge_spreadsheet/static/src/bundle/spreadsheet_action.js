import { Component, onWillStart, onWillUnmount, useState } from "@odoo/owl";

import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { SpreadsheetComponent } from "@spreadsheet/actions/spreadsheet_component";
import { createSpreadsheetModel } from "@spreadsheet/helpers/model";

const AUTOSAVE_MS = 5000;

/**
 * A spreadsheet Nudge made, in the editor of Odoo. What the person changes
 * is kept when they leave; Download gives them the .xlsx.
 */
export class NudgeSpreadsheetAction extends Component {
    static template = "nudge_spreadsheet.Action";
    static components = { SpreadsheetComponent };
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.spreadsheetId = this.props.action.params?.spreadsheet_id;
        this.state = useState({ name: "", ready: false, canWrite: false });
        this.model = null;
        this.saved = "";
        onWillStart(async () => {
            const served = await this.orm.call("nudge.spreadsheet", "get_spreadsheet", [this.spreadsheetId]);
            this.state.name = served.name;
            this.state.canWrite = served.can_write;
            const data = served.data ? JSON.parse(served.data) : {};
            this.model = createSpreadsheetModel({ env: this.env, data });
            this.saved = JSON.stringify(this.model.exportData());
            this.model.on("update", this, () => this.scheduleSave());
            this.state.ready = true;
        });
        onWillUnmount(() => {
            browser.clearTimeout(this.saveTimer);
            this.save();
        });
    }

    scheduleSave() {
        // A closed tab or a crash must not lose the work: it is saved a few seconds after the last change
        browser.clearTimeout(this.saveTimer);
        this.saveTimer = browser.setTimeout(() => this.save(), AUTOSAVE_MS);
    }

    async save() {
        if (!this.model || !this.state.canWrite) {
            return;
        }
        const data = JSON.stringify(this.model.exportData());
        if (data === this.saved) {
            return;
        }
        this.saved = data;
        try {
            await this.orm.call("nudge.spreadsheet", "save_spreadsheet", [[this.spreadsheetId], data]);
        } catch {
            this.notification.add(_t("The changes could not be saved."), { type: "warning" });
        }
    }

    download() {
        this.action.doAction({
            type: "ir.actions.client",
            tag: "action_download_spreadsheet",
            params: { name: this.state.name, data: this.model.exportData() },
        });
    }
}

registry.category("actions").add("nudge_spreadsheet.open", NudgeSpreadsheetAction, { force: true });

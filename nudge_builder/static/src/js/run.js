/** @odoo-module **/
import { Component, onMounted, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const SITE = "nudge.builder.site";
// The pages are made in the background by the scheduled action; the bar asks how far it got
export const POLL = { ms: 2000 };

/**
 * Shows the build that the scheduled action makes one step at a time: the bar is
 * what it reached, and Stop is heard between two steps.
 */
export class NudgeBuilderRun extends Component {
    static template = "nudge_builder.Run";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.siteId = this.props.action.params.site_id;
        this.state = useState({
            progress: 0, text: "", done: false, stopped: false, error: "", pages: [], tooled: false, private: false,
        });
        onMounted(() => this.run());
    }

    async run() {
        try {
            while (!this.state.stopped) {
                const result = await this.orm.call(SITE, "action_build_status", [[this.siteId]]);
                this.state.progress = result.progress;
                this.state.text = result.text;
                if (result.stopped) {
                    this.state.stopped = true;
                }
                if (result.failed) {
                    this.state.error = result.error;
                    break;
                }
                if (result.done) {
                    this.state.done = true;
                    break;
                }
                await new Promise((resolve) => setTimeout(resolve, POLL.ms));
            }
        } catch (error) {
            this.state.error = (error && error.data && error.data.message) || String(error);
        }
        await this.loadCard();
    }

    async loadCard() {
        const [site] = await this.orm.read(SITE, [this.siteId], ["private", "log"]);
        this.state.private = site.private;
        this.state.log = site.log || "";
        this.state.pages = await this.orm.searchRead(
            "nudge.builder.page", [["site_id", "=", this.siteId]], ["name", "url", "key"], { order: "sequence, id" });
    }

    async stop() {
        this.state.stopped = true;
        await this.orm.call(SITE, "action_stop", [[this.siteId]]);
    }

    openEditor(page) {
        return this.action.doAction({ type: "ir.actions.act_url", url: `${page.url}?enable_editor=1`, target: "self" });
    }

    openSite() {
        return this.action.doAction({
            type: "ir.actions.act_window", res_model: SITE, res_id: this.siteId, views: [[false, "form"]],
        });
    }
}

registry.category("actions").add("nudge_builder_run", NudgeBuilderRun);

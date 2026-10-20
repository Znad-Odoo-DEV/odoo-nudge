import { expect, test } from "@odoo/hoot";
import { click, queryAllTexts } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";

import {
    defineModels,
    fields,
    getService,
    models,
    mountView,
    onRpc,
    webModels,
} from "@web/../tests/web_test_helpers";

import { setupNudge } from "./voice_helpers";

class Partner extends models.Model {
    _name = "partner";

    name = fields.Char({ string: "Name" });

    _records = [{ id: 1, name: "Raghad" }];
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Partner, ResCompany, ResPartner, ResUsers]);

test("the health command lists what was found and opens each list", async () => {
    onRpc("nudge.quick", "run_command", () => ({
        command: "health",
        query: "",
        focused: false,
        content: "1 critical, 1 to look at, 0 to know",
        health: {
            report_id: 9,
            summary: "1 critical, 1 to look at, 0 to know",
            findings: [
                {
                    id: 5, severity: "critical", message: "2 email addresses are used by more than one contact.",
                    count: "2", is_new: true, delta: 2, can_show: true,
                },
                {
                    id: 6, severity: "warning", message: "3 users have not logged in for 90 days and are still active.",
                    count: "3", is_new: false, delta: 0, can_show: false,
                },
            ],
        },
    }));
    onRpc("nudge.health.finding", "action_show", ({ args }) => {
        expect.step(`show ${args[0]}`);
        return {
            type: "ir.actions.act_window", name: "Duplicates", res_model: "partner",
            views: [[false, "list"]], domain: [["id", "in", [1]]],
        };
    });
    onRpc("nudge.health.report", "action_open", ({ args }) => {
        expect.step(`report ${args[0]}`);
        return {
            type: "ir.actions.act_window", name: "Report", res_model: "partner",
            res_id: 1, views: [[false, "form"]],
        };
    });
    setupNudge({
        config: {
            is_configured: true,
            is_manager: true,
            commands: [{ name: "health", words: ["/health", "/فحص"], kind: "direct", hint: "Checks", example: "/health" }],
        },
    });
    onRpc("nudge.conversation", "get_status", () => ({ is_manager: true, is_configured: true }));
    await mountView({ resModel: "partner", type: "form", arch: `<form><field name="name"/></form>`, resId: 1 });
    const nudge = getService("nudge");
    nudge.open();
    await animationFrame();
    await nudge.send("/health");
    await animationFrame();
    expect(queryAllTexts(".o_nudge_found_finding .o_nudge_found_body strong")).toEqual(["2", "3"]);
    expect(".o_nudge_finding_critical").toHaveCount(1);
    expect(".o_nudge_found_finding .o_nudge_found_open").toHaveCount(1, { message: "Only a finding with records has Show me" });
    await click(".o_nudge_found_finding .o_nudge_found_open");
    await animationFrame();
    expect.verifySteps(["show 5"]);
});

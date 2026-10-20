import { expect, test } from "@odoo/hoot";
import { click, queryAllTexts } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";

import {
    defineModels,
    fields,
    models,
    mountView,
    onRpc,
    webModels,
} from "@web/../tests/web_test_helpers";

// The change an external assistant asked for waits in a nudge.step with an origin
// until the person says yes in Odoo. The model stands for it; the buttons below are
// the ones nudge_mcp adds to the header of the step form (views/nudge_mcp_views.xml,
// nudge_step_view_form_mcp) and the list is the one of its approvals menu.
class Step extends models.Model {
    _name = "nudge.step";

    summary = fields.Char({ string: "Summary" });
    state = fields.Selection({
        string: "State",
        selection: [
            ["pending", "Pending"],
            ["approved", "Approved"],
            ["refused", "Refused"],
            ["done", "Done"],
        ],
    });
    origin = fields.Char({ string: "Origin" });

    _records = [
        { id: 1, summary: "Change the phone of Raghad", state: "pending", origin: "MCP: Claude Desktop" },
        { id: 2, summary: "Archive Ebrahim Trading", state: "pending", origin: "MCP: ChatGPT" },
        { id: 3, summary: "Asked in the Nudge panel", state: "pending", origin: false },
        { id: 4, summary: "Already said yes", state: "approved", origin: "MCP: Claude Desktop" },
    ];
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Step, ResCompany, ResPartner, ResUsers]);

const FORM = /* xml */ `
    <form>
        <header>
            <button name="action_mcp_approve" type="object" string="Yes, let the assistant do it"
                    class="btn-primary" invisible="state != 'pending' or not origin"/>
            <button name="action_mcp_refuse" type="object" string="No"
                    invisible="state != 'pending' or not origin"/>
            <field name="state" widget="statusbar"/>
        </header>
        <field name="summary"/>
        <field name="origin"/>
    </form>`;

test("a pending change of an assistant asks yes or no", async () => {
    await mountView({ resModel: "nudge.step", type: "form", arch: FORM, resId: 1 });
    expect(queryAllTexts(".o_statusbar_buttons button")).toEqual(["Yes, let the assistant do it", "No"]);
    expect(".o_field_widget[name=summary] input").toHaveValue("Change the phone of Raghad");
});

test("yes calls action_mcp_approve of that change, no calls action_mcp_refuse", async () => {
    onRpc("nudge.step", "action_mcp_approve", ({ args }) => {
        expect.step(`approve ${args[0]}`);
        return false;
    });
    onRpc("nudge.step", "action_mcp_refuse", ({ args }) => {
        expect.step(`refuse ${args[0]}`);
        return false;
    });
    await mountView({ resModel: "nudge.step", type: "form", arch: FORM, resId: 2 });
    await click(".o_statusbar_buttons button.btn-primary");
    await animationFrame();
    await click(".o_statusbar_buttons button:not(.btn-primary)");
    await animationFrame();
    expect.verifySteps(["approve 2", "refuse 2"]);
});

test("a step made in the panel shows no assistant buttons", async () => {
    await mountView({ resModel: "nudge.step", type: "form", arch: FORM, resId: 3 });
    expect(".o_statusbar_buttons button").toHaveCount(0);
});

test("a change already approved shows no assistant buttons", async () => {
    await mountView({ resModel: "nudge.step", type: "form", arch: FORM, resId: 4 });
    expect(".o_statusbar_buttons button").toHaveCount(0);
});

test("the approvals list holds the pending changes of assistants only", async () => {
    await mountView({
        resModel: "nudge.step",
        type: "list",
        arch: `<list><field name="summary"/><field name="origin"/></list>`,
        domain: [
            ["state", "=", "pending"],
            ["origin", "!=", false],
        ],
    });
    expect(queryAllTexts(".o_data_row td[name=summary]")).toEqual([
        "Change the phone of Raghad",
        "Archive Ebrahim Trading",
    ]);
    expect(queryAllTexts(".o_data_row td[name=origin]")).toEqual(["MCP: Claude Desktop", "MCP: ChatGPT"]);
});

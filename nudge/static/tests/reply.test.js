import { expect, test } from "@odoo/hoot";
import { click, select } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";

import {
    defineModels,
    fields,
    getService,
    models,
    mountView,
    onRpc,
    patchWithCleanup,
    webModels,
} from "@web/../tests/web_test_helpers";

import { setupNudge } from "./voice_helpers";

class Partner extends models.Model {
    _name = "partner";

    name = fields.Char({ string: "Name", required: true });

    _records = [
        { id: 1, name: "Raghad Trading" },
        { id: 2, name: "Ebrahim Stores" },
    ];
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Partner, ResCompany, ResPartner, ResUsers]);

const FORM = /* xml */ `<form><sheet><field name="name"/></sheet></form>`;

async function start({ can = true, config = { can_draft_reply: true }, resId = 1 } = {}) {
    onRpc("nudge.reply", "state", ({ args }) => {
        expect.step(`state ${args[0]} ${args[1]}`);
        return { can };
    });
    setupNudge({ config });
    await mountView({ resModel: "partner", type: "form", arch: FORM, resId });
    const nudge = getService("nudge");
    nudge.open();
    await animationFrame();
    return nudge;
}

test("a customer message is on the record: Draft a reply is offered and opens the email window", async () => {
    onRpc("nudge.email.review", "action_draft_reply", ({ args }) => {
        expect.step(`draft ${args.join(" ")}`);
        return { type: "ir.actions.act_window", name: "Reply", res_model: "nudge.email.review", res_id: 9, target: "new" };
    });
    await start();
    expect.verifySteps(["state partner 1"]);
    expect(".o_nudge_reply_draft").toHaveCount(1);
    // The tone is chosen; the default one is friendly
    expect(".o_nudge_reply_tone").toHaveValue("friendly");
    await select("formal", { target: ".o_nudge_reply_tone" });
    patchWithCleanup(getService("action"), {
        async doAction(action) {
            expect.step(`window ${action.res_model} ${action.res_id} ${action.target}`);
        },
    });
    await click(".o_nudge_reply_draft");
    await animationFrame();
    // Only the window is opened: sending is the person's, there
    expect.verifySteps(["draft partner 1 formal", "window nudge.email.review 9 new"]);
});

test("no customer message on the record: no button", async () => {
    await start({ can: false });
    expect.verifySteps(["state partner 1"]);
    expect(".o_nudge_reply_draft").toHaveCount(0);
});

test("without the module of the emails, the server is not even asked", async () => {
    await start({ config: {} });
    expect.verifySteps([]);
    expect(".o_nudge_reply_draft").toHaveCount(0);
});

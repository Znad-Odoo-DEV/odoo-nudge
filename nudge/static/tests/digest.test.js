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

import { setupNudge, streamed } from "./voice_helpers";

class Partner extends models.Model {
    _name = "partner";

    name = fields.Char({ string: "Name" });

    _records = [{ id: 1, name: "Raghad" }];
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Partner, ResCompany, ResPartner, ResUsers]);

const FORM = /* xml */ `<form><field name="name"/></form>`;

async function start(params = {}) {
    const asked = setupNudge({ ...params, config: { can_use_agent: true, ...params.config } });
    await mountView({ resModel: "partner", type: "form", arch: FORM, resId: 1 });
    const nudge = getService("nudge");
    nudge.open();
    await animationFrame();
    return { asked, nudge };
}


const DIGEST = [
    "**Sales**",
    "- 4 quotations are waiting for an answer (last week: 6)",
    "",
    "**Accounting**",
    "- 2 bills are due this week",
    "",
    "**Today**",
    "- Call Raghad about the delivery",
].join("\n");

async function openPreferences(params) {
    const { asked, nudge } = await start(params);
    nudge.showSettings();
    await animationFrame();
    return { asked, nudge };
}

test("the preferences offer the digest with its hour, a setup button and a preview button", async () => {
    await openPreferences();
    expect(".o_nudge_digest").toHaveCount(1);
    expect(".o_nudge_digest_setup").toHaveText("Set up my digest");
    expect(".o_nudge_digest_preview").toHaveText("Preview");
    expect(".o_nudge_digest_hour").toHaveCount(1);
});

test("Set up my digest sends the chosen hour and frees the buttons again", async () => {
    await openPreferences();
    onRpc("nudge.task", "action_setup_digest", ({ kwargs }) => {
        expect.step(`setup ${kwargs.hour}`);
        return { id: 5, hour: kwargs.hour, tz: "Asia/Dubai" };
    });
    const select = document.querySelector(".o_nudge_digest_hour");
    select.value = "6";
    select.dispatchEvent(new Event("change", { bubbles: true }));
    await animationFrame();
    await click(".o_nudge_digest_setup");
    await animationFrame();
    expect.verifySteps(["setup 6"]);
    expect(".o_nudge_digest_setup").not.toHaveAttribute("disabled");
});

test("Preview opens the digest the server made, with a block for each part of the day", async () => {
    await openPreferences();
    onRpc("nudge.task", "action_preview_digest", () => {
        expect.step("preview");
        return 77;
    });
    onRpc("nudge.conversation", "read", () => [{ id: 77, name: "My morning digest" }]);
    onRpc("nudge.message", "search_read", () => [
        { id: 70, role: "assistant", content: DIGEST, state: "complete" },
    ]);
    onRpc("nudge.step", "get_cards", () => ({}));
    await click(".o_nudge_digest_preview");
    await animationFrame();
    expect.verifySteps(["preview"]);
    expect(".o_nudge_title").toHaveText("My morning digest");
    expect(queryAllTexts(".o_nudge_markdown strong")).toEqual(["Sales", "Accounting", "Today"]);
    expect(".o_nudge_markdown li").toHaveCount(3);
});

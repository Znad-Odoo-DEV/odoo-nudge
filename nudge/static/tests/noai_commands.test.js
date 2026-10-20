import { expect, test } from "@odoo/hoot";
import { click, edit, queryAllTexts } from "@odoo/hoot-dom";
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

import { answered, setupNudge, streamed } from "./voice_helpers";

class Partner extends models.Model {
    _name = "partner";

    name = fields.Char({ string: "Name" });

    _records = [{ id: 1, name: "Azure Interior" }];
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Partner, ResCompany, ResPartner, ResUsers]);

const SUGGESTION = { intent: "create", label: "Create: Contact Raghad", text: "create contact Raghad" };

async function start(params = {}) {
    const asked = setupNudge({
        ...params,
        config: { is_configured: false, is_manager: true, can_use_agent: true, can_work_without_ai: true },
    });
    onRpc("nudge.conversation", "get_status", () => ({
        is_manager: true, is_configured: false, can_use_agent: true, can_work_without_ai: true,
    }));
    onRpc("nudge.quick", "find", () => ({ menus: [], settings: [], pages: [], products: [], tours: [] }));
    await mountView({
        resModel: "partner", type: "form", arch: "<form><field name='name'/></form>", resId: 1,
    });
    const nudge = getService("nudge");
    nudge.open();
    await animationFrame();
    return { asked, nudge };
}

async function search(words) {
    await click(".o_nudge_quick_input");
    await edit(words);
    await animationFrame();
    await click(".o_nudge_quick_go");
    await animationFrame();
}

test("with no provider, the request understood without AI is the first suggestion", async () => {
    onRpc("nudge.noai", "suggest", ({ args }) => {
        expect.step(`suggest ${args[0]}`);
        return SUGGESTION;
    });
    await start();
    expect(".o_nudge_noai_do").toHaveCount(0);
    await search("create contact Raghad");
    expect.verifySteps(["suggest create contact Raghad"]);
    expect(".o_nudge_noai_do").toHaveCount(1);
    expect(".o_nudge_noai_do").toHaveText("Create: Contact Raghad");
    expect(".o_nudge_quick_action").toHaveCount(1, { message: "It comes before the results" });
});

test("nothing is offered when the words ask nothing it knows", async () => {
    onRpc("nudge.noai", "suggest", () => false);
    await start();
    await search("the quick brown fox");
    expect(".o_nudge_noai_do").toHaveCount(0);
});

test("doing the suggestion sends the words in Agent mode and shows the card in the chat", async () => {
    onRpc("nudge.noai", "suggest", () => SUGGESTION);
    const { asked, nudge } = await start({
        answer: streamed(
            { type: "start", conversation_id: 4, message_id: 1, title: "Create contact Raghad" },
            { type: "delta", text: "Preparing to create: Contact." },
            {
                type: "confirm", step_id: 8, kind: "create", state: "pending", title: "Create: Contact",
                records: [], lines: [{ field: "name", label: "Name", value: "Raghad" }], sensitive: false, error: "",
            },
            { type: "waiting", message_id: 2, state: "waiting", step_id: 8, message: "" }
        ),
    });
    await search("create contact Raghad");
    await click(".o_nudge_noai_do");
    await animationFrame();
    await animationFrame();
    expect(nudge.state.view).toBe("chat");
    expect(nudge.state.mode).toBe("agent");
    expect(asked.messages.map((question) => question.message)).toEqual(["create contact Raghad"]);
    expect(".o_nudge_composer").toHaveCount(1, { message: "The chat goes on without a provider" });
    expect(".o_nudge_card").toHaveCount(1);
});

test("a choice of records is shown as a list to open", async () => {
    onRpc("nudge.noai", "suggest", () => false);
    const { nudge } = await start({
        answer: answered(
            { type: "start", conversation_id: 5, message_id: 1, title: "Which one" },
            { type: "delta", text: "Which one is “Raghad”?" },
            {
                type: "found",
                records: [
                    { model: "res.partner", id: 11, name: "Raghad Sami", kind: "Contact", state: "" },
                    { model: "res.partner", id: 12, name: "Raghad Salem", kind: "Contact", state: "" },
                ],
            }
        ),
    });
    await nudge.sendWithoutAi("find Raghad");
    await animationFrame();
    await animationFrame();
    expect(queryAllTexts(".o_nudge_found_record strong")).toEqual(["Raghad Sami", "Raghad Salem"]);
});

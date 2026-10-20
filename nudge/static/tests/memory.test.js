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

const FIRST = streamed(
    { type: "start", conversation_id: 9, message_id: 1, title: "Terms" },
    { type: "delta", text: "Payment is due in 30 days." },
    { type: "done", message_id: 2, state: "complete", message: "", memory: true }
);
const SECOND = streamed(
    { type: "delta", text: "From the AI: 30 days." },
    { type: "done", message_id: 4, state: "complete", message: "" }
);

test("a remembered answer shows its badge and the button to ask the AI instead", async () => {
    const { asked, nudge } = await start({ answer: () => FIRST() });
    await nudge.send("What are the payment terms?");
    await animationFrame();

    expect(asked.messages).toHaveLength(1);
    expect(".o_nudge_remembered").toHaveCount(1);
    expect(".o_nudge_remembered").toHaveText(/A remembered answer: no AI was asked\./);
    expect(".o_nudge_remembered_ask").toHaveText("Ask the AI instead");
    // A remembered answer is not rated: the manager already approved it
    expect(".o_nudge_rate").toHaveCount(0);
    expect(".o_nudge_message_assistant .o_nudge_markdown").toHaveText("Payment is due in 30 days.");
    expect("fresh" in asked.messages[0] && asked.messages[0].fresh !== "false").toBe(false);
});

test("ask the AI instead sends the same question again with the fresh flag", async () => {
    let calls = 0;
    const { asked, nudge } = await start({ answer: () => (++calls === 1 ? FIRST() : SECOND()) });
    await nudge.send("What are the payment terms?");
    await animationFrame();
    await click(".o_nudge_remembered_ask");
    await animationFrame();

    expect(asked.messages).toHaveLength(2);
    expect(asked.messages[1].message).toBe("What are the payment terms?");
    expect(asked.messages[1].fresh).toBe("1");
    // The button is gone from the old answer, and the new answer is the AI's
    expect(".o_nudge_remembered_ask").toHaveCount(0);
    expect(queryAllTexts(".o_nudge_message_assistant .o_nudge_markdown")).toEqual([
        "Payment is due in 30 days.",
        "From the AI: 30 days.",
    ]);
});

test("a remembered answer reopened from the history keeps its badge, without the button", async () => {
    const { nudge } = await start();
    onRpc("nudge.conversation", "read", () => [{ id: 9, name: "Terms" }]);
    onRpc("nudge.message", "search_read", () => [
        { id: 1, role: "user", content: "What are the payment terms?", state: "complete" },
        { id: 2, role: "assistant", content: "Payment is due in 30 days.", state: "complete", is_remembered: true },
    ]);
    onRpc("nudge.step", "get_cards", () => ({}));
    await nudge.openConversation(9);
    await animationFrame();
    expect(".o_nudge_remembered").toHaveCount(1);
    expect(".o_nudge_remembered_ask").toHaveCount(0);
});

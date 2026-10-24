import { expect, test } from "@odoo/hoot";
import { click } from "@odoo/hoot-dom";
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
const LABEL = "From Nudge's memory, approved by Raghad on 10/24/2026";

async function start(params = {}) {
    setupNudge({ ...params, config: { can_use_agent: true, ...params.config } });
    await mountView({ resModel: "partner", type: "form", arch: FORM, resId: 1 });
    const nudge = getService("nudge");
    nudge.open();
    await animationFrame();
    return nudge;
}

const FROM_THE_AI = streamed(
    { type: "start", conversation_id: 9, message_id: 1, title: "Terms" },
    { type: "delta", text: "Payment is due in 30 days." },
    { type: "done", message_id: 2, state: "complete", message: "" }
);
const FROM_MEMORY = streamed(
    { type: "start", conversation_id: 9, message_id: 1, title: "Terms" },
    { type: "delta", text: "Payment is due in 30 days." },
    { type: "done", message_id: 2, state: "complete", message: "", memory: { id: 5, label: LABEL } }
);
const CHOICES = streamed(
    { type: "start", conversation_id: 9, message_id: 1, title: "Quotes" },
    { type: "delta", text: "I have approved answers to questions close to yours." },
    { type: "done", message_id: 2, state: "complete", message: "", memory: { id: false, choices: [] } }
);

test("a manager saves an answer of the AI as a reference", async () => {
    onRpc("nudge.memory", "action_save_from_message", ({ args }) => {
        expect.step(`save ${args[0]}`);
        return false;
    });
    const nudge = await start({ answer: () => FROM_THE_AI(), config: { is_manager: true } });
    await nudge.send("What are the payment terms?");
    await animationFrame();

    expect(".o_nudge_reference_save").toHaveCount(1);
    expect(".o_nudge_reference_save").toHaveText("Save as reference");
    await click(".o_nudge_reference_save");
    await animationFrame();
    expect.verifySteps(["save 2"]);
});

test("a person who is not a manager is not offered Save as reference", async () => {
    const nudge = await start({ answer: () => FROM_THE_AI(), config: { is_manager: false } });
    await nudge.send("What are the payment terms?");
    await animationFrame();
    expect(".o_nudge_message_assistant").toHaveCount(1);
    expect(".o_nudge_reference_save").toHaveCount(0);
});

test("an answer from memory says who approved it and when, and is not saved again", async () => {
    onRpc("nudge.memory", "reference_info", ({ args }) => {
        expect.step(`info ${args[0]}`);
        return { label: LABEL, choices: [] };
    });
    const nudge = await start({ answer: () => FROM_MEMORY(), config: { is_manager: true } });
    await nudge.send("What are the payment terms?");
    await animationFrame();
    await animationFrame();

    expect.verifySteps(["info 2"]);
    expect(".o_nudge_reference_label").toHaveText(LABEL);
    expect(".o_nudge_remembered_ask").toHaveText("Ask the AI instead");
    expect(".o_nudge_reference_save").toHaveCount(0);
});

test("close references are offered as choices, and the one picked is the answer", async () => {
    onRpc("nudge.memory", "reference_info", () => ({
        label: "",
        choices: [
            { id: 7, question: "How do I create a quotation?" },
            { id: 8, question: "How do I send a quotation?" },
        ],
    }));
    onRpc("nudge.memory", "answer_choice", ({ args }) => {
        expect.step(`choose ${args[0]} ${args[1]}`);
        return { conversation_id: 9 };
    });
    onRpc("nudge.conversation", "read", () => [{ id: 9, name: "Quotes" }]);
    onRpc("nudge.message", "search_read", () => [
        { id: 1, role: "user", content: "quotation how", state: "complete" },
        { id: 2, role: "assistant", content: "I have approved answers.", state: "complete", is_remembered: true },
        { id: 3, role: "assistant", content: "Open Sales, press New.", state: "complete", is_remembered: true },
    ]);
    onRpc("nudge.step", "get_cards", () => ({}));
    const nudge = await start({ answer: () => CHOICES() });
    await nudge.send("quotation how");
    await animationFrame();
    await animationFrame();

    expect(".o_nudge_reference_choice").toHaveCount(2);
    await click(".o_nudge_reference_choice:first");
    await animationFrame();
    expect.verifySteps(["choose 2 7"]);
    expect(".o_nudge_message_assistant .o_nudge_markdown:last").toHaveText("Open Sales, press New.");
});

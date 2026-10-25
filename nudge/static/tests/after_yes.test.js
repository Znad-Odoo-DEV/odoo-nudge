import { expect, test } from "@odoo/hoot";
import { click, edit, press, waitUntil } from "@odoo/hoot-dom";
import { advanceTime, animationFrame } from "@odoo/hoot-mock";

import { defineModels, fields, getService, models, mountView, onRpc, webModels } from "@web/../tests/web_test_helpers";

import { answered, lines, setupNudge, streamed } from "./voice_helpers";

// S13 (trial): a question sent right after a yes, while the answer that follows it is written

class Partner extends models.Model {
    _name = "partner";

    name = fields.Char({ string: "Name" });
    city = fields.Char({ string: "City" });

    _records = [{ id: 1, name: "Azure Interior", city: "Nablus" }];
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Partner, ResCompany, ResPartner, ResUsers]);

const CARD = {
    step_id: 31,
    kind: "write",
    state: "pending",
    title: "Change: Contact",
    records: [{ id: 1, name: "Azure Interior" }],
    lines: [{ field: "city", label: "City", value: "Jenin", before: "Nablus" }],
    sensitive: false,
    error: "",
};
const WAITING = streamed(
    { type: "start", conversation_id: 9, message_id: 1, title: "Move it" },
    { type: "confirm", ...CARD },
    { type: "waiting", message_id: 2, state: "waiting", step_id: 31, message: "" }
);

/** @returns {Object} what was asked, the service, the yes being answered, and ``finish``, which ends its answer */
async function start({ answer }) {
    let finish;
    const asked = setupNudge({
        config: { can_use_agent: true },
        answer,
        decide: (url, options) =>
            new Promise((resolve, reject) => {
                finish = (...events) => resolve(new Response(lines(...events), { status: 200 }));
                options.signal.addEventListener("abort", () => reject(new DOMException("Stopped", "AbortError")));
            }),
    });
    onRpc("nudge.conversation", "action_stop_answer", () => true);
    await mountView({ resModel: "partner", type: "form", resId: 1, arch: `<form><field name="city"/></form>` });
    const nudge = getService("nudge");
    nudge.open();
    await nudge.send("Move Azure to Jenin");
    await animationFrame();
    const deciding = nudge.decide(31, "confirm");
    await animationFrame();
    // Typed and sent at once, while the answer after the yes is written
    await click(".o_nudge_input");
    await edit("Where is Azure now?");
    await animationFrame();
    return { asked, nudge, deciding, finish: (...events) => finish(...events) };
}

const DONE = [
    { type: "decided", ...CARD, state: "done" },
    { type: "delta", text: "Azure Interior is now in Jenin." },
    { type: "done", message_id: 3, state: "complete", message: "" },
];

test("a question written while the answer to a yes is written comes after it", async () => {
    const responses = [WAITING, answered({ type: "delta", text: "Azure Interior is in Jenin." })];
    const { asked, nudge, deciding, finish } = await start({ answer: () => responses.shift()() });
    expect(".o_nudge_stop").toHaveCount(1);
    expect(".o_nudge_send_next").toHaveCount(1, { message: "Send stays at hand, Stop beside it" });
    await press("Enter");
    await animationFrame();
    expect(asked.messages).toHaveLength(1, { message: "Not sent in the middle of the answer" });
    expect(".o_nudge_input").toHaveValue("");
    expect([nudge.state.messages.at(-1).role, nudge.state.messages.at(-1).state]).toEqual(["user", "next"]);
    expect(".o_nudge_next").toHaveText("Sent when this answer ends");
    expect(".o_nudge_send_next").toHaveCount(0, { message: "One question waits at a time" });
    expect(nudge.state.isStreaming).toBe(true);

    finish(...DONE);
    await deciding;
    await advanceTime(10);
    await waitUntil(() => asked.messages.length === 2 && !nudge.state.isStreaming);
    expect(asked.messages.map((message) => message.message)).toEqual(["Move Azure to Jenin", "Where is Azure now?"]);
    expect(nudge.state.messages.map((message) => [message.role, message.content])).toEqual([
        ["user", "Move Azure to Jenin"],
        ["assistant", ""],
        ["assistant", "Azure Interior is now in Jenin."],
        ["user", "Where is Azure now?"],
        ["assistant", "Azure Interior is in Jenin."],
    ]);
    expect(nudge.state.messages[1].card.state).toBe("done", { message: "The answer to the yes is whole" });
    await animationFrame();
    expect(".o_nudge_message[data-state='next']").toHaveCount(0);
});

test("an answer that ends on a card gives the question back to the box", async () => {
    const { asked, nudge, deciding, finish } = await start({ answer: WAITING });
    await press("Enter");
    await animationFrame();
    finish(
        { type: "decided", ...CARD, state: "done" },
        { type: "confirm", ...CARD, step_id: 32, title: "Change: Contact again" },
        { type: "waiting", message_id: 4, state: "waiting", step_id: 32, message: "" }
    );
    await deciding;
    await advanceTime(10);
    await animationFrame();
    expect(asked.messages).toHaveLength(1, { message: "The card is answered first" });
    expect(".o_nudge_input").toHaveValue("Where is Azure now?");
    expect(".o_nudge_message[data-state='next']").toHaveCount(0);
    expect(nudge.state.messages.at(-1).card.state).toBe("pending", { message: "The new card is not left" });
});

test("stop gives the question back to the box and sends nothing", async () => {
    const { asked, nudge, deciding } = await start({ answer: WAITING });
    await press("Enter");
    await animationFrame();
    await click(".o_nudge_stop");
    await deciding;
    await advanceTime(10);
    await animationFrame();
    expect(asked.messages).toHaveLength(1);
    expect(".o_nudge_input").toHaveValue("Where is Azure now?");
    expect(nudge.state.next).toBe(null);
    expect(".o_nudge_message[data-state='next']").toHaveCount(0);
});

import { expect, test } from "@odoo/hoot";
import { click, waitFor, waitUntil } from "@odoo/hoot-dom";
import { advanceTime, animationFrame } from "@odoo/hoot-mock";

import {
    defineModels,
    fields,
    getService,
    models,
    MockServer,
    mountView,
    onRpc,
    serverState,
    webModels,
} from "@web/../tests/web_test_helpers";
import { busModels } from "@bus/../tests/bus_test_helpers";

import { answered, lines, setupNudge, streamed } from "./voice_helpers";

class Partner extends models.Model {
    _name = "partner";

    name = fields.Char({ string: "Name" });

    _records = [{ id: 1, name: "Azure Interior" }];
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Partner, ResCompany, ResPartner, ResUsers, ...Object.values(busModels)]);

const FORM = /* xml */ `<form><sheet><group><field name="name"/></group></sheet></form>`;

const IN_LINE = streamed({
    type: "in_line",
    slot_id: 7,
    state: "waiting",
    position: 2,
    seconds: 30,
    message: "Nudge is answering other people right now. Your question waits for its turn.",
});

function place(state, position = 1) {
    return { slot_id: 7, state, position, seconds: 15 };
}

/** The server tells the browser that the turn of the place has come */
function turn() {
    MockServer.env["bus.bus"]._sendone(serverState.partnerId, "nudge.slot", { slot_id: 7, state: "ready" });
}

/**
 * @param {Object} params
 * @param {Function} [params.status] where the place stands, each time the browser asks
 */
async function start({ status = () => place("waiting"), answer } = {}) {
    onRpc("nudge.slot", "wait_status", ({ args }) => {
        expect.step(`status ${args[0][0]}`);
        return status();
    });
    onRpc("nudge.slot", "cancel_wait", ({ args }) => {
        expect.step(`cancel ${args[0][0]}`);
        return true;
    });
    // The first time every place is taken; with its place, the question is answered
    const asked = setupNudge({
        answer:
            answer ||
            ((url, options) =>
                options.body.get("slot") ? answered({ type: "delta", text: "Under Sales." })() : IN_LINE()),
    });
    await mountView({ resModel: "partner", type: "form", arch: FORM, resId: 1 });
    const nudge = getService("nudge");
    nudge.open();
    await animationFrame();
    return { asked, nudge };
}

test("a question past the cap waits with its place, then starts when its turn comes", async () => {
    const { asked, nudge } = await start();
    const sent = nudge.send("Where are quotations?");
    await waitFor(".o_nudge_line");
    expect(".o_nudge_line_place").toHaveText("You are number 2 in line (about 30 s).");
    expect(".o_nudge_line_cancel").toHaveCount(1);
    expect(".o_nudge_typing").toHaveCount(0, { message: "Waiting, not thinking" });
    expect(nudge.state.isStreaming).toBe(true);

    // It asks where it stands, without holding the server
    await advanceTime(2100);
    await animationFrame();
    expect.verifySteps(["status 7"]);
    expect(".o_nudge_line_place").toHaveText("You are next in line (about 15 s).");

    turn();
    await sent;
    await animationFrame();
    expect(asked.messages.length).toBe(2);
    expect(asked.messages[1].slot).toBe("7");
    expect(asked.messages[1].message).toBe("Where are quotations?");
    expect(nudge.state.messages.at(-1).content).toBe("Under Sales.");
    expect(".o_nudge_line").toHaveCount(0);
    expect(nudge.state.isStreaming).toBe(false);
});

test("cancel gives the place up", async () => {
    const { asked, nudge } = await start();
    const sent = nudge.send("Where are quotations?");
    await waitFor(".o_nudge_line_cancel");
    await click(".o_nudge_line_cancel");
    await sent;
    await animationFrame();
    expect.verifySteps(["cancel 7"]);
    expect(nudge.state.messages.at(-1).state).toBe("aborted");
    expect(nudge.state.isStreaming).toBe(false);
    expect(asked.messages.length).toBe(1, { message: "Never asked again" });
});

test("a wait too long keeps the question, asked again with one click", async () => {
    let expired = true;
    const { asked, nudge } = await start({ status: () => place(expired ? "expired" : "waiting") });
    const sent = nudge.send("Where are quotations?");
    await waitFor(".o_nudge_line");
    await advanceTime(2100);
    await sent;
    await animationFrame();
    expect.verifySteps(["status 7"]);
    expect(".o_nudge_line_retry").toHaveCount(1);
    expect(".o_nudge_notice").toHaveText(/still busy/);
    expect(nudge.state.isStreaming).toBe(false);

    expired = false;
    const retried = nudge.retryLine();
    await waitFor(".o_nudge_line_cancel");
    expect(asked.messages[1].message).toBe("Where are quotations?", { message: "The text is kept" });
    turn();
    await retried;
    await animationFrame();
    expect(nudge.state.messages.at(-1).content).toBe("Under Sales.");
});

test("an answer given by the model standing in says so", async () => {
    const { nudge } = await start({
        answer: answered(
            { type: "start", conversation_id: 9, message_id: 1, title: "Quotations" },
            { type: "fallback", provider: "Our Ollama", model: "qwen-local", local: true, reason: "error" },
            { type: "delta", text: "Under Sales." }
        ),
    });
    await nudge.send("Where are quotations?");
    await animationFrame();
    expect(".o_nudge_fallback").toHaveText("Answered by the local model (qwen-local)");
});

test("stop tells the server, which lets the provider go at once", async () => {
    onRpc("nudge.conversation", "action_stop_answer", ({ args }) => {
        expect.step(`stop ${args[0][0]}`);
        return true;
    });
    const { nudge } = await start({
        answer: (url, { signal }) => {
            const encoder = new TextEncoder();
            const body = new ReadableStream({
                start(controller) {
                    controller.enqueue(
                        encoder.encode(lines({ type: "start", conversation_id: 9, message_id: 1, title: "Hello" }))
                    );
                    signal.addEventListener("abort", () =>
                        controller.error(new DOMException("Aborted", "AbortError"))
                    );
                },
            });
            return new Response(body, { status: 200 });
        },
    });
    const sent = nudge.send("Hello");
    await waitUntil(() => nudge.state.conversationId === 9);
    nudge.stop();
    await sent;
    await animationFrame();
    expect.verifySteps(["stop 9"]);
    expect(nudge.state.messages.at(-1).state).toBe("aborted");
});

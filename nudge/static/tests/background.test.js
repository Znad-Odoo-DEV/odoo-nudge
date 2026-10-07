import { expect, test } from "@odoo/hoot";
import { click, queryAllTexts, waitFor, waitUntil } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";

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

import { setupNudge, streamed } from "./voice_helpers";

class Partner extends models.Model {
    _name = "partner";

    name = fields.Char({ string: "Name" });

    _records = [{ id: 1, name: "Azure Interior" }];
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Partner, ResCompany, ResPartner, ResUsers, ...Object.values(busModels)]);

const FORM = /* xml */ `<form><sheet><group><field name="name"/></group></sheet></form>`;

const QUEUED = streamed(
    { type: "start", conversation_id: 9, message_id: 1, title: "Create them" },
    { type: "queued", run_id: 5, state: "queued", message: "" }
);

function news(kind, extra = {}) {
    return {
        kind,
        run_id: 5,
        conversation_id: 9,
        state: kind === "progress" ? "running" : kind,
        request: "Create them",
        progress: "",
        text: "",
        card: null,
        ...extra,
    };
}

function tell(payload) {
    MockServer.env["bus.bus"]._sendone(serverState.partnerId, "nudge.run", payload);
}

async function start(params = {}) {
    onRpc("nudge.run", "get_news", () => params.news || []);
    onRpc("nudge.run", "action_seen", ({ args }) => {
        expect.step(`seen ${args[0].join(",")}`);
        return true;
    });
    onRpc("nudge.run", "action_stop", ({ args }) => {
        expect.step(`stop ${args[0].join(",")}`);
        return true;
    });
    onRpc("nudge.conversation", "read", () => [{ id: 9, name: "Create them" }]);
    onRpc("nudge.message", "search_read", () => {
        expect.step("read again");
        return [
            { id: 1, role: "user", content: "Create them", state: "complete" },
            { id: 2, role: "assistant", content: "Both are created.", state: "complete" },
        ];
    });
    onRpc("nudge.step", "get_cards", () => ({}));
    const asked = setupNudge({
        ...params,
        config: { can_use_agent: true, ...params.config },
    });
    await mountView({ resModel: "partner", type: "form", arch: FORM, resId: 1 });
    return { asked, nudge: getService("nudge") };
}

test("the work is left to the background", async () => {
    const { asked, nudge } = await start({ answer: QUEUED });
    await nudge.setMode("agent");
    nudge.open();
    await animationFrame();
    expect(".o_nudge_later").toHaveCount(1);
    expect(".o_nudge_later").not.toBeEnabled();

    nudge.state.draft = "Create the contacts of the list";
    await animationFrame();
    expect(".o_nudge_later").toBeEnabled();
    await click(".o_nudge_later");
    await animationFrame();

    expect(asked.messages[0].background).toBe("1");
    expect(asked.messages[0].mode).toBe("agent");
    const answer = nudge.state.messages[1];
    expect(answer.state).toBe("queued");
    expect(answer.runId).toBe(5);
    expect(nudge.state.isStreaming).toBe(false);
    expect(".o_nudge_background").toHaveCount(1);
    expect(".o_nudge_background_head").toHaveText(/in the background/);
    expect(".o_nudge_background_stop").toHaveCount(1);
    expect(".o_nudge_typing").toHaveCount(0);
    expect(nudge.state.news).toEqual([
        {
            run_id: 5,
            conversation_id: 9,
            state: "queued",
            request: "Create the contacts of the list",
            progress: "",
            is_unseen: false,
        },
    ]);
    expect(".o_nudge_badge").toHaveCount(0, { message: "Nothing to look at yet" });
});

test("in Talk mode the work is not left to anybody", async () => {
    const { asked, nudge } = await start();
    nudge.open();
    await animationFrame();
    expect(".o_nudge_later").toHaveCount(0);
    await nudge.send("Explain", { background: true });
    expect("background" in asked.messages[0]).toBe(false);
});

test("what Nudge is doing is told as it goes", async () => {
    const { nudge } = await start({ answer: QUEUED });
    await nudge.setMode("agent");
    nudge.open();
    await nudge.send("Create them", { background: true });
    await animationFrame();

    tell(news("progress", { progress: "Looked up: Contact" }));
    await waitFor(".o_nudge_background_progress");
    expect(".o_nudge_background_progress").toHaveText("Looked up: Contact");
    expect(nudge.state.news[0].state).toBe("running");
    expect(".o_notification").toHaveCount(0, { message: "What is looked at is not announced" });
    expect.verifySteps([]);

    // Over: what Nudge wrote is read again from the server
    tell(news("done", { text: "Both are created." }));
    await waitUntil(() => nudge.state.messages.at(-1)?.content === "Both are created.");
    await animationFrame();
    expect.verifySteps(["read again", "seen 5"]);
    expect(".o_nudge_background").toHaveCount(0);
    expect(".o_notification").toHaveCount(0, { message: "Looked at already: no toast" });
    expect(".o_nudge_badge").toHaveCount(0);
});

test("elsewhere, the person is told, and taken there", async () => {
    const { nudge } = await start();
    expect(".o_nudge_badge").toHaveCount(0);

    tell(news("waiting", {
        text: "I am about to create Sara.",
        card: { step_id: 31, title: "Create: Contact", state: "pending" },
    }));
    await waitFor(".o_notification");
    expect(".o_notification_content").toHaveText(/Nudge needs your answer/);
    expect(".o_notification_content").toHaveText(/I am about to create Sara\./);
    expect(".o_notification_buttons button").toHaveText("Show me");
    expect(".o_nudge_badge").toHaveText("1");
    expect(nudge.state.isOpen).toBe(false);

    await click(".o_notification_buttons button");
    await waitUntil(() => nudge.state.messages.length === 2);
    expect(nudge.state.isOpen).toBe(true);
    expect(nudge.state.conversationId).toBe(9);
    expect.verifySteps(["read again", "seen 5"]);
    expect(".o_nudge_badge").toHaveCount(0);
    expect(nudge.state.news[0].is_unseen).toBe(false);
});

test("what ended while the person was away is a badge", async () => {
    const { nudge } = await start({
        news: [
            { run_id: 5, conversation_id: 9, state: "done", request: "Create them", progress: "", is_unseen: true },
            { run_id: 6, conversation_id: 10, state: "running", request: "Look", progress: "Looked up: Contact", is_unseen: false },
        ],
    });
    await animationFrame();
    expect(".o_nudge_badge").toHaveText("1");

    tell(news("failed", { run_id: 6, conversation_id: 10, request: "Look" }));
    await waitFor(".o_notification");
    expect(".o_notification_content").toHaveText(/Nudge could not finish/);
    expect(".o_notification_content").toHaveText(/Look/);
    expect(".o_nudge_badge").toHaveText("2");

    nudge.open();
    nudge.showHistory();
    onRpc("nudge.conversation", "search_read", () => [
        { id: 9, name: "Create them", write_date: "2026-09-30 05:00:00", message_count: 2 },
        { id: 10, name: "Look", write_date: "2026-09-30 05:00:00", message_count: 2 },
        { id: 11, name: "Talk", write_date: "2026-09-30 05:00:00", message_count: 2 },
    ]);
    await nudge.showHistory();
    await animationFrame();
    expect(queryAllTexts(".o_nudge_history_news")).toEqual(["Done", "Failed"]);
    expect(".o_nudge_history_news_unseen").toHaveCount(2);
});

test("what is worked on is shown when the conversation is opened, and can be stopped", async () => {
    const { nudge } = await start({
        news: [
            { run_id: 5, conversation_id: 9, state: "running", request: "Create them", progress: "Looked up: Contact", is_unseen: false },
        ],
    });
    nudge.open();
    await nudge.openConversation(9);
    await animationFrame();
    expect.verifySteps(["read again"]);
    expect(nudge.state.messages.at(-1).state).toBe("queued");
    expect(".o_nudge_background_progress").toHaveText("Looked up: Contact");
    expect(".o_nudge_working").toHaveCount(0, { message: "Told in the conversation, not twice" });

    onRpc("nudge.run", "get_news", () => [
        { run_id: 5, conversation_id: 9, state: "stopped", request: "Create them", progress: "", is_unseen: false },
    ]);
    await click(".o_nudge_background_stop");
    await waitUntil(() => nudge.state.news[0].state === "stopped");
    await animationFrame();
    expect.verifySteps(["stop 5", "read again"]);
    expect(".o_nudge_background").toHaveCount(0);
});

test("the empty panel says what is going on", async () => {
    const { nudge } = await start({
        news: [
            { run_id: 5, conversation_id: 9, state: "queued", request: "A", progress: "", is_unseen: false },
            { run_id: 6, conversation_id: 10, state: "waiting", request: "B", progress: "", is_unseen: true },
        ],
    });
    await nudge.setMode("agent");
    nudge.open();
    await animationFrame();
    expect(".o_nudge_working").toHaveText(/2 requests are worked on in the background/);
    await nudge.setMode("talk");
    await animationFrame();
    expect(".o_nudge_working").toHaveCount(0);
});

test("those who may not act hear nothing", async () => {
    onRpc("nudge.run", "get_news", () => {
        expect.step("news asked");
        return [];
    });
    const { nudge } = await start({ config: { can_use_agent: false } });
    expect.verifySteps([]);
    tell(news("done", { text: "Done." }));
    await animationFrame();
    await animationFrame();
    expect(".o_notification").toHaveCount(0);
    expect(nudge.state.news).toEqual([]);
});

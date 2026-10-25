import { expect, test } from "@odoo/hoot";
import { animationFrame, Deferred } from "@odoo/hoot-mock";

import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { session } from "@web/session";
import { getService, makeMockEnv, onRpc, patchWithCleanup } from "@web/../tests/web_test_helpers";

function lines(...events) {
    return events.map((event) => JSON.stringify(event) + "\n").join("");
}

/**
 * @param {string[]} chunks
 * @param {Deferred} [gate] the body ends once it is resolved
 */
function streamed(chunks, gate) {
    const encoder = new TextEncoder();
    const body = new ReadableStream({
        async start(controller) {
            for (const chunk of chunks) {
                controller.enqueue(encoder.encode(chunk));
            }
            await gate;
            controller.close();
        },
    });
    return new Response(body, { status: 200 });
}

async function startNudge(config, fetch, settings = { nudge_explain: "text" }) {
    patchWithCleanup(session, { nudge: config });
    patchWithCleanup(user, { settings: { ...user.settings, ...settings } });
    const requests = [];
    patchWithCleanup(browser, {
        fetch(url, options) {
            if (url !== "/nudge/chat/stream") {
                // The web client fetches things of its own, translations for instance
                return super.fetch(...arguments);
            }
            requests.push({ url, body: Object.fromEntries(options.body.entries()), options });
            return fetch(url, options);
        },
    });
    await makeMockEnv();
    return { nudge: getService("nudge"), requests };
}

test("the conversation is open again after the reload what Nudge made asked for", async () => {
    // "Undo everything you made in this conversation" went to a new, empty one
    browser.sessionStorage.setItem(
        "nudge.kept_conversation",
        JSON.stringify({ conversationId: 3, isOpen: true, at: Date.now() })
    );
    onRpc("nudge.conversation", "read", () => [{ id: 3, name: "A field" }]);
    onRpc("nudge.message", "search_read", () => [
        { id: 1, role: "user", content: "Add a field", state: "complete", screen_events: [] },
    ]);
    const { nudge } = await startNudge({ is_configured: true }, () => {});
    await animationFrame();
    expect(nudge.state.conversationId).toBe(3);
    expect(nudge.state.isOpen).toBe(true);
    expect(browser.sessionStorage.getItem("nudge.kept_conversation")).toBe(null, {
        message: "Taken once: the next page starts as usual",
    });
});

test("a conversation kept long ago is not opened again", async () => {
    browser.sessionStorage.setItem(
        "nudge.kept_conversation",
        JSON.stringify({ conversationId: 3, isOpen: true, at: Date.now() - 3600000 })
    );
    const { nudge } = await startNudge({ is_configured: true }, () => {});
    await animationFrame();
    expect(nudge.state.conversationId).toBe(null);
});

test("nothing is shown to someone outside the groups", async () => {
    const { nudge } = await startNudge(undefined, () => {});
    expect(nudge.state.isEnabled).toBe(false);
    expect(registry.category("main_components").contains("nudge.Root")).toBe(false);
});

test("the avatar is shown to a user of Nudge", async () => {
    const { nudge } = await startNudge({ is_manager: true, is_configured: true }, () => {});
    expect(nudge.state.isEnabled).toBe(true);
    expect(nudge.state.isManager).toBe(true);
    expect(nudge.state.isConfigured).toBe(true);
    expect(registry.category("main_components").contains("nudge.Root")).toBe(true);
    nudge.toggle();
    expect(nudge.state.isOpen).toBe(true);
    nudge.toggle();
    expect(nudge.state.isOpen).toBe(false);
});

test("a provider set up after the page was loaded is noticed", async () => {
    let isConfigured = false;
    onRpc("nudge.conversation", "get_status", () => {
        expect.step("get_status");
        return { is_manager: true, is_configured: isConfigured };
    });
    const { nudge } = await startNudge({ is_manager: true, is_configured: false }, () => {});

    nudge.open();
    await animationFrame();
    expect(nudge.state.isConfigured).toBe(false);
    nudge.close();

    isConfigured = true;
    nudge.toggle();
    await animationFrame();
    expect(nudge.state.isConfigured).toBe(true);
    nudge.close();

    // Nothing more is asked once Nudge can answer
    nudge.open();
    await animationFrame();
    expect.verifySteps(["get_status", "get_status"]);
});

test("an answer is built from the stream", async () => {
    const gate = new Deferred();
    const { nudge, requests } = await startNudge({ is_configured: true }, () =>
        streamed(
            [
                lines({ type: "start", conversation_id: 7, message_id: 1, title: "Hello" }),
                lines({ type: "delta", text: "Hi " }, { type: "delta", text: "there" }),
            ],
            gate.then(() => {})
        )
    );
    const sent = nudge.send("  Hello  ");
    expect(nudge.state.isStreaming).toBe(true);
    expect(nudge.state.messages.map((message) => [message.role, message.state])).toEqual([
        ["user", "complete"],
        ["assistant", "streaming"],
    ]);
    // A second message waits for the end of the answer
    await nudge.send("Again");
    expect(requests).toHaveLength(1);

    gate.resolve();
    await sent;
    expect(requests[0].url).toBe("/nudge/chat/stream");
    expect(requests[0].options.method).toBe("POST");
    expect(requests[0].body.message).toBe("Hello");
    expect("conversation_id" in requests[0].body).toBe(false);
    expect(nudge.state.conversationId).toBe(7);
    expect(nudge.state.title).toBe("Hello");
    // The stream ended without saying how: the answer is not presented as complete
    expect(nudge.state.messages[1].content).toBe("Hi there");
    expect(nudge.state.messages[1].state).toBe("error");
    expect(nudge.state.isStreaming).toBe(false);
});

test("the conversation goes on", async () => {
    let turn = 0;
    const { nudge, requests } = await startNudge({ is_configured: true }, () => {
        turn++;
        return streamed([
            lines(
                { type: "start", conversation_id: 7, title: "First" },
                { type: "delta", text: `Answer ${turn}` },
                { type: "done", message_id: 10 * turn, state: "complete", message: "" }
            ),
        ]);
    });
    await nudge.send("First");
    await nudge.send("Second");

    expect(requests[1].body.conversation_id).toBe("7");
    expect(nudge.state.messages.map((message) => message.content)).toEqual([
        "First",
        "Answer 1",
        "Second",
        "Answer 2",
    ]);
    expect(nudge.state.messages[3]).toEqual({
        id: 20,
        role: "assistant",
        content: "Answer 2",
        state: "complete",
        error: "",
        guidance: [],
        choice: "",
        explain: "text",
        card: null,
        runId: null,
        progress: "",
    });

    nudge.newConversation();
    expect(nudge.state.messages).toEqual([]);
    expect(nudge.state.conversationId).toBe(null);
    await nudge.send("Third");
    expect("conversation_id" in requests[2].body).toBe(false);
});

test("errors are told, not thrown", async () => {
    const responses = [
        () => streamed([lines({ type: "error", state: "rejected", message: "Not set up" })]),
        () => streamed([lines({ type: "start", conversation_id: 1 }, { type: "delta", text: "Par" },
            { type: "error", message_id: 5, message: "Provider down", detail: "HTTP 503" })]),
        () => new Response("Forbidden", { status: 403 }),
        () => Promise.reject(new TypeError("Failed to fetch")),
    ];
    const { nudge } = await startNudge({ is_configured: true }, () => responses.shift()());

    await nudge.send("One");
    expect(nudge.state.messages.at(-1).state).toBe("error");
    expect(nudge.state.messages.at(-1).error).toBe("Not set up");

    await nudge.send("Two");
    expect(nudge.state.messages.at(-1).content).toBe("Par");
    expect(nudge.state.messages.at(-1).error).toBe("Provider down\nHTTP 503");

    await nudge.send("Three");
    expect(nudge.state.messages.at(-1).state).toBe("error");
    await nudge.send("Four");
    expect(nudge.state.messages.at(-1).state).toBe("error");
    expect(nudge.state.messages.at(-1).error).not.toBe("");
    expect(nudge.state.isStreaming).toBe(false);
});

test("an answer can be stopped", async () => {
    const { nudge } = await startNudge({ is_configured: true }, (url, { signal }) => {
        const encoder = new TextEncoder();
        const body = new ReadableStream({
            start(controller) {
                controller.enqueue(encoder.encode(lines({ type: "delta", text: "Partial" })));
                signal.addEventListener("abort", () =>
                    controller.error(new DOMException("Aborted", "AbortError"))
                );
            },
        });
        return new Response(body, { status: 200 });
    });
    const sent = nudge.send("Hello");
    await new Promise((resolve) => setTimeout(resolve));
    nudge.stop();
    await sent;

    expect(nudge.state.messages[1].content).toBe("Partial");
    expect(nudge.state.messages[1].state).toBe("aborted");
    expect(nudge.state.isStreaming).toBe(false);
});

test("the screen goes with the message, unless the eye is closed", async () => {
    onRpc("res.users.settings", "set_res_users_settings", () => {
        expect.step("saved");
        return {};
    });
    const { nudge, requests } = await startNudge(
        { is_configured: true, can_share_screen: true },
        () => streamed([lines({ type: "done", message_id: 1, state: "complete", message: "" })])
    );
    expect(nudge.state.canShareScreen).toBe(true);
    expect(nudge.state.shareScreen).toBe(true);

    await nudge.send("With my screen");
    expect(JSON.parse(requests[0].body.snapshot)).toEqual({ v: 1 });

    await nudge.setShareScreen(false);
    expect.verifySteps(["saved"]);
    await nudge.send("Without");
    expect("snapshot" in requests[1].body).toBe(false);
    expect(nudge.state.screenSummary).toBe("");
});

test("the screen is never sent when the administrator does not allow it", async () => {
    const { nudge, requests } = await startNudge(
        { is_configured: true, can_share_screen: false },
        () => streamed([lines({ type: "done", message_id: 1, state: "complete", message: "" })])
    );
    expect(nudge.state.shareScreen).toBe(true);
    await nudge.send("Hello");
    expect("snapshot" in requests[0].body).toBe(false);
});

test("an answer can point at the screen and take to another one", async () => {
    const steps = [{ target: { type: "field", name: "email" }, text: "Here.", advance: "auto" }];
    const responses = [
        () => streamed([lines(
            { type: "start", conversation_id: 3, title: "Where?" },
            { type: "guidance", steps },
            { type: "done", message_id: 8, state: "complete", message: "" }
        )]),
        () => streamed([lines(
            { type: "delta", text: "On our way." },
            { type: "navigate", menu_id: 42, path: "Sales/Orders", text: "This is where orders are." },
            { type: "done", message_id: 9, state: "complete", message: "" }
        )]),
    ];
    const { nudge, requests } = await startNudge({ is_configured: true }, () => responses.shift()());
    patchWithCleanup(getService("menu"), {
        selectMenu(menuId) {
            expect.step(`menu ${menuId}`);
        },
    });
    const guidance = getService("nudge_guidance");

    nudge.open();
    await nudge.send("Where do I type the email?");
    const answer = nudge.state.messages[1];
    expect(answer.guidance).toEqual(steps);
    expect(answer.state).toBe("complete");
    // Asked in writing: the steps are listed, nothing is shown yet
    expect(requests[0].body.explain).toBe("text");
    expect(answer.choice).toBe("text");
    expect(guidance.state.isActive).toBe(false);
    expect(nudge.state.isOpen).toBe(true);
    nudge.chooseGuidance(answer.id, "screen");
    expect(answer.choice).toBe("screen");
    expect(guidance.state.isActive).toBe(true);
    expect(guidance.state.steps).toEqual(steps);
    expect(nudge.state.isOpen).toBe(false);

    guidance.stop();
    await nudge.send("Take me to the orders");
    expect(nudge.state.messages[3].content).toBe("On our way.\n\nThis is where orders are.");
    expect.verifySteps(["menu 42"]);

    nudge.showGuidance(steps);
    expect(guidance.state.isActive).toBe(true);
    guidance.stop();
    nudge.showGuidance([]);
    expect(guidance.state.isActive).toBe(false);
});

test("a conversation read again keeps what it showed", async () => {
    onRpc("nudge.conversation", "read", () => [{ id: 3, name: "Where?" }]);
    onRpc("nudge.message", "search_read", ({ kwargs }) => {
        expect.step(JSON.stringify(kwargs.domain[1]));
        return [
            { id: 1, role: "user", content: "Where?", state: "complete", screen_events: false },
            {
                id: 2, role: "assistant", content: "", state: "complete", error_message: false,
                screen_events: [{ type: "guidance", steps: [{ target: {}, text: "Here." }] }],
            },
            // Made of a call to a tool only: nothing to read
            { id: 3, role: "assistant", content: "", state: "complete", screen_events: false },
            {
                id: 4, role: "assistant", content: "Going.", state: "complete",
                screen_events: [{ type: "navigate", menu_id: 4, text: "To the orders." }],
            },
        ];
    });
    const { nudge } = await startNudge({ is_configured: true }, () => {});
    await nudge.openConversation(3);

    expect.verifySteps(['["role","in",["user","assistant"]]']);
    expect(nudge.state.title).toBe("Where?");
    expect(nudge.state.messages.map((message) => message.id)).toEqual([1, 2, 4]);
    expect(nudge.state.messages[1].guidance).toEqual([{ target: {}, text: "Here." }]);
    expect(nudge.state.messages[2].content).toBe("Going.\n\nTo the orders.");
});

test("a slip of a name asks the server before the choice of how, and any error shows the choice", async () => {
    const answer = () => streamed([lines(
        { type: "start", conversation_id: 5 },
        { type: "done", message_id: 8, state: "complete", message: "Did you mean" }
    )]);
    let fail = false;
    onRpc("nudge.noai", "slip_offered", ({ args }) => {
        expect.step(args[0]);
        if (fail) {
            throw new Error("down");
        }
        return args[0] === "sales ordrs";
    });
    const { nudge, requests } = await startNudge(
        { is_configured: true, can_share_screen: true }, answer, { nudge_explain: "ask" });
    nudge.open();

    // A slip: no choice, the server answers
    await nudge.send("sales ordrs");
    expect(nudge.state.asking).toBe(null);
    expect(requests).toHaveLength(1);
    // A short text the server does not take for a slip: the choice
    await nudge.send("Thanks!");
    expect(nudge.state.asking).not.toBe(null);
    // A question of how never asks the server
    await nudge.send("Where do I type the email?");
    expect(nudge.state.asking).not.toBe(null);
    // An error: the choice, as before
    fail = true;
    await nudge.send("sales ordrs");
    expect(nudge.state.asking).not.toBe(null);
    expect(requests).toHaveLength(1);
    expect.verifySteps(["sales ordrs", "Thanks!", "sales ordrs"]);
});

test("a question asked with the screen asks how first", async () => {
    const steps = [{ target: { type: "field", name: "email" }, text: "Here.", advance: "auto" }];
    const answer = () => streamed([lines(
        { type: "start", conversation_id: 5 },
        { type: "guidance", steps },
        { type: "done", message_id: 8, state: "complete", message: "" }
    )]);
    const { nudge, requests } = await startNudge(
        { is_configured: true, can_share_screen: true }, answer, { nudge_explain: "ask" });
    const guidance = getService("nudge_guidance");
    nudge.open();

    await nudge.send("Where do I type the email?");
    expect(requests).toHaveLength(0);
    expect(nudge.state.asking).not.toBe(null);
    expect(nudge.state.messages).toHaveLength(1);
    expect(nudge.state.messages[0].state).toBe("choosing");

    // Taken back: the question returns to the message box
    nudge.cancelExplain();
    expect(nudge.state.asking).toBe(null);
    expect(nudge.state.messages).toHaveLength(0);
    expect(nudge.state.draft).toBe("Where do I type the email?");

    // On the screen: the pointer sets off as soon as the steps come
    nudge.state.draft = "";
    await nudge.send("Where do I type the email?");
    await nudge.chooseExplain("screen");
    expect(requests[0].body.explain).toBe("screen");
    expect(nudge.state.messages[0].state).toBe("complete");
    expect(nudge.state.messages[1].choice).toBe("screen");
    expect(guidance.state.isActive).toBe(true);
    expect(nudge.state.isOpen).toBe(false);
    guidance.stop();

    // In writing: the steps are listed
    nudge.open();
    await nudge.send("And the phone?");
    await nudge.chooseExplain("text");
    expect(requests[1].body.explain).toBe("text");
    expect(nudge.state.messages[3].choice).toBe("text");
    expect(guidance.state.isActive).toBe(false);

    // Without the screen there is nothing to point at: nothing is asked
    nudge.setShareScreen(false);
    await nudge.send("Again?");
    expect(nudge.state.asking).toBe(null);
    expect(requests[2].body.explain).toBe("text");

    // A preference: nothing is asked either
    nudge.setShareScreen(true);
    nudge.setExplain("screen");
    await nudge.send("Once more");
    expect(nudge.state.asking).toBe(null);
    expect(requests[3].body.explain).toBe("screen");
    guidance.stop();

    // A new question replaces the one that waited
    nudge.setExplain("ask");
    await nudge.send("First");
    await nudge.send("Second");
    expect(nudge.state.messages.filter((message) => message.state === "choosing")).toHaveLength(1);
    expect(nudge.state.asking.text).toBe("Second");
    expect(requests).toHaveLength(4);
});

test("in Agent mode the question asks for the work as well", async () => {
    const answer = () => streamed([lines(
        { type: "start", conversation_id: 6 },
        { type: "delta", text: "Done." },
        { type: "done", message_id: 9, state: "complete", message: "" }
    )]);
    const { nudge, requests } = await startNudge(
        { is_configured: true, can_share_screen: true, can_use_agent: true }, answer,
        { nudge_explain: "ask", nudge_mode: "agent" });
    nudge.open();
    expect(nudge.state.mode).toBe("agent");

    // An order is the work: it is not asked how
    await nudge.send("Make them a Director");
    expect(nudge.state.asking).toBe(null);
    expect(requests[0].body.mode).toBe("agent");
    expect("explain" in requests[0].body).toBe(false);

    await nudge.send("How do I do it myself?");
    await nudge.chooseExplain("screen");
    expect(requests[1].body.explain).toBe("screen");

    // A preference other than asking leaves the agent its orders
    nudge.setExplain("text");
    await nudge.send("Archive them");
    expect(nudge.state.asking).toBe(null);
    expect("explain" in requests[2].body).toBe(false);

    // Without the screen, an order goes straight too
    nudge.setExplain("ask");
    nudge.setShareScreen(false);
    await nudge.send("And now a President");
    expect(nudge.state.asking).toBe(null);
    expect("explain" in requests[3].body).toBe(false);
});

test("a file goes with the question", async () => {
    const { nudge, requests } = await startNudge({ is_configured: true }, () =>
        streamed([lines({ type: "start", conversation_id: 7, message_id: 1, title: "A file" })])
    );
    const file = new File(["%PDF-1.4 hello"], "invoice.pdf", { type: "application/pdf" });
    expect(nudge.setAttachment(file)).toBe("");
    expect(nudge.state.attachment.name).toBe("invoice.pdf");
    await nudge.send("");
    expect(requests).toHaveLength(1, { message: "An empty text still sends, with the file" });
    expect(requests[0].body.message).toBe("invoice.pdf");
    expect(requests[0].body.file).toBeInstanceOf(File);
    expect(requests[0].body.file.name).toBe("invoice.pdf");
    expect(nudge.state.attachment).toBe(null, { message: "Sent: nothing waits for the next question" });
    expect(nudge.state.messages[0].attachment).toBe("invoice.pdf");

    const big = new File([new Uint8Array(11 * 1048576)], "big.pdf", { type: "application/pdf" });
    expect(nudge.setAttachment(big)).toMatch(/too large/);
    expect(nudge.state.attachment).toBe(null);
    const odd = new File(["x"], "setup.exe", { type: "application/octet-stream" });
    expect(nudge.setAttachment(odd)).toMatch(/Only PDF/);
    expect(nudge.setAttachment(new File(["a,b"], "list.csv", { type: "text/csv" }))).toBe("");
    nudge.setAttachment(null);
    expect(nudge.state.attachment).toBe(null);
});

test("a comparison asks: chat or spreadsheet", async () => {
    const { nudge, requests } = await startNudge(
        { is_configured: true, can_use_agent: true, can_make_spreadsheet: true },
        () => streamed([lines({ type: "start", conversation_id: 7, message_id: 1, title: "Compare" })])
    );
    await nudge.send("قارن رواتب هالشهر مع الشهر الماضي");
    expect(requests).toHaveLength(0, { message: "Nothing is sent before they choose" });
    expect(nudge.state.asking.kind).toBe("report");
    expect(nudge.state.messages.at(-1).state).toBe("choosing");

    await nudge.chooseReport("sheet");
    expect(requests).toHaveLength(1);
    expect(requests[0].body.report).toBe("sheet");
    expect(requests[0].body.mode).toBe("agent", { message: "The agent reads the figures and makes it" });
    expect(requests[0].body.explain).toBe(undefined);
    expect(nudge.state.asking).toBe(null);

    await nudge.send("Compare this month with last month");
    await nudge.chooseReport("chat");
    expect(requests).toHaveLength(2);
    expect(requests[1].body.report).toBe("chat");

    await nudge.send("How many contacts do we have?");
    expect(requests).toHaveLength(3, { message: "A plain question is not asked about" });
    expect(requests[2].body.report).toBe(undefined);
});

test("in Developer mode a report is something to build: nothing is asked", async () => {
    // "اعمل تقرير PDF لعرض السعر" stopped at the question, and never reached the model
    const { nudge, requests } = await startNudge(
        { is_configured: true, can_use_agent: true, can_develop: true, can_make_spreadsheet: true },
        () => streamed([lines({ type: "start", conversation_id: 7, message_id: 1, title: "Report" })])
    );
    await nudge.setMode("dev");
    await nudge.send("Make a PDF report of the quotation with the project site");
    expect(nudge.state.asking).toBe(null);
    expect(requests).toHaveLength(1);
    expect(requests[0].body.mode).toBe("dev");
});

test("without the spreadsheet module nothing is asked", async () => {
    const { nudge, requests } = await startNudge(
        { is_configured: true, can_use_agent: true },
        () => streamed([lines({ type: "start", conversation_id: 7, message_id: 1, title: "Compare" })])
    );
    await nudge.send("Compare the warehouses");
    expect(requests).toHaveLength(1);
    expect(nudge.state.asking).toBe(null);
});

test("an action is opened when a tool says so", async () => {
    const { nudge } = await startNudge({ is_configured: true, can_use_agent: true }, () =>
        streamed([
            lines(
                { type: "start", conversation_id: 7, message_id: 1, title: "Sheet" },
                { type: "open_action", action: { type: "ir.actions.client", tag: "x" }, text: "Here it is." },
                { type: "done", message_id: 2, state: "complete", message: "" }
            ),
        ])
    );
    patchWithCleanup(getService("action"), {
        async doAction(action) {
            expect.step(action.tag);
        },
    });
    await nudge.send("Make it");
    expect.verifySteps(["x"]);
    expect(nudge.state.messages[1].content).toBe("Here it is.");
});

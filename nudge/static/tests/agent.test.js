import { expect, test } from "@odoo/hoot";
import { click, edit, queryAllTexts, queryOne } from "@odoo/hoot-dom";
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

import { asksForFigures, asksForReport, asksHowTo, asksToDoIt } from "@nudge/core/nudge_service";

import { answered, lines, setupNudge, streamed } from "./voice_helpers";

class Partner extends models.Model {
    _name = "partner";

    name = fields.Char({ string: "Name" });
    city = fields.Char({ string: "City" });

    _records = [
        { id: 1, name: "Azure Interior", city: "Nablus" },
        { id: 2, name: "Deco Addict", city: "Hebron" },
    ];
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Partner, ResCompany, ResPartner, ResUsers]);

const FORM = /* xml */ `<form><sheet><group><field name="name"/><field name="city"/></group></sheet></form>`;
const LIST = /* xml */ `<list><field name="name"/><field name="city"/></list>`;

const CARD = {
    step_id: 31,
    kind: "write",
    state: "pending",
    title: "Change: Contact",
    records: [{ id: 1, name: "Azure Interior" }],
    lines: [
        { field: "city", label: "City", value: "Jenin", before: "Nablus" },
        { field: "is_company", label: "Is a Company", value: true, before: false },
        { field: "phone", label: "Phone", value: null, before: "0599" },
    ],
    sensitive: false,
    error: "",
};
const WAITING = streamed(
    { type: "start", conversation_id: 9, message_id: 1, title: "Move it" },
    { type: "delta", text: "I am about to change the city." },
    { type: "confirm", ...CARD },
    { type: "waiting", message_id: 2, state: "waiting", step_id: 31, message: "" }
);

async function start(params = {}, view = { type: "form", arch: FORM, resId: 1 }) {
    const asked = setupNudge({
        ...params,
        config: { can_use_agent: true, ...params.config },
    });
    await mountView({ resModel: "partner", ...view });
    return { asked, nudge: getService("nudge") };
}

test("do it is understood, said or written", () => {
    for (const text of [
        "do it",
        "Do it.",
        "Nudge, do it",
        "nudge do it for me",
        "  Go ahead",
        "do that please",
        "نفذ",
        "نفّذها",
        "Nudge، اعملها",
        "سويها",
        "اعمله هلأ",
        "اعمل ال Automation انت",
        "طبقها عني",
        "make the rule yourself, do it for me",
    ]) {
        expect(asksToDoIt(text)).toBe(true, { message: text });
    }
    for (const text of [
        "How do I do it?",
        "What does it do",
        "doit",
        "do items need a price?",
        "Should I go ahead?",
        "كيف أنفذ الطلب؟",
        "",
        null,
    ]) {
        expect(asksToDoIt(text)).toBe(false, { message: String(text) });
    }
});

test("the mode is for those who may", async () => {
    const { asked, nudge } = await start({ config: { can_use_agent: false } });
    nudge.open();
    await animationFrame();

    expect(".o_nudge_modes").toHaveCount(0);
    expect(".o_nudge_eye").toHaveCount(1);
    await nudge.setMode("agent");
    expect(nudge.state.mode).toBe("talk");
    await nudge.send("Nudge, do it");
    expect("mode" in asked.messages[0]).toBe(false);
    expect(nudge.state.mode).toBe("talk");
});

test("a lock shows in the panel when the company asks for private mode", async () => {
    const { nudge } = await start({ config: { is_private: true } });
    nudge.open();
    await animationFrame();
    expect(".o_nudge_private").toHaveCount(1);
});

test("no lock when private mode is off", async () => {
    const { nudge } = await start();
    nudge.open();
    await animationFrame();
    expect(".o_nudge_private").toHaveCount(0);
});

test("the person chooses who does the work", async () => {
    const { asked, nudge } = await start();
    nudge.open();
    await animationFrame();

    expect(nudge.state.mode).toBe("talk");
    expect(".o_nudge_mode_talk").toHaveClass("o_nudge_mode_on");
    expect(".o_nudge_input").toHaveAttribute("placeholder", "Ask Nudge…");
    await nudge.send("How do I create a contact?");
    expect("mode" in asked.messages[0]).toBe(false);
    nudge.newConversation();
    await animationFrame();

    await click(".o_nudge_mode_agent");
    await animationFrame();
    expect(nudge.state.mode).toBe("agent");
    expect(".o_nudge_mode_agent").toHaveClass("o_nudge_mode_on");
    expect(".o_nudge_mode_agent").toHaveAttribute("aria-pressed", "true");
    expect(".o_nudge_input").toHaveAttribute("placeholder", "Tell Nudge what to do…");
    expect(".o_nudge_agent_text").toHaveCount(1);
    expect(asked.saved).toEqual([{ nudge_mode: "agent" }]);

    await nudge.send("Create a contact");
    expect(asked.messages[1].mode).toBe("agent");
    expect(JSON.parse(asked.messages[1].snapshot).model).toBe("partner");

    await click(".o_nudge_mode_talk");
    expect(asked.saved.at(-1)).toEqual({ nudge_mode: "talk" });
    await nudge.send("Explain");
    expect("mode" in asked.messages[2]).toBe(false);
});

test("do it switches to the one who does", async () => {
    const { asked, nudge } = await start();
    await nudge.send("How do I move this customer to Jenin?");
    expect("mode" in asked.messages[0]).toBe(false);
    await nudge.send("Nudge, do it");
    expect(asked.messages[1].mode).toBe("agent");
    expect(nudge.state.mode).toBe("agent");
    expect(asked.saved).toEqual([{ nudge_mode: "agent" }]);
});

test("what is about to be done is shown, and waits", async () => {
    const { asked, nudge } = await start({ answer: WAITING });
    await nudge.setMode("agent");
    nudge.open();
    await nudge.send("Move Azure to Jenin");
    await animationFrame();

    const answer = nudge.state.messages[1];
    expect(answer.state).toBe("waiting");
    expect(answer.id).toBe(2);
    expect(answer.content).toBe("I am about to change the city.");
    expect(answer.card).toEqual(CARD);
    expect(nudge.state.isStreaming).toBe(false);
    expect(asked.decisions).toHaveLength(0);

    expect(".o_nudge_card").toHaveCount(1);
    expect(".o_nudge_card").toHaveAttribute("data-state", "pending");
    expect(".o_nudge_card_title").toHaveText("Change: Contact");
    expect(queryAllTexts(".o_nudge_card_records li")).toEqual(["Azure Interior"]);
    expect(queryAllTexts(".o_nudge_card_lines dt")).toEqual(["City", "Is a Company", "Phone"]);
    // As it is, and as it will be
    expect(queryAllTexts(".o_nudge_card_before")).toEqual(["Nablus", "No", "0599"]);
    expect(queryAllTexts(".o_nudge_card_value")).toEqual(["Jenin", "Yes", "—"]);
    expect(".o_nudge_confirm").toHaveCount(1);
    expect(".o_nudge_decline").toHaveCount(1);
    expect(".o_nudge_card .fa-exclamation-triangle").toHaveCount(0);
    expect(".o_nudge_typing").toHaveCount(0);
    expect(".o_nudge_message_assistant .o_nudge_markdown").toHaveText("I am about to change the city.");
});

test("a long label leaves its width to the value", async () => {
    // "Invoicing / Units of Measure & Packagings" took the whole card, and
    // "off → on" was written one letter under the other
    const label = "Invoicing / Units of Measure & Packagings / Sell and purchase products in different units";
    const card = { ...CARD, title: "Change the settings", lines: [{ label, value: "on", before: "off" }] };
    const { nudge } = await start({
        answer: streamed({ type: "confirm", ...card }, { type: "waiting", message_id: 2, step_id: 31 }),
    });
    nudge.open();
    await nudge.send("Switch on units of measure");
    await animationFrame();
    const lines = queryOne(".o_nudge_card_lines").getBoundingClientRect();
    const value = queryOne(".o_nudge_card_lines dd").getBoundingClientRect();
    expect(value.width / lines.width).toBeGreaterThan(0.4);
    const before = queryOne(".o_nudge_card_before").getBoundingClientRect();
    const after = queryOne(".o_nudge_card_value").getBoundingClientRect();
    expect(Math.abs(before.top - after.top)).toBeLessThan(4, { message: "off → on on one line" });
});

test("what cannot be taken back is marked", async () => {
    const card = {
        ...CARD,
        kind: "method",
        title: "Press “Post”: Journal Entry",
        lines: [],
        sensitive: true,
    };
    const { nudge } = await start({
        answer: streamed({ type: "confirm", ...card }, { type: "waiting", message_id: 2, step_id: 31 }),
    });
    nudge.open();
    await nudge.send("Post it");
    await animationFrame();
    expect(".o_nudge_card").toHaveClass("o_nudge_card_sensitive");
    expect(".o_nudge_card .fa-exclamation-triangle").toHaveCount(1);
    expect(".o_nudge_card_lines").toHaveCount(0);
});

test("lines of a record are shown under it", async () => {
    const card = {
        ...CARD,
        kind: "create",
        title: "Create: Sales Order",
        records: [],
        lines: [
            { field: "partner_id", label: "Customer", value: "Azure Interior" },
            {
                field: "order_line",
                label: "Order Lines",
                value: null,
                lines: [
                    { field: "product_id", label: "Product", value: "Desk" },
                    { field: "product_uom_qty", label: "Quantity", value: 3 },
                ],
            },
        ],
    };
    const { nudge } = await start({
        answer: streamed({ type: "confirm", ...card }, { type: "waiting", message_id: 2, step_id: 31 }),
    });
    nudge.open();
    await nudge.send("Create the order");
    await animationFrame();
    expect(queryAllTexts(".o_nudge_card > .o_nudge_card_lines > dt")).toEqual(["Customer", "Order Lines"]);
    expect(queryAllTexts(".o_nudge_card_nested dt")).toEqual(["Product", "Quantity"]);
    expect(queryAllTexts(".o_nudge_card_nested .o_nudge_card_value")).toEqual(["Desk", "3"]);
    expect(".o_nudge_card_before").toHaveCount(0);
});

test("yes", async () => {
    onRpc("partner", "web_read", () => {
        expect.step("read again");
    });
    const { asked, nudge } = await start({
        answer: WAITING,
        decide: answered(
            { type: "start", conversation_id: 9, title: "Move it" },
            { type: "decided", ...CARD, state: "done" },
            { type: "changed", model: "partner", ids: [1] },
            { type: "delta", text: "Azure Interior is now in Jenin." }
        ),
    });
    expect.verifySteps(["read again"]);
    nudge.open();
    await nudge.send("Move Azure to Jenin");
    await animationFrame();

    await click(".o_nudge_confirm");
    await animationFrame();
    await animationFrame();
    expect(asked.decisions).toEqual([
        { csrf_token: odoo.csrf_token, conversation_id: "9", step_id: "31", decision: "confirm" },
    ]);
    expect(nudge.state.messages.map((message) => [message.role, message.state])).toEqual([
        ["user", "complete"],
        ["assistant", "complete"],
        ["assistant", "complete"],
    ]);
    expect(nudge.state.messages[1].card.state).toBe("done");
    expect(nudge.state.messages[2].content).toBe("Azure Interior is now in Jenin.");
    expect(nudge.state.messages[2].card).toBe(null);
    // What the screen shows of the record is read again
    expect.verifySteps(["read again"]);

    expect(".o_nudge_card").toHaveAttribute("data-state", "done");
    expect(".o_nudge_card_outcome").toHaveText("Done");
    expect(".o_nudge_confirm").toHaveCount(0);
    expect(".o_nudge_decline").toHaveCount(0);

    // Decided once
    await nudge.decide(31, "confirm");
    expect(asked.decisions).toHaveLength(1);
});

test("a yes whose screen never finishes reloading still ends the answer", async () => {
    let hang = false;
    onRpc("partner", "web_read", () => (hang ? new Promise(() => {}) : undefined));
    const { nudge } = await start({
        answer: WAITING,
        decide: answered(
            { type: "start", conversation_id: 9, title: "Restore it" },
            { type: "decided", ...CARD, state: "done" },
            { type: "changed", model: "partner", ids: [1] }
        ),
    });
    nudge.open();
    await nudge.send("Restore the old city of Raghad");
    await animationFrame();
    hang = true;
    await click(".o_nudge_confirm");
    await animationFrame();
    await animationFrame();
    expect(nudge.state.isStreaming).toBe(false);
    expect(nudge.state.messages[1].card.state).toBe("done");
    expect(nudge.state.messages.at(-1).state).toBe("complete");
});

test("a yes settled without the AI leaves one bubble, its card done", async () => {
    const { nudge } = await start({
        answer: WAITING,
        decide: () =>
            new Response(
                lines(
                    { type: "decided", ...CARD, state: "done" },
                    { type: "delta", text: "Raghad's city is back." },
                    { type: "done", message_id: 2, state: "complete", message: "" }
                ),
                { status: 200 }
            ),
    });
    nudge.open();
    await nudge.send("Restore the old city of Raghad");
    await animationFrame();
    await click(".o_nudge_confirm");
    await animationFrame();
    await animationFrame();
    expect(nudge.state.isStreaming).toBe(false);
    const same = nudge.state.messages.filter((message) => message.id === 2);
    expect(same).toHaveLength(1);
    expect(same[0].card.state).toBe("done");
    expect(same[0].content).toInclude("Raghad's city is back.");
    expect(".o_nudge_typing").toHaveCount(0);
});

test("no", async () => {
    const { asked, nudge } = await start({
        answer: WAITING,
        decide: answered(
            { type: "decided", ...CARD, state: "declined" },
            { type: "delta", text: "All right. What would you like instead?" }
        ),
    });
    nudge.open();
    await nudge.send("Move Azure to Jenin");
    await animationFrame();
    await click(".o_nudge_decline");
    await animationFrame();

    expect(asked.decisions[0].decision).toBe("decline");
    expect(nudge.state.messages[1].card.state).toBe("declined");
    expect(".o_nudge_card_outcome").toHaveText("You said no");
    expect(".o_nudge_card").toHaveAttribute("data-state", "declined");
});

test("what did not work is said", async () => {
    const { nudge } = await start({
        answer: WAITING,
        decide: answered(
            { type: "decided", ...CARD, state: "failed", error: "ValidationError: the city is closed" },
            { type: "delta", text: "It did not go through." }
        ),
    });
    nudge.open();
    await nudge.send("Move Azure to Jenin");
    await animationFrame();
    await click(".o_nudge_confirm");
    await animationFrame();

    expect(".o_nudge_card").toHaveAttribute("data-state", "failed");
    expect(".o_nudge_card_outcome").toHaveText("It did not work");
    expect(".o_nudge_card_error").toHaveText("ValidationError: the city is closed");
});

test("a yes that is refused leaves the question open", async () => {
    const responses = [
        streamed({ type: "error", state: "rejected", message: "Nudge is still answering your previous message." }),
        () => new Response("Forbidden", { status: 403 }),
        answered({ type: "decided", ...CARD, state: "done" }),
    ];
    const { asked, nudge } = await start({ answer: WAITING, decide: () => responses.shift()() });
    nudge.open();
    await nudge.send("Move Azure to Jenin");

    await nudge.decide(31, "confirm");
    expect(nudge.state.messages[1].card.state).toBe("pending");
    expect(nudge.state.messages[2].error).toBe("Nudge is still answering your previous message.");
    await nudge.decide(31, "confirm");
    expect(nudge.state.messages[1].card.state).toBe("pending");
    expect(nudge.state.messages[3].state).toBe("error");
    await animationFrame();
    expect(".o_nudge_confirm").toHaveCount(1);

    await nudge.decide(31, "confirm");
    expect(nudge.state.messages[1].card.state).toBe("done");
    expect(asked.decisions).toHaveLength(3);
    // What is not there is not decided
    await nudge.decide(99, "confirm");
    expect(asked.decisions).toHaveLength(3);
});

test("a new message leaves what waited", async () => {
    const responses = [WAITING, answered({ type: "delta", text: "A contact is a person or a company." })];
    const { asked, nudge } = await start({ answer: () => responses.shift()() });
    nudge.open();
    await nudge.send("Move Azure to Jenin");
    await nudge.send("Wait, what is a contact?");
    await animationFrame();

    expect(nudge.state.messages[1].card.state).toBe("cancelled");
    expect(nudge.state.messages[1].state).toBe("complete");
    expect(".o_nudge_card_outcome").toHaveText("Left undecided");
    expect(".o_nudge_confirm").toHaveCount(0);
    await nudge.decide(31, "confirm");
    expect(asked.decisions).toHaveLength(0);
});

test("what the person typed and did not save is theirs", async () => {
    onRpc("partner", "web_read", () => {
        expect.step("read again");
    });
    const { nudge } = await start({
        answer: answered({ type: "changed", model: "partner", ids: [1] }),
    });
    expect.verifySteps(["read again"]);

    await click(".o_field_widget[name=city] input");
    await edit("Typed by hand", { confirm: "tab" });
    await nudge.send("Do it");
    expect.verifySteps([]);
    expect(".o_field_widget[name=city] input").toHaveValue("Typed by hand");
});

test("only what the screen shows is read again", async () => {
    onRpc("partner", "web_read", () => {
        expect.step("read again");
    });
    const responses = [
        answered({ type: "changed", model: "partner", ids: [2] }),
        answered({ type: "changed", model: "sale.order", ids: [1] }),
        answered({ type: "changed", model: "partner", ids: [2, 1] }),
    ];
    const { nudge } = await start({ answer: () => responses.shift()() });
    expect.verifySteps(["read again"]);
    await nudge.send("Another record");
    await nudge.send("Another model");
    expect.verifySteps([]);
    await nudge.send("This record among others");
    expect.verifySteps(["read again"]);
});

test("a list is read again", async () => {
    onRpc("partner", "web_search_read", () => {
        expect.step("searched again");
    });
    const { nudge } = await start(
        { answer: answered({ type: "changed", model: "partner", ids: [7] }) },
        { type: "list", arch: LIST }
    );
    expect.verifySteps(["searched again"]);
    await nudge.send("Create a contact");
    expect.verifySteps(["searched again"]);
});

test("a button that opens a window opens it", async () => {
    const opened = { type: "ir.actions.act_window", name: "Register Payment", res_model: "payment" };
    const { nudge } = await start({
        answer: WAITING,
        decide: answered(
            { type: "decided", ...CARD, state: "done" },
            { type: "changed", model: "partner", ids: [1], action: opened }
        ),
    });
    patchWithCleanup(getService("action"), {
        async doAction(action) {
            expect.step(`${action.name} ${action.res_model}`);
        },
    });
    nudge.open();
    await nudge.send("Pay it");
    await nudge.decide(31, "confirm");
    expect.verifySteps(["Register Payment payment"]);
});

test("a record is opened", async () => {
    const { nudge } = await start({
        answer: answered(
            { type: "delta", text: "Found it." },
            { type: "open", model: "partner", id: 2, name: "Deco Addict", text: "Here is Deco Addict." }
        ),
    });
    patchWithCleanup(getService("action"), {
        async doAction(action) {
            expect.step(JSON.stringify(action));
        },
    });
    await nudge.send("Open Deco");
    expect.verifySteps([
        JSON.stringify({
            type: "ir.actions.act_window",
            res_model: "partner",
            res_id: 2,
            views: [[false, "form"]],
            target: "current",
        }),
    ]);
    expect(nudge.state.messages[1].content).toBe("Found it.\n\nHere is Deco Addict.");
});

test("a list on another screen is opened filtered", async () => {
    const domain = [["name", "ilike", "Deco"]];
    const { nudge } = await start({
        answer: answered({
            type: "filter", model: "partner", domain, title: "Deco", count: 1, text: "Here they are.",
        }),
    });
    patchWithCleanup(getService("action"), {
        async doAction(action) {
            expect.step(JSON.stringify(action));
        },
    });
    await nudge.send("Filter Deco");
    expect.verifySteps([
        JSON.stringify({
            type: "ir.actions.act_window",
            name: "Deco",
            res_model: "partner",
            domain,
            context: {},
            views: [
                [false, "list"],
                [false, "form"],
            ],
            target: "current",
        }),
    ]);
    expect(nudge.state.messages[1].content).toBe("Here they are.");
});

test("the list in front gets the filter", async () => {
    const domain = [["name", "ilike", "Deco"]];
    const { nudge } = await start({
        answer: answered({
            type: "filter", model: "partner", domain, title: "Deco", count: 1, text: "Filtered.",
            group_by: ["country_id", "date:month"],
        }),
    });
    patchWithCleanup(getService("nudge_context"), {
        frontView: () => ({
            type: "list",
            controller: {
                env: {
                    inDialog: false,
                    searchModel: {
                        resModel: "partner",
                        async splitAndAddDomain(added) {
                            expect.step(`filter ${JSON.stringify(added)}`);
                        },
                        createNewGroupBy(fieldName, options) {
                            expect.step(`group ${fieldName} ${JSON.stringify(options)}`);
                        },
                    },
                },
            },
        }),
    });
    patchWithCleanup(getService("action"), {
        async doAction() {
            expect.step("opened");
        },
    });
    await nudge.send("Filter Deco");
    expect.verifySteps([
        `filter ${JSON.stringify(domain)}`,
        "group country_id {}",
        'group date {"interval":"month"}',
    ]);
});

test("an answer with nothing to read says so", async () => {
    const { nudge } = await start({ answer: answered() });
    await nudge.send("Hello");
    expect(nudge.state.messages[1].error).toBe("No answer came. Ask again, in other words.");
});

test("an update of the server while the page is open is told", async () => {
    const { nudge } = await start({
        config: { version: "1.0" },
        answer: answered({ type: "start", conversation_id: 1, message_id: 1, title: "t", version: "99.0" }),
    });
    await nudge.send("Hello");
    expect(nudge.state.isOutdated).toBe(true);
    expect(nudge.state.messages[1].error).toBe("Nudge was updated. Reload the page, then ask again.");
});

test("a conversation read again shows what was done and what waits", async () => {
    onRpc("nudge.conversation", "read", () => [{ id: 9, name: "Move it" }]);
    onRpc("nudge.message", "search_read", () => [
        { id: 1, role: "user", content: "Move Azure to Jenin", state: "complete" },
        { id: 2, role: "assistant", content: "", state: "complete", screen_events: [
            { type: "confirm", ...CARD }, { type: "changed", model: "partner", ids: [1] },
        ] },
        { id: 3, role: "assistant", content: "Done. Now Deco.", state: "waiting", screen_events: [
            { type: "confirm", ...CARD, step_id: 32 },
            { type: "open", model: "partner", id: 2, text: "Here is Deco." },
        ] },
    ]);
    onRpc("nudge.step", "get_cards", ({ args }) => {
        expect.step(`cards of ${args[0]}`);
        return {
            2: { ...CARD, state: "done" },
            3: { ...CARD, step_id: 32, records: [{ id: 2, name: "Deco Addict" }] },
        };
    });
    onRpc("partner", "web_read", () => {
        expect.step("read again");
    });
    const { asked, nudge } = await start();
    expect.verifySteps(["read again"]);
    nudge.open();
    await nudge.openConversation(9);
    await animationFrame();

    // Reading is not doing again
    expect.verifySteps(["cards of 9"]);
    expect(nudge.state.messages.map((message) => message.id)).toEqual([1, 2, 3]);
    expect(nudge.state.messages[1].card.state).toBe("done");
    expect(nudge.state.messages[2].content).toBe("Done. Now Deco.\n\nHere is Deco.");
    expect(".o_nudge_card").toHaveCount(2);
    expect(".o_nudge_confirm").toHaveCount(1);

    await click(".o_nudge_confirm");
    await animationFrame();
    expect(asked.decisions[0].step_id).toBe("32");
    expect(asked.decisions[0].conversation_id).toBe("9");
});

test("those who may not act ask for no card", async () => {
    onRpc("nudge.conversation", "read", () => [{ id: 9, name: "Talk" }]);
    onRpc("nudge.message", "search_read", () => [
        { id: 1, role: "user", content: "Hello", state: "complete" },
    ]);
    onRpc("nudge.step", "get_cards", () => {
        expect.step("cards");
        return {};
    });
    const { nudge } = await start({ config: { can_use_agent: false } });
    await nudge.openConversation(9);
    expect.verifySteps([]);
    expect(nudge.state.messages[0].card).toBe(null);
});

test("an answer is rated, and a no says why", async () => {
    const { nudge } = await start({
        answer: answered({ type: "delta", text: "Here is the answer." }),
    });
    onRpc("nudge.message", "rate", ({ args }) => {
        expect.step(`rate ${args[0][0]} ${args[1]} ${JSON.stringify(args[2])}`);
        return true;
    });
    nudge.open();
    await nudge.send("Where are the salaries?");
    await animationFrame();
    expect(".o_nudge_message_assistant .o_nudge_rate_ask").toHaveCount(1);
    await click(".o_nudge_rate_down");
    await animationFrame();
    expect(".o_nudge_rate_note").toHaveCount(1, { message: "A no asks why" });
    await click(".o_nudge_rate_note");
    await edit("In our own payroll module");
    await click(".o_nudge_rate_send");
    await animationFrame();
    const id = nudge.state.messages[1].id;
    expect.verifySteps([`rate ${id} down ""`, `rate ${id} down "In our own payroll module"`]);
    expect(".o_nudge_rate_done").toHaveCount(1);
    expect(nudge.state.messages[1].feedback).toBe("down");
});

test("a question of how is told from an order", () => {
    for (const text of ["How do I confirm it?", "where do I find the bills", "Show me the way",
        "كيف بأكد العرض؟", "وين الفواتير", "فرجيني كيف"]) {
        expect(asksHowTo(text)).toBe(true, { message: text });
    }
    for (const text of ["Confirm the quotation S00003", "Create a contact named Ebrahim",
        "أكّدلي عرض السعر S00002", "غيّرلي مدينة Raghad لحلب", ""]) {
        expect(asksHowTo(text)).toBe(false, { message: text });
    }
});

test("an Arabic word of the report rule is a whole word", () => {
    expect(asksForReport("قارن المبيعات مقابل المشتريات")).toBe(true);
    expect(asksForReport("جدول مقابلة المرشحين")).toBe(false);
    expect(asksForFigures("وكم عدد الموظفين")).toBe(true);
});

test("a question of figures is not asked how, a question of how still is", async () => {
    for (const text of ["How many contacts do we have?", "how much do we owe", "كم عدد جهات الاتصال؟",
        "قديش عدد الموظفين", "kam mowazaf 3andna", "addesh 3adad el fawateer"]) {
        expect(asksForFigures(text)).toBe(true, { message: text });
    }
    for (const text of ["How do I count the contacts?", "كيف أعرف كم عدد الموظفين", "Create a contact", ""]) {
        expect(asksForFigures(text)).toBe(false, { message: text });
    }
    const { asked, nudge } = await start({
        settings: { nudge_explain: "ask" },
        answer: answered({ type: "delta", text: "12 contacts." }),
    });
    nudge.open();
    await animationFrame();
    await nudge.send("How many contacts do we have?");
    await animationFrame();
    expect(".o_nudge_ask").toHaveCount(0, { message: "A count is answered, not asked how" });
    expect(asked.messages.at(-1).message).toBe("How many contacts do we have?");
    await nudge.send("How do I create a contact?");
    await animationFrame();
    expect(".o_nudge_ask").toHaveCount(1, { message: "A question of how still is" });
});

test("an order in Agent mode is the work, without asking how", async () => {
    const { asked, nudge } = await start({
        settings: { nudge_explain: "ask" },
        answer: answered({ type: "delta", text: "Here is the card." }),
    });
    nudge.setMode("agent");
    nudge.open();
    await animationFrame();
    await nudge.send("Confirm the quotation S00003");
    await animationFrame();
    expect(".o_nudge_ask").toHaveCount(0, { message: "An order is not asked how" });
    expect(asked.messages.at(-1).mode).toBe("agent");
    await nudge.send("How do I confirm a quotation?");
    await animationFrame();
    expect(".o_nudge_ask").toHaveCount(1, { message: "A question of how still is" });
});

test("an order in Developer mode is the work, and the choice offers it", async () => {
    const { asked, nudge } = await start({
        config: { can_develop: true },
        settings: { nudge_explain: "ask" },
        answer: answered({ type: "delta", text: "Here is the card." }),
    });
    nudge.setMode("dev");
    nudge.open();
    await animationFrame();
    await nudge.send("اعمل ال Automation انت");
    await animationFrame();
    expect(".o_nudge_ask").toHaveCount(0, { message: "Not asked how: it is the work" });
    expect(asked.messages.at(-1).mode).toBe("dev", { message: "Developer mode reaches the server" });
    expect("explain" in asked.messages.at(-1)).toBe(false);

    await nudge.send("How is a rule made here?");
    await animationFrame();
    expect(".o_nudge_explain_do").toHaveCount(1, { message: "Do it for me is offered in Developer mode too" });
});

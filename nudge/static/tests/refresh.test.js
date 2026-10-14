import { expect, test } from "@odoo/hoot";
import { click, queryAllTexts } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";

import { browser } from "@web/core/browser/browser";
import {
    defineModels,
    fields,
    getMockEnv,
    getService,
    models,
    MockServer,
    mountWebClient,
    onRpc,
    patchWithCleanup,
    serverState,
    webModels,
} from "@web/../tests/web_test_helpers";
import { busModels } from "@bus/../tests/bus_test_helpers";

import { answered, setupNudge, streamed } from "./voice_helpers";

/**
 * What Nudge did on the server is on the screen behind it, without the
 * person reloading the page.
 */

class Partner extends models.Model {
    _name = "partner";

    name = fields.Char({ string: "Name" });
    function = fields.Char({ string: "Job Position" });

    _records = [
        { id: 1, name: "Azure Interior", function: "Seller" },
        { id: 2, name: "Deco Addict", function: "Seller" },
    ];

    _views = {
        list: `<list><field name="name"/><field name="function"/></list>`,
        form: `<form><header><button name="action_send" type="object" string="Send"/></header>
                <sheet><field name="name"/><field name="function"/></sheet></form>`,
        search: `<search/>`,
    };
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Partner, ResCompany, ResPartner, ResUsers, ...Object.values(busModels)]);

const PLAN = {
    step_id: 41,
    kind: "write",
    state: "pending",
    title: "Make them buyers",
    records: [
        { id: 1, name: "Azure Interior" },
        { id: 2, name: "Deco Addict" },
    ],
    lines: [{ label: "1.", value: "Change Job Position: Buyer on 2 × Contact" }],
    sensitive: false,
    error: "",
    plan: { total: 2, passing: 2, failing: [], more_failing: 0, undoable: true, background: false },
    done_count: 0,
    failed_count: 0,
    can_undo: false,
    undone: false,
};

function waiting(card) {
    return streamed(
        { type: "start", conversation_id: 9, message_id: 1, title: card.title },
        { type: "confirm", ...card },
        { type: "waiting", message_id: 2, state: "waiting", step_id: card.step_id, message: "" }
    );
}

function write(values) {
    MockServer.env["partner"].write([1, 2], values);
}

async function start(params, view) {
    patchWithCleanup(browser.location, {
        reload() {
            expect.step("page reloaded");
        },
    });
    const asked = setupNudge({ ...params, config: { can_use_agent: true } });
    await mountWebClient();
    await getService("action").doAction({
        type: "ir.actions.act_window",
        res_model: "partner",
        views: [[false, view.type]],
        res_id: view.resId,
    });
    await animationFrame();
    const nudge = getService("nudge");
    nudge.open();
    return { asked, nudge };
}

test("the list behind shows what a yes wrote, then what Undo wrote back", async () => {
    // "17 records are back as they were", and the list still showed what they had become
    onRpc("nudge.step", "undo_changes", () => {
        write({ function: "Seller" });
        return {
            message: "2 records are back as they were.",
            card: { ...PLAN, state: "done", done_count: 2, can_undo: false, undone: true },
            changed: [{ model: "partner", ids: [1, 2] }],
        };
    });
    const { nudge } = await start(
        {
            answer: waiting(PLAN),
            decide: (...args) => {
                write({ function: "Buyer" });
                return answered(
                    { type: "start", conversation_id: 9, title: "Buyers" },
                    { type: "decided", ...PLAN, state: "done", done_count: 2, can_undo: true },
                    { type: "changed", model: "partner", ids: [1, 2] },
                    { type: "delta", text: "Both are buyers now." }
                )(...args);
            },
        },
        { type: "list" }
    );
    expect(queryAllTexts(".o_data_row [name=function]")).toEqual(["Seller", "Seller"]);
    await nudge.send("Make them all buyers");
    await animationFrame();
    await nudge.decide(41, "confirm");
    await animationFrame();
    expect(queryAllTexts(".o_data_row [name=function]")).toEqual(["Buyer", "Buyer"]);

    await click(".o_nudge_undo");
    await animationFrame();
    await animationFrame();
    expect(queryAllTexts(".o_data_row [name=function]")).toEqual(["Seller", "Seller"]);
    expect.verifySteps([]);
});

test("what was done in the background is on the screen once it is over", async () => {
    onRpc("nudge.run", "get_news", () => []);
    onRpc("nudge.conversation", "read", () => [{ id: 9, name: "Buyers" }]);
    onRpc("nudge.message", "search_read", () => []);
    onRpc("nudge.step", "get_cards", () => ({}));
    await start({}, { type: "list" });
    getService("nudge").close();
    write({ function: "Buyer" });
    MockServer.env["bus.bus"]._sendone(serverState.partnerId, "nudge.run", {
        kind: "done",
        run_id: 5,
        conversation_id: 9,
        state: "done",
        request: "Make them all buyers",
        progress: "",
        text: "Both are buyers now.",
        card: null,
        changed: [{ model: "partner", ids: [1, 2] }],
    });
    await animationFrame();
    await animationFrame();
    await animationFrame();
    expect(queryAllTexts(".o_data_row [name=function]")).toEqual(["Buyer", "Buyer"]);
});

test("a button built on the form is on it at once, without reloading the page", async () => {
    // The Review Email button was made, and the form showed it only once the
    // page was opened again: the browser kept the form it had read before
    const BUTTON = {
        ...PLAN,
        step_id: 51,
        kind: "create",
        title: "Add the button “Review Email” with its email",
        plan: false,
    };
    const forgotten = [];
    const forget = (event) => forgotten.push(event.detail ?? "everything");
    const { nudge } = await start(
        {
            answer: waiting(BUTTON),
            decide: (...args) => {
                MockServer.env["partner"]._views = {
                    ...MockServer.env["partner"]._views,
                    form: `<form><header><button name="action_send" type="object" string="Send"/>
                        <button name="99" type="action" string="Review Email"/></header>
                        <sheet><field name="name"/><field name="function"/></sheet></form>`,
                };
                return answered(
                    { type: "start", conversation_id: 9, title: "Review" },
                    { type: "decided", ...BUTTON, state: "done" },
                    {
                        type: "changed",
                        model: "nudge.email.flow",
                        ids: [3],
                        action: { type: "ir.actions.client", tag: "reload" },
                    },
                    { type: "delta", text: "The button is on the form." }
                )(...args);
            },
        },
        { type: "form", resId: 1 }
    );
    getMockEnv().bus.addEventListener("CLEAR-CACHES", forget);
    expect(queryAllTexts(".o_form_statusbar button")).toEqual(["Send"]);
    await nudge.send("Add a Review Email button");
    await animationFrame();
    await nudge.decide(51, "confirm");
    await animationFrame();
    await animationFrame();
    getMockEnv().bus.removeEventListener("CLEAR-CACHES", forget);
    expect(queryAllTexts(".o_form_statusbar button")).toEqual(["Send", "Review Email"]);
    expect(forgotten.includes("everything")).toBe(true);
    expect.verifySteps([]);
    expect(nudge.state.isOpen).toBe(true);
});

test("a change of the settings still reloads the page, with the screens read anew", async () => {
    const SETTING = { ...PLAN, step_id: 61, title: "Change the settings", plan: false };
    const { nudge } = await start(
        {
            answer: waiting(SETTING),
            decide: answered(
                { type: "start", conversation_id: 9, title: "Settings" },
                { type: "decided", ...SETTING, state: "done" },
                {
                    type: "changed",
                    model: "res.config.settings",
                    ids: [],
                    action: { type: "ir.actions.client", tag: "reload" },
                }
            ),
        },
        { type: "list" }
    );
    await nudge.send("Turn on discounts");
    await animationFrame();
    await nudge.decide(61, "confirm");
    await animationFrame();
    await animationFrame();
    expect.verifySteps(["page reloaded"]);
});

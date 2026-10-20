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
    patchWithCleanup,
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

const PLAN = {
    step_id: 41,
    kind: "write",
    state: "pending",
    title: "Make them buyers",
    records: [{ id: 1, name: "Azure Interior" }],
    lines: [{ label: "1.", value: "Change Job Position: Buyer on 30 × Contact" }],
    sensitive: false,
    error: "",
    plan: {
        total: 30,
        passing: 28,
        failing: [{ name: "Deco Addict", reason: "You cannot create recursive hierarchies." }],
        more_failing: 1,
        undoable: true,
        background: true,
    },
    done_count: 0,
    failed_count: 0,
    can_undo: false,
    undone: false,
};
const WAITING = streamed(
    { type: "start", conversation_id: 9, message_id: 1, title: "Buyers" },
    { type: "confirm", ...PLAN },
    { type: "waiting", message_id: 2, state: "waiting", step_id: 41, message: "" }
);

async function start(params = {}) {
    const asked = setupNudge({ answer: WAITING, ...params, config: { can_use_agent: true } });
    await mountView({ resModel: "partner", type: "form", arch: "<form><field name='name'/></form>", resId: 1 });
    const nudge = getService("nudge");
    nudge.open();
    await nudge.send("Make them all buyers");
    await animationFrame();
    return { asked, nudge };
}

test("one card for many records: how many go through, and why not the others", async () => {
    await start();
    expect(".o_nudge_plan_count").toHaveText("30 records: 28 go through, 2 do not, as tried just now.");
    expect(queryAllTexts(".o_nudge_plan_failing li")).toEqual([
        "Deco Addict: You cannot create recursive hierarchies.",
        "+ 1",
    ]);
    expect(".o_nudge_confirm").toHaveText("Yes, all of them");
    expect(".o_nudge_confirm_passing").toHaveText("Only the 28 that go through");
    expect(".o_nudge_plan_note").toHaveCount(1, { message: "Done in the background; nothing a button did" });
});

test("yes to those that go through, then undo", async () => {
    onRpc("nudge.step", "undo_changes", ({ args }) => {
        expect.step(`undo ${args[0]}`);
        return { message: "28 records are back as they were.", card: { ...PLAN, state: "done", done_count: 28, can_undo: false, undone: true } };
    });
    const { asked, nudge } = await start({
        decide: answered(
            { type: "start", conversation_id: 9, title: "Buyers" },
            { type: "decided", ...PLAN, state: "done", done_count: 28, can_undo: true },
            { type: "delta", text: "28 contacts are buyers now." }
        ),
    });
    await click(".o_nudge_confirm_passing");
    await animationFrame();
    await animationFrame();
    expect(asked.decisions[0].decision).toBe("passing");
    expect(".o_nudge_card_outcome").toHaveText("28 done");
    expect(nudge.state.messages[1].card.can_undo).toBe(true);
    await click(".o_nudge_undo");
    await animationFrame();
    expect.verifySteps(["undo 41"]);
    expect(".o_nudge_undo").toHaveCount(0);
    // "28 done" stayed, and nothing said the changes were taken back
    expect(".o_nudge_card_outcome").toHaveText("Undone");
    expect(".o_nudge_card_outcome .fa-undo").toHaveCount(1);
});

test("many records go on in the background", async () => {
    const { nudge } = await start({
        decide: answered(
            { type: "start", conversation_id: 9, title: "Buyers" },
            { type: "decided", ...PLAN, state: "working" }
        ),
    });
    await click(".o_nudge_confirm");
    await animationFrame();
    await animationFrame();
    expect(nudge.state.messages[1].card.state).toBe("working");
    expect(".o_nudge_card_outcome").toHaveText("Being done in the background: I'll tell you when it is over.");
    expect(".o_nudge_confirm").toHaveCount(0);
});

test("the person finds the tasks Nudge does by itself in the preferences", async () => {
    setupNudge({ config: { can_use_agent: true } });
    await mountView({ resModel: "partner", type: "form", arch: "<form><field name='name'/></form>", resId: 1 });
    patchWithCleanup(getService("action"), {
        doAction(action) {
            expect.step(`open ${action}`);
        },
    });
    const nudge = getService("nudge");
    nudge.open();
    nudge.showSettings();
    await animationFrame();
    await click(".o_nudge_open_tasks");
    await animationFrame();
    expect.verifySteps(["open nudge.nudge_task_action"]);
    expect(nudge.state.isOpen).toBe(false);
});

test("the person sets up the morning digest in one click and reads a preview", async () => {
    setupNudge({ config: { can_use_agent: true } });
    await mountView({ resModel: "partner", type: "form", arch: "<form><field name='name'/></form>", resId: 1 });
    onRpc("nudge.task", "action_setup_digest", ({ kwargs }) => {
        expect.step(`setup ${kwargs.hour}`);
        return { id: 5, hour: kwargs.hour, tz: "Asia/Dubai" };
    });
    onRpc("nudge.task", "action_preview_digest", () => {
        expect.step("preview");
        return 77;
    });
    onRpc("nudge.conversation", "read", () => [{ id: 77, name: "My morning digest" }]);
    onRpc("nudge.message", "search_read", () => []);
    const nudge = getService("nudge");
    nudge.open();
    nudge.showSettings();
    await animationFrame();
    await click(".o_nudge_digest_hour");
    const select = document.querySelector(".o_nudge_digest_hour");
    select.value = "7";
    select.dispatchEvent(new Event("change", { bubbles: true }));
    await animationFrame();
    await click(".o_nudge_digest_setup");
    await animationFrame();
    await click(".o_nudge_digest_preview");
    await animationFrame();
    expect.verifySteps(["setup 7", "preview"]);
});

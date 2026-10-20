import { expect, test } from "@odoo/hoot";
import { click, queryAllTexts, queryOne } from "@odoo/hoot-dom";
import { advanceTime, animationFrame } from "@odoo/hoot-mock";

import { getService, makeMockEnv, mountWithCleanup, onRpc, patchWithCleanup } from "@web/../tests/web_test_helpers";

import { NudgeCoachCard } from "@nudge/coach/coach_card";
import { setupNudge } from "./voice_helpers";

const CARD = {
    active: true, opened: 1, total: 4, percent: 25,
    next: { text: "You have not opened Orders yet: take the 2-minute tour.", tour_id: 7, xmlid: "sale.action_orders", required: false },
    apps: [{ name: "Sales", screens: [{ name: "Quotations", seen: true }, { name: "Orders", seen: false }] }],
    tours: [],
};

function start(card = CARD) {
    setupNudge({ config: { is_configured: true } });
    onRpc("nudge.coach", "status", () => ({ active: card.active }));
    onRpc("nudge.coach", "get_card", () => card);
    onRpc("nudge.coach", "record_visits", ({ args }) => {
        expect.step(`visits ${args[0].join(",")}`);
        return args[0].length;
    });
    onRpc("nudge.coach", "finish", () => {
        expect.step("finish");
        return true;
    });
}

test("the card shows the progress and the next suggested tour", async () => {
    start();
    await mountWithCleanup(NudgeCoachCard);
    await animationFrame();
    expect(queryOne(".o_nudge_coach_progress").textContent.replace(/\s+/g, " ").trim()).toBe(
        "You opened 1 of 4 key screens."
    );
    expect(queryOne(".o_nudge_coach_bar").style.width).toBe("25%");
    expect(queryAllTexts(".o_nudge_coach_next p")).toEqual([CARD.next.text]);
    expect(".o_nudge_coach_tour").toHaveCount(1);
});

test("a person the coach does not follow sees no card", async () => {
    start({ active: false });
    await mountWithCleanup(NudgeCoachCard);
    await animationFrame();
    expect(".o_nudge_coach").toHaveCount(0);
});

test("Done with training tells the server and the card goes away", async () => {
    start();
    await mountWithCleanup(NudgeCoachCard);
    await animationFrame();
    await click(".o_nudge_coach_done");
    await animationFrame();
    expect.verifySteps(["finish"]);
    expect(".o_nudge_coach").toHaveCount(0);
});

test("the screens opened are sent once, and only the ids", async () => {
    start();
    const env = await makeMockEnv();
    getService("nudge_coach");
    const action = getService("action");
    let current = { action: { id: 42 } };
    patchWithCleanup(action, {
        get currentController() {
            return current;
        },
    });
    env.bus.trigger("ACTION_MANAGER:UI-UPDATED", "current");
    env.bus.trigger("ACTION_MANAGER:UI-UPDATED", "current");
    await advanceTime(30500);
    await animationFrame();
    expect.verifySteps(["visits 42"]);
    // Opened again: nothing more to say
    env.bus.trigger("ACTION_MANAGER:UI-UPDATED", "current");
    current = { action: { id: 43, tag: "something" } };
    env.bus.trigger("ACTION_MANAGER:UI-UPDATED", "current");
    await advanceTime(30500);
    await animationFrame();
    expect.verifySteps(["visits 43"]);
});

import { expect, test } from "@odoo/hoot";
import { click, queryAllTexts, queryAll } from "@odoo/hoot-dom";
import { advanceTime, animationFrame } from "@odoo/hoot-mock";

import { getService, makeMockEnv, mountWithCleanup, onRpc, patchWithCleanup } from "@web/../tests/web_test_helpers";

import { NudgeHome, REFRESH_MS } from "@nudge/home/nudge_home";

const tile = (key, label, count, extra = {}) => ({
    key, section: key.split(".")[0], label, name: label, count, amount: "", trend: null,
    series: [0, 1, 2, 0, 3, 1, 0], model: "partner", ...extra,
});

function start({ layout = { order: [], hidden: [] }, says = [], more = [] } = {}) {
    const saved = [];
    onRpc("nudge.home", "get_home", ({ args }) => {
        expect.step(`get_home ${args[0] ? "forced" : "cached"}`);
        return {
            sections: [
                {
                    key: "sales", title: "Sales",
                    tiles: [
                        tile("sales.quotations", "Quotations awaiting an answer", 4, { trend: 33, amount: "AED 1,200.00" }),
                        tile("sales.new_leads", "New leads", 0),
                    ],
                },
                { key: "purchase", title: "Purchases", tiles: [tile("purchase.vendor_bills", "Vendor bills", 2, { trend: -50 })] },
                ...more,
            ],
            says, layout, is_login_home: false, generated: "2026-10-17 10:00:00",
        };
    });
    onRpc("nudge.home", "save_layout", ({ args }) => {
        saved.push(args[0]);
        return args[0];
    });
    onRpc("nudge.home", "show_tile", ({ args }) => {
        expect.step(`show ${args[0]}`);
        return { type: "ir.actions.act_window", name: "Quotations", res_model: "partner", views: [[false, "list"]], domain: [["id", "=", 1]] };
    });
    return saved;
}

async function mountHome() {
    await makeMockEnv();
    const actions = [];
    patchWithCleanup(getService("action"), { doAction: (action) => actions.push(action) });
    await mountWithCleanup(NudgeHome);
    await animationFrame();
    return actions;
}

test("each section shows its tiles with the number, the trend and the bars", async () => {
    start();
    await mountHome();
    expect.verifySteps(["get_home cached"]);
    expect(queryAllTexts(".o_nudge_home_section h4")).toEqual(["Sales", "Purchases"]);
    expect(queryAllTexts(".o_nudge_home_count")).toEqual(["4", "0", "2"]);
    expect(queryAllTexts(".o_nudge_home_trend")).toEqual(["+33%", "-50%"]);
    expect(queryAll(".o_nudge_home_tile").length).toBe(3);
    expect(queryAll(".o_nudge_home_spark").length).toBe(3);
});

test("a saved report is a tile with its total and the groups behind it, and Show me opens the report", async () => {
    start({
        more: [{
            key: "reports", title: "My Reports",
            tiles: [tile("reports.7", "Sales by salesperson", "AED 180.00", {
                kind: "report", report_id: 7, amount: "the total of Total", series: [150, 30], model: "sale.order",
            })],
        }],
    });
    const actions = await mountHome();
    expect.verifySteps(["get_home cached"]);
    expect(queryAllTexts(".o_nudge_home_section h4")).toEqual(["Sales", "Purchases", "My Reports"]);
    expect(queryAllTexts(".o_nudge_home_count")).toEqual(["4", "0", "2", "AED 180.00"]);
    const buttons = queryAll(".o_nudge_home_show");
    await click(buttons[buttons.length - 1]);
    await animationFrame();
    expect.verifySteps(["show reports.7"]);
    expect(actions.length).toBe(1);
});

test("Show me opens the list the server built for the tile", async () => {
    start();
    const actions = await mountHome();
    expect.verifySteps(["get_home cached"]);
    await click(".o_nudge_home_show");
    await animationFrame();
    expect.verifySteps(["show sales.quotations"]);
    expect(actions.length).toBe(1);
    expect(actions[0].domain).toEqual([["id", "=", 1]]);
});

test("a tile is moved and hidden, and the layout is saved for the person", async () => {
    const saved = start();
    await mountHome();
    expect.verifySteps(["get_home cached"]);
    // The controls of the tiles show only in the customise mode
    expect(".o_nudge_home_later").toHaveCount(0);
    await click(".o_nudge_home_customise");
    await animationFrame();
    await click(".o_nudge_home_section[data-section=sales] .o_nudge_home_later");
    await animationFrame();
    expect(saved.at(-1).order.slice(-2)).toEqual(["sales.new_leads", "sales.quotations"]);
    expect(queryAllTexts(".o_nudge_home_section[data-section=sales] .o_nudge_home_label")).toEqual([
        "New leads", "Quotations awaiting an answer",
    ]);
    await click(".o_nudge_home_section[data-section=sales] .o_nudge_home_hide");
    await animationFrame();
    expect(saved.at(-1).hidden).toEqual(["sales.new_leads"]);
    expect(queryAll(".o_nudge_home_tile").length).toBe(2);
    await click(".o_nudge_home_hidden button");
    await animationFrame();
    await click(".o_nudge_home_unhide");
    await animationFrame();
    expect(saved.at(-1).hidden).toEqual([]);
    expect(queryAll(".o_nudge_home_tile").length).toBe(3);
});

test("what Nudge says comes first, and every five minutes the numbers are counted again", async () => {
    start({ says: [{ kind: "approval", text: "PO0007 above 50,000", model: "partner", res_id: 1 }] });
    const actions = await mountHome();
    expect.verifySteps(["get_home cached"]);
    expect(queryAllTexts(".o_nudge_home_say")).toEqual(["Waiting for your approvalPO0007 above 50,000"]);
    await click(".o_nudge_home_say");
    expect(actions[0].res_id).toBe(1);
    await advanceTime(REFRESH_MS);
    await animationFrame();
    expect.verifySteps(["get_home forced"]);
});

test("the controls of the tiles show only in the customise mode, with labels and mirrored chevrons (UI-07)", async () => {
    start();
    await mountHome();
    expect.verifySteps(["get_home cached"]);
    expect(".o_nudge_home_tile").toHaveAttribute("draggable", "false");
    expect(".o_nudge_home_earlier").toHaveCount(0);
    expect(".o_nudge_home_customise").toHaveAttribute("aria-pressed", "false");
    await click(".o_nudge_home_customise");
    await animationFrame();
    expect(".o_nudge_home").toHaveClass("o_nudge_home_customising");
    expect(".o_nudge_home_customise").toHaveAttribute("aria-pressed", "true");
    expect(".o_nudge_home_tile").toHaveAttribute("draggable", "true");
    expect(".o_nudge_home_earlier").toHaveCount(3);
    expect(".o_nudge_home_earlier").toHaveAttribute("aria-label", "Move this tile earlier");
    expect(".o_nudge_home_hide").toHaveAttribute("aria-label", "Hide this tile");
    expect(".o_nudge_home_earlier .o_nudge_home_flip").toHaveCount(3);
    await click(".o_nudge_home_customise");
    await animationFrame();
    expect(".o_nudge_home_earlier").toHaveCount(0);
});

test("the sections sit in one flowing container and the preferences reach the page (UI-04, NP-23)", async () => {
    start();
    await mountHome();
    expect.verifySteps(["get_home cached"]);
    expect(".o_nudge_home_sections .o_nudge_home_section").toHaveCount(2);
    expect(".o_nudge_home").not.toHaveClass("o_nudge_home_compact");
    getService("nudge").state.compact = true;
    getService("nudge").state.highContrast = true;
    await animationFrame();
    expect(".o_nudge_home").toHaveClass("o_nudge_home_compact");
    expect(".o_nudge_home").toHaveClass("o_nudge_home_hc");
});

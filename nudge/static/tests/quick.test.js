import { expect, test } from "@odoo/hoot";
import { click, edit, queryAllTexts } from "@odoo/hoot-dom";
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

import { setupNudge } from "./voice_helpers";

class Partner extends models.Model {
    _name = "partner";

    name = fields.Char({ string: "Name" });

    _records = [{ id: 1, name: "Azure Interior" }];
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Partner, ResCompany, ResPartner, ResUsers]);

const FOUND = {
    menus: [{ id: 7, path: "Inventory/Configuration/Warehouses" }],
    settings: [{
        app: "stock",
        app_label: "Inventory",
        label: "Multi-Step Routes",
        help: "Use your own routes",
        field: "group_stock_adv_location",
    }],
    pages: [{ title: "Routes", url: "https://www.odoo.com/documentation/19.0/routes.html", snippet: "A route is…" }],
};

async function start(config = {}) {
    setupNudge({ config: { is_configured: false, is_manager: true, ...config } });
    onRpc("nudge.conversation", "get_status", () => ({ is_manager: true, is_configured: false, ...config }));
    await mountView({ resModel: "partner", type: "form", arch: "<form><field name='name'/></form>", resId: 1 });
    const nudge = getService("nudge");
    nudge.open();
    await animationFrame();
    return { nudge, guidance: getService("nudge_guidance") };
}

test("without a provider, screens, settings and pages are searched", async () => {
    onRpc("nudge.quick", "find", ({ args }) => {
        expect.step(`find ${args[0]}`);
        return FOUND;
    });
    await start();
    expect(".o_nudge_setup").toHaveCount(1, { message: "Setting a provider up comes first" });
    expect(".o_nudge_quick_go").not.toBeEnabled();
    expect(".o_nudge_composer").toHaveCount(0, { message: "No conversation without a provider" });

    await click(".o_nudge_quick_input");
    await edit("routes");
    await animationFrame();
    await click(".o_nudge_quick_go");
    await animationFrame();
    expect.verifySteps(["find routes"]);
    expect(queryAllTexts(".o_nudge_quick_head")).toEqual(["Screens", "Settings", "Odoo documentation"]);
    expect(".o_nudge_quick_menu").toHaveText("Inventory/Configuration/Warehouses");
    expect(".o_nudge_quick_setting").toHaveText("Multi-Step Routes · Inventory\nUse your own routes");
    expect(".o_nudge_quick_page").toHaveAttribute("href", FOUND.pages[0].url);
    expect(".o_nudge_quick_page").toHaveAttribute("target", "_blank");
});

test("a screen or a setting found is shown by the pointer", async () => {
    onRpc("nudge.quick", "find", () => FOUND);
    onRpc("nudge.quick", "guide", ({ args }) => {
        expect.step(`guide ${args[0]} ${JSON.stringify(args[1])}`);
        return [{ target: { type: "control", name: "save" }, text: "Save.", advance: "auto" }];
    });
    const { nudge, guidance } = await start();
    await click(".o_nudge_quick_input");
    await edit("routes");
    await animationFrame();
    await click(".o_nudge_quick_go");
    await animationFrame();

    await click(".o_nudge_quick_setting");
    await animationFrame();
    expect.verifySteps([`guide setting {"app":"stock","field":"group_stock_adv_location"}`]);
    expect(guidance.state.isActive).toBe(true);
    expect(nudge.state.isOpen).toBe(false, { message: "The panel steps aside for the pointer" });
});

test("nothing found says so", async () => {
    onRpc("nudge.quick", "find", () => ({ menus: [], settings: [], pages: [] }));
    await start();
    await click(".o_nudge_quick_input");
    await edit("zzz");
    await animationFrame();
    await click(".o_nudge_quick_go");
    await animationFrame();
    expect(".o_nudge_quick_none").toHaveCount(1);
});

test("with a provider, the search is not there", async () => {
    await start({ is_configured: true });
    expect(".o_nudge_quick").toHaveCount(0);
    expect(".o_nudge_composer").toHaveCount(1);
});

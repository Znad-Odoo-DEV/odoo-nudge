import { expect, test } from "@odoo/hoot";
import { queryAllTexts } from "@odoo/hoot-dom";
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

import { answered, setupNudge } from "./voice_helpers";

class Partner extends models.Model {
    _name = "partner";

    name = fields.Char({ string: "Name" });

    _records = [{ id: 1, name: "Azure Interior" }];
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Partner, ResCompany, ResPartner, ResUsers]);

const BOLT = {
    id: 41,
    name: "[HB-8] Hex Bolt Stainless 8 mm",
    code: "HB-8",
    image: "/web/image/product.product/41/image_128",
    price: "2.50 AED",
    cost: "1.10 AED",
    on_hand: 120,
    unit: "Units",
    by_warehouse: [{ name: "Main", quantity: 100 }, { name: "Site A", quantity: 20 }],
    reasons: ["Words: bolt, stainless", "Same size: 8 mm"],
};

async function start(params = {}) {
    const asked = setupNudge(params);
    await mountView({ resModel: "partner", type: "form", arch: "<form><field name='name'/></form>", resId: 1 });
    const nudge = getService("nudge");
    nudge.open();
    await animationFrame();
    return { asked, nudge };
}

test("/product finds products at once, without asking any AI", async () => {
    onRpc("nudge.quick", "find_products", ({ args }) => {
        expect.step(`find ${args[0]}`);
        return { query: args[0], items: [BOLT] };
    });
    const { asked, nudge } = await start();
    await nudge.send("/product bolt stainless 8 mm");
    await animationFrame();
    expect.verifySteps(["find bolt stainless 8 mm"]);
    expect(asked.messages).toHaveLength(0, { message: "No conversation with the provider" });
    expect(".o_nudge_product").toHaveCount(1);
    expect(".o_nudge_product_name").toHaveText("[HB-8] Hex Bolt Stainless 8 mm");
    // Hoot swaps every picture for a blank one: the picture is there, its address is not checked
    expect(".o_nudge_product_image").toHaveCount(1);
    expect(".o_nudge_product_cost").toHaveText("Cost 1.10 AED");
    expect(queryAllTexts(".o_nudge_product_warehouses span")).toEqual(["Main: 100", "Site A: 20"]);
    expect(".o_nudge_product_why").toHaveText("Words: bolt, stainless · Same size: 8 mm");
});

test("/منتج in Arabic, and nothing found is said", async () => {
    onRpc("nudge.quick", "find_products", ({ args }) => ({ query: args[0], items: [] }));
    const { nudge } = await start();
    await nudge.send("/منتج برغي");
    await animationFrame();
    expect(".o_nudge_product").toHaveCount(0);
    expect(".o_nudge_message_assistant .o_nudge_markdown").toHaveText(
        "No product looks like “برغي”. Try another word, a code or a size."
    );
});

test("/product alone says what to write after it", async () => {
    const { nudge } = await start();
    await nudge.send("/product");
    await animationFrame();
    expect(".o_nudge_message_assistant .o_nudge_markdown").toHaveText(
        "Write what you look for after /product: a name, a description, a code or a size."
    );
});

test("a product found by the model is shown as a card too", async () => {
    const { nudge } = await start({
        answer: answered({ type: "products", query: "bolt", items: [BOLT] }),
    });
    await nudge.send("Do we have stainless bolts?");
    await animationFrame();
    expect(".o_nudge_product").toHaveCount(1);
    expect(".o_nudge_notice").toHaveCount(0, { message: "Cards alone are an answer" });
});

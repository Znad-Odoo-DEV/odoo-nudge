import { expect, test } from "@odoo/hoot";
import { animationFrame } from "@odoo/hoot-mock";

import {
    defineModels,
    fields,
    getService,
    models,
    mountView,
    webModels,
} from "@web/../tests/web_test_helpers";

import { setupNudge } from "./voice_helpers";

class Gateway extends models.Model {
    _name = "gateway";

    name = fields.Char({ string: "Name" });
    transaction_code = fields.Char({ string: "Transaction Code" });
    city = fields.Char({ string: "City" });

    _records = [{ id: 1, name: "Raghad gateway", transaction_code: "live-abc-123", city: "Dubai" }];
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Gateway, ResCompany, ResPartner, ResUsers]);

test("a field the view shows as a password never reaches the snapshot, whatever it is called", async () => {
    setupNudge({});
    await mountView({
        resModel: "gateway",
        type: "form",
        resId: 1,
        arch: /* xml */ `
            <form>
                <field name="name"/>
                <field name="city"/>
                <field name="transaction_code" password="True"/>
            </form>`,
    });
    getService("nudge").open();
    await animationFrame();
    const { snapshot } = getService("nudge_context").capture();
    const entries = Object.fromEntries(snapshot.fields.map((entry) => [entry.name, entry]));
    expect(entries.transaction_code.value).toBe("[hidden]");
    expect(entries.transaction_code.masked).toBe(true);
    expect(entries.city.value).toBe("Dubai");
    expect(JSON.stringify(snapshot)).not.toInclude("live-abc-123");
});

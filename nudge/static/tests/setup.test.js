import { expect, test } from "@odoo/hoot";
import { click } from "@odoo/hoot-dom";
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

class Partner extends models.Model {
    _name = "partner";

    name = fields.Char({ string: "Name" });

    _records = [{ id: 1, name: "Azure Interior" }];
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Partner, ResCompany, ResPartner, ResUsers]);

const FORM = /* xml */ `<form><sheet><group><field name="name"/></group></sheet></form>`;

async function start(config) {
    const asked = setupNudge({ config: { can_use_agent: true, ...config }, settings: { nudge_mode: "agent" } });
    await mountView({ resModel: "partner", type: "form", arch: FORM, resId: 1 });
    const nudge = getService("nudge");
    nudge.open();
    await animationFrame();
    return { asked, nudge };
}

test("a database nobody has set up is offered the setup first", async () => {
    const { asked } = await start({ setup_offer: true });
    const first = document.querySelector(".o_nudge_suggestion");
    expect(first.textContent.trim()).toBe("Set up this database with me");
    await click(".o_nudge_suggestion");
    await animationFrame();
    expect(asked.messages[0].message).toBe("Set up this database with me");
});

test("a database that was set up is not offered it", async () => {
    await start({ setup_offer: false });
    expect(document.body.textContent).not.toInclude("Set up this database with me");
});

import { expect, test } from "@odoo/hoot";
import { click, queryAllTexts, queryOne } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";

import {
    defineModels,
    fields,
    getService,
    models,
    mountView,
    webModels,
} from "@web/../tests/web_test_helpers";

import { setupNudge, streamed } from "./voice_helpers";

class Partner extends models.Model {
    _name = "partner";

    name = fields.Char({ string: "Name" });

    _records = [{ id: 1, name: "Azure Interior" }];
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Partner, ResCompany, ResPartner, ResUsers]);

async function start(params = {}) {
    const asked = setupNudge({
        ...params,
        config: { can_use_agent: true, can_develop: true, ...params.config },
    });
    await mountView({ resModel: "partner", type: "form", arch: "<form><field name='name'/></form>", resId: 1 });
    const nudge = getService("nudge");
    nudge.open();
    await animationFrame();
    return { asked, nudge };
}

test("Developer mode is a third button, for those it is open to", async () => {
    const { nudge } = await start();
    expect(queryAllTexts(".o_nudge_mode")).toEqual(["Talk", "Agent", "Dev"]);

    await click(".o_nudge_mode_dev");
    await animationFrame();
    expect(nudge.state.mode).toBe("dev");
    expect(".o_nudge_mode_dev").toHaveClass("o_nudge_mode_on");
    expect(".o_nudge_input").toHaveAttribute("placeholder", "Tell Nudge what to build…");
    expect(".o_nudge_dev_text").toHaveCount(1);
    expect(".o_nudge_agent_text").toHaveCount(0);
    expect(".o_nudge_later").toHaveCount(1, { message: "Everything of Agent mode holds: the background too" });

    await click(".o_nudge_mode_agent");
    await animationFrame();
    expect(nudge.state.mode).toBe("agent");
    expect(".o_nudge_agent_text").toHaveCount(1);
});

test("without the switch, there is no Developer mode", async () => {
    const { nudge } = await start({ config: { can_develop: false } });
    expect(queryAllTexts(".o_nudge_mode")).toEqual(["Talk", "Agent"]);
    nudge.setMode("dev");
    expect(nudge.state.mode).toBe("agent", { message: "Asked for what is not open: the nearest" });
});

test("a card shows code as code", async () => {
    const { nudge } = await start({
        answer: streamed({
            type: "confirm",
            step_id: 41,
            kind: "create",
            state: "pending",
            title: "Create: automation rule “Mark buyers”",
            records: [],
            lines: [
                { label: "When", value: "when a record is created" },
                {
                    label: "Action 1: Run code",
                    lines: [{ label: "Code", value: "for r in records:\n    r.write({})", code: true }],
                },
            ],
            sensitive: true,
            error: "",
        }),
    });
    nudge.setMode("dev");
    await nudge.send("Mark the buyers");
    await animationFrame();
    expect(".o_nudge_card").toHaveClass("o_nudge_card_sensitive");
    expect(".o_nudge_card_code").toHaveCount(1);
    // Read raw: the indentation of code is part of it
    expect(queryOne(".o_nudge_card_code").textContent).toBe("for r in records:\n    r.write({})");
    expect(".o_nudge_card_value").toHaveText("when a record is created");
});

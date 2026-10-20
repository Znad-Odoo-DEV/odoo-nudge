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
    webModels,
} from "@web/../tests/web_test_helpers";

import { answered, setupNudge, streamed } from "./voice_helpers";

class Partner extends models.Model {
    _name = "partner";

    name = fields.Char({ string: "Name" });

    _records = [{ id: 1, name: "Raghad" }];
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Partner, ResCompany, ResPartner, ResUsers]);

const FORM = /* xml */ `<form><field name="name"/></form>`;

async function start(params = {}) {
    const asked = setupNudge({ ...params, config: { can_use_agent: true, ...params.config } });
    await mountView({ resModel: "partner", type: "form", arch: FORM, resId: 1 });
    const nudge = getService("nudge");
    nudge.open();
    await animationFrame();
    return { asked, nudge };
}


const CARD = {
    step_id: 41,
    kind: "tool",
    state: "pending",
    title: "Create a draft vendor bill from the document",
    records: [],
    lines: [
        { field: "partner_id", label: "Vendor", value: "Ebrahim Trading" },
        { field: "reference", label: "Reference", value: "INV-2026-044" },
        { field: "currency_id", label: "Currency", value: "AED" },
        {
            field: "lines",
            label: "Lines",
            value: null,
            lines: [
                { field: "line_1", label: "1. Steel bars", value: "10 × 100.00 · Steel bar" },
                { field: "line_2", label: "2. Delivery", value: "1 × 50.00 · no product: the description is kept" },
            ],
        },
        { field: "totals", label: "Totals", value: "untaxed 1,050.00, taxes 52.50, total 1,102.50" },
        {
            field: "differences",
            label: "Differences with the document",
            value: null,
            lines: [{ field: "difference", label: "Total", value: "Odoo 1,102.50, the document says 1,100.00" }],
        },
        { field: "file", label: "File kept on the record", value: "bill.pdf" },
    ],
    sensitive: false,
    error: "",
};
const WAITING = streamed(
    { type: "start", conversation_id: 9, message_id: 1, title: "Bill" },
    { type: "delta", text: "I read the bill. Nothing is posted." },
    { type: "confirm", ...CARD },
    { type: "waiting", message_id: 2, state: "waiting", step_id: 41, message: "" }
);
const DECIDED = (state) =>
    answered(
        { type: "decided", ...CARD, state },
        { type: "delta", text: state === "done" ? "The draft bill is made." : "Nothing was made." }
    );

test("the card of a document shows its header, its lines and the difference of the totals", async () => {
    const { nudge } = await start({ answer: WAITING });
    await nudge.setMode("agent");
    await nudge.send("Make a bill of this file");
    await animationFrame();

    expect(".o_nudge_card").toHaveAttribute("data-state", "pending");
    expect(".o_nudge_card_title").toHaveText("Create a draft vendor bill from the document");
    expect(queryAllTexts(".o_nudge_card_lines dt")).toEqual([
        "Vendor",
        "Reference",
        "Currency",
        "Lines",
        "1. Steel bars",
        "2. Delivery",
        "Totals",
        "Differences with the document",
        "Total",
        "File kept on the record",
    ]);
    // The lines of the document, and the difference, are nested under their label
    expect(queryAllTexts(".o_nudge_card_nested dt")).toEqual(["1. Steel bars", "2. Delivery", "Total"]);
    expect(queryAllTexts(".o_nudge_card_value")).toInclude("untaxed 1,050.00, taxes 52.50, total 1,102.50");
    expect(queryAllTexts(".o_nudge_card_nested .o_nudge_card_value").at(-1)).toBe(
        "Odoo 1,102.50, the document says 1,100.00"
    );
    expect(".o_nudge_confirm").toHaveCount(1);
    expect(".o_nudge_decline").toHaveCount(1);
});

test("yes posts the confirm of the step", async () => {
    const { asked, nudge } = await start({ answer: WAITING, decide: DECIDED("done") });
    await nudge.setMode("agent");
    await nudge.send("Make a bill of this file");
    await animationFrame();
    expect(asked.decisions).toHaveLength(0);

    await click(".o_nudge_confirm");
    await animationFrame();
    expect(asked.decisions).toHaveLength(1);
    expect(asked.decisions[0].decision).toBe("confirm");
    expect(String(asked.decisions[0].step_id)).toBe("41");
    expect(".o_nudge_card").toHaveAttribute("data-state", "done");
    expect(".o_nudge_confirm").toHaveCount(0);
});

test("no posts the decline, and the card cannot be answered twice", async () => {
    const { asked, nudge } = await start({ answer: WAITING, decide: DECIDED("declined") });
    await nudge.setMode("agent");
    await nudge.send("Make a bill of this file");
    await animationFrame();
    await click(".o_nudge_decline");
    await animationFrame();
    expect(asked.decisions.map((decision) => decision.decision)).toEqual(["decline"]);
    expect(".o_nudge_decline").toHaveCount(0);
});

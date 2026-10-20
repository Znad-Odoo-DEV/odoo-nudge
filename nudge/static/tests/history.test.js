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

import { setupNudge, streamed } from "./voice_helpers";

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


const SUMMARY = [
    "**Timeline**",
    "- 2026-10-01: quotation sent to Ebrahim. [Show me](/odoo/sale.order/12)",
    "- 2026-10-05: Raghad called about the delivery date. [Show me](/odoo/res.partner/7)",
    "",
    "**Next step**: confirm the delivery date with Raghad.",
].join("\n");

const ASKED = streamed(
    { type: "start", conversation_id: 9, message_id: 1, title: "Summary" },
    { type: "delta", text: SUMMARY },
    { type: "done", message_id: 2, state: "complete", message: "" }
);

test("the summary of a record renders its timeline with a Show me link for each record", async () => {
    const { nudge } = await start({ answer: ASKED });
    await nudge.send("Summarise this record");
    await animationFrame();

    expect(".o_nudge_message_assistant .o_nudge_markdown li").toHaveCount(2);
    expect(queryAllTexts(".o_nudge_markdown li")[0]).toMatch(/quotation sent to Ebrahim/);
    const links = [...document.querySelectorAll(".o_nudge_markdown a")];
    expect(links.map((link) => link.textContent)).toEqual(["Show me", "Show me"]);
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
        "/odoo/sale.order/12",
        "/odoo/res.partner/7",
    ]);
    // An address of this Odoo opens in the same window: nothing leaves the site
    expect(links.every((link) => !link.hasAttribute("target"))).toBe(true);
    expect(queryAllTexts(".o_nudge_markdown strong")).toEqual(["Timeline", "Next step"]);
});

test("a link that leaves the site or runs a script is not made out of a summary", async () => {
    const { nudge } = await start({
        answer: streamed(
            {
                type: "delta",
                text: "[a](javascript:alert(1)) [b](//evil.example/x) [Show me](/odoo/sale.order/12)",
            },
            { type: "done", message_id: 2, state: "complete", message: "" }
        ),
    });
    await nudge.send("Summarise this record");
    await animationFrame();
    const links = [...document.querySelectorAll(".o_nudge_markdown a")];
    expect(links.map((link) => link.getAttribute("href"))).toEqual(["/odoo/sale.order/12"]);
});

test("a summary opened again from the history of chats is shown the same", async () => {
    const { nudge } = await start();
    onRpc("nudge.conversation", "search_read", () => [
        { id: 9, name: "Summary of Raghad", write_date: "2026-10-05 08:00:00", message_count: 2 },
    ]);
    onRpc("nudge.conversation", "read", () => [{ id: 9, name: "Summary of Raghad" }]);
    onRpc("nudge.message", "search_read", () => [
        { id: 1, role: "user", content: "Summarise this record", state: "complete" },
        { id: 2, role: "assistant", content: SUMMARY, state: "complete" },
    ]);
    onRpc("nudge.step", "get_cards", () => ({}));
    await nudge.showHistory();
    await animationFrame();
    expect(".o_nudge_title").toHaveText("History");
    expect(".o_nudge_history_name").toHaveText("Summary of Raghad");
    await click(".o_nudge_history_item");
    await animationFrame();
    expect(".o_nudge_title").toHaveText("Summary of Raghad");
    expect(".o_nudge_markdown a").toHaveCount(2);
});

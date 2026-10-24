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
    patchWithCleanup,
    webModels,
} from "@web/../tests/web_test_helpers";

import { setupNudge } from "./voice_helpers";

class Partner extends models.Model {
    _name = "partner";

    name = fields.Char({ string: "Name" });

    _records = [{ id: 1, name: "Raghad" }];
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Partner, ResCompany, ResPartner, ResUsers]);

const CHASE = {
    id: 11,
    name: "Chase a customer",
    description: "A reminder",
    mode: "agent",
    body: "Chase {{customer}} for {{amount}}",
    variables: [
        { name: "customer", type: "string", required: true, label: "Customer", options: [], model: "", value: "" },
        { name: "amount", type: "number", required: true, label: "Amount", options: [], model: "", value: "" },
    ],
    preview: true,
    text: "",
};
const PLAIN = {
    id: 12,
    name: "What is next here?",
    description: "",
    mode: "talk",
    body: "What is next here?",
    variables: [],
    preview: false,
    text: "What is next here?",
};
const SHARED = { ...PLAIN, id: 13, name: "From a colleague", body: "Say hello", text: "Say hello", preview: true };

async function start({ config = {}, chips = [CHASE, PLAIN], settings } = {}) {
    const asked = setupNudge({ config: { is_configured: true, can_use_agent: true, ...config }, settings });
    onRpc("nudge.prompt", "chips", ({ args }) => {
        expect.step(`chips ${args[0]} ${args[1]}`);
        return chips;
    });
    onRpc("nudge.conversation", "get_status", () => ({ is_manager: true, ...config }));
    await mountView({ resModel: "partner", type: "form", arch: "<form><field name='name'/></form>", resId: 1 });
    // A view mounted alone has no action: the screen in front is told to the panel as the action manager would
    patchWithCleanup(getService("action"), {
        get currentController() {
            return { props: { resModel: "partner" } };
        },
    });
    const nudge = getService("nudge");
    nudge.open();
    await animationFrame();
    await animationFrame();
    return { asked, nudge };
}

test("the chips above the input are those of the screen in front", async () => {
    await start();
    expect.verifySteps(["chips partner false"]);
    expect(queryAllTexts(".o_nudge_prompt_chip")).toEqual([
        "Chase a customer\nFill in, then send",
        "What is next here?",
    ]);
    expect(".o_nudge_prompt_chip:first").toHaveAttribute("title", "A reminder");
});

test("without chips the built-in suggestions stay", async () => {
    await start({ chips: [] });
    expect.verifySteps(["chips partner false"]);
    expect(".o_nudge_prompt_chip").toHaveCount(0);
    expect(".o_nudge_suggestion").not.toHaveCount(0);
});

test("a chip with variables opens a small form, then sends the text the server made", async () => {
    onRpc("nudge.prompt", "render", ({ args }) => {
        expect.step(`render ${args[0]} ${JSON.stringify(args[1])}`);
        return { text: "Chase Ebrahim Trading for 1200", mode: "agent" };
    });
    const { asked, nudge } = await start();
    expect.verifySteps(["chips partner false"]);
    await click(".o_nudge_prompt_chip:first");
    await animationFrame();
    expect(".o_nudge_prompt_form").toHaveCount(1);
    expect(".o_nudge_prompt_text").toHaveText("Chase {{Customer}} for {{Amount}}", {
        message: "The text is read before it is sent",
    });
    expect(asked.messages).toHaveLength(0);

    await click(".o_nudge_prompt_form input:first");
    await edit("Ebrahim Trading");
    await animationFrame();
    expect(".o_nudge_prompt_text").toHaveText("Chase Ebrahim Trading for {{Amount}}");
    await click(".o_nudge_prompt_form input:last");
    await edit("1200");
    await click(".o_nudge_prompt_send");
    await animationFrame();
    await animationFrame();
    expect.verifySteps([`render 11 {"customer":"Ebrahim Trading","amount":"1200"}`]);
    expect(".o_nudge_prompt_form").toHaveCount(0);
    expect(asked.messages.map((entry) => entry.message)).toEqual(["Chase Ebrahim Trading for 1200"]);
    expect(nudge.state.mode).toBe("agent", { message: "An Agent prompt puts the panel in Agent mode" });
});

test("a chip with nothing to fill is sent as it is", async () => {
    onRpc("nudge.prompt", "render", ({ args }) => {
        expect.step(`render ${args[0]}`);
        return { text: "What is next here?", mode: "talk" };
    });
    const { asked } = await start();
    expect.verifySteps(["chips partner false"]);
    await click(".o_nudge_prompt_chip:last");
    await animationFrame();
    await animationFrame();
    expect.verifySteps(["render 12"]);
    expect(".o_nudge_prompt_form").toHaveCount(0);
    expect(asked.messages.map((entry) => entry.message)).toEqual(["What is next here?"]);
});

test("the text of a colleague is shown first and sent only when the person says so", async () => {
    onRpc("nudge.prompt", "render", () => ({ text: "Say hello", mode: "talk" }));
    const { asked } = await start({ chips: [SHARED] });
    expect.verifySteps(["chips partner false"]);
    await click(".o_nudge_prompt_chip");
    await animationFrame();
    expect(".o_nudge_prompt_text").toHaveText("Say hello");
    expect(asked.messages).toHaveLength(0);
    await click(".o_nudge_prompt_cancel");
    await animationFrame();
    expect(".o_nudge_prompt_form").toHaveCount(0);
    expect(asked.messages).toHaveLength(0, { message: "Cancel sends nothing" });
});

test("a refused value is told and nothing is sent", async () => {
    onRpc("nudge.prompt", "render", () => {
        throw new Error("rejected");
    });
    const { asked } = await start();
    expect.verifySteps(["chips partner false"]);
    await click(".o_nudge_prompt_chip:first");
    await animationFrame();
    await click(".o_nudge_prompt_send");
    await animationFrame();
    await animationFrame();
    expect(".o_nudge_prompt_error").toHaveCount(1);
    expect(asked.messages).toHaveLength(0);
});

test("without a provider only the prompts the rules understand are offered, and they are answered without AI", async () => {
    onRpc("nudge.prompt", "render", () => ({ text: "Show my overdue invoices", mode: "agent" }));
    const { asked } = await start({
        config: { is_configured: false },
        chips: [{ ...PLAIN, id: 21, name: "Show my overdue invoices", mode: "agent", preview: false }],
    });
    expect.verifySteps(["chips partner true"]);
    expect(".o_nudge_noai_prompts .o_nudge_prompt_chip").toHaveCount(1);
    await click(".o_nudge_noai_prompts .o_nudge_prompt_chip");
    await animationFrame();
    await animationFrame();
    expect(asked.messages.map((entry) => entry.message)).toEqual(["Show my overdue invoices"]);
});

test("a sent message can be kept as a prompt, with the parts that change as variables", async () => {
    onRpc("nudge.prompt", "propose", ({ args }) => {
        expect.step(`propose ${args[0]} ${args[1]} ${JSON.stringify(args[2])}`);
        return {
            body: "Remind Raghad about invoice {{number_1}}",
            variables: [{ name: "number_1", type: "integer", required: true, label: { en: "Number 1" } }],
            found: [{ name: "number_1", kind: "number", sample: "4501", used: true }],
        };
    });
    onRpc("nudge.prompt", "create", ({ args }) => {
        expect.step(`create ${JSON.stringify(args[0][0])}`);
        return [31];
    });
    await start({ chips: [] });
    expect.verifySteps(["chips partner false"]);
    expect(".o_nudge_save_prompt").toHaveCount(0, { message: "Nothing to keep yet" });
    await click(".o_nudge_input");
    await edit("Remind Raghad about invoice 4501");
    await animationFrame();
    await click(".o_nudge_save_prompt");
    await animationFrame();
    expect.verifySteps(["propose Remind Raghad about invoice 4501 false []"]);
    expect(".o_nudge_prompt_save .o_nudge_prompt_text").toHaveText("Remind Raghad about invoice {{number_1}}");
    await click(".o_nudge_prompt_keep");
    await animationFrame();
    await animationFrame();
    expect.verifySteps([
        `create {"name":"Remind Raghad about invoice 4501","body":"Remind Raghad about invoice {{number_1}}","variables":[{"name":"number_1","type":"integer","required":true,"label":{"en":"Number 1"}}],"mode":"talk","scope":"private"}`,
        "chips partner false",
    ]);
    expect(".o_nudge_prompt_save").toHaveCount(0);
});

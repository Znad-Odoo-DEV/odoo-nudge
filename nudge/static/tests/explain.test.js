import { expect, test } from "@odoo/hoot";
import { click } from "@odoo/hoot-dom";
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

    name = fields.Char({ string: "Name", required: true });
    email = fields.Char({ string: "Email" });
    is_company = fields.Boolean({ string: "Is a Company" });
    parent_id = fields.Many2one({ string: "Company", relation: "partner" });

    _records = [
        { id: 1, name: "Azure Interior", is_company: true },
        { id: 2, name: "Raghad", parent_id: 1 },
    ];
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Partner, ResCompany, ResPartner, ResUsers]);

const COMMANDS = [
    { name: "why", words: ["/why", "/لماذا"], kind: "direct", at_once: true, hint: "What stops this form", example: "/why" },
    { name: "field", words: ["/field", "/حقل"], kind: "direct", at_once: true, hint: "What a field is", example: "/field customer" },
    { name: "here", words: ["/here", "/هنا"], kind: "direct", at_once: true, hint: "What this screen is", example: "/here" },
];

const FORM = /* xml */ `
    <form>
        <header>
            <button name="action_confirm" type="object" string="Confirm" title="Confirms the contact"/>
        </header>
        <sheet>
            <group>
                <field name="name"/>
                <field name="email" required="is_company" readonly="parent_id"/>
                <field name="is_company"/>
                <field name="parent_id"/>
            </group>
        </sheet>
    </form>`;

async function start(extra = {}, resId = 2) {
    const asked = setupNudge({ config: { commands: COMMANDS }, ...extra });
    await mountView({ resModel: "partner", type: "form", arch: FORM, resId });
    const nudge = getService("nudge");
    nudge.open();
    await animationFrame();
    return { asked, nudge };
}

test("the screen carries what the explanations need: conditions, the linked record, tooltips, the field in", async () => {
    await start();
    await click(".o_field_widget[name=name] input");
    const { snapshot } = getService("nudge_context").capture();
    const fields_ = Object.fromEntries(snapshot.fields.map((entry) => [entry.name, entry]));
    expect(fields_.email.required_expr).toBe("is_company");
    expect(fields_.email.readonly_expr).toBe("parent_id");
    expect(fields_.name.required_expr).toBe(undefined, { message: "A plain required has no condition" });
    expect(fields_.parent_id.rid).toBe(1);
    expect(snapshot.buttons.find((button) => button.name === "action_confirm").help).toBe(
        "Confirms the contact"
    );
    expect(snapshot.focused_field).toBe("name");
});

test("/why takes the screen with it, is answered at once, and each line can be shown", async () => {
    onRpc("nudge.quick", "run_command", ({ args }) => {
        expect.step(`run ${args[0]} on ${args[2]?.model}`);
        return {
            command: "why", query: "", focused: false, content: "",
            explain: {
                title: "Why can't I save?",
                sections: [
                    { head: "Fill in", items: [{ text: "Name must be filled in.", field: "name", kind: "missing" }] },
                ],
            },
        };
    });
    const { nudge } = await start();
    await nudge.send("/why");
    await animationFrame();
    expect.verifySteps(["run /why on partner"]);
    expect(".o_nudge_found_explain .o_nudge_found_item span").toHaveText("Name must be filled in.");
    expect(".o_nudge_found_show").toHaveCount(1);
    expect(".o_nudge_found_what").toHaveCount(1);
    // What is it? asks the field command about that field
    await click(".o_nudge_found_what");
    await animationFrame();
    expect.verifySteps(["run /field name on partner"]);
});

test("when the AI does not answer, the commands of the screen are offered", async () => {
    onRpc("nudge.quick", "run_command", ({ args }) => {
        expect.step(`run ${args[0]}`);
        return {
            command: "here", query: "", focused: false, content: "",
            explain: { title: "What am I looking at?", sections: [{ head: "", items: [{ text: "You are on Contacts.", field: null, kind: "info" }] }] },
        };
    });
    const { nudge } = await start({
        answer: streamed({ type: "error", message: "The AI provider could not be reached." }),
    });
    await nudge.send("Why is it red?");
    await animationFrame();
    expect(nudge.state.messages.at(-1).state).toBe("error");
    expect(".o_nudge_found_command").toHaveCount(3);
    await click(".o_nudge_found_command:last");
    await animationFrame();
    expect.verifySteps(["run /here"]);
    expect(".o_nudge_found_explain").toHaveText(/What am I looking at\?[\s\S]*You are on Contacts\./);
});

test("before a provider, the commands of the screen are buttons in the search box", async () => {
    onRpc("nudge.conversation", "get_status", () => ({ is_manager: false, is_configured: false }));
    onRpc("nudge.quick", "run_command", ({ args }) => {
        expect.step(`run ${args[0]}`);
        return {
            command: "why", query: "", focused: false, content: "",
            explain: { title: "Why can't I save?", sections: [{ head: "", items: [{ text: "Nothing in the way.", field: null, kind: "info" }] }] },
        };
    });
    await start({ config: { commands: COMMANDS, is_configured: false } });
    expect(".o_nudge_noai_command").toHaveCount(3);
    await click(".o_nudge_noai_command:first");
    await animationFrame();
    expect.verifySteps(["run /why"]);
    expect(".o_nudge_quick_command .o_nudge_found_explain").toHaveText(/Nothing in the way\./);
});

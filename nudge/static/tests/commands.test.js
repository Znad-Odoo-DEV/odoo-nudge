import { expect, test } from "@odoo/hoot";
import { click, edit, press, queryAllTexts } from "@odoo/hoot-dom";
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

const COMMANDS = [
    { name: "help", words: ["/help", "/مساعدة"], kind: "direct", hint: "The commands you can use here", example: "/help" },
    { name: "screen", words: ["/screen", "/شاشة"], kind: "direct", hint: "A screen you can open", example: "/screen invoices" },
    { name: "find", words: ["/find", "/سجل"], kind: "direct", hint: "A record by its name", example: "/find S00042" },
    { name: "sql", words: ["/sql"], kind: "direct", hint: "One SELECT", example: "/sql SELECT 1" },
    { name: "explain", words: ["/explain", "/اشرح"], kind: "focused", hint: "Explained by the AI", example: "/explain tax" },
];

async function start(config = {}) {
    const asked = setupNudge({ config: { commands: COMMANDS, ...config } });
    await mountView({ resModel: "partner", type: "form", arch: "<form><field name='name'/></form>", resId: 1 });
    const nudge = getService("nudge");
    nudge.open();
    await animationFrame();
    return { asked, nudge };
}

function answering(answer) {
    onRpc("nudge.quick", "run_command", ({ args }) => {
        expect.step(`run ${args[0]}`);
        return answer;
    });
}

test("a direct command is answered at once, without asking any AI", async () => {
    answering({
        command: "screen", query: "warehouses", focused: false, content: "",
        menus: [{ id: 7, path: "Inventory/Configuration/Warehouses" }],
    });
    const { asked, nudge } = await start();
    await nudge.send("/screen warehouses");
    await animationFrame();
    expect.verifySteps(["run /screen warehouses"]);
    expect(asked.messages).toHaveLength(0, { message: "No conversation with the provider" });
    expect(".o_nudge_found_menu").toHaveText("Inventory/Configuration/Warehouses");
    expect(".o_nudge_found_open").toHaveCount(1);
});

test("records found are opened with a click", async () => {
    answering({
        command: "find", query: "Azure", focused: false, content: "",
        records: [{ model: "partner", kind: "Contact", id: 1, name: "Azure Interior", state: "" }],
    });
    const { nudge } = await start();
    await nudge.send("/سجل Azure");
    await animationFrame();
    expect.verifySteps(["run /سجل Azure"]);
    expect(".o_nudge_found_record strong").toHaveText("Azure Interior");
    await click(".o_nudge_found_record .o_nudge_found_open");
    await animationFrame();
    expect(".o_form_view .o_field_widget[name=name] input").toHaveValue("Azure Interior");
});

test("nothing found is said, and a query comes back as a table", async () => {
    answering({ command: "find", query: "zz", focused: false, content: "Nothing found for “zz”. Try other words.", records: [] });
    const { nudge } = await start();
    await nudge.send("/find zz");
    await animationFrame();
    expect.verifySteps(["run /find zz"]);
    expect(".o_nudge_message_assistant .o_nudge_markdown").toHaveText("Nothing found for “zz”. Try other words.");
    onRpc("nudge.quick", "run_command", () => ({
        command: "sql", query: "select", focused: false, content: "",
        table: { columns: ["name", "n"], rows: [["Azure", 2], ["Deco", null]], note: "" },
    }));
    await nudge.send("/sql select name, n from somewhere");
    await animationFrame();
    expect(queryAllTexts(".o_nudge_found_table th")).toEqual(["name", "n"]);
    expect(queryAllTexts(".o_nudge_found_table td")).toEqual(["Azure", "2", "Deco", ""]);
});

test("/help puts a command in the message box", async () => {
    answering({ command: "help", query: "", focused: false, content: "Start a message with one of these words.", commands: COMMANDS });
    const { nudge } = await start();
    await nudge.send("/help");
    await animationFrame();
    expect.verifySteps(["run /help"]);
    expect(".o_nudge_found_command").toHaveCount(5);
    expect(".o_nudge_found_command:last .o_nudge_found_ai").toHaveText("AI");
    await click(".o_nudge_found_command:eq(1)");
    await animationFrame();
    expect(".o_nudge_input").toHaveValue("/screen ");
});

test("a focused command goes to the AI, in writing, without being asked how", async () => {
    const { asked, nudge } = await start();
    await nudge.send("/explain analytic account");
    await animationFrame();
    expect(asked.messages).toHaveLength(1);
    expect(asked.messages[0].message).toBe("/explain analytic account");
    expect(".o_nudge_ask").toHaveCount(0, { message: "The how is not asked" });
});

test("typing / offers the commands, picked with the keyboard", async () => {
    await start();
    await click(".o_nudge_input");
    await edit("/");
    await animationFrame();
    expect(".o_nudge_commands li").toHaveCount(5);
    await edit("/s");
    await animationFrame();
    expect(queryAllTexts(".o_nudge_commands li strong")).toEqual(["/screen", "/sql"]);
    await press("ArrowDown");
    await animationFrame();
    await press("Enter");
    await animationFrame();
    expect(".o_nudge_input").toHaveValue("/sql ");
    expect(".o_nudge_commands").toHaveCount(0);
    await edit("/ش");
    await animationFrame();
    expect(queryAllTexts(".o_nudge_commands li strong")).toEqual(["/شاشة"], {
        message: "The word in the alphabet being typed",
    });
    await press("Escape");
    await animationFrame();
    expect(".o_nudge_commands").toHaveCount(0);
    expect(".o_nudge_panel").toHaveCount(1, { message: "Escape closes the list, not Nudge" });
});

test("before a provider, a command answers in the search box", async () => {
    answering({
        command: "screen", query: "warehouses", focused: false, content: "",
        menus: [{ id: 7, path: "Inventory/Configuration/Warehouses" }],
    });
    onRpc("nudge.conversation", "get_status", () => ({ is_manager: true, is_configured: false }));
    await start({ is_configured: false, is_manager: true });
    await click(".o_nudge_quick_input");
    await edit("/screen warehouses");
    await animationFrame();
    await click(".o_nudge_quick_go");
    await animationFrame();
    expect.verifySteps(["run /screen warehouses"]);
    expect(".o_nudge_quick_command .o_nudge_found_menu").toHaveText("Inventory/Configuration/Warehouses");
    await click(".o_nudge_quick_input");
    await edit("/explain tax");
    await animationFrame();
    await click(".o_nudge_quick_go");
    await animationFrame();
    expect(".o_nudge_quick_command .o_nudge_markdown").toHaveText(
        "/explain asks the AI: it works once a provider is set up."
    );
});

test("asked to be taken somewhere, the screens Nudge found are offered", async () => {
    const asked = setupNudge({
        config: { commands: COMMANDS },
        answer: answered(
            { type: "delta", text: "Go to Invoicing, then Vendors." },
            { type: "found", menus: [{ id: 7, path: "Invoicing/Vendors/Bills" }] }
        ),
    });
    await mountView({ resModel: "partner", type: "form", arch: "<form><field name='name'/></form>", resId: 1 });
    const nudge = getService("nudge");
    nudge.open();
    await nudge.send("Take me to the vendor bills");
    await animationFrame();
    expect(asked.messages).toHaveLength(1);
    expect(".o_nudge_found_menu").toHaveText("Invoicing/Vendors/Bills");
    expect(".o_nudge_found_open").toHaveCount(1);
});

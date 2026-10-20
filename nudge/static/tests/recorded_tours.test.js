import { expect, test } from "@odoo/hoot";
import { click, edit, queryAllTexts } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";

import {
    contains,
    defineModels,
    fields,
    getMockEnv,
    getService,
    models,
    mountView,
    onRpc,
    webModels,
} from "@web/../tests/web_test_helpers";

import { registry } from "@web/core/registry";

import "@nudge/tours/tour_client_action";
import { describeClick } from "@nudge/tours/tour_service";
import { setupNudge } from "./voice_helpers";

class Partner extends models.Model {
    _name = "partner";

    name = fields.Char({ string: "Name" });

    _records = [{ id: 1, name: "Raghad" }];
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Partner, ResCompany, ResPartner, ResUsers]);

const ARCH = `<form>
    <header><button name="action_ebrahim" string="Confirm" type="object"/></header>
    <sheet>
        <field name="name"/>
        <notebook><page name="notes_page" string="Notes"><div>Notes</div></page></notebook>
    </sheet>
</form>`;

async function start(config = {}) {
    setupNudge({ config: { is_configured: true, is_manager: true, ...config } });
    onRpc("nudge.conversation", "get_status", () => ({ is_manager: true, is_configured: true, ...config }));
    await mountView({ resModel: "partner", type: "form", arch: ARCH, resId: 1 });
    return { tours: getService("nudge_tours"), guidance: getService("nudge_guidance") };
}

test("a click is described by a name, never by a path in the page", async () => {
    const root = document.createElement("div");
    root.innerHTML = `
        <div class="o_menu_sections"><a data-section="42" data-menu-xmlid="sale.menu_sale_order"><span>Orders</span></a></div>
        <div class="o_notebook_headers"><a class="nav-link" name="notes_page"><span>Notes</span></a></div>
        <button class="o_form_button_save">Save</button>
        <button name="action_confirm" type="button"><span>Confirm</span></button>
        <div class="o_field_widget" name="partner_id"><input/></div>
        <div class="o_nudge"><button name="inside_nudge">Hidden</button></div>
        <p>Nothing</p>`;
    document.body.append(root);
    try {
        expect(describeClick(root.querySelector("a span"))).toEqual({
            kind: "menu", menu_id: "42", xmlid: "sale.menu_sale_order", label: "Orders",
        });
        expect(describeClick(root.querySelector(".nav-link span"))).toEqual({
            kind: "tab", name: "notes_page", label: "Notes",
        });
        expect(describeClick(root.querySelector(".o_form_button_save"))).toEqual({
            kind: "control", name: "save", label: "Save",
        });
        expect(describeClick(root.querySelector("button[name=action_confirm] span"))).toEqual({
            kind: "button", name: "action_confirm", label: "Confirm",
        });
        expect(describeClick(root.querySelector(".o_field_widget input"))).toEqual({
            kind: "field", name: "partner_id", label: "",
        });
        expect(describeClick(root.querySelector(".o_nudge button"))).toBe(null);
        expect(describeClick(root.querySelector("p"))).toBe(null);
    } finally {
        root.remove();
    }
});

test("a manager records a tour: notes, deleting a step, stopping, saving", async () => {
    onRpc("nudge.tour", "record", ({ args }) => {
        expect.step(`record ${JSON.stringify(args)}`);
        return 7;
    });
    onRpc("partner", "action_ebrahim", () => true);
    const { tours } = await start();
    tours.start();
    await animationFrame();
    expect(".o_nudge_tour_bar").toHaveCount(1);

    await click(".o_field_widget[name=name] input");
    await click(".o_field_widget[name=name] input");
    await click("button[name=action_ebrahim]");
    await click(".o_notebook_headers .nav-link");
    await click(".o_nudge_tour_bar .o_nudge_tour_hint, .o_nudge_tour_bar");
    await animationFrame();
    expect(tours.state.steps.map((step) => `${step.kind}:${step.name}`)).toEqual([
        "field:name", "button:action_ebrahim", "tab:notes_page",
    ]);
    expect(tours.state.steps[0].res_model).toBe("partner");
    expect(tours.state.steps[0].view_type).toBe("form");
    expect(".o_nudge_tour_step").toHaveCount(3, { message: "The clicks on the bar itself are not steps" });

    await contains(".o_nudge_tour_step:first-child .o_nudge_tour_note").edit("Write Raghad.");
    await contains(".o_nudge_tour_step:nth-child(2) .o_nudge_tour_remove").click();
    expect(".o_nudge_tour_step").toHaveCount(2);

    await click(".o_nudge_tour_stop");
    await animationFrame();
    expect(".o_nudge_tour_save").not.toBeEnabled({ message: "A tour needs a name" });
    await click(".o_nudge_tour_name");
    await edit("Contact for Ebrahim");
    await click(".o_nudge_tour_phrases_en");
    await edit("contact for ebrahim");
    await click(".o_nudge_tour_phrases_ar");
    await edit("جهة اتصال ابراهيم");
    await animationFrame();
    await click(".o_nudge_tour_save");
    await animationFrame();

    expect.verifySteps([
        `record ${JSON.stringify([
            "Contact for Ebrahim",
            [
                { kind: "field", name: "name", label: "", res_model: "partner", view_type: "form", action: "", note: "Write Raghad." },
                { kind: "tab", name: "notes_page", label: "Notes", res_model: "partner", view_type: "form", action: "", note: "" },
            ],
            "contact for ebrahim",
            "جهة اتصال ابراهيم",
            null,
        ])}`,
    ]);
    expect(".o_nudge_tour_bar").toHaveCount(0, { message: "Recording is over once it is saved" });
    expect(tours.state.isRecording).toBe(false);
});

test("cancelling a recording keeps nothing, and the clicks stop being listened to", async () => {
    onRpc("nudge.tour", "record", () => expect.step("record"));
    onRpc("partner", "action_ebrahim", () => true);
    const { tours } = await start();
    tours.start();
    await click("button[name=action_ebrahim]");
    await animationFrame();
    expect(tours.state.steps).toHaveLength(1);
    await click(".o_nudge_tour_discard");
    await animationFrame();
    expect(".o_nudge_tour_bar").toHaveCount(0);
    await click("button[name=action_ebrahim]");
    expect(tours.state.steps).toHaveLength(0);
    expect.verifySteps([]);
});

test("a stored tour is replayed by the pointer without any AI, and what is missing is told politely", async () => {
    onRpc("nudge.tour", "play", ({ args }) => {
        expect.step(`play ${args[0]}`);
        return {
            id: 7,
            name: "Contact for Ebrahim",
            action: false,
            steps: [
                { target: { type: "field", name: "name" }, text: "Write Raghad.", advance: "auto" },
                { target: { type: "button", name: "action_ebrahim" }, text: "Confirm it.", advance: "auto" },
            ],
            skipped: ["Skipped “Ghost”: this database does not have it."],
        };
    });
    const { tours, guidance } = await start();
    await tours.play(7);
    await animationFrame();
    expect.verifySteps(["play 7"]);
    expect(guidance.state.isActive).toBe(true);
    expect(guidance.state.steps).toHaveLength(2);
    expect(".o_nudge_guide_text").toHaveText("Write Raghad.");
    expect(".o_notification").toHaveText(/Skipped “Ghost”/);
});

test("a tour found by the slash command is replayed from the answer", async () => {
    onRpc("nudge.quick", "run_command", () => ({
        command: "tour",
        query: "raghad",
        focused: false,
        content: "",
        tours: [{ id: 7, name: "Contact for Raghad", steps: 2 }],
        can_record: true,
    }));
    onRpc("nudge.tour", "play", ({ args }) => {
        expect.step(`play ${args[0]}`);
        return {
            id: 7, name: "Contact for Raghad", action: false, skipped: [],
            steps: [{ target: { type: "field", name: "name" }, text: "Write Raghad.", advance: "auto" }],
        };
    });
    const { guidance } = await start({
        commands: [{ name: "tour", words: ["/tour", "/جولة"], kind: "direct", hint: "A tour", example: "/tour" }],
    });
    const nudge = getService("nudge");
    nudge.open();
    await animationFrame();
    await nudge.send("/tour raghad");
    await animationFrame();
    expect(queryAllTexts(".o_nudge_found_tour")).toEqual(["Contact for Raghad · 2"]);
    expect(".o_nudge_found_record_tour").toHaveCount(1, { message: "A manager may record one" });
    await click(".o_nudge_found_tour");
    await animationFrame();
    expect.verifySteps(["play 7"]);
    expect(guidance.state.isActive).toBe(true);
});

test("a tour that cannot be shown here says so and starts nothing", async () => {
    onRpc("nudge.tour", "play", () => ({}));
    const { tours, guidance } = await start();
    expect(await tours.play(99)).toBe(false);
    await animationFrame();
    expect(guidance.state.isActive).toBe(false);
    expect(".o_notification").toHaveText(/cannot be shown here/);
});

test("Record a tour is in the user menu of managers only", async () => {
    const { tours } = await start();
    const item = registry.category("user_menuitems").get("nudge_record_tour")(getMockEnv());
    expect(item.show()).toBe(true);
    item.callback();
    expect(tours.state.isRecording).toBe(true);
    tours.finish();
});

test("Record a tour is not in the user menu of the others", async () => {
    await start({ is_manager: false });
    const item = registry.category("user_menuitems").get("nudge_record_tour")(getMockEnv());
    expect(item.show()).toBe(false);
});

test("the list of recurring questions starts the recorder with the question as name and phrase", async () => {
    onRpc("nudge.tour", "record", ({ args }) => {
        expect.step(`record ${JSON.stringify(args.slice(0, 1).concat(args.slice(2)))}`);
        return 8;
    });
    const { tours } = await start();
    const startRecorder = registry.category("actions").get("nudge_record_tour");
    startRecorder(getMockEnv(), {
        params: { name: "How do I create an invoice for Raghad", phrases_en: "How do I create an invoice for Raghad", phrases_ar: "" },
    });
    await animationFrame();
    expect(tours.state.isRecording).toBe(true);
    expect(tours.state.name).toBe("How do I create an invoice for Raghad");
    expect(tours.state.phrasesEn).toBe("How do I create an invoice for Raghad");
    expect(tours.state.phrasesAr).toBe("");
    await click(".o_field_widget[name=name] input");
    await click(".o_nudge_tour_stop");
    await animationFrame();
    await click(".o_nudge_tour_save");
    await animationFrame();
    expect.verifySteps([
        `record ${JSON.stringify(["How do I create an invoice for Raghad", "How do I create an invoice for Raghad", "", null])}`,
    ]);
});

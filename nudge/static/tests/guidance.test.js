import { expect, getFixture, mockMatchMedia, test } from "@odoo/hoot";
import { click, edit, queryAll, queryOne } from "@odoo/hoot-dom";
import { advanceTime, animationFrame } from "@odoo/hoot-mock";

import { session } from "@web/session";
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

import { PRESS_MS, TRAVEL_MS } from "@nudge/guidance/nudge_guide";
import { findMenu, resolveTarget } from "@nudge/guidance/target_resolver";

class Partner extends models.Model {
    _name = "partner";

    name = fields.Char({ string: "Name" });
    email = fields.Char({ string: "Email" });
    notes = fields.Char({ string: "Notes" });
    state = fields.Selection({
        string: "Status",
        selection: [
            ["draft", "New"],
            ["done", "Confirmed"],
        ],
    });

    _records = [{ id: 1, name: "Azure Interior", email: "azure@example.com", state: "draft" }];
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Partner, ResCompany, ResPartner, ResUsers]);

const FORM = /* xml */ `
    <form>
        <header>
            <button name="action_confirm" type="object" string="Confirm"/>
        </header>
        <sheet>
            <group>
                <field name="name"/>
                <field name="email"/>
            </group>
            <notebook>
                <page string="Details" name="details">
                    <field name="state"/>
                </page>
                <page string="Internal Notes" name="notes_page">
                    <field name="notes"/>
                    <button name="action_in_tab" type="object" string="In Tab"/>
                </page>
            </notebook>
        </sheet>
    </form>`;

/**
 * @param {Object} [options]
 * @param {boolean} [options.motion=true] whether the pointer moves: the test
 *  runner asks for less motion unless told otherwise
 */
async function start({ motion = true } = {}) {
    mockMatchMedia({ "prefers-reduced-motion": motion ? "no-preference" : "reduce" });
    patchWithCleanup(session, { nudge: { can_share_screen: true, is_configured: true } });
    onRpc("action_confirm", () => true);
    // The guide comes with Nudge itself, among the main components
    await mountView({ resModel: "partner", type: "form", arch: FORM, resId: 1 });
    return {
        guidance: getService("nudge_guidance"),
        view: getService("nudge_context").frontView(),
    };
}

function step(type, name, text = "Do this.", advance = "auto") {
    return { target: { type, name }, text, advance };
}

/**
 * The pointer goes to the step and presses it: then the box is there.
 */
async function pointed() {
    await animationFrame();
    await advanceTime(TRAVEL_MS + PRESS_MS + 200);
}

function px(element, property) {
    return parseFloat(element.style[property]);
}

test("targets are found by kind and name", async () => {
    const { view } = await start();

    expect(resolveTarget({ type: "field", name: "email" }, view).element).toBe(
        queryOne(".o_field_widget[name=email]")
    );
    expect(resolveTarget({ type: "button", name: "action_confirm" }, view).element).toBe(
        queryOne("button[name=action_confirm]")
    );
    expect(resolveTarget({ type: "tab", name: "notes_page" }, view).element).toBe(
        queryOne(".o_notebook_headers .nav-link[name=notes_page]")
    );
    expect(resolveTarget({ type: "control", name: "save" }, view).element).toBe(
        queryOne(".o_form_button_save")
    );
    // A control this screen does not have
    expect(resolveTarget({ type: "control", name: "filters" }, view)).toBe(null);
    expect(resolveTarget({ type: "control", name: "format_disk" }, view)).toBe(null);
    expect(resolveTarget({ type: "field", name: "nowhere" }, view)).toBe(null);
    expect(resolveTarget({ type: "button", name: "nowhere" }, view)).toBe(null);
});

test("what is in a closed tab is reached through the tab", async () => {
    const { view } = await start();
    const tab = queryOne(".o_notebook_headers .nav-link[name=notes_page]");

    for (const target of [
        { type: "field", name: "notes" },
        { type: "button", name: "action_in_tab" },
    ]) {
        const found = resolveTarget(target, view);
        expect(found.element).toBe(tab);
        expect(found.isDetour).toBe(true);
        expect(found.text).toBe("Open the tab “Internal Notes” first.");
    }

    await click(tab);
    await animationFrame();
    const found = resolveTarget({ type: "field", name: "notes" }, view);
    expect(found.element).toBe(queryOne(".o_field_widget[name=notes]"));
    expect(Boolean(found.isDetour)).toBe(false);
});

test("a name is never read as a selector", async () => {
    const { view } = await start();
    const hostile = [
        'email"], body *, [x="',
        "*",
        "email], .o_form_view",
        "x' onmouseover='alert(1)",
        "../../etc",
    ];
    for (const name of hostile) {
        for (const type of ["field", "button", "tab", "control"]) {
            expect(resolveTarget({ type, name }, view)).toBe(null, { message: `${type} ${name}` });
        }
    }
    expect(resolveTarget({ type: "selector", name: ".o_form_view" }, view)).toBe(null);
    expect(resolveTarget({ type: "field", name: { toString: () => "email" } }, view)).toBe(null);
    expect(resolveTarget(null, view)).toBe(null);
});

test("the pointer sets off, reaches the step and presses it, then the box comes", async () => {
    const { guidance } = await start();
    guidance.begin([step("button", "action_confirm", "Press Confirm.")]);
    await animationFrame();

    // On its way: the screen is shaded and ringed, nothing is said yet
    expect(".o_nudge_shade").toHaveCount(4);
    expect(".o_nudge_ring").toHaveCount(1);
    expect(".o_nudge_cursor").toHaveCount(1);
    expect(".o_nudge_cursor").not.toHaveClass("o_nudge_cursor_pressing");
    expect(".o_nudge_guide_bubble").toHaveCount(0);
    const button = queryOne("button[name=action_confirm]").getBoundingClientRect();
    const cursor = queryOne(".o_nudge_cursor");
    const farFromTheButton = Math.hypot(
        px(cursor, "left") + 2 - (button.left + button.width / 2),
        px(cursor, "top") + 2 - (button.top + button.height * 0.55)
    );
    expect(farFromTheButton).toBeGreaterThan(20);

    // Arrived: it presses
    await advanceTime(TRAVEL_MS + 100);
    expect(".o_nudge_cursor").toHaveClass("o_nudge_cursor_pressing");
    expect(".o_nudge_guide_bubble").toHaveCount(0);

    // Pressed: the box, and the tip of the pointer on the button
    await advanceTime(PRESS_MS + 100);
    expect(".o_nudge_cursor").toHaveClass("o_nudge_cursor_still");
    expect(".o_nudge_guide_bubble").toHaveCount(1);
    expect(".o_nudge_guide_text").toHaveText("Press Confirm.");
    expect(Math.abs(px(cursor, "left") + 2 - (button.left + button.width / 2))).toBeLessThan(1.5);
    expect(Math.abs(px(cursor, "top") + 2 - (button.top + button.height * 0.55))).toBeLessThan(1.5);
});

test("the shades leave only the step clear", async () => {
    const { guidance } = await start();
    guidance.begin([step("field", "email")]);
    await pointed();

    const box = queryOne(".o_field_widget[name=email]").getBoundingClientRect();
    const root = document.documentElement;
    const [top, bottom, left, right] = [
        ".o_nudge_shade_top",
        ".o_nudge_shade_bottom",
        ".o_nudge_shade_left",
        ".o_nudge_shade_right",
    ].map((selector) => queryOne(selector));

    // The hole is the ring: the field and 6px around it
    expect(Math.abs(px(top, "height") - (box.top - 6))).toBeLessThan(1.5);
    expect(px(top, "width")).toBe(root.clientWidth);
    expect(Math.abs(px(bottom, "top") - (box.bottom + 6))).toBeLessThan(1.5);
    expect(Math.abs(px(bottom, "height") - (root.clientHeight - box.bottom - 6))).toBeLessThan(1.5);
    expect(Math.abs(px(left, "width") - (box.left - 6))).toBeLessThan(1.5);
    expect(Math.abs(px(right, "left") - (box.right + 6))).toBeLessThan(1.5);
    expect(Math.abs(px(left, "height") - (box.height + 12))).toBeLessThan(1.5);
    for (const shade of queryAll(".o_nudge_shade")) {
        expect(getComputedStyle(shade).pointerEvents).toBe("none");
    }
});

test("the pointer goes from one step to the next", async () => {
    const { guidance } = await start();
    guidance.begin([step("field", "email", "Type the address."), step("button", "action_confirm", "Then confirm.")]);
    await pointed();
    const cursor = queryOne(".o_nudge_cursor");
    const email = px(cursor, "left");

    await click(".o_nudge_guide_next");
    await animationFrame();
    // The box is gone while the pointer is on its way to the button
    expect(".o_nudge_guide_bubble").toHaveCount(0);
    expect(".o_nudge_cursor").toHaveCount(1);
    await pointed();
    expect(".o_nudge_guide_text").toHaveText("Then confirm.");
    const button = queryOne("button[name=action_confirm]").getBoundingClientRect();
    expect(Math.abs(px(cursor, "left") + 2 - (button.left + button.width / 2))).toBeLessThan(1.5);
    expect(px(cursor, "left")).not.toBe(email);
});

test("with less motion asked, the box is there at once", async () => {
    const { guidance } = await start({ motion: false });
    guidance.begin([step("field", "email", "Type the address.")]);
    await animationFrame();

    expect(".o_nudge_guide_bubble").toHaveCount(1);
    expect(".o_nudge_guide_text").toHaveText("Type the address.");
    expect(".o_nudge_cursor").toHaveClass("o_nudge_cursor_still");
    expect(".o_nudge_cursor").not.toHaveClass("o_nudge_cursor_pressing");
});

test("steps follow what the person does", async () => {
    const { guidance } = await start();
    guidance.begin([
        step("field", "email", "Type the address."),
        step("button", "action_confirm", "Then confirm."),
    ]);
    await pointed();

    expect(".o_nudge_ring").toHaveCount(1);
    expect(".o_nudge_guide_text").toHaveText("Type the address.");
    expect(".o_nudge_guide_count").toHaveText("1 / 2");
    expect(guidance.state.element).toBe(queryOne(".o_field_widget[name=email]"));
    expect(".o_nudge_guide_previous").toHaveCount(0);

    // Leaving the field and the value it now holds both tell the step is
    // done: it is left once all the same
    await click(".o_field_widget[name=email] input");
    await edit("new@example.com", { confirm: "tab" });
    await advanceTime(500);
    await animationFrame();
    await advanceTime(2000);
    await pointed();

    expect(guidance.state.index).toBe(1);
    expect(".o_nudge_guide_text").toHaveText("Then confirm.");
    expect(".o_nudge_guide_count").toHaveText("2 / 2");
    expect(guidance.state.element).toBe(queryOne("button[name=action_confirm]"));
    expect(".o_nudge_guide_next").toHaveText("Done");

    await click("button[name=action_confirm]");
    await advanceTime(500);
    await animationFrame();
    expect(guidance.state.isActive).toBe(false);
    expect(".o_nudge_ring").toHaveCount(0);
    expect(".o_nudge_shade").toHaveCount(0);
    expect(".o_nudge_cursor").toHaveCount(0);
});

test("a closed tab is opened on the way", async () => {
    const { guidance } = await start();
    guidance.begin([step("field", "notes", "Write a note.")]);
    await pointed();

    expect(".o_nudge_ring").toHaveClass("o_nudge_ring_detour");
    expect(".o_nudge_guide_text").toHaveText("Open the tab “Internal Notes” first.");
    expect(".o_nudge_guide_count").toHaveText("1 / 1");

    await click(".o_notebook_headers .nav-link[name=notes_page]");
    await animationFrame();
    // The tab is drawn, then the screen is looked at again, and the pointer
    // goes on to the field
    await advanceTime(500);
    await pointed();

    expect(guidance.state.isActive).toBe(true);
    expect(guidance.state.index).toBe(0);
    expect(".o_nudge_ring").not.toHaveClass("o_nudge_ring_detour");
    expect(".o_nudge_guide_text").toHaveText("Write a note.");
    expect(guidance.state.element).toBe(queryOne(".o_field_widget[name=notes]"));
});

test("next, back and stop are always at hand", async () => {
    const { guidance } = await start();
    guidance.begin([
        step("field", "name", "First.", "manual"),
        step("field", "email", "Second."),
    ]);
    await pointed();

    // A step that only explains does not move on its own
    await click(".o_field_widget[name=name] input");
    await edit("Changed", { confirm: "tab" });
    await advanceTime(500);
    await animationFrame();
    expect(".o_nudge_guide_text").toHaveText("First.");

    await click(".o_nudge_guide_next");
    await pointed();
    expect(".o_nudge_guide_text").toHaveText("Second.");
    await click(".o_nudge_guide_previous");
    await pointed();
    expect(".o_nudge_guide_text").toHaveText("First.");

    await click(".o_nudge_guide_stop");
    await animationFrame();
    expect(guidance.state.isActive).toBe(false);
    expect(".o_nudge_guide_bubble").toHaveCount(0);
    expect(".o_nudge_shade").toHaveCount(0);
});

test("the screen stays usable under the shades", async () => {
    const { guidance } = await start();
    guidance.begin([step("field", "email")]);
    await pointed();

    for (const selector of [".o_nudge_ring", ".o_nudge_cursor", ".o_nudge_shade_top"]) {
        expect(getComputedStyle(queryOne(selector)).pointerEvents).toBe("none");
    }
    await click(".o_field_widget[name=name] input");
    await edit("Still editable", { confirm: "tab" });
    expect(".o_field_widget[name=name] input").toHaveValue("Still editable");
    expect(guidance.state.index).toBe(0);
});

test("what cannot be found is said, with the text of the step", async () => {
    const { guidance } = await start();
    guidance.begin([step("button", "gone_button", "Press the button."), step("field", "email")]);
    await animationFrame();
    expect(".o_nudge_guide_bubble").toHaveCount(0);

    await advanceTime(3000);
    await animationFrame();
    expect(".o_nudge_guide_lost .o_nudge_guide_notice").toHaveText("I can't find this on the screen.");
    expect(".o_nudge_guide_text").toHaveText("Press the button.");
    expect(".o_nudge_ring").toHaveCount(0);
    expect(".o_nudge_shade").toHaveCount(0);
    expect(".o_nudge_cursor").toHaveCount(0);

    await click(".o_nudge_guide_next");
    await pointed();
    expect(guidance.state.element).toBe(queryOne(".o_field_widget[name=email]"));
    expect(".o_nudge_guide_text").toHaveText("Do this.");
});

test("steps that are not steps are ignored", async () => {
    const { guidance } = await start();
    for (const steps of [null, undefined, [], "steps", {}]) {
        guidance.begin(steps);
        expect(guidance.state.isActive).toBe(false);
    }
});

/**
 * A navbar as Odoo draws it: the apps menu, a section with a dropdown, an
 * item of the dropdown when it is open.
 */
function navbar({ open = false } = {}) {
    const bar = document.createElement("div");
    bar.className = "o_main_navbar";
    bar.innerHTML = `
        <div class="o_navbar_apps_menu"><button class="o_apps_toggle">Apps</button></div>
        <a class="o_menu_brand" data-menu-xmlid="test.app" data-section="10">Test App</a>
        <div class="o_menu_sections">
            <button data-menu-xmlid="test.reports"><span data-section="11">Reports</span></button>
            ${open ? '<a class="o-dropdown-item" data-menu-xmlid="test.report" data-section="12">Report</a>' : ""}
        </div>`;
    getFixture().append(bar);
    return bar;
}

function menuStep(id, { app = false, parent = "", label = "" } = {}) {
    return {
        target: { type: "menu", name: String(id), xmlid: "", app, parent: String(parent), parent_label: label },
        text: `Menu ${id}`,
        advance: "auto",
    };
}

test("menus are found by id, a section by the span of its button", async () => {
    await start();
    navbar({ open: true });
    expect(findMenu("12", "")).toBe(queryOne(".o-dropdown-item[data-section='12']"));
    expect(findMenu("11", "")).toBe(queryOne("button[data-menu-xmlid='test.reports']"));
    expect(findMenu("", "test.reports")).toBe(queryOne("button[data-menu-xmlid='test.reports']"));
    expect(findMenu("99", "")).toBe(null);
    // What a model writes is never a selector
    expect(findMenu('11"], *, [x="', "")).toBe(null);
    expect(findMenu("", 'test"], body')).toBe(null);
});

test("a menu not on the screen is reached through the one above it, or the apps", async () => {
    await start();
    navbar();
    const item = resolveTarget(menuStep(12, { parent: 11, label: "Reports" }).target, null);
    expect(item.element).toBe(queryOne("button[data-menu-xmlid='test.reports']"));
    expect(item.isDetour).toBe(true);
    expect(item.text).toBe("Open “Reports” first.");

    const app = resolveTarget(menuStep(20, { app: true }).target, null);
    expect(app.element).toBe(queryOne(".o_apps_toggle"));
    expect(app.text).toBe("Open the apps first.");
});

test("someone already in the app goes straight to its menus", async () => {
    const { guidance } = await start();
    patchWithCleanup(getService("menu"), { getCurrentApp: () => ({ id: 10 }) });
    navbar();
    guidance.begin([menuStep(10, { app: true }), menuStep(11, { parent: 10 }), menuStep(12, { parent: 11 })]);
    await animationFrame();
    expect(guidance.state.steps.map((step) => step.target.name)).toEqual(["11", "12"]);
    expect(guidance.state.element).toBe(queryOne("button[data-menu-xmlid='test.reports']"));
    guidance.stop();

    patchWithCleanup(getService("menu"), { getCurrentApp: () => ({ id: 77 }) });
    guidance.begin([menuStep(10, { app: true }), menuStep(11, { parent: 10 })]);
    await animationFrame();
    expect(guidance.state.steps).toHaveLength(2, { message: "From another app, the app comes first" });
    guidance.stop();
});

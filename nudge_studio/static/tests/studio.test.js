import { expect, getFixture, test } from "@odoo/hoot";

import { resolveTarget } from "@nudge/guidance/target_resolver";
import "@nudge_studio/studio_targets";

function studioScreen() {
    getFixture().innerHTML = `
        <div class="o_web_studio_menu">
            <a href="#">Views</a><a href="#">Reports</a><a href="#">Automations</a>
            <button class="o_web_studio_leave">Close</button>
        </div>
        <div class="o_web_studio_editor_manager">
            <div class="o_web_studio_sidebar">
                <div class="o_web_studio_new">Add</div>
                <div class="o_web_studio_view">View</div>
                <div class="o_web_studio_properties">Properties</div>
                <div class="o_web_studio_new_fields">
                    <div class="o_web_studio_component o_web_studio_field_char">Text</div>
                    <div class="o_web_studio_component o_web_studio_field_many2one">Many2One</div>
                </div>
            </div>
            <div class="o_web_studio_view_renderer">
                <button class="o-web-studio-editor--add-button-action">Add a button</button>
            </div>
        </div>`;
}

test("the tabs, components, menus and controls of Studio are found by name", () => {
    studioScreen();
    expect(resolveTarget({ type: "studio_tab", name: "properties" }, null).element).toHaveClass("o_web_studio_properties");
    expect(resolveTarget({ type: "studio_component", name: "many2one" }, null).element).toHaveClass("o_web_studio_field_many2one");
    expect(resolveTarget({ type: "studio_menu", name: "automations" }, null).element).toHaveText("Automations");
    expect(resolveTarget({ type: "studio_control", name: "add_button" }, null).element).toHaveText("Add a button");
    expect(resolveTarget({ type: "studio_control", name: "leave" }, null).element).toHaveClass("o_web_studio_leave");
    expect(resolveTarget({ type: "studio_tab", name: "nowhere" }, null)).toBe(null);
    expect(resolveTarget({ type: "studio_menu", name: "nowhere" }, null)).toBe(null);
    expect(resolveTarget({ type: "studio_component", name: "x\"]" }, null)).toBe(null);
});

test("a component that is not shown leads through the Add tab", () => {
    studioScreen();
    getFixture().querySelector(".o_web_studio_new_fields").remove();
    const found = resolveTarget({ type: "studio_component", name: "char" }, null);
    expect(found.isDetour).toBe(true);
    expect(found.element).toHaveClass("o_web_studio_new");
});

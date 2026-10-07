import { expect, getFixture, test } from "@odoo/hoot";
import { click, edit, press } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";

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

import { hotkeyLabel } from "@nudge/core/hotkeys";
import { formatValue, summarize, takeSnapshot } from "@nudge/context/snapshot";

class Partner extends models.Model {
    _name = "partner";

    name = fields.Char({ string: "Name", required: true });
    email = fields.Char({ string: "Email" });
    age = fields.Integer({ string: "Age" });
    is_company = fields.Boolean({ string: "Is a Company" });
    birthday = fields.Date({ string: "Birthday" });
    state = fields.Selection({
        string: "Status",
        selection: [
            ["draft", "New"],
            ["done", "Confirmed"],
        ],
    });
    secret_code = fields.Char({ string: "Secret Code" });
    notes = fields.Html({ string: "Notes" });
    photo = fields.Binary({ string: "Photo" });
    parent_id = fields.Many2one({ string: "Company", relation: "partner" });
    child_ids = fields.One2many({ string: "Contacts", relation: "partner", relation_field: "parent_id" });

    _records = [
        {
            id: 1,
            name: "Azure Interior",
            email: "azure@example.com",
            age: 12,
            is_company: true,
            birthday: "2020-02-29",
            state: "draft",
            secret_code: "hidden-by-the-view",
            notes: "<p>Pays <b>late</b></p><script>alert(1)</script>",
            photo: "R0lGODlhAQABAAAAACw=",
            child_ids: [2, 3],
        },
        { id: 2, name: "Brandon", email: "brandon@example.com", parent_id: 1, state: "done" },
        { id: 3, name: "Colleen", parent_id: 1, state: "draft" },
    ];
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Partner, ResCompany, ResPartner, ResUsers]);

const FORM = /* xml */ `
    <form>
        <header>
            <button name="action_confirm" type="object" string="Confirm" invisible="state != 'draft'"/>
            <button name="action_reset" type="object" string="Reset" invisible="state == 'draft'"/>
            <field name="state" widget="statusbar"/>
        </header>
        <sheet>
            <div name="button_box">
                <button name="action_open_orders" type="object" class="oe_stat_button" string="Orders"/>
            </div>
            <group>
                <field name="name"/>
                <field name="email" required="is_company"/>
                <field name="age" readonly="1"/>
                <field name="is_company"/>
                <field name="birthday"/>
                <field name="parent_id"/>
                <field name="secret_code" invisible="1"/>
                <field name="photo"/>
            </group>
            <group invisible="not is_company">
                <field name="child_ids">
                    <list>
                        <field name="name"/>
                        <field name="email"/>
                        <button name="action_in_line" type="object" string="In Line"/>
                    </list>
                </field>
            </group>
            <notebook>
                <page string="Details" name="details">
                    <field name="state"/>
                </page>
                <page string="Internal Notes" name="notes">
                    <field name="notes"/>
                </page>
                <page string="Hidden" name="hidden" invisible="1">
                    <field name="secret_code"/>
                </page>
            </notebook>
        </sheet>
    </form>`;

async function start(config = { can_share_screen: true }) {
    patchWithCleanup(session, { nudge: config });
}

function capture() {
    return getService("nudge_context").capture();
}

function byName(entries) {
    return Object.fromEntries(entries.map((entry) => [entry.name, entry]));
}

test("values are turned into plain data", () => {
    expect(formatValue({ type: "char" }, "  a   b  ")).toBe("a b");
    expect(formatValue({ type: "char" }, false)).toBe(null);
    expect(formatValue({ type: "boolean" }, false)).toBe(false);
    expect(formatValue({ type: "integer" }, 0)).toBe(0);
    expect(formatValue({ type: "many2one" }, { id: 4, display_name: "Azure" })).toBe("Azure");
    expect(formatValue({ type: "selection", selection: [["a", "Label A"]] }, "a")).toBe("Label A");
    expect(formatValue({ type: "html" }, "<p>Pays <b>late</b></p>")).toBe("Pays late");
    expect(formatValue({ type: "binary" }, "R0lGOD")).toBe(undefined);
    expect(formatValue({ type: "char" }, "x".repeat(500))).toHaveLength(160);
});

test("a form is described as the person sees it", async () => {
    await start();
    await mountView({ resModel: "partner", type: "form", arch: FORM, resId: 1 });
    const { snapshot, summary } = capture();

    expect(summary).toBe("Azure Interior");
    expect(snapshot.model).toBe("partner");
    expect(snapshot.view_type).toBe("form");
    expect(snapshot.record).toEqual({
        id: 1,
        is_new: false,
        unsaved_changes: false,
        name: "Azure Interior",
    });

    const seen = byName(snapshot.fields);
    expect(seen.name).toEqual({
        name: "name",
        label: "Name",
        type: "char",
        value: "Azure Interior",
        on_screen: true,
        required: true,
    });
    expect(seen.email.required).toBe(true);
    expect(seen.age).toEqual({
        name: "age",
        label: "Age",
        type: "integer",
        value: 12,
        on_screen: true,
        readonly: true,
    });
    expect(seen.is_company.value).toBe(true);
    expect(seen.birthday.value).toBe("2020-02-29");
    expect(seen.parent_id.value).toBe(null);
    expect(seen.state.value).toBe("New");
    expect(seen.child_ids.value).toEqual({
        count: 2,
        rows: [
            { name: "Brandon", email: "brandon@example.com", id: 2 },
            { name: "Colleen", id: 3 },
        ],
    });

    // What the view hides, and what cannot be read as text, is not described
    expect("secret_code" in seen).toBe(false);
    expect("photo" in seen).toBe(false);
    expect(JSON.stringify(snapshot)).not.toInclude("hidden-by-the-view");
    expect(JSON.stringify(snapshot)).not.toInclude("R0lGOD");

    // A tab that is not open: there, but not on screen
    expect(seen.notes).toEqual({
        name: "notes",
        label: "Notes",
        type: "html",
        value: "Pays late",
        on_screen: false,
        tab: "Internal Notes",
    });
    expect(snapshot.tabs).toEqual([
        { name: "details", label: "Details", active: true },
        { name: "notes", label: "Internal Notes" },
    ]);

    const buttons = byName(snapshot.buttons);
    expect(Object.keys(buttons)).toEqual(["action_confirm", "action_open_orders"]);
    expect(buttons.action_confirm).toEqual({
        name: "action_confirm",
        label: "Confirm",
        kind: "object",
        on_screen: true,
    });
    expect(buttons.action_open_orders.smart).toBe(true);
});

test("changes not saved yet are seen", async () => {
    await start();
    await mountView({ resModel: "partner", type: "form", arch: FORM, resId: 1 });
    await click(".o_field_widget[name=email] input");
    await edit("", { confirm: "tab" });
    await click(".o_field_widget[name=is_company] input");
    await animationFrame();
    const { snapshot } = capture();
    const seen = byName(snapshot.fields);

    expect(snapshot.record.unsaved_changes).toBe(true);
    expect(seen.email.value).toBe(null);
    expect(seen.is_company.value).toBe(false);
    expect("required" in seen.email).toBe(false);
    expect("child_ids" in seen).toBe(false);

    await click(".o_notebook_headers .nav-link[name=notes]");
    await animationFrame();
    const tabs = byName(capture().snapshot.tabs);
    expect(tabs.notes.active).toBe(true);
    expect("active" in tabs.details).toBe(false);
    expect(byName(capture().snapshot.fields).notes.on_screen).toBe(true);
});

test("a new record", async () => {
    await start();
    await mountView({ resModel: "partner", type: "form", arch: FORM });
    const { snapshot } = capture();

    expect(snapshot.record).toEqual({ id: null, is_new: true, unsaved_changes: false });
    expect(byName(snapshot.fields).name.value).toBe(null);
});

test("a list is described with its columns and first rows", async () => {
    await start();
    await mountView({
        resModel: "partner",
        type: "list",
        arch: /* xml */ `
            <list>
                <field name="name"/>
                <field name="state"/>
                <field name="email" optional="hide"/>
                <field name="secret_code" column_invisible="1"/>
            </list>`,
    });
    const { snapshot } = capture();

    expect(snapshot.view_type).toBe("list");
    expect(snapshot.total).toBe(3);
    expect(snapshot.columns).toEqual([
        { name: "name", label: "Name" },
        { name: "state", label: "Status" },
    ]);
    expect(snapshot.rows).toEqual([
        { name: "Azure Interior", state: "New", id: 1 },
        { name: "Brandon", state: "Confirmed", id: 2 },
        { name: "Colleen", state: "New", id: 3 },
    ]);
    expect(JSON.stringify(snapshot)).not.toInclude("hidden-by-the-view");

    await click(".o_data_row:eq(1) .o_list_record_selector input");
    await animationFrame();
    expect(capture().snapshot.selected_ids).toEqual([2]);
});

test("a grouped list is described by its groups", async () => {
    await start();
    await mountView({
        resModel: "partner",
        type: "list",
        arch: `<list><field name="name"/></list>`,
        groupBy: ["state"],
    });
    const { snapshot } = capture();

    expect(snapshot.grouped_by).toEqual(["state"]);
    expect(snapshot.groups).toEqual([
        { label: "New", count: 2 },
        { label: "Confirmed", count: 1 },
    ]);
    expect("rows" in snapshot).toBe(false);
});

test("a kanban is described by its cards", async () => {
    await start();
    await mountView({
        resModel: "partner",
        type: "kanban",
        arch: /* xml */ `
            <kanban>
                <templates>
                    <t t-name="card">
                        <field name="name"/>
                        <field name="state"/>
                    </t>
                </templates>
            </kanban>`,
    });
    const { snapshot } = capture();

    expect(snapshot.view_type).toBe("kanban");
    expect(snapshot.total).toBe(3);
    expect(snapshot.cards[0]).toEqual({ name: "Azure Interior", state: "New", id: 1 });
});

test("nothing is described when sharing is not allowed", async () => {
    await start({ can_share_screen: false });
    await mountView({ resModel: "partner", type: "form", arch: FORM, resId: 1 });
    expect(capture()).toBe(null);
});

test("a view that is gone is not described", async () => {
    await start();
    const view = await mountView({ resModel: "partner", type: "form", arch: FORM, resId: 1 });
    expect(capture().snapshot.model).toBe("partner");
    view.__owl__.app.destroy();
    await animationFrame();
    expect("model" in capture().snapshot).toBe(false);
});

test("the summary names what is looked at", () => {
    expect(summarize({ record: { name: "S00042" }, breadcrumbs: ["Quotations", "S00042"] })).toBe(
        "S00042"
    );
    expect(summarize({ breadcrumbs: ["Sales", "Quotations"] })).toBe("Quotations");
    expect(summarize({ action: { name: "Settings" } })).toBe("Settings");
    expect(summarize({ record: { name: "S00042" }, dialog_title: "Send by email" })).toBe(
        "Send by email · S00042"
    );
    expect(summarize({})).toBe("");
});

test("shortcuts are printed the way keyboards name them", () => {
    expect(hotkeyLabel("control+shift+space")).toBe("Ctrl + Shift + Space");
    expect(hotkeyLabel("alt+control+n")).toBe("Ctrl + Alt + N");
    expect(hotkeyLabel("none")).toBe("");
    expect(hotkeyLabel("")).toBe("");
});

test("the shortcut calls Nudge, which looks at the screen", async () => {
    onRpc("res.users.settings", "set_res_users_settings", () => ({}));
    await start({ can_share_screen: true, is_configured: true });
    await mountView({ resModel: "partner", type: "form", arch: FORM, resId: 1 });
    const nudge = getService("nudge");
    expect(nudge.state.isOpen).toBe(false);

    // From inside a field being edited
    await click(".o_field_widget[name=name] input");
    await press(["control", "shift", "space"]);
    await animationFrame();
    expect(nudge.state.isOpen).toBe(true);
    expect(nudge.state.screenSummary).toBe("Azure Interior");

    nudge.close();
    await nudge.setHotkey("alt+control+n");
    await press(["control", "shift", "space"]);
    await animationFrame();
    expect(nudge.state.isOpen).toBe(false);
    await press(["alt", "control", "n"]);
    await animationFrame();
    expect(nudge.state.isOpen).toBe(true);

    nudge.close();
    await nudge.setHotkey("none");
    await press(["alt", "control", "n"]);
    await animationFrame();
    expect(nudge.state.isOpen).toBe(false);
});

test("the Import screen is described: file, columns and errors", () => {
    getFixture().innerHTML = `
        <div class="o_import_action">
            <div class="o_import_data_sidepanel">
                <div class="fst-italic truncate">contacts</div><span class="fst-italic">.xlsx</span>
                <div class="o_import_report alert alert-danger">No matching record found for name 'Nowhere' in field Country</div>
            </div>
            <div class="o_import_grid_col"><div class="fw-bold">Email</div><select><option selected="">Email</option></select></div>
            <div class="o_import_grid_col"><div class="fw-bold">Zzz</div><select><option selected="">To import, select a field…</option></select></div>
        </div>`;
    const snapshot = takeSnapshot({
        action: {
            currentController: {
                displayName: "Import a File",
                action: { type: "ir.actions.client", tag: "import", params: { active_model: "res.partner" } },
            },
        },
        views: [],
    });
    expect(snapshot.import).toEqual({
        model: "res.partner",
        file: "contacts.xlsx",
        columns: [
            { header: "Email", field: "Email" },
            { header: "Zzz", field: "To import, select a field…" },
        ],
        errors: ["No matching record found for name 'Nowhere' in field Country"],
    });
    expect(snapshot.model).toBe("res.partner");
    expect(summarize(snapshot)).toBe("Import: contacts.xlsx");
});

test("Studio is seen in the snapshot, with the view edited and the tab open", () => {
    getFixture().innerHTML = `
        <div class="o_web_studio_editor_manager">
            <div class="o_web_studio_sidebar">
                <div class="o_web_studio_new">Add</div>
                <div class="o_web_studio_properties active">Properties</div>
            </div>
            <div class="o_web_studio_view_renderer o_web_studio_form_view_editor"></div>
        </div>`;
    const snapshot = takeSnapshot({
        action: { currentController: { displayName: "Studio", action: { type: "ir.actions.client", tag: "studio" } } },
        views: [],
    });
    expect(snapshot.studio).toEqual({ editing: true, view_type: "form", tab: "properties" });
    expect(summarize(snapshot)).toBe("Studio: form view");
    getFixture().innerHTML = "";
    expect(takeSnapshot({ action: { currentController: null }, views: [] }).studio).toBe(undefined);
});

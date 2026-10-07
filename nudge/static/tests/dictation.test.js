import { expect, getFixture, test } from "@odoo/hoot";
import { click, keyDown, keyUp, press, queryOne } from "@odoo/hoot-dom";
import { advanceTime, animationFrame, tick } from "@odoo/hoot-mock";

import {
    defineModels,
    fields,
    getService,
    models,
    mountView,
    onRpc,
    webModels,
} from "@web/../tests/web_test_helpers";

import { writeInField } from "@nudge/context/form_writer";
import { commit, insertText, isEditable, spaced } from "@nudge/voice/dictation";

import { answered, FakeRecognition, FakeRecorder, setupVoice } from "./voice_helpers";

class Partner extends models.Model {
    _name = "partner";

    name = fields.Char({ string: "Name" });
    email = fields.Char({ string: "Email" });
    notes = fields.Text({ string: "Remarks" });
    code = fields.Char({ string: "Code" });
    hidden = fields.Char({ string: "Hidden" });
    age = fields.Integer({ string: "Age" });
    birthday = fields.Date({ string: "Birthday" });
    state = fields.Selection({
        string: "Status",
        selection: [
            ["draft", "New"],
            ["done", "Confirmed"],
        ],
    });

    _records = [
        { id: 1, name: "Azure Interior", notes: "Pays on time.", code: "A1", state: "draft", age: 7 },
    ];
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Partner, ResCompany, ResPartner, ResUsers]);

const FORM = /* xml */ `
    <form>
        <sheet>
            <group>
                <field name="name"/>
                <field name="email"/>
                <field name="code" readonly="state == 'draft'"/>
                <field name="hidden" invisible="1"/>
                <field name="age"/>
                <field name="birthday"/>
                <field name="state"/>
                <field name="notes"/>
            </group>
        </sheet>
    </form>`;

async function openForm(options) {
    const asked = setupVoice(options);
    await mountView({ resModel: "partner", type: "form", arch: FORM, resId: 1 });
    const view = getService("nudge_context").frontView();
    return {
        asked,
        view,
        record: view.controller.model.root,
        nudge: getService("nudge"),
        voice: getService("nudge_voice"),
        guidance: getService("nudge_guidance"),
    };
}

/**
 * @param {string} tag
 * @param {Object} [attributes]
 * @param {Element} [parent]
 * @returns {HTMLElement} an element of the page of the test
 */
function make(tag, attributes = {}, parent = getFixture()) {
    const element = document.createElement(tag);
    for (const [name, value] of Object.entries(attributes)) {
        element.setAttribute(name, value);
    }
    parent.append(element);
    return element;
}

test("text is typed where text can be typed", () => {
    expect(isEditable(make("input", { type: "text" }))).toBe(true);
    expect(isEditable(make("input"))).toBe(true);
    expect(isEditable(make("input", { type: "search" }))).toBe(true);
    expect(isEditable(make("input", { type: "email" }))).toBe(true);
    expect(isEditable(make("textarea"))).toBe(true);
    expect(isEditable(make("div", { contenteditable: "true" }))).toBe(true);

    // Never a password, never what is not text, never what is closed
    expect(isEditable(make("input", { type: "password" }))).toBe(false);
    expect(isEditable(make("input", { type: "number" }))).toBe(false);
    expect(isEditable(make("input", { type: "text", inputmode: "numeric" }))).toBe(false);
    expect(isEditable(make("input", { type: "text", inputmode: "decimal" }))).toBe(false);
    expect(isEditable(make("input", { type: "checkbox" }))).toBe(false);
    expect(isEditable(make("input", { type: "file" }))).toBe(false);
    expect(isEditable(make("input", { type: "text", readonly: "readonly" }))).toBe(false);
    expect(isEditable(make("textarea", { disabled: "disabled" }))).toBe(false);
    expect(isEditable(make("button"))).toBe(false);
    expect(isEditable(make("div"))).toBe(false);
    expect(isEditable(document.body)).toBe(false);
    expect(isEditable(null)).toBe(false);
    // The message box of Nudge is not a field of Odoo
    const own = make("div", { class: "o_nudge" });
    expect(isEditable(make("textarea", {}, own))).toBe(false);

    // Fields of Odoo: those that hold text, not dates, amounts or records to pick
    const allowed = ["char", "text", "html", "email", "phone", "url", "char_emojis", "text_emojis"];
    for (const widget of allowed) {
        const field = make("div", { class: `o_field_widget o_field_empty o_field_${widget}` });
        expect(isEditable(make("input", { type: "text" }, field))).toBe(true, { message: widget });
    }
    const refused = ["date", "datetime", "monetary", "many2one", "many2many_tags", "float_time"];
    for (const widget of refused) {
        const field = make("div", { class: `o_field_widget o_field_invalid o_field_${widget}` });
        expect(isEditable(make("input", { type: "text" }, field))).toBe(false, { message: widget });
    }
});

test("what is said comes after what is written", async () => {
    const input = make("input", { type: "text" });
    const events = [];
    input.addEventListener("input", () => events.push("input"));
    input.addEventListener("change", () => events.push("change"));

    await click(input);
    expect(insertText(input, "Hello")).toBe(true);
    expect(input.value).toBe("Hello");
    expect(insertText(input, "to\nyou")).toBe(true);
    expect(input.value).toBe("Hello to you", { message: "A field of one line holds one line" });
    expect(insertText(input, ", and more")).toBe(true);
    expect(input.value).toBe("Hello to you, and more");
    expect(insertText(input, "؟")).toBe(true);
    expect(input.value).toBe("Hello to you, and more؟");
    expect(events.filter((name) => name === "input")).toHaveLength(4);
    expect(events).not.toInclude("change");

    commit(input);
    expect(events.at(-1)).toBe("change");

    // Where the cursor is
    input.setSelectionRange(5, 5);
    expect(spaced(input, "there")).toBe(" there");
    insertText(input, "there");
    expect(input.value).toBe("Hello there to you, and more؟");

    expect(insertText(input, "")).toBe(false);
    expect(insertText(make("input", { type: "password" }), "secret")).toBe(false);
    const gone = make("input", { type: "text" });
    gone.remove();
    expect(insertText(gone, "Hello")).toBe(false);
});

test("lines are kept where there are lines", async () => {
    const area = make("textarea");
    await click(area);
    insertText(area, "First line");
    insertText(area, "\nSecond line");
    expect(area.value).toBe("First line\nSecond line");

    // A field for an address has no cursor to tell
    const email = make("input", { type: "email" });
    await click(email);
    expect(insertText(email, "someone@example.com")).toBe(true);
    expect(email.value).toBe("someone@example.com");
});

test("a dictation is typed in the field of the form", async () => {
    const { asked, record, voice } = await openForm();
    const input = queryOne(".o_field_widget[name=email] input");

    await click(input);
    await press(["alt", "control", "d"]);
    await animationFrame();
    expect(voice.state.mode).toBe("dictation");
    expect(voice.state.target).toBe(input);
    const recognition = FakeRecognition.last;
    expect(recognition.continuous).toBe(true);
    expect(".o_nudge_dictation").toHaveCount(1);
    expect(".o_nudge_dictation_text").toHaveText("I'm listening…");
    expect(document.activeElement).toBe(input, { message: "The cursor stays in the field" });

    recognition.hear("sales at", false);
    await animationFrame();
    expect(".o_nudge_dictation_text").toHaveText("sales at");
    expect(input).toHaveValue("", { message: "What is not settled is not typed" });

    recognition.hear("sales@example.com");
    await animationFrame();
    expect(input).toHaveValue("sales@example.com");
    expect(".o_nudge_dictation_text").toHaveText("I'm listening…");
    expect(record.data.email || "").toBe("", { message: "Typing is not over" });

    // The same shortcut ends it
    await press(["alt", "control", "d"]);
    await animationFrame();
    expect(recognition.calls).toEqual(["start", "stop"]);
    expect(voice.state.mode).toBe(null);
    expect(".o_nudge_dictation").toHaveCount(0);
    expect(record.data.email).toBe("sales@example.com");
    expect(record.dirty).toBe(true);
    expect(asked.messages).toHaveLength(0, { message: "A dictation is not a question" });
});

test("a dictation is held, or ended from the field", async () => {
    const { record, voice } = await openForm();
    await click(".o_field_widget[name=notes] textarea");

    await keyDown(["alt", "control", "d"]);
    await animationFrame();
    expect(voice.state.mode).toBe("dictation");
    await advanceTime(400);
    FakeRecognition.last.hear("Asked for a delivery on Monday.");
    await keyUp(["alt", "control", "d"]);
    await animationFrame();
    expect(voice.state.mode).toBe(null);
    expect(record.data.notes).toBe("Pays on time. Asked for a delivery on Monday.");

    // Tapped: it lasts until it is ended
    await press(["alt", "control", "d"]);
    await advanceTime(1000);
    await animationFrame();
    expect(voice.state.mode).toBe("dictation");
    FakeRecognition.last.hear("Call back.");
    await animationFrame();
    await click(".o_nudge_dictation_stop");
    await animationFrame();
    expect(voice.state.mode).toBe(null);
    expect(record.data.notes).toBe("Pays on time. Asked for a delivery on Monday. Call back.");
    expect(document.activeElement).toBe(queryOne(".o_field_widget[name=notes] textarea"));
});

test("a dictation needs a field to type in", async () => {
    const { voice } = await openForm();
    await click(".o_form_view .o_form_sheet");
    await press(["alt", "control", "d"]);
    await animationFrame();

    expect(voice.state.mode).toBe(null);
    expect(FakeRecognition.made).toHaveLength(0, { message: "The microphone stays closed" });
    expect(".o_notification").toHaveCount(1);
    expect(".o_notification").toHaveText(/Click in a text field first/);

    // A number, a date are not dictated: heard wrong, they are wrong
    for (const name of ["age", "birthday"]) {
        const input = queryOne(`.o_field_widget[name=${name}] input`);
        expect(voice.dictate(input)).toBe(false, { message: name });
    }
    expect(FakeRecognition.made).toHaveLength(0);
});

test("a dictation in the message box is a message", async () => {
    const { asked, nudge, voice } = await openForm();
    nudge.open();
    await animationFrame();
    await click(".o_nudge_input");

    await press(["alt", "control", "d"]);
    await animationFrame();
    expect(voice.state.mode).toBe("question");
    expect(".o_nudge_dictation").toHaveCount(0);
    FakeRecognition.last.hear("What is this customer?");
    await press(["alt", "control", "d"]);
    await animationFrame();
    await tick();
    expect(asked.messages.map((message) => message.message)).toEqual(["What is this customer?"]);
});

test("the provider types what was dictated once it is said", async () => {
    const { asked, record, voice } = await openForm({
        recognition: false,
        config: { can_transcribe: true },
        transcribe: () => new Response(JSON.stringify({ text: "Asked for a discount." })),
    });
    await click(".o_field_widget[name=notes] textarea");
    await press(["alt", "control", "d"]);
    await animationFrame();
    expect(voice.state.mode).toBe("dictation");
    expect(FakeRecorder.made).toHaveLength(1);

    await click(".o_nudge_dictation_stop");
    await animationFrame();
    await tick();
    await animationFrame();
    await tick();
    expect(asked.recordings).toHaveLength(1);
    expect(JSON.parse(asked.recordings[0].hints)).toInclude("Remarks");
    expect(voice.state.mode).toBe(null);
    expect(record.data.notes).toBe("Pays on time. Asked for a discount.");
});

test("an error ends the dictation and keeps what was typed", async () => {
    const { record, voice } = await openForm();
    await click(".o_field_widget[name=email] input");
    voice.dictate(document.activeElement);
    FakeRecognition.last.hear("kept");
    FakeRecognition.last.fail("network");
    await animationFrame();

    expect(voice.state.mode).toBe(null);
    expect(record.data.email).toBe("kept");
    expect(".o_notification").toHaveText(/could not reach its speech service/);
});

test("a text is written in a field of the form, and taken back", async () => {
    const { view, record } = await openForm();

    let result = await writeInField(view, { field: "email", text: " a@example.com\n" });
    expect(result.error).toBe(undefined);
    expect(record.data.email).toBe("a@example.com");
    expect(record.dirty).toBe(true);
    await animationFrame();
    expect(".o_field_widget[name=email] input").toHaveValue("a@example.com");
    await result.undo();
    expect(record.data.email).toBe(false);

    // After what the field holds, unless told to replace it
    result = await writeInField(view, { field: "notes", text: "Asked for\na discount.", mode: "append" });
    expect(record.data.notes).toBe("Pays on time.\nAsked for\na discount.");
    await result.undo();
    expect(record.data.notes).toBe("Pays on time.");
    await writeInField(view, { field: "name", text: "Interior", mode: "append" });
    expect(record.data.name).toBe("Azure Interior Interior");
    await writeInField(view, { field: "name", text: "New\nname", mode: "replace" });
    expect(record.data.name).toBe("New name");
});

test("nothing is written where the person could not type", async () => {
    const { view, record } = await openForm();
    const before = { ...record.data };
    const cases = {
        nowhere: "no_field",
        state: "no_field",
        age: "no_field",
        code: "readonly",
        hidden: "readonly",
        constructor: "no_field",
        __proto__: "no_field",
        "email], body *": "no_field",
    };
    for (const [field, error] of Object.entries(cases)) {
        const result = await writeInField(view, { field, text: "Hello" });
        expect(result.error).toBe(error, { message: field });
        expect(result.undo).toBe(undefined);
    }
    for (const text of ["", "   ", null, 7, ["Hello"]]) {
        expect((await writeInField(view, { field: "email", text })).error).toBe("no_form");
    }
    expect((await writeInField(null, { field: "email", text: "Hello" })).error).toBe("no_form");
    expect((await writeInField({ type: "list" }, { field: "email", text: "Hello" })).error).toBe(
        "no_form"
    );
    expect(record.data.email).toBe(before.email);
    expect(record.data.code).toBe("A1");
    expect(record.dirty).toBe(false);
});

test("an answer writes in the form, the person reads and decides", async () => {
    onRpc("web_save", () => {
        expect.step("saved");
    });
    const { nudge, record, guidance } = await openForm({
        answer: answered(
            { type: "start", conversation_id: 4, title: "Write" },
            {
                type: "write",
                field: "notes",
                label: "Remarks",
                text: "The customer called.",
                mode: "append",
            }
        ),
    });
    nudge.open();
    await nudge.send("Write in the remarks that the customer called");
    await animationFrame();

    expect(record.data.notes).toBe("Pays on time.\nThe customer called.");
    expect(record.dirty).toBe(true);
    expect.verifySteps([]);
    expect(nudge.state.isOpen).toBe(false, { message: "The panel gets out of the way" });
    expect(guidance.state.element).toBe(queryOne(".o_field_widget[name=notes]"));
    expect(".o_nudge_guide_text").toHaveText("I wrote here what you asked. Read it, then save.");
    expect(".o_nudge_guide_undo").toHaveCount(1);
    expect(nudge.state.messages[1].content).toBe(
        "Written in “Remarks”:\n\n> The customer called."
    );
    expect(nudge.state.messages[1].guidance).toEqual([]);

    await click(".o_nudge_guide_undo");
    await animationFrame();
    expect(record.data.notes).toBe("Pays on time.");
    expect(guidance.state.isActive).toBe(false);
    expect.verifySteps([]);
});

test("what cannot be written is given to read", async () => {
    const { nudge, record, guidance } = await openForm({
        answer: answered({
            type: "write",
            field: "code",
            label: "Code",
            text: "B2\nsecond line",
            mode: "replace",
        }),
    });
    await nudge.send("Change the code");

    expect(record.data.code).toBe("A1");
    expect(guidance.state.isActive).toBe(false);
    expect(nudge.state.messages[1].content).toBe(
        "I could not write in “Code”. Here is the text:\n\n> B2\n> second line"
    );
    expect(nudge.state.messages[1].state).toBe("complete");
});

test("a conversation read again tells what was written", async () => {
    onRpc("nudge.conversation", "read", () => [{ id: 3, name: "Write" }]);
    onRpc("nudge.message", "search_read", () => [
        { id: 1, role: "user", content: "Write it", state: "complete", is_spoken: true },
        {
            id: 2,
            role: "assistant",
            content: "Done.",
            state: "complete",
            error_message: false,
            screen_events: [
                { type: "write", field: "notes", label: "Remarks", text: "Called.", mode: "append" },
            ],
        },
    ]);
    const { nudge, record, guidance } = await openForm();
    await nudge.openConversation(3);

    expect(nudge.state.messages[0].spoken).toBe(true);
    expect(nudge.state.messages[1].content).toBe("Done.\n\nWritten in “Remarks”:\n\n> Called.");
    expect(nudge.state.messages[1].guidance).toEqual([]);
    expect(record.data.notes).toBe("Pays on time.", { message: "Reading is not writing again" });
    expect(guidance.state.isActive).toBe(false);
});

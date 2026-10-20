import { beforeEach, expect, test } from "@odoo/hoot";
import { click, edit, queryAllTexts, queryOne } from "@odoo/hoot-dom";
import { advanceTime, animationFrame } from "@odoo/hoot-mock";

import { session } from "@web/session";
import {
    defineModels,
    fields,
    models,
    mountView,
    onRpc,
    patchWithCleanup,
    webModels,
} from "@web/../tests/web_test_helpers";

import { forgetSpecs } from "@nudge/validator/validator_strip";
import { collectPayload, plainValue } from "@nudge/validator/validator_payload";

class Partner extends models.Model {
    _name = "partner";

    name = fields.Char({ string: "Name" });
    email = fields.Char({ string: "Email" });
    birthday = fields.Date({ string: "Birthday" });
    parent_id = fields.Many2one({ string: "Company", relation: "partner" });

    _records = [{ id: 1, name: "Raghad Trading" }];
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Partner, ResCompany, ResPartner, ResUsers]);

const FORM = `<form><field name="name"/><field name="email"/></form>`;
const WARNING = {
    key: "partner_duplicate.email",
    check: "partner_duplicate",
    label: "Duplicate contact",
    severity: "warning",
    message: "Another contact already uses the email raghad@example.com: Raghad Trading.",
    field: "email",
    model: "partner",
    ids: [1],
    figure: 0,
};

beforeEach(() => {
    forgetSpecs();
    patchWithCleanup(session, { nudge: { is_configured: true } });
});

function serve({ checks = ["partner_duplicate"], warnings = [WARNING], onCheck } = {}) {
    onRpc("nudge.validator", "get_spec", () => {
        expect.step("spec");
        return { checks, fields: ["name", "email", "parent_id"], lines: {} };
    });
    onRpc("nudge.validator", "check", ({ args }) => {
        expect.step("check");
        onCheck?.(args[0]);
        return { warnings };
    });
}

async function typeEmail(value = "raghad@example.com") {
    await click(".o_field_widget[name=email] input");
    await edit(value, { confirm: "tab" });
    await animationFrame();
}

test("the strip shows under the form once the person stopped typing, 600 ms later, and only one call is made", async () => {
    let sent = null;
    serve({ onCheck: (payload) => (sent = payload) });
    await mountView({ resModel: "partner", type: "form", arch: FORM });
    await typeEmail("raghad@example.com");
    await advanceTime(599);
    expect(".o_nudge_strip").toHaveCount(0);
    await advanceTime(1);
    await animationFrame();
    expect(".o_nudge_strip").toHaveCount(1);
    expect(queryAllTexts(".o_nudge_strip_label")).toEqual(["Duplicate contact"]);
    expect(queryOne(".o_nudge_strip_message").textContent).toInclude("raghad@example.com");
    // The values not saved yet are what was sent
    expect(sent.model).toBe("partner");
    expect(sent.is_new).toBe(true);
    expect(sent.fields.email).toBe("raghad@example.com");
    // Typing again within the delay makes a single new call
    await typeEmail("raghad@example.org");
    await advanceTime(300);
    await typeEmail("raghad@example.net");
    await advanceTime(600);
    await animationFrame();
    expect.verifySteps(["spec", "check", "check"]);
});

test("a form no check looks at asks nothing more", async () => {
    serve({ checks: [] });
    await mountView({ resModel: "partner", type: "form", arch: FORM });
    await typeEmail();
    await advanceTime(600);
    await animationFrame();
    expect(".o_nudge_strip").toHaveCount(0);
    expect.verifySteps(["spec"]);
});

test("the strip never blocks: the form can still be saved", async () => {
    serve();
    await mountView({ resModel: "partner", type: "form", arch: FORM });
    await typeEmail();
    await advanceTime(600);
    await animationFrame();
    expect(".o_nudge_strip").toHaveCount(1);
    expect(".o_form_button_save").toHaveCount(1);
    expect(".o_form_button_save").not.toHaveAttribute("disabled");
    expect.verifySteps(["spec", "check"]);
});

test("a person mutes a warning for themselves and brings it back", async () => {
    serve();
    onRpc("nudge.validator", "mute", ({ args }) => {
        expect.step(`mute ${args[0]} ${args[1]}`);
        return args[1] ? [args[0]] : [];
    });
    await mountView({ resModel: "partner", type: "form", arch: FORM });
    await typeEmail();
    await advanceTime(600);
    await animationFrame();
    await click(".o_nudge_strip_mute");
    await animationFrame();
    expect(".o_nudge_strip_item").toHaveCount(0);
    expect(".o_nudge_strip_muted").toHaveCount(1);
    expect(queryOne(".o_nudge_strip_muted").textContent).toInclude("Duplicate contact");
    await click(".o_nudge_strip_unmute");
    await animationFrame();
    expect(".o_nudge_strip_muted").toHaveCount(0);
    expect.verifySteps([
        "spec",
        "check",
        "mute partner_duplicate true",
        "mute partner_duplicate false",
        "spec",
        "check",
    ]);
});

test("the AI is asked only when the person clicks, with the form as it is", async () => {
    serve();
    onRpc("nudge.validator", "ask", ({ args }) => {
        expect.step(`ask ${args[1]} ${args[0].fields.email}`);
        return { text: "It is probably the same company." };
    });
    await mountView({ resModel: "partner", type: "form", arch: FORM });
    await typeEmail();
    await advanceTime(600);
    await animationFrame();
    expect(".o_nudge_strip_answer").toHaveCount(0);
    await click(".o_nudge_strip_ask");
    await animationFrame();
    expect(queryOne(".o_nudge_strip_answer").textContent).toInclude("probably the same company");
    expect.verifySteps(["spec", "check", "ask partner_duplicate raghad@example.com"]);
});

test("when no AI can answer the click says so", async () => {
    serve();
    onRpc("nudge.validator", "ask", () => ({ text: "" }));
    await mountView({ resModel: "partner", type: "form", arch: FORM });
    await typeEmail();
    await advanceTime(600);
    await animationFrame();
    await click(".o_nudge_strip_ask");
    await animationFrame();
    expect(queryOne(".o_nudge_strip_answer").textContent).toInclude("Nudge cannot answer now");
    expect.verifySteps(["spec", "check"]);
});

test("a person without Nudge gets no strip and no call", async () => {
    patchWithCleanup(session, { nudge: undefined });
    serve();
    await mountView({ resModel: "partner", type: "form", arch: FORM });
    await typeEmail();
    await advanceTime(600);
    await animationFrame();
    expect(".o_nudge_strip").toHaveCount(0);
    expect.verifySteps([]);
});

test("values become plain data: numbers as numbers, linked records with their id", () => {
    expect(plainValue({ type: "float" }, 12.5)).toBe(12.5);
    expect(plainValue({ type: "many2one" }, { id: 4, display_name: "Raghad" })).toEqual({ id: 4, name: "Raghad" });
    expect(plainValue({ type: "many2one" }, [4, "Raghad"])).toEqual({ id: 4, name: "Raghad" });
    expect(plainValue({ type: "date" }, { toISODate: () => "2026-10-17" })).toBe("2026-10-17");
    expect(plainValue({ type: "char" }, false)).toBe(null);
    expect(plainValue({ type: "boolean" }, false)).toBe(false);
    expect(plainValue({ type: "binary" }, "abc")).toBe(null);
    const record = {
        resModel: "partner",
        resId: 7,
        isNew: false,
        dirty: true,
        fields: { name: { type: "char" } },
        activeFields: { name: {}, order_line: {} },
        data: {
            name: "Raghad",
            order_line: {
                records: [
                    {
                        fields: { price_unit: { type: "float" } },
                        activeFields: { price_unit: {} },
                        data: { price_unit: 3 },
                    },
                ],
            },
        },
    };
    expect(collectPayload(record, { fields: ["name", "missing"], lines: { order_line: ["price_unit", "other"] } })).toEqual({
        model: "partner",
        id: 7,
        is_new: false,
        changed: true,
        fields: { name: "Raghad" },
        lines: { order_line: [{ price_unit: 3 }] },
    });
});

import { expect, test } from "@odoo/hoot";
import { click, edit, press, queryAllTexts } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";

import { defineModels, fields, getService, models, mountView, onRpc, webModels } from "@web/../tests/web_test_helpers";

import { setupNudge, streamed } from "./voice_helpers";

class Partner extends models.Model {
    _name = "partner";

    name = fields.Char({ string: "Name" });

    _records = [{ id: 1, name: "Raghad Trading" }];
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Partner, ResCompany, ResPartner, ResUsers]);

function step(n, title, extra = {}) {
    return {
        n,
        title,
        state: "todo",
        records: [],
        lines: [],
        sensitive: false,
        exact: true,
        refs: [],
        removed_with: 0,
        error: "",
        verify: false,
        step_id: false,
        undoable: false,
        can_undo: false,
        undone: false,
        editable: [],
        ...extra,
    };
}

const PLAN = {
    step_id: 70,
    kind: "plan",
    state: "pending",
    title: "A plan of 3 steps",
    records: [],
    lines: [],
    sensitive: false,
    error: "",
    plan: false,
    plan_phase: "proposed",
    one_yes: false,
    current: 0,
    done_count: 0,
    failed_count: 0,
    can_undo: false,
    undone: false,
    verify: false,
    plan_steps: [
        step(1, "Create: Contact", {
            lines: [{ field: "name", label: "Name", value: "Raghad Trading" }],
            editable: ["name"],
        }),
        step(2, "Create: Sales Order", {
            refs: [1],
            lines: [{ field: "partner_id", label: "Customer", value: "the record step 1 makes", ref: 1 }],
        }),
        step(3, "Press “Send by Email”: Sales Order", { exact: false, refs: [2] }),
    ],
};

function withSteps(changes, card = {}) {
    return {
        ...PLAN,
        ...card,
        plan_steps: PLAN.plan_steps.map((each) => ({ ...each, ...(changes[each.n] || {}) })),
    };
}

const WAITING = streamed(
    { type: "start", conversation_id: 9, message_id: 1, title: "Raghad" },
    { type: "confirm", ...PLAN },
    { type: "waiting", message_id: 2, state: "waiting", step_id: 70, message: "" }
);

async function start(params = {}) {
    const asked = setupNudge({ answer: WAITING, ...params, config: { can_use_agent: true } });
    await mountView({ resModel: "partner", type: "form", arch: "<form><field name='name'/></form>", resId: 1 });
    const nudge = getService("nudge");
    nudge.open();
    await nudge.send("Create Raghad Trading, a quotation for it, and send it");
    await animationFrame();
    return { asked, nudge };
}

test("several changes: one card, numbered, nothing done before the yes", async () => {
    await start();
    expect(".o_nudge_plan_card .o_nudge_card_title").toHaveText("A plan of 3 steps");
    expect(queryAllTexts(".o_nudge_plan_step_title")).toEqual([
        "Create: Contact",
        "Create: Sales Order",
        "Press “Send by Email”: Sales Order",
    ]);
    expect(queryAllTexts(".o_nudge_plan_step_number")).toEqual(["1", "2", "3"]);
    expect(".o_nudge_plan_ref").toHaveText("the record step 1 makes");
    expect(".o_nudge_plan_uses").toHaveCount(2);
    expect(".o_nudge_plan_card .o_nudge_confirm").toHaveText("Yes, go through the plan");
    expect(".o_nudge_plan_remove").toHaveCount(3);
    expect(".o_nudge_plan_edit").toHaveCount(1, { message: "A value of its own, never a record it points at" });
});

test("a step taken out, a value corrected", async () => {
    onRpc("nudge.step", "plan_remove_step", ({ args }) => {
        expect.step(`remove ${args[1]}`);
        return withSteps({ 2: { state: "removed" }, 3: { state: "removed", removed_with: 2 } });
    });
    onRpc("nudge.step", "plan_edit_step", ({ args }) => {
        expect.step(`edit ${args.slice(1).join(" ")}`);
        return withSteps({
            1: { lines: [{ field: "name", label: "Name", value: "Raghad Corner" }] },
            2: { state: "removed" },
            3: { state: "removed", removed_with: 2 },
        });
    });
    await start();
    await click(".o_nudge_plan_step:nth-child(2) .o_nudge_plan_remove");
    await animationFrame();
    expect.verifySteps(["remove 2"]);
    expect(queryAllTexts(".o_nudge_plan_step[data-state='removed'] .o_nudge_plan_note")).toEqual([
        "Taken out: it will not be done.",
        "Taken out with step 2: it uses what that step makes.",
    ]);
    await click(".o_nudge_plan_edit");
    await animationFrame();
    await edit("Raghad Corner", { target: ".o_nudge_plan_input" });
    await press("Enter");
    await animationFrame();
    expect.verifySteps(["edit 1 name Raghad Corner"]);
    expect(".o_nudge_plan_input").toHaveCount(0);
    expect(".o_nudge_plan_step:nth-child(1) .o_nudge_card_value").toHaveText("Raghad Corner");
});

test("after the yes each change shows its own card, and the plan can be stopped", async () => {
    const answers = [
        streamed(
            { type: "start", conversation_id: 9, title: "Raghad" },
            {
                type: "decided",
                ...withSteps({ 1: { state: "waiting", step_id: 71 } }, { plan_phase: "running", current: 1 }),
            },
            { type: "done", message_id: 2, state: "complete", message: "" }
        ),
        streamed(
            { type: "start", conversation_id: 9, title: "Raghad" },
            {
                type: "decided",
                ...withSteps({ 1: { state: "done", step_id: 71, undoable: true, can_undo: true } }, {
                    plan_phase: "stopped",
                    done_count: 1,
                    can_undo: true,
                }),
            },
            { type: "done", message_id: 2, state: "complete", message: "" }
        ),
    ];
    const { asked, nudge } = await start({ decide: () => answers.shift()() });
    await click(".o_nudge_plan_card > .o_nudge_card_actions .o_nudge_confirm");
    await animationFrame();
    await animationFrame();
    expect([asked.decisions[0].step_id, asked.decisions[0].decision]).toEqual(["70", "confirm"]);
    expect(nudge.state.messages.filter((message) => message.role === "assistant")).toHaveLength(1, {
        message: "No second bubble: the card says it",
    });
    expect(".o_nudge_plan_step[aria-current='step'] .o_nudge_confirm").toHaveText("Yes, do it");
    expect(".o_nudge_plan_stop").toHaveText("Stop the plan");
    await click(".o_nudge_plan_stop");
    await animationFrame();
    await animationFrame();
    expect(asked.decisions[1].decision).toBe("stop");
    expect(".o_nudge_plan_resume").toHaveText("Resume the plan");
    expect(".o_nudge_plan_card .o_nudge_card_outcome").toHaveText(
        "Stopped: 1 of 3 steps done. What is done stays; resume to go on."
    );
    expect(".o_nudge_plan_card .o_nudge_undo").toHaveCount(1);
});

test("a change that did not end as asked is never told as done", async () => {
    const verify = {
        ok: false,
        checked: 2,
        fields: [{ id: 5, model: "res.partner", record: "Ebrahim", field: "city", label: "City", asked: "Irbid", actual: "Nablus" }],
    };
    await start({
        answer: streamed(
            { type: "start", conversation_id: 9, message_id: 1, title: "Raghad" },
            {
                type: "confirm",
                ...withSteps({ 1: { state: "done", verify } }, { state: "done", plan_phase: "done", done_count: 3 }),
            },
            { type: "done", message_id: 2, state: "complete", message: "" }
        ),
    });
    expect(".o_nudge_plan_card .o_nudge_card_outcome").toHaveText(
        "Done, but not everything is as asked: look at the steps marked."
    );
    expect(".o_nudge_verify li strong").toHaveText("City");
    expect(".o_nudge_verify_asked").toHaveText("Irbid");
    expect(".o_nudge_verify_actual").toHaveText("Nablus");
    await click(".o_nudge_verify_fix");
    await animationFrame();
    expect(getService("nudge").state.draft).toBe("Set City of “Ebrahim” to Irbid again.");
});

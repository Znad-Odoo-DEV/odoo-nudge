import { expect, test } from "@odoo/hoot";
import { click, queryAllTexts } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";

import { makeMockEnv, mountWithCleanup } from "@web/../tests/web_test_helpers";

import { NudgeMessage } from "@nudge/components/nudge_message";

const CHANGES = {
    model: "project.task",
    id: 3,
    name: "Fit the doors",
    can_write: true,
    note: "",
    items: [
        {
            change_id: 12, message_id: 7, date: "2026-10-16 09:00:00", who: "Raghad", field: "name",
            label: "Title", old: "Fit the doors", new: "Fit the windows", restorable: true, reason: "",
        },
        {
            change_id: 13, message_id: 7, date: "2026-10-16 09:00:00", who: "Raghad", field: "stage_id",
            label: "Stage", old: "Todo", new: "Doing", restorable: false,
            reason: "A state or a stage is never put back by a click: use the buttons of the record.",
        },
    ],
};

async function show(changes, onRestore) {
    await makeMockEnv();
    await mountWithCleanup(NudgeMessage, {
        props: { role: "assistant", content: "Here are the changes.", state: "complete", changes, onRestore },
    });
    await animationFrame();
}

test("each change shows who, the old and the new value, newest first", async () => {
    await show(CHANGES, () => {});
    expect(queryAllTexts(".o_nudge_change_who")).toEqual(["Raghad", "Raghad"]);
    expect(queryAllTexts(".o_nudge_change_old")).toEqual(["Fit the doors", "Todo"]);
    expect(queryAllTexts(".o_nudge_change_new")).toEqual(["Fit the windows", "Doing"]);
});

test("a change that can be put back has its buttons, a state says why not", async () => {
    await show(CHANGES, () => {});
    expect(".o_nudge_change_restore").toHaveCount(1);
    expect(".o_nudge_change_restore_all").toHaveCount(1);
    expect(".o_nudge_change_why").toHaveCount(1);
    expect(".o_nudge_change_why").toHaveText(/never put back/);
});

test("the buttons ask for one change, or for the whole message of the chatter", async () => {
    const asked = [];
    await show(CHANGES, (spec) => asked.push(spec));
    await click(".o_nudge_change_restore");
    await click(".o_nudge_change_restore_all");
    expect(asked).toEqual([
        { kind: "change", id: 12 },
        { kind: "message", id: 7 },
    ]);
});

test("a person who may not write has no button", async () => {
    await show({ ...CHANGES, can_write: false }, () => {});
    expect(".o_nudge_change_restore").toHaveCount(0);
    expect(".o_nudge_change_restore_all").toHaveCount(0);
});

import { expect, test } from "@odoo/hoot";
import { click, queryAllTexts, queryAllValues, select } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";

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
import { browser } from "@web/core/browser/browser";

import { setupNudge } from "./voice_helpers";

class Partner extends models.Model {
    _name = "partner";

    name = fields.Char({ string: "Name" });

    _records = [{ id: 1, name: "Raghad" }];
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Partner, ResCompany, ResPartner, ResUsers]);

const COMMANDS = [
    { name: "import", words: ["/import", "/استيراد"], kind: "direct", hint: "Match the columns of a file", example: "/import" },
];
const FIELDS = [
    { name: "name", label: "Name", type: "char" },
    { name: "email", label: "Email", type: "char" },
    { name: "comment", label: "Notes", type: "text" },
];
const CARD = {
    model: "res.partner", model_label: "Contact", file: "people.csv", rows: 2, missing: [], fields: FIELDS,
    columns: [
        { index: 0, column: "Customer Name", field: "name", label: "Name", confidence: 92, level: "high", reason: "a usual name", type: "char", sample: ["Raghad"] },
        { index: 1, column: "Mail", field: "email", label: "Email", confidence: 70, level: "medium", reason: "close", type: "char", sample: ["r@nudge.example"] },
        { index: 2, column: "Zzz", field: false, label: "", confidence: 0, level: "none", reason: "", type: "", sample: ["a"] },
    ],
};

async function start(config) {
    const asked = setupNudge({ config: { commands: COMMANDS, ...config } });
    const posted = [];
    patchWithCleanup(browser, {
        fetch(url, options) {
            if (url !== "/nudge/import/file") {
                return super.fetch(...arguments);
            }
            const body = Object.fromEntries(options.body.entries());
            posted.push(body);
            // As the server does: what the person chose is over what was matched
            const columns = CARD.columns.map((column) => ({ ...column }));
            for (const { index, field } of JSON.parse(body.mapping || "[]")) {
                columns[index].field = field || false;
            }
            const answer = { ...CARD, columns };
            if (body.step === "check") {
                answer.check = { problems: ["- error: row 3, column 'Zzz': bad"], errors: 1, records: 0, summary: "" };
            }
            return Promise.resolve(new Response(JSON.stringify(answer), { status: 200 }));
        },
    });
    await mountView({ resModel: "partner", type: "form", arch: "<form><field name='name'/></form>", resId: 1 });
    const nudge = getService("nudge");
    nudge.open();
    await animationFrame();
    return { asked, nudge, posted };
}

test("with private mode on, a spreadsheet is matched without the AI, changed and tried", async () => {
    const { asked, nudge, posted } = await start({ is_private: true, is_configured: true });
    expect(nudge.setAttachment(new File(["Customer Name,Mail,Zzz\n"], "people.csv", { type: "text/csv" }))).toBe("");
    await nudge.send("Please check this file");
    await animationFrame();
    expect(asked.messages).toHaveLength(0);
    expect(posted).toHaveLength(1);
    expect(posted[0].step).toBe("match");
    expect(".o_nudge_import_model").toHaveText("Contact");
    expect(".o_nudge_import_row").toHaveCount(3);
    expect(queryAllTexts(".o_nudge_import_row .badge")).toEqual(["92%", "70%", "None"]);
    // The person says what the last column is
    await select("comment", { target: ".o_nudge_import_row:last .o_nudge_import_field" });
    await animationFrame();
    expect(".o_nudge_import_row:last .badge").toHaveText("100%");
    await click(".o_nudge_import_check");
    await animationFrame();
    expect(posted).toHaveLength(2);
    expect(posted[1].step).toBe("check");
    expect(JSON.parse(posted[1].mapping)).toEqual([{ index: 2, field: "comment" }]);
    expect(".o_nudge_import_result li").toHaveText(/row 3, column 'Zzz'/);
    expect(".o_nudge_import_result").toHaveText(/Nothing was saved\./);
    expect(queryAllValues(".o_nudge_import_field")).toEqual(["name", "email", "comment"]);
});

test("/import and 'without AI' take the no-AI path even with an AI ready; a plain question does not", async () => {
    const { asked, nudge, posted } = await start({ is_configured: true });
    nudge.setAttachment(new File(["a,b\n"], "people.csv", { type: "text/csv" }));
    await nudge.send("/import");
    await animationFrame();
    expect(posted).toHaveLength(1);
    expect(asked.messages).toHaveLength(0);
    nudge.setAttachment(new File(["a,b\n"], "people.csv", { type: "text/csv" }));
    await nudge.send("check this file بدون ذكاء");
    await animationFrame();
    expect(posted).toHaveLength(2);
    nudge.setAttachment(new File(["a,b\n"], "people.csv", { type: "text/csv" }));
    await nudge.send("what do these columns mean?");
    await animationFrame();
    expect(posted).toHaveLength(2);
    expect(asked.messages).toHaveLength(1);
});

test("with no provider, a button in the panel checks a file without AI", async () => {
    onRpc("nudge.conversation", "get_status", () => ({ is_manager: false, is_configured: false }));
    const { posted } = await start({ is_configured: false });
    expect(".o_nudge_noai_import").toHaveCount(1);
    const input = document.querySelector(".o_nudge_import_entry input[type=file]");
    const transfer = new DataTransfer();
    transfer.items.add(new File(["Customer Name,Mail,Zzz\n"], "people.csv", { type: "text/csv" }));
    input.files = transfer.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
    await animationFrame();
    expect(posted).toHaveLength(1);
    expect(".o_nudge_import_row").toHaveCount(3);
});

import { expect, test } from "@odoo/hoot";
import { queryAllTexts } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";

import {
    defineModels,
    fields,
    getService,
    models,
    mountView,
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

const FORM = /* xml */ `<form><field name="name"/></form>`;
async function start(params = {}) {
    const asked = setupNudge({ ...params, config: { is_configured: true, ...params.config } });
    const imported = [];
    patchWithCleanup(browser, {
        fetch(url, options) {
            if (url !== "/nudge/import/file") {
                return super.fetch(...arguments);
            }
            imported.push(Object.fromEntries(options.body.entries()));
            return Promise.resolve(
                new Response(
                    JSON.stringify({
                        model: "res.partner", model_label: "Contact", file: "people.csv", rows: 1,
                        missing: [], fields: [{ name: "name", label: "Name", type: "char" }],
                        columns: [{ index: 0, column: "Customer Name", field: "name", label: "Name",
                            confidence: 92, level: "high", reason: "a usual name", type: "char",
                            sample: ["Raghad"] }],
                    }),
                    { status: 200 }
                )
            );
        },
    });
    await mountView({ resModel: "partner", type: "form", arch: FORM, resId: 1 });
    const nudge = getService("nudge");
    nudge.open();
    await animationFrame();
    return { asked, imported, nudge };
}

const send = async (nudge, text) => {
    nudge.setAttachment(new File(["Customer Name\nRaghad\n"], "people.csv", { type: "text/csv" }));
    await nudge.send(text);
    await animationFrame();
};

test("the lock tells the person that nothing leaves the server, and only when private mode is on", async () => {
    await start({ config: { is_private: true } });
    expect(".o_nudge_private").toHaveCount(1);
    expect(".o_nudge_private").toHaveAttribute("aria-label", /Private mode: nothing is sent to an AI provider/);
    expect(".o_nudge_private").toHaveClass("fa-lock");
});

test("in private mode a file question never reaches the provider", async () => {
    const { asked, imported, nudge } = await start({ config: { is_private: true } });
    // A plain question, with an AI configured: it would go to the AI if the mode were not private
    await send(nudge, "what do these columns mean?");
    expect(asked.messages).toHaveLength(0);
    expect(imported).toHaveLength(1);
    expect(".o_nudge_import_model").toHaveText("Contact");
    expect(queryAllTexts(".o_nudge_import_row .badge")).toEqual(["92%"]);
});

test("without private mode the same question goes to the AI and no lock shows", async () => {
    const { asked, imported, nudge } = await start();
    await send(nudge, "what do these columns mean?");
    expect(".o_nudge_private").toHaveCount(0);
    expect(imported).toHaveLength(0);
    expect(asked.messages).toHaveLength(1);
});

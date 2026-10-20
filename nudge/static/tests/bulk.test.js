import { expect, test } from "@odoo/hoot";
import { click, edit, queryAllTexts, queryOne } from "@odoo/hoot-dom";
import { advanceTime, animationFrame } from "@odoo/hoot-mock";

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

import { answered, setupNudge } from "./voice_helpers";

class Partner extends models.Model {
    _name = "partner";

    name = fields.Char({ string: "Name" });

    _records = [
        { id: 1, name: "Raghad" },
        { id: 2, name: "Ebrahim" },
    ];
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Partner, ResCompany, ResPartner, ResUsers]);

const OPTIONS = {
    available: true,
    reason: "",
    limit: 200,
    classify: [{ name: "category_id", label: "Tags" }],
    text: [{ name: "comment", label: "Notes" }],
    fill: [{ name: "comment", label: "Notes" }],
};

async function start({ selection = [{ resId: 1 }, { resId: 2 }], options = OPTIONS, root = {} } = {}) {
    const asked = setupNudge({ config: { can_use_agent: true } });
    asked.bulk = [];
    const nudgeFetch = browser.fetch;
    patchWithCleanup(browser, {
        fetch(url, fetchOptions) {
            if (url === "/nudge/bulk/start") {
                asked.bulk.push(Object.fromEntries(fetchOptions.body.entries()));
                return answered({ type: "delta", text: "Here is a sample." })();
            }
            return nudgeFetch.call(browser, url, fetchOptions);
        },
    });
    onRpc("nudge.bulk", "options", () => options);
    await mountView({ resModel: "partner", type: "list", arch: `<list><field name="name"/></list>` });
    patchWithCleanup(getService("nudge_context"), {
        frontView: () => ({
            type: "list",
            controller: {
                env: { inDialog: false },
                model: {
                    root: {
                        resModel: "partner",
                        isGrouped: false,
                        count: 40,
                        domain: [["name", "ilike", "a"]],
                        isDomainSelected: false,
                        selection,
                        ...root,
                    },
                },
            },
        }),
    });
    const nudge = getService("nudge");
    nudge.open();
    await animationFrame();
    await advanceTime(1100);
    await animationFrame();
    return { asked, nudge };
}

test("with records selected the panel offers the AI for the selected, and nothing without", async () => {
    await start({ selection: [] });
    expect(".o_nudge_bulk").toHaveCount(0);
});

test("the offer names the count, asks what to do and starts with a sample", async () => {
    const { asked } = await start();
    expect(".o_nudge_bulk_open").toHaveCount(1);
    expect(queryOne(".o_nudge_bulk_open").textContent.replace(/\s+/g, " ").trim()).toBe(
        "Do this for the 2 selected"
    );
    await click(".o_nudge_bulk_open");
    await animationFrame();
    expect(queryAllTexts(".o_nudge_bulk_field option")).toEqual(["Tags"]);
    expect(".o_nudge_bulk_go").toHaveProperty("disabled", false);
    // Translating needs a language; filling needs words
    queryOne(".o_nudge_bulk_operation").value = "translate";
    queryOne(".o_nudge_bulk_operation").dispatchEvent(new Event("change", { bubbles: true }));
    await animationFrame();
    expect(queryAllTexts(".o_nudge_bulk_field option")).toEqual(["Notes"]);
    expect(".o_nudge_bulk_go").toHaveProperty("disabled", true);
    await click(".o_nudge_bulk_language");
    await edit("Arabic");
    await animationFrame();
    expect(".o_nudge_bulk_go").toHaveProperty("disabled", false);
    await click(".o_nudge_bulk_go");
    await animationFrame();
    expect(asked.bulk).toEqual([
        {
            csrf_token: asked.bulk[0].csrf_token,
            model: "partner",
            operation: "translate",
            field: "comment",
            ids: "[1,2]",
            language: "Arabic",
        },
    ]);
    expect(".o_nudge_bulk").toHaveCount(1);
});

test("every record that matches the filter is offered when the whole list is selected", async () => {
    const { asked } = await start({ root: { isDomainSelected: true } });
    expect(queryOne(".o_nudge_bulk_open").textContent.replace(/\s+/g, " ").trim()).toBe(
        "Do this for the 2 selected"
    );
    await click(".o_nudge_bulk_open");
    await animationFrame();
    await click(".o_nudge_bulk_everything");
    await animationFrame();
    expect(queryOne(".o_nudge_bulk_open").textContent.replace(/\s+/g, " ").trim()).toBe(
        "Do this for the 40 selected"
    );
    await click(".o_nudge_bulk_go");
    await animationFrame();
    expect(asked.bulk[0].domain).toBe('[["name","ilike","a"]]');
    expect(asked.bulk[0].ids).toBe(undefined);
});

test("without a provider the offer says why instead of a form", async () => {
    await start({ options: { ...OPTIONS, available: false, reason: "This needs an AI provider." } });
    await click(".o_nudge_bulk_open");
    await animationFrame();
    expect(".o_nudge_bulk_note").toHaveText("This needs an AI provider.");
    expect(".o_nudge_bulk_go").toHaveCount(0);
});

test("the records an AI over a list wrote are opened as a list", async () => {
    const { nudge } = await start();
    patchWithCleanup(getService("action"), {
        async doAction(action) {
            expect.step(`${action.res_model} ${JSON.stringify(action.domain)}`);
        },
    });
    await nudge.openBulkList({ model: "partner", ids: [1, 2], name: "Classify" });
    expect.verifySteps(['partner [["id","in",[1,2]]]']);
});

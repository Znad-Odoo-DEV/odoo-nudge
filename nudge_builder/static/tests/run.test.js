import { expect, test } from "@odoo/hoot";
import { click, queryAllTexts } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";

import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { makeMockEnv, mockService, mountWithCleanup } from "@web/../tests/web_test_helpers";

import { NudgeBuilderRun, POLL } from "@nudge_builder/js/run";

POLL.ms = 0;

defineMailModels();

const PAGES = [
    { id: 1, name: "Home", url: "/home", key: "home" },
    { id: 2, name: "About us", url: "/about", key: "about" },
];

function orm(steps, extra = {}) {
    const queue = [...steps];
    mockService("orm", {
        async call(model, method) {
            expect.step(`${model}.${method}`);
            if (method === "action_build_status") {
                return queue.shift();
            }
            return true;
        },
        async read() {
            return [{ private: extra.private || false, log: "" }];
        },
        async searchRead() {
            return PAGES;
        },
    });
}

async function mount() {
    await makeMockEnv();
    await mountWithCleanup(NudgeBuilderRun, { props: { action: { params: { site_id: 7 } } } });
    await animationFrame();
    await animationFrame();
}

test("the bar follows the steps and the card lists the pages with Open in editor", async () => {
    orm([
        { done: false, stopped: false, progress: 30, text: "Theme: colours and fonts" },
        { done: false, stopped: false, progress: 60, text: "Page: Home" },
        { done: true, stopped: false, progress: 100, text: "Done: nothing is published yet." },
    ]);
    await mount();
    expect(".progress-bar").toHaveText("100%");
    expect(".o_nudge_builder_text").toHaveText("Done: nothing is published yet.");
    expect(".o_nudge_builder_card li").toHaveCount(2);
    expect(queryAllTexts(".o_nudge_builder_card li button")).toEqual(["Open in editor", "Open in editor"]);
    expect(".o_nudge_builder_stop").toHaveCount(0);
    expect.verifySteps([
        "nudge.builder.site.action_build_status",
        "nudge.builder.site.action_build_status",
        "nudge.builder.site.action_build_status",
    ]);
});

test("Open in editor opens the page in the editor", async () => {
    orm([{ done: true, stopped: false, progress: 100, text: "" }]);
    mockService("action", {
        doAction(action) {
            expect.step(`${action.type} ${action.url}`);
        },
    });
    await mount();
    await click(".o_nudge_builder_open:first");
    expect.verifySteps([
        "nudge.builder.site.action_build_status",
        "ir.actions.act_url /home?enable_editor=1",
    ]);
});

test("Stop is heard between two steps and what is built is shown", async () => {
    let pause;
    const wait = new Promise((resolve) => (pause = resolve));
    mockService("orm", {
        async call(model, method) {
            expect.step(method);
            if (method === "action_build_status") {
                await wait;
                return { done: false, stopped: false, progress: 20, text: "Page: Home" };
            }
            return true;
        },
        async read() {
            return [{ private: true, log: "" }];
        },
        async searchRead() {
            return PAGES;
        },
    });
    await makeMockEnv();
    await mountWithCleanup(NudgeBuilderRun, { props: { action: { params: { site_id: 7 } } } });
    await animationFrame();
    await click(".o_nudge_builder_stop");
    pause();
    await animationFrame();
    await animationFrame();
    expect(".alert-warning").toHaveCount(1);
    expect(".alert-info").toHaveCount(1);
    expect.verifySteps(["action_build_status", "action_stop"]);
});

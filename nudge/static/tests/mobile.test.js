import { expect, test } from "@odoo/hoot";
import { click, pointerDown, pointerUp, queryAll, queryFirst, queryOne, resize } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";

import { browser } from "@web/core/browser/browser";
import { localization } from "@web/core/l10n/localization";
import {
    defineModels,
    fields,
    getService,
    models,
    mountView,
    patchWithCleanup,
    webModels,
} from "@web/../tests/web_test_helpers";

import { detectMobile } from "@nudge/core/nudge_service";

import { FakeRecognition, setupVoice } from "./voice_helpers";

class Partner extends models.Model {
    _name = "partner";

    name = fields.Char({ string: "Name" });

    _records = [{ id: 1, name: "Raghad" }];
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Partner, ResCompany, ResPartner, ResUsers]);

const FORM = /* xml */ `<form><sheet><group><field name="name"/></group></sheet></form>`;
const TILES = ["clock", "photo", "tasks", "record", "note"];

async function openSheet({ width = 390, tiles = TILES, config = {}, rtl = false, ...options } = {}) {
    const asked = setupVoice({
        config: { can_use_agent: true, mobile_tiles: tiles, can_transcribe: true, voice_engine: "provider", ...config },
        ...options,
    });
    await mountView({ resModel: "partner", type: "form", arch: FORM, resId: 1 });
    if (rtl) {
        patchWithCleanup(localization, { direction: "rtl" });
    }
    await resize({ width, height: 800 });
    const nudge = getService("nudge");
    nudge.open();
    await animationFrame();
    return { asked, nudge };
}

test("on 390 px the panel is a sheet over the whole window with the avatar hidden", async () => {
    const { nudge } = await openSheet();
    expect(nudge.state.isMobile).toBe(true);
    expect(".o_nudge").toHaveClass("o_nudge_mobile");
    expect(".o_nudge_panel").toHaveClass("o_nudge_sheet");
    const panel = queryOne(".o_nudge_panel").getBoundingClientRect();
    expect(Math.round(panel.width)).toBe(390);
    expect(panel.height).toBeGreaterThan(600);
    // It fills the whole of the root, which fills the window
    const root = queryOne(".o_nudge").getBoundingClientRect();
    expect([panel.left, panel.top, panel.width, panel.height]).toEqual([root.left, root.top, root.width, root.height]);
    expect(".o_nudge_avatar").not.toBeVisible();
    expect(".o_nudge_grabber").toHaveCount(1);
});

test("the big hold-to-talk button is under the message box, the mode switch is a segmented control", async () => {
    await openSheet();
    expect(".o_nudge_holdtalk").toHaveCount(1);
    const box = queryOne(".o_nudge_input").getBoundingClientRect();
    const hold = queryOne(".o_nudge_holdtalk").getBoundingClientRect();
    expect(hold.top).toBeGreaterThan(box.bottom - 1);
    expect(hold.height).toBeGreaterThan(60);
    expect(hold.width).toBeGreaterThan(280);
    // The mic of the box makes room for it
    expect(".o_nudge_composer .o_nudge_mic").not.toBeVisible();
    const modes = queryOne(".o_nudge_modes").getBoundingClientRect();
    const panel = queryOne(".o_nudge_panel").getBoundingClientRect();
    expect(modes.width).toBeGreaterThan(panel.width - 40);
    const buttons = queryAll(".o_nudge_mode");
    expect(buttons.length).toBeGreaterThan(1);
    expect(Math.abs(buttons[0].getBoundingClientRect().width - buttons[1].getBoundingClientRect().width)).toBeLessThan(2);
});

test("held, the button listens; let go, it stops", async () => {
    const { nudge } = await openSheet({ config: { can_transcribe: false, voice_engine: "browser" } });
    const voice = getService("nudge_voice");
    await pointerDown(".o_nudge_holdtalk");
    await animationFrame();
    expect(voice.state.mode).toBe("question");
    expect(".o_nudge_holdtalk").toHaveClass("o_nudge_holdtalk_on");
    FakeRecognition.last.hear("Where is the invoice of Raghad");
    await pointerUp(".o_nudge_holdtalk");
    await animationFrame();
    expect(voice.state.mode).toBe(null);
    expect(nudge.state.messages.at(0).content).toBe("Where is the invoice of Raghad");
});

test("the tiles of the company, in its order, with the record tile only while a record is open", async () => {
    const { nudge } = await openSheet({ tiles: ["tasks", "photo", "record", "clock"] });
    // A form with a record is on the screen
    nudge.lookAtScreen();
    await animationFrame();
    const labels = queryAll(".o_nudge_tile").map((tile) => tile.textContent.trim());
    expect(labels).toEqual(["My tasks today", "Photo of a bill", "Ask about this record", "Clock in/out"]);
    const first = queryFirst(".o_nudge_tile").getBoundingClientRect();
    expect(first.height).toBeGreaterThan(90);
    await click(".o_nudge_tile_tasks");
    await animationFrame();
    expect(nudge.state.mode).toBe("agent");
    expect(nudge.state.messages.at(0).content).toBe("What are my tasks for today?");
});

test("a tile the company did not choose is not there", async () => {
    await openSheet({ tiles: ["photo"] });
    expect(".o_nudge_tile").toHaveCount(1);
    expect(".o_nudge_tile_clock").toHaveCount(0);
    expect(".o_nudge_tile_tasks").toHaveCount(0);
});

test("swiping down on the header closes the sheet", async () => {
    const { nudge } = await openSheet();
    const header = queryOne(".o_nudge_header");
    const touch = (y, x = 120) => new Touch({ identifier: 1, target: header, clientX: x, clientY: y });
    // Too short, or sideways: it stays
    header.dispatchEvent(new TouchEvent("touchstart", { changedTouches: [touch(100)], bubbles: true }));
    header.dispatchEvent(new TouchEvent("touchend", { changedTouches: [touch(140)], bubbles: true }));
    await animationFrame();
    expect(nudge.state.isOpen).toBe(true);
    header.dispatchEvent(new TouchEvent("touchstart", { changedTouches: [touch(100)], bubbles: true }));
    header.dispatchEvent(new TouchEvent("touchend", { changedTouches: [touch(260)], bubbles: true }));
    await animationFrame();
    expect(nudge.state.isOpen).toBe(false);
    expect(".o_nudge_panel").toHaveCount(0);
});

test("right-to-left mirrors the sheet: the first tile is on the right", async () => {
    await openSheet({ rtl: true });
    expect(".o_nudge").toHaveAttribute("dir", "rtl");
    const [first, second] = queryAll(".o_nudge_tile").map((tile) => tile.getBoundingClientRect());
    expect(first.left).toBeGreaterThan(second.left);
    // Same row, mirrored
    expect(Math.round(first.top)).toBe(Math.round(second.top));
});

test("left-to-right: the first tile is on the left", async () => {
    await openSheet();
    const [first, second] = queryAll(".o_nudge_tile").map((tile) => tile.getBoundingClientRect());
    expect(first.left).toBeLessThan(second.left);
});

test("on a wide window nothing changes: no sheet, the avatar, no tiles, no big button", async () => {
    const { nudge } = await openSheet({ width: 1280 });
    expect(nudge.state.isMobile).toBe(false);
    expect(".o_nudge_mobile").toHaveCount(0);
    expect(".o_nudge_sheet").toHaveCount(0);
    expect(".o_nudge_tile").toHaveCount(0);
    expect(".o_nudge_holdtalk").toHaveCount(0);
});

test("?mobile=1 and the Odoo app switch the sheet on whatever the width", async () => {
    await resize({ width: 1280, height: 800 });
    expect(detectMobile()).toBe(false);
    patchWithCleanup(browser, { location: { ...browser.location, search: "?mobile=1" } });
    expect(detectMobile()).toBe(true);
    patchWithCleanup(browser, {
        location: { ...browser.location, search: "" },
        navigator: { ...browser.navigator, userAgent: "Mozilla/5.0 OdooMobile/19.0" },
    });
    expect(detectMobile()).toBe(true);
});

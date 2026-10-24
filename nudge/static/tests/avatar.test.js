import { Component, xml } from "@odoo/owl";

import { expect, test } from "@odoo/hoot";
import { click, queryOne } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";

import { browser } from "@web/core/browser/browser";
import { user } from "@web/core/user";
import { getService, makeMockEnv, mountWithCleanup, onRpc, patchWithCleanup } from "@web/../tests/web_test_helpers";

import { placement } from "@nudge/core/drag";
import { readPosition } from "@nudge/core/looks";

import { nudgeMenuItem } from "@nudge/components/nudge_user_menu";

import { setupNudge } from "./voice_helpers";

class Nothing extends Component {
    static template = xml`<div class="o_nudge_test_page"/>`;
    static props = {};
}

async function start(settings = {}) {
    const asked = setupNudge();
    patchWithCleanup(user, { settings: { ...user.settings, ...settings } });
    await makeMockEnv();
    await mountWithCleanup(Nothing);
    return { asked, nudge: getService("nudge") };
}

function press(element, type, x, y) {
    element.dispatchEvent(
        new PointerEvent(type, { clientX: x, clientY: y, pointerId: 1, bubbles: true, button: 0 })
    );
}

test("a position is read with care", () => {
    expect(readPosition('{"x": 0.2, "y": 0.7}')).toEqual({ x: 0.2, y: 0.7 });
    for (const saved of ["", null, undefined, "{broken", '{"x": 2, "y": 0.5}', '{"x": "a"}', "[]", '{"x": -1, "y": 0}']) {
        expect(readPosition(saved)).toBe(null, { message: String(saved) });
    }
});

test("the panel opens on the side where there is room", () => {
    const viewport = { width: 1000, height: 800 };
    expect(placement(null, viewport, 56)).toBe(null);

    const bottomLeft = placement({ x: 0.1, y: 0.8 }, viewport, 56);
    expect(bottomLeft).toEqual({ style: "left: 100px; right: auto; bottom: 104px; top: auto", flip: "up", align: "start" });
    const topRight = placement({ x: 0.9, y: 0.1 }, viewport, 56);
    expect(topRight).toEqual({ style: "right: 44px; left: auto; top: 80px; bottom: auto", flip: "down", align: "end" });
    // Kept on the screen
    expect(placement({ x: 1, y: 1 }, viewport, 56).style).toBe("right: 0px; left: auto; bottom: 0px; top: auto");
    expect(placement({ x: -3, y: 9 }, viewport, 56).style).toBe("left: 0px; right: auto; bottom: 0px; top: auto");
});

test("the avatar is dragged where the person wants it, and stays there", async () => {
    const { asked, nudge } = await start();
    await animationFrame();
    const root = queryOne(".o_nudge");
    const avatar = queryOne(".o_nudge_avatar");
    expect(root).not.toHaveClass("o_nudge_moved");
    expect(root).not.toHaveAttribute("style");

    // A press that goes nowhere is a click
    press(avatar, "pointerdown", 30, 850);
    press(avatar, "pointerup", 32, 851);
    await click(avatar);
    await animationFrame();
    expect(nudge.state.isOpen).toBe(true);
    expect(nudge.state.position).toBe(null);
    nudge.close();
    await animationFrame();

    // The avatar starts at the inline-end of the screen: dragged towards the inline-start
    press(avatar, "pointerdown", 300, 850);
    press(avatar, "pointermove", 30, 400);
    await animationFrame();
    expect(avatar).toHaveClass("o_nudge_avatar_dragging");
    expect(root).toHaveClass("o_nudge_moved");
    press(avatar, "pointerup", 30, 400);
    await animationFrame();

    expect(nudge.state.position).not.toBe(null);
    expect(nudge.state.position.x).toBeGreaterThan(0);
    expect(asked.saved.at(-1)).toEqual({ nudge_position: JSON.stringify(nudge.state.position) });
    expect(avatar).not.toHaveClass("o_nudge_avatar_dragging");
    expect(root).toHaveAttribute("style");
    expect(root.dataset.flip).toMatch(/^(up|down)$/);

    // Put back in the corner from the preferences
    nudge.open();
    nudge.showSettings();
    await animationFrame();
    await click(".o_nudge_corner");
    await animationFrame();
    expect(nudge.state.position).toBe(null);
    expect(asked.saved.at(-1)).toEqual({ nudge_position: false });
    expect(".o_nudge_corner").toHaveCount(0);
});

test("the position and the colour are remembered", async () => {
    patchWithCleanup(browser, { innerWidth: 1000, innerHeight: 800 });
    const { asked, nudge } = await start({ nudge_position: '{"x": 0.9, "y": 0.1}', nudge_theme: "teal" });
    await animationFrame();
    const root = queryOne(".o_nudge");
    expect(root).toHaveAttribute("data-theme", "teal");
    expect(root).toHaveAttribute("data-flip", "down");
    expect(root).toHaveAttribute("data-align", "end");
    expect(root.style.right).toBe("44px");

    nudge.open();
    nudge.showSettings();
    await animationFrame();
    expect(".o_nudge_theme").toHaveValue("teal");
    await nudge.setTheme("rose");
    await animationFrame();
    expect(root).toHaveAttribute("data-theme", "rose");
    expect(asked.saved.at(-1)).toEqual({ nudge_theme: "rose" });
    await nudge.setTheme("neon");
    expect(nudge.state.theme).toBe("odoo");
    expect(getComputedStyle(root).getPropertyValue("--nudge-accent").trim()).not.toBe("");
});

test("Nudge is switched off and on from the user menu", async () => {
    const { asked, nudge } = await start();
    await animationFrame();
    expect(".o_nudge_avatar").toHaveCount(1);
    const item = nudgeMenuItem({ services: { nudge } });
    expect(item.show()).toBe(true);
    expect(item.description).toBe("Nudge: On");

    nudge.open();
    await animationFrame();
    item.callback();
    await animationFrame();
    expect(".o_nudge_avatar").toHaveCount(0, { message: "Switched off: gone at once" });
    expect(nudge.state.isOpen).toBe(false);
    expect(asked.saved.at(-1)).toEqual({ nudge_enabled: false });
    expect(nudgeMenuItem({ services: { nudge } }).description).toBe("Nudge: Off");

    nudgeMenuItem({ services: { nudge } }).callback();
    await animationFrame();
    expect(".o_nudge_avatar").toHaveCount(1);
    expect(asked.saved.at(-1)).toEqual({ nudge_enabled: true });
});

test("switched off, Nudge stays off on the next page", async () => {
    await start({ nudge_enabled: false });
    await animationFrame();
    expect(".o_nudge_avatar").toHaveCount(0);
    expect(getService("nudge").state.isAvailable).toBe(true, { message: "and can be switched on again" });
});

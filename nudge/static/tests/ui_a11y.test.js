import { Component, xml } from "@odoo/owl";

import { expect, test } from "@odoo/hoot";
import { click, edit, queryAllTexts, queryOne } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";

import { user } from "@web/core/user";
import { getService, makeMockEnv, mountWithCleanup, onRpc, patchWithCleanup } from "@web/../tests/web_test_helpers";

import { DEFAULT_THEME, LOOK_SETTINGS, readCompanyColor, THEMES } from "@nudge/core/looks";

import { setupNudge } from "./voice_helpers";

// UI-01..UI-09 and NP-23: the panel as assistive technology and the person's look preferences see it

class Nothing extends Component {
    static template = xml`<div class="o_nudge_test_page"/>`;
    static props = {};
}

const COMMANDS = [
    { name: "help", words: ["/help", "/مساعدة"], kind: "direct", hint: "The commands you can use here", example: "/help" },
    { name: "screen", words: ["/screen", "/شاشة"], kind: "direct", hint: "A screen you can open", example: "/screen invoices" },
];

async function start(settings = {}, config = {}) {
    const asked = setupNudge({ config: { commands: COMMANDS, ...config } });
    patchWithCleanup(user, { settings: { ...user.settings, ...settings } });
    await makeMockEnv();
    await mountWithCleanup(Nothing);
    const nudge = getService("nudge");
    return { asked, nudge };
}

test("the message list is a polite log, busy while the answer streams (UI-01)", async () => {
    const { nudge } = await start();
    nudge.open();
    await animationFrame();
    const body = queryOne(".o_nudge_body");
    expect(body).toHaveAttribute("role", "log");
    expect(body).toHaveAttribute("aria-live", "polite");
    expect(body).toHaveAttribute("aria-busy", "false");
    nudge.state.isStreaming = true;
    await animationFrame();
    expect(body).toHaveAttribute("aria-busy", "true");
    nudge.state.isStreaming = false;
    await animationFrame();
    expect(body).toHaveAttribute("aria-busy", "false");
    // The history and the preferences are not a log
    nudge.showSettings();
    await animationFrame();
    expect(queryOne(".o_nudge_body")).not.toHaveAttribute("role");
});

test("a command is isolated left-to-right and the box is a combobox (UI-02, UI-09)", async () => {
    const { nudge } = await start();
    nudge.open();
    await animationFrame();
    const input = queryOne(".o_nudge_input");
    expect(input).toHaveAttribute("role", "combobox");
    expect(input).toHaveAttribute("aria-expanded", "false");
    expect(input).not.toHaveAttribute("aria-activedescendant");
    await click(".o_nudge_input");
    await edit("/");
    await animationFrame();
    expect(".o_nudge_commands li strong bdi[dir=ltr]").toHaveCount(2);
    expect(queryAllTexts(".o_nudge_commands li strong bdi")).toEqual(["/help", "/screen"]);
    expect(input).toHaveAttribute("aria-expanded", "true");
    expect(input).toHaveAttribute("aria-controls", "o_nudge_commands");
    expect(input).toHaveAttribute("aria-activedescendant", "o_nudge_command_0");
    expect("#o_nudge_command_0").toHaveCount(1);
});

test("the accent follows the colour of Odoo unless another one is chosen (UI-03)", async () => {
    expect(DEFAULT_THEME).toBe("odoo");
    expect(THEMES).toInclude("violet");
    const { nudge } = await start();
    await animationFrame();
    expect(".o_nudge").toHaveAttribute("data-theme", "odoo");
    nudge.setTheme("violet");
    await animationFrame();
    expect(".o_nudge").toHaveAttribute("data-theme", "violet");
    // A theme that does not exist is the default
    nudge.setTheme("neon");
    await animationFrame();
    expect(".o_nudge").toHaveAttribute("data-theme", "odoo");
});

test("the colour of a company is taken only when it is a plain hexadecimal colour", () => {
    expect(readCompanyColor("#714b67")).toBe("#714b67");
    for (const value of ["red", "#fff", "url(x)", "#12345g", "#714b67; x: y", false, null, 12]) {
        expect(readCompanyColor(value)).toBe("", { message: String(value) });
    }
});

test("the avatar is a button inside the root, and the page is told Nudge is there (UI-05)", async () => {
    await start();
    await animationFrame();
    expect(document.body).toHaveClass("o_nudge_on");
    expect(".o_nudge .o_nudge_avatar").toHaveCount(1);
});

test("the preferences keep high contrast, large text, compact tiles and the dock (NP-23)", async () => {
    const { asked, nudge } = await start();
    await animationFrame();
    expect(".o_nudge").not.toHaveClass("o_nudge_hc");
    nudge.open();
    nudge.showSettings();
    await animationFrame();
    expect(".o_nudge_settings input[class*=o_nudge_look_]").toHaveCount(5);

    await click(".o_nudge_look_highContrast");
    await animationFrame();
    expect(".o_nudge").toHaveClass("o_nudge_hc");
    expect(asked.saved.at(-1)).toEqual({ nudge_high_contrast: true });

    await click(".o_nudge_look_largeText");
    await animationFrame();
    expect(".o_nudge").toHaveClass("o_nudge_lg");
    expect(asked.saved.at(-1)).toEqual({ nudge_large_text: true });

    await click(".o_nudge_look_compact");
    await animationFrame();
    expect(asked.saved.at(-1)).toEqual({ nudge_compact: true });
    expect(nudge.state.compact).toBe(true);

    await click(".o_nudge_look_dock");
    await animationFrame();
    expect(asked.saved.at(-1)).toEqual({ nudge_dock: true });
    expect(".o_nudge").toHaveClass("o_nudge_docked");
    expect(document.body).toHaveClass("o_nudge_dock_open");

    // Back off
    await click(".o_nudge_look_highContrast");
    await animationFrame();
    expect(".o_nudge").not.toHaveClass("o_nudge_hc");
    expect(Object.keys(LOOK_SETTINGS)).toEqual(["highContrast", "largeText", "compact", "dock", "companyAccent"]);
    // Only the known toggles are written
    await nudge.setLook("nudge_enabled", false);
    expect(asked.saved.at(-1)).toEqual({ nudge_high_contrast: false });
});

test("the choices made before are there when the page opens (NP-23)", async () => {
    const { nudge } = await start({ nudge_high_contrast: true, nudge_large_text: true, nudge_dock: true });
    nudge.open();
    await animationFrame();
    expect(".o_nudge").toHaveClass("o_nudge_hc");
    expect(".o_nudge").toHaveClass("o_nudge_lg");
    expect(".o_nudge").toHaveClass("o_nudge_docked");
});

test("the colour of the company is read once and used as the accent when asked for (NP-23)", async () => {
    onRpc("res.company", "read", ({ args }) => {
        expect.step("read company");
        return [{ id: args[0][0], primary_color: "#0f8b8d" }];
    });
    const { nudge } = await start();
    await animationFrame();
    expect.verifySteps([]);
    await nudge.setLook("companyAccent", true);
    await animationFrame();
    expect.verifySteps(["read company"]);
    expect(".o_nudge").toHaveClass("o_nudge_company");
    expect(queryOne(".o_nudge").getAttribute("style")).toInclude("--nudge-company: #0f8b8d");
    expect(queryOne(".o_nudge").getAttribute("style")).toInclude("--nudge-company-rgb: 15, 139, 141");
});

test("the question mark lists the commands and Escape closes it (NP-23)", async () => {
    const { nudge } = await start();
    nudge.open();
    await animationFrame();
    expect(".o_nudge_shortcuts").toHaveCount(0);
    await click(".o_nudge_help");
    await animationFrame();
    expect(".o_nudge_shortcuts").toHaveCount(1);
    expect(".o_nudge_shortcuts dt bdi[dir=ltr]").toHaveCount(2);
    expect(queryAllTexts(".o_nudge_shortcuts dt bdi")).toEqual(["/help", "/screen"]);
    expect(".o_nudge_help").toHaveAttribute("aria-expanded", "true");
    await click(".o_nudge_input");
    queryOne(".o_nudge_panel").dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await animationFrame();
    expect(".o_nudge_shortcuts").toHaveCount(0);
    expect(".o_nudge_panel").toHaveCount(1, { message: "Escape closes the list, not Nudge" });
});

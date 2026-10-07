/** @odoo-module **/

export const DEFAULT_HOTKEY = "control+shift+space";
export const DEFAULT_DICTATION_HOTKEY = "alt+control+d";
export const NO_HOTKEY = "none";

// Combinations that neither the web client nor the browsers use. Modifiers are
// in the order the hotkey service writes them: alt, control, shift.
export const HOTKEYS = [DEFAULT_HOTKEY, "alt+control+space", "alt+control+n", NO_HOTKEY];
// None of them calls Nudge: the two shortcuts cannot be the same
export const DICTATION_HOTKEYS = [DEFAULT_DICTATION_HOTKEY, "control+shift+d", NO_HOTKEY];

const KEY_NAMES = { control: "Ctrl", alt: "Alt", shift: "Shift", space: "Space" };

/**
 * @param {string} hotkey as the hotkey service writes it
 * @returns {string} as it is printed on a keyboard
 */
export function hotkeyLabel(hotkey) {
    if (!hotkey || hotkey === NO_HOTKEY) {
        return "";
    }
    const keys = hotkey.split("+").map((key) => KEY_NAMES[key] || key.toUpperCase());
    // Written Alt first by the hotkey service, read Ctrl first by people
    keys.sort((a, b) => Number(b === "Ctrl") - Number(a === "Ctrl"));
    return keys.join(" + ");
}

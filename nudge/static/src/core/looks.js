/** @odoo-module **/

/**
 * How Nudge looks to a person: its colour, and where it sits on the screen.
 * Kept apart from the service so that the components may read it without
 * depending on the service (which depends on the root, which depends on them).
 */

// "odoo" takes the primary colour of Odoo (and so the colour of the company, the theme and the dark mode);
// the others are Nudge's own, kept as a choice
export const THEMES = ["odoo", "violet", "teal", "amber", "rose", "slate"];
export const DEFAULT_THEME = "odoo";

// NP-23: the per-person toggles of the look, with the setting each is kept in
export const LOOK_SETTINGS = {
    highContrast: "nudge_high_contrast",
    largeText: "nudge_large_text",
    compact: "nudge_compact",
    dock: "nudge_dock",
    companyAccent: "nudge_company_accent",
};

/**
 * @param {*} value what the company holds as its colour
 * @returns {string} the colour when it is a plain #rrggbb, else empty: it goes into a style attribute
 */
export function readCompanyColor(value) {
    return typeof value === "string" && /^#[0-9a-fA-F]{6}$/.test(value.trim()) ? value.trim() : "";
}

/**
 * @param {string} saved what the setting holds
 * @returns {{ x: number, y: number } | null} a position on the screen, or none
 */
export function readPosition(saved) {
    try {
        const position = JSON.parse(saved || "null");
        const { x, y } = position || {};
        if (typeof x === "number" && typeof y === "number" && x >= 0 && x <= 1 && y >= 0 && y <= 1) {
            return { x, y };
        }
    } catch {
        // Not a position: the corner
    }
    return null;
}

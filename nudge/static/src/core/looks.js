/**
 * How Nudge looks to a person: its colour, and where it sits on the screen.
 * Kept apart from the service so that the components may read it without
 * depending on the service (which depends on the root, which depends on them).
 */

export const THEMES = ["violet", "teal", "amber", "rose", "slate"];
export const DEFAULT_THEME = "violet";

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

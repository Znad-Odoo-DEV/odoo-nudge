import { browser } from "@web/core/browser/browser";

// Keys kept down longer than this are held, not tapped
export const HOLD_DELAY = 350;

/**
 * Tells a shortcut that is held down from one that is tapped. The hotkey
 * service says when a combination is pressed; when it is released is heard
 * here.
 *
 * @param {Object} params
 * @param {() => void} [params.onPress] at once, held or tapped
 * @param {() => void} [params.onHold] once the keys have been down long enough
 * @param {() => void} [params.onRelease] when keys that were held are released
 * @returns {() => void} to call when the combination is pressed
 */
export function holdable({ onPress, onHold, onRelease }) {
    let timer = null;
    let isHeld = false;
    let forget = null;

    function release() {
        forget?.();
        forget = null;
        browser.clearTimeout(timer);
        timer = null;
        if (isHeld) {
            isHeld = false;
            onRelease?.();
        }
    }

    function swallow(event) {
        // Keys kept down repeat themselves: that is not typing
        if (event.repeat) {
            event.preventDefault();
            event.stopPropagation();
        }
    }

    return function press() {
        if (timer || isHeld) {
            return;
        }
        window.addEventListener("keyup", release, true);
        window.addEventListener("blur", release);
        window.addEventListener("keydown", swallow, true);
        forget = () => {
            window.removeEventListener("keyup", release, true);
            window.removeEventListener("blur", release);
            window.removeEventListener("keydown", swallow, true);
        };
        timer = browser.setTimeout(() => {
            timer = null;
            isHeld = true;
            onHold?.();
        }, HOLD_DELAY);
        onPress?.();
    };
}

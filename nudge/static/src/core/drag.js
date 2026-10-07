/** @odoo-module **/

// A press that moves less than this is a click, not a drag
export const DRAG_THRESHOLD = 6;

/**
 * Lets the pointer move something. A press that goes nowhere is a click.
 *
 * @param {HTMLElement} handle what is pressed
 * @param {Object} params
 * @param {(dx: number, dy: number) => void} params.onMove while moving, from where it was pressed
 * @param {() => void} params.onDrop once let go after a move
 * @param {() => void} [params.onClick] once let go without a move
 * @returns {() => void} to call to stop listening
 */
export function makeDraggable(handle, { onMove, onDrop, onClick }) {
    let start = null;
    let isMoving = false;

    function onPointerDown(event) {
        if (event.button !== 0 && event.pointerType === "mouse") {
            return;
        }
        start = { x: event.clientX, y: event.clientY, id: event.pointerId };
        isMoving = false;
        try {
            handle.setPointerCapture(event.pointerId);
        } catch {
            // Nothing to capture in some environments: the pointer is followed all the same
        }
    }

    function onPointerMove(event) {
        if (!start || event.pointerId !== start.id) {
            return;
        }
        const dx = event.clientX - start.x;
        const dy = event.clientY - start.y;
        if (!isMoving && Math.hypot(dx, dy) < DRAG_THRESHOLD) {
            return;
        }
        isMoving = true;
        event.preventDefault();
        onMove(dx, dy);
    }

    function onPointerUp(event) {
        if (!start || event.pointerId !== start.id) {
            return;
        }
        const wasMoving = isMoving;
        start = null;
        isMoving = false;
        if (wasMoving) {
            onDrop();
        } else {
            onClick?.();
        }
    }

    function onClickCapture(event) {
        // The click that ends a drag is not a click on the handle
        if (event.detail !== 0 && handle.dataset.dragged === "true") {
            event.stopPropagation();
            event.preventDefault();
            delete handle.dataset.dragged;
        }
    }

    handle.addEventListener("pointerdown", onPointerDown);
    handle.addEventListener("pointermove", onPointerMove);
    handle.addEventListener("pointerup", onPointerUp);
    handle.addEventListener("pointercancel", onPointerUp);
    handle.addEventListener("click", onClickCapture, true);
    return () => {
        handle.removeEventListener("pointerdown", onPointerDown);
        handle.removeEventListener("pointermove", onPointerMove);
        handle.removeEventListener("pointerup", onPointerUp);
        handle.removeEventListener("pointercancel", onPointerUp);
        handle.removeEventListener("click", onClickCapture, true);
    };
}

/**
 * Where the avatar sits, as fractions of the screen, and what follows from
 * it: the panel opens on the side where there is room.
 *
 * @param {{ x: number, y: number } | null} position of the top-left corner of the avatar
 * @param {{ width: number, height: number }} viewport
 * @param {number} size of the avatar
 * @returns {{ style: string, flip: string, align: string } | null} null when in the corner
 */
export function placement(position, viewport, size) {
    if (!position || typeof position.x !== "number" || typeof position.y !== "number") {
        return null;
    }
    const x = Math.min(Math.max(position.x, 0), 1);
    const y = Math.min(Math.max(position.y, 0), 1);
    const left = Math.min(Math.max(x * viewport.width, 0), Math.max(viewport.width - size, 0));
    const top = Math.min(Math.max(y * viewport.height, 0), Math.max(viewport.height - size, 0));
    const flip = top + size / 2 < viewport.height / 2 ? "down" : "up";
    const align = left + size / 2 > viewport.width / 2 ? "end" : "start";
    const parts = [];
    if (align === "end") {
        parts.push(`right: ${Math.round(viewport.width - left - size)}px`, "left: auto");
    } else {
        parts.push(`left: ${Math.round(left)}px`, "right: auto");
    }
    if (flip === "down") {
        parts.push(`top: ${Math.round(top)}px`, "bottom: auto");
    } else {
        parts.push(`bottom: ${Math.round(viewport.height - top - size)}px`, "top: auto");
    }
    return { style: parts.join("; "), flip, align };
}

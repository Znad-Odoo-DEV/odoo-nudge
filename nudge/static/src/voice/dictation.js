/**
 * Typing what was said where the person was typing.
 *
 * The text is inserted the way a keyboard would insert it, so that whatever
 * owns the field, a field of a form, the editor of a note, the search box,
 * hears it as typing and treats it as such.
 */

const TEXT_INPUTS = new Set(["text", "search", "email", "url", "tel", ""]);
// Odoo writes its fields for numbers as fields for text that ask for digits
const DIGIT_MODES = new Set(["numeric", "decimal"]);
// Widgets of Odoo that hold text, by the class they give to their field:
// o_field_char, o_field_text, o_field_html, o_field_email, o_field_char_emojis…
const TEXT_WIDGET = /^o_field_\w*(char|text|html|email|url|phone)/;
const OPENING = /^[\s.,;:!?،؛؟)\]}»]/;

/**
 * Dates, amounts and records to pick are typed in fields for text too. What
 * is heard is not precise enough for them.
 */
function holdsText(element) {
    const widget = element.closest(".o_field_widget");
    // Not the field of a record: a search box, the text of a message
    return !widget || [...widget.classList].some((name) => TEXT_WIDGET.test(name));
}

/**
 * @param {Element|null} element
 * @returns {boolean} whether text can be typed in it
 */
export function isEditable(element) {
    if (!(element instanceof HTMLElement) || element.closest(".o_nudge")) {
        return false;
    }
    if (!holdsText(element)) {
        return false;
    }
    if (element instanceof HTMLTextAreaElement) {
        return !element.readOnly && !element.disabled;
    }
    if (element instanceof HTMLInputElement) {
        // Never a password, never what is not text: a number that is heard
        // wrong is a wrong number
        const type = (element.getAttribute("type") || "").toLowerCase();
        const mode = (element.getAttribute("inputmode") || "").toLowerCase();
        return (
            TEXT_INPUTS.has(type) &&
            !DIGIT_MODES.has(mode) &&
            !element.readOnly &&
            !element.disabled
        );
    }
    return element.isContentEditable;
}

function isField(element) {
    return element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement;
}

function textBefore(element) {
    if (isField(element)) {
        return element.value.slice(0, element.selectionStart ?? element.value.length);
    }
    const selection = element.ownerDocument.getSelection();
    if (!selection?.rangeCount || !element.contains(selection.anchorNode)) {
        return "";
    }
    const range = selection.getRangeAt(0).cloneRange();
    range.collapse(true);
    range.setStart(element, 0);
    return range.toString();
}

/**
 * @param {HTMLElement} element
 * @param {string} text
 * @returns {string} the text with the space that separates it from what comes before
 */
export function spaced(element, text) {
    const before = textBefore(element);
    if (!before || /\s$/.test(before) || OPENING.test(text)) {
        return text;
    }
    return ` ${text}`;
}

/**
 * @param {HTMLElement} element where to type; it keeps or takes the focus
 * @param {string} text
 * @returns {boolean} whether the text was typed
 */
export function insertText(element, text) {
    if (!text || !isEditable(element) || !element.isConnected) {
        return false;
    }
    const isSingleLine = element instanceof HTMLInputElement;
    text = spaced(element, isSingleLine ? text.replace(/\s+/g, " ") : text);
    if (element.ownerDocument.activeElement !== element) {
        element.focus();
    }
    // The only way to type that editors of rich text follow, and the one that
    // keeps what Ctrl+Z undoes
    let done = false;
    try {
        done = element.ownerDocument.execCommand("insertText", false, text);
    } catch {
        done = false;
    }
    if (done) {
        return true;
    }
    if (!isField(element)) {
        return false;
    }
    try {
        const start = element.selectionStart ?? element.value.length;
        const end = element.selectionEnd ?? start;
        element.setRangeText(text, start, end, "end");
    } catch {
        // Fields for addresses have no cursor to tell: at the end
        element.value += text;
    }
    element.dispatchEvent(
        new InputEvent("input", { bubbles: true, inputType: "insertText", data: text })
    );
    return true;
}

/**
 * Tell the owner of the field that the typing is over. A browser says so
 * when someone leaves a field they typed in; nobody left this one.
 *
 * @param {HTMLElement} element
 */
export function commit(element) {
    if (isField(element) && element.isConnected) {
        element.dispatchEvent(new Event("change", { bubbles: true }));
    }
}

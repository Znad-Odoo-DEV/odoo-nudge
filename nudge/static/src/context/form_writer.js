/** @odoo-module **/

import { evaluateBooleanExpr } from "@web/core/py_js/py";

/**
 * Types a text in a field of the form in front, through the record the form
 * edits: the same path as the keyboard of the person. Nothing is saved.
 *
 * What to write where comes from a model. It is checked again here, against
 * the form as it is now: the field is one of this form, it holds text, and
 * the person could type in it themselves.
 */

const WRITABLE_TYPES = new Set(["char", "text"]);

function isTrue(expression, context) {
    if (!expression) {
        return false;
    }
    try {
        return evaluateBooleanExpr(expression, context);
    } catch {
        // Not known to be editable is not editable
        return true;
    }
}

/**
 * @param {Object|null} view the view in front, as registered by its controller
 * @param {Object} params
 * @param {string} params.field technical name of the field
 * @param {string} params.text
 * @param {"append"|"replace"} [params.mode]
 * @returns {Promise<{ undo: () => Promise<void> } | { error: string }>}
 */
export async function writeInField(view, { field, text, mode }) {
    const record = view?.type === "form" ? view.controller.model?.root : null;
    if (!record || typeof field !== "string" || typeof text !== "string" || !text.trim()) {
        return { error: "no_form" };
    }
    const isKnown = Object.hasOwn(record.fields, field) && Object.hasOwn(record.activeFields, field);
    const definition = isKnown ? record.fields[field] : null;
    if (!definition || !WRITABLE_TYPES.has(definition.type)) {
        return { error: "no_field" };
    }
    const info = record.activeFields[field];
    const context = record.evalContextWithVirtualIds;
    if (!record.isInEdition || isTrue(info.readonly, context) || isTrue(info.invisible, context)) {
        return { error: "readonly" };
    }
    const before = record.data[field] || "";
    const isSingleLine = definition.type === "char";
    let value = isSingleLine ? text.replace(/\s+/g, " ").trim() : text.trim();
    if (mode !== "replace" && before) {
        value = `${before}${isSingleLine ? " " : "\n"}${value}`;
    }
    try {
        await record.update({ [field]: value });
    } catch {
        return { error: "refused" };
    }
    return {
        undo: async () => {
            await record.update({ [field]: before || false });
        },
    };
}

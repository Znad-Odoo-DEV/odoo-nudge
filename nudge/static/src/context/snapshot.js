/** @odoo-module **/

import { evaluateBooleanExpr } from "@web/core/py_js/py";

/**
 * Describes the screen as structured data: what the view is, which record it
 * shows, its fields with their values, and the buttons and tabs it offers.
 *
 * Everything comes from what the web client already holds for the current
 * user. Nothing is read from the server here, so the description cannot
 * contain more than the person can see.
 */

export const LIMITS = {
    fields: 80,
    buttons: 40,
    rows: 10,
    lineRows: 5,
    lineColumns: 6,
    names: 5,
    text: 160,
    notices: 5,
};

const SKIPPED_TYPES = new Set(["binary", "image", "json", "properties", "properties_definition"]);

function truncate(text, size = LIMITS.text) {
    text = String(text).replace(/\s+/g, " ").trim();
    return text.length > size ? text.slice(0, size - 1) + "…" : text;
}

function htmlToText(html) {
    // Parsed in a document of its own, where nothing loads or runs
    const doc = new DOMParser().parseFromString(String(html), "text/html");
    for (const element of doc.querySelectorAll("script, style, template")) {
        element.remove();
    }
    return doc.body.textContent || "";
}

function isTrue(expression, context) {
    if (!expression) {
        return false;
    }
    try {
        return evaluateBooleanExpr(expression, context);
    } catch {
        return false;
    }
}

function isShown(element) {
    return Boolean(element && element.getClientRects().length);
}

/**
 * @param {Object} field definition of the field
 * @param {any} value value held by the record
 * @returns {any} value as plain data, or undefined when it is not described
 */
export function formatValue(field, value) {
    if (SKIPPED_TYPES.has(field.type)) {
        return undefined;
    }
    if (value === false || value === null || value === undefined) {
        return field.type === "boolean" ? false : null;
    }
    switch (field.type) {
        case "boolean":
            return Boolean(value);
        case "integer":
        case "float":
        case "monetary":
            return typeof value === "number" ? value : null;
        case "date":
            return value.toISODate ? value.toISODate() : truncate(value);
        case "datetime":
            return value.toISO ? value.toISO({ suppressMilliseconds: true }) : truncate(value);
        case "selection": {
            const option = (field.selection || []).find(([key]) => key === value);
            return option ? truncate(option[1]) : truncate(value);
        }
        case "many2one":
            return truncate(value.display_name ?? value.id ?? "");
        case "many2one_reference":
        case "reference":
            return truncate(value.displayName ?? value.display_name ?? value.resId ?? "");
        case "one2many":
        case "many2many":
            return formatList(value);
        case "html":
            // An empty field is told the same way whatever its type
            return truncate(htmlToText(value)) || null;
        default:
            return truncate(value) || null;
    }
}

function formatList(list) {
    const records = list.records || [];
    const result = { count: list.count ?? records.length };
    const rows = records.slice(0, LIMITS.lineRows).map((record) => describeRow(record));
    if (rows.length) {
        result.rows = rows;
    }
    return result;
}

function describeRow(record, names) {
    const row = {};
    names = names || Object.keys(record.activeFields || {});
    let columns = 0;
    for (const name of names) {
        const field = record.fields[name];
        const info = record.activeFields[name];
        if (!field || !info || SKIPPED_TYPES.has(field.type) || field.type === "html") {
            continue;
        }
        if (isTrue(info.invisible, record.evalContextWithVirtualIds)) {
            continue;
        }
        const isRelation = field.type === "one2many" || field.type === "many2many";
        const value = isRelation
            ? { count: record.data[name]?.count ?? 0 }
            : formatValue(field, record.data[name]);
        if (value === undefined || value === null || value === "") {
            continue;
        }
        row[name] = value;
        if (++columns >= LIMITS.lineColumns) {
            break;
        }
    }
    if (record.resId) {
        row.id = record.resId;
    }
    return row;
}

function isInsideField(node) {
    return Boolean(node.parentElement?.closest("field"));
}

function isHiddenByArch(node, context) {
    for (let current = node; current; current = current.parentElement) {
        if (isTrue(current.getAttribute("invisible"), context)) {
            return true;
        }
    }
    return false;
}

function pageOf(node) {
    const page = node.closest("page");
    return page ? page.getAttribute("string") || page.getAttribute("name") || "" : "";
}

function describeFormFields(controller, record, root) {
    const context = record.evalContextWithVirtualIds;
    const fields = [];
    const seen = new Set();
    for (const node of controller.archInfo.xmlDoc.querySelectorAll("field[field_id]")) {
        if (isInsideField(node)) {
            continue;
        }
        const info = controller.archInfo.fieldNodes[node.getAttribute("field_id")];
        const field = info && record.fields[info.name];
        if (!field || seen.has(info.name) || SKIPPED_TYPES.has(field.type)) {
            continue;
        }
        if (isTrue(info.invisible, context) || isHiddenByArch(node, context)) {
            continue;
        }
        seen.add(info.name);
        const value = formatValue(field, record.data[info.name]);
        if (value === undefined) {
            continue;
        }
        const widget = root?.querySelector(`.o_field_widget[name="${CSS.escape(info.name)}"]`);
        const entry = {
            name: info.name,
            label: truncate(info.string || field.string || info.name, 80),
            type: field.type,
            value,
            on_screen: isShown(widget),
        };
        const tab = pageOf(node);
        if (tab) {
            entry.tab = truncate(tab, 60);
        }
        if (isTrue(info.required, context)) {
            entry.required = true;
        }
        if (isTrue(info.readonly, context)) {
            entry.readonly = true;
        }
        if (record.isFieldInvalid(info.name)) {
            entry.invalid = true;
        }
        fields.push(entry);
    }
    // What is on screen is what the question is most likely about
    fields.sort((a, b) => Number(b.on_screen) - Number(a.on_screen));
    return fields.slice(0, LIMITS.fields);
}

function describeButtons(xmlDoc, context, root) {
    const buttons = [];
    for (const node of xmlDoc.querySelectorAll("button[name], button[special]")) {
        if (isInsideField(node) || isHiddenByArch(node, context)) {
            continue;
        }
        const name = node.getAttribute("name") || node.getAttribute("special");
        const label = node.getAttribute("string") || node.textContent || "";
        const entry = {
            name,
            label: truncate(label || node.getAttribute("title") || name, 80),
            kind: node.getAttribute("type") || "special",
        };
        if (node.closest('div[name="button_box"]')) {
            entry.smart = true;
        }
        const element = root?.querySelector(`button[name="${CSS.escape(name)}"]`);
        entry.on_screen = isShown(element);
        buttons.push(entry);
        if (buttons.length >= LIMITS.buttons) {
            break;
        }
    }
    return buttons;
}

function describeTabs(xmlDoc, context, root) {
    const active = new Set(
        [...(root?.querySelectorAll(".o_notebook_headers .nav-link.active") || [])].map(
            (link) => link.getAttribute("name") || link.textContent.trim()
        )
    );
    const tabs = [];
    for (const node of xmlDoc.querySelectorAll("page")) {
        if (isInsideField(node) || isHiddenByArch(node, context)) {
            continue;
        }
        const name = node.getAttribute("name") || "";
        const label = node.getAttribute("string") || name;
        const entry = { name, label: truncate(label, 60) };
        if (active.has(name) || active.has(label)) {
            entry.active = true;
        }
        tabs.push(entry);
    }
    return tabs;
}

/**
 * @param {Object} controller form controller
 */
export function describeForm(controller) {
    const record = controller.model.root;
    const root = controller.rootRef?.el;
    const context = record.evalContextWithVirtualIds;
    const description = {
        record: {
            id: record.resId || null,
            is_new: Boolean(record.isNew),
            unsaved_changes: Boolean(record.dirty),
        },
        fields: describeFormFields(controller, record, root),
        buttons: describeButtons(controller.archInfo.xmlDoc, context, root),
    };
    const name = record.data.display_name || record.data.name;
    if (typeof name === "string" && name) {
        description.record.name = truncate(name, 80);
    }
    const invalid = description.fields.filter((field) => field.invalid).map((field) => field.name);
    if (invalid.length) {
        description.record.invalid_fields = invalid;
    }
    const tabs = describeTabs(controller.archInfo.xmlDoc, context, root);
    if (tabs.length) {
        description.tabs = tabs;
    }
    return description;
}

function shownColumns(controller, root) {
    const shown = [...(root?.querySelectorAll("thead th[data-name]") || [])].map((cell) =>
        cell.getAttribute("data-name")
    );
    const columns = [];
    for (const column of controller.archInfo.columns || []) {
        if (column.type !== "field" || !column.name) {
            continue;
        }
        if (shown.length ? !shown.includes(column.name) : column.optional === "hide") {
            continue;
        }
        columns.push({ name: column.name, label: truncate(column.label || column.name, 60) });
    }
    return columns;
}

function describeGroups(list) {
    return (list.groups || []).slice(0, LIMITS.rows).map((group) => ({
        label: truncate(group.displayName || String(group.value ?? ""), 60),
        count: group.count,
    }));
}

/**
 * @param {Object} controller list controller
 */
export function describeList(controller) {
    const list = controller.model.root;
    const root = controller.rootRef?.el;
    const columns = shownColumns(controller, root);
    const names = columns.map((column) => column.name);
    const description = { columns, total: list.count ?? null };
    if (list.isGrouped) {
        description.grouped_by = [...(list.groupBy || [])];
        description.groups = describeGroups(list);
    } else {
        description.rows = (list.records || [])
            .slice(0, LIMITS.rows)
            .map((record) => describeRow(record, names));
    }
    const selected = (list.selection || []).map((record) => record.resId).filter(Boolean);
    if (selected.length) {
        description.selected_ids = selected.slice(0, 50);
    }
    const buttons = describeButtons(controller.archInfo.xmlDoc, list.evalContext || {}, root);
    if (buttons.length) {
        description.buttons = buttons;
    }
    return description;
}

/**
 * @param {Object} controller kanban controller
 */
export function describeKanban(controller) {
    const list = controller.model.root;
    const description = { total: list.count ?? null };
    if (list.isGrouped) {
        description.grouped_by = [...(list.groupBy || [])];
        description.groups = describeGroups(list);
    } else {
        description.cards = (list.records || [])
            .slice(0, LIMITS.rows)
            .map((record) => describeRow(record));
    }
    return description;
}

function describeSearch(searchModel) {
    if (!searchModel) {
        return null;
    }
    const filters = [];
    for (const facet of searchModel.facets || []) {
        const values = (facet.values || []).map((value) => truncate(value, 60)).join(", ");
        filters.push(facet.title ? `${truncate(facet.title, 40)}: ${values}` : values);
    }
    const groupBy = [...(searchModel.groupBy || [])];
    if (!filters.length && !groupBy.length) {
        return null;
    }
    return { filters, group_by: groupBy };
}

function texts(selector, limit = LIMITS.notices) {
    const result = [];
    for (const element of document.querySelectorAll(selector)) {
        if (element.closest(".o_nudge") || !isShown(element)) {
            continue;
        }
        const text = truncate(element.textContent, 200);
        if (text && !result.includes(text)) {
            result.push(text);
        }
        if (result.length >= limit) {
            break;
        }
    }
    return result;
}

const DESCRIBERS = { form: describeForm, list: describeList, kanban: describeKanban };

function describeView(entry) {
    const { controller, type } = entry;
    const view = {
        model: controller.props.resModel || controller.model?.root?.resModel || null,
        view_type: type,
        ...DESCRIBERS[type](controller),
    };
    const search = describeSearch(controller.env.searchModel);
    if (search) {
        view.search = search;
    }
    return view;
}

function isMounted(entry) {
    const root = entry.controller.rootRef?.el;
    return Boolean(root && root.isConnected);
}

/**
 * @param {Object[]} views views registered by the patches, oldest first
 * @returns {Object|null} the view the person is looking at: a dialog covers the screen
 */
export function frontView(views) {
    const mounted = views.filter(isMounted);
    const dialogs = mounted.filter((entry) => entry.controller.env.inDialog);
    return dialogs.at(-1) || mounted.filter((entry) => !entry.controller.env.inDialog).at(-1) || null;
}

/**
 * @param {Object} params
 * @param {Object} params.action action service
 * @param {Object[]} params.views views registered by the patches, oldest first
 * @returns {Object} the description of the screen
 */
export function takeSnapshot({ action, views }) {
    const snapshot = { v: 1 };
    const current = action.currentController;
    if (current) {
        const definition = current.action || {};
        snapshot.action = {
            name: truncate(current.displayName || definition.name || "", 80),
            type: definition.type || null,
        };
        if (definition.xml_id) {
            snapshot.action.xml_id = definition.xml_id;
        }
        if (definition.tag) {
            snapshot.action.tag = definition.tag;
        }
        const breadcrumbs = (current.config?.breadcrumbs || [])
            .map((item) => truncate(item.name || "", 60))
            .filter(Boolean);
        if (breadcrumbs.length) {
            snapshot.breadcrumbs = breadcrumbs.slice(-5);
        }
        if (definition.res_model) {
            snapshot.model = definition.res_model;
        }
        if (current.view?.type) {
            snapshot.view_type = current.view.type;
        }
    }

    const mounted = views.filter(isMounted);
    const dialogs = mounted.filter((entry) => entry.controller.env.inDialog);
    const main = mounted.filter((entry) => !entry.controller.env.inDialog).at(-1);
    // A dialog covers the screen: it is what the person is looking at
    const front = dialogs.at(-1) || main;
    if (front) {
        try {
            Object.assign(snapshot, describeView(front));
        } catch (error) {
            snapshot.incomplete = truncate(error.message || "error", 120);
        }
        if (dialogs.length) {
            snapshot.in_dialog = true;
            if (main) {
                snapshot.behind_dialog = {
                    model: main.controller.props.resModel || null,
                    view_type: main.type,
                };
            }
        }
    }

    const dialogTitles = texts(".modal:not(.o_inactive_modal) .modal-title", 2);
    if (dialogTitles.length) {
        snapshot.dialog_title = dialogTitles.at(-1);
    }
    const notices = texts(".o_notification_manager .o_notification, .o_form_view .alert");
    if (notices.length) {
        snapshot.notices = notices;
    }
    return snapshot;
}

/**
 * @param {Object} snapshot
 * @returns {string} one line telling the person what Nudge is looking at
 */
export function summarize(snapshot) {
    const parts = [];
    const last = snapshot.breadcrumbs?.at(-1);
    if (snapshot.record?.name) {
        parts.push(snapshot.record.name);
    } else if (last) {
        parts.push(last);
    } else if (snapshot.action?.name) {
        parts.push(snapshot.action.name);
    }
    if (snapshot.dialog_title && !parts.includes(snapshot.dialog_title)) {
        parts.unshift(snapshot.dialog_title);
    }
    return parts.join(" · ");
}

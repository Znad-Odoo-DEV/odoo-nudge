/** @odoo-module **/

import { evaluateBooleanExpr } from "@web/core/py_js/py";
import { formatMonetary } from "@web/views/fields/formatters";

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
    lineRows: 50,
    lineColumns: 8,
    names: 5,
    text: 160,
    notices: 5,
};

const HIDDEN = "[hidden]";
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
export function formatValue(field, value, data) {
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
            return typeof value === "number" ? value : null;
        case "monetary":
            return typeof value === "number" ? money(value, field, data) : null;
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

/**
 * An amount as the person reads it, with its currency symbol; the bare number
 * when the currency of the record is not known.
 */
function money(value, field, data, currencyId) {
    try {
        const text = formatMonetary(value, { data, field, currencyId });
        return text ? truncate(text, 40) : value;
    } catch {
        return value;
    }
}

/**
 * The totals box of an invoice or an order (widget account-tax-totals-field):
 * the total, the untaxed amount and the taxes, as shown on screen. Odoo 17 and
 * 18 hold them as amount_total / amount_untaxed, 19 as *_amount_currency.
 */
export function describeTaxTotals(totals, field, data) {
    if (!totals || typeof totals !== "object") {
        return null;
    }
    const currencyId = totals.currency_id ?? totals.currency?.id;
    const show = (formatted, ...values) => {
        const value = values.find((item) => typeof item === "number");
        if (typeof formatted === "string" && formatted) {
            return truncate(formatted, 40);
        }
        return value === undefined ? undefined : money(value, field, data, currencyId);
    };
    const result = {};
    const total = show(
        totals.formatted_amount_total,
        totals.total_amount_currency,
        totals.amount_total
    );
    const untaxed = show(
        totals.formatted_amount_untaxed,
        totals.base_amount_currency,
        totals.amount_untaxed
    );
    if (total !== undefined) {
        result.total = total;
    }
    if (untaxed !== undefined) {
        result.untaxed = untaxed;
    }
    const taxValue = typeof totals.tax_amount_currency === "number"
        ? totals.tax_amount_currency
        : typeof totals.amount_total === "number" && typeof totals.amount_untaxed === "number"
          ? totals.amount_total - totals.amount_untaxed
          : undefined;
    const tax = show(undefined, taxValue);
    if (tax !== undefined) {
        result.tax = tax;
    }
    const groups = [];
    const lists = Array.isArray(totals.subtotals) ? totals.subtotals : [];
    for (const subtotal of lists) {
        for (const group of subtotal.tax_groups || []) {
            const amount = show(
                group.formatted_tax_group_amount,
                group.tax_amount_currency,
                group.tax_group_amount
            );
            const name = group.group_name ?? group.tax_group_name;
            if (name && amount !== undefined) {
                groups.push({ name: truncate(String(name), 60), amount });
            }
        }
    }
    if (groups.length) {
        result.taxes = groups.slice(0, LIMITS.notices);
    }
    return Object.keys(result).length ? result : null;
}

function formatList(list) {
    const records = list.records || [];
    const count = list.count ?? records.length;
    const result = { count };
    const rows = records.slice(0, LIMITS.lineRows).map((record) => describeRow(record));
    if (rows.length) {
        result.rows = rows;
    }
    if (count > rows.length && rows.length) {
        result.more = count - rows.length;
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
        const value = isPasswordField(info) || isSecretParameter(record, name)
            ? (record.data[name] ? HIDDEN : null)
            : isRelation
            ? { count: record.data[name]?.count ?? 0 }
            : formatValue(field, record.data[name], record.data);
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

/** A condition written in the view (not a plain true or false), kept short */
function conditionOf(expression) {
    if (typeof expression !== "string" || !expression || /^(true|1|false|0)$/i.test(expression)) {
        return "";
    }
    return truncate(expression, 200);
}

// The field the person was last in: asked about with no name, it is the one meant
let lastFocusedField = "";
document.addEventListener("focusin", (event) => {
    const widget = event.target?.closest?.(".o_field_widget[name]");
    if (widget && !widget.closest(".o_nudge")) {
        lastFocusedField = widget.getAttribute("name");
    }
});

// A field the view shows as a password holds a credential, whatever it is called
function isPasswordField(info, node) {
    const attr = node?.getAttribute?.("password") ?? info?.attrs?.password ?? info?.password;
    return info?.widget === "password" || (attr !== undefined && attr !== null && !/^(false|0)?$/i.test(String(attr)));
}

// A system parameter keeps its value to itself unless its key is known and names no credential (database.secret)
const SECRET_KEY = /secret|token|key|password|passwd|credential|oauth/i;
function isSecretParameter(record, name) {
    if (record.resModel !== "ir.config_parameter" || name !== "value") {
        return false;
    }
    const key = record.data.key;
    return !(typeof key === "string" && key && !SECRET_KEY.test(key));
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
        const isTotals = info?.widget === "account-tax-totals-field";
        if (!field || seen.has(info.name) || (SKIPPED_TYPES.has(field.type) && !isTotals)) {
            continue;
        }
        if (isTrue(info.invisible, context) || isHiddenByArch(node, context)) {
            continue;
        }
        seen.add(info.name);
        const secret = isPasswordField(info, node) || isSecretParameter(record, info.name);
        const value = secret
            ? (record.data[info.name] ? HIDDEN : null)
            : isTotals
              ? describeTaxTotals(record.data[info.name], field, record.data)
              : formatValue(field, record.data[info.name], record.data);
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
        if (secret) {
            entry.masked = true;
        }
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
        // Why, for the explanations that need no AI: the condition of the view and the record linked
        const required = conditionOf(info.required);
        if (required) {
            entry.required_expr = required;
        }
        const readonly = conditionOf(info.readonly);
        if (readonly) {
            entry.readonly_expr = readonly;
        }
        const linked = record.data[info.name];
        const linkedId = Array.isArray(linked) ? linked[0] : linked?.id;
        if (field.type === "many2one" && Number.isInteger(linkedId)) {
            entry.rid = linkedId;
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
        const tooltip = node.getAttribute("title") || node.getAttribute("help") || "";
        if (tooltip) {
            entry.help = truncate(tooltip, 160);
        }
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
    if (description.fields.some((field) => field.name === lastFocusedField)) {
        description.focused_field = lastFocusedField;
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
const MAX_IMPORT_COLUMNS = 40;
const STUDIO_TABS = { o_web_studio_new: "new", o_web_studio_view: "view", o_web_studio_properties: "properties" };

/**
 * Studio's editor, when the person is inside it: which view they edit and
 * which tab of the sidebar is open.
 */
function describeStudio() {
    const editor = document.querySelector(".o_web_studio_editor, .o_web_studio_editor_manager");
    if (!editor || !isShown(editor)) {
        return null;
    }
    const result = { editing: true };
    const renderer = document.querySelector(".o_web_studio_view_renderer");
    const kind = renderer && [...renderer.classList].find((cls) => /^o_web_studio_(\w+)_view_editor$/.test(cls));
    if (kind) {
        result.view_type = kind.replace(/^o_web_studio_(\w+)_view_editor$/, "$1");
    }
    for (const [cls, tab] of Object.entries(STUDIO_TABS)) {
        const element = document.querySelector(`.o_web_studio_sidebar .${cls}`);
        if (element && (element.classList.contains("active") || element.getAttribute("aria-selected") === "true")) {
            result.tab = tab;
        }
    }
    return result;
}

/**
 * The Import screen: what is imported into which model, how its columns are
 * matched, and the errors it shows.
 */
function describeImport(current) {
    const params = current.action?.params || {};
    const result = { model: params.model || params.active_model || null };
    const file = document.querySelector(".o_import_data_sidepanel .fst-italic.truncate");
    if (file) {
        const extension = file.nextElementSibling?.classList.contains("fst-italic")
            ? file.nextElementSibling.textContent.trim()
            : "";
        result.file = truncate(file.textContent.trim() + extension, 120);
    }
    const columns = [];
    for (const column of document.querySelectorAll(".o_import_grid_col")) {
        if (columns.length >= MAX_IMPORT_COLUMNS) {
            break;
        }
        const header =
            column.querySelector(".o_import_grid_header, .fw-bold, strong, th")?.textContent?.trim() ||
            column.firstElementChild?.textContent?.trim() ||
            "";
        const select = column.querySelector("select");
        const field = select
            ? select.selectedOptions[0]?.textContent?.trim() || ""
            : column.querySelector("input")?.value || "";
        if (header) {
            columns.push({ header: truncate(header, 60), field: truncate(field, 80) });
        }
    }
    if (columns.length) {
        result.columns = columns;
    }
    const errors = texts(".o_import_report, .o_import_action .alert, .o_import_data_sidepanel .alert", 20);
    if (errors.length) {
        result.errors = errors;
    }
    return result;
}

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
        if (definition.tag === "import") {
            snapshot.import = describeImport(current);
            if (snapshot.import.model) {
                snapshot.model = snapshot.import.model;
            }
        }
    }
    const studio = describeStudio();
    if (studio) {
        snapshot.studio = studio;
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
    const errors = texts(".o_notification.border-danger, .o_error_dialog .modal-body", 1);
    if (errors.length) {
        snapshot.error = errors[0];
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
    if (snapshot.import) {
        parts.push(snapshot.import.file ? `Import: ${snapshot.import.file}` : "Import");
    } else if (snapshot.studio) {
        parts.push(snapshot.studio.view_type ? `Studio: ${snapshot.studio.view_type} view` : "Studio");
    } else if (snapshot.record?.name) {
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

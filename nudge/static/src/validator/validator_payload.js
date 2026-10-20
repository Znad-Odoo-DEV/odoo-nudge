/** @odoo-module **/

/**
 * The unsaved values of a form, as the input validator wants them, the way the
 * snapshot of the screen describes a form (see ../context/snapshot.js) but with
 * the numbers as numbers and the linked records with their id. Only the fields
 * and the columns of the lines that the server asked for are read, and
 * nothing is read from the server: it is what the web client already holds.
 */
export const MAX_LINES = 60;

/**
 * @param {Object} field definition of the field
 * @param {any} value value held by the record
 * @returns {any} the value as plain data, null when it is empty or not described
 */
export function plainValue(field, value) {
    if (value === undefined || value === null || value === false) {
        return field.type === "boolean" ? false : null;
    }
    switch (field.type) {
        case "many2one":
            if (Array.isArray(value)) {
                return { id: value[0], name: String(value[1] ?? "") };
            }
            return Number.isInteger(value.id)
                ? { id: value.id, name: String(value.display_name ?? value.displayName ?? "") }
                : null;
        case "date":
            return value.toISODate ? value.toISODate() : String(value);
        case "datetime":
            return value.toISO ? value.toISO({ suppressMilliseconds: true }) : String(value);
        case "integer":
        case "float":
        case "monetary":
            return typeof value === "number" ? value : null;
        case "boolean":
            return Boolean(value);
        case "char":
        case "text":
        case "selection":
            return String(value);
        default:
            return null;
    }
}

/**
 * @param {Object} record the root record of a form
 * @param {{ fields: string[], lines: Object<string, string[]> }} spec what the server asked for
 * @returns {Object} the payload of `nudge.validator.check`
 */
export function collectPayload(record, spec) {
    const fields = {};
    for (const name of spec.fields || []) {
        const field = record.fields[name];
        if (field && record.activeFields[name]) {
            fields[name] = plainValue(field, record.data[name]);
        }
    }
    const lines = {};
    for (const [name, columns] of Object.entries(spec.lines || {})) {
        const records = record.data[name]?.records;
        if (!records || !record.activeFields[name]) {
            continue;
        }
        lines[name] = records.slice(0, MAX_LINES).map((line) => {
            const row = {};
            for (const column of columns) {
                const field = line.fields[column];
                if (field && line.activeFields?.[column]) {
                    row[column] = plainValue(field, line.data[column]);
                }
            }
            return row;
        });
    }
    return {
        model: record.resModel,
        id: record.resId || null,
        is_new: Boolean(record.isNew),
        changed: Boolean(record.dirty),
        fields,
        lines,
    };
}

"""The columns of a file matched to the fields of a model, without any AI.

A column is matched by its header (the technical name of a field, its label
in the language of the person or in English, a known synonym, or a close
spelling) and by what its sample values look like (dates, numbers, emails,
phones, yes/no, a choice of a selection, a name of a record). The same
matcher serves the panel without a provider, in private mode, and the AI as
a tool. Nothing is written: the file is read by the importer of Odoo and
tried by the dry run of ``importing``.
"""
import csv
import difflib
import io
import re

from . import base
from .commands import fold
from .importing import attached_file, dry_run, read_columns
from .records import MODEL_PATTERN, Refused, cut, get_model, refusing

THRESHOLD = 50
HIGH, MEDIUM = 85, 65
MAX_SAMPLE = 60
MAX_CHOICES = 5

# The words a header may use for the fields of the common models, in English
# and in Arabic. Keyed by the technical name of the field, which is the same
# on Odoo 17, 18 and 19 for these; a field a version does not have is ignored.
SYNONYMS = {
    "res.partner": {
        "name": ["customer", "customer name", "client", "client name", "company", "company name", "full name",
                 "contact", "contact name", "vendor", "supplier", "الاسم", "اسم العميل", "العميل", "الشركة", "المورد"],
        "email": ["e-mail", "mail", "email address", "e mail", "البريد", "البريد الالكتروني", "ايميل"],
        "phone": ["telephone", "tel", "phone number", "landline", "هاتف", "الهاتف", "رقم الهاتف", "تلفون"],
        "mobile": ["mobile phone", "cell", "cellphone", "whatsapp", "جوال", "موبايل", "محمول", "الجوال"],
        "vat": ["tax id", "tax number", "trn", "vat number", "tax registration", "الرقم الضريبي"],
        "street": ["address", "address 1", "address line 1", "العنوان", "عنوان"],
        "street2": ["address 2", "address line 2"],
        "city": ["town", "المدينة", "مدينة"],
        "zip": ["postal code", "postcode", "zip code", "الرمز البريدي"],
        "country_id": ["country", "الدولة", "البلد"],
        "website": ["url", "web", "web site", "الموقع"],
        "ref": ["reference", "code", "customer code", "internal reference", "الكود", "رمز"],
        "function": ["job title", "job position", "position", "المسمى الوظيفي"],
        "comment": ["notes", "note", "remarks", "ملاحظات"],
    },
    "product.template": {
        "name": ["product", "product name", "item", "item name", "اسم المنتج", "المنتج", "الصنف", "اسم الصنف"],
        "default_code": ["sku", "code", "item code", "internal reference", "reference", "product code",
                         "الكود", "كود الصنف", "رمز"],
        "barcode": ["ean", "upc", "bar code", "gtin", "الباركود"],
        "list_price": ["price", "sales price", "sale price", "selling price", "unit price", "سعر البيع", "السعر"],
        "standard_price": ["cost", "unit cost", "cost price", "purchase price", "التكلفة", "سعر التكلفة"],
        "categ_id": ["category", "product category", "الفئة", "التصنيف"],
        "uom_id": ["unit", "uom", "unit of measure", "الوحدة"],
        "taxes_id": ["taxes", "customer taxes", "vat", "الضريبة"],
        "weight": ["weight kg", "الوزن"],
        "volume": ["volume m3", "الحجم"],
        "type": ["product type", "نوع المنتج"],
        "detailed_type": ["product type", "نوع المنتج"],
    },
    "sale.order": {
        "partner_id": ["customer", "client", "customer name", "العميل"],
        "name": ["order", "order number", "order reference", "number", "quotation", "quotation number", "رقم الطلب"],
        "date_order": ["date", "order date", "تاريخ الطلب", "التاريخ"],
        "user_id": ["salesperson", "sales rep", "seller", "المندوب", "مندوب المبيعات"],
        "client_order_ref": ["customer reference", "po number", "customer po", "مرجع العميل"],
        "payment_term_id": ["payment terms", "terms", "شروط الدفع"],
        "note": ["notes", "terms and conditions", "ملاحظات"],
    },
    "sale.order.line": {
        "order_id": ["order", "order reference", "quotation", "sales order", "order number", "رقم الطلب"],
        "product_id": ["product", "item", "product name", "المنتج", "الصنف"],
        "product_uom_qty": ["quantity", "qty", "ordered quantity", "الكمية"],
        "price_unit": ["price", "unit price", "السعر", "سعر الوحدة"],
        "discount": ["discount %", "disc", "discount percent", "الخصم"],
        "name": ["description", "line description", "الوصف"],
        "tax_id": ["taxes", "tax", "vat", "الضريبة"],
    },
    "purchase.order": {
        "partner_id": ["vendor", "supplier", "vendor name", "supplier name", "المورد"],
        "name": ["order", "order number", "po", "po number", "purchase order", "رقم الطلب"],
        "date_order": ["date", "order date", "تاريخ الطلب", "التاريخ"],
        "partner_ref": ["vendor reference", "supplier reference", "مرجع المورد"],
        "date_planned": ["receipt date", "expected date", "delivery date", "تاريخ الاستلام"],
        "notes": ["note", "terms", "ملاحظات"],
    },
    "purchase.order.line": {
        "order_id": ["order", "purchase order", "po", "po number", "order reference"],
        "product_id": ["product", "item", "المنتج", "الصنف"],
        "product_qty": ["quantity", "qty", "الكمية"],
        "price_unit": ["price", "unit price", "السعر"],
        "name": ["description", "الوصف"],
    },
    "account.move": {
        "partner_id": ["customer", "vendor", "supplier", "partner", "client", "العميل", "المورد"],
        "invoice_date": ["date", "invoice date", "bill date", "تاريخ الفاتورة", "التاريخ"],
        "invoice_date_due": ["due date", "payment due", "تاريخ الاستحقاق"],
        "name": ["number", "invoice number", "invoice no", "رقم الفاتورة"],
        "ref": ["reference", "vendor bill reference", "supplier invoice", "المرجع"],
        "currency_id": ["currency", "العملة"],
        "journal_id": ["journal", "دفتر اليومية"],
        "payment_reference": ["payment ref", "memo", "مرجع الدفع"],
        "narration": ["notes", "note", "terms", "ملاحظات"],
        "move_type": ["type", "invoice type", "النوع"],
    },
    "hr.employee": {
        "name": ["employee", "employee name", "full name", "الموظف", "اسم الموظف", "الاسم"],
        "work_email": ["email", "work email", "e-mail", "mail", "البريد الالكتروني", "ايميل"],
        "work_phone": ["phone", "work phone", "telephone", "الهاتف"],
        "mobile_phone": ["mobile", "cell", "mobile phone", "جوال", "الجوال", "موبايل"],
        "job_title": ["title", "position", "job position", "المسمى الوظيفي", "الوظيفة"],
        "department_id": ["department", "dept", "القسم"],
        "parent_id": ["manager", "direct manager", "line manager", "المدير", "المدير المباشر"],
        "job_id": ["job", "job id"],
        "birthday": ["birth date", "date of birth", "dob", "birthday", "تاريخ الميلاد"],
        "gender": ["sex", "الجنس"],
        "identification_id": ["id number", "national id", "identification number", "emirates id", "رقم الهوية"],
        "passport_id": ["passport", "passport number", "رقم الجواز", "جواز السفر"],
        "barcode": ["badge", "badge id", "بطاقة"],
        "registration_number": ["employee id", "employee number", "employee code", "رقم الموظف"],
        "country_id": ["nationality", "country", "الجنسية"],
        "company_id": ["company", "الشركة"],
    },
    "stock.quant": {
        "product_id": ["product", "item", "sku", "product name", "المنتج", "الصنف"],
        "location_id": ["location", "warehouse location", "bin", "الموقع"],
        "inventory_quantity_auto_apply": ["quantity", "qty", "on hand", "on hand quantity", "counted quantity",
                                         "inventoried quantity", "الكمية"],
        "inventory_quantity": ["counted quantity", "counted", "الكمية المعدودة"],
        "quantity": ["qty", "on hand", "on hand quantity", "الكمية"],
        "lot_id": ["lot", "serial", "lot/serial number", "serial number", "lot number", "الدفعة"],
        "package_id": ["package", "pack", "الطرد"],
    },
}
# product.product imports the same columns as the template
SYNONYMS["product.product"] = SYNONYMS["product.template"]

# A field whose name fits the kind of value in the column, when the header says little
VALUE_HINTS = {
    "email": ("email", "work_email", "email_from"),
    "phone": ("phone", "mobile", "mobile_phone", "work_phone"),
}

DATE_PATTERNS = (
    re.compile(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}$"),
    re.compile(r"^\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}$"),
)
DATETIME_PATTERN = re.compile(r"^\S+[ T]\d{1,2}:\d{2}(:\d{2})?(\.\d+)?\s?(am|pm)?$", re.IGNORECASE)
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
PHONE_PATTERN = re.compile(r"^(\+|00)?[\d\s\-()./]{6,20}$")
INTEGER_PATTERN = re.compile(r"^[-+]?\d{1,3}(,\d{3})*$|^[-+]?\d+$")
FLOAT_PATTERN = re.compile(r"^[-+]?(\d{1,3}(,\d{3})+|\d+)?[.,]?\d+$")
BOOLEANS = frozenset(fold(word) for word in (
    "yes", "no", "true", "false", "y", "n", "1", "0", "نعم", "لا", "صح", "خطا"))
TEXT_FIELDS = ("char", "text", "html")
NUMBER_FIELDS = ("integer", "float", "monetary")


def kinds_of(value):
    """:return: the kinds of data one cell may be: date, datetime, email,
    phone, integer, float, boolean; every cell is also text"""
    text = str(value).strip()
    kinds = {"text"}
    if not text:
        return kinds
    if EMAIL_PATTERN.match(text):
        kinds.add("email")
        return kinds
    if any(pattern.match(text) for pattern in DATE_PATTERNS):
        kinds.add("date")
        kinds.add("datetime")
        return kinds
    if DATETIME_PATTERN.match(text) and any(char.isdigit() for char in text):
        first = text.replace("T", " ").split(" ")[0]
        if any(pattern.match(first) for pattern in DATE_PATTERNS):
            kinds.add("datetime")
            return kinds
    digits = re.sub(r"\D", "", text)
    if INTEGER_PATTERN.match(text):
        kinds.update(("integer", "float"))
    elif FLOAT_PATTERN.match(text):
        kinds.add("float")
    if PHONE_PATTERN.match(text) and len(digits) >= 7 and (text.startswith(("+", "00", "0")) or "-" in text
                                                         or " " in text or "(" in text):
        kinds.add("phone")
    if fold(text) in BOOLEANS:
        kinds.add("boolean")
    return kinds


def column_kinds(samples):
    """:return: the kinds all the sample values of a column may be, or None
    when the column holds nothing to judge from"""
    values = [value for value in samples if value is not None and str(value).strip()]
    if not values:
        return None
    kinds = None
    for value in values[:MAX_SAMPLE]:
        found = kinds_of(value)
        kinds = found if kinds is None else kinds & found
    return kinds or {"text"}


def compatible(field, kinds, samples):
    """:return: whether the values of a column may go into ``field``"""
    kind = field["type"]
    if kind == "selection":
        options = field.get("options") or []
        if not options:
            return True
        known = {fold(str(key)) for key, _label in options} | {fold(str(label)) for _key, label in options}
        return all(fold(str(value)) in known for value in samples if value is not None and str(value).strip())
    if kinds is None:
        return True
    if kind in NUMBER_FIELDS:
        return "float" in kinds or "integer" in kinds
    if kind == "date":
        return "date" in kinds
    if kind == "datetime":
        return "datetime" in kinds or "date" in kinds
    if kind == "boolean":
        return "boolean" in kinds
    if kind in ("many2one", "many2many"):
        # Looked up by name: a date or a decimal number names nothing
        return not kinds & {"date", "datetime", "email"}
    return True


def importable_fields(env, model):
    """:return: the fields of ``model`` the person may import into, at the
    top level: ``{"name", "label" (their language), "label_en", "type",
    "required", "options"}``. The importer of Odoo decides what is importable."""
    Import = env["base_import.import"]
    try:
        tree = Import.get_fields_tree(model._name, depth=1)
    except TypeError:
        tree = Import.get_fields_tree(model._name)
    english = {}
    try:
        for field in Import.with_context(lang="en_US").get_fields_tree(model._name, depth=1):
            english[field["name"]] = field["string"]
    except Exception:  # noqa: BLE001 — English is only one more spelling to match
        pass
    fields = []
    for field in tree:
        name = field["name"]
        if field.get("type") in ("one2many", "binary", "properties") or name in (".id",):
            continue
        definition = model._fields.get(name)
        if definition is not None and not definition.store:
            # A field only for searching or showing holds nothing to import
            continue
        entry = {"name": name, "label": field.get("string") or name, "label_en": english.get(name, ""),
                 "type": field.get("type") or "char", "required": bool(field.get("required"))}
        if definition is not None and definition.type == "selection":
            try:
                entry["options"] = list(definition._description_selection(env))
            except Exception:  # noqa: BLE001
                entry["options"] = []
        fields.append(entry)
    return fields


def _names(field):
    """:return: the spellings of a field a header may use, as fold() writes them"""
    spellings = {fold(field["name"]), fold(field["name"].replace("_", " ")), fold(field["label"]),
                 fold(field["label_en"])}
    if field["name"] == "id":
        spellings.update((fold("external id"), fold("xml id")))
    spellings.discard("")
    return spellings


def _similar(header, spellings):
    """:return: how alike a header and the nearest spelling are, 0 to 1"""
    best = 0.0
    words = set(header.split())
    for spelling in spellings:
        if header == spelling:
            return 1.0
        ratio = difflib.SequenceMatcher(None, header, spelling).ratio()
        other = set(spelling.split())
        # "customer name" holds "customer": a whole word of the other, not a few letters
        if words and other and (words <= other or other <= words) and min(len(word) for word in words | other) > 2:
            ratio = max(ratio, 0.78)
        best = max(best, ratio)
    return best


def score_pair(model_name, header, kinds, samples, field):
    """:return: ``(confidence 0-100, reason)`` of a column for a field"""
    folded = fold(header)
    if not folded:
        return 0, ""
    spellings = _names(field)
    synonyms = {fold(word) for word in SYNONYMS.get(model_name, {}).get(field["name"], ())}
    if folded in spellings:
        score, reason = 100, "the header is the name of the field"
    elif folded in synonyms:
        score, reason = 92, "a usual name for this field"
    else:
        ratio = _similar(folded, spellings | synonyms)
        score = round(82 * ratio) if ratio >= 0.7 else 0
        reason = "the header is close to the name of the field" if score else ""
    if score < THRESHOLD and kinds and field["name"] in VALUE_HINTS.get(
            next((kind for kind in ("email", "phone") if kind in kinds), ""), ()):
        score, reason = max(score, 60), "the values look like %s" % ("emails" if "email" in kinds else "phone numbers")
    if score and not compatible(field, kinds, samples):
        return min(score, 40), "the values do not fit a field of this kind"
    if score and kinds:
        strong = (field["type"] == "date" and "date" in kinds) or (field["type"] in NUMBER_FIELDS and "integer" in kinds
                  and "phone" not in kinds)
        if strong and score < 100:
            score = min(score + 4, 99)
    return score, reason


def match_columns(env, model, headers, samples, importer_fields=None, fields=None):
    """Match every column of a file to a field of ``model``.

    :param samples: per column, up to five sample values
    :param importer_fields: what the importer of Odoo matched by itself, per
        column, as a fallback for paths such as ``partner_id/name``
    :return: one entry per column: ``{"index", "column", "field" (name or
        False), "label", "confidence", "level", "reason", "sample", "type"}``
    """
    fields = fields if fields is not None else importable_fields(env, model)
    by_name = {field["name"]: field for field in fields}
    columns = []
    pairs = []
    for index, header in enumerate(headers):
        values = samples[index] if index < len(samples) else []
        kinds = column_kinds(values)
        columns.append({"index": index, "column": str(header), "field": False, "label": "", "confidence": 0,
                        "level": "none", "reason": "", "type": "",
                        "sample": [cut(str(value), 40) for value in list(values)[:3]]})
        for field in fields:
            score, reason = score_pair(model._name, str(header), kinds, values, field)
            if score >= THRESHOLD:
                pairs.append((-score, index, field["name"], reason))
    pairs.sort()
    taken_columns, taken_fields = set(), set()
    for negative, index, name, reason in pairs:
        if index in taken_columns or name in taken_fields:
            continue
        taken_columns.add(index)
        taken_fields.add(name)
        field = by_name[name]
        columns[index].update(field=name, label=field["label"], confidence=-negative, reason=reason, type=field["type"])
    for column in columns:
        path = importer_fields[column["index"]] if importer_fields and column["index"] < len(importer_fields) else None
        if not column["field"] and path:
            head = path.split("/")[0]
            if head not in taken_fields:
                taken_fields.add(head)
                field = by_name.get(head)
                column.update(field=path, label=field["label"] if field else path, confidence=70,
                              reason="Odoo's importer reads this header", type=field["type"] if field else "")
        column["level"] = ("none" if not column["field"] else "high" if column["confidence"] >= HIGH
                           else "medium" if column["confidence"] >= MEDIUM else "low")
    return columns


def apply_changes(columns, fields, mapping, model_name):
    """Put what the person chose over what was matched, in place.
    :raise Refused: for a field the model does not have"""
    for entry in mapping or []:
        index = entry.get("index")
        if isinstance(index, int) and 0 <= index < len(columns):
            path = str(entry.get("field") or "")
            field = next((other for other in fields if other["name"] == path.split("/")[0]), None)
            if path and field is None:
                raise Refused(f"{model_name} has no field {path!r}.")
            columns[index].update(
                field=path or False, label=field["label"] if field else "", confidence=100 if path else 0,
                level="chosen" if path else "none", reason="chosen by you" if path else "",
                type=field["type"] if field else "")
    return columns


def header_for(column, by_name):
    """:return: the header that makes the standard importer map the column to
    its field: the label, or the technical name when two fields share it"""
    path = column["field"]
    field = by_name.get(path.split("/")[0])
    if "/" in path or field is None:
        return path
    labels = [other for other in by_name.values() if other["label"] == field["label"]]
    return field["label"] if len(labels) == 1 else path


def corrected_csv(env, model, name, data, mimetype, mapping, sheet="", has_headers=True):
    """:return: a CSV whose header row names the fields matched, the rows
    of the file as they are: the standard importer maps it by itself"""
    found = read_columns(env, model, name, data, mimetype, sheet, has_headers, with_rows=True)
    if found["rows"] is None:
        raise Refused("The file could not be read again.")
    fields = importable_fields(env, model)
    by_name = {field["name"]: field for field in fields}
    columns = match_columns(env, model, found["headers"], found["samples"], found["fields"], fields)
    apply_changes(columns, fields, mapping, model._name)
    out = [header_for(column, by_name) if column["field"] else str(column["column"]) for column in columns]
    rows = found["rows"][1:] if has_headers else found["rows"]
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(out)
    for row in rows:
        writer.writerow(["" if cell is None or cell is False else cell for cell in row])
    return buffer.getvalue().encode("utf-8")


def candidate_models(env, headers, samples):
    """:return: ``[{"model", "label", "score"}]``, the common models the
    person may import into, the best fitting the file first"""
    ranked = []
    for name in SYNONYMS:
        if name not in env:
            continue
        try:
            model = get_model(env, name, "create")
            columns = match_columns(env, model, headers, samples)
        except (Refused, Exception):  # noqa: BLE001 — a model that cannot be read is not offered
            continue
        score = sum(column["confidence"] for column in columns if column["field"]) / max(len(columns), 1)
        ranked.append({"model": name, "label": model._description, "score": round(score)})
    ranked.sort(key=lambda item: -item["score"])
    return ranked[:MAX_CHOICES]


def card(env, model_name, name, data, mimetype, mapping=None, sheet="", check=False, has_headers=True):
    """What the panel shows: the mapping of the columns, and when asked the
    result of the dry run. Nothing is saved.

    :param mapping: ``[{"index", "field"}]`` the person changed
    :return: a dict; with ``"choose_model"`` alone when the model is unknown"""
    if "base_import.import" not in env:
        raise Refused("The Import app (base_import) is not installed here: nothing can be tried.")
    if not model_name:
        # Any common model will do to read the file; the headers pick the choices
        reader = get_model(env, next(candidate for candidate in SYNONYMS if candidate in env), "create")
        found = read_columns(env, reader, name, data, mimetype, sheet, has_headers)
        return {"choose_model": candidate_models(env, found["headers"], found["samples"]), "file": name}
    model = get_model(env, model_name, "create")
    found = read_columns(env, model, name, data, mimetype, sheet, has_headers)
    fields = importable_fields(env, model)
    columns = match_columns(env, model, found["headers"], found["samples"], found["fields"], fields)
    apply_changes(columns, fields, mapping, model._name)
    result = {
        "model": model._name, "model_label": model._description, "file": name, "columns": columns,
        "fields": [{"name": field["name"], "label": field["label"], "type": field["type"]} for field in fields],
        "rows": (found["count"] - (1 if has_headers else 0)) if isinstance(found["count"], int) else None,
        "missing": [field["label"] for field in fields if field["required"]
                    and field["name"] not in {column["field"] for column in columns}],
    }
    if check:
        chosen = [{"index": column["index"], "field": column["field"] or ""} for column in columns]
        trial = dry_run(env, model, name, data, mimetype, chosen, sheet, has_headers)
        result["check"] = {"problems": trial["problems"], "errors": trial["errors"], "records": trial["ids"],
                           "summary": trial["report"][0]}
    return result


@base.register
class MatchImportColumns(base.Tool):
    name = "match_import_columns"
    description = (
        "Match the columns of the Excel or CSV file the person sent to the fields of a model, "
        "without any AI: by the header (field name, label, usual names, close spelling) and by "
        "what the values look like. Cheap. It gives a first mapping with a confidence for each "
        "column; correct what is doubtful, then pass the mapping to test_import to try the file."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["model"],
        "properties": {
            "model": {"type": "string", "pattern": MODEL_PATTERN,
                      "description": "Technical name of the model imported into."},
            "sheet": {"type": "string", "maxLength": 80, "description": "For an Excel file with several sheets."},
        },
    }

    @refusing
    def run(self, env, arguments, screen):
        model = get_model(env, arguments["model"], "create")
        name, data, mimetype = attached_file(env)
        result = card(env, model._name, name, data, mimetype, sheet=arguments.get("sheet") or "")
        lines = [f"{name!r} into {model._description} ({model._name}), {result['rows']} data row(s):"]
        for column in result["columns"]:
            sample = ", ".join(column["sample"])
            lines.append(
                f"- {column['column']!r} → {column['field'] or 'no field'}"
                + (f" ({column['confidence']}%, {column['reason']})" if column["field"] else "")
                + (f" [e.g. {sample}]" if sample else ""))
        if result["missing"]:
            lines.append("Required fields with no column: " + ", ".join(result["missing"]))
        lines.append("Doubtful (under 65%) or missing columns: check them with the person, then call "
                     "test_import with the mapping.")
        return base.ToolResult("\n".join(lines))

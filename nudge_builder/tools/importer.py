"""Import of content: the columns of every kind, the Excel/CSV reader and writer, and the matching of pictures to
records by file name. Pure functions: no record is read or written here; the wizard does that as the person."""
import csv
import io
import re
import unicodedata

MAX_ROWS = 500
MAX_CELL = 5000
MAX_FILE = 5 * 1024 * 1024
MAX_PICTURE = 10 * 1024 * 1024
PICTURE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")
TRUE = {"1", "true", "yes", "y", "x", "نعم"}

# kind -> content model of the builder (contract section P)
MODELS = {
    "project": "nudge.builder.project",
    "product": "nudge.builder.product",
    "service": "nudge.builder.service",
    "member": "nudge.builder.member",
    "testimonial": "nudge.builder.testimonial",
    "faq": "nudge.builder.faq",
    "location": "nudge.builder.location",
}

# (field, English header, Arabic header, type). type: char, text, int, float, bool, sel:<a|b|c>.
# A field that the model translates also has a "<field>_ar" column, written under the Arabic language.
_NAME = ("name", "Name", "الاسم", "char")
_SUMMARY = ("summary", "Summary", "الملخص", "char")
_DESCRIPTION = ("description", "Description", "الوصف", "text")
_PICTURE = ("picture", "Picture file name", "اسم ملف الصورة", "char")
COLUMNS = {
    "project": [_NAME, _SUMMARY, _DESCRIPTION, ("location", "Location", "الموقع", "char"),
                ("year", "Year", "السنة", "int"), ("client", "Client", "العميل", "char"),
                ("category", "Category", "الفئة", "char"),
                ("status", "Status (planned/ongoing/done)", "الحالة (planned/ongoing/done)", "sel:planned|ongoing|done"),
                _PICTURE],
    "product": [_NAME, _SUMMARY, _DESCRIPTION, ("price", "Price", "السعر", "float"),
                ("category", "Category", "الفئة", "char"), _PICTURE],
    "service": [_NAME, _SUMMARY, _DESCRIPTION, ("icon", "Icon (fa-...)", "الأيقونة (fa-...)", "char"),
                ("quote", "Request a quote (yes/no)", "طلب عرض سعر (yes/no)", "bool"), _PICTURE],
    "member": [_NAME, ("role", "Role", "المسمى الوظيفي", "char"), ("specialty", "Specialty", "التخصص", "char"),
               ("bio", "Biography", "نبذة", "text"), ("linkedin", "LinkedIn link", "رابط لينكدإن", "char"),
               ("hours", "Working hours", "ساعات العمل", "text"), _PICTURE],
    "testimonial": [("author", "Author", "الكاتب", "char"), ("author_role", "Author role", "صفة الكاتب", "char"),
                    ("company", "Company", "الشركة", "char"), ("rating", "Rating (1-5)", "التقييم (1-5)", "int"),
                    ("description", "Text", "النص", "text"), _PICTURE],
    "faq": [("question", "Question", "السؤال", "char"), ("answer", "Answer", "الجواب", "text")],
    "location": [_NAME, ("street", "Street", "الشارع", "char"), ("city", "City", "المدينة", "char"),
                 ("zip", "Postal code", "الرمز البريدي", "char"), ("country", "Country", "الدولة", "char"),
                 ("phone", "Phone", "الهاتف", "char"), ("whatsapp", "WhatsApp (+971...)", "واتساب (+971...)", "char"),
                 ("email", "Email", "البريد الإلكتروني", "char"), ("hours", "Working hours", "ساعات العمل", "text"),
                 ("maps_url", "Google Maps link", "رابط خرائط جوجل", "char"),
                 ("is_main", "Main location (yes/no)", "الموقع الرئيسي (yes/no)", "bool"), _PICTURE],
}
TRANSLATED = {
    "project": ("name", "summary", "description"), "product": ("name", "summary", "description"),
    "service": ("name", "summary", "description"), "member": ("name", "bio"),
    "testimonial": ("description",), "faq": ("question", "answer"), "location": ("name",),
}
HTML_FIELDS = {"description", "answer", "bio"}


def columns(kind):
    """:return: the list of ``(field, en, ar, type)`` of ``kind``, with the ``_ar`` twins of the translated ones"""
    cols = list(COLUMNS[kind])
    texts = {c[0]: c for c in cols}
    for field in TRANSLATED.get(kind, ()):
        _f, en, ar, kind_type = texts[field]
        cols.append((field + "_ar", "%s (Arabic)" % en, "%s (بالعربية)" % ar, kind_type))
    return cols


def header_label(col):
    return "%s / %s" % (col[1], col[2])


def _norm_header(text):
    return re.sub(r"[^a-z0-9؀-ۿ]+", "", str(text or "").lower())


def header_map(kind, headers):
    """:return: ``{column index: field}``; a header matches by field key, English or Arabic label, or the
    ``English / Arabic`` form of the template; unknown headers are ignored"""
    known = {}
    for col in columns(kind):
        for label in (col[0], col[1], col[2], header_label(col)):
            known[_norm_header(label)] = col[0]
    out = {}
    for index, head in enumerate(headers):
        field = known.get(_norm_header(head))
        if field and field not in out.values():
            out[index] = field
    return out


def clean(text, limit=MAX_CELL):
    """:return: a cell as plain text: no control character, trimmed, cut at ``limit``; the leading ``=`` or ``@``
    of a spreadsheet formula is dropped so that nothing can run when the value is exported again"""
    text = "" if text is None else str(text)
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C").strip()
    if text[:1] in ("=", "@"):
        text = text.lstrip("=@ ")
    return text[:limit]


def convert(value, kind_type):
    """:return: the value in the type of the column, or None when it is empty or not valid"""
    text = clean(value)
    if not text:
        return None
    if kind_type in ("char", "text"):
        return text
    if kind_type == "bool":
        return text.lower() in TRUE
    if kind_type == "int":
        try:
            return int(float(text.replace(",", ".")))
        except ValueError:
            return None
    if kind_type == "float":
        try:
            number = float(text.replace(",", ""))
        except ValueError:
            return None
        return number if number == number and abs(number) < 1e12 else None
    if kind_type.startswith("sel:"):
        text = text.lower()
        return text if text in kind_type[4:].split("|") else None
    return None


def rows_to_records(kind, rows):
    """:param rows: list of lists, the first being the headers
    :return: ``(records, warnings)``; a record is a dict field -> value, empty rows are skipped"""
    if not rows:
        return [], ["The file is empty."]
    mapping = header_map(kind, rows[0])
    if not mapping:
        return [], ["No column of the template was recognised in the first row."]
    types = {c[0]: c[3] for c in columns(kind)}
    records, warnings = [], []
    for row in rows[1:MAX_ROWS + 1]:
        record = {}
        for index, field in mapping.items():
            if index < len(row):
                value = convert(row[index], types[field])
                if value is not None:
                    record[field] = value
        if record:
            records.append(record)
    if len(rows) - 1 > MAX_ROWS:
        warnings.append("Only the first %d rows are read." % MAX_ROWS)
    return records, warnings


def read_file(data, filename):
    """:param data: bytes of an .xlsx or .csv file
    :return: list of rows. Raises ValueError on a file that cannot be read, or is too big"""
    if not data or len(data) > MAX_FILE:
        raise ValueError("The file is empty or larger than 5 MB.")
    if (filename or "").lower().endswith((".xlsx", ".xlsm")) or data[:2] == b"PK":
        try:
            import openpyxl  # noqa: PLC0415
        except ImportError as error:  # pragma: no cover
            raise ValueError("Excel files cannot be read here: save the sheet as CSV.") from error
        try:
            book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            sheet = book.worksheets[0]
            return [list(row) for row in sheet.iter_rows(max_row=MAX_ROWS + 1, values_only=True)]
        except Exception as error:  # noqa: BLE001 - any broken workbook is the same answer to the person
            raise ValueError("The Excel file could not be read.") from error
    text = None
    for encoding in ("utf-8-sig", "utf-16", "cp1256", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeError:
            continue
    if text is None:  # pragma: no cover
        raise ValueError("The file could not be read.")
    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [row for _n, row in zip(range(MAX_ROWS + 1), csv.reader(io.StringIO(text), dialect))]


def build_template(kind, fmt="xlsx"):
    """:return: ``(bytes, filename, mimetype)`` the empty template of ``kind`` with the bilingual headers;
    xlsx when xlsxwriter is installed, else CSV"""
    headers = [header_label(c) for c in columns(kind)]
    if fmt == "xlsx":
        try:
            import xlsxwriter  # noqa: PLC0415
        except ImportError:
            fmt = "csv"
    if fmt == "xlsx":
        out = io.BytesIO()
        book = xlsxwriter.Workbook(out, {"in_memory": True})
        sheet = book.add_worksheet(kind[:31])
        bold = book.add_format({"bold": True, "bg_color": "#EEEEEE", "text_wrap": True})
        for index, head in enumerate(headers):
            sheet.write_string(0, index, head, bold)
            sheet.set_column(index, index, max(16, min(40, len(head))))
        sheet.freeze_panes(1, 0)
        book.close()
        return (out.getvalue(), "nudge_import_%s.xlsx" % kind,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    out = io.StringIO()
    csv.writer(out).writerow(headers)
    return ("﻿" + out.getvalue()).encode("utf-8"), "nudge_import_%s.csv" % kind, "text/csv"


def slug(text):
    """:return: a file or record name reduced to letters and digits, for matching a picture to a record"""
    text = unicodedata.normalize("NFKC", str(text or "")).lower()
    return re.sub(r"[\W_]+", "", text)


def stem(filename):
    return slug(re.sub(r"\.[A-Za-z0-9]{1,5}$", "", str(filename or "")))


def match_picture(pictures, wanted, name):
    """:param pictures: dict ``{stem: item}`` built with :func:`stem`
    :param wanted: the file name given in the row (may be empty)
    :param name: the record's name
    :return: the item whose file name is ``wanted``, else the one named like the record, else None"""
    for candidate in (wanted, name):
        key = stem(candidate)
        if key and key in pictures:
            return pictures[key]
    return None


def plain_to_html(text):
    """:return: plain text as escaped HTML paragraphs (no tag or script of the cell survives)"""
    from markupsafe import escape  # noqa: PLC0415
    paragraphs = [p for p in re.split(r"\n+", str(text or "")) if p.strip()]
    return "".join("<p>%s</p>" % escape(p.strip()) for p in paragraphs)

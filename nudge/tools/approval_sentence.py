"""An approval rule said in a sentence, read by rules: no model, no database.

"Any purchase order above 50,000 needs the finance manager before it is
confirmed", "أي طلب شراء فوق 50 ألف بدو موافقة المدير المالي قبل التأكيد",
"Confirming a sales order above 50,000 needs the sales manager". ``parse``
finds what is approved (a kind of document), from which amount, and by whom,
one level for each person named, in the order said: "then the CEO above
200,000" is a second level. Anything it cannot read with certainty gives
None, and the provider reads the sentence instead.
"""
import re

from ..llm import arabic

# What is approved: the phrases (folded), the model and what the sentence is about
KINDS = (
    ("purchase.order", "purchase_order", (
        "purchase order", "purchase orders", "purchase request", "purchase requests", "purchase", "po",
        "طلب شراء", "طلبات شراء", "امر شراء", "اوامر شراء", "طلبيه شراء", "طلبيات شراء", "طلب مشتريات",
        "طلبات مشتريات", "امر مشتريات", "طلب الشراء", "طلبات الشراء", "امر الشراء")),
    ("sale.order", "sales_order", (
        "sales order", "sales orders", "sale order", "sale orders", "quotation", "quotations", "quote", "quotes",
        "امر بيع", "اوامر بيع", "طلب بيع", "طلبات بيع", "طلبيه بيع", "طلب مبيعات", "طلبات مبيعات", "امر مبيعات",
        "عرض سعر", "عروض اسعار", "عرض اسعار", "طلبيه مبيعات", "امر البيع", "طلب البيع")),
    ("account.move", "vendor_bill", (
        "vendor bill", "vendor bills", "supplier bill", "supplier bills", "bill", "bills",
        "فاتوره مورد", "فاتوره المورد", "فواتير الموردين", "فواتير مورد", "فاتوره شراء", "فاتوره مشتريات",
        "فواتير شراء", "فواتير المشتريات")),
    ("account.move", "customer_invoice", (
        "customer invoice", "customer invoices", "invoice", "invoices",
        "فاتوره عميل", "فاتوره بيع", "فواتير العملاء", "فواتير بيع", "فاتوره", "فواتير")),
)
# What the sentence names as the one who approves: roles and the group each one is
ROLES = (
    ("finance", "account.group_account_manager", (
        "finance manager", "financial manager", "finance director", "financial controller", "cfo",
        "accounting manager", "accounts manager", "chief accountant", "accountant",
        "مدير ماليه", "المدير المالي", "مدير الماليه", "مدير الحسابات", "مدير الحسابات", "المحاسب العام",
        "رئيس الحسابات", "المحاسب", "مدير محاسبه", "المدير المالى")),
    ("sales", "sales_team.group_sale_manager", (
        "sales manager", "sales director", "head of sales",
        "مدير المبيعات", "مدير مبيعات", "مدير البيع", "رئيس المبيعات")),
    ("purchase", "purchase.group_purchase_manager", (
        "purchase manager", "purchasing manager", "procurement manager", "head of purchasing",
        "مدير المشتريات", "مدير مشتريات", "مدير الشراء", "رئيس المشتريات")),
    ("general", None, (
        "ceo", "general manager", "managing director", "chief executive", "gm",
        "المدير العام", "مدير عام", "المدير التنفيذي", "الرئيس التنفيذي", "رئيس مجلس الاداره", "صاحب الشركه",
        "المدير الاعلي", "المدير الاعلى")),
    ("manager", "", ("the manager", "manager", "المدير", "مدير")),
)
# The group of the manager of the app, when the sentence says only "the manager"
APP_MANAGER = {
    "purchase.order": "purchase.group_purchase_manager",
    "sale.order": "sales_team.group_sale_manager",
    "account.move": "account.group_account_manager",
}
# What follows the amount: thousands and millions
UNITS = {
    "k": 1_000, "thousand": 1_000, "m": 1_000_000, "mm": 1_000_000, "million": 1_000_000,
    "الف": 1_000, "الاف": 1_000, "الفا": 1_000, "ملايين": 1_000_000, "مليون": 1_000_000, "ملاين": 1_000_000,
}
# Arabic number words people write before "thousand"
WORDS = {
    "واحد": 1, "اثنين": 2, "ثلاثه": 3, "اربعه": 4, "خمسه": 5, "سته": 6, "سبعه": 7, "ثمانيه": 8, "تسعه": 9,
    "عشره": 10, "عشرين": 20, "ثلاثين": 30, "اربعين": 40, "خمسين": 50, "ستين": 60, "سبعين": 70,
    "ثمانين": 80, "تسعين": 90, "ميه": 100, "مايه": 100, "مئه": 100, "مئتين": 200, "ميتين": 200, "ثلاثمايه": 300,
    "ten": 10, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
    "ninety": 90, "hundred": 100,
}
APPROVAL_WORDS = (
    "approval", "approve", "approved", "approves", "authorise", "authorize", "sign off", "needs", "need",
    "requires", "require", "must", "موافقه", "موافقة", "يوافق", "وافق", "يحتاج", "تحتاج", "بدو", "بدها", "تبي",
    "تبغي", "لازم", "ضروري", "يلزم", "تلزم", "اعتماد", "يعتمد", "تصديق", "اذن", "يطلب")
THEN_WORDS = ("then", "after that", "and then", "followed by", "above that", "بعدين", "بعدها", "بعد ذلك", "ثم",
              "وبعدين", "وبعدها", "وبعد ذلك", "وبعد")
NUMBER = re.compile(r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*([^\s\d,.;:!?()]*)")


def _fold(text):
    text = (text or "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789"))
    text = text.translate(str.maketrans({"٫": ".", "٬": ",", "،": ","}))
    return arabic.fold(text)


def _phrase(text, phrase):
    """:return: the position of the whole word or phrase in the text, or -1"""
    match = re.search(r"(?<![\w])(?:و|ب|ل|ال|وال|بال|لل)?" + re.escape(arabic.fold(phrase)) + r"(?![\w])", text)
    return match.start() if match else -1


def _amounts(text):
    """:return: ``[(position, amount)]`` of the amounts the sentence names"""
    found = []
    for match in NUMBER.finditer(text):
        number = float(match.group(1).replace(",", ""))
        factor = UNITS.get(match.group(2).lower(), 1)
        found.append((match.start(), number * factor, match.end()))
    # "خمسين الف", "fifty thousand": a number word and its unit
    for word, value in WORDS.items():
        for match in re.finditer(r"(?<![\w])" + re.escape(arabic.fold(word)) + r"\s+(\S+)", text):
            unit = match.group(1)
            if unit in UNITS:
                found.append((match.start(), value * UNITS[unit], match.end()))
    # the same place is one amount, the longest read
    found.sort(key=lambda item: (item[0], -item[2]))
    out = []
    for position, amount, end in found:
        if out and position < out[-1][2]:
            continue
        out.append((position, amount, end))
    return [(position, amount) for position, amount, _end in out if amount > 0]


def _kind(text):
    best = None
    for model, key, phrases in KINDS:
        for phrase in phrases:
            position = _phrase(text, phrase)
            if position >= 0 and (best is None or len(phrase) > best[0]):
                best = (len(phrase), model, key)
    return best[1:] if best else (None, None)


def _roles(text):
    """:return: ``[(position, role key, xmlid)]`` in the order said, a role once for each place"""
    found = []
    for key, xmlid, phrases in ROLES:
        for phrase in phrases:
            for match in re.finditer(r"(?<![\w])(?:و|ب|ل|ال|وال|بال|لل)?" + re.escape(arabic.fold(phrase)) + r"(?![\w])", text):
                found.append((match.start(), match.end(), key, xmlid, len(phrase)))
    found.sort(key=lambda item: (item[0], -item[4]))
    out = []
    for start, end, key, xmlid, _size in found:
        if out and start < out[-1][1]:
            continue
        out.append((start, end, key, xmlid))
    # "manager" alone inside "sales manager" is already taken by the longer phrase
    return [(start, key, xmlid) for start, _end, key, xmlid in out]


def parse(text):
    """:return: ``{"model", "kind", "levels": [{"amount", "role", "group"}]}`` or None when the
    sentence is not an approval rule said clearly: a document, an approver, and approval asked for.
    ``group`` is the xmlid of the group of the approver (None for a role with no group of Odoo,
    which is looked for by name)."""
    folded = _fold(str(text or "")[:600])
    if not folded.strip():
        return None
    model, kind = _kind(folded)
    if not model:
        return None
    if not any(_phrase(folded, word) >= 0 for word in APPROVAL_WORDS):
        return None
    roles = _roles(folded)
    if not roles:
        return None
    amounts = _amounts(folded)
    levels = []
    for index, (_position, role, xmlid) in enumerate(roles):
        if role == "manager":
            xmlid = APP_MANAGER.get(model)
        if index < len(amounts):
            amount = amounts[index][1]
        elif amounts:
            amount = amounts[-1][1]
        else:
            amount = 0
        # the same person named twice in a row is one level
        if levels and levels[-1]["role"] == role:
            continue
        levels.append({"amount": amount, "role": role, "group": xmlid})
    levels.sort(key=lambda level: level["amount"])
    return {"model": model, "kind": kind, "levels": levels}

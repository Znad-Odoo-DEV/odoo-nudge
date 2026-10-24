"""An Arabic question looks for English documentation pages.

The built-in documentation is English text. The words of an Arabic question
are carried to English here, from three places: the nouns of the command
lexicon (an invoice, a time off, a bill...), the words a company added to its
own lexicon (they mean one of those nouns), and a small glossary of the
topics the pages are about. Nothing is translated by guessing: a word that is
in none of them is left out, and a question with no known word keeps the
old way of searching. No database, no AI."""
from . import lexicon, normalise

ARABIC_LETTER = normalise.ARABIC

# What the pages call each noun of the lexicon
CATEGORY_TERMS = {
    "contact": "contact customer", "product": "product", "quotation": "quotation",
    "sales_order": "sales order", "sales": "sales", "purchase_order": "purchase order",
    "invoice": "invoice", "bill": "vendor bill", "task": "task", "lead": "lead", "ticket": "ticket",
    "timeoff": "time off", "employee": "employee",
    "unpaid": "unpaid", "paid": "paid", "overdue": "overdue", "draft": "draft", "confirmed": "confirmed",
    "posted": "posted", "cancelled": "cancelled",
    "chart": "chart", "history": "history",
}

# The topics of the pages, said in Arabic: one phrase, the English words that find its pages
GLOSSARY = {
    "ضريبة": "tax", "ضرائب": "tax", "الضريبة": "tax", "ضريبة القيمة المضافة": "vat tax",
    "مخزون": "inventory stock", "المخزون": "inventory stock", "مستودع": "warehouse", "مستودعات": "warehouse",
    "المستودع": "warehouse", "جرد": "inventory adjustment", "تسليم": "delivery", "استلام": "receipt",
    "شحن": "shipping", "كشف حساب": "statement", "رواتب": "payroll", "الرواتب": "payroll", "راتب": "payroll salary",
    "كشف رواتب": "payslip payroll", "تقرير": "report", "تقارير": "report", "التقارير": "report",
    "اعدادات": "settings", "الاعدادات": "settings", "إعدادات": "settings", "ضبط": "configure settings",
    "تهيئة": "configure", "مستخدم": "user", "مستخدمين": "user", "المستخدمين": "user", "صلاحيات": "access rights",
    "صلاحية": "access rights", "الصلاحيات": "access rights", "مجموعة": "group", "شركة": "company",
    "شركات": "company", "عملة": "currency", "عملات": "currency", "قائمة اسعار": "pricelist",
    "قوائم اسعار": "pricelist", "خصم": "discount", "خصومات": "discount", "دفعة": "payment", "دفع": "payment",
    "مدفوعات": "payment", "سداد": "payment", "تسوية": "reconcile reconciliation", "مطابقة": "reconcile",
    "بنك": "bank", "البنك": "bank", "كشف بنكي": "bank statement", "يومية": "journal", "قيد": "journal entry",
    "قيود": "journal entry", "محاسبة": "accounting", "المحاسبة": "accounting", "حسابات": "accounts",
    "تصنيع": "manufacturing", "امر تصنيع": "manufacturing order", "قائمة مواد": "bill of materials",
    "مشروع": "project", "مشاريع": "project", "المشاريع": "project", "ساعات": "timesheet", "جدول زمني": "timesheet",
    "مصروفات": "expense", "مصروف": "expense", "نفقات": "expense", "توظيف": "recruitment", "وظيفة": "job position",
    "حضور": "attendance", "الحضور": "attendance", "اشتراك": "subscription", "اشتراكات": "subscription",
    "موقع": "website", "متجر": "shop ecommerce", "تجارة الكترونية": "ecommerce", "بريد": "email",
    "الايميل": "email", "ايميل": "email", "بريد الكتروني": "email", "قالب": "template", "قوالب": "template",
    "طباعة": "print", "اطبع": "print", "ملف": "file attachment", "مرفق": "attachment", "مرفقات": "attachment",
    "استيراد": "import", "تصدير": "export", "نسخ احتياطي": "backup", "تثبيت": "install", "تركيب": "install",
    "تحديث": "update upgrade", "ترقية": "upgrade", "لغة": "language", "ترجمة": "translation",
    "ترجمات": "translation", "اشعار": "notification", "اشعارات": "notification", "نشاط": "activity",
    "انشطة": "activity", "أنشطة": "activity", "موافقة": "approval", "موافقات": "approval", "اعتماد": "approval",
    "تقويم": "calendar", "اجتماع": "meeting", "اجتماعات": "meeting", "محادثة": "discuss chat",
    "قناة": "channel", "رسالة": "message", "رسائل": "message", "وحدة قياس": "unit of measure",
    "وحدات القياس": "unit of measure", "باركود": "barcode", "رقم تسلسلي": "serial number",
    "دفعة تصنيع": "lot batch", "سلسلة": "sequence", "ترقيم": "sequence", "كوبون": "coupon",
    "ولاء": "loyalty", "نقاط": "points", "نقطة بيع": "point of sale", "كاشير": "point of sale",
    "جهاز": "device", "مخطط": "chart", "رسم بياني": "chart", "لوحة": "dashboard", "لوحة معلومات": "dashboard",
    "الية": "automation", "أتمتة": "automation", "اتمتة": "automation", "قاعدة": "rule", "قواعد": "rule",
    "حقل": "field", "حقول": "field", "عرض": "view", "شاشة": "view", "قائمة": "list menu", "قائمة منسدلة": "menu",
    "تقرير مخصص": "custom report", "فلتر": "filter", "تصفية": "filter", "بحث": "search", "تجميع": "group by",
    "مورد": "vendor supplier", "الموردين": "vendor supplier", "موردين": "vendor supplier",
    "فواتير": "invoice", "تذكير": "reminder follow-up", "متابعة": "follow-up", "كيف": "", "كيفية": "",
    "رصيد": "balance credit", "اشعار دائن": "credit note", "اشعار مدين": "debit note", "استرداد": "refund",
    "ارجاع": "return refund", "مرتجع": "return", "مرتجعات": "return",
}

_glossary_cache = {}


def _glossary():
    """:return: ``{phrase key: english words}``, built once"""
    if not _glossary_cache:
        for phrase, terms in GLOSSARY.items():
            key = normalise.phrase_key(phrase)
            if key and terms:
                _glossary_cache[key] = terms
    return _glossary_cache


def has_arabic(text):
    return bool(ARABIC_LETTER.search(text or ""))


def terms(question, extra_pairs=()):
    """:param question: a question that may be in Arabic
    :param extra_pairs: ``(phrase, category)`` the company added to its lexicon
    :return: the English words that find the pages of the question: the Latin words it holds, and the
        translation of each Arabic word or phrase that is known; ``[]`` when there is nothing"""
    tokens = normalise.tokenize(question or "")
    glossary = _glossary()
    extra = {}
    for phrase, category in extra_pairs:
        key = normalise.phrase_key(phrase)
        if key and category in CATEGORY_TERMS:
            extra.setdefault(key, set()).add(category)
    longest = max([3] + [len(key) for key in extra])
    out = []
    index = 0
    while index < len(tokens):
        taken = 0
        for size in range(min(longest, len(tokens) - index), 0, -1):
            key = tuple(token.stem for token in tokens[index:index + size])
            words = []
            if key in glossary:
                words.append(glossary[key])
            categories = {tag for tag in lexicon.PHRASES.get(key, ()) if tag in CATEGORY_TERMS}
            categories |= extra.get(key, set())
            words += [CATEGORY_TERMS[tag] for tag in sorted(categories)]
            if words:
                out += " ".join(words).split()
                taken = size
                break
        if taken:
            index += taken
            continue
        word = tokens[index].raw
        if word.isascii() and word.isalpha() and len(word) > 2:
            out.append(word)
        index += 1
    return list(dict.fromkeys(out))

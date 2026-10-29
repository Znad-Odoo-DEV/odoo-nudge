"""An Arabic question looks for English documentation pages.

The built-in documentation is English text. The words of an Arabic question
are carried to English here, from four places: the nouns of the command
lexicon (an invoice, a time off, a bill...), the words a company added to its
own lexicon (they mean one of those nouns), a small glossary of the
topics the pages are about, and the terms of the screens as Odoo itself
translates them in the ar.po file of each installed module ("شروط الدفع" is
"Payment Terms"). Nothing is translated by guessing: a word that is
in none of them is left out, and a question with no known word keeps the
old way of searching. No database, no AI."""
import re
from collections import Counter

from . import lexicon, normalise

ARABIC_LETTER = normalise.ARABIC
LATIN_LETTER = re.compile("[A-Za-z]")
# A term of the screens: one to four English words, letters only (no placeholder, no markup)
TERM_WORDS = 4
TERM = re.compile(r"[A-Za-z][A-Za-z'&/ -]*")
PO_ESCAPE = re.compile(r"\\(.)")
PO_ESCAPES = {"n": "\n", "t": "\t", "r": "\r"}
# A term made of these words only says nothing about a page ("On", "In the", "How")
STOP_WORDS = frozenset((
    "a an the of on in at to for from by with and or not no yes is are be it this that these those all any "
    "how what when where which who why do does my your our me you we i if as per via so up out off then "
    "than here there new more less other others none some each").split())

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
    # Said as a whole they name one topic: "the order" alone would bring every sales page
    "اعادة الطلب": "reordering replenishment", "قواعد اعادة الطلب": "reordering replenishment",
    "قاعدة اعادة الطلب": "reordering replenishment", "اعادة التموين": "replenishment",
    # The screens say "تاريخ الإقفال" only inside longer labels: no term of its own to be found by
    "تاريخ الاقفال": "lock date", "تواريخ الاقفال": "lock date", "تاريخ القفل": "lock date",
    "تواريخ القفل": "lock date",
}

# "كيف أؤكد ...": the verb of a how-to question as it is asked; the screens only say its noun ("تأكيد")
HOWTO_VERBS = {
    "اؤكد": "confirm", "انشئ": "create", "اسجل": "record register", "احدد": "set", "اضبط": "configure set",
    "اضيف": "add", "الغي": "cancel", "احذف": "delete", "ارسل": "send", "اعدل": "edit", "اغير": "change",
    "ادفع": "pay payment", "اسوي": "reconcile", "اخصص": "allocate allocation", "اقفل": "lock",
    "ارشف": "archive", "اطلب": "request", "استرد": "refund",
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


def _po_text(line):
    """:return: the text of one quoted line of a .po file, its escapes read"""
    line = line.strip()
    if len(line) < 2 or line[0] != '"' or line[-1] != '"':
        return ""
    return PO_ESCAPE.sub(lambda match: PO_ESCAPES.get(match.group(1), match.group(1)), line[1:-1])


def po_entries(lines):
    """:param lines: the lines of a .po file
    :return: ``[(msgid, msgstr)]`` of its translated entries; a fuzzy one or one with plurals is left out"""
    entries = []
    msgid = msgstr = None
    part = None
    fuzzy = False

    def close():
        source, translation = "".join(msgid or ()), "".join(msgstr or ())
        if source and translation and not fuzzy:
            entries.append((source, translation))

    for line in lines:
        if line.startswith("msgid "):
            close()
            msgid, msgstr, part = [_po_text(line[6:])], None, "id"
        elif line.startswith("msgstr ") and msgid is not None:
            msgstr, part = [_po_text(line[7:])], "str"
        elif line.startswith('"') and part:
            (msgid if part == "id" else msgstr).append(_po_text(line))
        elif line.startswith(("msgid_plural", "msgstr[")):
            msgid = msgstr = part = None
        elif line.startswith("msgctxt"):
            close()
            msgid = msgstr = part = None
        elif line.startswith("#,") and "fuzzy" in line:
            close()
            msgid = msgstr = part = None
            fuzzy = True
        elif not line.strip():
            close()
            msgid = msgstr = part = None
            fuzzy = False
    close()
    return entries


def screen_terms(files):
    """:param files: the lines of each ar.po file of the installed modules
    :return: ``{phrase key: english words}``: the short terms of the screens (one to four words) by their
        Arabic; an Arabic term Odoo translates from several, the one it uses most (and the next one when
        it is used at least half as often: "فاتورة" is "bill" and "invoice"). With the verbs of a how-to
        question (:data:`HOWTO_VERBS`)"""
    counts = {}
    for lines in files:
        for english, arabic_text in po_entries(lines):
            english = " ".join(english.split())
            words = english.casefold().split()
            if not words or len(words) > TERM_WORDS or not TERM.fullmatch(english):
                continue
            if all(word in STOP_WORDS for word in words):
                continue
            if not has_arabic(arabic_text) or LATIN_LETTER.search(arabic_text):
                continue
            key = normalise.phrase_key(arabic_text)
            if not key or len(key) > TERM_WORDS or (len(key) == 1 and len(key[0]) < 3):
                continue
            counts.setdefault(key, Counter())[" ".join(words)] += 1
    found = {}
    for key, counter in counts.items():
        ranked = sorted(counter.items(), key=lambda item: (-item[1], len(item[0]), item[0]))
        top = ranked[0][1]
        found[key] = " ".join(english for english, count in ranked[:2] if count * 2 >= top)
    for verb, english in HOWTO_VERBS.items():
        found[normalise.phrase_key(verb)] = english
    return found


def terms(question, extra_pairs=(), known=None):
    """:param question: a question that may be in Arabic
    :param extra_pairs: ``(phrase, category)`` the company added to its lexicon
    :param known: ``{phrase key: english words}`` of the terms of the screens (:func:`screen_terms`)
    :return: the English words that find the pages of the question: the Latin words it holds, and the
        translation of each Arabic word or phrase that is known; ``[]`` when there is nothing"""
    tokens = normalise.tokenize(question or "")
    glossary = _glossary()
    known = known or {}
    extra = {}
    for phrase, category in extra_pairs:
        key = normalise.phrase_key(phrase)
        if key and category in CATEGORY_TERMS:
            extra.setdefault(key, set()).add(category)
    longest = max([3] + [len(key) for key in extra] + ([TERM_WORDS] if known else []))
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
            if not words and key in known:
                # The glossary and the lexicon say what a page calls it; the screens, only when they are silent
                words.append(known[key])
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

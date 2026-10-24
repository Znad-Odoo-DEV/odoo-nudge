"""The fingerprint of a question: the same question asked in another dialect, in another
word order or with another politeness gives the same hash.

What is kept of a question is the set of its meaningful words: folded (Arabic marks and
letter forms, English case), without the words that only make it polite or a question
("how can I", "كيف", "شلون", "ازاي", "بدي"), the common verbs and nouns of an application
brought to one form in every dialect ("create", "أنشئ", "اعمل", "بعمل", "سوي" and "make" are one
word), the rest stemmed like the answer memory does. The hash of the sorted set is the
fingerprint. Nothing of who asked is in it. Plain Python, no state, nothing from Odoo."""
import hashlib
import re

from . import arabic, memory_match, redact

# A question of fewer meaningful words than this says too little to be told from another
MIN_WORDS = 2
SAMPLE_CHARS = 300

# Said with more than one word: written as one before the words are cut
PHRASES = (
    (r"عرض\s+(?:ال)?اسعار|عرض\s+(?:ال)?سعر|عروض\s+(?:ال)?اسعار?|price\s+quot\w*|عرض\s+سعري",
     "quotation"),
    (r"كلمه\s+(?:ال)?(?:سر|مرور)|(?:ال)?باسورد|password\w*", "password"),
    (r"جهه\s+(?:ال)?اتصال|جهات\s+(?:ال)?اتصال", "contact"),
    (r"set\s+up|setting\s+up", "setup"),
    (r"time\s*-?\s*off", "leave"),
    (r"purchase\s+order", "purchase"),
    (r"(?:امر|اوامر|طلب|طلبات|طلبيه|طلبيات)\s+(?:ال)?شراء", "purchase"),
    (r"(?:امر|اوامر|طلب|طلبات|طلبيه|طلبيات)\s+(?:ال)?بيع", "sale"),
)

# Concept: the words, in every dialect, that say it
CONCEPTS = {
    # What is done
    "create": "انشاء انشئ انشىء ننشئ ننشىء اعمل عمل بعمل نعمل تعمل يعمل اعملها سوي اسوي بسوي نسوي يسوي تسوي "
              "اسوى سوى اصنع صنع اصدار اطلع جهز تجهيز اطلع create creates created creating make makes "
              "made making issue issues issued issuing generate generates generated generating raise "
              "produce prepare",
    "delete": "حذف احذف نحذف احذفها مسح امسح بمسح نمسح شيل اشيل ازاله ازل ازيل delete deletes deleted "
              "deleting remove removes removed removing erase erased",
    "cancel": "الغاء الغي الغيها نلغي بلغي كنسل cancel cancels cancelled canceled cancelling canceling",
    "confirm": "تاكيد اكد نأكد نأكد confirm confirms confirmed confirming validate validates validated "
               "validating validation",
    "approve": "اعتماد اعتمد موافقه الموافقه وافق اوافق approve approves approved approving approval",
    "print": "طباعه اطبع اطبعها نطبع بطبع print prints printing printed",
    "send": "ارسال ارسل ابعت ابعث بعت ببعت نبعت ارسلها send sends sending sent",
    "add": "اضافه اضف اضيف ضيف بضيف نضيف اضافتها add adds adding added",
    "edit": "تعديل عدل اعدل نعدل بعدل تغيير غير اغير بغير نغير تحديث حدث احدث edit edits editing edited "
            "change changes changing changed modify modifies modified modifying update updates updated "
            "updating",
    "pay": "دفع ادفع ادفعها ندفع بدفع دفعه دفعات سداد سدد اسدد pay pays paid paying payment payments",
    "register": "تسجيل سجل اسجل نسجل بسجل register registers registered registering record records "
                "recorded recording",
    "search": "بحث ابحث نبحث ادور دور بدور لاقي الاقي لقى search searches searching find finds finding "
              "found",
    "open": "فتح افتح نفتح بفتح open opens opened opening",
    "view": "عرض اعرض نعرض view views viewing see sees seeing display displays",
    "export": "تصدير صدر اصدر نصدر export exports exported exporting",
    "import": "استيراد استورد نستورد import imports imported importing",
    "setup": "اعداد اعد ضبط اضبط نضبط ظبط اظبط تهيئه configure configures configured configuring "
             "configuration setup",
    "copy": "نسخ انسخ نسخه copy copies duplicate duplicates duplicated clone",
    "archive": "ارشفه ارشف archive archives archived",
    "install": "تثبيت ثبت install installs installed installing installation",
    "upload": "رفع ارفع upload uploads uploaded uploading",
    # What it is done to
    "invoice": "فاتوره فواتير فاتورتي فاتورتها invoice invoices",
    "customer": "عميل عملاء زبون زبائن زباين كلاينت customer customers client clients",
    "vendor": "مورد موردين vendor vendors supplier suppliers",
    "product": "منتج منتجات صنف اصناف product products item items",
    "order": "طلب طلبات اوردر طلبيه طلبيات order orders",
    "quotation": "quotation quotations quote quotes",
    "employee": "موظف موظفين موظفه موظفات employee employees staff",
    "leave": "اجازه اجازات اجازتي leave leaves vacation vacations holiday holidays",
    "report": "تقرير تقارير report reports",
    "salary": "راتب رواتب مرتب مرتبات salary salaries payroll payslip payslips",
    "user": "مستخدم مستخدمين user users",
    "warehouse": "مخزن مخازن مستودع مستودعات warehouse warehouses inventory stock مخزون",
    "purchase": "شراء مشتريات purchase purchases buy buying bought",
    "sale": "بيع مبيعات sale sales sell selling sold",
    "project": "مشروع مشاريع project projects",
    "task": "مهمه مهام task tasks",
    "expense": "مصروف مصروفات مصاريف expense expenses",
    "budget": "ميزانيه budget budgets",
    "tax": "ضريبه ضرائب tax taxes vat",
    "currency": "عمله عملات currency currencies",
    "contact": "contact contacts",
    "password": "password",
}

# Words that make a sentence polite or a question, in every dialect, and say nothing of its subject
FILLERS = frozenset(arabic.fold(word) for word in (
    "كيف كيفاش كيفاه شلون ازاي ازي كيفيه وكيف شو شنو ايش ايه اي وش ليش ليه لماذا ممكن يمكن ينفع بدي بدنا "
    "ابغى ابغا ابي عايز عاوز عايزه عاوزه اريد نريد حابب حاب حابه نبي نبغي نبغا بغيت اشتي ودي لو سمحت سمحتي "
    "فضلك رجاء من في على الى عن مع هل ما ماذا هو هي هذا هذه هاد هاي هادا هاذا هذي ده دي دا انا انت احنا نحن "
    "لي لنا لك اقدر نقدر استطيع نستطيع فيني فينا ساعدني ساعدوني طريقه خطوات خطوه جديد جديده تبع تاع ديال "
    "اللي الي الذي التي يعني طيب شرح اشرح وريني ورجيني ارني علمني عرفني قولي اخبرني انه انها هنا هناك "
    "يا اخي استاذ مرحبا هلا سلام اهلا "
    "please pls plz could would should shall will want wanna need needs like how can do does did i my me "
    "we our you your it its this that what where when which with for of in on at and or be by as there "
    "some any new way steps step guide tell show explain teach help hi hello thanks thank get got go to "
    "the a an is are was were am have has one ones").split())

_CONCEPT_OF = {}
for _concept, _words in CONCEPTS.items():
    for _word in _words.split():
        _CONCEPT_OF[arabic.fold(_word)] = _concept
_PHRASES = [(re.compile(pattern, re.I), concept) for pattern, concept in PHRASES]
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_REPEATED = re.compile(r"(.)\1{2,}")
_NOT_WORD = re.compile(r"[^\w]+", re.UNICODE)
_EMAIL = re.compile(r"\S+@\S+")
_LONG_NUMBER = re.compile(r"\d{5,}")
# A telephone number written with spaces, dashes, dots or brackets: seven digits or more in one run
_PHONE = re.compile(r"\+?\d[\d\s().-]{5,}\d")


def _concept(word):
    """:return: the concept ``word`` says, or None"""
    found = _CONCEPT_OF.get(word)
    if found:
        return found
    bare = arabic.strip_article(word, keep=2)
    if bare != word:
        return _CONCEPT_OF.get(bare)
    return None


def terms(text):
    """:return: the set of meaningful words of ``text``, dialects and spellings made one"""
    folded = _REPEATED.sub(r"\1", memory_match.fold(text)).translate(_ARABIC_DIGITS)
    for pattern, concept in _PHRASES:
        folded = pattern.sub(" %s " % concept, folded)
    found = set()
    for word in _NOT_WORD.split(folded):
        if not word or word in FILLERS or len(word) < 2 and not word.isdigit():
            continue
        concept = _concept(word)
        if concept:
            found.add("c:" + concept)
            continue
        stem = memory_match.stem(word)
        if stem in FILLERS or stem in memory_match.STOPWORDS:
            continue
        concept = _concept(stem)
        found.add("c:" + concept if concept else stem)
    return found


def of(text):
    """:return: the fingerprint of ``text`` (a hash), or None when it has too few meaningful words
    to be told from another question"""
    found = terms(text)
    if len(found) < MIN_WORDS:
        return None
    return hashlib.sha1("\n".join(sorted(found)).encode("utf-8")).hexdigest()[:24]


# What a person types by mistake into a question: a key, or the word that follows "password"
_SECRET = re.compile(
    r"\b(?:sk|pk|rk|gsk|ghp|xox[a-z]|AIza)[-_A-Za-z0-9]{8,}"
    r"|\b(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{20,}"
    r"|(?i:\b(?:password|passwd|pwd|passcode|api[ _-]?key|كلمة\s+(?:ال)?(?:سر|مرور)|الباسورد|باسورد)"
    r"\s*(?:is|=|:|هي|هو)?\s*)\S+")


def sample(text):
    """:return: ``text`` as it may be kept to be read by a manager: one line, no address of mail,
    no telephone or long number, no key or password, the personal data the redactor knows replaced,
    cut"""
    text = " ".join(str(text or "").split())
    text = _SECRET.sub("…", text)
    text = redact.TOKEN.sub("…", redact.Redactor().redact(text))
    text = _PHONE.sub(lambda m: "…" if sum(c.isdigit() for c in m.group()) >= 7 else m.group(), _EMAIL.sub("…", text))
    text = _LONG_NUMBER.sub("…", text)
    return text[:SAMPLE_CHARS]

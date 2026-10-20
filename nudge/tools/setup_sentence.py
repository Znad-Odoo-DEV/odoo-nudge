"""What a person says while setting a database up, read by rules: no model.

"Set up this database with me", "بدي تساعدني جهز قاعدة البيانات", "عايز أظبط
السيستم" start the first-setup assistant; the answers that follow (a company
name and its address, a country, a currency, languages, a time zone, the kind
of business, the names of warehouses, users with their role) are read here
too, so that the assistant also works with no provider. Anything not read
with certainty gives None and the question is asked again, or the provider
reads the sentence when there is one.
"""
import re

from ..llm import arabic

SETUP_SENTENCE = "Set up this database with me"

# A request to set the database up: a verb of preparing and what is prepared, or a known phrase
VERBS = (
    "set up", "setup", "configure", "initialise", "initialize", "get started", "start", "prepare", "install",
    "جهز", "اجهز", "تجهيز", "تجهز", "جهزلي", "جهزلنا", "هيئ", "اهيئ", "تهيئه", "هيء", "اظبط", "اضبط", "ظبط", "ضبط",
    "اعداد", "اعد", "اعدلي", "ركب", "تركيب", "ابدا", "نبدا", "بدايه", "ابدأ", "اعمل", "نظم", "رتب", "تظبط", "تضبط")
OBJECTS = (
    "database", "db", "odoo", "system", "erp", "company", "my company", "this instance", "instance",
    "قاعده البيانات", "قاعده بيانات", "قواعد البيانات", "الداتابيس", "الداتا بيز", "داتابيس", "الداتا بيس", "النظام",
    "السيستم", "سيستم", "اودو", "الاودو", "الشركه", "شركتي", "شركتنا", "البرنامج", "الاعدادات")
PHRASES = (
    "first setup", "setup wizard", "setup assistant", "initial setup", "set this up", "set it up",
    "help me set up", "help me start", "new database", "fresh database", "اعداد اولي", "الاعداد الاولي",
    "التهيئه الاوليه", "تهيئه اوليه", "اعداد اولى", "اول مره", "قاعده جديده", "قاعده البيانات جديده")
SKIP = ("skip", "later", "not now", "pass", "next", "no thanks", "none", "no", "تخطي", "تخطى", "بعدين", "لاحقا",
        "مو الحين", "مش دلوقتي", "مش دلوقت", "سكيب", "خليها", "ما بدي", "مابدي", "لا", "لأ", "بلاش", "ما ابي", "مابي",
        "ما ابغى", "مابغى", "ماأبي", "التالي", "الي بعده", "اللي بعده", "يلا بعده")
CANCEL = ("stop", "cancel", "quit", "exit", "forget it", "never mind", "stop the setup", "كفايه", "وقف", "الغي",
          "الغاء", "الغ", "انهي", "خلاص", "اوقف", "وقفه", "بطل", "بطلت", "انسى", "انسي")
DEFAULTS = (
    "defaults", "default", "the rest", "all remaining", "everything else", "do the rest", "with the defaults",
    "الباقي", "الباقى", "الباقي افتراضي", "الافتراضي", "افتراضي", "الافتراضيه", "كمل الباقي", "كمل الباقى",
    "كل الباقي", "الباقي بالافتراضي", "باقي الخطوات", "الباقي على الافتراضي", "الباقي عالافتراضي", "كملهم", "كمل كلهم")
YES = ("yes", "ok", "okay", "sure", "go", "go ahead", "proceed", "نعم", "ايوه", "ايوا", "اي", "ايه", "تمام", "ماشي",
       "اوكي", "حاضر", "طيب", "يلا", "اكيد", "اجل", "بلى", "ايوة", "هيك", "تفضل")


def _fold(text):
    text = (text or "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789"))
    text = text.translate(str.maketrans({"٫": ".", "٬": ",", "،": ",", "؛": ";", "؟": "?"}))
    return arabic.fold(text)


def has(text, phrases):
    """:return: whether the folded ``text`` holds one of ``phrases`` as a whole word or phrase"""
    for phrase in phrases:
        folded = arabic.fold(phrase)
        if re.search(r"(?<![\w])(?:و|ب|ل|ال|وال|بال|لل)?" + re.escape(folded) + r"(?![\w])", text):
            return True
    return False


def is_request(text):
    """:return: whether the sentence asks to set the database up, in English or in any dialect of Arabic"""
    folded = _fold(text)
    if not folded or len(folded) > 200:
        return False
    if folded == _fold(SETUP_SENTENCE) or has(folded, PHRASES):
        return True
    # a question about one setting is not the setup of the whole database
    if re.search(r"\b(how do i|how to|where|كيف|وين|فين|شلون|ازاي)\b", folded) and not has(folded, ("with me", "معي")):
        return False
    return has(folded, VERBS) and has(folded, OBJECTS)


def wants(text, words, limit=5):
    """:return: whether the whole answer is one of ``words`` (a few more words are allowed around it)"""
    folded = _fold(text).strip(" .!?,;:")
    if not folded or len(folded.split()) > limit:
        return False
    return has(folded, words)


# ----------------------------------------------------------------------
# Pieces of an answer
# ----------------------------------------------------------------------
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?<![\w@])\+?\d[\d\s().-]{6,17}\d(?![\w@])")
LABEL_KEYS = (
    "name", "company", "vat", "trn", "tax id", "tax number", "tax", "street", "address", "city", "zip", "postal",
    "phone", "tel", "mobile", "email", "website", "web", "registry", "registration", "license", "licence",
    "الاسم", "اسم الشركه", "الشركه", "الرقم الضريبي", "رقم ضريبي", "الضريبه", "الشارع", "العنوان", "المدينه",
    "الرمز البريدي", "الهاتف", "التلفون", "الجوال", "الموبايل", "البريد", "الايميل", "الموقع", "السجل التجاري",
    "الرخصه", "رقم الرخصه")


def _key_pattern(key):
    """A label as it may be written: the Arabic letters that people write one way or the other"""
    return re.escape(key).replace("ه", "[هة]").replace("ي", "[يى]").replace("ا", "[اأإآ]").replace(r"\ ", r"\s+")


LABELLED = re.compile(
    r"(?P<key>" + "|".join(_key_pattern(key) for key in sorted(LABEL_KEYS, key=len, reverse=True)) + r")\s*[:=\-]\s*"
    r"(?P<value>[^\n;,|؛،]+)", re.IGNORECASE)
LABELS = {
    "name": "name", "company": "name", "الاسم": "name", "اسم الشركه": "name", "الشركه": "name",
    "vat": "vat", "trn": "vat", "tax id": "vat", "tax number": "vat", "tax": "vat", "الرقم الضريبي": "vat",
    "رقم ضريبي": "vat", "الضريبه": "vat",
    "street": "street", "address": "street", "الشارع": "street", "العنوان": "street",
    "city": "city", "المدينه": "city", "zip": "zip", "postal": "zip", "الرمز البريدي": "zip",
    "phone": "phone", "tel": "phone", "mobile": "phone", "الهاتف": "phone", "التلفون": "phone", "الجوال": "phone",
    "الموبايل": "phone", "email": "email", "البريد": "email", "الايميل": "email",
    "website": "website", "web": "website", "الموقع": "website",
    "registry": "company_registry", "registration": "company_registry", "license": "company_registry",
    "licence": "company_registry", "السجل التجاري": "company_registry", "الرخصه": "company_registry",
    "رقم الرخصه": "company_registry",
}
VAT_NUMBER = re.compile(r"(?<![\w])(?:[A-Za-z]{2})?\d{9,15}(?![\w])")


def company_answer(text):
    """:return: the answers of the company step read from a few lines of free text: its name first,
    then anything labelled (``VAT: ...``), an email, a phone, and the address

    >>> company_answer("Raghad Trading LLC, TRN 100123456700003, Sheikh Zayed Road, Dubai")["vat"]
    '100123456700003'
    """
    text = (text or "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")).strip()
    if not text:
        return None
    out = {}
    rest = text
    for match in LABELLED.finditer(text):
        key = LABELS.get(_fold(match.group("key")))
        value = match.group("value").strip(" .")
        if key and value and key not in out:
            out[key] = value
    rest = LABELLED.sub(" ", rest)
    email = EMAIL.search(rest)
    if email:
        out.setdefault("email", email.group(0))
        rest = rest.replace(email.group(0), " ")
    # a tax number: 15 digits, or the number after TRN / VAT / الرقم الضريبي written without a colon
    if "vat" not in out:
        match = re.search(r"(?:trn|vat|tax(?: id| number)?|الرقم الضريبي|رقم ضريبي)\s*(?:no\.?|number|#)?\s*"
                          r"([A-Za-z]{0,2}[\d ]{9,18})", _fold(rest), re.IGNORECASE)
        if match:
            out["vat"] = re.sub(r"\s+", "", match.group(1)).upper()
            rest = re.sub(re.escape(match.group(0)), " ", rest, flags=re.IGNORECASE)
    phone = PHONE.search(rest)
    if phone and "phone" not in out and not re.fullmatch(r"\d{15}", re.sub(r"\D", "", phone.group(0))):
        out["phone"] = phone.group(0).strip()
        rest = rest.replace(phone.group(0), " ")
    parts = [part.strip(" .") for part in re.split(r"[\n;,|،]+", rest) if part.strip(" .")]
    parts = [part for part in parts if not re.fullmatch(r"(?i)(trn|vat|tax id|tax)", part)]
    if parts:
        out.setdefault("name", parts[0])
        if len(parts) > 1:
            out.setdefault("street", parts[1])
        if len(parts) > 2:
            out.setdefault("city", parts[2])
    return out if out.get("name") else None


# ----------------------------------------------------------------------
# Words that name a country, a currency, a language, a place, a kind of business
# ----------------------------------------------------------------------
COUNTRY_WORDS = {
    "AE": ("uae", "u.a.e", "emirates", "united arab emirates", "dubai", "abu dhabi", "sharjah", "ajman",
           "ras al khaimah", "fujairah", "الامارات", "الامارات العربيه المتحده", "دبي", "ابوظبي", "ابو ظبي",
           "الشارقه", "عجمان", "راس الخيمه", "الفجيره", "اماراتي"),
    "SA": ("saudi", "ksa", "saudi arabia", "riyadh", "jeddah", "dammam", "السعوديه", "المملكه", "المملكه العربيه",
           "الرياض", "جده", "الدمام", "سعودي", "السعودية"),
    "QA": ("qatar", "doha", "قطر", "الدوحه", "قطري"),
    "KW": ("kuwait", "الكويت", "كويتي"),
    "BH": ("bahrain", "manama", "البحرين", "المنامه", "بحريني"),
    "OM": ("oman", "muscat", "sultanate of oman", "عمان", "مسقط", "سلطنه عمان", "عماني"),
    "EG": ("egypt", "cairo", "alexandria", "مصر", "القاهره", "الاسكندريه", "مصري"),
    "JO": ("jordan", "amman", "الاردن", "اردني", "عمان الاردن"),
    "LB": ("lebanon", "beirut", "لبنان", "بيروت", "لبناني"),
    "IQ": ("iraq", "baghdad", "erbil", "العراق", "بغداد", "اربيل", "عراقي"),
    "SY": ("syria", "damascus", "سوريا", "سورية", "دمشق"),
    "PS": ("palestine", "فلسطين", "رام الله", "غزه"),
    "MA": ("morocco", "casablanca", "المغرب", "الدار البيضاء", "مغربي"),
    "TN": ("tunisia", "تونس", "تونسي"),
    "DZ": ("algeria", "الجزائر", "جزائري"),
    "LY": ("libya", "ليبيا", "ليبي"),
    "SD": ("sudan", "khartoum", "السودان", "الخرطوم", "سوداني"),
    "YE": ("yemen", "اليمن", "يمني"),
    "TR": ("turkey", "turkiye", "istanbul", "تركيا", "اسطنبول", "تركي"),
    "IN": ("india", "الهند", "هندي", "mumbai"),
    "PK": ("pakistan", "باكستان", "باكستاني"),
    "GB": ("united kingdom", "uk", "britain", "england", "london", "بريطانيا", "المملكه المتحده", "لندن", "انجلترا"),
    "US": ("usa", "united states", "america", "امريكا", "الولايات المتحده", "امريكي"),
    "FR": ("france", "فرنسا", "paris"),
    "DE": ("germany", "المانيا", "berlin"),
}
CURRENCY_WORDS = {
    "AED": ("aed", "dirham", "dirhams", "emirati dirham", "درهم", "دراهم", "درهم اماراتي"),
    "SAR": ("sar", "saudi riyal", "ريال سعودي", "ريال", "ريالات"),
    "QAR": ("qar", "qatari riyal", "ريال قطري"),
    "KWD": ("kwd", "kuwaiti dinar", "دينار كويتي", "دينار"),
    "BHD": ("bhd", "bahraini dinar", "دينار بحريني"),
    "OMR": ("omr", "omani rial", "ريال عماني"),
    "EGP": ("egp", "egyptian pound", "جنيه", "جنيه مصري", "جنيهات"),
    "JOD": ("jod", "jordanian dinar", "دينار اردني"),
    "USD": ("usd", "dollar", "dollars", "us dollar", "دولار", "دولار امريكي", "دولارات"),
    "EUR": ("eur", "euro", "euros", "يورو"),
    "GBP": ("gbp", "pound sterling", "sterling", "جنيه استرليني", "باوند"),
    "INR": ("inr", "rupee", "indian rupee", "روبيه هنديه"),
    "PKR": ("pkr", "pakistani rupee", "روبيه باكستانيه"),
    "TRY": ("try", "lira", "turkish lira", "ليره", "ليره تركيه"),
}
# Explicit currency words that need the country to tell which one ("ريال", "دينار", "جنيه")
LANGUAGE_WORDS = {
    "en_US": ("english", "en", "انجليزي", "انجليزيه", "انكليزي", "انجليزى", "الانجليزيه", "الانكليزيه"),
    "ar_001": ("arabic", "عربي", "عربى", "العربيه", "عربية", "العربي", "بالعربي"),
    "fr_FR": ("french", "francais", "فرنسي", "الفرنسيه"),
    "es_ES": ("spanish", "espanol", "اسباني", "الاسبانيه"),
    "de_DE": ("german", "deutsch", "الماني", "الالمانيه"),
    "tr_TR": ("turkish", "تركي", "التركيه"),
    "ur": ("urdu", "اردو", "الاوردو"),
    "hi_IN": ("hindi", "هندي", "الهنديه"),
    "fa_IR": ("persian", "farsi", "فارسي", "الفارسيه"),
    "ru_RU": ("russian", "روسي", "الروسيه"),
    "zh_CN": ("chinese", "صيني", "الصينيه"),
    "it_IT": ("italian", "ايطالي", "الايطاليه"),
    "pt_PT": ("portuguese", "برتغالي", "البرتغاليه"),
}
CITY_ZONES = {
    "dubai": "Asia/Dubai", "abu dhabi": "Asia/Dubai", "sharjah": "Asia/Dubai", "دبي": "Asia/Dubai",
    "ابوظبي": "Asia/Dubai", "الامارات": "Asia/Dubai", "riyadh": "Asia/Riyadh", "jeddah": "Asia/Riyadh",
    "الرياض": "Asia/Riyadh", "جده": "Asia/Riyadh", "السعوديه": "Asia/Riyadh", "doha": "Asia/Qatar",
    "الدوحه": "Asia/Qatar", "قطر": "Asia/Qatar", "kuwait": "Asia/Kuwait", "الكويت": "Asia/Kuwait",
    "manama": "Asia/Bahrain", "البحرين": "Asia/Bahrain", "المنامه": "Asia/Bahrain", "muscat": "Asia/Muscat",
    "مسقط": "Asia/Muscat", "عمان": "Asia/Muscat", "cairo": "Africa/Cairo", "القاهره": "Africa/Cairo",
    "مصر": "Africa/Cairo", "amman": "Asia/Amman", "الاردن": "Asia/Amman", "beirut": "Asia/Beirut",
    "بيروت": "Asia/Beirut", "لبنان": "Asia/Beirut", "baghdad": "Asia/Baghdad", "بغداد": "Asia/Baghdad",
    "العراق": "Asia/Baghdad", "istanbul": "Europe/Istanbul", "اسطنبول": "Europe/Istanbul",
    "london": "Europe/London", "لندن": "Europe/London",
}
KIND_WORDS = {
    "trading": ("trading", "trade", "wholesale", "distribution", "import", "export", "shop", "تجاره", "تجاري",
                "تجارة", "توزيع", "جمله", "استيراد", "تصدير", "محل", "مبيعات"),
    "services": ("services", "service", "consulting", "consultancy", "agency", "law", "clinic", "school", "خدمات",
                 "استشارات", "مكتب", "وكاله", "عياده", "مدرسه", "محاماه"),
    "retail": ("retail", "store", "supermarket", "pos", "cashier", "تجزئه", "متجر", "سوبرماركت", "كاشير",
               "بقاله", "نقطه بيع"),
    "manufacturing": ("manufacturing", "factory", "production", "workshop", "تصنيع", "مصنع", "انتاج", "ورشه",
                      "صناعه", "صناعي"),
    "contracting": ("contracting", "construction", "projects", "building", "engineering", "مقاولات", "مقاوله", "بناء",
                    "مشاريع", "انشاءات", "هندسه"),
    "restaurant": ("restaurant", "cafe", "coffee", "food", "catering", "مطعم", "كافيه", "مقهى", "اغذيه", "ضيافه"),
    "online": ("online", "ecommerce", "e-commerce", "website", "web shop", "webshop", "اونلاين", "متجر الكتروني",
               "موقع", "تجاره الكترونيه"),
}
ROLE_WORDS = {
    "salesperson": ("sales", "salesperson", "salesman", "salesperson", "seller", "مبيعات", "مندوب", "بائع", "مبيعاتي"),
    "accountant": ("accountant", "accounts", "accounting", "finance", "bookkeeper", "محاسب", "محاسبه", "حسابات",
                   "مالي", "ماليه"),
    "warehouse": ("warehouse", "store keeper", "storekeeper", "inventory", "stock", "مخزن", "مستودع", "امين مخزن",
                  "امين المستودع", "مخازن"),
    "hr": ("hr", "human resources", "payroll", "موارد بشريه", "الموارد البشريه", "شؤون الموظفين", "رواتب"),
    "manager": ("manager", "director", "supervisor", "مدير", "مديره", "مسؤول", "مشرف"),
    "admin": ("admin", "administrator", "owner", "ceo", "superuser", "ادمن", "ادارة", "مدير النظام", "صاحب", "مالك",
              "المدير العام", "مدير عام"),
}
INVITE_WORDS = ("invite", "invitation", "send an invitation", "send invitations", "send them an email",
                "send email", "ارسل دعوه", "ارسل دعوة", "ادعي", "ادعيهم", "دعوه", "دعوات", "ارسل لهم ايميل")
MONTHS = {
    1: ("january", "jan", "يناير", "كانون الثاني"), 2: ("february", "feb", "فبراير", "شباط"),
    3: ("march", "mar", "مارس", "اذار"), 4: ("april", "apr", "ابريل", "نيسان"), 5: ("may", "مايو", "ايار"),
    6: ("june", "jun", "يونيو", "حزيران"), 7: ("july", "jul", "يوليو", "تموز"),
    8: ("august", "aug", "اغسطس", "اب"), 9: ("september", "sep", "sept", "سبتمبر", "ايلول"),
    10: ("october", "oct", "اكتوبر", "تشرين الاول"), 11: ("november", "nov", "نوفمبر", "تشرين الثاني"),
    12: ("december", "dec", "ديسمبر", "كانون الاول"),
}


def find_codes(text, words):
    """:return: the keys of ``words`` whose phrases the text holds, in the order they are said"""
    folded = _fold(text)
    found = []
    for key, phrases in words.items():
        positions = []
        for phrase in phrases:
            match = re.search(r"(?<![\w])(?:و|ب|ل|ال|وال|بال|لل)?" + re.escape(arabic.fold(phrase)) + r"(?![\w])", folded)
            if match:
                positions.append(match.start())
        if positions:
            found.append((min(positions), key))
    return [key for _position, key in sorted(found)]


def split_names(text):
    """:return: the names in a list said in words: ``A, B and C`` / ``A و B``"""
    text = re.sub(r"(?i)\b(and|&)\b|\s+و(?=\S)", ",", text or "")
    return [part.strip(" .:-") for part in re.split(r"[\n,;،|]+", text) if part.strip(" .:-")]


def users_answer(text):
    """:return: ``(users, invite)``: ``[{"name", "email", "role"}]`` read from one user a line
    (``Raghad raghad@example.com accountant``), and whether the person asked to send invitations"""
    folded = _fold(text)
    invite = has(folded, INVITE_WORDS) and not re.search(r"\b(no|don.?t|without)\b|بدون|لا ترسل|ما ترسل", folded)
    users = []
    for line in re.split(r"[\n;|]+", text or ""):
        email = EMAIL.search(line)
        if not email:
            continue
        rest = line.replace(email.group(0), " ")
        roles = find_codes(rest, ROLE_WORDS)
        name = rest
        for words in ROLE_WORDS.values():
            for word in words:
                name = re.sub(r"(?i)(?<![\w])(?:as|role|is|-|:|ك|دور)?\s*(?:ال|وال|بال|لل)?" + re.escape(word)
                              + r"(?![\w])", " ", name)
        name = re.sub(r"(?i)\b(as|role|invite|invitation|send|with|email|is|the|a)\b|[:()\-,]", " ", name)
        name = " ".join(name.split())
        users.append({"name": name or email.group(0).split("@")[0].title(), "email": email.group(0).lower(),
                      "role": roles[0] if roles else "salesperson"})
    return users, invite


def fiscal_answer(text):
    """:return: ``{"month", "day", "lock_date"}`` read from "year ends 31 December, lock until 2025-12-31"""
    folded = _fold(text)
    out = {}
    lock = re.search(r"(?:lock|closed|close|قفل|اقفال|اغلاق|مقفل)[^\d]{0,30}(\d{4}-\d{2}-\d{2})", folded)
    if lock:
        out["lock_date"] = lock.group(1)
    for number, names in MONTHS.items():
        for name in names:
            match = re.search(r"(?<![\w])(\d{1,2})\s*(?:of\s+)?" + re.escape(arabic.fold(name)) + r"(?![\w])", folded)
            reverse = re.search(r"(?<![\w])" + re.escape(arabic.fold(name)) + r"\s*(\d{1,2})(?![\w])", folded)
            hit = match or reverse
            if hit and "month" not in out:
                out["month"], out["day"] = number, int(hit.group(1))
    if "month" not in out:
        month = re.search(r"\b(\d{1,2})[/-](\d{1,2})\b", folded)
        if month and not lock:
            out["day"], out["month"] = int(month.group(1)), int(month.group(2))
    return out or None


def formats_answer(text):
    """:return: ``{"date_format", "decimal_point", "thousands_sep", "time_format"}`` read from a
    sample such as ``31/12/2026 and 1,234.56``"""
    folded = _fold(text)
    out = {}
    date = re.search(r"\b(\d{1,4})([/.\-])(\d{1,2})\2(\d{1,4})\b", folded)
    if date:
        first, sep, _middle, last = date.groups()
        pieces = ["%d", "%m", "%Y"] if len(last) == 4 else ["%Y", "%m", "%d"]
        if len(first) == 4:
            pieces = ["%Y", "%m", "%d"]
        elif len(last) == 4 and int(first) <= 12 < int(date.group(3)):
            pieces = ["%m", "%d", "%Y"]
        out["date_format"] = sep.join(pieces)
    elif re.search(r"mm\W+dd\W+yyyy|month first", folded):
        out["date_format"] = "%m/%d/%Y"
    elif re.search(r"dd\W+mm\W+yyyy|day first", folded):
        out["date_format"] = "%d/%m/%Y"
    number = re.search(r"\b\d{1,3}(?:([.,\s])\d{3})+([.,]\d{1,2})?\b", folded)
    if number:
        out["thousands_sep"] = number.group(1).replace(" ", " ")
        if number.group(2):
            out["decimal_point"] = number.group(2)[0]
    if re.search(r"24\s*h|24 hour|٢٤", folded):
        out["time_format"] = "%H:%M:%S"
    elif re.search(r"12\s*h|12 hour|am/pm", folded):
        out["time_format"] = "%I:%M:%S %p"
    return out or None

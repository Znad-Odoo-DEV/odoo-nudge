"""S1: the words the skill router knows beyond the text of a skill (``skill_router.py``).

``SYNONYMS``: groups of words that mean the same, English and Arabic: a question and a skill that
use two words of one group meet through the group. Written once for the work Nudge does in Odoo.

``CUES``: for each skill that comes with Nudge, how people ask for that kind of work when they do not
use its trigger words: the verbs, the things and the situations of it, in English and in Arabic as it
is spoken. A skill written by a company has none of these: its own text is read the same way, and its
trigger words stay the surest way to bring it. Arabic is also what an Arabic sentence written in Latin
letters ("Arabizi") is read against, so each skill has its Arabic cues.
"""

SYNONYMS = (
    "invoice bill receipt invoicing فاتوره فواتير ايصال",
    "customer client buyer purchaser consumer عميل عملاء زبون زبائن",
    "vendor supplier seller مورد موردين",
    "order sale quotation quote sales طلب طلبات عرض عروض بيع مبيعات",
    "product item article goods merchandise منتج منتجات صنف اصناف بضاعه سلعه",
    "stock inventory warehouse shelf shelves storage مخزون مخزن مستودع",
    "email mail message letter newsletter ايميل بريد رساله رسائل",
    "template layout design style format look appearance نموذج قالب تصميم شكل",
    "error fail failed failure broken crash stuck problem issue wrong stopped stop خطا اخطاء فشل مشكله توقف توقفت عطل وقف وقفت",
    "file document attachment attached csv xlsx sheet ملف ملفات مستند مرفق",
    "import upload load ingest rejected استيراد رفع تحميل حمل",
    "report print printing pdf printout تقرير طباعه اطبع",
    "field box column attribute input حقل خانه عمود اعمده",
    "button action key زر",
    "screen page view form window menu شاشه صفحه واجهه نافذه قائمه",
    "find search lookup locate look ابحث دور لاقي لقيت",
    "change modify edit update alter adjust amend تعديل غير بدل عدل",
    "create make add new build generate register انشئ سوي اعمل اضف جديد ابني سجل",
    "delete remove archive cancel احذف الغ ارشف الغي",
    "meeting call discussion conversation minutes audio voice اجتماع مكالمه نقاش صوتي صوت",
    "recording recorded تسجيل",
    "task todo activity reminder remind notify alert مهمه مهام نشاط تذكير ذكرني نبهني تنبيه",
    "schedule recurring repeat daily weekly monthly periodic automatically automatic regularly يوميا اسبوعيا شهريا تلقائي دوري تلقائيا",
    "approve approval signoff authorise authorize permission sign clear موافقه اعتماد اعتمد وافق يوقع توقيع يصدق",
    "manager director boss supervisor head owner مدير مسؤول رئيس",
    "history log trail story timeline previous old revert restore undo rollback تاريخ رجع ارجع استرجع قديم",
    "total sum amount figures count average statistics revenue expense cost spending مجموع اجمالي عدد متوسط ايرادات مصروف مصاريف تكلفه كم",
    "chart graph plot diagram visual dashboard مخطط رسم بياني",
    "website site web online shop webshop ecommerce homepage موقع متجر",
    "module app application addon extension custom تطبيق موديول مديول اضافه برمجيه",
    "setup configure configuration install initial prepare arrange option settings switch enable activate تهيئه ضبط تجهيز جهز ترتيب ارتب خيار تفعيل فعل",
    "assess assessment health overview overall audit review evaluate diagnose فحص تقييم قيم مراجعه نظره شامله",
    "company branch country currency language fiscal شركه فرع عمله لغه دوله",
    "guide tour walk point navigate where دلني ورجيني وين اين وجهني جوله",
    "bulk batch many every each mass multiple together once جميع دفعه جماعي بالجمله",
    "spreadsheet workbook formula formulas table xlsx جدول ورقه صيغه صيغ",
    "automation rule trigger cron scheduled whenever قاعده اتمته",
    "photo picture image scan scanned screenshot صوره صور مسح",
    "person employee user staff salesperson team موظف مستخدم فريق",
    "payment pay paid payroll salary مدفوعات دفع رواتب راتب",
    "contact partner lead prospect جهه اتصال",
    "discount price pricing rate سعر اسعار خصم",
    "tax vat taxes ضريبه ضريبي ضرائب",
    "happened happen حصل صار",
    "close finish complete اغلق اقفل انهي",
    "draft مسوده مسودات",
    "overtime hours attendance حضور اضافي ساعات",
    "deliver delivery shipment ship shipping dispatch توصيل تسليم شحنه شحن",
    "late overdue delayed pending outstanding unpaid متاخر متاخره معلق مستحق",
    "reservation booking appointment حجز حجوزات موعد",
    "package bundle zip installable حزمه تركيب",
    "exception traceback crash استثناء",
    "weekly week friday monday daily morning evening night afternoon اسبوع جمعه صبح ليل عصر مساء",
    "decision decisions owner owners attendee agreed قرار قرارات مسؤول اتفقنا",
)

CUES = {
    "odoo-setup": (
        "recommend advise suggest best practice approach arrangement structure policy turn tick option switch "
        "enable activate feature process flow steps stage stages receive receiving reception dock putaway rate "
        "landed costs valuation costing tracking serial barcode units variants pricelist multi currency "
        "suit fits business what should we how should we what do i need can odoo "
        "افعل فعل شغل تفعيل خيار ميزه خاصيه انسب افضل طريقه ترتيب رتب نظام مرحله مراحل استلام استلم ينفع يناسب "
        "سياسه كيف ارتب تتبع ارقام تسلسليه دفعات تكلفه"),
    "import-files": (
        "rejected rejects reject refused load loading upload comma separated column columns mismatch line up "
        "duplicates header rows validate before list customers contacts leads excel csv go in bring in "
        "ملف رفع انرفع حمل احمل اعمده عمود صفوف تكرار سليمه ازاي قائمه اكسل تحميل ينفع ادخل ارقام جوالات"),
    "email-design": (
        "message note nice looking professional branded logo header sample style welcome buyer confirmation "
        "mail sent goes out beautiful "
        "رساله رسائل جميله راقي شعار ترحيب نموذج نسخه تجريبيه صمم تصميم ايميل بريد ترسل تنرسل اكتب"),
    "designed-emails": (
        "message note nice looking professional branded logo header sample style welcome buyer confirmation "
        "mail sent goes out beautiful "
        "رساله رسائل جميله راقي شعار ترحيب نموذج نسخه تجريبيه صمم تصميم ايميل بريد ترسل تنرسل اكتب"),
    "failure-diagnosis": (
        "stopped halfway nobody knows why does nothing anymore since yesterday last night stuck wrong output "
        "strange message log ran never finished silently not doing exception traceback throwing keeps "
        "whenever it runs every night "
        "وقف توقف وقفت ما اشتغل ما كمل غريبه رساله خطا امس ليش سبب ما تسوي ما يسوي معلق استثناء كل ليله تعطيني"),
    "module-scaffold": (
        "skeleton bespoke application new app own models menus install installable package zip download "
        "tracking rentals fleet from scratch code developer ship membership system gym "
        "تطبيق هيكل جديد خاص اسطول تاجير تتبع جداول قوائم انزل برمجه حزمه تركيب جاهزه نظام اداره"),
    "database-assessment": (
        "overall picture healthy health overview where to begin improving installed unused nobody uses missing "
        "apps status condition configured badly mistakes wrong problems review everything "
        "نظره شامله وضع النظام ناقص مثبت مثبته يستخدم ما حد تحسين اعدادات غلط اشرح قاعده بيانات"),
    "data-analysis": (
        "which most least best worst top delay late average over year quarter breakdown break down by category "
        "per brought revenue compare trend how long figures numbers how many how much count each month "
        "اكثر اقل افضل ايرادات الربع متوسط تاخير حسب تصنيف مقارنه كم صرفنا مصروف عدد بكل شهر السنه متاخره"),
    "record-work": (
        "look up register new contact phone mark confirmed set date due update cancel open find move edit "
        "purchase order delivery "
        "دور ابحث سجل جهه اتصال عدل بدل تاريخ استحقاق اعتمد افتح موعد طلب شراء حق"),
    "bulk-work": (
        "every each close open tasks at once in one go same discount tag status draft apply all together "
        "pending older cancel many "
        "جميع كل مره وحده دفعه واحده غير الحاله اغلق الغ كل القديمه"),
    "recurring-tasks": (
        "start of each week every day morning notify remind watch for new tell me about summary digest list "
        "regularly friday afternoon email me send me still open "
        "كل يوم الصبح ملخص نبهني اذا وصل جديد اسبوع يوم جمعه عصر ابعث لي قائمه مفتوحه"),
    "documents-to-records": (
        "scan photo picture business card supplier invoice attached create from this paper document pdf enter "
        "the bill put in the system "
        "صوره مرفق فاتوره مورد سجلها حول ورقه حطها بالنظام مسوده"),
    "meetings": (
        "recording call discussion voice file who has to do what agreed decisions owners notes attendees talk "
        "board audio pull out "
        "تسجيل مكالمه نقاش صوتي اتفقنا لخص مهام اجتماع قرارات مسؤول مجلس طلع"),
    "record-history": (
        "story happened so far from the beginning who modified changed touched edited price bring old one "
        "back previous what went on after confirmation "
        "ايش صار من البدايه مين بدل غير رجعه كما كان قديم ارجعه شو صار بعد التاكيد"),
    "product-search": (
        "available blue size colour like this picture something similar shoes chair do we have catalogue "
        "wireless headphones under price dollars sneakers white show me items "
        "عندنا لون مقاس يشبه كراسي كرسي موجود حجم ابيض سعر"),
    "database-setup": (
        "just installed new company initial configuration guide currency languages branch country fiscal "
        "prepare getting started brand new start chart of accounts users "
        "ركبنا شركه جديده تجهيز اول عمله لغات فرع بدايه ابدا مستخدمين"),
    "guided-tours": (
        "find page where enter button prints slip point me show me how cannot find menu locate screen click "
        "where can i see where do i go "
        "ما لقيت صفحه وين الق زر ادخل اضغط شاشه اوصل شوف اشوف اشاهد"),
    "automations-build": (
        "whenever confirmed assign activity team first day of every month archive old automatically when saved "
        "run something happens stage reaches follow up call create "
        "لما يتاكد انشئ نشاط تلقائيا اول الشهر ارشف تلقائي اذا صار مرحله متابعه مكالمه"),
    "screens-fields-build": (
        "extra box customer form store tax number layout pdf bank details look of add show on selection field "
        "contact card level "
        "خانه اضافيه نموذج رقم ضريبي شكل طباعه بيانات البنك بطاقه حقل اختيار"),
    "approval-flows": (
        "over above higher than larger than signed off cleared okay director manager before payment discount "
        "expense purchases limit needs someone to sign cfo "
        "فوق اكثر من اكبر لازم يوقعه المدير قبل الدفع يسمح خصم مصروف حد موافقه مشتريات"),
    "website-builder": (
        "home page store selling soap shop site review broken create small business online landing contact "
        "form clinic "
        "صفحه رئيسيه متجر لبيع موقع افحص صلح عياده نموذج تواصل"),
    "spreadsheets": (
        "workbook formulas keep numbers xlsx totals by customer monthly table figures export pivot sheet "
        "pipeline "
        "ملف اكسل ورقه حسابات صيغ مجاميع جدول ارقام صدر"),
    "studio-approvals": (
        "allowed press click validate button two approvers chain two managers require manager approve confirm "
        "button using step form "
        "زر اعتماد موافق ثاني قاعده تمنع خطوه يضغط"),
}

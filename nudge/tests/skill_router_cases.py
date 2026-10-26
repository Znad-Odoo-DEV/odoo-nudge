"""S1: questions in English, Arabic and Arabizi, with the skill each must bring (any of the keys of a set).

``CASES`` (116) are written the way people write: no trigger word on purpose (a few hold one by chance), with
slips of the keys, in the Arabic as it is spoken and in Latin letters and digits. The thresholds and the word
lists of the router were set on them. ``FRESH`` (40) were written afterwards and never used to set anything:
their figures are the honest ones. 156 sentences in all.
"""
EMAIL = ("email-design", "designed-emails")
AUTO = ("automations-build",)

CASES = [
    # odoo-setup
    ("What should we switch on so goods are received at the dock and then moved to shelves by a second person?", ("odoo-setup",), "en"),
    ("We pay overtime at one and a half times, what is the right approach in Odoo for that?", ("odoo-setup",), "en"),
    ("شو الأنسب لشركتنا حتى نستلم البضاعة على مرحلتين ونراجعها؟", ("odoo-setup",), "ar"),
    ("كيف أرتب النظام لشركة مقاولات عندها عدة مواقع ومخزن لكل موقع؟", ("odoo-setup",), "ar"),
    ("kayf nrattib el nizam la sharika 3ndha 3eda mawaqe3 w mukhzen la kol mawqe3", ("odoo-setup",), "az"),
    # import-files
    ("My contacts list in a comma separated file keeps getting rejected when I load it", ("import-files",), "en"),
    ("Can you check this file before I load customers in, I am afraid of duplicates", ("import-files",), "en"),
    ("ليش ملف العملاء ما انرفع وطلعلي أخطاء بالأعمدة؟", ("import-files",), "ar"),
    ("بدي أحمّل قائمة الأصناف من إكسل وأتأكد انها سليمة", ("import-files",), "ar"),
    ("el malaf el 3omala2 ma nrafa3 w tale3 5ata2 bel a3mida", ("import-files",), "az"),
    # email
    ("I want a nice looking message sent to the buyer once his order is confirmed, with our logo", EMAIL + AUTO, "en"),
    ("Make the welcome message to new customers look professional and mail me a sample first", EMAIL, "en"),
    ("ابغى رسالة جميلة تنرسل للعميل عند تأكيد الطلب مع شعارنا", EMAIL + AUTO, "ar"),
    ("صمم لي نموذج رسالة ترحيب للعملاء وارسل لي نسخة تجريبية", EMAIL, "ar"),
    ("baddi risale jamila trooh lal 3emil lamma yet2akkad el talab", EMAIL + AUTO, "az"),
    # failure-diagnosis
    ("Yesterday the scheduled action stopped halfway and nobody knows why", ("failure-diagnosis",), "en"),
    ("The rule I made does nothing anymore since Monday", ("failure-diagnosis",), "en"),
    ("القاعدة اللي سويتها وقفت ما تسوي شي من امس", ("failure-diagnosis",), "ar"),
    ("الاجراء المجدول ما كمل وطلع رسالة غريبة", ("failure-diagnosis",), "ar"),
    ("el qa3ida wa2fat w ma 3am btshteghel mn ams", ("failure-diagnosis",), "az"),
    # module-scaffold
    ("Create a new app for tracking equipment rentals with its own models and menus I can install", ("module-scaffold",), "en"),
    ("I need the skeleton of a bespoke application for our fleet, as a zip", ("module-scaffold",), "en"),
    ("ابني لي تطبيق جديد لادارة تأجير المعدات اقدر انزله", ("module-scaffold",), "ar"),
    ("اريد هيكل تطبيق خاص بجداول وقوائم لتتبع الاسطول", ("module-scaffold",), "ar"),
    ("baddi tatbee2 jdeed la idarat ta2jeer el mu3addat", ("module-scaffold",), "az"),
    # database-assessment
    ("Give me an overall picture of how healthy our Odoo is and where to begin improving", ("database-assessment",), "en"),
    ("Which apps are installed but nobody uses them?", ("database-assessment",), "en"),
    ("اعطني نظرة شاملة على وضع النظام عندنا وايش ناقص", ("database-assessment",), "ar"),
    ("ايش التطبيقات المثبتة وما حد يستخدمها؟", ("database-assessment",), "ar"),
    ("e3tini nathra shamila 3ala wad3 el nizam w shu na2es", ("database-assessment",), "az"),
    # data-analysis
    ("Which salesperson brought the most revenue last quarter?", ("data-analysis",), "en"),
    ("What is the delay of our vendors in delivering, taken over the year?", ("data-analysis",), "en"),
    ("Break down expenses by category for this year", ("data-analysis",), "en"),
    ("مين اكثر موظف مبيعات جاب ايرادات الربع الماضي؟", ("data-analysis",), "ar"),
    ("وريني مبيعات كل شهر بشكل رسم", ("data-analysis", "spreadsheets"), "ar"),
    ("kam sarafna 3ala el rawateb hal sana", ("data-analysis",), "az"),
    # record-work
    ("Look up Raghad's last sales order and mark it as confirmed", ("record-work",), "en"),
    ("Register Ebrahim as a new contact with this phone number", ("record-work",), "en"),
    ("دور على فاتورة راغد وعدّل تاريخ الاستحقاق", ("record-work",), "ar"),
    ("سجل جهة اتصال جديدة باسم ابراهيم", ("record-work",), "ar"),
    ("dawwer 3ala talab raghad w a3temdo", ("record-work", "approval-flows"), "az"),
    # bulk-work
    ("Close every open task of the Alpha project at once", ("bulk-work",), "en"),
    ("Apply a ten percent discount to each of the quotations in draft", ("bulk-work",), "en"),
    ("اغلق جميع المهام المفتوحة في مشروع ألفا مرة وحدة", ("bulk-work",), "ar"),
    ("غير الحالة لكل العروض اللي في المسودة", ("bulk-work",), "ar"),
    ("ghayyer el hala la kol el 3oroodat daf3a wahda", ("bulk-work",), "az"),
    # recurring-tasks
    ("Send me a list of unpaid invoices at the start of each week", ("recurring-tasks",), "en"),
    ("Watch for new leads from the website and tell me about them", ("recurring-tasks", "automations-build"), "en"),
    ("ابغى كل يوم الصبح ملخص الطلبات الجديدة", ("recurring-tasks",), "ar"),
    ("نبهني اذا وصل طلب جديد من عميل كبير", ("recurring-tasks", "automations-build"), "ar"),
    ("kol yom sobo7 ba3tli molakhas el talabat", ("recurring-tasks",), "az"),
    # documents-to-records
    ("Here is a scan of a supplier's invoice, create it in the system", ("documents-to-records",), "en"),
    ("Turn this photo of a business card into a contact", ("documents-to-records",), "en"),
    ("هذي صورة فاتورة من مورد سجلها لي", ("documents-to-records",), "ar"),
    ("حول الملف المرفق الى عرض سعر", ("documents-to-records",), "ar"),
    ("hay soorat fatoora mn mawrid sajjelha", ("documents-to-records",), "az"),
    # meetings
    ("Here is the recording of today's call with the client, write down what we agreed", ("meetings",), "en"),
    ("Who has to do what after this discussion? I have the voice file", ("meetings",), "en"),
    ("هذا تسجيل مكالمة اليوم مع العميل لخص ما اتفقنا عليه", ("meetings",), "ar"),
    ("استخرج المهام من الملف الصوتي للنقاش", ("meetings",), "ar"),
    ("hay tasjeel el ijtima3 lakhess shu ittafaqna", ("meetings",), "az"),
    # record-history
    ("What is the story of this order so far?", ("record-history",), "en"),
    ("Somebody modified the price on this quotation, can you tell who and bring the old one back?", ("record-history",), "en"),
    ("ايش صار بهذا الطلب من البداية؟", ("record-history",), "ar"),
    ("مين بدل السعر في هذا العرض وارجعه زي ما كان", ("record-history",), "ar"),
    ("shu sar b hal talab men el bidaye", ("record-history",), "az"),
    # product-search
    ("Do we have blue running shoes available?", ("product-search",), "en"),
    ("Find me something like the item in this picture", ("product-search",), "en"),
    ("عندنا كراسي مكتب سوداء؟", ("product-search",), "ar"),
    ("دور لي على منتج يشبه الصورة هذي", ("product-search",), "ar"),
    ("3ndna kraasi maktab sooda?", ("product-search",), "az"),
    # database-setup
    ("We just installed Odoo for a new company, guide me through the initial configuration", ("database-setup", "odoo-setup"), "en"),
    ("Prepare the company, currency and languages for our branch in Dubai", ("database-setup",), "en"),
    ("ركبنا اودو لشركة جديدة ساعدني بالتجهيز الاول", ("database-setup",), "ar"),
    ("جهزلي الشركة والعملة واللغات", ("database-setup",), "ar"),
    ("rakkabna odoo la sharika jdeede sa3idni bel tajheez el awwali", ("database-setup",), "az"),
    # guided-tours
    ("I can't find the page where I enter vendor payments", ("guided-tours",), "en"),
    ("Point me to the button that prints the delivery slip", ("guided-tours",), "en"),
    ("ما لقيت الصفحة اللي ادخل فيها مدفوعات الموردين", ("guided-tours",), "ar"),
    ("وين ألقى زر طباعة اذن التسليم؟", ("guided-tours",), "ar"),
    ("ma la2et el safha eli bdakhkhel feha daf3at el mawridin", ("guided-tours",), "az"),
    # automations-build
    ("Whenever an order is confirmed, assign an activity to the warehouse team", AUTO, "en"),
    ("Run something on the first day of every month to archive old records", AUTO, "en"),
    ("لما يتأكد الطلب انشئ نشاط لفريق المستودع تلقائيا", AUTO, "ar"),
    ("كل اول الشهر ارشف السجلات القديمة تلقائياً", AUTO + ("recurring-tasks",), "ar"),
    ("lamma yet2akkad el talab 5ali el nizam ya3mal nashat tilqa2i", AUTO, "az"),
    # screens-fields-build
    ("I need an extra box on the customer form to store the tax number", ("screens-fields-build",), "en"),
    ("Change the layout of the quotation PDF to show our bank details", ("screens-fields-build",), "en"),
    ("ابغى خانة اضافية في نموذج العميل للرقم الضريبي", ("screens-fields-build",), "ar"),
    ("غير شكل طباعة عرض السعر وخلي فيه بيانات البنك", ("screens-fields-build",), "ar"),
    ("baddi 5ana idafiye bel nemouzaj la raqam darebi", ("screens-fields-build",), "az"),
    # approval-flows
    ("Any expense over 5000 should be signed off by the director before payment", ("approval-flows",), "en"),
    ("Discounts higher than 15 percent must be cleared by the sales manager", ("approval-flows",), "en"),
    ("اي مصروف فوق خمسة الاف لازم يوقعه المدير قبل الدفع", ("approval-flows",), "ar"),
    ("الخصم اكثر من 15 بالمية يحتاج مدير المبيعات يسمح فيه", ("approval-flows",), "ar"),
    ("ay masrouf fouq 5000 lazem yousaddiq 3aleh el modeer", ("approval-flows",), "az"),
    # website-builder
    ("Create a home page and a small store for selling handmade soap", ("website-builder",), "en"),
    ("Review my site and tell me what is broken", ("website-builder",), "en"),
    ("سوي لي صفحة رئيسية ومتجر صغير لبيع الصابون", ("website-builder",), "ar"),
    ("افحص الموقع وصلح الاخطاء", ("website-builder",), "ar"),
    ("se3mal li mawqe3 w matjar sagheer la bay3 el saboon", ("website-builder",), "az"),
    # spreadsheets
    ("Put the monthly sales numbers in a workbook with formulas I can keep", ("spreadsheets",), "en"),
    ("Give me an xlsx with totals by customer", ("spreadsheets", "data-analysis"), "en"),
    ("اعطني ملف اكسل فيه مبيعات كل عميل مع المجاميع", ("spreadsheets", "data-analysis"), "ar"),
    ("جهز لي ورقة حسابات فيها صيغ", ("spreadsheets",), "ar"),
    ("e3mel li malaf excel fih mabee3at kol 3emil", ("spreadsheets", "data-analysis"), "az"),
    # studio-approvals
    ("Who is allowed to press the validate button on a purchase and how do I require two approvers?", ("studio-approvals", "approval-flows"), "en"),
    ("Require the manager to approve before the confirm button works, using Studio", ("studio-approvals", "approval-flows"), "en"),
    ("اريد قاعدة تمنع زر التأكيد الا بعد موافقة المدير ثم المالية", ("studio-approvals", "approval-flows"), "ar"),
    ("من يقدر يضغط زر الاعتماد؟ وكيف اضيف موافق ثاني", ("studio-approvals", "approval-flows"), "ar"),
    ("baddi qa3ida ta7ber el zir ela ba3d mowafaqet el modeer", ("studio-approvals", "approval-flows"), "az"),
    # slips of the keys
    ("wich seting should I change for recipts in two steps", ("odoo-setup",), "en"),
    ("improt my custumers from exel", ("import-files",), "en"),
    ("show me totlas of salse per monht in a chrat", ("data-analysis",), "en"),
    ("whats the histroy of this recrod and who chnaged the price", ("record-history",), "en"),
    ("autmation to send mial when ordr confirmed", AUTO + EMAIL, "en"),
]

# Sentences no skill should bring: the router says nothing, the model asks for one if it needs it
NOTHING = [
    "What is the capital of France?", "hello", "thanks a lot", "tell me a joke", "How are you today",
    "ما رأيك بالطقس اليوم", "shukran ktir", "who won the football match", "translate good morning to French",
    "write me a short poem", "مرحبا", "ما هي عاصمة فرنسا",
]

# With a screen: the question holds one word of the work and the screen says the rest
SCREENS = [
    ("it stopped yesterday", {"model": "base.automation", "view_type": "form"}, ("failure-diagnosis", "automations-build")),
    ("make it look better", {"model": "ir.ui.view", "view_type": "form"}, ("screens-fields-build",)),
    ("ما اشتغلت امس", {"model": "ir.cron", "view_type": "form"}, ("failure-diagnosis", "automations-build")),
    ("change the wording and send me a trial", {"model": "mail.template", "view_type": "form"}, ("designed-emails", "email-design")),
    ("which option do I tick for two steps", {"model": "res.config.settings", "title": "Settings Inventory"}, ("odoo-setup",)),
    ("what do you have like this in blue", {"model": "product.template", "view_type": "kanban"}, ("product-search",)),
]


# Written after the thresholds and the word lists were set, and never used to set them: the honest figures
FRESH = [
    ("We buy from abroad; what do I have to enable so landed costs are included in what an item is worth?", ("odoo-setup",), "en"),
    ("A file full of leads from a trade fair needs to go in; will the columns line up?", ("import-files",), "en"),
    ("Draft a branded note that goes out to a client when his payment is received", EMAIL, "en"),
    ("My server action keeps throwing an exception whenever it runs on invoices", ("failure-diagnosis",), "en"),
    ("I want to ship an installable package for a gym membership system", ("module-scaffold",), "en"),
    ("Tell me what is configured badly in our database", ("database-assessment",), "en"),
    ("How many deliveries were late in each month of 2025?", ("data-analysis",), "en"),
    ("Find the purchase order for Ebrahim and move the delivery date", ("record-work",), "en"),
    ("Cancel all pending reservations older than a month in one go", ("bulk-work",), "en"),
    ("Every Friday afternoon email me the list of tasks still open", ("recurring-tasks",), "en"),
    ("Take this PDF from a vendor and enter the bill", ("documents-to-records",), "en"),
    ("Pull out the decisions and owners from the audio of yesterday's board meeting", ("meetings",), "en"),
    ("Which user touched the sale price after confirmation?", ("record-history",), "en"),
    ("Show me wireless headphones under 100 dollars", ("product-search",), "en"),
    ("Start a brand new company in Odoo: its country, chart of accounts and users", ("database-setup",), "en"),
    ("Where can I see unpaid bills from suppliers?", ("guided-tours",), "en"),
    ("When a lead reaches the proposal stage, create a follow up call automatically", AUTO, "en"),
    ("Put the customer's loyalty level as a selection field on their contact card", ("screens-fields-build",), "en"),
    ("Purchases larger than twenty thousand need the CFO's okay", ("approval-flows", "studio-approvals"), "en"),
    ("Make me a landing page with a contact form for my dental clinic", ("website-builder",), "en"),
    ("Export the pipeline numbers into a sheet with a pivot and formulas", ("spreadsheets", "data-analysis"), "en"),
    ("Who can click Approve on the leave form and can I chain two managers?", ("studio-approvals", "approval-flows"), "en"),
    ("ابي اعرف كيف افعّل التتبع بالأرقام التسلسلية للمنتجات", ("odoo-setup",), "ar"),
    ("الاكسل اللي عندي فيه ارقام جوالات العملاء، ممكن تتأكد انه ينفع للتحميل؟", ("import-files",), "ar"),
    ("اكتب لي رسالة بتصميم راقي تنرسل لما تنسدد الفاتورة", EMAIL, "ar"),
    ("المهمة المجدولة تعطيني استثناء كل ليلة", ("failure-diagnosis",), "ar"),
    ("ابغى اطلع حزمة جاهزة للتركيب لإدارة صالة رياضية", ("module-scaffold",), "ar"),
    ("اشرح لي وش الاعدادات الغلط عندنا في قاعدة البيانات", ("database-assessment", "odoo-setup"), "ar"),
    ("كم عدد الطلبات المتأخرة بكل شهر السنة الماضية؟", ("data-analysis",), "ar"),
    ("ابحث عن طلب الشراء حق ابراهيم وبدّل موعد التسليم", ("record-work",), "ar"),
    ("الغ كل الحجوزات المعلقة القديمة دفعة وحدة", ("bulk-work",), "ar"),
    ("كل جمعة العصر ابعث لي قائمة المهام المفتوحة", ("recurring-tasks",), "ar"),
    ("هذي مسودة من ورق فاتورة مورد حطها بالنظام", ("documents-to-records",), "ar"),
    ("طلع القرارات ومين المسؤول من تسجيل اجتماع المجلس", ("meetings",), "ar"),
    ("مين غير سعر البيع بعد التأكيد؟", ("record-history",), "ar"),
    ("3andkon snakers abyad 7ajem 42?", ("product-search",), "az"),
    ("kif b2dar shouf el fawateer el lesa ma etdafa3et", ("guided-tours",), "az"),
    ("lamma yousal talab jdeed ib3at tanbeeh la modeer el mabee3at", AUTO + ("recurring-tasks",), "az"),
    ("5aleeh yetlob mowafaqet el modeer lamma el khasm ykoon aktar mn 10", ("approval-flows",), "az"),
    ("e3melli mawqe3 sagheer la 3eyadet asnan", ("website-builder",), "az"),
]

# A second set, written after the word lists were extended with what FRESH showed was missing: figures on this
# one are the honest ones, taken once
FRESH2 = [
    ("Can Odoo handle selling the same item in boxes of six and singles?", ("odoo-setup",), "en"),
    ("I've got an XLSX of opening balances to bring into accounting, is anything wrong with it?", ("import-files",), "en"),
    ("Write the message our customers receive after they buy something, make it pretty", EMAIL, "en"),
    ("Last night's job did not finish and left the data half updated", ("failure-diagnosis",), "en"),
    ("Develop a brand new Odoo extension for course registrations with its own tables", ("module-scaffold",), "en"),
    ("Are we using Odoo properly? Where are the gaps?", ("database-assessment",), "en"),
    ("Compare this quarter's income against the previous one", ("data-analysis",), "en"),
    ("Open the contract with Raghad and extend it by six months", ("record-work",), "en"),
    ("Mark every ticket assigned to Ebrahim as resolved", ("bulk-work",), "en"),
    ("Ping me each month end with the aged receivables", ("recurring-tasks",), "en"),
    ("This email has a quote attached; make a draft sales order from it", ("documents-to-records",), "en"),
    ("Turn the Zoom call we had into action points", ("meetings",), "en"),
    ("What was the quantity before somebody changed it on this delivery?", ("record-history",), "en"),
    ("Do you stock leather wallets in brown?", ("product-search",), "en"),
    ("We're opening a subsidiary in Jordan, help me prepare its books and currency", ("database-setup",), "en"),
    ("I never remember how to reach the payslips screen", ("guided-tours",), "en"),
    ("As soon as a ticket is marked urgent, notify the support lead", AUTO, "en"),
    ("Add a checkbox on the vendor form for preferred supplier", ("screens-fields-build",), "en"),
    ("A refund above 500 must get an ok from the accountant first", ("approval-flows", "studio-approvals"), "en"),
    ("I need a small online catalogue with a shopping cart for my bakery", ("website-builder",), "en"),
    ("plese make a spredsheet of expenes per departmnt", ("spreadsheets", "data-analysis"), "en"),
    ("evrey monday send me the overdue invoces", ("recurring-tasks",), "en"),
    ("كيف اخلي النظام يحسب العمولة لكل مندوب حسب هدفه؟", ("odoo-setup",), "ar"),
    ("في الملف عمود اسمه الرقم المرجعي، راح ينقبل لما احمله؟", ("import-files",), "ar"),
    ("ابي تصميم لطيف للإيميل اللي يوصل المورد لما نرسل له طلب شراء", EMAIL, "ar"),
    ("ليش التحديث الليلي ما خلص مبارح؟", ("failure-diagnosis",), "ar"),
    ("طور لي اضافة جديدة لتسجيل الدورات التدريبية بجداولها الخاصة", ("module-scaffold",), "ar"),
    ("قيّم لي استخدامنا للنظام وين الثغرات", ("database-assessment",), "ar"),
    ("قارن دخل هالربع بالربع اللي قبله", ("data-analysis",), "ar"),
    ("افتح عقد راغد ومدد المدة ستة اشهر", ("record-work",), "ar"),
    ("حط علامة تم الحل على كل التذاكر اللي عند ابراهيم", ("bulk-work",), "ar"),
    ("كل نهاية شهر ذكرني بأعمار الديون", ("recurring-tasks",), "ar"),
    ("هذا ايميل فيه عرض سعر مرفق سوي منه طلب مبيعات مسودة", ("documents-to-records",), "ar"),
    ("حول المكالمة اللي سويناها على زوم الى نقاط عمل", ("meetings",), "ar"),
    ("kif ba3ref shou kan el se3er abel ma yghayyero 3al talab", ("record-history",), "az"),
    ("3ndkon mahafez jeld bunni?", ("product-search",), "az"),
    ("ana fatah far3 jdeed bel ordon sa3idni a jahhez el 7sabat wel 3emle", ("database-setup",), "az"),
    ("ma bala2i el shasha taba3 el rawateb", ("guided-tours",), "az"),
    ("awal ma yo3allem el tiket 3ajel nabbeh mas2oul el da3m", AUTO, "az"),
    ("lazem mowafaqet el mohaseb abel ma yersal el estirdad aktar mn 500", ("approval-flows",), "az"),
]

# Arabizi written after the round that tuned the Arabizi reading: never used to set a weight or a word list
HELD_OUT_AZ = [
    ("shu el tareeqa la nfa3el el tasleem 3ala marhaltein bel mustawda3", ("odoo-setup",), "az"),
    ("el malaf taba3 el mowarridin ma 5ala9 w tale3 fi 5ata2", ("import-files",), "az"),
    ("7ebb a3mel taswim helo lal risale elli btousal lal zboun ba3d el shira", EMAIL, "az"),
    ("el mohema el mojadwala wa2afat w ma 3reft laysh", ("failure-diagnosis",), "az"),
    ("bdi a3mel modul jdeed la tasjeel el tadreeb", ("module-scaffold",), "az"),
    ("2olli shu el barameg el mothabbata w mish mostakhdama", ("database-assessment",), "az"),
    ("meen aktar bayyi3 jab dakhl bel rob3 el madi", ("data-analysis",), "az"),
    ("eftah el 3aqd taba3 raghad w maddid el modda sitta ashhor", ("record-work",), "az"),
    ("ghayyer el hala la kol el tazakir elli 3and ebrahim", ("bulk-work",), "az"),
    ("kol akher el shahr zakkirni bel fawateer el mota2akhkhira", ("recurring-tasks",), "az"),
    ("shu kan el se3er abl ma ye3adduloh bel talab", ("record-history",), "az"),
    ("fi 3ndkon shanta jild asmar", ("product-search",), "az"),
]

# A second batch, written blind (Gulf and Egyptian spellings too) and only measured at the start and at the end
HELD_OUT_AZ2 = [
    ("ayz a3raf ezay a5ally el nezam yeh7seb el 3omola le kol mandoob 7asab el target", ("odoo-setup",), "az"),
    ("el sheet el ely 3ndi feeh 3amood esmo el ragm el marge3y, hayetqabel lama a3mello upload?", ("import-files",), "az"),
    ("wesh el khata2 elly tal3 lama ishtaghal el job el layli", ("failure-diagnosis",), "az"),
    ("abi tatbeeq jadeed le idarat el 7odoor wel ensraf yenzel 3ndi", ("module-scaffold",), "az"),
    ("qaren el dakhl bta3 el sana di bel sana elly fatet", ("data-analysis",), "az"),
    ("sajjel 3emeel jdeed esmo ebrahim wa rakamo 0501234567", ("record-work",), "az"),
    ("kol youm el sobh ba3tholi mulakhas bel talabat el jdeeda", ("recurring-tasks",), "az"),
    ("fa7lil el egtema3 elly 3amalnah ams w tal3 li el qararat", ("meetings",), "az"),
    ("meen ghayyar el se3r 3ala hal talab w emta", ("record-history",), "az"),
    ("e7na 3ayzeen mawqe3 saghir le matjar el 7alawiyat", ("website-builder",), "az"),
    ("wein a2dar alqa shashat el mowazzafeen", ("guided-tours",), "az"),
    ("lazem el modeer yowafeq abl ma ye3taded ay masroof fo2 alf dirham", ("approval-flows", "studio-approvals"), "az"),
]

# A third batch, written blind on 2026-10-25 (before its misses were looked at), only measured at the start and
# at the end of the round that added vocabulary
HELD_OUT_AZ3 = [
    ("ana 3ayez a3mel e3dad awwali lel sherka el gedida: el 3omla wel lo3at wel far3", ("database-setup",), "az"),
    ("saweli automation yaksar nashat la mas2ool el mabee3at awal ma yetaakkad el talab", AUTO, "az"),
    ("shoofli sora lel fatoora wa a3melha musawwada bel nizam", ("documents-to-records",), "az"),
    ("bdi a7ot 5ana ziyada bel namouzaj taba3 el zboun la raqam el dariba", ("screens-fields-build",), "az"),
    ("ghayyer sa3er kol el montajat el mofattaha dafa3a wahda", ("bulk-work",), "az"),
    ("a3tini jadwal excel bel mabee3at hasab kol 3emeel w kol shahr", ("spreadsheets", "data-analysis"), "az"),
    ("ayna a2dar a2ra el tagrir 3an el 3omala el mota2akhkhira bel daf3", ("guided-tours", "data-analysis"), "az"),
    ("laysh el rasael elly btetba3at lel zaboon btetla3 kharbaana", ("failure-diagnosis", "email-design", "designed-emails"), "az"),
    ("sajjel el ijtima3 elli 3emelnah el yom w istakhrej el mahaam", ("meetings",), "az"),
    ("eish el mouwafaqat el lazem ya5odha el masroof abl ma yindafa3", ("approval-flows", "studio-approvals"), "az"),
    ("a7taj taqrir 3an ay tatbeeqat ma 7ad bystakhdemha 3andna", ("database-assessment",), "az"),
    ("kam 7abba 3endi men el kursi el abyad fi el makhzan", ("product-search", "data-analysis"), "az"),
]

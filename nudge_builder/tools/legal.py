"""Privacy and terms pages from templates, with the company's own data.

They are a starting text, not legal advice: every page says so and the
final card of the builder tells the person to have them read by a lawyer.
"""

NOTICE = {
    "en": "This text is a starting template. Have it read by a lawyer before the site is published.",
    "ar": "هذا النص قالب أولي. يجب أن يراجعه محام قبل نشر الموقع.",
}

PRIVACY = {
    "en": {
        "name": "Privacy Policy",
        "title": "Privacy Policy",
        "sections": [
            ("Who we are", "{company} ({address}) runs this website. You can write to us at {email}."),
            ("What we collect", "The details you give us in a contact form or an order: name, email, phone and the message or the items you ask for. The site also keeps technical data such as pages visited, through cookies, to make it work and to count visits."),
            ("Why we use it", "To answer you, to deliver what you ordered, to send the documents the law requires and to improve the site. We do not sell your details."),
            ("Who sees it", "Our team, and the providers who host the site, deliver orders or take payments for us, only for that purpose."),
            ("How long we keep it", "For as long as the work with you lasts and as long as the law asks us to keep records."),
            ("Your rights", "You may ask to see, correct or delete your details. Write to {email}."),
        ],
    },
    "ar": {
        "name": "سياسة الخصوصية",
        "title": "سياسة الخصوصية",
        "sections": [
            ("من نحن", "تدير {company} ({address}) هذا الموقع. يمكنك مراسلتنا على {email}."),
            ("ما الذي نجمعه", "البيانات التي تقدمها في نموذج الاتصال أو الطلب: الاسم والبريد والهاتف والرسالة أو المنتجات المطلوبة. كما يحفظ الموقع بيانات تقنية مثل الصفحات المزارة عبر ملفات تعريف الارتباط ليعمل ولنحصي الزيارات."),
            ("لماذا نستخدمها", "للرد عليك وتسليم ما طلبته وإرسال المستندات التي يشترطها القانون وتحسين الموقع. لا نبيع بياناتك."),
            ("من يطلع عليها", "فريقنا ومزودو الخدمة الذين يستضيفون الموقع أو يوصلون الطلبات أو يستلمون المدفوعات نيابة عنا، ولهذا الغرض فقط."),
            ("مدة الاحتفاظ", "طوال مدة التعامل معك وللمدة التي يلزمنا بها القانون بحفظ السجلات."),
            ("حقوقك", "يحق لك طلب الاطلاع على بياناتك أو تصحيحها أو حذفها. راسلنا على {email}."),
        ],
    },
}

TERMS = {
    "en": {
        "name": "Terms and Conditions",
        "title": "Terms and Conditions",
        "sections": [
            ("Use of the site", "By using this website you accept these terms. {company} may change the site and these terms at any time."),
            ("Information", "We work to keep the information correct, but prices, availability and descriptions may change without notice."),
            ("Orders and quotations", "A quotation or an order is final only when {company} confirms it in writing. Delivery dates are estimates."),
            ("Payment and returns", "Payment is due as written on the quotation or the invoice. Returns follow the law in force and the policy we confirmed with you."),
            ("Liability", "To the extent the law allows, {company} is not liable for indirect loss arising from the use of this site."),
            ("Contact", "Questions about these terms: {email}."),
        ],
    },
    "ar": {
        "name": "الشروط والأحكام",
        "title": "الشروط والأحكام",
        "sections": [
            ("استخدام الموقع", "باستخدامك هذا الموقع فإنك توافق على هذه الشروط. يحق لـ {company} تعديل الموقع وهذه الشروط في أي وقت."),
            ("المعلومات", "نحرص على صحة المعلومات، ولكن قد تتغير الأسعار والتوفر والأوصاف دون إشعار."),
            ("الطلبات وعروض الأسعار", "لا يصبح عرض السعر أو الطلب نهائيا إلا بتأكيد كتابي من {company}. مواعيد التسليم تقديرية."),
            ("الدفع والإرجاع", "يستحق الدفع كما هو مكتوب في عرض السعر أو الفاتورة. يخضع الإرجاع للقانون المعمول به وللسياسة التي أكدناها لك."),
            ("المسؤولية", "بالقدر الذي يسمح به القانون، لا تتحمل {company} مسؤولية الخسائر غير المباشرة الناتجة عن استخدام هذا الموقع."),
            ("التواصل", "للاستفسار عن هذه الشروط: {email}."),
        ],
    },
}


def _fill(text, facts):
    return text.format(**facts)


def legal_specs(facts):
    """:param facts: ``company``, ``address``, ``email``
    :return: two page specs in the format of the template pages, `legal_privacy` and `legal_terms`"""
    specs = []
    for key, source, url in (("legal_privacy", PRIVACY, "/privacy-policy"), ("legal_terms", TERMS, "/terms")):
        blocks = [{"snippet": "s_title", "copy": {
            code: {"title": data["title"]} for code, data in source.items()}}]
        for index in range(len(source["en"]["sections"])):
            blocks.append({"snippet": "s_text_block", "copy": {
                code: {"title": data["sections"][index][0], "text": _fill(data["sections"][index][1], facts)}
                for code, data in source.items()}})
        blocks.append({"snippet": "s_text_block", "copy": {
            code: {"title": "", "text": text} for code, text in NOTICE.items()}})
        specs.append({
            "key": key, "kind": "legal", "url": url, "legal": True,
            "name": {code: data["name"] for code, data in source.items()},
            "blocks": blocks,
        })
    return specs

"""Does the message ask a question, rather than ask for something to be done? And does it ask WHETHER to do it?

``asks``: "should I confirm this order?", "what happens if I confirm it?", "why did the automation fail?", "هل أؤكد
هذا الأمر؟", "ليش فشلت الأتمتة؟", "أؤكد الطلب؟" ask a question. "can you confirm this order?", "could you cancel
it", "please confirm it", "confirm this order", "ممكن تأكده", "هل يمكنك تأكيد الطلب؟", "لو سمحت أكد الطلب" ask for
the change.

``asks_whether``: of those questions, the ones about WHETHER the person should or may do it: "should I ...", "can /
may I ...", "is it ok to ...", "do I need to ...", "... or wait?", "هل" with a verb of the person ("هل أؤكد"),
"ممكن / لازم أ...". Only these turn a change the AI prepares into a suggestion under its explanation
(``nudge.conversation._chat_hold``); after "why did it fail?", "how do I ...", "what happens if ..." the change the
AI proposes stays on its card, which waits for the yes anyway.

The line between a question and a request: a request addressed to Nudge (you: "can you", "could you", "ممكن
ت...", "تقدر ت...", "يمكنك"), an imperative ("confirm it", "أكده", with or without "please" or a question mark), a
wish ("I want to", "بدي أ...") or a conditional order ("if so, confirm it", "إذا نعم أكده") asks for the change,
even when it ends in a question mark. Asking Nudge to tell, explain, advise or check ("can you tell me whether I
should confirm it?", "ممكن تقلي إذا لازم أأكده؟") is still a question. No database, no model: words only."""
import re

from ...llm import arabic

MAX_CHARS = 600
# Below this, a sentence that only ends in a question mark answers what Nudge asked ("AED?")
MIN_QUESTION_WORDS = 2
# The kind of the suggestion that holds a change asked about (``nudge.conversation._chat_hold``): chosen, the change
# is prepared on its card as it is (``nudge.noai._serve_held``)
HELD_KIND = "suggested_change"

# The verbs of a change or a look, said as an order: "confirm it", "أكده", "please send the quotation"
_EN_VERBS = (
    "confirm", "cancel", "create", "delete", "remove", "send", "post", "validate", "approve", "reject", "refuse",
    "archive", "unarchive", "set", "write", "change", "edit", "update", "close", "submit", "register", "assign",
    "lock", "unlock", "add", "make", "mark", "print", "duplicate", "merge", "reset", "pay", "book", "schedule",
    "move", "rename", "apply", "publish", "unpublish", "restore", "generate", "convert", "issue", "run", "do",
    "put", "give", "press", "click", "build", "prepare", "draft", "record", "log", "save", "fill", "enter", "turn",
    "switch", "fix", "correct", "invoice", "reserve", "receive", "deliver", "ship", "transfer", "import", "export",
    "open", "show", "list", "find", "count", "tell", "explain", "go", "proceed", "continue", "finish",
)
_AR_VERBS = (
    "اكد", "الغ", "الغي", "انشي", "انشا", "اعمل", "احذف", "امسح", "ارسل", "ابعت", "ابعث", "رحل", "اعتمد", "وافق",
    "ارفض", "ارشف", "عدل", "غير", "اغلق", "سكر", "سجل", "اقفل", "اضف", "ضيف", "اطبع", "ادفع", "حدث", "انقل",
    "نفذ", "كمل", "سوي", "صدر", "ثبت", "حول", "فعل", "اوقف", "شغل", "حط", "خلي", "اكتب", "اضغط", "دوس", "جهز",
    "حضر", "انشر", "ابدا", "افتح", "اعرض", "وريني", "ورني", "هات", "صحح", "اصلح", "استلم", "سلم", "فوتر",
)
# Said before an order, and not changing it: "please", "ok", "then", "لو سمحت", "طيب"
_EN_LEAD = r"(?:(?:please|pls|plz|kindly|then|and|so|ok|okay|now|just|yes|yeah|yep|sure|alright|go\s+ahead\s+and)\s*,?\s+)*"
_AR_LEAD = (r"(?:(?:لو\s+سمحت|من\s+فضلك|رجاء|ارجو|ارجوك|بليز|طيب|طب|تمام|اوكي|اوك|يلا|خلاص|ثم|بعدين|اذن|نعم|اه|"
            r"ايوه|ايوا|ماشي)\s*[,،]?\s*)*")
_SUFFIX = r"(?:ه|ها|هم|هن|لي|ني|يه|يها|وه|وها)?"
IMPERATIVE = re.compile(
    r"^\W*(?:" + _EN_LEAD + r"(?:" + "|".join(_EN_VERBS) + r")\b"
    r"|" + _AR_LEAD + r"(?:و|ف)?(?:" + "|".join(_AR_VERBS) + r")" + _SUFFIX + r"(?![ء-ي]))")

# Words of a request to tell, explain, advise or check: asked of Nudge, they still ask a question
_EN_TELL = (r"(?:think|recommend|advise|suggest|say|tell|explain|know|check|verify|see|look|find\s+out|imagine|guess|"
            r"describe|clarify|confirm\s+(?:if|whether|that)|show\s+me\s+(?:how|why|what|whether|if)|"
            r"help\s+me\s+(?:decide|understand|know|figure|choose|see))\b")
_AR_TELL = (r"(?:تقول|تقلي|تقولي|تقللي|تخبرني|تشرح|تشرحلي|توضح|توضحلي|تفهمني|تنصح|تنصحني|تشوف|تتاكد|"
            r"تفسر|تساعدني\s+(?:افهم|اقرر|اعرف|اختار))")
# A request addressed to Nudge, a suggestion that it acts, a wish, or an order on a condition
REQUEST = re.compile(
    # "can you confirm it", "would you please cancel S00049", "will you post it?"; "why can't you ..." asks why
    r"(?<!\bwhat\s)(?<!\bhow\s)(?<!\bwhy\s)(?<!\bwhen\s)(?<!\bwhere\s)(?<!\bwhich\s)"
    r"\b(?:can|could|would|will|won't|wont|wouldn't|cant|can't)\s+(?:you|u|ya)\s+(?:please\s+|kindly\s+|just\s+)?"
    r"(?!" + _EN_TELL + r")[a-z]"
    r"|\bwould\s+it\s+be\s+possible\b|\bis\s+it\s+(?:ok|okay|fine|alright|possible)\s+(?:if|for)\s+you\b"
    r"|\bfor\s+you\s+to\b"
    r"|\bif\s+(?:so|yes|ok|okay|possible|needed|necessary|fine|you\s+can)\b|\bif\s+not\s*[,،]"
    # At the start of a sentence: "go ahead", "do it", "why don't you", "let's", "I want to", "you can"
    r"|(?:^|[.!?؟,،]\s*)" + _EN_LEAD + r"(?:go\s+ahead|do\s+it|please\s+do|just\s+do|proceed"
    r"|why\s+(?:don't|dont|not|wouldn't)|how\s+about"
    r"|(?:let's|lets|let\s+us)\s+(?!(?:know|see|check|think|find\s+out)\b)"
    r"|(?:i\s+want|i'd\s+like|i\s+would\s+like|i\s+need|we\s+need|i\s+wanna)(?:\s+you)?\s+(?:to\s+)?"
    r"(?!(?:to|know|understand|ask|learn|check|see|find\s+out|be\s+sure)\b)"
    r"|you\s+(?:can|may|should|could|must)\s)"
    # "ممكن تأكده", "هل يمكنك تأكيد الطلب؟", "تقدر تلغيها", "ياريت ترسلها", "بدي تأكده"
    r"|(?:ممكن|ممكنك|ينفع|يمديك|تقدر|بتقدر|تقدري|بتقدري|تستطيع|تستطيعي|يمكنك|بامكانك|باستطاعتك|ياريت|يا\s+ريت"
    r"|لو\s+تكرمت|اتمني|ارجو|رجاء|لو\s+سمحت|من\s+فضلك|بليز|بدي|بدنا|ابغاك|ابيك|عايزك|عاوزك|اريدك|حابب|حاب)"
    r"\s+(?:ان\s+)?(?!" + _AR_TELL + r")(?:ت|بت)[ء-ي]{2,}"
    # "ليش ما تأكده" (why don't you): a suggestion; "إذا نعم أكده": an order on a condition
    r"|(?:ليش|ليه|لماذا|لم)\s+(?:ما|لا|مش|م)\s*(?:ت|بت)[ء-ي]{2,}"
    r"|(?:اذا|لو|في\s+حال)\s+(?:نعم|اه|ايوه|ايوا|ماشي|ممكن|اوكي|تمام|مناسب|ما\s+في\s+مانع|مافي\s+مانع"
    r"|ما\s+في\s+مشكله|مافي\s+مشكله)(?![ء-ي])"
    # "بدي أأكد الطلب", "أريد أن أؤكد الطلب": a wish of the person is an order ("بدي أعرف ..." asks)
    r"|(?:^|[.!?؟,،]\s*)(?:بدي|بدنا|ابغي|ابغا|ابي|نبي|اريد|نريد|عايز|عاوز|عايزه|عاوزه|ودي)\s+(?:ان\s+)?"
    r"(?!(?:اعرف|نعرف|افهم|نفهم|اسال|نسال|استفسر|اتاكد|نتاكد|اشوف|نشوف)(?![ء-ي]))(?:ا|ن)[ء-ي]{2,}")

_EN_FILLER = r"(?:(?:so|ok|okay|and|but|hey|hi|well|also|now|then|nudge|wait)\s*,?\s+)*"
_AR_FILLER = r"(?:(?:طيب|طب|يعني|بس|اذن|يا\s+نودج|نودج|سؤال)\s*[,،:]?\s*)*"
_AUXILIARIES = (
    "is", "are", "was", "were", "am", "has", "have", "had", "does", "do", "did", "can", "could", "will", "would",
    "should", "shall", "may", "might", "must", "ought", "isn't", "aren't", "wasn't", "weren't", "hasn't", "haven't",
    "hadn't", "doesn't", "don't", "didn't", "can't", "cannot", "couldn't", "won't", "wouldn't", "shouldn't",
)
# A question, by its shape: anywhere in a sentence, or at its start (after a word like "so" or "طيب")
ASKING = re.compile(
    # "should I confirm", "do we need to", "whether I should", "do you think", "how to", "what happens"
    r"\b(?:should|shall)\s+(?:i|we)\b"
    r"|\b(?:whether|if)\s+(?:i|we)\s+(?:should|can|could|may|must|need|ought|have\s+to|shall)\b"
    r"|\bdo\s+(?:i|we)\s+(?:need|have)\s+to\b"
    r"|\b(?:do|would)\s+you\s+(?:think|recommend|suggest|advise|say)\b|\bwhat\s+do\s+you\s+think\b"
    r"|\byour\s+(?:advice|opinion|recommendation)\b"
    r"|\b(?:how|whether)\s+(?:to|do|can|should|would)\b|\bwhat\s+(?:happens|would\s+happen|will\s+happen)\b"
    r"|\bis\s+it\s+(?:ok|okay|fine|alright|safe|wise|a\s+good\s+idea|right|correct|better|worth)\b"
    r"|\b(?:want|need|would\s+like|'d\s+like)\s+to\s+(?:know|understand|ask)\b"
    # "confirm it now or wait?", "أأكده ولا لا؟": a choice is asked
    r"|\bor\s+(?:not|no|wait|later|leave\s+it)\b[^.!?؟]*[?؟]"
    r"|(?<![ء-ي])(?:ولا|او|والا)\s+(?:لا|استني|انتظر|نستني|بعدين|اتركه|اخليه)(?![ء-ي])[^.!?؟]*[?؟]"
    # "can I cancel S00049", "is it too early", "should this be posted": a question is asked the other way round;
    # "can you ..." is a request (REQUEST) and "do what the note says" an order. With another subject ("has the
    # customer paid?") the question mark says it
    r"|^\W*" + _EN_FILLER + r"(?:" + "|".join(_AUXILIARIES) + r")\s+"
    r"(?:i|we|it|this|that|there|they|he|she|these|those)\b"
    r"|^\W*" + _EN_FILLER + r"(?:what|why|how|when|which|who|whom|whose|where)(?:'s|'re|\s+(?:is|are|was|were|"
    r"happens|happened|would|will|should|shall|can|could|do|does|did|if|to|much|many|else|exactly|about|kind|"
    r"isn't|aren't|can't|cannot|won't|don't|doesn't|didn't|has|have|i|we))\b"
    # "هل أؤكد", "هل يجب", "كيف أؤكد", "ليش ما بقدر", "شو رأيك", "تنصحني"
    r"|^\W*" + _AR_FILLER + r"(?:هل|ليش|لماذا|ليه|كيف|كيفاش|شلون|متي|امتي|ايمتي|وين|اين|ماذا|شو|ايش|وش|اشمعني|مين"
    r"|ما\s+(?:هو|هي|الذي|اللي|الفرق|معني)|من\s+(?:هو|هي|اللي|الذي))(?![ء-ي])"
    r"|(?<![ء-ي])(?:ب|و)?(?:رايك|تنصحني|تنصح|بتنصحني|تقترح|تنصحنا)(?![ء-ي])"
    r"|(?<![ء-ي])(?:المفروض|مفروض)\s+(?:ان\s+)?(?:ا|ن)(?!ل(?!غ))[ء-ي]{2,}"
    # "بدي أعرف إذا لازم أأكد" (whether I must), "حابب أفهم ..."
    r"|(?<![ء-ي])(?:اذا|لو|ان\s+كان)\s+(?:لازم|يجب|ممكن|ينفع|المفروض|اقدر|بقدر|يمكنني)\s+(?:ان\s+)?(?:ا|ن)[ء-ي]{2,}"
    r"|(?<![ء-ي])(?:بدي|ابي|ابغي|اريد|حابب|حاب|ودي)\s+(?:ان\s+)?(?:اعرف|افهم|اسال|استفسر)(?![ء-ي])"
    # "ممكن أأكد", "لازم ألغي", "بقدر أرحل": what the person may or must do themselves
    r"|^\W*" + _AR_FILLER + r"(?:ممكن|ينفع|يجوز|لازم|يجب|ينبغي|يلزم|اقدر|بقدر|يمكنني|استطيع|بصير|يصير)\s+(?:ان\s+)?"
    r"(?:ا|ن)(?!ل(?!غ))[ء-ي]{2,}")
# Of the questions, those about WHETHER the person should or may do it, said in a clause of their own
WHETHER = re.compile(
    # "should I", "can we", "may I", "can't I"; "do I need to"; "is it ok to"; "whether I should"; "do you think"
    r"\b(?:should|shall|can|could|may|might|must|ought|can't|cannot|couldn't|shouldn't)\s+(?:i|we)\b"
    r"|\b(?:do|does|did|don't)\s+(?:i|we)\s+(?:need|have)\s+to\b"
    r"|\b(?:is|would|will|was|isn't)\s+it\s+(?:be\s+)?(?:ok|okay|fine|alright|all\s+right|safe|wise|possible|allowed|"
    r"a\s+good\s+idea|a\s+bad\s+idea|better|worth|right|correct|too\s+early|too\s+late|time)\b"
    r"|\b(?:whether|if)\s+(?:i|we)\s+(?:should|can|could|may|must|need|ought|have\s+to|shall)\b"
    r"|\b(?:do|would)\s+you\s+(?:think|recommend|suggest|advise)\b|\byour\s+(?:advice|opinion|recommendation)\b"
    r"|\bor\s+(?:not|no|wait|later|leave\s+it)\b"
    # "هل أؤكد", "هل نلغي", "هل يجب", "هل يمكن"; "ممكن أأكد", "لازم ألغي"; "المفروض أ"; "رأيك", "تنصحني"
    r"|^\W*" + _AR_FILLER + r"هل\s+(?:ا(?!ل(?!غ))|ن(?=[ء-ي]{3}))"
    r"|^\W*" + _AR_FILLER + r"هل\s+(?:يجب|يلزم|ينبغي|يصح|ينفع|يجوز|لازم|يمكن(?!ك)|من\s+الافضل|الافضل|المفروض)"
    r"(?![ء-ي])"
    r"|^\W*" + _AR_FILLER + r"(?:ممكن|ينفع|يجوز|لازم|يجب|ينبغي|يلزم|اقدر|بقدر|يمكنني|استطيع|بصير|يصير|المفروض|"
    r"مفروض)\s+(?:ان\s+)?(?:ا|ن)(?!ل(?!غ))[ء-ي]{2,}"
    r"|(?<![ء-ي])(?:اذا|لو|ان\s+كان)\s+(?:لازم|يجب|ممكن|ينفع|المفروض|اقدر|بقدر|يمكنني)\s+(?:ان\s+)?(?:ا|ن)[ء-ي]{2,}"
    r"|(?<![ء-ي])(?:ب|و)?(?:رايك|تنصحني|تنصح|بتنصحني|تقترح|تنصحنا)(?![ء-ي])"
    r"|(?<![ء-ي])(?:ولا|او|والا)\s+(?:لا|استني|انتظر|نستني|بعدين|اتركه|اخليه)(?![ء-ي])"
    # "أؤكد الطلب؟", "أأكد الطلب ولا لا؟": a verb of the person opens the question
    r"|^\W*" + _AR_FILLER + r"(?:اا|او(?!امر))[ء-ي]+[^.!?؟]*[?؟]")
# A clause that asks why, how, when or what happens is not asking whether ("why should I ...?" asks why)
WH_OPENER = re.compile(
    r"^\W*" + _EN_FILLER + r"(?:what(?!\s+do\s+you\s+(?:think|recommend|suggest|say))|why|how|when|which|who|whom|"
    r"whose|where)\b"
    r"|^\W*" + _AR_FILLER + r"(?:ليش|لماذا|ليه|لم|كيف|كيفاش|شلون|متي|امتي|ايمتي|وين|اين|ماذا|ايش|وش|اشمعني|مين|"
    r"شو(?!\s+(?:رايك|بتنصح|تنصح)))(?![ء-ي])")
CLAUSES = re.compile(r"[^,،.!?؟\n]+[.!?؟]*")
SENTENCES = re.compile(r"[^.!?؟\n]+[.!?؟]*")
QUESTION_MARKS = ("?", "؟")


def _folded(text):
    text = str(text or "")[:MAX_CHARS].replace("’", "'").replace("‘", "'")
    return arabic.fold(text).strip()


def asks(text):
    """:return: whether ``text`` asks a question (whether, how or why to do something) rather than for it to be
    done; see the module"""
    folded = _folded(text)
    if not folded or REQUEST.search(folded):
        return False
    sentences = [sentence.strip() for sentence in SENTENCES.findall(folded) if sentence.strip()]
    if not sentences:
        return False
    # An order said in a sentence of its own ("is it paid? then confirm it.") asks for the change
    if any(IMPERATIVE.match(sentence) and not sentence.endswith(QUESTION_MARKS) and not ASKING.search(sentence)
           for sentence in sentences):
        return False
    if any(ASKING.search(sentence) for sentence in sentences):
        return True
    # A sentence of two words or more that ends in a question mark: one word ("AED?", "S00012?") answers what Nudge
    # asked, and a question mark after an order ("confirm it?") does not make it a question
    last = sentences[-1]
    return last.endswith(QUESTION_MARKS) and len(last.split()) >= MIN_QUESTION_WORDS and not IMPERATIVE.match(last)


def asks_whether(text):
    """:return: whether ``text`` asks a question about WHETHER the person should or may do something ("should I
    confirm this order?", "هل أؤكد هذا الأمر؟"), not why, how or what happens; see the module"""
    if not asks(text):
        return False
    clauses = [clause.strip() for clause in CLAUSES.findall(_folded(text)) if clause.strip()]
    return any(WHETHER.search(clause) and not WH_OPENER.match(clause) for clause in clauses)

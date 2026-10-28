"""Is the message a thanks and nothing else?

"Thanks", "thank you very much", "ok thanks", "شكرا", "شكراً جزيلاً", "مشكور", "يعطيك العافية". The whole
message must be the thanks (a word of agreement before it, "again" or "for your help" after it): "thanks, now
create a contact" is a request, and "no thanks" a refusal. Matched on the forms ``normalise`` brings together.
It only says yes or no: the answer is a fixed sentence (``nudge.noai._chat_thanks``). No database."""
from . import normalise

MAX_WORDS = 8

_THANKS = (
    "thanks", "thank you", "thank u", "thankyou", "thanx", "thx", "tnx", "ty", "many thanks", "merci", "shukran",
    "thanks a lot", "thanks so much", "thanks very much", "thank you a lot", "thank you so much",
    "thank you very much", "much appreciated", "appreciate it", "i appreciate it",
    "شكرا", "شكرا لك", "شكرا لكم", "شكرا جزيلا", "شكرا جزيلا لك", "شكرا كثير", "شكرا كتير", "شكرا كثيرا",
    "الف شكر", "ألف شكر", "مشكور", "مشكورة", "مشكورين", "يسلمو", "يسلموا", "تسلم", "تسلمي", "تسلم ايدك",
    "يعطيك العافية", "الله يعطيك العافية", "جزاك الله خير", "جزاك الله خيرا", "بارك الله فيك",
)
# A word of agreement before the thanks
_BEFORE = (
    "ok", "okay", "great", "perfect", "good", "nice", "cool", "alright", "all right", "got it", "excellent",
    "تمام", "ممتاز", "طيب", "اوكي", "حلو", "زين", "رائع", "جميل", "تم",
)
# What may follow it
_AFTER = (
    "again", "nudge", "for your help", "for the help", "for that", "for this", "for helping", "a lot", "so much",
    "very much", "على المساعدة", "على مساعدتك", "على هذا", "يا نودج", "نودج", "مرة ثانية",
)


def _folded(phrase):
    return " ".join(token.norm for token in normalise.tokenize(phrase))


_THANKS_FORMS = frozenset(_folded(phrase) for phrase in _THANKS)
_BEFORE_FORMS = tuple(sorted({_folded(phrase) for phrase in _BEFORE}, key=len, reverse=True))
_AFTER_FORMS = tuple(sorted({_folded(phrase) for phrase in _AFTER}, key=len, reverse=True))


def asks(text):
    """:return: whether ``text`` is only a thanks"""
    tokens = normalise.tokenize((text or "")[:200])
    if not 1 <= len(tokens) <= MAX_WORDS:
        return False
    words = " ".join(token.norm for token in tokens)
    if words in _THANKS_FORMS:
        return True
    for before in _BEFORE_FORMS:
        if words.startswith(before + " "):
            words = words[len(before) + 1:]
            break
    for after in _AFTER_FORMS:
        if words.endswith(" " + after):
            words = words[:-len(after) - 1]
            break
    return words in _THANKS_FORMS

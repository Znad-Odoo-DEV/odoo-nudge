"""Is the person asking what is waiting for them today?

"What is waiting for me today", "what do I have today", "أجندتي اليوم",
"ايش عندي اليوم". A handful of words, in English and in every Arabic dialect,
matched on the forms ``normalise`` brings together. It only says yes or no: the
answer is counted by Odoo as the person (see ``nudge.noai``). No database."""
from . import lexicon, normalise

# Said in Latin letters or in Arabic, as ``normalise.fold`` leaves them
_WAITING = {
    "waiting", "pending", "due", "outstanding", "agenda", "todo", "plate", "schedule", "expect",
    "ينتظر", "ينتظرني", "اجندتي", "منتظر", "معلق", "معلقه", "مطلوب", "اجنده", "جدول", "مستحق", "ناطر", "يستني",
}
_ME = {"me", "my", "mine", "i", "مني", "لي", "علي", "عندي", "لدي", "يومي", "جدولي", "اجندتي", "اجنده"}
_HAVE = {"have", "got", "عندي", "لدي", "علي", "ورايا", "وراي"}


def asks(text):
    """:return: whether ``text`` asks for the agenda of the day of the person who writes it"""
    tokens = normalise.tokenize((text or "")[:200])
    if not 2 <= len(tokens) <= 12:
        return False
    forms = {form for token in tokens for form in (token.norm, token.stem)}
    today = any("today" in lexicon.tags_of((token.stem,)) for token in tokens)
    # A sentence about one kind of record ("invoices due today") is a filter, not an agenda
    about_kind = any(lexicon.tags_of((token.stem,)) & set(lexicon.CATEGORIES_OF_KIND) for token in tokens)
    if about_kind:
        return False
    waiting = bool(forms & _WAITING)
    me = bool(forms & _ME)
    have = bool(forms & _HAVE)
    return (waiting and (me or today)) or (have and today)

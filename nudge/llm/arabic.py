"""The one place where Arabic spelling is made comparable.

The same word is written with or without its vowel marks, its hamza, its stretching, its
article; the memory of answers, the rules that understand a sentence without an AI, the
matching of the columns of a file and the search of the profile all fold a text this way
before they compare. Each of them adds its own steps on top (stems, digits, stop words).
No state, no database, nothing from Odoo."""
import re
import unicodedata

# Tashkeel (fatha, damma, kasra, shadda, sukun...), the dagger alef and the tatweel
MARKS = re.compile("[\u064b-\u065f\u0670\u0640]")
# One form for the letters people write one for the other, and the letters of other
# languages that dialects borrow (Iraqi "چ" and "گ", Maghrebi "ڤ", Urdu and Persian forms)
LETTERS = str.maketrans({
    "\u0623": "\u0627", "\u0625": "\u0627", "\u0622": "\u0627", "\u0671": "\u0627",  # alef forms
    "\u0629": "\u0647", "\u0649": "\u064a",  # ta marbuta, alef maqsura
    "\u0624": "\u0648", "\u0626": "\u064a",  # hamza on waw and on ya
    "\u067e": "\u0628", "\u0686": "\u062c", "\u06a4": "\u0641", "\u06af": "\u0643",
    "\u06a9": "\u0643", "\u06cc": "\u064a", "\u06c1": "\u0647", "\u06be": "\u0647",
})
# Written onto the start of a word; the longest first
ARTICLES = ("\u0648\u0627\u0644", "\u0628\u0627\u0644", "\u0641\u0627\u0644", "\u0643\u0627\u0644",
            "\u0644\u0644", "\u0627\u0644")


def simplify(text):
    """:return: ``text`` without marks and with one form of each letter, case untouched"""
    return MARKS.sub("", text or "").translate(LETTERS)


def fold(text):
    """:return: ``text`` in compatibility form, lower case, without marks, with one form of
    each letter"""
    return simplify(unicodedata.normalize("NFKC", str(text or "")).casefold())


def strip_article(word, keep=3):
    """:return: ``word`` without the Arabic article (and the particle written onto it) when at
    least ``keep`` letters are left"""
    for article in ARTICLES:
        if word.startswith(article) and len(word) - len(article) >= keep:
            return word[len(article):]
    return word

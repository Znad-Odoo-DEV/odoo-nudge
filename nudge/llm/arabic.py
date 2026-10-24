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


# ----------------------------------------------------------------------
# Arabic written in Latin letters and digits ("Arabizi")
# ----------------------------------------------------------------------
# The letters a vowel or a hamza is written with: they are not kept when two spellings of one
# word are compared by their consonants ("fawateer" and "فواتير" are both f-t-r)
VOWEL_LETTERS = "اويء"
# What a pair of Latin letters stands for, once the Arabic is folded; a pair is read before its
# letters. "th" and "dh" are the dialects' own mix of sounds
ARABIZI_PAIRS = {
    "sh": "ش", "kh": "خ", "gh": "غق", "dh": "ذظدضز",
    "ch": "شجك", "ph": "ف", "th": "ثتسذ",
}
# A Latin letter or digit: the consonants it can stand for
ARABIZI_CONSONANTS = {
    "b": "ب", "p": "ب", "t": "تط", "j": "ج", "d": "دض",
    "r": "ر", "z": "زذظ", "s": "سص", "f": "ف", "v": "ف",
    "q": "قك", "k": "ك", "l": "ل", "m": "م", "n": "ن",
    "h": "هح", "g": "جقغك", "c": "كس", "x": "ك",
    # The digits people type for the letters a keyboard does not have
    "3": "ع", "5": "خ", "6": "طت", "7": "حه",
    "8": "غق", "9": "قص",
}
# A Latin vowel, or a letter that is one at times: the vowel letters of Arabic it may be written
# as; it can as well be left out, a short vowel is not written
ARABIZI_VOWELS = {
    "a": "اي", "e": "اي", "i": "يا", "o": "وا", "u": "وا",
    "w": "و", "y": "ي", "2": "ءا",
}
ARABIZI_DIGITS = frozenset("2356789")
# What leaving out a vowel the Arabic word writes, and one it does not write, costs when two
# spellings are compared
COST_UNWRITTEN_VOWEL = 1.0
COST_UNSPOKEN_VOWEL = 0.2
# Combinations of readings tried for one word at most: a word of many ambiguous letters is
# not worth guessing
MAX_READINGS = 256


def skeleton(word):
    """:return: the consonants of an Arabic ``word`` (folded or not), no vowel letter: the form a
    Latin spelling is compared with"""
    return "".join(letter for letter in fold(word)
                   if letter not in VOWEL_LETTERS and "ء" <= letter <= "ي")


def arabizi_units(word):
    """:return: the letters of the Latin ``word`` as ``(letters, is_vowel)``: the Arabic letters
    each can stand for (a pair read as one, a letter written twice once); ``None`` when ``word``
    holds anything else than the letters and digits of Arabizi"""
    word = (word or "").casefold()
    units, i = [], 0
    while i < len(word):
        pair = word[i:i + 2]
        if pair in ARABIZI_PAIRS:
            units.append((ARABIZI_PAIRS[pair], False))
            i += 2
            continue
        char = word[i]
        i += 1
        if char in ARABIZI_CONSONANTS:
            if i >= 2 and word[i - 2] == char:
                continue  # a letter doubled: "mm"
            units.append((ARABIZI_CONSONANTS[char], False))
        elif char in ARABIZI_VOWELS:
            if i >= 2 and word[i - 2] == char:
                continue  # a long vowel: "ee", "oo"
            units.append((ARABIZI_VOWELS[char], True))
        else:
            return None
    return units


def arabizi_skeletons(word):
    """:return: the consonant skeletons the Latin ``word`` can be read as (Arabic letters only), at
    most ``MAX_READINGS``; empty when ``word`` holds anything else than the letters of Arabizi"""
    units = arabizi_units(word)
    if not units:
        return []
    # "fatora", "gdeeda": a last "a" or "e" may be the ta marbuta, written as a "h" in Arabic
    if word.casefold()[-1:] in "ae":
        units.append(("?ه", False))
    readings = [""]
    for letters, vowel in units:
        if vowel:
            continue
        step = list(letters.lstrip("?")) + ([""] if letters[0] == "?" else [])
        readings = list(dict.fromkeys(prefix + letter for prefix in readings for letter in step))
        if len(readings) > MAX_READINGS:
            return []
    return [reading for reading in readings if reading]


def arabizi_cost(word, arabic_word):
    """:return: how far the Latin spelling ``word`` is from the Arabic ``arabic_word`` (0 is a
    spelling that writes every long vowel the Arabic does), or ``None`` when no reading of it gives
    those consonants. Of the words a spelling can be read as, the least costly is the likeliest."""
    units = arabizi_units(word)
    target = fold(arabic_word)
    if not units or not target:
        return None
    if word.casefold()[-1:] in "ae":
        units.append(("?ه", False))
    infinity = float("inf")
    n, m = len(units), len(target)
    cost = [[infinity] * (m + 1) for _ in range(n + 1)]
    cost[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            here = cost[i][j]
            if here == infinity:
                continue
            if j < m and target[j] in VOWEL_LETTERS:
                # A vowel letter of the Arabic the spelling does not write
                cost[i][j + 1] = min(cost[i][j + 1], here + COST_UNWRITTEN_VOWEL)
            if i < n:
                letters, vowel = units[i]
                optional = letters[0] == "?"
                if vowel:
                    cost[i + 1][j] = min(cost[i + 1][j], here + COST_UNSPOKEN_VOWEL)
                elif optional:
                    cost[i + 1][j] = min(cost[i + 1][j], here)
                if j < m and target[j] in letters.lstrip("?"):
                    cost[i + 1][j + 1] = min(cost[i + 1][j + 1], here)
    best = cost[n][m]
    return None if best == infinity else best


def has_arabizi_mark(word):
    """:return: whether the Latin ``word`` is spelled the way only Arabic is: a digit among its
    letters, or a pair like "sh", "kh", "gh" ("th" and "ch" are English as well)"""
    word = (word or "").casefold()
    if any(char in ARABIZI_DIGITS for char in word) and any(char.isalpha() for char in word):
        return True
    return any(pair in word for pair in ("sh", "kh", "gh", "dh"))

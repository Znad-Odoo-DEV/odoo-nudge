"""Matching a question against remembered ones, without any AI.

Both sides are folded the same way (Arabic marks and letter forms, English
case and endings), cut into words, and compared as TF-IDF vectors by the
cosine of the angle between them: 1 is the same words, 0 shares none.
Plain Python, no library: it must run on any Odoo."""
import math
import re
from collections import Counter

from . import arabic

_NOT_WORD = re.compile(r"[^\w]+", re.UNICODE)
ARABIC_LETTER = re.compile("[؀-ۿ]")

# Words that carry no question: they would make every question look alike
STOPWORDS = frozenset(
    "the a an is are was were to of in on at for and or how do does did i my me you your can it this "
    "that what where when which with from be by as".split()
    + "من في على الي عن هل "
    "ما ماذا كيف اين متي "
    "هذا هذه انا لي مع او "
    "و اذا".split())

_AR_PREFIXES = ("وال", "فال", "بال", "كال",
                "لل", "ال")
_AR_SUFFIXES = ("ات", "ون", "ين", "ها", "هم", "ان")
_EN_SUFFIXES = ("ing", "ed", "es", "ly", "s")


def fold(text):
    """:return: ``text`` lower-cased, without Arabic marks, with the letters
    that are written in more than one way unified"""
    return arabic.fold(text)


def _stem(word):
    if ARABIC_LETTER.search(word):
        for prefix in _AR_PREFIXES:
            if word.startswith(prefix) and len(word) - len(prefix) >= 3:
                word = word[len(prefix):]
                break
        for suffix in _AR_SUFFIXES:
            if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                return word[:-len(suffix)]
        return word
    for suffix in _EN_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[:-len(suffix)]
            break
    # create, creating, created: one word
    return word[:-1] if word.endswith("e") and len(word) > 3 else word


def stem(word):
    """:return: ``word`` (already folded) without what is written onto it"""
    return _stem(word)


def words(text):
    """:return: the stems of the meaningful words of ``text``"""
    folded = fold(text)
    return [_stem(word) for word in _NOT_WORD.split(folded) if word and word not in STOPWORDS]


def language(text):
    """:return: "ar" when the letters of ``text`` are mostly Arabic, else "en" """
    letters = re.findall(r"[^\W\d_]", str(text or ""))
    arabic = sum(1 for letter in letters if ARABIC_LETTER.match(letter))
    return "ar" if letters and arabic * 2 >= len(letters) else "en"


def blend(words_score, meaning_score):
    """:return: the score of the words and of the meaning together, when the
    local search model gave one to a candidate: the better of the two, and a
    quarter of the other when both say yes. The meaning never takes from the words."""
    return min(1.0, max(words_score, meaning_score) + 0.25 * min(words_score, meaning_score))


def rank(question, candidates, semantic=None):
    """:param candidates: ``[(key, remembered question)]``
    :param semantic: ``{key: score from 0 to 1}`` by meaning, of the candidates that have one
    :return: ``[(score, key)]`` best first, the candidates sharing no word left out
        (unless they are near in meaning)"""
    asked = Counter(words(question))
    if not candidates or (not asked and not semantic):
        return []
    documents = [(key, Counter(words(text))) for key, text in candidates]
    count = len(documents)
    frequency = Counter(term for _key, terms in documents for term in terms)

    def weight(term):
        return math.log((count + 1) / (frequency.get(term, 0) + 1)) + 1

    def vector(terms):
        return {term: (1 + math.log(number)) * weight(term) for term, number in terms.items()}

    query = vector(asked)
    query_norm = math.sqrt(sum(value * value for value in query.values()))
    ranked = []
    semantic = semantic or {}
    for key, terms in documents:
        document = vector(terms)
        dot = sum(value * document.get(term, 0.0) for term, value in query.items())
        score = 0.0
        if dot > 0:
            norm = math.sqrt(sum(value * value for value in document.values()))
            score = dot / (query_norm * norm)
        if key in semantic:
            score = blend(score, semantic[key])
        if score > 0:
            ranked.append((score, key))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]))
    return ranked


def best(question, candidates, threshold, margin=0.05, semantic=None):
    """:return: ``(key, score)`` of the candidate to answer with, or ``(None, best score)``
    when none reaches ``threshold`` or two come so close that none is surely the one"""
    ranked = rank(question, candidates, semantic)
    if not ranked:
        return None, 0.0
    score, key = ranked[0]
    if score < threshold:
        return None, score
    if len(ranked) > 1 and score - ranked[1][0] < margin:
        return None, score
    return key, score

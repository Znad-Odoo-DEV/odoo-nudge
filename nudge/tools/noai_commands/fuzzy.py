"""A word the lexicon does not know, read as the word it was meant to be.

Two things people do when they type fast: they slip ("opne", "invocies", "موضف") or they write
Arabic in Latin letters and digits ("fawateer", "3omala2", "a3mel"). Both are met here with
rules and the lexicon, no model, no database: a near word of the lexicon (a letter more, less,
changed or swapped: one for a short word, two for a long one), or the Arabic word whose
consonants the Latin spelling can be read as.

A word read this way is a *correction*: it costs confidence and it is reported, so that the
sentence is never understood better than the same sentence typed right, and so that a parse
that needed a correction and stays under the threshold can be offered as "Did you mean".
Numbers, dates, addresses, names in quotes and values are never touched.
"""
import re

from ...llm import arabic
from . import lexicon, normalise

# A word shorter than this is never corrected: one letter changes it into another word
MIN_LENGTH = 4
# From this length a word may be two letters away; below, one
LONG_LENGTH = 8
# What a correction costs in confidence: per letter for a typo, flat for a word read from Latin
PENALTY_TYPO = 0.06
PENALTY_ARABIZI = 0.04
MAX_PENALTY = 0.18
# An Arabic skeleton shorter than this says too little to name a word; one of two consonants
# only counts when the spelling is not longer than the word by more than a letter
MIN_SKELETON = 2
SHORT_SKELETON = 3
MAX_OPTIONS = 4
SHORT_ARABIC = 3
QUOTES = re.compile("\"([^\"]+)\"|(?<!\\w)'([^']+)'(?!\\w)|“([^”]+)”|«([^»]+)»|"
                    "‘([^’]+)’")
LATIN_PREFIXES = ("el", "al", "il")
VALUE_TAGS = ("month=", "weekday=", "number=")


class Option:
    """A word of the lexicon an unknown word may have meant"""
    __slots__ = ("word", "tags", "distance", "via", "rank")

    def __init__(self, word, tags, distance, via, rank=None):
        self.word, self.tags, self.distance, self.via = word, frozenset(tags), distance, via
        # Of two words read from one spelling, the nearer in length wins
        self.rank = distance if rank is None else rank

    def __repr__(self):
        return f"Option({self.word!r}, d={self.distance}, {self.via})"


class Fix:
    """One unknown word and what it may have meant, the likeliest first"""
    __slots__ = ("raw", "options", "chosen")

    def __init__(self, raw, options):
        self.raw, self.options, self.chosen = raw, options, 0

    @property
    def option(self):
        return self.options[self.chosen]

    @property
    def ambiguous(self):
        """:return: whether another option is as likely as the first, with another meaning"""
        first = self.options[0]
        return any(other.tags and other.tags != first.tags and abs(other.rank - first.rank) < 0.05
                   and other.via == first.via
                   for other in self.options[1:])

    @property
    def penalty(self):
        option = self.option
        return PENALTY_TYPO * option.distance if option.via == "typo" else PENALTY_ARABIZI

    def record(self):
        option = self.option
        return {"word": self.raw, "to": option.word, "distance": option.distance, "via": option.via}


# ----------------------------------------------------------------------
# Distance
# ----------------------------------------------------------------------

def _neighbours():
    """:return: ``{letter: letters it is mistaken for}``: the keys next to it on a keyboard, and the Arabic
    letters people write one for the other"""
    near = {}

    def link(one, other):
        near.setdefault(one, set()).add(other)
        near.setdefault(other, set()).add(one)

    # Latin keyboard and the keys of the Arabic one, each row as the keys run
    for rows in (("qwertyuiop", "asdfghjkl", "zxcvbnm"),
                 ("ضصثقفغعهخحج",
                  "شسيبلاتنمكط",
                  "ءؤرلاىةوزظ")):
        for r, row in enumerate(rows):
            for c, letter in enumerate(row):
                for dr in (-1, 0, 1):
                    if 0 <= r + dr < len(rows):
                        for dc in (-1, 0, 1):
                            if 0 <= c + dc < len(rows[r + dr]) and (dr or dc):
                                link(letter, rows[r + dr][c + dc])
    for group in ("ضظ", "تطث", "دضذ", "سصث", "زذظ",
                  "حخهع", "قكغ", "اءي", "iy", "bv", "cks"):
        for one in group:
            for other in group:
                if one != other:
                    link(one, other)
    return near


NEIGHBOURS = _neighbours()


def distance(a, b, limit=2):
    """:return: the number of letters to add, remove or swap (two next to each other) to turn ``a``
    into ``b``, a letter changed for one that is not near it on a keyboard or in sound counting
    twice; more than ``limit`` is ``limit + 1``"""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous2, previous = None, list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        row = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1 if cb in NEIGHBOURS.get(ca, ()) else 2
            row[j] = min(previous[j] + 1, row[j - 1] + 1, previous[j - 1] + cost)
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                row[j] = min(row[j], previous2[j - 2] + 1)
        previous2, previous = previous, row
    return min(previous[-1], limit + 1)


def budget(length, arabic_word=False):
    """:return: how many letters a word of ``length`` letters may be away from the word it meant. Arabic words
    are short ("بيع", "امر"): one of three letters may be a slip, as in the phrase "اوامر البيغ" """
    if length < MIN_LENGTH:
        return 1 if arabic_word and length == SHORT_ARABIC else 0
    return 2 if length >= LONG_LENGTH else 1


def _variants(word, deletions):
    out = {word}
    layer = {word}
    for _ in range(deletions):
        layer = {w[:i] + w[i + 1:] for w in layer for i in range(len(w))}
        out |= layer
    return out


# ----------------------------------------------------------------------
# The index of the lexicon
# ----------------------------------------------------------------------

class Index:
    """The single words of the lexicon, found again by a near spelling or by their consonants"""

    def __init__(self, phrases):
        self.tags = {}
        self.deletes = {}
        self.skeletons = {}
        for key, tags in phrases.items():
            if any(tag.startswith(VALUE_TAGS) for tag in tags):
                continue
            if len(key) == 1:
                self.add(key[0], tags)
            else:
                # "how many": "many" alone says nothing, but a slip of it is read as it, and the
                # phrase is found again once the sentence is read again
                for word in key:
                    self.add(word, (), alone=False)

    def add(self, word, tags, alone=True):
        self.tags[word] = self.tags.get(word, frozenset()) | frozenset(tags)
        if len(word) >= MIN_LENGTH or (len(word) == SHORT_ARABIC and normalise.ARABIC.search(word)):
            for variant in _variants(word, 2 if len(word) >= LONG_LENGTH else 1):
                self.deletes.setdefault(variant, set()).add(word)
        if alone and normalise.ARABIC.search(word):
            skeleton = arabic.skeleton(word)
            if len(skeleton) >= MIN_SKELETON:
                self.skeletons.setdefault(skeleton, set()).add(word)

    def near(self, word):
        """:return: the :class:`Option` of the words within the budget of ``word``, nearest first"""
        allowed = budget(len(word), bool(normalise.ARABIC.search(word)))
        if allowed == 0:
            return []
        found = {}
        for variant in _variants(word, 2 if len(word) >= LONG_LENGTH else 1):
            for other in self.deletes.get(variant, ()):
                if other != word and other not in found:
                    found[other] = distance(word, other, allowed)
        # Two words with the same letters in another order are the likelier slip
        return sorted((Option(other, self.tags[other], d, "typo", d - 0.5 * (sorted(other) == sorted(word)))
                       for other, d in found.items() if d <= allowed),
                      key=lambda option: (option.rank, option.word))

    def read(self, word):
        """:return: the :class:`Option` of the Arabic words whose consonants the Latin ``word`` can be
        read as, the one whose length is nearest to the spelling first"""
        found = {}
        marked = arabic.has_arabizi_mark(word)
        for skeleton in arabic.arabizi_skeletons(word):
            if len(skeleton) >= MIN_SKELETON:
                for other in self.skeletons.get(skeleton, ()):
                    cost = arabic.arabizi_cost(word, other)
                    if cost is not None and (len(skeleton) >= SHORT_SKELETON or (marked and cost <= 0.5)):
                        found[other] = cost
        return [Option(other, self.tags[other], 0, "arabizi", cost)
                for other, cost in sorted(found.items(), key=lambda item: (item[1], item[0]))]


_INDEX = None


def index():
    """:return: the :class:`Index` of the lexicon files, built the first time it is asked for"""
    global _INDEX
    if _INDEX is None:
        _INDEX = Index(lexicon.PHRASES)
    return _INDEX


def _extra_index():
    extra = lexicon.extra_words()
    return Index(extra) if extra else None


# ----------------------------------------------------------------------
# The words of a sentence
# ----------------------------------------------------------------------

def quoted_words(text):
    """:return: the set of the words written between quotes in ``text``: names and values to leave alone"""
    words = set()
    for match in QUOTES.finditer(text or ""):
        segment = next(group for group in match.groups() if group)
        words.update(token.norm for token in normalise.tokenize(segment))
    return words


def _distinct(options):
    """:return: ``options`` with one per meaning, the order kept, at most ``MAX_OPTIONS``"""
    seen, out = set(), []
    for option in options:
        if option.tags not in seen:
            seen.add(option.tags)
            out.append(option)
    return out[:MAX_OPTIONS]


def suggest(token, first=False):
    """:param first: the word opens the sentence, where a capital letter says nothing
    :return: the :class:`Fix` of ``token`` (a word nothing in the lexicon matches), or ``None``"""
    raw = token.raw
    word = token.stem
    if not word or any(char in word for char in "@/+-.") or raw.isdigit():
        return None
    arabic_word = bool(normalise.ARABIC.search(word))
    if not arabic_word:
        if not word.isascii():
            return None
        if raw[:1].isupper() and not first and not raw.isupper():
            return None  # a name
        if any(char.isdigit() for char in word) and not arabic.has_arabizi_mark(word):
            return None
    elif not word.isalpha():
        return None
    indexes = [index()]
    extra = _extra_index()
    if extra:
        indexes.append(extra)
    candidates = [word]
    if not arabic_word:
        for prefix in LATIN_PREFIXES:
            if word.startswith(prefix) and len(word) - len(prefix) >= MIN_LENGTH:
                candidates.append(word[len(prefix):])
    options = []
    for candidate in candidates:
        for one in indexes:
            if arabic_word:
                options += one.near(candidate)
            elif any(char.isdigit() for char in candidate):
                options += one.read(candidate)
            else:
                options += one.near(candidate) or one.read(candidate)
        if options:
            break
    options = _distinct(sorted(options, key=lambda option: (option.distance, option.rank)))
    return Fix(raw, options) if options else None


def penalty(fixes):
    """:return: what the corrections cost in confidence, at most ``MAX_PENALTY``"""
    return min(MAX_PENALTY, sum(fix.penalty for fix in fixes))


def corrected_text(text, fixes):
    """:return: ``text`` with each corrected word replaced by the word it was read as"""
    for fix in fixes:
        text = re.sub(r"(?<!\w)" + re.escape(fix.raw) + r"(?!\w)", lambda _m, w=fix.option.word: w, text,
                      count=1)
    return text

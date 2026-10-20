"""The words of the grammar, loaded from the files of ``lexicon/``.

One file per group of dialects (Modern Standard, Gulf, Egyptian, Iraqi,
Levantine, Maghrebi, Sudanese and Yemeni, English, Arabizi). Every file has
the same shape: a category and the phrases that say it. The groups are only
a way to keep the files easy to extend: the parser merges them, so a phrase
of any group means the same in every sentence.
"""
import contextlib
import contextvars
import json
import os

from . import normalise

DIRECTORY = os.path.join(os.path.dirname(__file__), "lexicon")
# Categories whose value is a number: month, weekday, number
VALUED = {"months": "month", "weekdays": "weekday", "numbers": "number"}


def _load():
    phrases = {}
    groups = {}
    for name in sorted(os.listdir(DIRECTORY)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(DIRECTORY, name), encoding="utf-8") as handle:
            data = json.load(handle)
        group = name[:-5]
        for category, entries in data.items():
            if category in VALUED:
                pairs = [(phrase, f"{VALUED[category]}={value}") for phrase, value in entries.items()]
            else:
                pairs = [(phrase, category) for phrase in entries]
            for phrase, tag in pairs:
                key = normalise.phrase_key(phrase)
                if not key:
                    continue
                phrases.setdefault(key, set()).add(tag)
                groups.setdefault(group, set()).add(key)
    return {key: frozenset(tags) for key, tags in phrases.items()}, groups


PHRASES, GROUPS = _load()
MAX_LEN = max(len(key) for key in PHRASES)


def phrases_of(category, group=None):
    """:return: the phrases of ``category``, as keys; of one group when given"""
    keys = GROUPS[group] if group else PHRASES
    return [key for key in keys if category in PHRASES[key]]


# The words a company added (``nudge.lexicon.entry``), for the parse that runs
# inside ``extended``: they add to the files, they never replace a word of them
_EXTRA = contextvars.ContextVar("nudge_lexicon_extra", default=None)
CATEGORIES = frozenset(tag for tags in PHRASES.values() for tag in tags if "=" not in tag)


def tags_of(key):
    """:return: the categories of the phrase ``key`` (a tuple of stems), the company's words included"""
    tags = PHRASES.get(key, frozenset())
    extra = _EXTRA.get()
    if extra and key in extra:
        return tags | extra[key]
    return tags


def max_len():
    """:return: the number of words of the longest phrase, the company's words included"""
    extra = _EXTRA.get()
    return max(MAX_LEN, max((len(key) for key in extra), default=0)) if extra else MAX_LEN


@contextlib.contextmanager
def extended(pairs):
    """Understand the ``(phrase, category)`` pairs too, in the block"""
    extra = {}
    for phrase, category in pairs:
        key = normalise.phrase_key(phrase)
        if key and category in CATEGORIES:
            extra[key] = extra.get(key, frozenset()) | {category}
    token = _EXTRA.set(extra or None)
    try:
        yield
    finally:
        _EXTRA.reset(token)

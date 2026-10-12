"""Looking up the profile for one person.

The profile describes the whole database. What one person gets of it is
decided here, each time, from their rights at that moment: a slice about a
model is for those who can read that model, a restricted slice is for the
managers of Nudge.
"""
import re

from . import activity

MAX_RESULTS = 6
MAX_CHARS = 5000
MAX_LOCATED = 6
MAX_AT_WORK = 15
# Between two pieces that match as well, the one that tells the most comes first
KINDS = ("overview", "module", "configuration", "model", "automation", "security", "activity")
WORD = re.compile(r"[\w.]+", re.UNICODE)
# Arabic is written in many ways: the same word with or without its
# vowels, its hamza, its article
ARABIC_MARKS = re.compile("[\u064b-\u0652\u0640]")
ARABIC_LETTERS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه", "ى": "ي",
                                "ؤ": "و", "ئ": "ي"})
ARABIC_ARTICLES = ("وال", "بال", "فال", "كال", "لل", "ال")


def can_read(env, piece):
    """:param env: environment of the person, not sudo
    :param piece: ``nudge.profile.slice`` record, read in sudo"""
    if piece.restricted and not env.user.has_group("nudge.group_nudge_manager"):
        return False
    if piece.model_name:
        if piece.model_name not in env:
            return False
        return env[piece.model_name].has_access("read")
    return True


def _fold(word):
    word = ARABIC_MARKS.sub("", word).translate(ARABIC_LETTERS)
    for article in ARABIC_ARTICLES:
        if word.startswith(article) and len(word) - len(article) >= 3:
            return word[len(article):]
    return word


def _words(text):
    # Imported here: the tools import the profile
    from ..tools.commands import root_of  # noqa: PLC0415

    words = set()
    for word in WORD.findall((text or "").lower()):
        # "hr.payslip" is found by "payslip" too
        for part in {word, *word.replace("_", ".").split(".")}:
            # By the root: "عرض السعر" meets "عروض الأسعار", "invoice" "Invoices"
            part = root_of(_fold(part))
            if len(part) > 1:
                words.add(part)
    return words


def _score(piece, words, title=None, body=None):
    title = _words(f"{piece.key} {piece.title}") if title is None else title
    body = _words(piece._get_summary()) if body is None else body
    score = 0
    for word in words:
        if word in title:
            score += 5
        elif any(word in candidate for candidate in title):
            score += 3
        elif word in body:
            score += 2
        elif any(word in candidate for candidate in body):
            score += 1
    return score


def search(env, profile, query, kind=None):
    """:return: slices of ``profile`` the person may read, best match first"""
    words = _words(query)
    found = []
    for piece in profile.sudo().slice_ids:
        # The use of the data is looked up by locate()
        if (kind and piece.kind != kind) or (not kind and piece.kind == "activity"):
            continue
        score = _score(piece, words)
        if score and can_read(env, piece):
            found.append((score, piece))
    found.sort(key=lambda item: (-item[0], KINDS.index(item[1].kind), item[1].key))
    return [piece for _score_value, piece in found[:MAX_RESULTS]]


def _activity_words(data):
    names = [data.get("model", ""), data.get("label", ""), *(data.get("labels") or {}).values()]
    places = [data.get("module", ""), data.get("module_title", "")]
    for menu in data.get("menus") or []:
        for path in (menu.get("paths") or {}).values():
            # The menu is what people call the records: "Customers", "فواتير العملاء"
            names.append((path or "").rsplit("/", 1)[-1])
            places.append(path or "")
    return _words(" ".join(names)), _words(" ".join(places))


def locate(env, profile, query):
    """:return: (slices, strong): the use of the data about ``query``
    (activity slices) that the person may read: the ``strong`` first ones
    match best, the most used first; the others match less"""
    words = _words(query)
    found = []
    for piece in profile.sudo().slice_ids:
        if piece.kind != "activity":
            continue
        title, body = _activity_words(piece.data or {})
        score = _score(piece, words, title, body)
        if score and can_read(env, piece):
            found.append((score, piece))
    if not found:
        return [], 0
    best = max(score for score, _piece in found)
    # Among what matches well, the business decides: where it works comes first
    found.sort(key=lambda item: (
        item[0] * 4 < best * 3,
        -activity.activity(item[1].data or {}),
        -item[0],
        # What a menu opens is what people work with, not a setting behind it
        not (item[1].data or {}).get("menus"),
        -((item[1].data or {}).get("records") or 0),
        item[1].key,
    ))
    found = found[:MAX_LOCATED]
    return [piece for _score_value, piece in found], sum(score * 4 >= best * 3 for score, _piece in found)


def label_of(env, data):
    """:return: the name of the kind of record in the person's language"""
    labels = data.get("labels") or {}
    return labels.get(env.lang or "en_US") or data.get("label") or data.get("model", "")


def where_the_work_is(env, profile):
    """:return: the kinds of records most used in this database, among
    those the person may read, in words rather than numbers; empty when
    the database has not been read yet"""
    pieces = [
        piece for piece in profile.sudo().slice_ids
        if piece.kind == "activity" and (piece.data or {}).get("changed")
    ]
    pieces.sort(key=lambda piece: (-activity.activity(piece.data), piece.key))
    lines = []
    for piece in pieces:
        if len(lines) >= MAX_AT_WORK:
            break
        if not can_read(env, piece):
            continue
        data = piece.data
        origin = data.get("module_title") or data.get("module") or ""
        if data.get("custom"):
            origin += ", custom"
        lines.append(
            f"- {label_of(env, data)} ({data['model']}), {origin}: {activity.level(data['changed'])}")
    if not lines:
        return ""
    return (
        "## Where the work is\n"
        "The kinds of records the people of this database changed most in the last "
        f"{activity.RECENT_DAYS} days, among those this person may read, the most used first. "
        "When a topic is covered by several of them, this is where the business works. "
        "locate_data tells more, about any topic.\n" + "\n".join(lines))


def render(pieces):
    """:return: the slices as text for the model, within a size"""
    parts, size = [], 0
    for piece in pieces:
        text = f"## {piece.title} [{piece.key}]\n{piece._get_summary()}"
        if size + len(text) > MAX_CHARS:
            text = text[:max(0, MAX_CHARS - size)]
        if not text:
            break
        parts.append(text)
        size += len(text)
    return "\n\n".join(parts)

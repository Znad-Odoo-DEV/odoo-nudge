"""A sentence turned into what is asked, by rules: no model, no database.

``parse`` finds the verb, the kind of record, the state and the period said
in the sentence, in any of the dialects of the lexicon, and what is left is
the name of the record, the value, the title. It never looks at the data:
the resolution of the names is done by ``nudge.noai``, as the person.
"""
import datetime
import re

from . import fuzzy, lexicon, normalise

KINDS = ("contact", "product", "quotation", "sales_order", "sales", "purchase_order", "invoice", "bill",
         "task", "lead", "ticket", "timeoff", "employee")
STATES = ("unpaid", "paid", "overdue", "draft", "confirmed", "posted", "cancelled")
PERIODS = ("today", "yesterday", "tomorrow", "this_week", "last_week", "this_month", "last_month",
           "next_month", "this_year", "last_year")
# Negations the lexicon may not carry as one word
NEGATING = frozenset(("don't", "dont", "doesn't", "بدون", "لا"))
PRESS = ("press_confirm", "press_validate", "press_post", "press_approve")
FIELDS = ("phone", "mobile", "email", "price", "name", "deadline", "date", "city", "street")
VERBS = ("open", "show", "find", "create", "update", "count", "sum", "avg", "list", *PRESS)
# What carries no meaning of its own
GLUE = frozenset((
    "filler", "question", "to", "for", "and", "this", "latest", "rel", "status_word", "only",
    "times", "neg", "new", "num", "isodate", "year", "chart", "by", "by_user", "by_partner", "by_product",
    "by_month", "by_year", "by_week", "by_state", "type_pie", "type_line", "type_bar",
    "history", "revert", "old", "type_pivot", "save"))
# What a chart can be split by, and the type of chart a word asks for
CHART_GROUPS = ("by_user", "by_partner", "by_product", "by_month", "by_year", "by_week", "by_state")
CHART_TYPES = {"type_pie": "pie", "type_line": "line", "type_bar": "bar"}
# The shapes a saved report takes: the chart types, and the pivot table
REPORT_TYPES = {**CHART_TYPES, "type_pivot": "pivot"}
CREATABLE = ("contact", "product", "quotation", "sales_order", "purchase_order", "invoice", "bill",
             "task", "lead", "ticket", "timeoff")
# Words for a tag or a category of records: the grammar has no kind for them, so a "create" that names one is the
# AI's to answer, not a contact called "category X"
TAG_WORDS = frozenset(("category", "categories", "tag", "tags", "تصنيف", "تصنيفات", "وسم", "وسوم"))
# A sentence that ASKS whether the person themselves should or may do something: it is a question, not a command.
# "can you ..." and "please ..." are addressed to Nudge and stay commands
SELF_QUESTION = re.compile(
    r"^\W*(?:(?:should|can|could|may|might|shall|must|do|does|did|am|would)\s+(?:i|we)\b"
    r"|(?:is|would)\s+it\s+(?:ok|okay|fine|alright|all\s+right)\s+(?:to|if)\b"
    r"|(?:لازم|لزم|هل\s+(?:لازم|يجب|ينفع|يصح))\s+[أا]"
    r"|هل\s+(?:[أا]قدر|[أا]ستطيع|[أا]مكن|يمكنني|يجوز\s+لي|ينفع\s+[أا])"
    r"|(?:ممكن|ينفع|يجوز)\s+[أا]أ"
    # Any "هل" question about a first person verb ("هل أؤكد ...", "هل نلغي ..."), or about what is due, allowed or
    # right ("هل يجب", "هل يصح"); "هل تستطيع ..." / "هل يمكنك ..." are addressed to Nudge and stay commands
    r"|هل\s+(?:[أإ]|ن(?=[ء-ي]{3}))"
    r"|هل\s+(?:يجب|يلزم|ينبغي|يصح|ينفع|يجوز|لازم|يمكن(?!ك))"
    r"|(?:ممكن|ينفع|يجوز|لازم|يجب)\s+[أإ]"
    # A first person verb ("أؤكد", "أأكد": an imperative does not double its hamza) asked about at the end
    r"|(?:أؤ|أأ)[ء-ي]+[^؟?]*[؟?]\s*$)",
    re.IGNORECASE)
# The words of a change the lexicon has no verb for, and of a look: "can I cancel ..." is a question about a change,
# "can I see ..." is a polite request to show
CHANGE_WORDS = frozenset((
    "cancel", "delete", "remove", "send", "refuse", "reject", "archive", "set", "write", "change", "edit", "close",
    "submit", "approve", "validate", "post", "confirm", "register", "assign", "lock", "unlock",
    "الغي", "الغاء", "احذف", "امسح", "ارسل", "ارفض", "ارشف", "عدل", "اغلق"))
READ_WORDS = frozenset((
    "see", "get", "view", "have", "check", "look", "list", "show", "display", "read", "شوف", "اشوف", "أشوف", "اعرض",
    "أعرض", "افتح", "أفتح", "اطلع", "أطلع"))
# The opener of such a question, dropped when the rest is a request to look
SELF_OPENER = re.compile(
    r"^\W*(?:(?:should|can|could|may|might|shall|must|do|does|did|am|would)\s+(?:i|we)\b|هل\s+[أا]قدر|ممكن)\s*",
    re.IGNORECASE)
THRESHOLD = 0.7

NUMBER = re.compile(r"^\d+(?:[.,]\d+)?$")
THOUSANDS = re.compile(r"^\d{1,3}(?:,\d{3})+(?:\.\d+)?$")
ISO_DATE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
SLASH_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


class Span:
    """A word or a phrase of the sentence, with what it can mean"""
    __slots__ = ("i", "j", "cats", "raw", "value", "fix")

    def __init__(self, i, j, cats, raw, value=None, fix=None):
        self.i, self.j, self.cats, self.raw, self.value, self.fix = i, j, set(cats), raw, value, fix

    def __repr__(self):
        return f"Span({self.raw!r}, {sorted(self.cats)})"

    def tagged(self, prefix):
        for cat in self.cats:
            if cat.startswith(prefix + "="):
                return int(cat.split("=", 1)[1])
        return None


class Parse:
    """What a sentence asks"""

    def __init__(self, text):
        self.text = text
        self.intent = ""
        self.confidence = 0.0
        self.kind = None
        self.slots = {}
        self.states = []
        self.period = None
        self.latest = False
        # The words read as other words of the lexicon, and the other sentences the person may
        # have meant when this one is not sure enough to be acted on (see ``parse``)
        self.corrections = []
        self.candidates = []
        # The words of the sentence the lexicon says nothing of: a sentence the rules own has none (or a name)
        self.free = 0

    def __repr__(self):
        return f"Parse({self.intent!r}, {self.confidence:.2f}, kind={self.kind!r}, {self.slots!r})"


# ----------------------------------------------------------------------
# Words
# ----------------------------------------------------------------------

def _literal(token):
    """:return: ``(cats, value)`` of a token that is a number, an address, a date; or None"""
    raw = token.raw
    if "@" in raw and "." in raw.split("@")[-1]:
        return {"email_addr"}, raw
    compact = raw.replace("-", "").replace("/", "")
    digits = re.sub(r"\D", "", raw)
    if ISO_DATE.match(raw):
        year, month, day = map(int, ISO_DATE.match(raw).groups())
        return {"isodate"}, (year, month, day)
    if SLASH_DATE.match(raw):
        day, month, year = map(int, SLASH_DATE.match(raw).groups())
        return {"isodate"}, (year, month, day)
    if THOUSANDS.match(raw):
        return {"num"}, float(raw.replace(",", ""))
    if NUMBER.match(raw):
        value = float(raw.replace(",", "."))
        if len(digits) >= 7 or (raw.startswith("0") and len(digits) >= 6):
            return {"phone_num", "num"}, raw
        cats = {"num"}
        if raw.isdigit() and 1900 <= int(raw) <= 2100:
            cats.add("year")
        return cats, value
    if raw.startswith("+") and len(digits) >= 7 and compact[1:].isdigit():
        return {"phone_num"}, raw
    if raw.startswith("00") and len(digits) >= 8 and compact.isdigit():
        return {"phone_num"}, raw
    return None


def scan(tokens):
    """:return: the spans of ``tokens``: the longest phrase of the lexicon at each place"""
    spans, i = [], 0
    while i < len(tokens):
        literal = _literal(tokens[i])
        if literal:
            spans.append(Span(i, i + 1, literal[0], tokens[i].raw, literal[1]))
            i += 1
            continue
        hit = None
        for size in range(min(lexicon.max_len(), len(tokens) - i), 0, -1):
            key = tuple(token.stem for token in tokens[i:i + size])
            cats = lexicon.tags_of(key)
            if cats:
                hit = (size, cats)
                break
        if hit:
            size, cats = hit
            span = Span(i, i + size, cats, " ".join(token.raw for token in tokens[i:i + size]))
            number = span.tagged("number")
            if number is not None and span.tagged("weekday") is not None:
                # "الاتنين" is Monday, "اتنين" is two
                if span.raw.startswith("ال"):
                    span.cats = {cat for cat in span.cats if not cat.startswith("number=")}
                    number = None
                else:
                    span.cats = {cat for cat in span.cats if not cat.startswith("weekday=")}
            if number is not None:
                span.cats = {"num"}
                span.value = float(number)
            spans.append(span)
            i += size
        else:
            spans.append(Span(i, i + 1, (), tokens[i].raw))
            i += 1
    return _compound_numbers(spans)


def _compound_numbers(spans):
    """"خمسة وعشرين" is 25: a unit, "and", a ten"""
    merged, i = [], 0
    while i < len(spans):
        if (i + 2 < len(spans) and spans[i].cats == {"num"} and spans[i].value < 10
                and spans[i + 1].raw in ("و", "wa", "w", "and") and spans[i + 2].cats == {"num"}
                and spans[i + 2].value in (20, 30, 40, 50, 60, 70, 80, 90)):
            merged.append(Span(spans[i].i, spans[i + 2].j, {"num"}, spans[i].raw + " " + spans[i + 2].raw,
                               spans[i].value + spans[i + 2].value))
            i += 3
            continue
        if spans[i].raw.startswith("و") and len(spans[i].raw) > 3 and spans[i].cats == set():
            # "وعشرين": the "and" written onto the ten
            rest = normalise.tokenize(spans[i].raw[1:])
            key = tuple(token.stem for token in rest)
            cats = lexicon.tags_of(key)
            if merged and "num" in merged[-1].cats and any(c.startswith("number=") for c in cats):
                value = [int(c.split("=")[1]) for c in cats if c.startswith("number=")][0]
                if value in (20, 30, 40, 50, 60, 70, 80, 90) and merged[-1].value < 10:
                    last = merged.pop()
                    merged.append(Span(last.i, spans[i].j, {"num"}, last.raw + " " + spans[i].raw,
                                       last.value + value))
                    i += 1
                    continue
        merged.append(spans[i])
        i += 1
    return merged


def _first(spans, *cats):
    wanted = set(cats)
    for span in spans:
        if span.cats & wanted:
            return span
    return None


def _all(spans, *cats):
    wanted = set(cats)
    return [span for span in spans if span.cats & wanted]


# ----------------------------------------------------------------------
# Dates
# ----------------------------------------------------------------------

def date_specs(spans):
    """:return: the dates the spans say, in order, each as a tuple:
    ``("rel", key)``, ``("weekday", n)``, ``("iso", y, m, d)``, ``("md", m, d, y|None)``,
    ``("month", m, y|None)``, ``("year", y)``"""
    specs, used = [], set()
    for index, span in enumerate(spans):
        if "isodate" in span.cats:
            specs.append(("iso",) + span.value)
            used.add(index)
            continue
        month = span.tagged("month")
        if month is not None:
            day = year = None
            for near in (index - 1, index + 1):
                if 0 <= near < len(spans) and near not in used and "num" in spans[near].cats:
                    value = spans[near].value
                    if "year" in spans[near].cats and value >= 1900:
                        year = int(value)
                        used.add(near)
                    elif 1 <= value <= 31 and day is None:
                        day = int(value)
                        used.add(near)
            if year is None and index + 2 < len(spans) and "year" in spans[index + 2].cats and day is not None:
                year = int(spans[index + 2].value)
                used.add(index + 2)
            used.add(index)
            specs.append(("md", month, day, year) if day else ("month", month, year))
            continue
        weekday = span.tagged("weekday")
        if weekday is not None:
            specs.append(("weekday", weekday))
            used.add(index)
            continue
        for key in PERIODS:
            if key in span.cats:
                specs.append(("rel", key))
                used.add(index)
                break
    for index, span in enumerate(spans):
        if index not in used and "year" in span.cats:
            specs.append(("year", int(span.value)))
    return specs


def _add_months(day, months):
    index = day.year * 12 + day.month - 1 + months
    return day.replace(year=index // 12, month=index % 12 + 1, day=1)


def period_range(spec, today):
    """:return: ``(first day, first day after)`` of a period spec, or None"""
    kind = spec[0]
    if kind == "rel":
        key = spec[1]
        if key == "today":
            return today, today + datetime.timedelta(days=1)
        if key == "yesterday":
            return today - datetime.timedelta(days=1), today
        if key == "tomorrow":
            return today + datetime.timedelta(days=1), today + datetime.timedelta(days=2)
        if key in ("this_week", "last_week"):
            start = today - datetime.timedelta(days=today.weekday())
            if key == "last_week":
                start -= datetime.timedelta(days=7)
            return start, start + datetime.timedelta(days=7)
        if key in ("this_month", "last_month", "next_month"):
            start = _add_months(today, {"this_month": 0, "last_month": -1, "next_month": 1}[key])
            return start, _add_months(start, 1)
        if key in ("this_year", "last_year"):
            year = today.year - (key == "last_year")
            return datetime.date(year, 1, 1), datetime.date(year + 1, 1, 1)
    if kind == "month":
        year = spec[2] or today.year
        start = datetime.date(year, spec[1], 1)
        return start, _add_months(start, 1)
    if kind == "year":
        return datetime.date(spec[1], 1, 1), datetime.date(spec[1] + 1, 1, 1)
    if kind in ("iso", "md", "weekday"):
        day = date_of(spec, today)
        return (day, day + datetime.timedelta(days=1)) if day else None
    return None


def date_of(spec, today):
    """:return: the day a date spec says, or None; a weekday is its next one"""
    kind = spec[0]
    try:
        if kind == "iso":
            return datetime.date(spec[1], spec[2], spec[3])
        if kind == "md":
            year = spec[3] or today.year
            day = datetime.date(year, spec[1], spec[2])
            if not spec[3] and day < today - datetime.timedelta(days=60):
                day = datetime.date(year + 1, spec[1], spec[2])
            return day
        if kind == "weekday":
            return today + datetime.timedelta(days=(spec[1] - today.weekday()) % 7)
        if kind == "rel":
            offset = {"today": 0, "yesterday": -1, "tomorrow": 1}.get(spec[1])
            if offset is not None:
                return today + datetime.timedelta(days=offset)
    except ValueError:
        return None
    return None


# ----------------------------------------------------------------------
# Sentences
# ----------------------------------------------------------------------

def _words(spans, skip=()):
    """:return: the words of ``spans`` that are no glue and no label, as written"""
    out = []
    for span in spans:
        if span.cats and not (span.cats - set(skip) - GLUE) and span.cats & set(skip):
            continue
        if span.cats & GLUE and not (span.cats - GLUE):
            continue
        out.append(span)
    return out


def _name_of(spans):
    """:return: the words that name something: no verb, no label, no glue, no number"""
    words = []
    for span in spans:
        if span.cats & set(VERBS) or span.cats & set(KINDS) or span.cats & set(FIELDS):
            continue
        if span.cats & set(STATES) or span.cats & set(PERIODS):
            continue
        if span.cats and not (span.cats - GLUE):
            if "num" in span.cats and "year" not in span.cats and "phone_num" not in span.cats:
                words.append(span.raw)
            continue
        if span.tagged("month") is not None or span.tagged("weekday") is not None:
            continue
        if "email_addr" in span.cats or "phone_num" in span.cats or "isodate" in span.cats:
            continue
        words.append(span.raw)
    return " ".join(words).strip()


def _interpret(text, spans, screen):
    """:return: the :class:`Parse` of the ``spans`` of ``text``, as they are"""
    result = Parse(text)
    if not spans:
        return result
    result.free = len([span for span in spans if not span.cats])
    cats = set().union(*(span.cats for span in spans))
    kinds = [span for span in spans if span.cats & set(KINDS)]
    primary = next((span for span in kinds if not span.cats & {"contact"}), kinds[0] if kinds else None)
    result.kind = next(iter(primary.cats & set(KINDS))) if primary else None
    if primary and len(primary.cats & set(KINDS)) > 1:
        # "طلب" is a sales order and, with "شراء", a purchase order: the longest phrase won
        result.kind = sorted(primary.cats & set(KINDS))[0]
    result.states = [state for state in STATES if state in cats]
    # "not paid": the negation turns the state around
    for index, span in enumerate(spans):
        if "neg" in span.cats and index + 1 < len(spans) and "paid" in spans[index + 1].cats:
            result.states = [s for s in result.states if s != "paid"] + ["unpaid"]
    if "unpaid" in result.states and "paid" in result.states:
        result.states.remove("paid")
    dates = date_specs(spans)
    result.period = dates[0] if dates and dates[0][0] in ("rel", "month", "year") else None
    result.latest = "latest" in cats
    verbs = {verb for verb in VERBS if verb in cats}
    navigation = verbs & {"open", "show", "find"}

    def finish(intent, confidence, **slots):
        result.intent, result.confidence, result.slots = intent, round(min(confidence, 0.98), 2), slots
        return result

    head = next((span for span in spans if span.cats & set(VERBS) and not span.cats <= GLUE), None)
    names = _name_of(spans)
    free_count = len([span for span in spans if not span.cats])

    # A verb that is negated ("do not publish", "don't send") is never read as the verb: the rules leave the
    # sentence to a reader who understands the whole of it
    for index, span in enumerate(spans):
        if ("neg" in span.cats or span.raw.lower() in NEGATING) and any(
                nxt.cats & set(VERBS) for nxt in spans[index + 1:index + 3]):
            return result

    # A question about what the person themselves should or may do ("should I confirm this order?", "can I cancel
    # S00049?") is not the command it contains: it goes to the reader who answers
    if SELF_QUESTION.match(text):
        # only when it asks about a change: a question to look at something is a polite request to show it
        changes = bool(verbs & {"create", "update", *PRESS}) or any(span.raw.lower() in CHANGE_WORDS for span in spans)
        looks = bool(verbs & {"open", "show", "find", "list", "count", "sum", "avg"}) or any(
            span.raw.lower() in READ_WORDS for span in spans)
        if changes or not looks:
            return result
        opener = SELF_OPENER.match(text)
        count = len(opener.group().split()) if opener else 0
        if count and len(spans) > count:
            spans = spans[count:]
            head = next((span for span in spans if span.cats & set(VERBS) and not span.cats <= GLUE), None)
            names = _name_of(spans)
            free_count = len([span for span in spans if not span.cats])
    # "create a partner category X": a tag has no kind of its own here, so it is never a contact called "category X"
    if "create" in verbs and any(span.raw.lower() in TAG_WORDS for span in spans[:-1]):
        return result

    # 1. Press a button of the record on screen
    pressing = next((span for span in spans[:4] if span.cats & set(PRESS)), None)
    if pressing and not verbs & {"create", "update", "count", "sum", "avg"}:
        button = next(cat for cat in PRESS if cat in pressing.cats)
        target = _name_of([span for span in spans if span is not pressing])
        on_screen = bool(screen.get("record_id")) and (not target or "this" in cats)
        return finish("press", 0.9 if (on_screen or target) else 0.55, button=button, name=target,
                      current=on_screen, kind=result.kind)

    # 1a. Keep a report: "save sales this month by salesperson as a chart", "save invoices by month and customer as a pivot"
    has_shape = bool(cats & {"chart", "type_pivot"}) or any(span.cats & set(CHART_GROUPS) for span in spans)
    if "save" in cats and not verbs & {"update"} and has_shape and (result.kind or screen.get("model")):
        first_by = next((index for index, span in enumerate(spans) if "by" in span.cats), None)
        groups = []
        for index, span in enumerate(spans):
            for cat in CHART_GROUPS:
                if cat in span.cats and cat not in groups:
                    groups.append(cat)
        # "sales of the month by salesperson": the month before the "by" is the period, not a group
        in_period = None
        if (first_by is not None and "by_month" in groups and len(groups) > 1 and not result.period
                and any(index < first_by and "by_month" in span.cats for index, span in enumerate(spans))
                and not any(index > first_by and "by_month" in span.cats for index, span in enumerate(spans))):
            groups.remove("by_month")
            in_period = ("rel", "this_month")
        if in_period:
            result.period = in_period
        shape = next((value for cat, value in REPORT_TYPES.items() if cat in cats), None)
        measure = "count" if "count" in verbs else "avg" if "avg" in verbs else "sum"
        return finish("save_report", 0.9, measure=measure, kind=result.kind, groups=groups[:2], shape=shape)

    # 1b. Draw a chart: "draw me sales this month by salesperson"
    drawing = "chart" in cats and not verbs & {"update"} and (
        any(span for span in spans if span.cats & set(CHART_GROUPS)) or bool(result.kind or screen.get("model")))
    if drawing:
        by = [span for span in spans if span.cats & set(CHART_GROUPS)]
        group = next((cat for cat in CHART_GROUPS if cat in by[-1].cats), None) if by else None
        chart_type = next((value for cat, value in CHART_TYPES.items() if cat in cats), None)
        measure = "count" if "count" in verbs else "avg" if "avg" in verbs else "sum"
        has_target = bool(result.kind) or bool(screen.get("model"))
        return finish("chart", 0.9 if has_target else 0.45, measure=measure, kind=result.kind, group=group,
                      chart_type=chart_type)

    # 1c. Who changed it, and put it back: "who changed the price of this product", "restore Raghad's old
    # address". Odoo only knows the tracked fields: the tools say which, and why not.
    if "history" in cats or ("revert" in cats and not verbs & {"create", "count", "sum", "avg"}):
        field_span = _first(spans, *FIELDS)
        field = next(cat for cat in FIELDS if cat in field_span.cats) if field_span else None
        # "Raghad's old address": the tokens are Raghad and s
        target = re.sub(r"\s+s$", "", _name_of(spans))
        on_screen = bool(screen.get("record_id"))
        located = bool(target) or on_screen
        slots = {"field": field, "name": target, "kind": result.kind, "current": not target and on_screen}
        if "history" in cats:
            return finish("history", 0.9 if located else 0.55, **slots)
        if field or "old" in cats:
            return finish("revert", 0.9 if located and field else 0.8 if located else 0.55, **slots)

    # 2. Change one field of a record
    field_span = _first(spans, *FIELDS)
    if "update" in verbs and field_span and not verbs & {"count", "sum", "avg"}:
        marker = None
        for index in range(len(spans) - 1, spans.index(field_span), -1):
            if "to" in spans[index].cats and index + 1 < len(spans):
                marker = index
                break
        field = next(cat for cat in FIELDS if cat in field_span.cats)
        if marker is not None:
            value_spans = spans[marker + 1:]
            before = [span for span in spans[:marker] if span is not field_span]
            value = " ".join(span.raw for span in value_spans).strip()
            target = _name_of(before)
            return finish("update", 0.88 if target or screen.get("record_id") else 0.6, field=field,
                          value=value, value_spans=value_spans, name=target, kind=result.kind,
                          current=not target and bool(screen.get("record_id")))
        last = spans[-1]
        if last.cats & {"num", "phone_num", "email_addr", "isodate"} and last is not field_span:
            target = _name_of([span for span in spans[:-1] if span is not field_span])
            return finish("update", 0.8 if target else 0.55, field=field, value=last.raw, value_spans=[last],
                          name=target, kind=result.kind, current=not target and bool(screen.get("record_id")))
        return finish("", 0.3)

    # 3. Aggregates
    if verbs & {"count", "sum", "avg"} and not verbs & {"create", "update"}:
        measure = "avg" if "avg" in verbs else "sum" if "sum" in verbs else "count"
        kind = result.kind
        if measure == "count" and kind == "sales":
            measure = "sum"
        if measure == "sum" and kind in (None, "contact") and not screen.get("model"):
            return finish("", 0.3)
        has_target = bool(kind) or bool(screen.get("model"))
        return finish("aggregate", 0.88 if has_target else 0.45, measure=measure, kind=kind)

    # 4. Create
    wants = "filler" in cats or "new" in cats
    if result.kind in CREATABLE and not navigation - {"open"} and (
            "create" in verbs or ("new" in cats and not verbs - {"open", "update"}) or (
            wants and not verbs and "new" in cats)):
        slots = _create_slots(result.kind, spans, primary)
        enough = slots.pop("_enough")
        return finish("create", 0.9 if enough else 0.5, **slots)
    if result.kind in CREATABLE and "create" in verbs and navigation - {"open"}:
        return finish("", 0.3)

    # 5. Filter the list on screen, or list some records by their state or period
    has_filter = bool(result.states or result.period)
    if has_filter and (verbs & {"list", "show", "open", "find"} or cats & {"only", "rel", "status_word"} or not verbs):
        if not names or "rel" in cats or "only" in cats or "list" in verbs or not verbs:
            has_target = bool(result.kind) or bool(screen.get("model"))
            if has_target and not (verbs & {"find"} and names):
                return finish("filter", 0.88 if verbs or cats & {"only", "rel", "status_word"} else 0.5,
                              kind=result.kind, name=names)
        if verbs & {"show", "open"} and result.kind and not names:
            return finish("filter", 0.85, kind=result.kind, name="")

    # 6. Find a record, or take the person to a screen
    if navigation or "list" in verbs:
        if names and (result.kind or "find" in verbs or "show" in verbs):
            if "open" in verbs and not result.kind and "find" not in verbs and "show" not in verbs:
                return finish("open", 0.85, words=_screen_words(spans), name=names, kind=None)
            return finish("find", 0.85 if (result.kind or names) else 0.5, name=names, kind=result.kind,
                          latest=result.latest)
        if result.latest and result.kind:
            return finish("find", 0.85, name="", kind=result.kind, latest=True)
        words = _screen_words(spans)
        if words:
            return finish("open", 0.85 if "open" in verbs or result.kind else 0.72, words=words,
                          name=names, kind=result.kind)
    return finish("", 0.2) if head else result


# What changes data or the screen: a sentence whose words were guessed from a slip is never acted on as one
WRITES = frozenset(("create", "update", "press", "save_report", "revert"))
# Below the threshold and from here, a parse is still worth offering as "Did you mean"
OFFER_FROM = 0.4
# What a bare name of a record is read as asking, when it carries a slip
BARE_VERB = "show"
MAX_CANDIDATES = 3


def _summary(parsed, fixes=()):
    """:return: ``parsed`` as a plain dict, for the panel to offer it"""
    slots = {key: value for key, value in parsed.slots.items() if key != "value_spans"}
    return {"intent": parsed.intent, "kind": parsed.kind, "confidence": parsed.confidence, "slots": slots,
            "free": parsed.free, "states": list(parsed.states),
            "text": fuzzy.corrected_text(parsed.text, fixes) if fixes else parsed.text,
            "corrections": [fix.record() for fix in fixes]}


def _fixes_of(text, tokens, spans):
    """:return: ``[(index of the token, Fix)]`` for the words nothing in the lexicon explains and
    that may be a slip or Arabic in Latin letters. Names in quotes, the value a field is set to,
    numbers and addresses are left alone."""
    quoted = fuzzy.quoted_words(text)
    fixes = []
    for index, span in enumerate(spans):
        if span.cats or span.j - span.i != 1 or tokens[span.i].norm in quoted:
            continue
        before = spans[:index]
        if before and "to" in before[-1].cats and any(prev.cats & set(FIELDS) for prev in before):
            continue  # "set the phone to ...": a value, as typed
        fix = fuzzy.suggest(tokens[span.i], first=span.i == 0)
        if fix:
            fixes.append((span.i, fix))
    return fixes


def _rescan(tokens, fixes):
    """:return: the spans of ``tokens`` with the words of ``fixes`` read as the word of the lexicon
    (so that a phrase of the lexicon made of it is found), each still as it was written"""
    tokens = list(tokens)
    for index, fix in fixes:
        read = normalise.Token(fix.option.word)
        read.raw = tokens[index].raw
        tokens[index] = read
    return scan(tokens)


def _corrected(text, tokens, screen, fixes):
    """:return: the parse of ``tokens`` with the words of ``fixes`` read as the lexicon word, scored
    lower for it, and kept under the threshold when it would change something (a correction is
    never enough to act) or when a word may have meant two things"""
    parsed = _interpret(text, _rescan(tokens, fixes), screen)
    parsed.corrections = [fix.record() for _, fix in fixes]
    if parsed.intent:
        score = max(0.0, parsed.confidence - fuzzy.penalty([fix for _, fix in fixes]))
        # A slip is not enough to change something, and a word that may be two things is not enough
        # to act on; Arabic read from Latin letters, not guessed from a slip, is
        if any(fix.ambiguous or (parsed.intent in WRITES and fix.option.via == "typo") for _, fix in fixes):
            score = min(score, THRESHOLD - 0.01)
        parsed.confidence = round(score, 2)
    return parsed


def parse(text, screen=None):
    """:return: the :class:`Parse` of ``text`` (see :func:`_parse`); a bare name of a record with a slip in it
    ("sales ordrs", "qoutations") says no verb, so it is offered as "open" what it names, never acted on"""
    parsed = _parse(text, screen)
    words = (text or "").strip()
    if not parsed.intent and not parsed.candidates and words and not words.lower().startswith(BARE_VERB):
        shown = _parse(BARE_VERB + " " + words, screen)
        if shown.intent == "open" and shown.corrections:
            shown.confidence = min(shown.confidence, THRESHOLD - 0.01)
            summary = _summary(shown)
            for fix in shown.corrections:
                summary["text"] = summary["text"].replace(fix["word"], fix["to"])
                # The chip shows the corrected words, not the slip
                summary["slots"]["words"] = str(summary["slots"].get("words") or "").replace(fix["word"], fix["to"])
            summary["corrections"] = list(shown.corrections)
            shown.candidates = [summary] if shown.confidence >= OFFER_FROM else []
            return shown
    return parsed


def _parse(text, screen=None):
    """:param screen: what is known of the screen the person is on:
        ``{"model": str, "record_id": int|None}``
    :return: the :class:`Parse` of ``text``; ``intent`` is empty when nothing is understood.

    A word the lexicon does not know is read as the word it was most likely meant to be (a slip, or
    Arabic in Latin letters). That costs confidence, and a sentence that changes something is
    never sure enough to be acted on after a correction. A parse under the threshold that is worth
    asking about has the sentences it may have meant in ``candidates``, each as a dict
    ``{"intent", "kind", "confidence", "slots", "text", "corrections"}``, the likeliest first.
    """
    text = (text or "").strip()[:400]
    screen = screen or {}
    tokens = normalise.tokenize(text)
    spans = scan(tokens)
    strict = _interpret(text, spans, screen)
    fixes = _fixes_of(text, tokens, spans) if spans else []
    if fixes:
        loose = _corrected(text, tokens, screen, fixes)
        if loose.intent:
            if (strict.intent and strict.confidence >= THRESHOLD and strict.intent != loose.intent
                    and strict.intent not in ("open", "find")):
                # Understood as it was typed, and not by taking the unknown words for names: the
                # corrected reading is only a suggestion
                strict.candidates = [_summary(loose, [fix for _, fix in fixes])]
                return strict
            same = (loose.intent, loose.kind, loose.states, loose.period) == (
                strict.intent, strict.kind, strict.states, strict.period)
            if not (same and strict.confidence >= THRESHOLD):
                loose.candidates = _alternatives(text, tokens, screen, fixes, loose)
                return loose
    if strict.intent and OFFER_FROM <= strict.confidence < THRESHOLD:
        strict.candidates = [_summary(strict)]
    return strict


def _alternatives(text, tokens, screen, fixes, loose):
    """:return: the readings of the sentence worth offering when the corrected one is not sure
    enough: the likeliest, and what the words that may have meant two things would give"""
    if loose.confidence >= THRESHOLD:
        return []
    found = {(loose.intent, loose.kind): (loose, [fix for _, fix in fixes])}
    for index, (position, fix) in enumerate(fixes):
        for choice in range(1, len(fix.options)):
            other = fuzzy.Fix(fix.raw, fix.options)
            other.chosen = choice
            swapped = list(fixes)
            swapped[index] = (position, other)
            parsed = _corrected(text, tokens, screen, swapped)
            key = (parsed.intent, parsed.kind)
            if parsed.intent and (key not in found or found[key][0].confidence < parsed.confidence):
                found[key] = (parsed, [one for _, one in swapped])
    ranked = sorted(found.values(), key=lambda pair: -pair[0].confidence)
    return [_summary(parsed, used) for parsed, used in ranked[:MAX_CANDIDATES] if parsed.confidence >= OFFER_FROM]


def _screen_words(spans):
    """:return: the words that name a screen: the sentence without its verb and its glue"""
    words = []
    for span in spans:
        if span.cats & set(VERBS) or span.cats & {"filler", "question", "to", "for", "and", "this", "rel", "only"}:
            continue
        if "num" in span.cats and "year" not in span.cats:
            continue
        words.append(span.raw)
    return " ".join(words).strip()


def _create_slots(kind, spans, primary):
    """:return: what to create ``kind`` with, from the sentence"""
    position = spans.index(primary) if primary in spans else 0
    after = spans[position + 1:]
    before = [span for span in spans[:position] if not span.cats & set(VERBS)]
    slots = {"kind": kind}
    if kind in ("contact", "product"):
        name_parts, label, price = [], None, None
        for span in after + [span for span in before if not span.cats]:
            if span.cats & set(FIELDS):
                label = next(cat for cat in FIELDS if cat in span.cats)
                continue
            if "email_addr" in span.cats:
                slots["email"] = span.raw
                continue
            if "phone_num" in span.cats:
                slots["phone"] = span.raw
                continue
            if "num" in span.cats:
                if kind == "product":
                    price = span.value
                continue
            if span.cats & (GLUE | set(VERBS)):
                continue
            if label is None or label == "name":
                name_parts.append(span.raw)
        slots["name"] = " ".join(name_parts).strip()
        if price is not None:
            slots["price"] = price
        slots["_enough"] = bool(slots["name"])
        return slots
    if kind in ("task", "lead", "ticket"):
        words = []
        for span in after:
            if not words and span.cats & {"for", "to", "filler", "new", "and"} and not span.cats - GLUE:
                continue
            words.append(span.raw)
        slots["title"] = " ".join(words).strip()
        slots["_enough"] = bool(slots["title"])
        return slots
    if kind == "timeoff":
        specs = date_specs(spans)
        slots["dates"] = specs
        slots["words"] = _name_of(spans)
        slots["_enough"] = bool(specs)
        return slots
    # Orders, invoices: a partner and the lines
    partner, lines, current, mode = [], [], None, "partner"
    for span in after:
        if mode == "partner":
            if "num" in span.cats and "year" not in span.cats and "phone_num" not in span.cats:
                mode, current = "lines", {"qty": span.value, "words": []}
                continue
            if span.cats & {"and"} and partner:
                mode, current = "lines", {"qty": None, "words": []}
                continue
            if span.cats and not (span.cats - GLUE - set(KINDS)):
                continue
            if span.cats & set(VERBS):
                continue
            partner.append(span.raw)
            continue
        if "num" in span.cats and "year" not in span.cats and "phone_num" not in span.cats:
            if current and current["words"] and current["qty"] is None:
                current["qty"] = span.value
            else:
                if current and current["words"]:
                    lines.append(current)
                current = {"qty": span.value, "words": []}
            continue
        if span.cats & {"and"}:
            if current and current["words"]:
                lines.append(current)
            current = {"qty": None, "words": []}
            continue
        if span.cats & {"times"}:
            continue
        if span.cats and not (span.cats - GLUE):
            continue
        if current is None:
            current = {"qty": None, "words": []}
        current["words"].append(span.raw)
    if current and current["words"]:
        lines.append(current)
    slots["partner"] = " ".join(partner).strip()
    slots["lines"] = [{"qty": line["qty"] or 1.0, "product": " ".join(line["words"])} for line in lines]
    slots["_enough"] = bool(slots["partner"]) and bool(slots["lines"] or kind in ("invoice", "bill", "quotation", "sales_order", "purchase_order"))
    return slots

"""Explaining a saved report: short sentences from its numbers, no AI.

The facts are computed by reading the data of the report as the person who asks (their
rights and record rules, each time): the total, the biggest contributors, the movers against the
previous period, the rows that changed sign, the columns that look empty. A model may later write
one paragraph around these sentences; it never sees a row, and a check flags a number it wrote
that is not in the sentences.
"""
import datetime
import re

from dateutil.relativedelta import relativedelta

from odoo import fields

from . import saved_reports
from .analytics import TooBig, grouped
from .records import Refused, build_domain, get_model



def _tr(env, source, *args, **kwargs):
    """Translate in the language of ``env``: ``env._`` on 18 and 19; on 17 ``_()`` reads the
    ``context`` and ``cr`` of the frame that calls it, which a function of this module lacks.
    The code calls ``_tr(env, `` so that its terms are extracted; scripts/port17.py turns those calls into this."""
    method = getattr(env, "_", None)
    if method:
        return method(source, *args, **kwargs)
    from odoo import _
    context = env.context  # noqa: F841
    cr = env.cr  # noqa: F841
    return _(source, *args, **kwargs)


MAX_ROWS = 60
# A few rows that carry most of the total are worth a sentence
CONCENTRATION = 0.6
MAX_NAMED = 3
# A share of the total with no value in the group field is worth a sentence
UNDEFINED_SHARE = 0.2


# ----------------------------------------------------------------------
# Numbers as they are written
# ----------------------------------------------------------------------
def _spec_field(model, spec):
    return model._fields.get(spec.partition(":")[0]) if spec != "count" else None


def _money(env, model, spec, value):
    """:return: a value of a measure as the person reads it: money with its currency, else a plain number"""
    field = _spec_field(model, spec)
    if field is not None and field.type == "monetary":
        return env.company.currency_id.format(value)
    value = float(value or 0)
    return f"{value:,.0f}" if value == int(value) else f"{value:,.2f}"


def _measure_name(env, model, spec):
    """:return: what is measured as a sentence names it: "Total", "the average of Price", "the number of records" """
    if spec != "count" and spec.partition(":")[2] == "sum":
        return model._fields[spec.partition(":")[0]]._description_string(env)
    return saved_reports.measure_words(env, model, spec)


def _percent(ratio):
    return f"{abs(ratio) * 100:.0f}%"


ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,")
NUMBER = re.compile(r"\d[\d,.]*")


def figures_in(text):
    """:return: the numbers written in ``text`` (Arabic or Latin digits, thousands separators), as floats"""
    found = set()
    for token in NUMBER.findall((text or "").translate(ARABIC_DIGITS)):
        token = token.rstrip(".,")
        # "1,234.50": the commas are thousands; "1,5" is read as a decimal comma only when it is not a group of three
        if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", token):
            token = token.replace(",", "")
        else:
            token = token.replace(",", ".")
        try:
            found.add(round(float(token), 2))
        except ValueError:
            continue
    return found


def unverified_numbers(text, facts):
    """:return: the numbers of ``text`` that no fact holds, as written in the text, sorted

    :param facts: the sentences the text must keep to"""
    allowed = set()
    for sentence in facts:
        allowed |= figures_in(sentence)
    out = []
    for token in NUMBER.findall((text or "").translate(ARABIC_DIGITS)):
        if figures_in(token) - allowed:
            out.append(token.rstrip(".,"))
    return sorted(set(out))


# ----------------------------------------------------------------------
# Reading
# ----------------------------------------------------------------------
def previous_period(preset, today):
    """:return: ``(start, end)`` of the period before ``preset`` on ``today``, end excluded"""
    month = today.replace(day=1)
    year = today.replace(month=1, day=1)
    if preset == "this_month":
        return month - relativedelta(months=1), month
    if preset == "last_month":
        return month - relativedelta(months=2), month - relativedelta(months=1)
    if preset == "this_year":
        return year - relativedelta(years=1), year
    if preset == "last_year":
        return year - relativedelta(years=2), year - relativedelta(years=1)
    days = 7 if preset == "last_7_days" else 30
    start = today - datetime.timedelta(days=days - 1)
    return start - datetime.timedelta(days=days), start


def _previous_domain(env, model, definition, today):
    if not definition.get("period"):
        return None
    start, end = previous_period(definition["period"], today)
    field = definition["period_field"]
    static = build_domain(model, definition["conditions"], definition["match"])
    domain = static.to_list() if hasattr(static, "to_list") else list(static)
    return domain + [(field, ">=", start.isoformat()), (field, "<", end.isoformat())]


def _aggregate(spec):
    return "__count" if spec == "count" else spec


def _rows(env, model, domain, definition):
    """:return: ``[(label, value)]`` of the first measure per first group, the newest periods oldest first for a
    date; ``(label, value)`` keeps ``None`` for a group with no value"""
    group = definition["group_by"][0]
    aggregate = _aggregate(definition["measures"][0])
    dated = model._fields[group.partition(":")[0]].type in ("date", "datetime")
    rows = grouped(env, model, domain, [group], [aggregate], MAX_ROWS,
                   f"{group} desc" if dated else f"{aggregate} DESC")
    if dated:
        rows = list(reversed(rows))
    return [(saved_reports._label(env, key, group), float(value or 0), key) for key, value in rows], dated


def _total(env, model, domain, spec):
    [(total, count)] = grouped(env, model, domain, [], [_aggregate(spec), "__count"])
    return float(total or 0), count


# ----------------------------------------------------------------------
# The facts
# ----------------------------------------------------------------------
def facts(env, definition, domain_source):
    """The sentences that explain a report, read as the person in ``env``.

    :param definition: the definition of a saved report
    :param domain_source: the text of the domain of its action
    :return: ``[{"key": kind of fact, "text": the sentence}]``, the first being the total
    :raise Refused: when the person may not read the data of the report"""
    model = get_model(env, definition["model"])
    domain = saved_reports.evaluate(env, domain_source)
    today = fields.Date.context_today(env["res.users"])
    spec = definition["measures"][0]
    measure = _measure_name(env, model, spec)
    try:
        total, count = _total(env, model, domain, spec)
    except TooBig as error:
        raise Refused(str(error)) from error
    if not count:
        return [{"key": "empty", "text": _tr(env, "There is no data in this report: no record matches it.")}]
    records = f"{count:,}"
    out = [{"key": "total", "text": _tr(env, 
        "Overall, %(measure)s is %(total)s over %(count)s records.",
        measure=measure, total=_money(env, model, spec, total), count=records)}]
    if definition["group_by"]:
        try:
            out += _group_facts(env, model, definition, domain, total, today)
        except TooBig as error:
            out.append({"key": "toobig", "text": str(error)})
    out += _column_facts(env, model, definition, domain)
    return out


def _group_facts(env, model, definition, domain, total, today):
    spec = definition["measures"][0]
    group = definition["group_by"][0]
    rows, dated = _rows(env, model, domain, definition)
    field_label = saved_reports.group_words(env, model, group)
    out = []
    if dated:
        out += _step_facts(env, model, spec, rows)
    else:
        out += _contributor_facts(env, model, spec, rows, total)
        previous = _previous_domain(env, model, definition, today)
        if previous is not None:
            out += _mover_facts(env, model, definition, rows, previous, total)
    out += _undefined_facts(env, rows, total, field_label)
    if not any(fact["key"] in ("signs", "movers", "steps") for fact in out):
        negatives = [label for label, value, _key in rows if value < 0]
        if negatives:
            out.append({"key": "negative", "text": _tr(env, 
                "%(count)s rows are below zero, for instance %(names)s.",
                count=len(negatives), names=", ".join(negatives[:MAX_NAMED]))})
    return out


def _contributor_facts(env, model, spec, rows, total):
    if total <= 0 or len(rows) < 2:
        return []
    ranked = sorted((row for row in rows if row[1] > 0), key=lambda row: -row[1])
    if not ranked:
        return []
    # Shares are of what is above zero: a row below it would make a share of more than all
    total = sum(row[1] for row in ranked)
    running, named = 0.0, []
    for label, value, _key in ranked:
        running += value
        named.append(label)
        if running / total >= CONCENTRATION or len(named) == MAX_NAMED:
            break
    share = running / total
    if len(named) == 1:
        if share >= CONCENTRATION:
            return [{"key": "contributors", "text": _tr(env, 
                "%(name)s alone is %(share)s of the total.", name=named[0], share=_percent(share))}]
    elif share >= CONCENTRATION and len(named) < len(ranked):
        return [{"key": "contributors", "text": _tr(env, 
            "%(count)s rows (%(names)s) make %(share)s of the total.",
            count=len(named), names=", ".join(named), share=_percent(share))}]
    label, value, _key = ranked[0]
    return [{"key": "contributors", "text": _tr(env, 
        "The biggest is %(name)s with %(value)s, %(share)s of the total.",
        name=label, value=_money(env, model, spec, value), share=_percent(value / total))}]


def _change_words(env, model, spec, now, before):
    delta = now - before
    if not before:
        return _money(env, model, spec, delta)
    return f"{_money(env, model, spec, abs(delta))} ({_percent(delta / before)})"


def _step_facts(env, model, spec, rows):
    """A report by date: the last period against the one before, the biggest step, the periods that changed sign"""
    if len(rows) < 2:
        return []
    out = []
    (last, now, _k1), (before_label, before, _k2) = rows[-1], rows[-2]
    if now != before:
        sentence = _tr(env, 
            "%(last)s is %(now)s, up %(change)s on %(before)s (%(was)s).", last=last,
            now=_money(env, model, spec, now), change=_change_words(env, model, spec, now, before),
            before=before_label, was=_money(env, model, spec, before)) if now > before else _tr(env, 
            "%(last)s is %(now)s, down %(change)s on %(before)s (%(was)s).", last=last,
            now=_money(env, model, spec, now), change=_change_words(env, model, spec, now, before),
            before=before_label, was=_money(env, model, spec, before))
    else:
        sentence = _tr(env, "%(last)s is %(now)s, the same as %(before)s.", last=last,
                         now=_money(env, model, spec, now), before=before_label)
    out.append({"key": "movers", "text": sentence})
    steps = [(abs(rows[index][1] - rows[index - 1][1]), index) for index in range(1, len(rows))]
    size, index = max(steps)
    if size and index != len(rows) - 1:
        out.append({"key": "steps", "text": _tr(env, 
            "The biggest step is from %(before)s to %(label)s: %(change)s.", before=rows[index - 1][0],
            label=rows[index][0], change=_money(env, model, spec, rows[index][1] - rows[index - 1][1]))})
    flips = [index for index in range(1, len(rows)) if rows[index][1] * rows[index - 1][1] < 0]
    if flips:
        names = ", ".join(rows[index][0] for index in flips[:MAX_NAMED])
        out.append({"key": "signs", "text": _tr(env, 
            "The sign changed at %(names)s.", names=names)})
    return out


def _mover_facts(env, model, definition, rows, previous_domain, total):
    """A report cut on a period: each row against the same period before"""
    spec = definition["measures"][0]
    now = {key if key is not None else False: (label, value) for label, value, key in rows}
    try:
        before_rows, _dated = _rows(env, model, previous_domain, definition)
        before_total, _count = _total(env, model, previous_domain, spec)
    except TooBig:
        return []
    before = {key if key is not None else False: (label, value) for label, value, key in before_rows}
    if not before_total and not before:
        return [{"key": "movers", "text": _tr(env, "There is nothing in the period before to compare with.")}]
    out = []
    sentence = _tr(env, "Against the period before (%(was)s) the total is up %(change)s.") if total >= before_total         else _tr(env, "Against the period before (%(was)s) the total is down %(change)s.")
    out.append({"key": "movers", "text": sentence % {
        "was": _money(env, model, spec, before_total), "change": _change_words(env, model, spec, total, before_total)}})
    deltas = []
    for key in set(now) | set(before):
        label = (now.get(key) or before.get(key))[0]
        deltas.append((abs(now.get(key, (0, 0.0))[1] - before.get(key, (0, 0.0))[1]), label,
                       now.get(key, (0, 0.0))[1], before.get(key, (0, 0.0))[1]))
    movers = [delta for delta in sorted(deltas, key=lambda item: (-item[0], str(item[1]))) if delta[0]][:MAX_NAMED]
    if movers:
        parts = []
        for size, label, value, was in movers:
            sign = "+" if value >= was else "-"
            parts.append(f"{label} {sign}{_money(env, model, spec, size)}")
        out.append({"key": "movers", "text": _tr(env, "Biggest movers: %(movers)s.", movers="; ".join(parts))})
    flips = sorted(str(label) for key, (label, value) in now.items()
                   if key in before and value * before[key][1] < 0)
    if flips:
        out.append({"key": "signs", "text": _tr(env, 
            "These changed sign against the period before: %(names)s.", names=", ".join(flips[:MAX_NAMED]))})
    return out


def _undefined_facts(env, rows, total, field_label):
    if total <= 0:
        return []
    undefined = sum(value for _label, value, key in rows if key in (None, False))
    if undefined / total >= UNDEFINED_SHARE:
        return [{"key": "suspicious", "text": _tr(env, 
            "%(share)s of the total has no %(field)s set.", share=_percent(undefined / total), field=field_label)}]
    return []


def _column_facts(env, model, definition, domain):
    """Measures after the first that are zero in every row"""
    out = []
    for spec in definition["measures"][1:]:
        try:
            total, _count = _total(env, model, domain, spec)
        except TooBig:
            continue
        if not total:
            out.append({"key": "suspicious", "text": _tr(env, 
                "%(measure)s is empty: zero in every row.", measure=_measure_name(env, model, spec))})
    return out

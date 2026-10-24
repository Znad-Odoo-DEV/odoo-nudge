"""S6: which model answers, the model of the provider or its economy tier, decided with no AI.

A provider may have an economy tier: a cheaper model of the same provider (Claude Haiku beside
Sonnet, a mini model beside the full one, Flash beside Pro), or another provider set up in
Settings, in the cloud or on a server of one's own. It answers the light work: a question in Talk,
which changes nothing, a summary, an explanation, a short text. The model of the provider answers
what acts or builds (Agent and Developer modes), what is heavy (a skill marked heavy, a long
question, a file, words that ask for an analysis), and, once, what the economy tier failed at.
With no economy tier everything goes to the model of the provider, as before.

No Odoo imports, as the rest of this package: plain values in, a decision out."""
import re

from . import base

MAIN = "main"
ECONOMY = "economy"

# A question this long is a piece of work more than a question
HEAVY_CHARS = 600
# Words that ask for reasoning rather than for an answer, in English and French (whole words)
HEAVY_WORDS = re.compile(
    r"\b(analy[sz]e[sd]?|analy[sz]ing|analysis|compare[sd]?|comparing|comparison|in depth|in detail|"
    r"detailed|step by step|think (?:hard|carefully|it through)|strateg(?:y|ies|ic)|forecasts?|audit|"
    r"evaluate|assess(?:ment)?|investigate|diagnose|optimi[sz]e|pros and cons|trade-?offs?|"
    r"analyser|comparer|comparaison|en d[ée]tail|d[ée]taill[ée]e?s?|[ée]tape par [ée]tape|"
    r"strat[ée]gie|pr[ée]vision|[ée]valuer)\b",
    re.IGNORECASE)
# The same in Arabic: the letters joined to a word (al-, wa-, bi-) leave no word boundary
HEAVY_ARABIC = ("حلل", "حلّل", "تحليل", "قارن", "مقارنة", "بالتفصيل", "مفصل", "خطوة بخطوة",
                "استراتيجية", "إستراتيجية", "توقعات", "تدقيق", "تقييم", "قيّم")

# Failures of the account, not of the model: the model above, of the same provider and the same
# key, fails the same way; the provider chosen to stand in (NP-10) is asked instead
ACCOUNT_ERRORS = frozenset({
    base.ERROR_AUTH, base.ERROR_WORKSPACE, base.ERROR_RATE_LIMIT, base.ERROR_NETWORK,
    base.ERROR_UNAVAILABLE, base.ERROR_MISSING_LIBRARY,
})


def heavy_reason(text, attachment=False):
    """:param text: the question as the person wrote it
    :param attachment: whether a file or a picture came with it
    :return: why the question needs the model of the provider ("file", "long", "words"), or ""
        when the economy tier may answer it"""
    text = text or ""
    if attachment:
        return "file"
    if len(text) > HEAVY_CHARS:
        return "long"
    if HEAVY_WORDS.search(text) or any(word in text for word in HEAVY_ARABIC):
        return "words"
    return ""


def escalation(outcome, same_provider):
    """:param dict outcome: what one call of the economy tier gave (``llm/loop.py``)
    :param same_provider: whether the economy tier is a model of the provider of the model above
    :return: why the model above answers instead ("error", "empty", "refusal"), or "" when the
        answer stands, or when another way serves better (the person stopped it; it was already
        handed over; an error of the account, which the model above would meet too)"""
    if outcome.get("aborted") or outcome.get("fell_back"):
        return ""
    if outcome.get("text") or outcome.get("tool_calls"):
        return ""
    error = outcome.get("error")
    if error:
        return "" if same_provider and error.get("code") in ACCOUNT_ERRORS else "error"
    reason = (outcome.get("stop") or {}).get("reason")
    if reason == base.STOP_REFUSAL:
        return "refusal"
    if reason in (base.STOP_END, base.STOP_TOOL_USE):
        return "empty"
    return ""


def tool_failed(results):
    """:param results: the results of the tools of one call, as stored
    :return: whether one of them is an error the model above may read better"""
    return any(result.get("is_error") for result in results or ())

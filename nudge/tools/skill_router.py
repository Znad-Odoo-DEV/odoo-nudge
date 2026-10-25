"""S1: choose the skills a question needs even when it holds none of their trigger words.

Layers, the cheapest first, none of them needs a model:

1. the trigger words and the screen models of a skill (``skills.pick``, as before);
2. a lexical score: the words of the question, folded as the no-AI rules fold them (Arabic
   particles and long vowels, English plurals), freed of a slip of the keys or of Arabic written
   in Latin letters (``arabizi``), are scored against the text of each skill (BM25: its words
   that bring it, its summary and its instructions), through a table of words that mean the
   same in English and in Arabic;
3. the screen: the model on it and its menu or app title, a small push for the skills of that
   kind of screen;
4. only when the local search model is already switched on: its reading of the question against
   the skills, as one more signal.

Nothing found: no skill, and the model asks for one with ``use_skill``; the question is noted for
the coverage page. The index of a set of skills is built once per registry and set of skills
(an id and its last change), so that a question costs a few milliseconds.
"""
import collections
import logging
import math
import re
import threading
import time

from ..llm import arabic
from . import commands
from . import skills as skill_rules
from .noai_commands import fuzzy
from .skill_words import CUES, SYNONYMS

_logger = logging.getLogger(__name__)

# Thresholds were set on CASES of tests/skill_router_cases.py, never on FRESH2
THRESHOLD = 3.0
# The second skill must be this share of the first (and above the threshold) to join it
SECOND_SHARE = 0.55
# What a word read through a typo, an Arabizi spelling or a synonym is worth against a word as typed
W_LITERAL, W_CONCEPT, W_FIXED, W_SCREEN = 1.0, 0.55, 0.8, 0.35
# Weights of where a word of a skill comes from
F_TRIGGER, F_NAME, F_SUMMARY, F_TEXT = 3.0, 2.0, 1.5, 0.35
# A word inside a phrase of the trigger words says less than the phrase; the phrase itself (two words
# together) says more
F_IN_PHRASE, F_PAIR, F_TOOL, F_CUE = 1.2, 3.5, 1.0, 1.6
MINOR = frozenset(("on", "up", "off", "out", "down"))
# The things every business question is about say what it is about, not what is wanted: they count for
# little in a question (the work is told by the verbs and the rest)
GENERIC_WORDS = ("customer client invoice order payment product record contact employee company sale purchase "
                 "vendor supplier account document item amount price number user person team "
                 "عميل فاتوره طلب دفع منتج موظف شركه سعر")
W_GENERIC = 0.35
F_TEXT_MAX = 1.5
K1 = 1.2
LAYERS = ("rules", "lexical", "screen", "embedding")
# What a trigger word of a skill is worth: enough alone, as it always was
RULES_BONUS = 1.1 * THRESHOLD
# What the screen is worth to a skill of its kind, as a share of the threshold
SCREEN_PUSH = 0.6
# What the search model adds: its score (0..1) times this, in the units of the lexical score
EMBED_WEIGHT = 6.0
EMBED_MIN = 0.35
# A choice this sure of itself never asks the search model
CONFIDENT = 9.0
MAX_RANKED = 24
# Skills the index of a call lists: those the question scored best for, and those already loaded
INDEX_MAX = 8
MAX_QUERY = 500
MAX_CACHED = 12
MAX_MISSES = 100

_STOP = frozenset("""a an the and or of to in on at for from by with without is are was were be been am do does did
done have has had having i me my we our us you your he she it its they them their this that these those there here
what which who whom whose how when where why will would shall should can could may might must please just so
if then than too very also as not no yes about into over under again any some all more most other such only own
same s t get got let like want need would tell show give make let ok okay hi hello thanks thank
هل ما ماذا لماذا ليش شو ايش وش متى اين وين من الى على عن في مع هذا هذه هذي هاي هادا ذلك التي الذي اللي انا نحن
انت انتي هو هي هم لي لنا لك له لها بدي ابغى ابي اريد ممكن لو اذا يا كان كانت يكون حتى لكن او ثم قد لا شي شيء
عند عندنا عندي لما فيه فيها هاد هاذا بس كمان مرة انو انه انها اللي""".split())

# The same small words as people type them in Latin letters (Arabizi): the, to, of, from, on, all, what, how,
# I want, we have, only, but; they say nothing of the work
_STOP_ARABIZI = """el al la lal lel ll w wa ma mn men min 3ala 3al 3la hal hayda hay hayy hadi hadol shu shou shoo
shi eh eish wesh kif kifak kayf keef lesh leish lamma lama eza iza ya kol kul bas kaman marra bdi baddi bade
bdna baddna abghi abi ureed 3ndi 3ndna 3andi 3ndak 3ndkon 3ndha 3ndo 3and ana ent inta inti huwa hiye hom
nahnu fi fih feh feha fe 3am ba3d abel abl ma3 eli elli illi lli lazem mumken momken bs
taba3 tab3 bta3 bta3at ely elly mish mesh msh di dah da dol ayz 3ayz 3ayzeen ayza ezay emta wein wen bel bil wel""".split()

STOP = frozenset(commands.fold(word) for word in _STOP) | _STOP | frozenset(_STOP_ARABIZI)
# What the Latin spelling of an Arabic word has written onto it: the article and the particles, the endings
LATIN_HEADS = ("bel", "bil", "wel", "lel", "lal", "el", "al", "bi", "ll", "la", "w", "b", "l", "y", "t", "n")
LATIN_TAILS = ("een", "oon", "at", "et", "it", "in", "un", "ha", "hom", "kon", "na", "ni", "ak", "ek", "o")

# Models of screens that are of a kind of work: the push they give to the skills named (only the
# skills that are there, and only with some word of the question as well)
MODEL_HINTS = {
    "base.automation": ("automations-build", "failure-diagnosis"),
    "ir.actions.server": ("automations-build", "failure-diagnosis"),
    "ir.cron": ("automations-build", "failure-diagnosis", "recurring-tasks"),
    "ir.ui.view": ("screens-fields-build",),
    "ir.model.fields": ("screens-fields-build",),
    "ir.actions.report": ("screens-fields-build",),
    "res.config.settings": ("odoo-setup",),
    "ir.module.module": ("odoo-setup", "module-scaffold"),
    "mail.template": ("designed-emails", "email-design"),
    "res.company": ("database-setup", "odoo-setup"),
    "product.template": ("product-search",),
    "product.product": ("product-search",),
    "website.page": ("website-builder",),
    "website": ("website-builder",),
    "studio.approval.rule": ("studio-approvals",),
    "calendar.event": ("meetings",),
    "base_import.import": ("import-files",),
}
SCREEN_KEYS = ("title", "menu", "app", "action", "breadcrumb", "breadcrumbs")

LATIN = re.compile(r"[a-z]")
ARABIC = re.compile("[؀-ۿ]")
WORD = re.compile(r"\w+")
AR_PREFIXES = "وبلكف"
AR_TAILS = "نته"
# The consonants of a word say what it is from this many; a root of two (sar, khata) only for a spelling that is
# close to it, the rule of the commands (NP-05) with the vowels of a dialect word counted for little
MIN_SKELETON = fuzzy.MIN_SKELETON
SHORT_SKELETON = fuzzy.SHORT_SKELETON
SHORT_COST = 0.8

_lock = threading.Lock()
_static = {}


# ----------------------------------------------------------------------
# Words
# ----------------------------------------------------------------------
def _english(word):
    """:return: an English word reduced to its stem, lightly: plurals and the usual endings"""
    if len(word) <= 3:
        return word
    if word.endswith("ies") and len(word) > 4:
        word = word[:-3] + "y"
    elif word.endswith(("sses", "xes", "ches", "shes")):
        word = word[:-2]
    elif word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    for ending in ("ing", "ed", "ly", "er"):
        if word.endswith(ending) and len(word) - len(ending) >= 4:
            word = word[:-len(ending)]
            if len(word) > 3 and word[-1] == word[-2] and word[-1] not in "sl":
                word = word[:-1]
            break
    if word.endswith("e") and len(word) > 4:
        word = word[:-1]
    if word.endswith("ation") and len(word) > 7:
        word = word[:-5]
    elif word.endswith("ion") and len(word) > 6:
        word = word[:-3]
    return word


AR_PARTICLES = ("وال", "بال", "كال", "فال",
                "هال", "لل", "ال")
# What is written onto the end of an Arabic word: us, her, their, you; plural and dual; the ta marbuta
AR_ENDINGS = ("ها", "هم", "هن", "كم", "نا", "ات", "ون",
              "ين", "ان", "ني", "ه")
AR_VOWELS = str.maketrans("", "", "اوي")


def _arabic(word):
    """:return: an Arabic word as the rules read it: no particle, no ending written onto it, no long vowel"""
    for particle in AR_PARTICLES:
        if word.startswith(particle) and len(word) - len(particle) >= 3:
            word = word[len(particle):]
            break
    for ending in AR_ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            word = word[:-len(ending)]
            break
    stripped = word.translate(AR_VOWELS)
    return stripped if len(stripped) >= 2 else word


def stem(word):
    return _arabic(word) if ARABIC.search(word) else _english(word)


def words_of(text):
    """:return: the folded words of ``text``, the small words of no meaning left out, as written
    (not stemmed): the form the typo and Arabizi readings start from"""
    folded = commands.fold((text or "")[:MAX_QUERY])
    return [word for word in WORD.findall(folded) if word not in STOP and not word.isdigit() and len(word) > 1]


def tokens_of(text):
    """:return: the stems of the words of ``text``, in order"""
    return [stem(word) for word in words_of(text)]


def pairs_of(tokens):
    """:return: the pairs of words that follow each other, as terms"""
    return [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]


def _group_words(line):
    return [stem(word) for word in WORD.findall(commands.fold(line)) if word not in STOP]


def _static_tables():
    """:return: ``(concept of a stem -> group numbers, the words of every group)`` built once"""
    with _lock:
        if "concepts" not in _static:
            concepts = collections.defaultdict(set)
            for number, line in enumerate(SYNONYMS):
                for word in _group_words(line):
                    concepts[word].add(number)
            _static["concepts"] = dict(concepts)
            _static["words"] = {word for line in SYNONYMS for word in _group_words(line)}
        return _static["concepts"], _static["words"]


# ----------------------------------------------------------------------
# The index of a set of skills
# ----------------------------------------------------------------------
class Index:
    """The words of a set of skills, scored the way a search engine does (BM25), built once"""

    def __init__(self, rows):
        """:param rows: ``{"key", "name", "summary", "instructions", "trigger_words", "model_names"}`` per skill"""
        concepts, group_words = _static_tables()
        self.concepts = concepts
        self.generic = {stem(word) for word in GENERIC_WORDS.split()}
        self.keys = [row["key"] for row in rows]
        self.models = [set(re.split(r"[,\s]+", row.get("model_names") or "")) - {""} for row in rows]
        self.engine_text = [
            " ".join(filter(None, (row.get("name"), row.get("summary"), row.get("trigger_words")))) for row in rows]
        self.forms = [forms_of(row.get("trigger_words")) for row in rows]
        weights = []
        for row in rows:
            weight = collections.defaultdict(float)

            def put(term, value, weight=weight):
                weight[term] = max(weight[term], value) if term.startswith("~") else weight[term] + value

            for phrase in skill_rules.split_words(row.get("trigger_words")):
                found = tokens_of(phrase)
                if len(found) == 1:
                    if found[0] not in MINOR:
                        put(found[0], F_TRIGGER)
                else:
                    for token in found:
                        if token not in MINOR:
                            put(token, F_IN_PHRASE)
                    for pair in pairs_of(found):
                        put(pair, F_PAIR)
            for text, factor in ((row.get("name"), F_NAME), (row.get("summary"), F_SUMMARY)):
                found = tokens_of(text)
                for token in set(found) - MINOR:
                    put(token, factor)
                for pair in set(pairs_of(found)):
                    put(pair, factor)
            for token in set(tokens_of(CUES.get(row["key"], ""))) - MINOR:
                put(token, F_CUE)
            for name in skill_rules.split_names(row.get("tool_names")):
                for token in set(tokens_of(name.replace("_", " "))) - MINOR:
                    put(token, F_TOOL)
            body = collections.Counter(tokens_of(row.get("instructions")))
            for token, times in body.items():
                if token not in MINOR:
                    put(token, min(F_TEXT_MAX, F_TEXT * times))
            # a concept holds what every word of its group holds
            for token, value in list(weight.items()):
                for number in concepts.get(token, ()):
                    weight[f"~{number}"] = max(weight[f"~{number}"], value)
            weights.append(weight)
        count = len(rows) or 1
        document_frequency = collections.Counter(term for weight in weights for term in weight)
        self.idf = {term: math.log(1 + (count - found + 0.5) / (found + 0.5))
                    for term, found in document_frequency.items()}
        # term -> [(skill index, its saturated weight)]
        self.postings = collections.defaultdict(list)
        for position, weight in enumerate(weights):
            for term, value in weight.items():
                self.postings[term].append((position, value * (K1 + 1) / (value + K1)))
        # What a slip or an Arabizi spelling may be read as: the words of the skills and of the groups
        vocabulary = set(self.postings) | group_words
        vocabulary = {term for term in vocabulary if not term.startswith("~")}
        self.vocabulary = vocabulary
        self.latin = sorted(term for term in vocabulary if term.isascii() and len(term) >= 4)
        self.arabic = sorted(term for term in vocabulary if ARABIC.search(term))
        self.skeletons = collections.defaultdict(list)
        for term in self.arabic:
            skeleton = arabic.skeleton(term)
            if len(skeleton) >= MIN_SKELETON:
                self.skeletons[skeleton].append(term)
        self._fixed = {}
        self.vectors = None

    # -- reading a word
    def _near(self, word, pool):
        limit = fuzzy.budget(len(word))
        if not limit:
            return None
        best, best_distance = None, limit + 1
        for candidate in pool:
            if abs(len(candidate) - len(word)) > limit:
                continue
            found = fuzzy.distance(word, candidate, limit)
            if found < best_distance:
                best, best_distance = candidate, found
        return best

    def read(self, raw):
        """:return: ``[(term, weight)]`` a word of the question may stand for, the word first"""
        term = stem(raw)
        if term in self.vocabulary or term in self.postings:
            return [(term, W_LITERAL)]
        if ARABIC.search(term):
            for variant in self._arabic_variants(term):
                if variant in self.vocabulary or variant in self.postings:
                    return [(variant, W_LITERAL)]
        if raw in self._fixed:
            return self._fixed[raw]
        found = []
        if LATIN.search(raw) and raw.isascii() and not raw.isdigit():
            if arabic.has_arabizi_mark(raw) or True:
                found += self._arabizi(raw)
            near = self._near(term, self.latin) if not arabic.has_arabizi_mark(raw) else None
            if near:
                found.append((near, W_FIXED))
        elif ARABIC.search(term):
            near = self._near(term, self.arabic)
            if near:
                found.append((near, W_FIXED))
        found = found[:3] or [(term, W_LITERAL)]
        if len(self._fixed) < 4000:
            self._fixed[raw] = found
        return found

    @staticmethod
    def _arabic_variants(term):
        out = []
        if len(term) > 3 and term[-1] in AR_TAILS:
            out.append(term[:-1])
        if len(term) > 3 and term[0] in AR_PREFIXES:
            out.append(term[1:])
            if len(term) > 4 and term[-1] in AR_TAILS:
                out.append(term[1:-1])
        return out

    def _arabizi(self, raw):
        """:return: the Arabic terms whose consonants the Latin spelling can be read as, the likeliest two.
        The spelling is also read without the article, the particle or the ending written onto it ("bel",
        "at", "na"), and with a 2 as the qaf the Levant says with it ("wa2fat"), as the Arabic stems are"""
        out = []
        for variant, penalty in arabizi_variants(raw):
            for reading in arabic.arabizi_skeletons(variant):
                if len(reading) < MIN_SKELETON:
                    continue
                for term in self.skeletons.get(reading, ()):
                    cost = arabic.arabizi_cost(variant, term)
                    if cost is not None and (len(reading) >= SHORT_SKELETON or cost <= SHORT_COST):
                        out.append((cost + penalty, term))
        out.sort()
        seen, terms = set(), []
        for _cost, term in out:
            if term not in seen:
                seen.add(term)
                terms.append((term, W_FIXED))
        return terms[:2]

    # -- scoring
    def query_terms(self, text, extra=""):
        """:return: ``{term: weight}`` for the words of a question, the pairs of them and the concepts they stand for"""
        terms = collections.defaultdict(float)
        read = []
        for raw in words_of(text):
            reading = self.read(raw)
            read.append(reading[0][0])
            if reading[0][0] in MINOR:
                continue
            for term, weight in reading:
                if term in self.generic:
                    weight *= W_GENERIC
                terms[term] = max(terms[term], weight)
                for number in self.concepts.get(term, ()):
                    key = f"~{number}"
                    terms[key] = max(terms[key], weight * W_CONCEPT)
        for pair in pairs_of(read):
            terms[pair] = W_LITERAL
        for raw in words_of(extra):
            for term, weight in self.read(raw):
                terms[term] = max(terms[term], W_SCREEN * weight)
        return terms

    def lexical(self, terms):
        """:return: ``{skill index: score}`` of the skills any term of the question is in"""
        scores = collections.defaultdict(float)
        for term, weight in terms.items():
            idf = self.idf.get(term)
            if idf is None:
                continue
            for position, saturated in self.postings[term]:
                scores[position] += weight * idf * saturated
        return scores


def arabizi_variants(raw):
    """:return: ``[(spelling, extra cost)]`` the ways a Latin spelling of an Arabic word is read: as typed,
    with its 2 as a q, and each of them without a particle or an ending written onto the word"""
    bases = [(raw, 0.0)]
    if "2" in raw:
        bases.append((raw.replace("2", "q"), 0.3))
    out = list(bases)
    for base, penalty in bases:
        heads = [("", base)] + [(head, base[len(head):]) for head in LATIN_HEADS
                                if base.startswith(head) and len(base) - len(head) >= 3]
        for head, rest in heads:
            tails = [rest] + [rest[:-len(tail)] for tail in LATIN_TAILS
                              if rest.endswith(tail) and len(rest) - len(tail) >= 3]
            for cut in tails:
                if cut != base and (cut, penalty + 0.5) not in out:
                    out.append((cut, penalty + 0.5))
    return out[:12]


def forms_of(trigger_words):
    return skill_rules.forms_of(trigger_words)


# ----------------------------------------------------------------------
# Choosing
# ----------------------------------------------------------------------
class Choice:
    """The skills chosen for a question and which layer chose each"""

    def __init__(self):
        self.keys, self.layer, self.score = [], {}, {}
        # Every skill the question scored for, the best first (the index offers these): (key, score)
        self.ranked = []

    def add(self, key, layer, score):
        if key not in self.layer:
            self.keys.append(key)
            self.layer[key] = layer
            self.score[key] = score


def screen_text(screen):
    """:return: the words of the title, menu or app of a screen shared with the question"""
    if not isinstance(screen, dict):
        return ""
    parts = []
    for key in SCREEN_KEYS:
        value = screen.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, (list, tuple)):
            parts.extend(item for item in value if isinstance(item, str))
    return " ".join(parts)[:200]


def route(index, text, model=None, screen=None, limit=2, engine=None, layers=LAYERS, engine_of=None):
    """Choose the skills a question needs. Each layer adds what it finds to the score of a skill, and the
    skills above the threshold are chosen, the highest first: a trigger word, as before, is enough alone.

    :param index: the :class:`Index` of the skills the person may use
    :param model: the model of the screen shared with the question
    :param screen: the snapshot of the screen: its title, menu or app say a little more
    :param engine: the local search model when it is switched on, else None
    :param engine_of: or a function that returns it, called only when the words left the choice unsure
    :param layers: which layers may choose (the tests measure each alone)
    :return: a :class:`Choice` with at most ``limit`` skills"""
    choice = Choice()
    text = text or ""
    parts = collections.defaultdict(dict)
    if "rules" in layers:
        for position, (forms, models) in enumerate(zip(index.forms, index.models)):
            hits = skill_rules.score(forms, text) + (2 if model and model in models else 0)
            if hits:
                parts[position]["rules"] = RULES_BONUS * min(hits, 3)
    if "lexical" in layers or "screen" in layers:
        terms = index.query_terms(text, screen_text(screen) if "screen" in layers else "")
        if "lexical" in layers:
            for position, score in index.lexical(terms).items():
                parts[position]["lexical"] = score
    if "screen" in layers and model in MODEL_HINTS:
        for key in MODEL_HINTS[model]:
            if key in index.keys:
                parts[index.keys.index(key)]["screen"] = SCREEN_PUSH * THRESHOLD
    if "embedding" in layers and text.strip():
        best = max((sum(found.values()) for found in parts.values()), default=0.0)
        if engine is None and engine_of is not None and best < CONFIDENT:
            engine = engine_of()
        if engine is not None:
            for position, similarity in _similar(index, engine, text).items():
                if similarity >= EMBED_MIN:
                    parts[position]["embedding"] = EMBED_WEIGHT * similarity
    ranked = sorted(((-sum(found.values()), position) for position, found in parts.items()))
    top = -ranked[0][0] if ranked else 0.0
    choice.ranked = [(index.keys[position], -negative) for negative, position in ranked[:MAX_RANKED]]
    for negative, position in ranked:
        score = -negative
        found = parts[position]
        if len(choice.keys) >= limit or score < THRESHOLD:
            break
        # A second skill must be close to the first, unless its own trigger word brought it (as it always did)
        if choice.keys and score < SECOND_SHARE * top and "rules" not in found:
            continue
        choice.add(index.keys[position], max(found, key=found.get), score)
    return choice


def offered(choice, keep, limit=INDEX_MAX):
    """:return: the keys the index of a call lists: ``keep`` (the skills given or kept in the conversation)
    first, then the best scored for the question, at most ``limit`` (more when ``keep`` is longer)"""
    shown = list(dict.fromkeys(keep))
    for key, _score in choice.ranked:
        if len(shown) >= max(limit, len(keep)):
            break
        if key not in shown:
            shown.append(key)
    return set(shown)


def _similar(index, engine, text):
    """:return: ``{skill index: score 0..1}`` of the reading of the search model, the vectors of the skills
    kept in the index. A model that fails is no signal."""
    from ..llm import embedder  # noqa: PLC0415
    try:
        if index.vectors is None:
            index.vectors = engine.embed(index.engine_text, kind="passage")
        query = engine.embed([" ".join(text.split())[:MAX_QUERY]], kind="query")[0]
    except Exception:  # noqa: BLE001 - a question is never stopped by the search model
        _logger.exception("Nudge: the search model failed on a question for the skill router")
        return {}
    floor, ceiling = getattr(engine, "floor", 0.5), getattr(engine, "ceiling", 0.9)
    out = {}
    for position, vector in enumerate(index.vectors):
        cosine = sum(a * b for a, b in zip(query, vector))
        score = embedder.scale(cosine, floor, ceiling)
        if score > 0:
            out[position] = score
    return out


# ----------------------------------------------------------------------
# Index per registry, and the misses
# ----------------------------------------------------------------------
def index_of(env, skills):
    """:return: the :class:`Index` of the recordset ``skills``, kept in the registry for as long as
    none of them changes: a new registry (a module update) or a changed skill builds it again"""
    # what a question is scored against, so that a change in the same second is seen too
    signature = tuple((skill.id, skill.write_date, hash((
        skill.key, skill.name, skill.summary, skill.instructions, skill.trigger_words, skill.model_names,
        skill.tool_names))) for skill in skills)
    registry = env.registry
    cache = getattr(registry, "_nudge_skill_router", None)
    if cache is None:
        cache = registry._nudge_skill_router = collections.OrderedDict()
    found = cache.get(signature)
    if found is not None:
        cache.move_to_end(signature)
        return found
    rows = [{"key": skill.key, "name": skill.name, "summary": skill.summary, "instructions": skill.instructions,
             "trigger_words": skill.trigger_words, "model_names": skill.model_names,
             "tool_names": skill.tool_names} for skill in skills]
    found = Index(rows)
    with _lock:
        cache[signature] = found
        while len(cache) > MAX_CACHED:
            cache.popitem(last=False)
    return found


MISSES = collections.deque(maxlen=MAX_MISSES)


def note_miss(sample):
    """Keep a question no skill was found for (already a redacted sample), for the coverage page"""
    MISSES.append((time.time(), sample))

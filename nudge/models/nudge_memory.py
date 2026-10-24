"""Answer memory: approved answers, served again without the AI (T16, S15).

A remembered answer is a *reference*: a question (with the other ways people
ask it), its answer, and what it is about (the mode, the screen, the menu, and
the steps of a screen explanation, kept as a recorded tour). A manager of
Nudge saves one from any answer with Save as reference, editing it first; a
thumbs-up of a manager keeps one at once; when the company asks for it, an
answer liked by enough different people becomes a Suggested reference that
waits for a manager's yes. Nothing is ever served without that yes.

Before any provider is called, the question is compared with the approved
references, with no AI: as typed, with its slips and Arabic in Latin letters
read right, carried to English by the term bridge (an Arabic question finds an
English reference), by the notions it names in any dialect, and by the TF-IDF
of ``llm.memory_match``. A sure match answers at once, at no cost, marked with
who approved it and when, with Ask the AI instead; close matches are offered
to choose from. A question that asks for live figures (how many, how much, a
list of records) is never answered from memory: it goes to its tool or rule.

A reference holds explanations, policies and how-tos, never live data. It is
served only to the people of its company, in its groups, who may open the
screen it is about. Its text is shown, never given to a model as instructions.
An answer kept in another language is translated once, by the cheapest model
of the provider, stored for a manager to review, and free from then on; in
private mode nothing is sent and the original is offered instead.
"""
import json
import logging
import re

from psycopg2 import OperationalError

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.misc import format_date

from ..llm import fingerprint, memory_match
from ..llm import snapshot as screen_snapshot
from ..llm.fingerprint import _SECRET as SECRET
from ..tools import base as tools_base
from ..tools import noai_commands
from ..tools import skills as skill_rules
from ..tools.base import MODE_AGENT, MODE_DEV, MODE_TALK
from ..tools.navigation import _visible_menus
from ..tools.noai_commands import bridge, fuzzy, grammar, normalise

_logger = logging.getLogger(__name__)

ENABLED_PARAM = "nudge.answer_memory"
THRESHOLD_PARAM = "nudge.memory_threshold"
LIKE_PARAM = "nudge.memory_like_suggest"
LIKE_COUNT_PARAM = "nudge.memory_like_count"
SKILL_COUNT_PARAM = "nudge.memory_skill_count"
DEFAULT_THRESHOLD = 0.75
DEFAULT_LIKE_COUNT = 3
DEFAULT_SKILL_COUNT = 5
# Offered, not served, when no AI provider is set up to ask instead
NO_PROVIDER_FLOOR = 0.4
# From here a reference is close enough to be offered as a choice
NEAR_FLOOR = 0.5
MAX_CHOICES = 3
# What a question and a reference must share besides a score (see ``_fits``): of the things the question
# names, the share the reference names too, to be answered with it and to be offered
SERVE_COVER = 0.99
OFFER_COVER = 0.75
# A match by meaning alone (the local search model) is believed from here, scaled 0 to 1
SEMANTIC_SURE = 0.8
MAX_SEARCHED = 2000
# Memories compared with a new one on each thumbs-up: the newest of the company
MAX_KNOWN = 2000
MAX_PENDING_PER_USER = 20
MAX_ANSWER_CHARS = 6000
MAX_VARIANTS = 20
MAX_VARIANT_CHARS = 300
MIN_QUESTION_CHARS = 8
# What a question carried to English by the bridge costs: its words were guessed from a glossary
BRIDGE_PENALTY = 0.05
TRANSLATE_SECONDS = 20
MAX_STEP_NOTES = 20
# pg_try_advisory_xact_lock(namespace, memory id): one translation of an answer at a time
TRANSLATE_LOCK = 0x4E4D
NO_TOKENS = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
# The tools whose results are the same for everybody: the documentation, the
# way through the menus, the structure of the database. Any other tool read
# or did something in this person's data; locate_data counts the records as
# the person sees them (record rules, companies), so it is not one of them
SAFE_TOOLS = frozenset((
    "search_odoo_documentation", "find_screen", "show_menu_path", "open_screen", "show_on_screen",
    "search_database_profile", "describe_model", "describe_module", "show_settings",
    "more_tools"))
# What the rules understand as asking for figures or records of the database: never from memory
LIVE_INTENTS = frozenset(("find", "filter", "chart"))
# A question that asks how or where, not for the records themselves
HOW_TO = re.compile(
    r"^\W*(?:how\b(?!\s+(?:many|much)\b)|where\b|what\s+(?:is|are|does|do)\b|why\b|explain\b|"
    r"كيف|ازاي|شلون|كيفاش|كيفيه|وين|فين|اين|ليش|ليه|لماذا|ما\s+(?:هو|هي)|ماذا|شو\s+يعني|"
    r"kif\b|keef\b|kaif\b|ezay\b|izay\b|ezzay\b|shlon\b|wein\b|fein\b)", re.IGNORECASE)
# Arabic written in Latin letters: a digit inside a word ("a3mel", "3ard")
ARABIZI = re.compile(r"[a-z][2356789]|[2356789][a-z]", re.IGNORECASE)
ENGLISH_WORDS = frozenset(
    "the a an is are was how what where when why do does did can could should i my we our you your to of in "
    "on for and or with this that it from please".split())
# Words of a question in Latin letters that only make it a question, in the dialects
ARABIZI_FILLERS = frozenset(
    "kif keef kaif kef ezay ezzay izay izzay shlon shloon sho shu esh eish ana badi biddi bdi 3ayez 3awez "
    "abghi abi momken mumkin law ya hal kaifa kayf wain wein fein feen ma meen".split())
# Categories of the lexicon that say what a question is about: the kinds of records, their states, what is
# done to them, their fields. The others (and, for, this, the periods...) only shape a sentence
NOTION_KEEP = frozenset((*grammar.KINDS, *grammar.STATES, *grammar.VERBS, *grammar.FIELDS,
                         "chart", "history", "revert", "save"))
LANGUAGE_NAMES = {
    "ar": "Arabic", "en": "English", "fr": "French", "es": "Spanish", "de": "German", "it": "Italian",
    "pt": "Portuguese", "tr": "Turkish", "ur": "Urdu", "hi": "Hindi", "fa": "Persian", "ru": "Russian",
    "zh": "Chinese", "id": "Indonesian", "bn": "Bengali", "ml": "Malayalam", "tl": "Filipino", "nl": "Dutch",
}
MODEL_NAME = re.compile(r"[a-z0-9_.]{1,128}")
# What a field on the screen holds of its record (not a choice or a tick shared by every record)
SCREEN_VALUE_TYPES = frozenset(("char", "text", "many2one", "monetary", "float", "integer", "date", "datetime"))
DIGIT = re.compile(r"\d")

# What a text of a reference means and its English words: the same for every company and every request
_CACHE_LIMIT = 5000
_notions_cache = {}
_bridged_cache = {}


def scrub(text):
    """:return: ``text`` without the keys and passwords a person may have typed into it, each line kept"""
    return SECRET.sub("…", str(text or ""))


def _one_line(text, limit):
    return " ".join(str(text or "").split())[:limit]


def _cached(cache, key, make):
    if key in cache:
        return cache[key]
    value = make(key)
    if len(cache) >= _CACHE_LIMIT:
        cache.clear()
    cache[key] = value
    return value


def _concepts_of(word):
    """:return: the concepts of the fingerprint ``word`` says ("send", "ابعت" and "ارسل" are one)"""
    return {"=" + term[2:] for term in fingerprint.terms(word) if term.startswith("c:")}


def _is_known(word):
    """:return: whether ``word`` (folded) is a word of a question already: a concept of the fingerprint, a
    word that only makes it a question. Such a word is never "corrected" into another one"""
    return (word in memory_match.STOPWORDS or word in fingerprint.FILLERS or word in ARABIZI_FILLERS
            or bool(_concepts_of(word)))


def notions(text):
    """:return: ``(notions, penalty)``: what ``text`` names, by rules and in any dialect (the meanings of
    the lexicon for Arabic dialects, English and Arabic in Latin letters, a slip read right; the
    concepts of the fingerprint; the stems of the other meaningful words), and what the slips read
    right cost"""
    tokens = normalise.tokenize(str(text or "")[:400])
    spans = grammar.scan(tokens)
    found, fixes = set(), []
    for span in spans:
        cats = {cat for cat in span.cats if cat in NOTION_KEEP}
        if not span.cats and span.j - span.i == 1 and not _is_known(tokens[span.i].norm):
            fix = fuzzy.suggest(tokens[span.i], first=span.i == 0)
            if fix and not fix.ambiguous:
                cats = {tag for tag in fix.option.tags if tag in NOTION_KEEP}
                if cats:
                    fixes.append(fix)
        if cats:
            found.update("=" + cat for cat in cats)
            continue
        if span.cats:
            continue
        for token in tokens[span.i:span.j]:
            word = token.norm
            if not word or word.isdigit() or word in ARABIZI_FILLERS or word in fingerprint.FILLERS:
                continue
            concepts = _concepts_of(word)
            if concepts:
                found.update(concepts)
                continue
            stem = memory_match.stem(word)
            if len(stem) >= 2 and stem not in memory_match.STOPWORDS and stem not in fingerprint.FILLERS:
                found.add(stem)
    for term in fingerprint.terms(text):
        if term.startswith("c:"):
            found.add("=" + term[2:])
    return frozenset(found), fuzzy.penalty(fixes)


# What is done, as the notions of a question say it: the same thing whatever the dialect. The rest of
# the notions are what it is done to (a quotation, a phone, a sales team)
ACTION_NOTIONS = frozenset("=" + name for name in (
    *grammar.VERBS, "create", "delete", "cancel", "confirm", "approve", "print", "send", "add", "edit",
    "register", "search", "open", "view", "export", "import", "setup", "copy", "archive", "install", "upload"))
FIELD_NOTIONS = frozenset("=" + name for name in grammar.FIELDS)


def _fits(asked, theirs):
    """Whether a reference is about what the question is about: sharing words is not enough ("create a
    sales team" shares two of three with "create a quotation in Sales" and asks another thing).

    :param asked: the notions of the question, :param theirs: those of the reference's question
    :return: ``(sure, close)``: the reference may answer the question; it may be offered as close to it.
        Both need the question's things to be named by the reference (all of them to answer, three
        quarters to offer), the same fields on both sides (a phone is not a mail), and, when both say what
        is done, the same thing done (to send is not to create)"""
    asked, theirs = frozenset(asked or ()), frozenset(theirs or ())
    things, others = asked - ACTION_NOTIONS, theirs - ACTION_NOTIONS
    shared = things & others
    if not shared or (things & FIELD_NOTIONS) != (others & FIELD_NOTIONS):
        return False, False
    done, theirs_done = asked & ACTION_NOTIONS, theirs & ACTION_NOTIONS
    if done and theirs_done and not done & theirs_done:
        return False, False
    cover = len(shared) / len(things)
    return cover >= SERVE_COVER, cover >= OFFER_COVER


def english_words(text, extra_pairs=()):
    """:return: the words of ``text`` carried to English by the term bridge (NP-12), or "" when it
    holds no word the bridge knows"""
    return " ".join(bridge.terms(text, extra_pairs))


class NudgeMemory(models.Model):
    _name = "nudge.memory"
    _inherit = ["nudge.recipe.mixin"]
    _description = "Nudge Remembered Answer"
    _order = "uses desc, id desc"
    _rec_name = "question"

    question = fields.Text(required=True)
    variants = fields.Text(
        string="Other Wordings",
        help="Other ways people ask it, one per line: each finds this answer as the question does.")
    answer = fields.Text(required=True)
    language = fields.Char(
        compute="_compute_language", store=True, readonly=False, size=8,
        help="The language of the question and the answer: en, ar, fr... A person asking in another "
             "language gets its translation.")
    translation_ids = fields.One2many("nudge.memory.translation", "memory_id", string="Translations")
    languages = fields.Char(compute="_compute_languages", string="Languages")
    mode = fields.Selection(
        [("talk", "Talk"), ("agent", "Agent")], default="talk", required=True,
        help="Talk: served to every question that matches. Agent: an answer of Agent mode, served in Agent "
             "mode too.")
    model_name = fields.Char(
        string="About the Screen",
        help="Technical name of the model the answer is about. Only people who may open it are answered "
             "with it.")
    view_type = fields.Char(string="View")
    menu_id = fields.Many2one(
        "ir.ui.menu", string="Menu", ondelete="set null",
        help="Where its screen is found. About a menu and no model: only people who see this menu are "
             "answered with it.")
    screen_bound = fields.Boolean(
        string="Only on Its Screen",
        help="Served only to someone asking from that screen: for an answer that says “this button”.")
    tour_id = fields.Many2one(
        "nudge.tour", string="Steps on the Screen", ondelete="set null",
        help="The steps of the explanation, shown again on the screen with no AI.")
    step_count = fields.Integer(related="tour_id.step_count", string="Steps")
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, index=True,
        help="The company whose people are answered with it. Empty: all of them.")
    group_ids = fields.Many2many(
        "res.groups", string="For the Groups",
        help="Who may be answered with it. Empty: everybody who uses Nudge.")
    state = fields.Selection(
        [("liked", "Collecting Likes"), ("proposed", "Suggested"), ("approved", "Approved"),
         ("refused", "Refused")],
        default="approved", required=True, index=True)
    active = fields.Boolean(default=True)
    uses = fields.Integer(string="Times Used", readonly=True)
    last_used = fields.Datetime(readonly=True)
    proposed_by = fields.Many2one("res.users", readonly=True)
    approved_by = fields.Many2one("res.users", readonly=True)
    approved_date = fields.Datetime(string="Approved On", readonly=True)
    like_user_ids = fields.Many2many(
        "res.users", "nudge_memory_like_rel", "memory_id", "user_id", string="Liked By", readonly=True)
    like_count = fields.Integer(compute="_compute_like_count", string="Likes")
    domain_key = fields.Char(
        string="Domain", compute="_compute_domain_key", store=True, index=True,
        help="The app or the skill it is about: approved answers of one domain propose a draft skill.")
    skill_id = fields.Many2one(
        "nudge.skill", string="In the Skill", readonly=True, index=True, ondelete="set null",
        help="The draft skill this answer was put into.")
    conversation_id = fields.Many2one("nudge.conversation", readonly=True, ondelete="set null")

    @api.depends("question")
    def _compute_language(self):
        for memory in self:
            memory.language = self._language_of(memory.question or "")

    @api.depends("language", "translation_ids.language")
    def _compute_languages(self):
        for memory in self:
            codes = [memory.language or ""] + memory.translation_ids.mapped("language")
            memory.languages = ", ".join(dict.fromkeys(code for code in codes if code))

    @api.depends("like_user_ids")
    def _compute_like_count(self):
        for memory in self:
            memory.like_count = len(memory.like_user_ids)

    @api.depends("model_name", "question")
    def _compute_domain_key(self):
        # sudo: which skills are published, a fact of Nudge's configuration, read for their words only
        published = self.env["nudge.skill"].sudo().search([("state", "=", "published")])
        entries = published._router_entries()
        for memory in self:
            if memory.model_name:
                memory.domain_key = memory.model_name.split(".")[0]
                continue
            picked = skill_rules.pick(entries, memory.question or "") if entries else []
            memory.domain_key = f"skill:{picked[0]}" if picked else False

    def _embedding_text(self):
        self.ensure_one()
        return (self.question or "")[:1500]

    @api.model_create_multi
    def create(self, vals_list):
        now = fields.Datetime.now()
        for vals in vals_list:
            for key in ("question", "answer", "variants"):
                if vals.get(key):
                    vals[key] = scrub(vals[key])
            if vals.get("state", "approved") == "approved":
                vals.setdefault("approved_by", self.env.uid)
                vals.setdefault("approved_date", now)
        return super().create(vals_list)

    def write(self, values):
        values = dict(values)
        for key in ("question", "answer", "variants"):
            if values.get(key):
                values[key] = scrub(values[key])
        if values.get("state") == "approved" and "approved_date" not in values:
            values["approved_date"] = fields.Datetime.now()
        left = self.browse()
        if values.get("active") is False or values.get("state", "approved") != "approved":
            left = self.filtered("skill_id")
        result = super().write(values)
        if "question" in values:
            # Its vector said the old question: it is made again
            self.env["nudge.embedding"]._forget(self._name, self.ids)
        if left:
            # An answer no longer approved leaves the draft skill it was put into
            left.sudo().skill_id._rebuild_from_references()
        return result

    def unlink(self):
        skills = self.sudo().skill_id
        result = super().unlink()
        skills._rebuild_from_references()
        return result

    def action_approve(self):
        self._nudge_check_access("write")
        self.write({"state": "approved", "approved_by": self.env.uid})
        self._after_approval()

    def action_refuse(self):
        self._nudge_check_access("write")
        self.write({"state": "refused"})

    def _after_approval(self):
        """Approved answers of one domain may now be enough for a draft skill (NP-19). Never stops the yes."""
        try:
            with self.env.cr.savepoint():
                self.env["nudge.skill"]._propose_from_references()
        except Exception:  # noqa: BLE001 - a draft that cannot be made never stops an approval
            _logger.exception("Nudge: no draft skill could be made from the references of company %s",
                              self.env.company.id)

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    @api.model
    def _set_defaults(self):
        """On by default, as the README says (answer memory, understanding common requests without AI):
        set where nothing is set, never over a choice.
        Called after an install and by the update that brings the defaults back."""
        Parameter = self.env["ir.config_parameter"].sudo()
        for key, value in ((ENABLED_PARAM, "True"), (THRESHOLD_PARAM, "0.75"), ("nudge.noai_first", "True")):
            if Parameter.get_param(key, None) is None:
                Parameter.set_param(key, value)

    @api.model
    def _param_on(self, key):
        return str(self.env["ir.config_parameter"].sudo().get_param(key) or "").lower() in ("1", "true")

    @api.model
    def _param_count(self, key, default):
        try:
            value = int(self.env["ir.config_parameter"].sudo().get_param(key) or 0)
        except ValueError:
            value = 0
        return value if value > 0 else default

    @api.model
    def _is_enabled(self):
        return self._param_on(ENABLED_PARAM)

    @api.model
    def _likes_suggest(self):
        """:return: whether answers liked by enough people are suggested to the managers (off by default)"""
        return self._param_on(LIKE_PARAM)

    @api.model
    def _like_count_needed(self):
        return self._param_count(LIKE_COUNT_PARAM, DEFAULT_LIKE_COUNT)

    @api.model
    def _skill_count(self):
        return self._param_count(SKILL_COUNT_PARAM, DEFAULT_SKILL_COUNT)

    @api.model
    def _get_threshold(self):
        try:
            value = float(self.env["ir.config_parameter"].sudo().get_param(THRESHOLD_PARAM) or 0)
        except ValueError:
            value = 0
        return value if 0 < value <= 1 else DEFAULT_THRESHOLD

    # ------------------------------------------------------------------
    # What a question is
    # ------------------------------------------------------------------
    @api.model
    def _language_of(self, text):
        """:return: the code of the language ``text`` is written in, by rules: Arabic letters or Arabic
        in Latin letters are Arabic, a sentence with English words is English, any other in Latin
        letters is the language of the person who writes it"""
        text = str(text or "")
        if memory_match.language(text) == "ar" or ARABIZI.search(text):
            return "ar"
        words = re.findall(r"[^\W\d_]+", text.lower())
        if not words or any(word in ENGLISH_WORDS for word in words):
            return "en"
        code = (self.env.user.lang or self.env.lang or "en_US").split("_")[0].lower()
        return "en" if code == "ar" else code

    @api.model
    def _language_name(self, code):
        """:return: the name of the language ``code`` in English, for the provider"""
        if code in LANGUAGE_NAMES:
            return LANGUAGE_NAMES[code]
        # sudo: the names of the languages, a fact of the database
        lang = self.env["res.lang"].sudo().with_context(active_test=False, lang="en_US").search(
            [("code", "=like", f"{code}%")], limit=1)
        return (lang.name or code).split(" /")[0].split(" (")[0]

    @api.model
    def _asks_live_data(self, text):
        """:return: whether ``text`` asks for figures or records of the database (how many, how much,
        a list): those are worked out from the data each time, never served from memory"""
        with self.env["nudge.noai"]._company_words():
            parsed = noai_commands.parse(str(text or "")[:400])
        readings = [(parsed.intent, parsed.confidence)] + [
            (candidate.get("intent"), candidate.get("confidence") or 0.0) for candidate in parsed.candidates]
        how_to = HOW_TO.match(memory_match.fold(text or ""))
        for intent, confidence in readings:
            if confidence < grammar.OFFER_FROM:
                continue
            if intent == "aggregate" or (intent in LIVE_INTENTS and not how_to):
                return True
        return False

    @api.model
    def _asks_to_act(self, text):
        """:return: whether ``text`` asks Nudge to change something (Agent mode): done, not explained"""
        with self.env["nudge.noai"]._company_words():
            parsed = noai_commands.parse(str(text or "")[:400])
        return (parsed.intent in grammar.WRITES and parsed.confidence >= grammar.OFFER_FROM
                and not HOW_TO.match(memory_match.fold(text or "")))

    @api.model
    def _question_forms(self, text):
        """:return: ``[(form, penalty)]`` of ``text``: as typed, with its slips and Arabic in Latin letters
        read as the words of the lexicon, and carried to English by the term bridge"""
        text = _one_line(text, 500)
        forms = [(text, 0.0)]
        tokens = normalise.tokenize(text)
        spans = grammar.scan(tokens)
        quoted = fuzzy.quoted_words(text)
        fixes = []
        for span in spans:
            if (span.cats or span.j - span.i != 1 or tokens[span.i].norm in quoted
                    or _is_known(tokens[span.i].norm)):
                continue
            fix = fuzzy.suggest(tokens[span.i], first=span.i == 0)
            if fix:
                fixes.append(fix)
        corrected, penalty = text, 0.0
        if fixes:
            corrected, penalty = fuzzy.corrected_text(text, fixes), fuzzy.penalty(fixes)
            if corrected != text:
                forms.append((corrected, penalty))
        english = english_words(corrected, self.env["nudge.lexicon.entry"]._pairs())
        if english and memory_match.words(english) != memory_match.words(corrected):
            forms.append((english, penalty + BRIDGE_PENALTY))
        return forms

    def _texts(self):
        """:return: the texts this reference is found by: its question, the other wordings, the questions
        of its translations, and the English words of those written in Arabic"""
        self.ensure_one()
        texts = [self.question or ""] + self._variant_lines()
        texts += [question for question in self.translation_ids.mapped("question") if question]
        found = []
        for text in dict.fromkeys(_one_line(text, MAX_VARIANT_CHARS * 2) for text in texts):
            if not text:
                continue
            found.append(text)
            if bridge.has_arabic(text) or ARABIZI.search(text):
                english = _cached(_bridged_cache, text, english_words)
                if english:
                    found.append(english)
        return found

    def _variant_lines(self):
        self.ensure_one()
        return [line.strip() for line in (self.variants or "").splitlines() if line.strip()]

    # ------------------------------------------------------------------
    # Keeping
    # ------------------------------------------------------------------
    @api.model
    def _propose_from(self, message):
        """Keep the question and the answer of ``message`` (a message of Nudge
        that its asker found useful), when it is the kind that may be kept.

        A manager's thumbs-up is a yes at once. Another person's counts as one like: when the company
        suggests from likes, the answer liked by enough different people waits for a manager's yes.
        :return: the remembered answer, or an empty recordset"""
        # sudo: the asker rates their own message; the history around it is read in full,
        # and the table is written by Nudge, for a manager to decide on
        self = self.sudo()  # noqa: PLW0642
        none = self.browse()
        message = message.sudo()
        if not self._is_enabled() or message.is_remembered or message.state != "complete":
            return none
        if self.env["nudge.provider"]._is_private_mode():
            # In private mode nothing of a question is kept for the managers to read
            return none
        answer = (message.content or "").strip()
        conversation = message.conversation_id
        earlier = conversation.message_ids.filtered(lambda other: other.id < message.id)
        question = earlier.filtered(lambda other: other.role == "user")[-1:]
        if not question or not answer or len(answer) > MAX_ANSWER_CHARS:
            return none
        text = " ".join((question.content or "").split())
        # Talk mode, typed or said, with no file, no command and nothing Nudge found in the database for
        # it. The screen is shared by default: it is no reason by itself, an answer that quotes its records is
        if (question.mode not in (False, MODE_TALK) or question.command
                or question.attachment_name or len(text) < MIN_QUESTION_CHARS
                or "<nudge_found>" in (question.context_text or "") or conversation.run_ids):
            return none
        screen = self._question_screen(question)
        if screen and self._quotes_screen(answer, screen):
            return none
        # Every tool called for this answer must be one of the safe ones
        between = earlier.filtered(lambda other: other.id > question.id) | message
        for turn in between:
            if any(call.get("name") not in SAFE_TOOLS for call in turn.tool_calls or []):
                return none
        if self._asks_live_data(text):
            return none
        user = self.env.user
        manager = user.has_group("nudge.group_nudge_manager")
        if not manager and not self._likes_suggest():
            return none
        company = self.env.company
        folded = memory_match.fold(text)
        known = self.with_context(active_test=False).search(
            [("company_id", "in", [company.id, False])], order="id desc", limit=MAX_KNOWN)
        for other in known:
            if memory_match.fold(" ".join(other.question.split())) == folded and other.answer.strip() == answer:
                if not manager and other.state in ("liked", "proposed") and other.active:
                    return other._count_like(user, text)
                return none
        if not manager:
            collecting = known.filtered(lambda other: other.state in ("liked", "proposed") and other.active)
            key, _score = memory_match.best(
                text, [(other.id, line) for other in collecting for line in other._texts()],
                self._get_threshold(), margin=0.0)
            if key:
                return collecting.browse(key)._count_like(user, text)
            if self.search_count([
                    ("state", "in", ("liked", "proposed")), ("proposed_by", "=", user.id)]) >= MAX_PENDING_PER_USER:
                return none
        model_name = str(screen.get("model") or "")
        model_name = model_name if MODEL_NAME.fullmatch(model_name) and model_name in self.env else ""
        memory = self.create({
            "question": text,
            "answer": answer,
            # Given on a screen, served on that screen: the manager widens it when it stays true elsewhere
            "model_name": model_name,
            "view_type": str(screen.get("view_type") or "")[:20] if model_name else False,
            "screen_bound": bool(model_name),
            "company_id": company.id,
            "state": "approved" if manager else ("proposed" if self._like_count_needed() <= 1 else "liked"),
            "proposed_by": user.id,
            "approved_by": user.id if manager else False,
            "conversation_id": conversation.id,
            "like_user_ids": [(4, user.id)],
        })
        if manager:
            memory._after_approval()
        return memory

    @api.model
    def _question_screen(self, question):
        """:return: the screen ``question`` was asked from, as kept with it (or with the earlier question that
        showed it, when it was sent as unchanged), or an empty dict"""
        screen = question.snapshot if isinstance(question.snapshot, dict) else {}
        if not screen and question.snapshot_hash:
            shown = question.conversation_id.message_ids.filtered(
                lambda other: other.role == "user" and other.id < question.id
                and other.snapshot_hash == question.snapshot_hash and isinstance(other.snapshot, dict))[-1:]
            screen = shown.snapshot if shown else {}
        return screen

    @api.model
    def _quotes_screen(self, answer, screen):
        """:return: whether ``answer`` repeats what the screen showed of its records (a name, an amount, a date,
        a reference): it is about those records, not a way of working that stays true for everybody"""
        shown = []
        record = screen.get("record")
        if isinstance(record, dict):
            shown.append(record.get("name"))
        for field in screen.get("fields") or []:
            if isinstance(field, dict) and field.get("type") in SCREEN_VALUE_TYPES:
                shown.append(field.get("value"))
        for row in (screen.get("rows") or []) + (screen.get("cards") or []):
            if isinstance(row, dict):
                # A word alone in a list is mostly a state ("New", "Confirmed"); a name or a figure is a record's
                shown.extend(value for key, value in row.items()
                             if key != "id" and isinstance(value, str) and (" " in value.strip() or DIGIT.search(value)))
        folded = memory_match.fold(answer)
        for value in shown:
            if not isinstance(value, (str, int, float)) or isinstance(value, bool):
                continue
            value = str(value).strip()
            if len(value) < 4 or value == screen_snapshot.HIDDEN:
                continue
            pieces = [value] + [piece for piece in value.split() if len(piece) >= 3 and DIGIT.search(piece)]
            if any(memory_match.fold(piece) in folded for piece in pieces):
                return True
        return False

    def _count_like(self, user, text):
        """One more person liked this answer (to ``text``): a Suggested reference once enough different
        people did. Never approved by likes alone."""
        self.ensure_one()
        values = {"like_user_ids": [(4, user.id)]}
        wordings = self._variant_lines()
        if memory_match.fold(text) not in {memory_match.fold(line) for line in wordings + [self.question]}:
            values["variants"] = "\n".join((wordings + [_one_line(text, MAX_VARIANT_CHARS)])[-MAX_VARIANTS:])
        self.write(values)
        if self.state == "liked" and len(self.like_user_ids) >= self._like_count_needed():
            self.state = "proposed"
        return self

    @api.model
    def _check_manager(self):
        if not self.env.user.has_group("nudge.group_nudge_manager"):
            raise AccessError(_("Only Nudge managers save references."))

    @api.model
    def action_save_from_message(self, message_id):
        """Save as reference: the form a manager checks and edits before an answer of Nudge is kept.
        :return: the action opening it"""
        self._check_manager()
        wizard = self.env["nudge.memory.save"].create(self._reference_values(message_id))
        return {
            "type": "ir.actions.act_window", "res_model": "nudge.memory.save", "res_id": wizard.id,
            "view_mode": "form", "views": [(False, "form")], "target": "new",
            "name": _("Save as Reference"),
        }

    @api.model
    def _reference_values(self, message_id):
        """:return: the values of a reference made of the answer ``message_id``, for the manager to check
        :raise UserError: for an answer that changed data, or a question that asks for live figures"""
        try:
            message_id = int(message_id)
        except (TypeError, ValueError):
            message_id = 0
        # A search, as the manager: the conversations of other companies stay theirs
        message = self.env["nudge.message"].search([("id", "=", message_id)])
        if not message or message.role != "assistant":
            raise UserError(_("This answer cannot be saved as a reference."))
        # sudo: the screens, the tools and the steps of the answer are read to describe it
        record = message.sudo()
        if record.state != "complete":
            raise UserError(_("Only a finished answer can be saved as a reference."))
        if record.is_remembered:
            raise UserError(_("This answer already comes from Nudge's memory."))
        conversation = record.conversation_id
        earlier = conversation.message_ids.filtered(lambda other: other.id < record.id)
        questions = earlier.filtered(lambda other: other.role == "user")
        question = questions[-1:]
        if not question:
            raise UserError(_("This answer cannot be saved as a reference."))
        text = " ".join((question.content or "").split())
        between = earlier.filtered(lambda other: other.id > question.id) | record
        calls = [call.get("name") for turn in between for call in turn.tool_calls or []]
        for name in calls:
            tool = tools_base.TOOLS.get(name)
            if tool is None or tool.kind != "read":
                raise UserError(_(
                    "This answer changed data or did work: only explanations, policies and how-tos are kept "
                    "as references."))
        if self._asks_live_data(text):
            raise UserError(_(
                "This question asks for live figures of the database: Nudge works them out each time, from "
                "the data, and never answers them from memory."))
        steps = []
        for turn in between:
            for event in turn.screen_events or []:
                if isinstance(event, dict) and event.get("type") == "guidance" and event.get("steps"):
                    steps = list(event["steps"])
        # Sent as unchanged: the screen is the one of the question that showed it
        screen = self._question_screen(question)
        model_name = str(screen.get("model") or "")
        model_name = model_name if MODEL_NAME.fullmatch(model_name) and model_name in self.env else ""
        answer = (record.content or "").strip()
        if not answer and steps:
            answer = "\n".join(f"{number}. {step.get('text') or ''}" for number, step in enumerate(steps, 1))
        warning = False
        if any(name not in SAFE_TOOLS for name in calls):
            warning = _(
                "This answer was made from records of the database: keep only what stays true for everybody, "
                "never a figure or a record that changes.")
        return {
            "message_ref": record.id,
            "question": scrub(text),
            "variants": "\n".join(self._known_wordings(text, conversation, question)),
            "answer": scrub(answer)[:MAX_ANSWER_CHARS],
            "language": self._language_of(text),
            "mode": MODE_AGENT if question.mode == MODE_AGENT else MODE_TALK,
            "model_name": model_name,
            "view_type": str(screen.get("view_type") or "")[:20],
            "menu_id": self._menu_of(model_name).id,
            "screen_bound": bool(model_name),
            "steps": steps or False,
            "company_id": (conversation.company_id or self.env.company).id,
            "warning": warning,
        }

    @api.model
    def _known_wordings(self, text, conversation, question):
        """:return: the other ways the question is known to be asked: read right without its slips, the
        sample of the recurring question it counts as, the same question asked earlier in the
        conversation"""
        folded = {memory_match.fold(text)}
        found = []

        def keep(line):
            line = _one_line(scrub(line), MAX_VARIANT_CHARS)
            if line and memory_match.fold(line) not in folded:
                folded.add(memory_match.fold(line))
                found.append(line)

        forms = self._question_forms(text)
        if len(forms) > 1 and forms[1][0] != english_words(text):
            keep(forms[1][0])
        key = fingerprint.of(text)
        if key:
            # sudo: one sample of the recurring question, already kept without addresses or numbers
            sample = self.env["nudge.question"].sudo().search([
                ("company_id", "=", (conversation.company_id or self.env.company).id),
                ("fingerprint", "=", key)], limit=1).sample
            if sample:
                keep(sample)
            for asked in conversation.message_ids.filtered(
                    lambda other: other.role == "user" and other.id < question.id):
                if fingerprint.of(asked.content) == key:
                    keep(asked.content)
        return found[:MAX_VARIANTS]

    @api.model
    def _menu_of(self, model_name):
        """:return: the menu of the manager that opens a list of ``model_name``, the shortest path first"""
        if not model_name:
            return self.env["ir.ui.menu"]
        menus = _visible_menus(self.env).filtered(
            lambda menu: menu.action._name == "ir.actions.act_window" and menu.action.res_model == model_name)
        return menus.sorted(lambda menu: (len(menu.complete_name or ""), menu.id))[:1]

    @api.model
    def _save_reference(self, values):
        """Keep a reference a manager checked: approved at once, with its steps as a recorded tour.
        :return: the reference"""
        self._check_manager()
        question = _one_line(scrub(values.get("question")), 1000)
        answer = scrub(values.get("answer") or "").strip()
        if len(question) < 2 or not answer:
            raise UserError(_("A reference needs a question and an answer."))
        if len(answer) > MAX_ANSWER_CHARS:
            raise UserError(_("The answer is too long. The limit is %s characters.", MAX_ANSWER_CHARS))
        if self._asks_live_data(question):
            raise UserError(_(
                "This question asks for live figures of the database: Nudge works them out each time, from "
                "the data, and never answers them from memory."))
        model_name = str(values.get("model_name") or "")
        model_name = model_name if MODEL_NAME.fullmatch(model_name) and model_name in self.env else ""
        variants = []
        for line in str(values.get("variants") or "").splitlines():
            line = _one_line(scrub(line), MAX_VARIANT_CHARS)
            if line and memory_match.fold(line) != memory_match.fold(question) and line not in variants:
                variants.append(line)
        company = self.env["res.company"].browse(values.get("company_id") or self.env.company.id)
        groups = [int(group) for group in values.get("group_ids") or []]
        message = self.env["nudge.message"].sudo().browse(int(values.get("message_ref") or 0)).exists()
        memory = self.create({
            "question": question,
            "variants": "\n".join(variants[:MAX_VARIANTS]),
            "answer": answer,
            "language": (values.get("language") or self._language_of(question))[:8],
            "mode": values.get("mode") if values.get("mode") in (MODE_TALK, MODE_AGENT) else MODE_TALK,
            "model_name": model_name,
            "view_type": str(values.get("view_type") or "")[:20],
            "menu_id": values.get("menu_id") or False,
            "screen_bound": bool(values.get("screen_bound") and model_name),
            "company_id": company.id,
            "group_ids": [(6, 0, groups)],
            "state": "approved",
            "proposed_by": self.env.uid,
            "approved_by": self.env.uid,
            "conversation_id": message.conversation_id.id if message else False,
        })
        steps = values.get("steps")
        if isinstance(steps, list) and steps:
            tour = self.env["nudge.tour"]._from_guidance(
                question, steps, res_model=model_name, view_type=memory.view_type, phrases=question,
                group_ids=groups, company=company)
            if tour:
                memory.tour_id = tour
        memory._after_approval()
        return memory

    # ------------------------------------------------------------------
    # Serving
    # ------------------------------------------------------------------
    @api.model
    def _user_groups(self, user):
        # v19 names it all_group_ids; 17 and 18 groups_id (implied groups included)
        user = user.sudo()
        return user.all_group_ids if "all_group_ids" in user._fields else user.groups_id

    @api.model
    def _servable(self, mode=MODE_TALK, screen_model=None, steps_only=False):
        """:return: the approved references of this company that this person may be answered with, in this
        mode and on this screen: in their groups, about a screen and a menu they may open"""
        groups = self._user_groups(self.env.user)
        # As the person, not sudo: may they open the screen the answer is about
        person = self.env(su=False)
        visible = []
        # sudo: people have no access to the table; what they may see is decided here
        pool = self.sudo().search([
            ("state", "=", "approved"), ("company_id", "in", [self.env.company.id, False]),
        ], limit=MAX_SEARCHED)

        def allowed(memory):
            if memory.group_ids and not memory.group_ids & groups:
                return False
            if mode == MODE_AGENT and memory.mode != MODE_AGENT:
                return False
            if steps_only and not memory.tour_id:
                return False
            if memory.model_name:
                if memory.model_name not in person or not person[memory.model_name]._nudge_has_access("read"):
                    return False
                if memory.screen_bound and memory.model_name != screen_model:
                    return False
            elif memory.menu_id:
                # About a menu only (a screen with no model of its own): those who see it
                if not visible:
                    visible.append(set(person["ir.ui.menu"]._visible_menu_ids()))
                if memory.menu_id.id not in visible[0]:
                    return False
            return True

        return pool.filtered(allowed)

    @api.model
    def _match(self, text, floor=False, mode=MODE_TALK, screen_model=None, steps_only=False):
        """:param floor: offer the best match above a lower floor, instead of requiring confidence
        :return: ``(reference, score, near)``: the reference to answer ``text`` with (or None), its score,
            and the references close enough to be offered as choices when none is sure"""
        pool = self._servable(mode=mode, screen_model=screen_model, steps_only=steps_only)
        if not pool:
            return None, 0.0, []
        with self.env["nudge.noai"]._company_words():
            forms = self._question_forms(text)
            asked = [notions(form) for form, _penalty in forms[:2]]
        candidates, concepts = [], {}
        # The references that are about what is asked, not only worded alike (see ``_fits``)
        sure_ids, close_ids = set(), set()
        for memory in pool:
            texts = memory._texts()
            candidates += [(memory.id, line) for line in texts]
            score = 0.0
            for line in texts:
                theirs, _cost = _cached(_notions_cache, line, notions)
                for (mine, cost), (_form, penalty) in zip(asked, forms):
                    score = max(score, memory_match.dice(mine, theirs) - max(cost, penalty))
                    sure, close = _fits(mine, theirs)
                    if sure:
                        sure_ids.add(memory.id)
                    if close:
                        close_ids.add(memory.id)
            if score > 0:
                concepts[memory.id] = score
        # By meaning too, when the local search model is there; None otherwise
        semantic = self.env["nudge.embedding"]._semantic(self._name, text, ids=pool.ids)
        for other, value in (semantic or {}).items():
            if value >= SEMANTIC_SURE:
                sure_ids.add(other)
                close_ids.add(other)
        ranked = memory_match.rank_forms(forms, candidates, semantic=semantic, concepts=concepts)
        threshold = self._get_threshold()
        if floor:
            key, score = memory_match.choose(
                [pair for pair in ranked if pair[1] in close_ids], min(threshold, NO_PROVIDER_FLOOR), margin=0.0)
        else:
            key, score = memory_match.choose(
                [pair for pair in ranked if pair[1] in sure_ids], threshold, margin=0.05)
        near = [] if key else [
            pool.browse(other) for value, other in ranked if value >= NEAR_FLOOR and other in close_ids][:MAX_CHOICES]
        return (pool.browse(key) if key else None), score, near

    @api.model
    def _find(self, text, floor=False):
        """:param floor: offer the best match above a lower floor, instead of requiring confidence
        :return: ``(remembered answer, score)`` for ``text``, among the approved
            ones of this company that this person may see; ``(None, score)`` otherwise"""
        memory, score, _near = self._match(text, floor=floor)
        return memory, score

    @api.model
    def _chat_serve(self, conversation, text, mode, focus, found, explain, background, fresh=False, snapshot=None):
        """Answer ``text`` from memory, when it can be, before any provider is called: a sure match is the
        answer, close matches are offered to choose from (with Ask the AI instead).
        :return: the job of the stream (the answer is already written), or None to go on"""
        if fresh or focus or found or background or mode == MODE_DEV or not self._is_enabled():
            return None
        # sudo: whether the company has any approved answer at all; nothing more is read before the checks
        if not self.sudo().search_count([
                ("state", "=", "approved"), ("company_id", "in", [self.env.company.id, False])], limit=1):
            return None
        if self._asks_live_data(text) or (mode == MODE_AGENT and self._asks_to_act(text)):
            # Live figures and work to do: their tool or rule answers, never a memory
            return None
        screen = screen_snapshot.sanitize(snapshot) or {}
        screen_model = screen.get("model") if isinstance(screen.get("model"), str) else None
        provider = self.env["nudge.provider"].sudo()._get_default()
        memory, _score, near = self._match(
            text, floor=not provider, mode=mode, screen_model=screen_model, steps_only=explain == "screen")
        if memory:
            return self._answer_with(conversation, text, memory, mode=mode)
        if near and provider:
            return self._offer(conversation, text, near, mode=mode)
        return None

    def _label(self, note=""):
        """:return: what is said under an answer served from this reference: who approved it and when"""
        self.ensure_one()
        # sudo: who approved it and when are shown to the person answered; the table is not theirs
        record = self.sudo()
        approver = record.approved_by.name or record.create_uid.name or ""
        label = _("From Nudge's memory, approved by %(manager)s on %(date)s",
                           manager=approver, date=format_date(self.env, record.approved_date or record.create_date))
        return f"{label}. {note}" if note else label

    def _answer_in(self, language, asked=""):
        """:return: ``(answer, note)``: the answer in ``language`` (kept, or translated now, once), or the
        original with a note saying why it is not translated"""
        self.ensure_one()
        if not language or language == (self.language or "en"):
            return self.answer, ""
        kept = self.translation_ids.filtered(lambda translation: translation.language == language)[:1]
        if kept:
            return kept.answer, ""
        name = self._language_name(self.language or "en")
        Provider = self.env["nudge.provider"].sudo()
        chosen = Provider._get_for_mode("agent") or Provider._get_default()
        if Provider._is_private_mode() and not chosen.runs_locally:
            return self.answer, _("Kept in %s: private mode sends nothing to translate it.", name)
        text = self._translate_once(language, asked) if chosen else ""
        if text:
            return text, ""
        return self.answer, _("Kept in %s.", name)

    def _translate_once(self, language, asked=""):
        """Translate the answer into ``language`` once, and keep it: whoever asks next is served the kept
        translation at no cost. Of two people asking at the same instant, the second is given the
        original rather than a second call.
        :return: the translation, or "" """
        self.ensure_one()
        self.env.cr.execute("SELECT pg_try_advisory_xact_lock(%s, %s)", (TRANSLATE_LOCK, self.id))
        if not self.env.cr.fetchone()[0]:
            return ""
        # What another request kept since this one began, read past the cache
        self.env.cr.execute(
            "SELECT answer FROM nudge_memory_translation WHERE memory_id = %s AND language = %s",
            (self.id, language))
        row = self.env.cr.fetchone()
        if row:
            return row[0]
        notes = self._step_notes()
        text, kept_notes = self._reference_translate(self.answer, language, notes)
        if not text:
            return ""
        Provider = self.env["nudge.provider"].sudo()
        chosen = Provider._get_for_mode("agent") or Provider._get_default()
        # The model that most likely answered: the economy one when set, as _complete tries it first
        economy = chosen._get_economy() if chosen else None
        model_name = economy[1] if economy else chosen.model_name
        # sudo: the translation is Nudge's own record, kept for a manager to review
        self.env["nudge.memory.translation"].sudo().create({
            "memory_id": self.id, "language": language, "answer": text[:MAX_ANSWER_CHARS],
            "question": _one_line(scrub(asked), MAX_VARIANT_CHARS) or False,
            "origin": "ai", "model_name": model_name or False,
            "step_notes": json.dumps(kept_notes, ensure_ascii=False) if kept_notes else False,
        })
        self.invalidate_recordset(["translation_ids"])
        return text

    def _step_notes(self):
        """:return: the notes of the steps of the tour of this reference, as the pointer says them"""
        self.ensure_one()
        notes = (step.note or "" for step in self.sudo().tour_id.step_ids.sorted(lambda s: (s.sequence, s.id)))
        return list(dict.fromkeys(note.strip() for note in notes if note.strip()))[:MAX_STEP_NOTES]

    @api.model
    def _reference_translate(self, text, language, notes=()):
        """One very short request: the source text only, "translate to <language>, keep terms". The notes of
        the steps of its tour, when it has any, go in the same request (a JSON of the answer and the notes),
        never in a second one. Nothing is sent in private mode to a provider outside the server.
        ``nudge.provider._complete`` gives it to the economy model of the provider when one is set (S6), to
        its main model otherwise.

        :return: ``(translation, {note: translated note})``, the translation "" when no provider can answer"""
        Provider = self.env["nudge.provider"].sudo()
        chosen = Provider._get_for_mode("agent") or Provider._get_default()
        if not chosen or (Provider._is_private_mode() and not chosen.runs_locally):
            return "", {}
        system = (f"Translate to {self._language_name(language)}. Keep the terms, names, numbers and "
                  f"formatting. Reply with the translation only.")
        notes = list(notes or ())
        if notes:
            system = (f"Translate to {self._language_name(language)} the values of this JSON object. Keep the "
                      f"terms, names, numbers and formatting. Reply with the JSON object only, with the same "
                      f"keys: \"answer\" is a text, \"notes\" a list in the same order.")
            text = json.dumps({"answer": text, "notes": notes}, ensure_ascii=False)
        answer = (Provider._complete(system, text, self.env.user, self.env.company,
                                     timeout=TRANSLATE_SECONDS) or "").strip()
        if not notes:
            return answer, {}
        try:
            body = answer[answer.index("{"):answer.rindex("}") + 1]
            data = json.loads(body)
            translated = str(data.get("answer") or "").strip()
            done = [str(note).strip() for note in data.get("notes") or []]
        except (ValueError, AttributeError, TypeError):
            return "", {}
        pairs = {note: new for note, new in zip(notes, done) if new} if len(done) == len(notes) else {}
        return translated, pairs

    def _question_in(self, language):
        self.ensure_one()
        kept = self.translation_ids.filtered(
            lambda translation: translation.language == language and translation.question)[:1]
        return kept.question or self.question

    def _replay_steps(self, language=""):
        """:return: the steps of the reference for the pointer, as the person may see them (the record
        rules of the tour, the menus they may open), with no AI; the notes in ``language`` when a
        translation of the answer kept them"""
        self.ensure_one()
        if not self.tour_id:
            return []
        Tour = self.env(su=False)["nudge.tour"]
        tour = Tour.search([("id", "=", self.tour_id.id)])
        if not tour or not tour._is_offered(Tour._installed_modules()):
            return []
        steps, _skipped = tour._pointer_steps()
        # sudo: the kept translation of an answer the person is served is Nudge's own record
        kept = self.sudo().translation_ids.filtered(lambda translation: translation.language == language)[:1]
        try:
            notes = json.loads(kept.step_notes) if language and kept.step_notes else {}
        except ValueError:
            notes = {}
        if notes:
            for step in steps:
                step["text"] = notes.get((step.get("text") or "").strip()) or step.get("text")
        return steps

    def _answer_with(self, conversation, text, memory, mode=MODE_TALK, ask=True):
        """Write the question (unless ``ask`` is False) and ``memory`` as its answer, in the language of
        the question, at no cost, and return the job of the stream"""
        record = conversation.sudo()
        self._leave_open_work(record)
        if not record.name:
            record.name = text[:60].strip()
        language = self._language_of(text)
        content, note = memory._answer_in(language, asked=text)
        steps = memory._replay_steps(language)
        events = [{"type": "guidance", "steps": steps}] if steps else []
        Message = self.env["nudge.message"].sudo()
        question = Message.browse()
        if ask:
            question = Message.create({
                "conversation_id": conversation.id, "role": "user", "content": text, "mode": mode})
        answer = Message.create({
            "conversation_id": conversation.id, "role": "assistant", "content": content,
            "state": "complete", "is_remembered": True, "memory_id": memory.id,
            "screen_events": events or False})
        self._count_use(memory, conversation=conversation, message=answer)
        return {
            "conversation_id": conversation.id,
            "user_message_id": question.id,
            "title": record.name,
            "mode": mode,
            "first_events": [{"type": "delta", "text": content}] + events,
            # Ends the stream after the events above: no provider is asked
            "answered": {"type": "done", "message_id": answer.id, "state": "complete", "message": "",
                         "memory": {"id": memory.id, "label": memory._label(note)}},
        }

    @api.model
    def _leave_open_work(self, record):
        """As every other answer does (nudge.noai._deliver): a card waiting for a decision is left, the run is
        ended. Otherwise a yes on the old card dropped its call and the turns ended on this answer"""
        if record.waiting_step_id:
            record._agent_leave_undecided()
        if record.run_id:
            record.run_id._end(record.run_id._outcome("stopped"))
            record.run_id = False

    @api.model
    def _offer(self, conversation, text, near, mode=MODE_TALK):
        """Close matches, none sure: offered to choose from, with Ask the AI instead. Nothing is spent.
        :return: the job of the stream"""
        record = conversation.sudo()
        self._leave_open_work(record)
        if not record.name:
            record.name = text[:60].strip()
        language = self._language_of(text)
        content = _("I have approved answers to questions close to yours. Pick one, or ask the AI instead.")
        items = [{"id": memory.id, "question": memory._question_in(language)} for memory in near]
        Message = self.env["nudge.message"].sudo()
        question = Message.create({"conversation_id": conversation.id, "role": "user", "content": text, "mode": mode})
        answer = Message.create({
            "conversation_id": conversation.id, "role": "assistant", "content": content, "state": "complete",
            "is_remembered": True, "screen_events": [{"type": "memory_choices", "items": items}]})
        return {
            "conversation_id": conversation.id,
            "user_message_id": question.id,
            "title": record.name,
            "mode": mode,
            "first_events": [{"type": "delta", "text": content}],
            "answered": {"type": "done", "message_id": answer.id, "state": "complete", "message": "",
                         "memory": {"id": False, "choices": items}},
        }

    @api.model
    def _own_message(self, message_id):
        """:return: the message ``message_id`` of a conversation of this person, in sudo
        :raise AccessError: for any other"""
        if not self.env.user.has_group("nudge.group_nudge_user"):
            raise AccessError(_("Nudge is not open to you."))
        try:
            message_id = int(message_id)
        except (TypeError, ValueError):
            message_id = 0
        # sudo: the message is read only once it is known to be of this person's conversation
        message = self.env["nudge.message"].sudo().browse(message_id).exists()
        if not message or message.conversation_id.user_id != self.env.user:
            raise AccessError(_("This message is not yours."))
        return message

    @api.model
    def reference_info(self, message_id):
        """For the panel: what is said under an answer served from memory, and the choices of an offer.
        :return: ``{"label", "choices": [{"id", "question"}]}``"""
        message = self._own_message(message_id)
        choices = []
        for event in message.screen_events or []:
            if isinstance(event, dict) and event.get("type") == "memory_choices":
                choices = [{"id": int(item["id"]), "question": str(item.get("question") or "")}
                           for item in event.get("items") or [] if isinstance(item, dict) and item.get("id")]
        label = ""
        memory = message.memory_id
        if memory:
            note = ""
            question = message.conversation_id.message_ids.filtered(
                lambda other: other.role == "user" and other.id < message.id)[-1:]
            if (message.content == memory.answer
                    and self._language_of(question.content or "") != (memory.language or "en")):
                note = _("Kept in %s.", self._language_name(memory.language or "en"))
            label = memory._label(note)
        return {"label": label, "choices": choices}

    @api.model
    def answer_choice(self, message_id, memory_id):
        """The person picked one of the references offered: it is the answer, at no cost, and their words
        become one more way to find it.
        :return: ``{"conversation_id"}``"""
        message = self._own_message(message_id)
        offered = {int(item.get("id") or 0) for event in message.screen_events or []
                   if isinstance(event, dict) and event.get("type") == "memory_choices"
                   for item in event.get("items") or [] if isinstance(item, dict)}
        try:
            memory_id = int(memory_id)
        except (TypeError, ValueError):
            memory_id = 0
        if not memory_id or memory_id not in offered:
            raise AccessError(_("This answer was not offered here."))
        conversation = message.conversation_id
        conversation._chat_check_not_at_work()
        question = conversation.message_ids.filtered(
            lambda other: other.role == "user" and other.id < message.id)[-1:]
        text = question.content or ""
        mode = MODE_AGENT if question.mode == MODE_AGENT else MODE_TALK
        screen = question.snapshot if isinstance(question.snapshot, dict) else {}
        memory = self._servable(mode=mode, screen_model=screen.get("model")).filtered(
            lambda one: one.id == memory_id)
        if not memory:
            raise AccessError(_("This answer is not open to you any more."))
        lines = memory._variant_lines()
        folded = {memory_match.fold(line) for line in lines + [memory.question]}
        wording = _one_line(scrub(text), MAX_VARIANT_CHARS)
        if wording and memory_match.fold(wording) not in folded and not self._asks_live_data(wording):
            # sudo: Nudge keeps the wording on its own record; it finds the answer, it is never shown as one
            memory.sudo().variants = "\n".join((lines + [wording])[-MAX_VARIANTS:])
        self._answer_with(conversation, text, memory, mode=mode, ask=False)
        return {"conversation_id": conversation.id}

    @api.model
    def _count_use(self, memory, conversation=None, message=None):
        # Never waits and never fails, as nudge.skill._count_given: a memory another answer is counting at this
        # instant (SKIP LOCKED), or counted since this one began (a serialization error under repeatable read, which
        # ended a phone's answer in "Something went wrong"), is not counted this time; the figure is an indication
        try:
            with self.env.cr.savepoint():
                self.env.cr.execute(
                    "UPDATE nudge_memory SET uses = COALESCE(uses, 0) + 1, last_used = now() at time zone 'utc' "
                    "WHERE id IN (SELECT id FROM nudge_memory WHERE id = %s FOR UPDATE SKIP LOCKED)",
                    (memory.id,), log_exceptions=False)
        except OperationalError:
            _logger.info("Nudge: memory %s counted by another answer at the same time, left uncounted", memory.id)
        memory.invalidate_recordset(["uses", "last_used"])
        # A call that cost nothing: the usage report shows how many the memory answered
        links = {}
        if conversation:
            links.update(conversation_id=conversation.id, message_id=message.id)
        self.env["nudge.usage"]._add("memory", dict(NO_TOKENS), 0.0, self.env.user, self.env.company, **links)

    @api.model
    def suggest(self, words):
        """Without any AI provider: the remembered answer that best fits ``words``.
        :return: ``{"id", "question", "answer", "label"}`` or False"""
        if not self.env.user.has_group("nudge.group_nudge_user"):
            raise AccessError(_("Nudge is not open to you."))
        text = " ".join(str(words or "").split())[:500]
        if not self._is_enabled() or len(text) < 2 or self._asks_live_data(text):
            return False
        memory, _score = self._find(text, floor=True)
        if not memory:
            return False
        self._count_use(memory)
        return {"id": memory.id, "question": memory.question, "answer": memory.answer, "label": memory._label()}


class NudgeMemoryTranslation(models.Model):
    """A reference in another language: translated once by the AI, or written by a manager. Kept, so the
    next person asking in that language is answered at no cost."""
    _name = "nudge.memory.translation"
    _description = "Nudge Remembered Answer Translation"
    _order = "language, id"
    _rec_name = "language"

    if hasattr(models, "Constraint"):
        _unique_language = models.Constraint(
            "unique(memory_id, language)", "A remembered answer has one translation per language.")
    else:  # Odoo 17 and 18
        _sql_constraints = [("unique_language", "unique(memory_id, language)",
                             "A remembered answer has one translation per language.")]

    memory_id = fields.Many2one("nudge.memory", required=True, index=True, ondelete="cascade")
    company_id = fields.Many2one(related="memory_id.company_id", store=True, index=True)
    language = fields.Char(required=True, size=8, help="en, ar, fr...")
    question = fields.Text(help="The question in this language: it finds the answer as the question does.")
    answer = fields.Text(required=True)
    origin = fields.Selection(
        [("ai", "Translated by the AI"), ("manager", "Written by a manager")], default="manager", required=True)
    model_name = fields.Char(string="Translated By", readonly=True)
    reviewed = fields.Boolean(help="A manager read it. A translation by the AI is served before it is reviewed.")
    step_notes = fields.Text(
        string="Translated Step Notes",
        help="The notes of the steps of the explanation in this language, as JSON: the note, then its translation.")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            for key in ("question", "answer"):
                if vals.get(key):
                    vals[key] = scrub(vals[key])
        return super().create(vals_list)

    def write(self, vals):
        vals = dict(vals)
        for key in ("question", "answer"):
            if vals.get(key):
                vals[key] = scrub(vals[key])
        if "answer" in vals and "reviewed" not in vals:
            # Edited by a manager: read, and theirs now
            vals["reviewed"] = True
        return super().write(vals)


class NudgeMemorySave(models.TransientModel):
    """Save as reference: an answer of Nudge, checked and edited by a manager before it is kept."""
    _name = "nudge.memory.save"
    _description = "Nudge Save as Reference"

    message_ref = fields.Integer(readonly=True)
    question = fields.Text(required=True)
    variants = fields.Text(
        string="Other Wordings", help="Other ways people ask it, one per line: each finds this answer too.")
    answer = fields.Text(required=True, help="What people are answered. Change it as you like before saving.")
    language = fields.Char(size=8)
    mode = fields.Selection([("talk", "Talk"), ("agent", "Agent")], default="talk", required=True)
    model_name = fields.Char(string="About the Screen", readonly=True)
    view_type = fields.Char(readonly=True)
    menu_id = fields.Many2one("ir.ui.menu", string="Menu")
    screen_bound = fields.Boolean(string="Only on Its Screen")
    steps = fields.Json(readonly=True)
    step_lines = fields.Text(string="Steps on the Screen", compute="_compute_step_lines")
    group_ids = fields.Many2many(
        "res.groups", "nudge_memory_save_group_rel", "wizard_id", "group_id", string="For the Groups",
        help="Who may be answered with it. Empty: everybody who uses Nudge and may open its screen.")
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, required=True)
    warning = fields.Text(readonly=True)

    @api.depends("steps")
    def _compute_step_lines(self):
        for wizard in self:
            steps = wizard.steps if isinstance(wizard.steps, list) else []
            wizard.step_lines = "\n".join(
                f"{number}. {step.get('text') or ''}" for number, step in enumerate(steps, 1)
                if isinstance(step, dict)) or False

    def action_save(self):
        self.ensure_one()
        self.env["nudge.memory"]._save_reference({
            "message_ref": self.message_ref, "question": self.question, "variants": self.variants,
            "answer": self.answer, "language": self.language, "mode": self.mode, "model_name": self.model_name,
            "view_type": self.view_type, "menu_id": self.menu_id.id, "screen_bound": self.screen_bound,
            "steps": self.steps, "group_ids": self.group_ids.ids, "company_id": self.company_id.id,
        })
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {
                "type": "success",
                "message": _("Saved: the same question is answered from Nudge's memory from now on, "
                                      "with no AI."),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    nudge_memory_like_suggest = fields.Boolean(
        string="Suggest From Likes", config_parameter=LIKE_PARAM,
        help="An answer liked by enough different people becomes a Suggested reference, for a manager to "
             "approve with one click. Never served before that yes.")
    nudge_memory_like_count = fields.Integer(
        string="Likes Needed", config_parameter=LIKE_COUNT_PARAM, default=DEFAULT_LIKE_COUNT,
        help="How many different people must like an answer before it is suggested.")
    nudge_memory_skill_count = fields.Integer(
        string="Answers per Draft Skill", config_parameter=SKILL_COUNT_PARAM, default=DEFAULT_SKILL_COUNT,
        help="When this many approved answers are about one app or skill, they are put together as a draft "
             "skill for a manager to publish.")

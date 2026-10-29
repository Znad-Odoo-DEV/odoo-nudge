import collections
from unittest import mock

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge.llm import summary
from odoo.addons.nudge.models import nudge_embedding
from odoo.addons.nudge.models.nudge_conversation import DEVELOPER_PARAM
from odoo.addons.nudge.models.nudge_request_size import FALLBACK_LIMITS, SAMPLE_QUESTION
from odoo.addons.nudge.tools import skill_router

from .common import NudgeCase
from .scenarios.spec import DEFAULT_BUDGET
from .test_embedding import FakeEmbedder, with_fake


class AlikeEngine:
    """The local search model as it reads the skills of a business: every skill about as near to any question
    (multilingual-e5-small read "Show me my open sales orders." 0.4 of the way to vendor bills, reordering rules and
    customer invoices alike). It keeps the texts of the skills it is given to read."""
    floor, ceiling = 0.5, 0.9

    def __init__(self):
        self.passages = []

    def embed(self, texts, kind="passage"):
        if kind == "passage":
            self.passages.extend(texts)
        return [[0.6, 0.8] for _text in texts]


@tagged("post_install", "-at_install")
class TestRequestSize(NudgeCase, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.boss = new_test_user(
            cls.env, login="nudge_size_boss", groups="nudge.group_nudge_manager,nudge.group_nudge_agent,base.group_system")
        cls.env["ir.config_parameter"].sudo().set_param(DEVELOPER_PARAM, "yes")

    def measure(self, user):
        wizard = self.env["nudge.request.size"].with_user(user).create({})
        wizard.action_measure()
        return {line.mode: line for line in wizard.line_ids}

    def test_each_mode_gets_a_size_without_any_provider(self):
        adapter = self.use_adapter()
        before = {name: self.env[name].sudo().search_count([]) for name in (
            "nudge.conversation", "nudge.message", "nudge.run")}
        lines = self.measure(self.boss)
        self.assertEqual(set(lines), {"talk", "agent", "dev"})
        for mode, line in lines.items():
            self.assertIn(line.state, ("ok", "over"), f"{mode}: {line.note}")
            self.assertGreater(line.total, 1000)
            self.assertGreater(line.tool_count, 0)
            self.assertEqual(line.state == "over", line.total > line.limit)
        # The limits are the gate's own, and the fallback the screen carries says the same
        self.assertEqual({mode: lines[mode].limit for mode in lines}, dict(DEFAULT_BUDGET))
        self.assertEqual(FALLBACK_LIMITS, dict(DEFAULT_BUDGET))
        self.assertGreater(lines["agent"].tool_count, lines["talk"].tool_count)
        self.assertEqual(adapter.calls, [], "No request went to a provider")
        after = {name: self.env[name].sudo().search_count([]) for name in before}
        self.assertEqual(after, before, "Nothing is kept of the conversation built to measure")

    def talk_line(self, lang, question=None):
        """:return: the Talk line of the screen opened by the boss in ``lang``, for ``question`` or the screen's own"""
        self.boss.lang = lang
        wizard = self.env["nudge.request.size"].with_user(self.boss).with_context(lang=lang).create(
            {"question": question} if question else {})
        wizard.action_measure()
        line = {line.mode: line for line in wizard.line_ids}["talk"]
        self.assertEqual(line.state, "ok", f"{lang}, {wizard.question}: {line.total} tokens, limit {line.limit} ("
                         f"{line.system_tokens} instructions, {line.tools_tokens} tools, {line.turns_tokens} conversation)")
        return wizard.question, line

    def test_the_arabic_talk_request_is_within_the_limit_too(self):
        """R3-38, as on the capture database: every skill of Nudge with its Arabic title, and the local search model
        switched on. The Talk request of the sentence the screen measures (its own, in English) took 16,424 tokens
        on an Arabic screen against 16,280 on an English one, over the limit of 16,300: the router read the titles of
        the skills in the person's language, and the Arabic ones brought three more skills to the index; and the
        search model, which reads a business question as near every skill, brought vendor bills and reordering
        rules to a question about sales orders. A question in Arabic costs about what the English one does too."""
        self.use_adapter()
        self.env["res.lang"]._activate_lang("ar_001")
        self.env["ir.module.module"]._load_module_terms(["nudge"], ["ar_001"])
        Skill = self.env["nudge.skill"].with_user(self.boss)
        self.assertGreater(len(Skill._visible("talk")), skill_router.INDEX_MAX, "The index is cut to the question")
        replenishment = Skill.search([("key", "=", "inventory-replenishment")])
        self.assertNotEqual(replenishment.with_context(lang="ar_001").name, replenishment.name, "Titles in Arabic")
        engine = AlikeEngine()
        lines, read = {}, {}
        # A cache of the router of its own: the skills are read anew, by this model
        with mock.patch.object(self.env.registry, "_nudge_skill_router", collections.OrderedDict(), create=True), \
                mock.patch.object(type(Skill), "_search_engine", lambda self_: engine):
            for lang in ("en_US", "ar_001"):
                start = len(engine.passages)
                question, lines[lang] = self.talk_line(lang)
                self.assertEqual(question, SAMPLE_QUESTION)
                read[lang] = engine.passages[start:]
            _question, arabic = self.talk_line("ar_001", "أرني أوامر البيع المفتوحة الخاصة بي.")
        with mock.patch.object(type(Skill), "_search_engine", lambda self_: None):
            _question, without = self.talk_line("en_US")
        english, translated = lines["en_US"], lines["ar_001"]
        # The skills are read in English whoever asks: an Arabic screen gives the search model no text of its own
        self.assertTrue(read["en_US"])
        self.assertFalse(set(read["ar_001"]) - set(read["en_US"]), "The router read the titles in Arabic")
        # A model that finds every skill as near says nothing of the work: it brings no skill
        self.assertLessEqual(english.total, without.total)
        # What the Arabic request carries more is the names of the kinds of records in Arabic, and nothing else
        work = {lang: summary.estimate_tokens(self.env["nudge.conversation"].with_user(self.boss).with_context(
            lang=lang)._chat_open()._chat_where_the_work_is()) for lang in lines}
        self.assertEqual(translated.tools_tokens, english.tools_tokens)
        # a few tokens of margin: the Arabic names of each version's own menus differ by a token or two (18, 17)
        self.assertLessEqual(translated.total, english.total + max(0, work["ar_001"] - work["en_US"]) + 16)
        self.assertLess(arabic.total, english.total * 1.1)

    def test_the_screens_have_a_title_of_their_own(self):
        for name in ("nudge.request.size", "nudge.search.try"):
            record = self.env[name].with_user(self.boss).create({})
            self.assertTrue(record.display_name.startswith(("Request Size", "Try Search")), record.display_name)
            self.assertNotIn(name, record.display_name)

    def test_a_mode_closed_to_the_user_is_said_not_measured(self):
        Conversation = type(self.env["nudge.conversation"])
        with mock.patch.object(Conversation, "_can_use_agent", lambda self: False):
            lines = self.measure(self.manager)
        self.assertIn(lines["talk"].state, ("ok", "over"))
        self.assertEqual(lines["agent"].state, "skipped")
        self.assertEqual(lines["dev"].state, "skipped")

    def test_no_provider_is_said(self):
        self.env["nudge.provider"].search([]).active = False
        lines = self.measure(self.manager)
        self.assertEqual(lines["talk"].state, "error")
        self.assertIn("provider", lines["talk"].note)

    def test_only_a_manager_measures(self):
        wizard = self.env["nudge.request.size"].create({})
        with self.assertRaises(AccessError):
            wizard.with_user(self.user_a).action_measure()


@tagged("post_install", "-at_install")
class TestSearchTry(NudgeCase, TransactionCase):

    def setUp(self):
        super().setUp()
        nudge_embedding._cache.clear()
        self.addCleanup(nudge_embedding._cache.clear)

    def index_some(self):
        self.env.cr.execute("SELECT id FROM nudge_doc_passage WHERE body ILIKE '%reordering rule%' ORDER BY id LIMIT 10")
        ids = [row[0] for row in self.env.cr.fetchall()]
        self.env.cr.execute("SELECT id FROM nudge_doc_passage WHERE NOT (body ILIKE '%reordering%') ORDER BY id LIMIT 20")
        ids += [row[0] for row in self.env.cr.fetchall()]
        with with_fake(self):
            self.env["nudge.embedding"]._index_ids("nudge.doc.passage", ids)
        return ids

    def test_passages_found_by_meaning_with_source_and_score(self):
        ids = self.index_some()
        wizard = self.env["nudge.search.try"].with_user(self.manager).create({"query": "قواعد إعادة الطلب"})
        with with_fake(self):
            wizard.action_search()
        self.assertFalse(wizard.message)
        self.assertTrue(wizard.line_ids)
        for line in wizard.line_ids:
            self.assertTrue(line.source.startswith("Odoo documentation"))
            self.assertGreater(line.score, 0)
            self.assertEqual(line.res_model, "nudge.doc.passage")
            self.assertIn(line.res_id, ids)
            self.assertTrue(line.excerpt)
        scores = wizard.line_ids.mapped("score")
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_nothing_near_says_so(self):
        self.index_some()
        wizard = self.env["nudge.search.try"].with_user(self.manager).create({"query": "zzz qqq"})
        with mock.patch.object(type(self.env["nudge.embedding"]), "_semantic", lambda self, *args, **kw: {}):
            wizard.action_search()
        self.assertFalse(wizard.line_ids)
        self.assertTrue(wizard.message)

    def test_a_missing_model_is_said_with_how_to_start_it(self):
        with mock.patch.object(type(self.env["nudge.embedding"]), "_embedder", lambda self: None):
            wizard = self.env["nudge.search.try"].with_user(self.manager).create({"query": "reorder"})
            wizard.action_search()
            self.assertFalse(wizard.line_ids)
            self.assertIn("off", wizard.message.lower())
            self.assertFalse(wizard.model_ready)
            self.assertTrue(wizard.status)
            self.assertTrue(wizard.howto)

    def test_the_state_of_the_index(self):
        self.index_some()
        wizard = self.env["nudge.search.try"].with_user(self.manager).create({})
        self.assertGreater(wizard.doc_passages, 0)
        self.assertGreaterEqual(wizard.indexed, 30)
        self.assertTrue(wizard.last_indexed)

    def test_only_a_manager_searches(self):
        wizard = self.env["nudge.search.try"].create({"query": "reorder"})
        with self.assertRaises(AccessError):
            wizard.with_user(self.user_a).action_search()

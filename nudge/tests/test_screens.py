from unittest import mock

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge.models import nudge_embedding
from odoo.addons.nudge.models.nudge_conversation import DEVELOPER_PARAM

from .common import NudgeCase
from .test_embedding import FakeEmbedder, with_fake


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
        self.assertEqual((lines["talk"].limit, lines["agent"].limit, lines["dev"].limit), (17800, 24500, 26000))
        self.assertGreater(lines["agent"].tool_count, lines["talk"].tool_count)
        self.assertEqual(adapter.calls, [], "No request went to a provider")
        after = {name: self.env[name].sudo().search_count([]) for name in before}
        self.assertEqual(after, before, "Nothing is kept of the conversation built to measure")

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

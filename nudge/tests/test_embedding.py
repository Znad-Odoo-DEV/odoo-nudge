import hashlib
import os
import tempfile
from unittest import SkipTest, mock

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.llm import embedder, memory_match
from odoo.addons.nudge.models import nudge_doc, nudge_embedding
from odoo.addons.nudge.models.nudge_embedding import PATH_PARAM
from odoo.addons.nudge.tools.noai_commands import bridge

from .common import NudgeCase

# Words that mean the same thing for the fake model, one direction each
CONCEPTS = (
    ("reorder", "replenish", "إعادة الطلب"),
    ("annual leave", "holiday", "vacation"),
    ("invoice", "bill"),
)


class FakeEmbedder:
    """A deterministic stand-in for the model: a text pointing at a concept is
    that concept, any other text is a direction of its own."""

    name = "fake"
    floor = 0.5
    ceiling = 0.9
    dimension = len(CONCEPTS) + 16

    def embed(self, texts, kind="passage"):
        vectors = []
        for text in texts:
            lowered = (text or "").lower()
            vector = [0.0] * self.dimension
            for index, words in enumerate(CONCEPTS):
                if any(word in lowered for word in words):
                    vector[index] = 1.0
            if not any(vector):
                digest = hashlib.md5(lowered.encode()).digest()[0] % 16
                vector[len(CONCEPTS) + digest] = 1.0
            norm = sum(value * value for value in vector) ** 0.5
            vectors.append([value / norm for value in vector])
        return vectors


def with_fake(test):
    """The model is there, and it is the fake one."""
    return mock.patch.object(
        type(test.env["nudge.embedding"]), "_embedder", lambda self: FakeEmbedder())


@tagged("post_install", "-at_install")
class TestVectors(TransactionCase):

    def test_scale_and_cosines_without_numpy(self):
        self.assertEqual(embedder.scale(0.5, 0.5, 0.9), 0.0)
        self.assertEqual(embedder.scale(0.95, 0.5, 0.9), 1.0)
        self.assertAlmostEqual(embedder.scale(0.7, 0.5, 0.9), 0.5)
        rows = [(1, embedder.pack([1.0, 0.0])), (2, embedder.pack([0.0, 1.0])), (3, embedder.pack([1.0]))]
        with mock.patch.dict("sys.modules", {"numpy": None}):
            plain = embedder.cosines([1.0, 0.0], rows)
        self.assertEqual(plain, [(1, 1.0), (2, 0.0)], "The vector of another size is left out")
        try:
            import numpy  # noqa: F401, PLC0415
        except ImportError:
            return
        self.assertEqual(embedder.cosines([1.0, 0.0], rows), plain)

    def test_blend_adds_meaning_to_words_and_takes_nothing_from_them(self):
        self.assertEqual(memory_match.blend(0.9, 0.0), 0.9)
        self.assertEqual(memory_match.blend(0.0, 1.0), 1.0)
        self.assertGreater(memory_match.blend(0.6, 0.6), 0.7)
        rank = memory_match.rank("replenishment", [(1, "How do I reorder stock?")], semantic={1: 1.0})
        self.assertEqual([key for _score, key in rank], [1])
        self.assertEqual(memory_match.rank("replenishment", [(1, "How do I reorder stock?")]), [])


@tagged("post_install", "-at_install")
class TestWithoutTheModel(NudgeCase, TransactionCase):
    """Neither the packages nor the model: the feature is off and nothing changes."""

    def test_the_feature_is_off_and_the_searches_are_what_they_were(self):
        Embedding = self.env["nudge.embedding"]
        with tempfile.TemporaryDirectory() as empty:
            self.env["ir.config_parameter"].sudo().set_param(PATH_PARAM, empty)
            self.assertIsNone(Embedding._embedder())
            self.assertFalse(Embedding._is_ready())
            self.assertIsNone(Embedding._semantic("nudge.doc.passage", "reorder", ids=[1]))
            self.assertIsNone(Embedding._blend("nudge.doc.passage", "reorder", {1: 1.0}))
            self.assertEqual(Embedding._index_some(), 0)
            self.assertIn("Off", Embedding._status())
        found = self.env["nudge.doc.passage"]._search_text("reordering rules")
        self.assertEqual(found[0].page_id.category, "inventory_and_mrp")
        # The Arabic words are carried to English keywords (NP-12), so the reordering pages are found by them
        arabic = self.env["nudge.doc.passage"]._search_text("قواعد إعادة الطلب")
        self.assertTrue(arabic, "The Arabic question is mapped to English keywords")
        self.assertTrue(all("reorder" in passage.body.lower() or "replenish" in passage.body.lower()
                            for passage in arabic))
        # ("بنفسجي" is no longer unknown: the screens of Odoo call a colour "Purple" in Arabic)
        self.assertFalse(self.env["nudge.doc.passage"]._search_text("زنجبيل مقرمش"),
                         "No known word, no keyword in common: nothing is found without the model")

    def test_the_settings_say_what_to_install(self):
        Embedding = self.env["nudge.embedding"]
        with mock.patch.object(
                type(Embedding), "_dependencies", lambda self: (False, ["onnxruntime", "tokenizers"])):
            status = Embedding._status()
            self.assertIn("pip install onnxruntime tokenizers", status)
            with self.assertRaises(UserError) as error:
                Embedding.action_download()
            self.assertIn("onnxruntime", str(error.exception))
            settings = self.env["res.config.settings"].create({})
            self.assertIn("onnxruntime", settings.nudge_embedding_summary)

    def test_the_module_does_not_import_the_packages(self):
        # Neither the manifest nor the import of the module needs them
        manifest = self.env["ir.module.module"].get_module_info("nudge")
        self.assertFalse((manifest.get("external_dependencies") or {}).get("python"))


@tagged("post_install", "-at_install")
class TestSearchByMeaning(NudgeCase, TransactionCase):

    def setUp(self):
        super().setUp()
        self.Embedding = self.env["nudge.embedding"]
        self.Passage = self.env["nudge.doc.passage"]
        nudge_embedding._cache.clear()
        self.addCleanup(nudge_embedding._cache.clear)

    def index_docs(self):
        self.env.cr.execute("""
            SELECT id FROM nudge_doc_passage WHERE body ILIKE '%reordering rule%' ORDER BY id LIMIT 20
        """)
        ids = [row[0] for row in self.env.cr.fetchall()]
        self.env.cr.execute("SELECT id FROM nudge_doc_passage WHERE NOT (body ILIKE '%reordering%') ORDER BY id LIMIT 40")
        ids += [row[0] for row in self.env.cr.fetchall()]
        self.assertTrue(ids)
        with with_fake(self):
            self.assertEqual(self.Embedding._index_ids("nudge.doc.passage", ids), len(ids))
        return ids

    def test_indexing_is_resumable_and_forgets_what_is_gone(self):
        env = self.env(context=dict(self.env.context, nudge_keep_transaction=True))
        Embedding = env["nudge.embedding"]
        self.env["nudge.memory"].create({"question": "How do I reorder stock?", "answer": "Rules."})
        with with_fake(self):
            self.env.cr.execute("DELETE FROM nudge_embedding")
            pending = Embedding._pending("nudge.memory", 10)
            self.assertTrue(pending)
            # No time at all: nothing is done, nothing is lost
            self.assertEqual(Embedding._index_some(budget=0), 0)
            made = Embedding._index_ids("nudge.memory", pending)
            self.assertEqual(made, len(pending))
            self.assertFalse(Embedding._pending("nudge.memory", 10), "What has a vector is not done twice")
            indexed, todo = Embedding._counts()
            self.assertGreaterEqual(indexed, made)
            self.assertGreater(todo, 0, "The documentation is still to do")
            memory = self.env["nudge.memory"].browse(pending[0])
            memory.question = "How do I invoice?"
            self.assertEqual(Embedding._pending("nudge.memory", 10), [memory.id],
                             "A question that changed is embedded again")
            memory.unlink()
            Embedding._drop_stale()
            self.env.cr.execute("SELECT count(*) FROM nudge_embedding WHERE res_model = 'nudge.memory' AND res_id = %s",
                                (memory.id,))
            self.assertEqual(self.env.cr.fetchone()[0], 0)
            # The cron goes on from there, a batch at a time
            self.assertGreater(Embedding._index_some(budget=0.5), 0)

    def test_documentation_is_found_by_meaning_without_a_keyword_in_common(self):
        self.index_docs()
        with with_fake(self):
            found = self.Passage._search_text("قواعد إعادة الطلب")
            self.assertTrue(found, "An Arabic question finds English pages")
            self.assertTrue(all("reordering" in passage.body.lower() or "replenish" in passage.body.lower()
                                for passage in found))
            # The app limits the pages found by meaning too
            # (the sales folder has a few pages that do say replenishment: those may come, none of inventory)
            in_sales = self.Passage._search_text("قواعد إعادة الطلب", app="sales")
            self.assertTrue(all(passage.page_id.category == "sales" for passage in in_sales))
            self.assertFalse(in_sales & found.filtered(lambda p: p.page_id.category == "inventory_and_mrp"))
            result = tools.base.call(
                self.env(user=self.user_a), "search_odoo_documentation", {"query": "replenishment"})
            self.assertFalse(result.is_error, result.content)
            self.assertIn("Source: https://www.odoo.com/documentation", result.content)

    def test_keyword_hits_are_reranked_by_meaning(self):
        ids = self.index_docs()
        with with_fake(self):
            found = self.Passage._search_text("reordering rules", limit=5)
        self.assertTrue(found)
        self.assertTrue(set(found.ids) <= set(ids) or found[0].id in ids)

    def test_a_model_that_fails_never_breaks_a_search(self):
        class Broken(FakeEmbedder):
            def embed(self, texts, kind="passage"):
                raise RuntimeError("model crashed")

        with mock.patch.object(type(self.Embedding), "_embedder", lambda self: Broken()):
            found = self.Passage._search_text("reordering rules")
        self.assertEqual(found[0].page_id.category, "inventory_and_mrp")

    def test_private_mode_does_not_stop_it(self):
        self.index_docs()
        self.env.company.nudge_private_mode = True
        self.assertTrue(self.env["nudge.provider"]._is_private_mode())
        with with_fake(self):
            self.assertTrue(self.Passage._search_text("قواعد إعادة الطلب"))

    def test_company_files_by_meaning_for_those_who_may_read_them(self):
        Source = self.env["nudge.source"]
        text = b"HR Policy\n\nAnnual leave is thirty days for every employee after a year of service.\n"
        open_file = Source.with_user(self.manager).create({
            "name": "Open policy", "file": __import__("base64").b64encode(text), "file_name": "open.txt"})
        hidden = Source.with_user(self.manager).create({
            "name": "Hidden policy", "file_name": "hidden.txt",
            "file": __import__("base64").b64encode(text.replace(b"HR", b"CEO")),
            "group_ids": [(6, 0, [self.env.ref("nudge.group_nudge_manager").id])]})
        passages = open_file.passage_ids | hidden.passage_ids
        with with_fake(self):
            self.Embedding._index_ids("nudge.source.passage", passages.ids)
            query = "how many days of holiday do I get"
            found = self.env["nudge.source.passage"].with_user(self.user_a)._search_text(query)
            self.assertEqual(found.source_id, open_file, "Found with no word in common, and only what may be read")
            seen_by_manager = self.env["nudge.source.passage"].with_user(self.manager)._search_text(query)
            self.assertEqual(seen_by_manager.source_id, open_file | hidden)

    def test_remembered_answers_blend_meaning_with_words(self):
        self.env["ir.config_parameter"].sudo().set_param("nudge.answer_memory", "True")
        Memory = self.env["nudge.memory"]
        memory = Memory.create({"question": "How do I reorder stock automatically?", "answer": "Use the rules."})
        Memory.create({"question": "How do I print an invoice?", "answer": "Press Print."})
        words_only, _score = Memory.with_user(self.user_a)._find("replenishment of products")
        self.assertIsNone(words_only, "No word in common")
        with with_fake(self):
            self.Embedding._index_ids("nudge.memory", Memory.search([]).ids)
            found, score = Memory.with_user(self.user_a)._find("replenishment of products")
            self.assertEqual(found, memory)
            self.assertGreater(score, 0.6)
            same, _score = Memory.with_user(self.user_a)._find("How do I reorder stock automatically?")
            self.assertEqual(same, memory)
            other, _score = Memory.with_user(self.user_a)._find("weather in Paris today")
            self.assertIsNone(other)


@tagged("post_install", "-at_install")
class TestArabicSearch(NudgeCase, TransactionCase):
    """An Arabic question finds the pages of its English equivalent: the terms of the screens, as Odoo
    translates them in the ar.po file of each installed module, carry it to English words, for the
    keywords and for the meaning."""

    PO = 'msgid "Payment Terms"\nmsgstr "شروط الدفع"\n\nmsgid "Invoice"\nmsgstr "الفاتورة"\n'
    QUESTION = "كيف أحدد شروط الدفع في الفاتورة"

    def setUp(self):
        super().setUp()
        self.Passage = self.env["nudge.doc.passage"]
        self.Embedding = self.env["nudge.embedding"]
        for cache in (nudge_doc._screen_terms_cache, nudge_embedding._cache):
            cache.clear()
            self.addCleanup(cache.clear)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.po = os.path.join(folder.name, "ar.po")
        with open(self.po, "w", encoding="utf-8") as po:
            po.write(self.PO)

    def files(self, *paths):
        """The installed modules ship these ar.po files"""
        return mock.patch.object(
            type(self.env["ir.module.module"]), "_nudge_arabic_po_files", lambda self: paths)

    def test_the_terms_are_read_once_for_a_set_of_modules(self):
        with mock.patch.object(bridge, "screen_terms", wraps=bridge.screen_terms) as read:
            with self.files(self.po):
                terms = self.Passage._arabic_terms()
                self.assertIs(self.Passage._arabic_terms(), terms)
                self.assertEqual(read.call_count, 1)
                self.assertEqual(self.Passage._english_words(self.QUESTION), ["set", "payment", "terms", "invoice"])
                self.assertEqual(self.Passage._english_words("payment terms"), [], "An English question is left as is")
            with self.files(self.po, self.po + ".missing"):
                self.Passage._arabic_terms()
                self.assertEqual(read.call_count, 2, "A module installed or removed: the files are read again")

    def test_an_arabic_question_finds_the_english_page_without_the_model(self):
        with self.files(self.po), mock.patch.object(type(self.Embedding), "_embedder", lambda self: None):
            found = self.Passage._search_text(self.QUESTION)
        self.assertTrue(found)
        self.assertIn("payment terms", f"{found[0].title} {found[0].heading or ''}".lower())

    def test_the_meaning_is_that_of_the_english_words_too(self):
        self.env.cr.execute("SELECT id FROM nudge_doc_passage WHERE body ILIKE '%invoice%' ORDER BY id LIMIT 5")
        invoices = [row[0] for row in self.env.cr.fetchall()]
        with with_fake(self):
            self.Embedding._index_ids("nudge.doc.passage", invoices)
            self.assertFalse(self.Embedding._semantic("nudge.doc.passage", "شروط الدفع", ids=invoices),
                             "The fake model, as the small one, does not see the English in the Arabic")
            near = self.Embedding._semantic(
                "nudge.doc.passage", "شروط الدفع", ids=invoices, also=["payment terms invoice"])
            self.assertEqual(set(near), set(invoices))
            wizard = self.env["nudge.search.try"].with_user(self.manager).create({"query": self.QUESTION})
            with self.files(self.po):
                wizard.action_search()
        self.assertTrue(set(invoices) & set(wizard.line_ids.mapped("res_id")),
                        "Try Search by Meaning searches as the answers do")

    def test_odoo_says_its_own_terms_in_arabic(self):
        if not self.env["ir.module.module"].search_count([("name", "=", "account"), ("state", "=", "installed")]):
            raise SkipTest("Invoicing is not installed: its ar.po is not there to be read")
        files = self.env["ir.module.module"]._nudge_arabic_po_files()
        self.assertTrue(any(path.replace("\\", "/").endswith("/account/i18n/ar.po") for path in files))
        # 18 and 17 also translate the singular "Payment Term": the English words hold at least these two
        words = self.Passage._english_words("شروط الدفع")
        self.assertEqual(words[:2], ["payment", "terms"])
        self.assertLessEqual(set(words), {"payment", "terms", "term"})


@tagged("post_install", "-at_install")
class TestVectorCache(NudgeCase, TransactionCase):
    """The vectors are held in memory by the worker, one set per database and version."""

    def setUp(self):
        super().setUp()
        self.Embedding = self.env["nudge.embedding"]
        nudge_embedding._cache.clear()
        self.addCleanup(nudge_embedding._cache.clear)
        self.env.cr.execute(
            "SELECT id FROM nudge_doc_passage WHERE body ILIKE '%reordering rule%' ORDER BY id LIMIT 5")
        self.ids = [row[0] for row in self.env.cr.fetchall()]

    def test_filled_once_then_dropped_when_the_index_changes(self):
        with with_fake(self):
            self.Embedding._index_ids("nudge.doc.passage", self.ids)
            version = self.Embedding._version()
            found = self.Embedding._semantic("nudge.doc.passage", "reorder", ids=self.ids)
            self.assertEqual(set(found), set(self.ids))
            db = self.env.cr.dbname
            (key,) = [k for k in nudge_embedding._cache if k[0] == db]
            self.assertEqual(key, (db, self.Embedding._model_name(), version))
            keys, matrix = nudge_embedding._cache[key]["nudge.doc.passage"]
            self.assertEqual(sorted(keys), sorted(self.ids))
            # A second question is answered from memory: the table is not read again
            self.env.cr.execute("DELETE FROM nudge_embedding")
            again = self.Embedding._semantic("nudge.doc.passage", "reorder", ids=self.ids)
            self.assertEqual(set(again), set(self.ids), "Served from the memory of the worker")
            # A change of the index is a new version: the old set is dropped, the table is read
            self.Embedding._bump()
            self.assertNotEqual(self.Embedding._version(), version)
            self.assertEqual(self.Embedding._semantic("nudge.doc.passage", "reorder", ids=self.ids), {})
            self.assertEqual(len([k for k in nudge_embedding._cache if k[0] == db]), 1)
            self.Embedding._index_ids("nudge.doc.passage", self.ids[:2])
            self.Embedding._bump()  # an indexing pass ends with one change of version
            self.assertEqual(
                set(self.Embedding._semantic("nudge.doc.passage", "reorder", ids=self.ids)), set(self.ids[:2]))

    def test_unlinking_a_passage_or_reindexing_is_a_new_version(self):
        with with_fake(self):
            self.Embedding._index_ids("nudge.doc.passage", self.ids)
            before = self.Embedding._version()
            self.env["nudge.doc.passage"].sudo().browse(self.ids[0]).unlink()
            after_unlink = self.Embedding._version()
            self.assertNotEqual(before, after_unlink)
            with mock.patch.object(type(self.Embedding), "_dependencies", lambda self: (True, [])), \
                    mock.patch.object(type(self.Embedding), "_start", lambda self: None):
                self.as_admin().action_reindex()
            self.assertNotEqual(self.Embedding._version(), after_unlink)

    def as_admin(self):
        return self.Embedding.with_user(self.env.ref("base.user_admin"))

    def test_never_across_databases_and_without_numpy(self):
        with with_fake(self):
            self.Embedding._index_ids("nudge.doc.passage", self.ids)
            nudge_embedding._cache[("other_db", "x", "1")] = {"nudge.doc.passage": ([], [])}
            self.Embedding._semantic("nudge.doc.passage", "reorder", ids=self.ids)
            self.assertIn(("other_db", "x", "1"), nudge_embedding._cache, "Another database is left alone")
            nudge_embedding._cache.clear()
            with mock.patch.dict("sys.modules", {"numpy": None}):
                found = self.Embedding._semantic("nudge.doc.passage", "reorder", ids=self.ids)
                keys, rows = nudge_embedding._cache[
                    (self.env.cr.dbname, self.Embedding._model_name(), self.Embedding._version())][
                    "nudge.doc.passage"]
            self.assertEqual(set(found), set(self.ids))
            self.assertIsInstance(rows, list, "A list when numpy is not there")


@tagged("post_install", "-at_install")
class TestSettingsActions(NudgeCase, TransactionCase):

    def setUp(self):
        super().setUp()
        self.Embedding = self.env["nudge.embedding"]
        nudge_embedding._cache.clear()
        self.addCleanup(nudge_embedding._cache.clear)
        self.folder = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.folder, True)
        self.env["ir.config_parameter"].sudo().set_param(PATH_PARAM, self.folder)
        self.model = b"model bytes" * 10
        self.tokenizer = b'{"tokenizer": true}'
        self.files = [
            {"name": "model.onnx", "url": "https://example.test/model", "size": len(self.model),
             "sha256": hashlib.sha256(self.model).hexdigest()},
            {"name": "tokenizer.json", "url": "https://example.test/tokenizer", "size": len(self.tokenizer),
             "sha256": hashlib.sha256(self.tokenizer).hexdigest()},
        ]
        spec = dict(embedder.MODELS[embedder.DEFAULT_MODEL], files=self.files)
        patcher = mock.patch.object(embedder, "MODELS", {embedder.DEFAULT_MODEL: spec})
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(
            type(self.Embedding), "_dependencies", lambda self: (True, []))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.corrupt = False
        self.fetched = []

    def fetch(self, url, dest, offset=0, timeout=30):
        self.fetched.append(url)
        data = self.model if url.endswith("model") else self.tokenizer
        if self.corrupt:
            data = data[::-1]
        with open(dest, "ab" if offset else "wb") as out:
            out.write(data[offset:])
        yield len(data)

    def as_admin(self):
        return self.Embedding.with_user(self.env.ref("base.user_admin"))

    def test_only_an_administrator_manages_it(self):
        for method in ("action_download", "action_remove", "action_reindex"):
            with self.assertRaises(AccessError):
                getattr(self.Embedding.with_user(self.manager), method)()

    def test_download_checks_the_files_and_the_cron_indexes(self):
        env = self.env(context=dict(self.env.context, nudge_keep_transaction=True))
        Embedding = env["nudge.embedding"]
        with mock.patch.object(embedder, "fetch", self.fetch):
            action = self.as_admin().action_download()
            self.assertEqual(action["tag"], "display_notification")
            self.assertEqual(Embedding._download_state()["state"], "running")
            self.assertIn("Downloading", Embedding._status())
            with with_fake(self):
                Embedding._cron_work()
        self.assertEqual(sorted(os.listdir(self.folder)), ["model.onnx", "tokenizer.json"])
        self.assertFalse(Embedding._download_state(), "The state is cleared when it is done")
        self.assertEqual(len(self.fetched), 2)
        with with_fake(self):
            self.assertIn("On:", Embedding._status())
            self.assertGreater(Embedding._counts()[0], 0, "The cron went on to index")

    def test_a_file_that_is_not_the_expected_one_is_removed(self):
        env = self.env(context=dict(self.env.context, nudge_keep_transaction=True))
        Embedding = env["nudge.embedding"]
        self.corrupt = True
        with mock.patch.object(embedder, "fetch", self.fetch):
            self.as_admin().action_download()
            Embedding._cron_work()
        self.assertEqual(os.listdir(self.folder), [])
        state = Embedding._download_state()
        self.assertEqual(state["state"], "error")
        self.assertIn("checksum", state["error"])
        self.assertIn("The download failed", Embedding._status())

    def test_a_half_made_download_is_resumed(self):
        part = os.path.join(self.folder, "model.onnx.part")
        with open(part, "wb") as stream:
            stream.write(self.model[:20])
        calls = []

        def fetch(url, dest, offset=0, timeout=30):
            calls.append((url, offset))
            yield from self.fetch(url, dest, offset)

        with mock.patch.object(embedder, "fetch", fetch):
            self.assertTrue(embedder.download(self.files, self.folder))
        self.assertEqual(calls[0][1], 20, "It goes on from where it stopped")

    def test_how_far_the_download_is_is_read_on_the_disk(self):
        # Review odoo-tech #11: the progress was a parameter written every two seconds, and each
        # write emptied the caches of every worker for as long as the download lasted
        self.as_admin().action_download()
        with open(os.path.join(self.folder, "model.onnx.part"), "wb") as stream:
            stream.write(self.model[:55])
        total = len(self.model) + len(self.tokenizer)
        self.assertIn(f"{int(100 * 55 / total)}%", self.Embedding._status())
        self.assertNotIn("done", self.Embedding._download_state())

    def test_remove_and_reindex(self):
        for name, data in (("model.onnx", self.model), ("tokenizer.json", self.tokenizer)):
            with open(os.path.join(self.folder, name), "wb") as stream:
                stream.write(data)
        self.env["nudge.memory"].create({"question": "How do I reorder stock?", "answer": "Rules."})
        keep = self.env(context=dict(self.env.context, nudge_keep_transaction=True))["nudge.embedding"]
        with with_fake(self):
            keep._index_some(budget=0.3)
            self.env.cr.execute("SELECT count(*) FROM nudge_embedding")
            self.assertGreater(self.env.cr.fetchone()[0], 0)
            self.as_admin().action_reindex()
            self.env.cr.execute("SELECT count(*) FROM nudge_embedding")
            self.assertEqual(self.env.cr.fetchone()[0], 0, "Re-index forgets the vectors, the cron makes them again")
        settings = self.env["res.config.settings"].with_user(self.env.ref("base.user_admin")).create({})
        settings.action_nudge_embedding_remove()
        self.assertEqual(os.listdir(self.folder), [], "Only the files of the model go")
        self.env.cr.execute("SELECT count(*) FROM nudge_embedding")
        self.assertEqual(self.env.cr.fetchone()[0], 0)
        with self.assertRaises(UserError):
            self.as_admin().action_reindex()


@tagged("post_install", "-at_install")
class TestTheRealModel(TransactionCase):
    """Only where onnxruntime, tokenizers and the model are really there."""

    def test_the_real_model_embeds_and_ranks(self):
        Embedding = self.env["nudge.embedding"]
        engine = Embedding._embedder()
        if engine is None:
            raise SkipTest("onnxruntime, tokenizers or the downloaded model is missing")
        documents = engine.embed([
            "Reordering rules create purchase orders when the stock falls below the minimum.",
            "Payment terms set the due date of an invoice."])
        query = engine.embed(["how to replenish stock automatically"], kind="query")[0]
        scores = [score for _key, score in embedder.cosines(
            query, [(index, embedder.pack(vector)) for index, vector in enumerate(documents)])]
        self.assertGreater(scores[0], scores[1])

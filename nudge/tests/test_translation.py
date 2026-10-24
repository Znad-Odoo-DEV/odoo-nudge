import io
import json
import re

import polib

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge.llm import base
from odoo.addons.nudge.llm.fake_adapter import FakeAdapter
from odoo.addons.nudge.tools import translation as helper

from .common import NudgeCase

SOURCE = "Nudge Memory"
TRANSLATED = "Gespeicherte Antworten"


class TranslatingAdapter(FakeAdapter):
    """Translates every text it is given by putting "DE " before it, and drops what must be kept
    from the texts listed in ``broken``"""

    def __init__(self, broken=(), **kwargs):
        super().__init__(**kwargs)
        self.broken = set(broken)
        self.questions = []

    def stream_chat(self, system, turns, deadline=None, tools=None, tool_choice=None):
        text = turns[0]["text"]
        # oneshot.ask hands the system prompt over as a list of texts
        self.questions.append({"system": " ".join(base.system_texts(system)), "text": text})
        rows = json.loads(re.match(r'<data source="module_translation">(.*)</data>$', text, re.S)
                          .group(1).replace("&lt;", "<"))
        answers = {}
        for row in rows:
            if row["text"] == SOURCE:
                answers[str(row["id"])] = TRANSLATED
            elif row["text"] in self.broken:
                answers[str(row["id"])] = "DE kaputt"
            else:
                answers[str(row["id"])] = "DE " + row["text"]
        yield base.text_event(json.dumps(answers, ensure_ascii=False))
        yield base.stop_event(base.STOP_END, usage={"input_tokens": 40, "output_tokens": 30}, model="fake-model")


@tagged("post_install", "-at_install", "nudge")
class TestPlaceholders(TransactionCase):

    def test_tokens_kept(self):
        self.assertEqual(helper.problems("Hello %s, you have %(n)d items", "Hallo %s, %(n)d Artikel"), [])
        self.assertEqual(helper.problems("Open {name}", "Öffne {name}"), [])
        self.assertEqual(helper.problems("Click <b>here</b>", "Klicke <b>hier</b>"), [])

    def test_tokens_lost_or_added(self):
        self.assertEqual(helper.problems("Hello %s", "Hallo")[0][0], "missing")
        self.assertEqual(helper.problems("Hello", "Hallo %s")[0][0], "extra")
        self.assertEqual(helper.problems("Open {name}", "Öffne {nom}")[0][0], "missing")
        self.assertEqual(helper.problems("Click <b>here</b>", "Klicke hier")[0][0], "missing")
        self.assertEqual(helper.problems("Hello %s", "")[0][0], "empty")

    def test_an_address_or_a_handler_changed_by_the_translation_is_refused(self):
        source = 'Visit <a href="https://shop.example">our shop</a>'
        self.assertEqual(helper.problems(source, 'Besuche <a href="https://shop.example">unseren Laden</a>'), [])
        for bad in ('Besuche <a href="javascript:alert(1)">Laden</a>',
                    'Besuche <a href="https://shop.example" onmouseover="x()">Laden</a>',
                    'Besuche <a href="https://evil.example">Laden</a>'):
            with self.subTest(bad=bad):
                self.assertTrue(helper.problems(source, bad))

    def test_glossary(self):
        terms = helper.glossary_terms("Raghad Trading\nEbrahim Cars", "nudge")
        self.assertIn("Odoo", terms)
        self.assertIn("Raghad Trading", terms)
        self.assertEqual(helper.problems("Sell with Odoo", "Verkaufe mit Odoo", terms), [])
        self.assertEqual(helper.problems("Sell with Odoo", "Verkaufe mit OpenERP", terms)[0][0], "glossary")

    def test_batches(self):
        items = [(index, "word %s" % index) for index in range(130)]
        cut = helper.batches(items)
        self.assertEqual([len(batch) for batch in cut], [60, 60, 10])
        self.assertEqual(sum(batch and 1 for batch in helper.batches([(1, "x" * 10000), (2, "y" * 10000)])), 2)


@tagged("post_install", "-at_install", "nudge")
class TestModuleTranslation(NudgeCase, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = new_test_user(cls.env, login="nudge_admin", groups="base.group_system,nudge.group_nudge_manager")
        cls.german = cls.env["res.lang"].with_context(active_test=False).search([("code", "=", "de_DE")])
        cls.module = cls.env["ir.module.module"].search([("name", "=", "nudge")])
        cls.wizard = cls.env["nudge.translation"].with_user(cls.admin).create({
            "module_id": cls.module.id, "lang_id": cls.german.id})
        cls.wizard.action_list()

    def adapter(self, **kwargs):
        adapter = TranslatingAdapter(**kwargs)
        self.patch(type(self.env["nudge.provider"]), "_get_adapter", lambda provider: adapter)
        return adapter

    def keep_only(self, *sources):
        """The wizard with only the lines of these texts, to keep the provider's work short"""
        self.wizard.line_ids.filtered(lambda line: line.source not in sources).unlink()
        return self.wizard.line_ids

    def test_terms_listed(self):
        sources = self.wizard.line_ids.mapped("source")
        self.assertIn(SOURCE, sources)
        line = self.wizard.line_ids.filtered(lambda other: other.source == SOURCE)[:1]
        self.assertEqual(line.kind, "db")
        self.assertEqual(line.state, "todo")
        self.assertEqual(self.wizard.state, "listed")
        self.assertIn("tokens", self.wizard.estimate_text)

    def test_only_administrators(self):
        with self.assertRaises(AccessError):
            self.env["nudge.translation"].with_user(self.user_a).create({
                "module_id": self.module.id, "lang_id": self.german.id}).action_list()

    def test_placeholder_mismatch_rejected(self):
        Line = self.env["nudge.translation.line"]
        broken = "Hello %s from {name}"
        wizard = self.env["nudge.translation"].with_user(self.admin).create({
            "module_id": self.module.id, "lang_id": self.german.id, "state": "listed"})
        Line.create([{"wizard_id": wizard.id, "source": text} for text in (broken, "Plain text")])
        adapter = self.adapter(broken=[broken])
        wizard._translate_piece()
        by_source = {line.source: line for line in wizard.line_ids}
        self.assertEqual(by_source[broken].state, "rejected")
        self.assertIn("%s", by_source[broken].issue)
        self.assertEqual(by_source["Plain text"].state, "proposed")
        self.assertEqual(by_source["Plain text"].translation, "DE Plain text")
        # Frame and glossary reach the provider; the call is written in the ledger
        question = adapter.questions[0]
        self.assertIn("<data", question["text"])
        self.assertIn("Odoo", question["system"])
        self.assertTrue(self.env["nudge.usage"].search_count([("kind", "=", "translation")]))
        # Accepting the rejected one is refused; editing it right makes it ready
        with self.assertRaises(UserError):
            by_source[broken].action_accept()
        by_source[broken].translation = "Hallo %s von {name}"
        self.assertEqual(by_source[broken].state, "proposed")
        by_source[broken].action_accept()
        self.assertEqual(by_source[broken].state, "accepted")
        wizard.action_accept_all()
        self.assertEqual(by_source["Plain text"].state, "accepted")

    def test_apply_writes_the_translation(self):
        lines = self.keep_only(SOURCE)
        self.adapter()
        self.wizard._translate_piece()
        self.assertEqual(lines[:1].translation, TRANSLATED)
        self.wizard.action_accept_all()
        self.wizard.action_apply()
        menu = self.env.ref("nudge.nudge_menu_memory").with_context(lang="de_DE")
        self.assertEqual(menu.name, TRANSLATED)
        self.assertEqual(self.env.ref("nudge.nudge_menu_memory").with_context(lang="en_US").name, SOURCE)
        self.assertTrue(self.german.active)
        self.assertEqual(set(lines.mapped("state")), {"applied"})
        # Listed again, the term is no longer one without a translation
        again = self.env["nudge.translation"].with_user(self.admin).create({
            "module_id": self.module.id, "lang_id": self.german.id})
        again.action_list()
        self.assertNotIn(SOURCE, again.line_ids.mapped("source"))
        self.assertTrue(again.line_ids)

    def test_po_download_is_valid(self):
        lines = self.keep_only(SOURCE)
        self.adapter()
        self.wizard._translate_piece()
        self.wizard.action_accept_all()
        action = self.wizard.action_download()
        match = re.search(r"/web/content/(\d+)", action["url"])
        attachment = self.env["ir.attachment"].browse(int(match.group(1)))
        po = polib.pofile(attachment.raw.decode())
        self.assertEqual(po.metadata["Language"], "de_DE")
        entries = {entry.msgid: entry.msgstr for entry in po}
        self.assertEqual(entries[SOURCE], TRANSLATED)
        self.assertIn("module: nudge", [entry.comment.split("\n")[0] for entry in po if entry.msgid == SOURCE][0])
        # Odoo's own reader takes it
        from odoo.tools.translate import PoFileReader  # noqa: PLC0415
        buffer = io.BytesIO(attachment.raw)
        buffer.name = ""
        rows = list(PoFileReader(buffer))
        self.assertTrue(any(row["src"] == SOURCE and row["value"] == TRANSLATED for row in rows))
        self.assertTrue(lines)

    def test_private_mode_refuses(self):
        self.keep_only(SOURCE)
        adapter = self.adapter()
        self.env.company.nudge_private_mode = True
        with self.assertRaises(UserError) as caught:
            self.wizard.action_translate()
        self.assertIn("Private mode is on", str(caught.exception))
        self.assertFalse(adapter.questions)

    def work_jobs(self):
        """The scheduled action takes up the queued work (a test is one transaction it undoes)"""
        self.patch(type(self.env["ir.cron"]), "_commit_progress", lambda cron, *args, **kwargs: 10 ** 6)
        self.env["nudge.run"].with_context(nudge_keep_transaction=True)._cron_work()

    def test_the_button_queues_a_background_run_that_commits_batch_by_batch_and_can_be_stopped(self):
        """Release-2 review B3/S1: no provider batch inside the request"""
        self.keep_only(SOURCE)
        adapter = self.adapter()
        self.wizard.action_translate()
        self.assertFalse(adapter.questions)
        run = self.env["nudge.run"].search([("job", "=", "translate")])
        self.assertEqual((run.state, run.is_background, run.user_id), ("queued", True, self.admin))
        # a second press does not queue a second run
        self.wizard.action_translate()
        self.assertEqual(self.env["nudge.run"].search_count([("job", "=", "translate")]), 1)
        self.work_jobs()
        self.assertEqual(run.state, "done")
        self.assertTrue(adapter.questions)
        self.assertEqual(self.wizard.line_ids[:1].state, "proposed")
        # stopped before its turn: nothing is asked
        questions = len(adapter.questions)
        self.wizard.line_ids.write({"state": "todo", "translation": False})
        self.wizard.action_translate()
        again = self.env["nudge.run"].search([("job", "=", "translate"), ("state", "=", "queued")])
        again.action_stop()
        self.work_jobs()
        self.assertEqual((again.state, len(adapter.questions)), ("stopped", questions))

    def test_budget_stops_the_work(self):
        self.keep_only(SOURCE)
        adapter = self.adapter()
        self.env["ir.config_parameter"].sudo().set_param("nudge.budget_user_tokens", "10")
        self.env["nudge.usage"]._add(
            "chat", {"input_tokens": 50, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0},
            0.0, self.admin, self.env.company)
        self.wizard._translate_piece()
        self.assertFalse(adapter.questions)
        self.assertIn("tokens", self.wizard.note)
        self.assertEqual(self.wizard.line_ids[:1].state, "todo")

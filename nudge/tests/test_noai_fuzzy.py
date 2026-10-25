import random

from odoo.tests import BaseCase, tagged

from odoo.addons.nudge.llm import arabic
from odoo.addons.nudge.tools.noai_commands import fuzzy, grammar, lexicon, normalise

from .noai_cases import CASES

SCREEN = {"model": "sale.order", "record_id": 5}


def understood(parsed):
    """What the sentence asks, as the module acts on it: nothing under the threshold"""
    return parsed.intent if parsed.confidence >= grammar.THRESHOLD else ""


@tagged("post_install", "-at_install")
class TestNoaiFuzzy(BaseCase):
    """Slips and Arabic in Latin letters, read by rules. No database, no AI."""

    # --- Arabic made comparable ---------------------------------------------------------------

    def test_arabic_forms_meet(self):
        same = (
            ("أحمد", "احمد", "إحمد", "آحمد"),            # alef and hamza forms
            ("مدرسة", "مدرسه"),                           # taa marbuta
            ("على", "علي", "عَلى"),                       # alef maqsura, yaa
            ("فاتورة", "فَاتُورَة", "فاتـــورة"),          # diacritics, tatweel
            ("مؤسسة", "موسسه"),                           # hamza on waw
        )
        for forms in same:
            with self.subTest(forms=forms):
                self.assertEqual(len({normalise.fold(form) for form in forms}), 1, forms)

    def test_a_word_is_found_in_any_spelling(self):
        for text in ("اعرض الفاتورة", "اعرض الفاتوره", "اعرض الفَاتُورَة", "اعرض الفـــاتورة"):
            with self.subTest(text=text):
                parsed = grammar.parse(text, SCREEN)
                self.assertEqual((understood(parsed), parsed.kind), ("open", "invoice"), parsed)
                self.assertFalse(parsed.corrections, "a spelling variant is folded, not corrected")

    # --- Arabizi ------------------------------------------------------------------------------

    def test_arabizi_digits_and_pairs_become_arabic_letters(self):
        reads = {
            "3omala2": "عمل",      # 3 = ayn, 2 = hamza
            "7obb": "حب",
            "5alas": "خلس",
            "sheikh": "شخ",
            "6alab": "طلب",
            "9aba7": "قبح",
            "8arib": "غرب",
        }
        for word, skeleton in reads.items():
            with self.subTest(word=word):
                self.assertIn(arabic.skeleton(skeleton), arabic.arabizi_skeletons(word))

    def test_the_spelling_that_writes_the_long_vowels_is_the_nearer_word(self):
        near = arabic.arabizi_cost("3omala2", "عملاء")
        far = arabic.arabizi_cost("3omala2", "عميل")
        self.assertLess(near, far)
        self.assertIsNone(arabic.arabizi_cost("3omala2", "كتاب"))
        self.assertIsNone(arabic.arabizi_cost("hello world", "عملاء"))

    def test_only_arabic_spelling_carries_the_mark(self):
        for word in ("3ayez", "a7mad", "khalas", "shokran", "gh"):
            self.assertTrue(arabic.has_arabizi_mark(word), word)
        for word in ("invoice", "the", "chart", "that", "open"):
            self.assertFalse(arabic.has_arabizi_mark(word), word)

    def test_arabizi_is_read_as_the_arabic_word(self):
        for text, intent, kind in (
                ("a3rad el 3omala2", "open", "contact"),
                ("7otli el mntgat", "open", "product"),
                ("ftah el 3omala2", "open", "contact"),
                ("wareeni el mabee3at", "open", "sales")):
            with self.subTest(text=text):
                parsed = grammar.parse(text, SCREEN)
                self.assertEqual((understood(parsed), parsed.kind), (intent, kind), parsed)

    def test_arabizi_costs_a_little_confidence(self):
        parsed = grammar.parse("a3rad el 3omala2", SCREEN)
        clean = grammar.parse("اعرض العملاء", SCREEN)
        self.assertTrue(parsed.corrections)
        self.assertEqual({fix["via"] for fix in parsed.corrections}, {"arabizi"})
        self.assertLess(parsed.confidence, clean.confidence)

    # --- Slips --------------------------------------------------------------------------------

    def test_distance(self):
        self.assertEqual(fuzzy.distance("opne", "open"), 1)             # two letters swapped
        self.assertEqual(fuzzy.distance("invoyce", "invoice"), 1)       # a letter near it in sound
        self.assertEqual(fuzzy.distance("invocies", "invoices"), 1)
        self.assertEqual(fuzzy.distance("ارسل", "ارسم"), 2)              # another letter: not a slip
        self.assertEqual(fuzzy.distance("quotation", "qoutation"), 1)
        self.assertEqual(fuzzy.distance("abcdef", "uvwxyz", 2), 3)

    def test_the_budget_follows_the_length(self):
        self.assertEqual([fuzzy.budget(n) for n in (2, 3, 4, 7, 8, 12)], [0, 0, 1, 1, 2, 2])

    def test_a_slip_is_read_as_the_word(self):
        for text, intent, kind in (
                ("opne the invoices", "open", "invoice"),
                ("shwo me the customers", "open", "contact"),
                ("show overdue invocies", "filter", "invoice"),
                ("how many qoutations", "aggregate", "quotation"),
                ("pie chrat of sales by month", "chart", "sales"),
                ("كم موضف عنا", "aggregate", "employee")):
            with self.subTest(text=text):
                parsed = grammar.parse(text, SCREEN)
                self.assertEqual((understood(parsed), parsed.kind), (intent, kind), parsed)
                self.assertTrue(parsed.corrections, parsed)

    def test_a_bare_name_with_a_slip_is_offered_as_open_never_acted_on(self):
        for text, kind in (("sales ordrs", "sales_order"), ("qoutations", "quotation")):
            with self.subTest(text=text):
                parsed = grammar.parse(text, SCREEN)
                self.assertEqual(understood(parsed), "", parsed)
                self.assertEqual([(c["intent"], c["kind"]) for c in parsed.candidates], [("open", kind)])
                self.assertNotIn(text, parsed.candidates[0]["slots"]["words"], "The corrected words, not the typed ones")
        self.assertEqual(grammar.parse("sales orders", SCREEN).candidates, [])

    def test_a_slip_in_a_phrase_finds_the_phrase(self):
        parsed = grammar.parse("how maney employes", SCREEN)
        self.assertEqual((understood(parsed), parsed.kind), ("aggregate", "employee"), parsed)

    def test_what_is_never_corrected(self):
        untouched = (
            "find Raghad",                                    # a name
            "open Ebrahim invoices",
            'find "opne" invoices',                           # between quotes
            "open “invocies”",
            "show 150000 invoices",                           # a number
            "set the name of Raghad to invoicse",             # a value
            "create a task fix the printer",                  # free text
            "new product laptop 1500",
            "find raghad@example.com",                        # an address
            "show me the sale",                               # three letters
        )
        for text in untouched:
            with self.subTest(text=text):
                self.assertEqual(grammar.parse(text, SCREEN).corrections, [], text)

    def test_a_short_word_is_not_corrected(self):
        self.assertEqual(grammar.parse("shw invoices", SCREEN).corrections, [])
        self.assertIsNone(fuzzy.suggest(normalise.tokenize("cat")[0]))

    def test_a_word_the_company_added_is_not_a_slip(self):
        with lexicon.extended([("opnn", "open")]):
            parsed = grammar.parse("opnn the invoices", SCREEN)
        self.assertEqual((understood(parsed), parsed.kind), ("open", "invoice"), parsed)
        self.assertEqual(parsed.corrections, [])

    # --- A slip never makes a sentence surer --------------------------------------------------

    def test_a_correction_lowers_the_confidence(self):
        for typed, clean in (("opne the invoices", "open the invoices"),
                             ("show me unpiad invoicse", "show me unpaid invoices"),
                             ("كم موضف عنا", "كم موظف عنا")):
            with self.subTest(typed=typed):
                a, b = grammar.parse(typed, SCREEN), grammar.parse(clean, SCREEN)
                self.assertEqual((a.intent, a.kind), (b.intent, b.kind))
                self.assertLess(a.confidence, b.confidence)

    def test_no_slip_raises_the_confidence_of_a_sentence(self):
        rng = random.Random(7)
        checked = 0
        for group, cases in CASES.items():
            if group == "typo_arabizi":
                continue
            for text, intent, kind, expected in cases:
                clean = grammar.parse(text, SCREEN)
                words = text.split()
                for _ in range(3):
                    long_words = [i for i, word in enumerate(words) if len(word) >= 5 and word.isalpha()]
                    if not long_words:
                        continue
                    i = rng.choice(long_words)
                    at = rng.randrange(1, len(words[i]) - 2)
                    slipped = list(words)
                    slipped[i] = words[i][:at] + words[i][at + 1] + words[i][at] + words[i][at + 2:]
                    parsed = grammar.parse(" ".join(slipped), SCREEN)
                    checked += 1
                    with self.subTest(group=group, typed=" ".join(slipped)):
                        if parsed.intent == clean.intent:
                            self.assertLessEqual(parsed.confidence, clean.confidence, (clean, parsed))
                        elif parsed.corrections:
                            # Another intent through a correction: seen, never sure enough to act
                            self.assertLessEqual(parsed.confidence, clean.confidence, (clean, parsed))
        self.assertGreater(checked, 150)

    def test_a_slip_in_a_verb_that_changes_data_is_only_offered(self):
        for text in ("creat a task fix printer", "cretae a contact Raghad",
                     "updaet the phone of Raghad to 0501234567"):
            with self.subTest(text=text):
                parsed = grammar.parse(text, SCREEN)
                self.assertLess(parsed.confidence, grammar.THRESHOLD, parsed)
                self.assertEqual(understood(parsed), "")

    def test_the_offer_says_what_was_meant(self):
        parsed = grammar.parse("creat a task fix printer", SCREEN)
        self.assertTrue(parsed.candidates)
        first = parsed.candidates[0]
        self.assertEqual((first["intent"], first["kind"]), ("create", "task"))
        self.assertEqual(first["text"], "create a task fix printer")
        self.assertEqual(first["corrections"], [{"word": "creat", "to": "create", "distance": 1, "via": "typo"}])
        self.assertLess(first["confidence"], grammar.THRESHOLD)
        self.assertLessEqual(len(parsed.candidates), grammar.MAX_CANDIDATES)
        self.assertNotIn("value_spans", first["slots"])  # plain data only

    def test_a_word_that_may_be_two_things_is_offered_not_acted_on(self):
        a = fuzzy.Option("bill", {"bill"}, 1, "typo")
        b = fuzzy.Option("bell", {"filler"}, 1, "typo")
        self.assertTrue(fuzzy.Fix("bll", [a, b]).ambiguous)
        self.assertFalse(fuzzy.Fix("bll", [a]).ambiguous)
        far = fuzzy.Option("bell", {"filler"}, 2, "typo")
        self.assertFalse(fuzzy.Fix("bll", [a, far]).ambiguous)

    def test_a_clean_sentence_is_untouched(self):
        for group, cases in CASES.items():
            for text, intent, kind, expected in cases:
                if group == "typo_arabizi":
                    continue
                with self.subTest(group=group, text=text):
                    self.assertEqual(grammar.parse(text, SCREEN).corrections, [], text)

    def test_a_confident_reading_as_typed_is_not_replaced_by_a_guess(self):
        parsed = grammar.parse("create a task fix the printr", SCREEN)
        self.assertEqual((understood(parsed), parsed.kind), ("create", "task"), parsed)
        self.assertEqual(parsed.corrections, [])

    # --- The band under the threshold (NOAI-14) -----------------------------------------------

    def test_a_parse_under_the_threshold_is_offered_not_acted_on(self):
        parsed = grammar.parse("ابغى انشئ مهمه جديده", SCREEN)
        self.assertEqual(understood(parsed), "")
        self.assertGreaterEqual(parsed.confidence, grammar.OFFER_FROM)
        self.assertEqual([c["intent"] for c in parsed.candidates], ["create"])

    def test_nothing_is_offered_for_what_is_not_understood(self):
        for text in ("hello there", "what is the weather", ""):
            with self.subTest(text=text):
                self.assertEqual(grammar.parse(text, SCREEN).candidates, [])

    def test_a_sure_parse_has_no_offer(self):
        self.assertEqual(grammar.parse("open the invoices", SCREEN).candidates, [])

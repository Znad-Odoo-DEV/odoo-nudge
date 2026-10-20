import datetime

from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge.tools import noai_commands
from odoo.addons.nudge.tools.noai_commands import grammar, lexicon, normalise

from .common import NudgeCase
from .noai_cases import CASES

SCREEN = {"model": "sale.order", "record_id": 5}
TODAY = datetime.date(2026, 10, 17)


def understood(parsed):
    """What the sentence asks, as the module acts on it: nothing under the threshold"""
    return parsed.intent if parsed.confidence >= noai_commands.THRESHOLD else ""


@tagged("post_install", "-at_install")
class TestNoaiParser(TransactionCase):
    """The grammar: a sentence of any dialect, or of English, into what it asks.
    No database, no AI."""

    def test_the_sentences_of_every_dialect(self):
        for group, cases in CASES.items():
            for text, intent, kind, expected in cases:
                with self.subTest(group=group, text=text):
                    parsed = grammar.parse(text, SCREEN)
                    self.assertEqual(understood(parsed), intent, parsed)
                    if intent and kind:
                        self.assertEqual(parsed.kind, kind, parsed)
                    for key, value in (expected.get("slots") or {}).items():
                        got = parsed.slots.get(key)
                        if isinstance(value, str) and value.startswith("~"):
                            self.assertIn(value[1:], str(got), parsed)
                        else:
                            self.assertEqual(got, value, parsed)
                    if "states" in expected:
                        self.assertEqual(parsed.states, expected["states"], parsed)
                    if "period" in expected:
                        self.assertEqual(parsed.period, expected["period"], parsed)

    def test_enough_sentences_of_each_group(self):
        total = sum(len(cases) for cases in CASES.values())
        self.assertGreaterEqual(total, 90)
        arabic = sum(len(cases) for group, cases in CASES.items() if group != "english")
        self.assertGreaterEqual(arabic, total // 2, "Half of the sentences are Arabic")
        for group in ("gulf", "egyptian", "iraqi", "levantine", "maghrebi", "msa"):
            self.assertGreaterEqual(len(CASES[group]), 8, group)

    def test_every_group_of_the_lexicon_is_loaded(self):
        for group in ("msa", "gulf", "egyptian", "iraqi", "levantine", "maghrebi", "sudanese_yemeni",
                      "english", "arabizi"):
            self.assertIn(group, lexicon.GROUPS)
            self.assertGreater(len(lexicon.GROUPS[group]), 100, group)

    def test_a_word_of_one_dialect_means_the_same_in_a_sentence_of_another(self):
        mixed = (
            ("ودني على الفواتير", "open", "invoice"),            # Gulf verb, Modern Standard noun
            ("كام فاتورة مو مدفوعة", "aggregate", "invoice"),     # Egyptian count, Iraqi negation
            ("شحال الفواتير المتأخرة", "aggregate", "invoice"),   # Maghrebi count, Standard state
            ("open الفواتير", "open", "invoice"),                  # English verb, Arabic noun
            ("وريني unpaid invoices", "filter", "invoice"),        # Levantine verb, English state
            ("how many فاتورة overdue", "aggregate", "invoice"),
        )
        for text, intent, kind in mixed:
            with self.subTest(text=text):
                parsed = grammar.parse(text, SCREEN)
                self.assertEqual((understood(parsed), parsed.kind), (intent, kind), parsed)

    def test_the_spelling_does_not_matter(self):
        same = (
            ("كَيْفَ", "كيف"), ("فاتــــورة", "فاتوره"), ("فاتورة", "فاتوره"), ("أكّد", "اكد"),
            ("إجازة", "اجازه"), ("مرحّل", "مرحل"), ("جـوّال", "جوال"), ("KAAAM", "kam"),
            ("چم", "جم"), ("ڤاتورة", "فاتوره"),
        )
        for left, right in same:
            with self.subTest(left=left):
                self.assertEqual(normalise.fold(left), normalise.fold(right))
        self.assertEqual(normalise.fold("٠١٢٣٤٥٦٧٨٩".translate(normalise.DIGITS)), "0123456789")
        self.assertEqual(grammar.parse("افتح الفواتير", SCREEN).slots["words"], "الفواتير")
        self.assertEqual(understood(grammar.parse("اَفْتَحْ الفَوَاتِيرَ", SCREEN)), "open")
        self.assertEqual(understood(grammar.parse("افتـــح الفواتير", SCREEN)), "open")

    def test_digits_and_numbers_said_in_words(self):
        for text, quantity in (
                ("أنشئ عرض سعر لراغد ٣ لابتوب", 3.0),
                ("اعمل عرض سعر لراغد خمسة لابتوب", 5.0),
                ("اعمل عرض سعر لراغد تلاتة لابتوب", 3.0),
                ("سوي عرض سعر لراغد ثنين لابتوب", 2.0),
                ("دير ليا عرض سعر ديال راغد جوج لابتوب", 2.0),
                ("create quotation for Raghad five Laptop", 5.0),
                ("create quotation for Raghad 3 Laptop", 3.0),
                ("اعمل عرض سعر لراغد خمسة وعشرين لابتوب", 25.0),
                ("create quotation for Raghad 12×Laptop", 12.0),
        ):
            with self.subTest(text=text):
                parsed = grammar.parse(text, SCREEN)
                self.assertEqual(understood(parsed), "create", parsed)
                self.assertEqual(parsed.slots["lines"][0]["qty"], quantity, parsed)
                self.assertTrue(parsed.slots["lines"][0]["product"].endswith(("Laptop", "لابتوب")), parsed)

    def test_a_line_may_say_its_quantity_after_the_product(self):
        parsed = grammar.parse("create quotation for Raghad 1 x Mouse and Laptop x 4", SCREEN)
        self.assertEqual(parsed.slots["lines"], [{"qty": 1.0, "product": "Mouse"}, {"qty": 4.0, "product": "Laptop"}])

    def test_dates_and_periods(self):
        cases = (
            ("مجموع المبيعات اليوم", ("rel", "today"), (TODAY, TODAY + datetime.timedelta(days=1))),
            ("total sales yesterday", ("rel", "yesterday"), (TODAY - datetime.timedelta(days=1), TODAY)),
            ("مجموع المبيعات هالشهر", ("rel", "this_month"), (datetime.date(2026, 10, 1), datetime.date(2026, 11, 1))),
            ("مجموع المبيعات الشهر اللي راح", ("rel", "last_month"), (datetime.date(2026, 9, 1), datetime.date(2026, 10, 1))),
            ("مجموع المبيعات الشهر الجاي", ("rel", "next_month"), (datetime.date(2026, 11, 1), datetime.date(2026, 12, 1))),
            ("total sales this year", ("rel", "this_year"), (datetime.date(2026, 1, 1), datetime.date(2027, 1, 1))),
            ("مجموع المبيعات السنة اللي فاتت", ("rel", "last_year"), (datetime.date(2025, 1, 1), datetime.date(2026, 1, 1))),
            ("مجموع المبيعات في اغسطس", ("month", 8, None), (datetime.date(2026, 8, 1), datetime.date(2026, 9, 1))),
            ("مجموع المبيعات في آب ٢٠٢٤", ("month", 8, 2024), (datetime.date(2024, 8, 1), datetime.date(2024, 9, 1))),
            ("total sales in jan 2025", ("month", 1, 2025), (datetime.date(2025, 1, 1), datetime.date(2025, 2, 1))),
            ("total sales 2025", ("year", 2025), (datetime.date(2025, 1, 1), datetime.date(2026, 1, 1))),
            ("مجموع المبيعات جانفي", ("month", 1, None), (datetime.date(2026, 1, 1), datetime.date(2026, 2, 1))),
            ("total sales this week", ("rel", "this_week"), (datetime.date(2026, 10, 12), datetime.date(2026, 10, 19))),
        )
        for text, spec, expected in cases:
            with self.subTest(text=text):
                parsed = grammar.parse(text, SCREEN)
                self.assertEqual(parsed.period, spec, parsed)
                self.assertEqual(grammar.period_range(spec, TODAY), expected)

    def test_a_day_said_for_a_value(self):
        today = TODAY  # a Saturday
        self.assertEqual(today.weekday(), 5)
        for text, expected in (
                ("Friday", datetime.date(2026, 10, 23)),
                ("الجمعة", datetime.date(2026, 10, 23)),
                ("tomorrow", datetime.date(2026, 10, 18)),
                ("بكرة", datetime.date(2026, 10, 18)),
                ("2026-12-01", datetime.date(2026, 12, 1)),
                ("15 october", datetime.date(2026, 10, 15)),
        ):
            with self.subTest(text=text):
                spans = grammar.scan(normalise.tokenize(text))
                specs = grammar.date_specs(spans)
                self.assertEqual(grammar.date_of(specs[0], today), expected)

    def test_what_is_not_understood_is_left_to_the_ai(self):
        for text in ("", "   ", "hello", "what is the weather", "ما هو الطقس اليوم", "باجر", "ؤؤؤ", "?!", "x" * 5000,
                     "افتح", "غيّر", "create", "كم", "total", "the quick brown fox"):
            with self.subTest(text=text[:20]):
                self.assertEqual(understood(grammar.parse(text, {})), "", text[:20])

    def test_a_press_needs_a_record_or_a_name(self):
        self.assertEqual(understood(grammar.parse("confirm", {})), "")
        self.assertEqual(understood(grammar.parse("confirm", {"model": "sale.order", "record_id": 3})), "press")
        self.assertEqual(understood(grammar.parse("confirm S00012", {})), "press")
        self.assertEqual(grammar.parse("confirm S00012", {}).slots["name"], "S00012")

    def test_a_negation_turns_a_state_around(self):
        self.assertEqual(grammar.parse("how many invoices not paid", SCREEN).states, ["unpaid"])
        self.assertEqual(grammar.parse("كم فاتورة مش مدفوعة", SCREEN).states, ["unpaid"])
        self.assertEqual(grammar.parse("كم فاتورة مدفوعة", SCREEN).states, ["paid"])

    def test_a_question_is_not_a_command(self):
        # "what" and "كيف" alone ask for an explanation, not for work
        self.assertEqual(understood(grammar.parse("how do I create a quotation", {})), "")
        self.assertEqual(understood(grammar.parse("كيف أنشئ فاتورة", {})), "")


@tagged("post_install", "-at_install")
class TestNoaiRun(NudgeCase, TransactionCase):
    """What the module does with a sentence: the cards, the rights, the log."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.has_sales = "sale.order" in cls.env
        cls.has_contacts_app = bool(cls.env["ir.module.module"].sudo().search_count([("name", "=", "contacts"), ("state", "=", "installed")]))
        groups = "nudge.group_nudge_agent,base.group_partner_manager"
        if cls.has_sales:
            groups += ",sales_team.group_sale_salesman"
        cls.worker = new_test_user(cls.env, login="nudge_noai", groups=groups)
        cls.raghad = cls.env["res.partner"].create({"name": "Raghad", "phone": "0500000001"})
        cls.ebrahim = cls.env["res.partner"].create({"name": "Ebrahim"})
        # Not a provider: whatever reaches the model would fail
        cls.provider.active = False

    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param("nudge.noai_first", "1")

    def last_text(self, events):
        return "".join(event.get("text", "") for event in events if event["type"] == "delta")

    def cards(self, events):
        return [event for event in events if event["type"] == "confirm"]

    def test_a_parsed_create_goes_to_a_card_and_nothing_is_created_before_the_yes(self):
        before = self.env["res.partner"].search_count([("name", "=", "Raghad Ali")])
        conversation, events = self.order(self.worker, "create contact Raghad Ali phone 0501234567")
        cards = self.cards(events)
        self.assertEqual(len(cards), 1, events)
        self.assertEqual(cards[0]["title"], "Create: Contact")
        self.assertEqual(events[-1]["type"], "waiting")
        self.assertEqual(self.env["res.partner"].search_count([("name", "=", "Raghad Ali")]), before)
        done = self.decide(conversation, "confirm")
        partner = self.env["res.partner"].search([("name", "=", "Raghad Ali")])
        self.assertEqual((len(partner), partner.phone), (1, "0501234567"))
        self.assertEqual(done[-1]["type"], "done")
        decided = [event for event in done if event["type"] == "decided"]
        self.assertTrue(decided and decided[0]["state"] == "done", done)

    def test_a_no_creates_nothing(self):
        conversation, events = self.order(self.worker, "أنشئ جهة اتصال راغد الثانية")
        self.assertEqual(len(self.cards(events)), 1, events)
        self.decide(conversation, "decline")
        self.assertFalse(self.env["res.partner"].search([("name", "=", "راغد الثانية")]))

    def test_the_arabic_create_of_a_contact_with_a_phone(self):
        conversation, events = self.order(self.worker, "سويلي جهة اتصال رغد الاولى جوال ٠٥٠١٢٣٤٥٦٧")
        self.assertEqual(len(self.cards(events)), 1, events)
        self.decide(conversation, "confirm")
        partner = self.env["res.partner"].search([("name", "=", "رغد الاولى")])
        self.assertEqual(partner.phone, "0501234567")

    def test_a_parsed_quotation_has_its_lines_in_the_card(self):
        if not self.has_sales:
            self.skipTest("Sales is not installed")
        self.env["product.product"].create({"name": "Laptop", "list_price": 1500})
        self.env["product.product"].create({"name": "Mouse", "list_price": 25})
        conversation, events = self.order(self.worker, "create quotation for Raghad 2 x Laptop and 3 x Mouse")
        cards = self.cards(events)
        self.assertEqual(len(cards), 1, events)
        text = str(cards[0]["lines"])
        self.assertIn("Laptop", text)
        self.assertIn("Mouse", text)
        self.decide(conversation, "confirm")
        order = self.env["sale.order"].search([("partner_id", "=", self.raghad.id)])
        self.assertEqual(sorted(order.order_line.mapped("product_uom_qty")), [2.0, 3.0])

    def test_a_press_shows_the_card_with_the_warning(self):
        if not self.has_sales:
            self.skipTest("Sales is not installed")
        product = self.env["product.product"].create({"name": "Chair", "list_price": 100})
        order = self.env["sale.order"].create({
            "partner_id": self.raghad.id, "user_id": self.worker.id,
            "order_line": [(0, 0, {"product_id": product.id, "product_uom_qty": 1})],
        })
        snapshot = {
            "v": 1, "model": "sale.order", "view_type": "form", "record": {"id": order.id, "name": order.name},
            "buttons": [{"name": "action_confirm", "label": "Confirm", "kind": "object"},
                        {"name": "action_cancel", "label": "Cancel", "kind": "object"}],
        }
        for text in ("confirm", "أكّد", "كونفرمي", "ثبتها"):
            with self.subTest(text=text):
                conversation, events = self.order(self.worker, text, snapshot=snapshot)
                cards = self.cards(events)
                self.assertEqual(len(cards), 1, events)
                self.assertTrue(cards[0]["sensitive"], "The warning is there")
                self.assertEqual(cards[0]["kind"], "method")
                self.assertEqual(order.state, "draft", "Nothing is pressed before the yes")
                self.decide(conversation, "decline")
        conversation, events = self.order(self.worker, "confirm", snapshot=snapshot)
        self.decide(conversation, "confirm")
        self.assertEqual(order.state, "sale")

    def test_pressing_a_button_the_record_has_not_says_so(self):
        if not self.has_sales:
            self.skipTest("Sales is not installed")
        order = self.env["sale.order"].create({"partner_id": self.raghad.id, "user_id": self.worker.id})
        snapshot = {"v": 1, "model": "sale.order", "view_type": "form", "record": {"id": order.id},
                    "buttons": [{"name": "action_cancel", "label": "Cancel", "kind": "object"}]}
        conversation, events = self.order(self.worker, "post", snapshot=snapshot)
        self.assertFalse(self.cards(events))
        self.assertIn("no such button", self.last_text(events))

    def test_a_change_of_one_field_goes_to_a_card(self):
        conversation, events = self.order(self.worker, "غيّر جوال Raghad ل 0509998888")
        cards = self.cards(events)
        self.assertEqual(len(cards), 1, events)
        self.assertEqual(self.raghad.phone, "0500000001")
        done = self.decide(conversation, "confirm")
        self.assertEqual(self.raghad.mobile, "0509998888")
        self.assertTrue([event for event in done if event["type"] == "decided"][0]["can_undo"], "It can be taken back")

    def test_a_name_that_fits_two_records_is_a_choice_never_a_guess(self):
        self.env["res.partner"].create({"name": "Raghad Sami"})
        self.env["res.partner"].create({"name": "Raghad Salem"})
        conversation, events = self.order(self.worker, "set the phone of Raghad S to 0501111111")
        self.assertFalse(self.cards(events), events)
        found = [event for event in events if event["type"] == "found"]
        self.assertEqual(len(found), 1, events)
        self.assertEqual(sorted(record["name"] for record in found[0]["records"]), ["Raghad Salem", "Raghad Sami"])
        self.assertFalse(self.env["res.partner"].search([("phone", "=", "0501111111")]))

    def test_an_open_is_a_screen_or_a_choice_of_screens(self):
        if not self.has_contacts_app:
            self.skipTest("Contacts is not installed")
        _conversation, events = self.order(self.worker, "open contacts")
        navigate = [event for event in events if event["type"] == "navigate"]
        self.assertEqual(len(navigate), 1, events)
        self.assertIn("Contacts", navigate[0]["path"])
        _conversation, events = self.order(self.worker, "افتح جهات الاتصال")
        self.assertTrue([event for event in events if event["type"] == "navigate"], events)

    def test_a_find_opens_the_record(self):
        _conversation, events = self.order(self.worker, "find Ebrahim")
        opened = [event for event in events if event["type"] == "open"]
        self.assertEqual([(event["model"], event["id"]) for event in opened], [("res.partner", self.ebrahim.id)], events)

    def test_a_count_is_answered_in_words(self):
        total = self.env["res.partner"].with_user(self.worker).search_count([])
        _conversation, events = self.order(self.worker, "how many contacts")
        self.assertIn(f"{total:,}", self.last_text(events))
        _conversation, events = self.order(self.worker, "كم عدد جهات الاتصال")
        self.assertIn(f"{total:,}", self.last_text(events))

    def test_a_filter_opens_the_list_with_the_conditions(self):
        if not self.has_sales:
            self.skipTest("Sales is not installed")
        snapshot = {"v": 1, "model": "sale.order", "view_type": "list"}
        _conversation, events = self.order(self.worker, "filter to this month", snapshot=snapshot)
        shown = [event for event in events if event["type"] == "filter"]
        self.assertEqual(len(shown), 1, events)
        self.assertEqual(shown[0]["model"], "sale.order")

    def test_the_run_is_written_down_with_no_token(self):
        self.order(self.worker, "find Ebrahim")
        self.order(self.worker, "create contact Raghad Third")
        usage = self.env["nudge.usage"].search([("user_id", "=", self.worker.id), ("kind", "=", "noai")])
        self.assertEqual(sorted(usage.mapped("intent")), ["create", "find"])
        self.assertEqual(sum(usage.mapped("tokens")), 0)
        self.assertFalse(self.env["nudge.usage"].search([("user_id", "=", self.worker.id), ("kind", "=", "chat")]))

    def test_the_rights_of_the_person_are_the_rights_of_the_run(self):
        reader = new_test_user(self.env, login="nudge_noai_reader", groups="nudge.group_nudge_agent")
        _conversation, events = self.order(reader, "create contact Raghad Fourth")
        self.assertFalse(self.cards(events), "A person who may not create contacts is shown no card")
        self.assertFalse(self.env["res.partner"].search([("name", "=", "Raghad Fourth")]))
        # And someone without the right to ask for work is not served
        user = new_test_user(self.env, login="nudge_noai_plain", groups="nudge.group_nudge_user")
        conversation = self.env["nudge.conversation"].with_user(user)._chat_open()
        from odoo.exceptions import UserError
        with self.assertRaises(UserError):
            conversation._chat_prepare("create contact Raghad Fifth", mode="agent")

    def test_with_no_provider_the_person_is_told_what_was_understood(self):
        self.assertFalse(self.env["nudge.provider"]._get_default())
        _conversation, events = self.order(self.worker, "hello there my friend")
        text = self.last_text(events)
        self.assertIn("I did not understand", text)
        self.assertIn("without an ai provider", text.lower())
        self.assertEqual(events[-1]["type"], "done")
        _conversation, events = self.order(self.worker, "total sales last month")
        self.assertNotIn("I did not understand", self.last_text(events))

    def test_the_setting_off_leaves_everything_to_the_provider(self):
        self.provider.active = True
        self.env["ir.config_parameter"].sudo().set_param("nudge.noai_first", "0")
        adapter = self.use_adapter(reply="From the AI")
        _conversation, events = self.order(self.worker, "create contact Raghad Sixth")
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(self.last_text(events), "From the AI")
        self.assertFalse(self.cards(events))

    def test_the_setting_on_spares_the_provider_and_what_is_not_understood_goes_to_it(self):
        self.provider.active = True
        adapter = self.use_adapter(reply="From the AI")
        _conversation, events = self.order(self.worker, "create contact Raghad Seventh")
        self.assertEqual(len(adapter.calls), 0, "A common request costs no call")
        self.assertEqual(len(self.cards(events)), 1)
        _conversation, events = self.order(self.worker, "please write me a poem about the sea")
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(self.last_text(events), "From the AI")

    def test_private_mode_with_an_outside_provider_is_no_provider(self):
        self.provider.active = True
        self.env.company.nudge_private_mode = True
        adapter = self.use_adapter(reply="From the AI")
        _conversation, events = self.order(self.worker, "please write me a poem about the sea")
        self.assertEqual(len(adapter.calls), 0)
        self.assertIn("I did not understand", self.last_text(events))
        _conversation, events = self.order(self.worker, "find Ebrahim")
        self.assertTrue([event for event in events if event["type"] == "open"], events)

    def test_the_quick_search_offers_the_parsed_action_first(self):
        Noai = self.env["nudge.noai"].with_user(self.worker)
        suggestion = Noai.suggest("create contact Raghad Eighth phone 0501234567")
        self.assertEqual(suggestion["intent"], "create")
        self.assertIn("Contact", suggestion["label"])
        self.assertEqual(Noai.suggest("اعمل جهة اتصال راغد")["intent"], "create")
        self.assertFalse(Noai.suggest("the quick brown fox"))
        self.env["ir.config_parameter"].sudo().set_param("nudge.noai_first", "0")
        self.assertFalse(Noai.suggest("find Ebrahim"), "Off, nothing is offered")

    def test_a_conversation_goes_on_after_a_request_without_ai(self):
        conversation, events = self.order(self.worker, "find Ebrahim")
        conversation, events = self.order(self.worker, "find Raghad", conversation_id=conversation.id)
        self.assertEqual(len(conversation.sudo().message_ids.filtered(lambda m: m.role == "user")), 2)

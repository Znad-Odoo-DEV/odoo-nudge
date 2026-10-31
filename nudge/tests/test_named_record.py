from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge.llm import loop, prompt
from odoo.addons.nudge.tools import named_record, noai_commands

from .test_agent import AgentCase, asking, call, saying, values

NOT_VISIBLE = "the record asked for is not visible to this person"
SAID = "I told the person."


@tagged("post_install", "-at_install")
class TestReferencesWritten(TransactionCase):
    """Which words of a sentence are the reference of a record, in words only"""

    def test_what_is_numbered_as_records_are(self):
        written = noai_commands.question.references_in
        for text, found in (
            ("غيّر كمية السطر في أمر البيع S00129 إلى 5", ["s00129"]),
            ("change INV/2026/00012 and WH/OUT/00007", ["inv/2026/00012", "wh/out/00007"]),
            ("INV/2026/12", ["inv/2026/12"]),
            ("لأمرS00129 من فضلك", ["s00129"]),
            ("SO-2026-001?", ["so-2026-001"]),
        ):
            with self.subTest(text=text):
                self.assertEqual(written(text), found)

    def test_what_only_looks_like_it(self):
        for text in ("change the quantity to 5", "an order of 50,000", "the sales of Q4", "Q4-2026", "the budget of FY2026",
                     "paper A4 and 100kg", "write to raghad123@example.com", "the file report123.pdf", "2026-10-31",
                     "E-COM11", "غيّر الكمية إلى 5"):
            with self.subTest(text=text):
                self.assertEqual(noai_commands.question.references_in(text), [])

    def test_a_reference_is_held_as_a_whole(self):
        holds = noai_commands.question.holds_reference
        self.assertTrue(holds("S00129 - [E-COM11] Cabinet (Ebrahim)", "s00129"))
        self.assertTrue(holds("Sales Order (S00129)", "s00129"))
        self.assertFalse(holds("S00129", "s0012"))
        self.assertFalse(holds("S001290", "s00129"))


@tagged("post_install", "-at_install")
class TestNamedRecord(AgentCase, TransactionCase):
    """Filmed: Osama, own documents only, on the screen of his order, asks to change a line of Raghad's order. The search
    finds his own only, and the model prepared the change on his line. No card changes another record than the one
    named, whatever the model called, and nothing tells whether the one named exists"""

    def setUp(self):
        super().setUp()
        if "sale.order" not in self.env:
            self.skipTest("Sales is not installed")
        groups = "nudge.group_nudge_agent,sales_team.group_sale_salesman"
        self.osama = new_test_user(self.env, login="nudge_named_osama", groups=groups)
        self.raghad = new_test_user(self.env, login="nudge_named_raghad", groups=groups)
        product = self.env["product.product"].create({"name": "Nudge Cabinet"})

        def order(user):
            return self.env["sale.order"].create({
                "partner_id": self.acme.id, "user_id": user.id,
                "order_line": [(0, 0, {"product_id": product.id, "product_uom_qty": 3})]})

        self.mine, self.other_of_mine, self.hers = order(self.osama), order(self.osama), order(self.raghad)
        self.line = self.mine.order_line
        self.snapshot = {"v": 1, "model": "sale.order", "view_type": "form",
                         "record": {"id": self.mine.id, "name": self.mine.name}, "buttons": [
                             {"name": "action_confirm", "label": "Confirm", "kind": "object"}]}

    def turn(self, text, *rounds, user=None):
        """The model makes ``rounds`` of calls, then says it told the person, through the path of the controller
        :return: adapter, conversation, events"""
        adapter = self.use_adapter(script=[*[asking(*calls) for calls in rounds], saying(SAID)])
        conversation = self.env["nudge.conversation"].with_user(user or self.osama)._chat_open(None)
        job = conversation._chat_prepare(text, snapshot=self.snapshot, mode="agent")
        return adapter, conversation, list(loop.run(job, conversation._chat_record))

    def write_line(self):
        return call("prepare_write", {"model": "sale.order.line", "ids": self.line.ids,
                                      "values": values(product_uom_qty="5")})

    def press(self, *orders):
        return call("call_method", {"model": "sale.order", "ids": [order.id for order in orders],
                                    "method": "action_confirm"})

    @staticmethod
    def cards(events):
        return [event for event in events if event["type"] == "confirm"]

    def assertRefused(self, turn, fragment=NOT_VISIBLE):
        adapter, conversation, events = turn
        self.assertFalse(self.cards(events), events)
        self.assertFalse(conversation.sudo().waiting_step_id)
        self.assertFalse(conversation.sudo().run_ids.step_ids.filtered(lambda step: step.state == "pending"))
        told = adapter.calls[-1]["turns"][-1]["results"][0]
        self.assertTrue(told["is_error"])
        self.assertIn(fragment, told["content"])
        return told["content"]

    def assertCard(self, turn):
        self.assertEqual(len(self.cards(turn[2])), 1, turn[2])

    # ---------------------------------------------------------------- what the person named is out of reach
    def test_a_line_of_another_record_makes_no_card(self):
        for text in (f"Change the quantity of the line in sales order {self.hers.name} to 5",
                     f"غيّر كمية السطر في أمر البيع {self.hers.name} إلى 5"):
            with self.subTest(text=text):
                self.assertRefused(self.turn(text, [self.write_line()]))
                self.assertEqual(self.line.product_uom_qty, 3, "Nothing is written")

    def test_it_does_not_tell_whether_the_record_exists(self):
        there = self.assertRefused(self.turn(f"Change the line of {self.hers.name}", [self.write_line()]))
        gone = self.assertRefused(self.turn("Change the line of S99999", [self.write_line()]))
        self.assertEqual(there.replace(self.hers.name, "S99999"), gone)
        for name in (self.mine.name, self.line.display_name):
            self.assertNotIn(name, there)

    def test_a_press_of_another_record_makes_no_card(self):
        for text in (f"Can you confirm {self.hers.name}?", f"ممكن تأكد {self.hers.name}"):
            with self.subTest(text=text):
                self.assertRefused(self.turn(text, [self.press(self.mine)]))
                self.assertEqual(self.mine.state, "draft")

    def test_a_plan_of_one_yes_on_another_record_makes_no_card(self):
        step = {"action": "write", "model": "sale.order.line", "ids": self.line.ids,
                "values": values(product_uom_qty="5")}
        self.assertRefused(self.turn(
            f"Change the line of {self.hers.name}", [call("prepare_plan", {"title": "Set it", "steps": [step]})]))

    def test_a_plan_of_several_calls_on_another_record_makes_no_card(self):
        calls = [call("prepare_write", {"model": "sale.order", "ids": self.mine.ids, "values": values(note="x")}, "c1"),
                 self.press(self.mine)]
        adapter, conversation, events = self.turn(f"Change and confirm {self.hers.name}", calls)
        self.assertFalse(self.cards(events), events)
        results = adapter.calls[-1]["turns"][-1]["results"]
        self.assertIn(NOT_VISIBLE, results[0]["content"])
        self.assertFalse(conversation.sudo().waiting_step_id)

    def test_a_question_about_another_record_offers_no_chip(self):
        for text in (f"Should I change the line of {self.hers.name}?", f"هل أغير سطر {self.hers.name}؟"):
            with self.subTest(text=text):
                adapter, _conversation, events = self.turn(text, [self.write_line()])
                self.assertFalse(self.cards(events), events)
                self.assertFalse([event for event in events if event["type"] == "found"], events)
                self.assertIn(NOT_VISIBLE, adapter.calls[-1]["turns"][-1]["results"][0]["content"])

    def test_a_question_answered_in_words_offers_no_press_of_another_record(self):
        adapter = self.use_adapter(script=[saying(SAID)])
        conversation = self.env["nudge.conversation"].with_user(self.osama)._chat_open(None)
        job = conversation._chat_prepare(f"Should I confirm {self.hers.name}?", snapshot=self.snapshot, mode="agent")
        events = list(loop.run(job, conversation._chat_record))
        self.assertTrue(adapter.calls)
        self.assertFalse([event for event in events if event["type"] in ("found", "confirm")], events)

    def test_a_line_added_to_another_record_makes_no_card(self):
        create = call("prepare_create", {"model": "sale.order.line", "values": values(
            order_id=str(self.mine.id), product_id=str(self.line.product_id.id))})
        self.assertRefused(self.turn(f"Add a line to {self.hers.name}", [create]))

    def test_the_tool_stands_alone_without_a_request(self):
        result = self.run_tool("prepare_write", {"model": "sale.order.line", "ids": self.line.ids,
                                                 "values": values(product_uom_qty="5")}, user=self.osama)
        self.assertTrue(result.pending, result.content)

    # ---------------------------------------------------------------- what still gets its card
    def test_a_request_that_names_nothing_gets_its_card(self):
        for text in ("Can you change the quantity of this line to 5?", "ممكن تغيّر كمية هذا السطر إلى 5",
                     "Change the quantity to 5, the deal is 50,000 for Q4", "غيّر الكمية إلى 5 لأجل Q4"):
            with self.subTest(text=text):
                self.assertCard(self.turn(text, [self.write_line()]))

    def test_a_request_that_names_the_record_itself_gets_its_card(self):
        for text in (f"Change the quantity of the line in {self.mine.name} to 5",
                     f"غيّر كمية السطر في أمر البيع {self.mine.name} إلى 5"):
            with self.subTest(text=text):
                self.assertCard(self.turn(text, [self.write_line()]))
        self.assertCard(self.turn(f"Can you confirm {self.mine.name}?", [self.press(self.mine)]))
        self.assertCard(self.turn(f"ممكن تأكد {self.mine.name}", [self.press(self.mine)]))

    def test_two_records_named_and_both_changed_get_their_card(self):
        text = f"Can you confirm {self.mine.name} and {self.other_of_mine.name}?"
        self.assertCard(self.turn(text, [self.press(self.mine, self.other_of_mine)]))
        text = f"ممكن تأكد {self.mine.name} و {self.other_of_mine.name}"
        self.assertCard(self.turn(text, [self.press(self.mine, self.other_of_mine)]))

    def test_one_of_two_named_is_itself_named(self):
        self.assertCard(self.turn(f"Can you confirm {self.hers.name} and {self.mine.name}?", [self.press(self.mine)]))

    def test_a_third_record_beside_the_one_named_makes_no_card(self):
        self.assertRefused(self.turn(f"Can you confirm {self.mine.name}?", [self.press(self.mine, self.other_of_mine)]))

    def test_a_reference_that_is_being_written_is_no_record_named(self):
        write = call("prepare_write", {"model": "sale.order", "ids": self.mine.ids,
                                       "values": values(client_order_ref="PO-4455")})
        self.assertCard(self.turn("Set the customer reference of this order to PO-4455", [write]))

    def test_an_amount_with_its_currency_is_no_record_named(self):
        self.env["res.currency"].with_context(active_test=False).search([("name", "=", "USD")], limit=1).ensure_one()
        self.assertCard(self.turn("Change the quantity to 5, the deal is USD5000", [self.write_line()]))

    def test_a_record_reached_through_the_one_named_gets_its_card(self):
        read = call("search_records", {
            "model": "sale.order", "conditions": [{"field": "name", "operator": "=", "value": self.mine.name}],
            "fields": ["partner_id"]}, "c1")
        write = call("prepare_write", {"model": "res.partner", "ids": self.acme.ids, "values": values(phone="123")})
        text = f"Change the phone of the customer of {self.mine.name}"
        self.assertRefused(self.turn(text, [write]))
        self.assertCard(self.turn(text, [read], [write]))

    def test_another_record_of_the_same_kind_as_the_one_read_makes_no_card(self):
        read = call("search_records", {
            "model": "sale.order", "conditions": [{"field": "name", "operator": "=", "value": self.other_of_mine.name}]},
            "c1")
        self.assertRefused(
            self.turn(f"Change the line of {self.other_of_mine.name}", [read], [self.write_line()]),
            "another record than the one the request names")

    def test_a_record_holds_what_it_comes_from_in_a_text_of_its_own(self):
        self.other_of_mine.origin = self.mine.name
        press = self.press(self.other_of_mine)
        self.assertCard(self.turn(f"Can you confirm the order that comes from {self.mine.name}?", [press]))
        self.other_of_mine.origin = False
        self.assertRefused(self.turn(f"Can you confirm the order that comes from {self.mine.name}?",
                                     [self.press(self.other_of_mine)]))

    def test_a_long_request_is_read_whole(self):
        text = f"{'The customer wrote a long story about this order. ' * 20}Change the line of {self.hers.name}"
        self.assertRefused(self.turn(text, [self.write_line()]))

    def test_a_step_on_what_the_plan_makes_is_not_the_record_named(self):
        calls = [call("prepare_create", {"model": "sale.order", "values": values(partner_id=str(self.acme.id))}, "c1"),
                 call("prepare_create", {"model": "sale.order.line", "values": values(
                     order_id="@1", product_id=str(self.line.product_id.id))}, "c2")]
        _adapter, conversation, events = self.turn(f"Make a new order like {self.mine.name} with this product", calls)
        self.assertEqual(len(self.cards(events)), 1, events)
        self.assertEqual(conversation.sudo().waiting_step_id.kind, "plan")

    # ---------------------------------------------------------------- the rules, without any AI
    def rules(self, text):
        """Answered by the rules alone: no provider, whatever reaches the model would fail
        :return: conversation, events, the words of the answer"""
        self.provider.active = False
        self.env["ir.config_parameter"].sudo().set_param("nudge.noai_first", "1")
        conversation = self.env["nudge.conversation"].with_user(self.osama)._chat_open(None)
        job = conversation._chat_prepare(text, snapshot=self.snapshot, mode="agent")
        events = list(loop.run(job, conversation._chat_record))
        return conversation, events, "".join(event.get("text", "") for event in events if event["type"] == "delta")

    def test_the_rules_cannot_reach_it_either(self):
        for words in ("confirm {}", "أكد {}", "cancel order {}", "confirm this order {}", "أكد هذا الأمر {}"):
            with self.subTest(words=words):
                conversation, events, there = self.rules(words.format(self.hers.name))
                self.assertFalse(self.cards(events), events)
                self.assertFalse(conversation.sudo().waiting_step_id)
                self.assertEqual(self.hers.sudo().state, "draft")
                self.assertEqual(self.mine.state, "draft")
                self.assertTrue(there, events)
                # Nothing tells it from a record that is not there
                _conversation, _events, gone = self.rules(words.format("S99999"))
                self.assertEqual(there.replace(self.hers.name, "S99999"), gone)

    def test_the_rules_still_press_the_record_of_the_screen(self):
        _conversation, events, _words = self.rules("confirm this order")
        self.assertEqual(len(self.cards(events)), 1, events)
        _conversation, events, _words = self.rules(f"confirm {self.mine.name}")
        self.assertEqual(len(self.cards(events)), 1, events)

    def test_the_rules_say_it_in_the_words_of_the_person(self):
        conversation = self.env["nudge.conversation"].with_user(self.osama)._chat_open(None)
        self.env["nudge.message"].sudo().create({
            "conversation_id": conversation.id, "role": "user", "content": f"confirm {self.hers.name}", "mode": "agent"})
        env = self.env(user=self.osama, context={"nudge_conversation_id": conversation.id, "nudge_mode": "agent"})
        refused = named_record.refusal(env, "call_method", {"model": "sale.order", "ids": self.mine.ids, "method": "x"}, {
            "res_model": "sale.order", "res_ids": self.mine.ids})
        self.assertTrue(refused.is_error)
        self.assertEqual(refused.content.split(" Nothing was")[0],
                         f"I cannot find {self.hers.name.upper()} among the records you can see, so I prepared nothing.")


@tagged("post_install", "-at_install")
class TestAccessWording(AgentCase, TransactionCase):
    """What a person without the rights is told: in their language and plain words, never the words a tool has for the
    model, never a fact about what they cannot see"""

    def setUp(self):
        super().setUp()
        if "sale.order" not in self.env:
            self.skipTest("Sales is not installed")
        self.no_sales = new_test_user(self.env, login="nudge_no_sales", groups="nudge.group_nudge_agent")
        self.assertFalse(self.env["sale.order"].with_user(self.no_sales).has_access("read"))

    def answer(self, text, user, mode="agent"):
        """Answered by the rules alone: no provider, whatever reaches the model would fail"""
        self.provider.active = False
        self.env["ir.config_parameter"].sudo().set_param("nudge.noai_first", "1")
        conversation = self.env["nudge.conversation"].with_user(user)._chat_open(None)
        job = conversation._chat_prepare(text, mode=mode)
        events = list(loop.run(job, conversation._chat_record))
        return "".join(event.get("text", "") for event in events if event["type"] == "delta")

    def test_sec04_the_tool_tells_the_model_how_to_say_it(self):
        result = self.run_tool("search_records", {"model": "sale.order"}, user=self.no_sales)
        self.assertTrue(result.is_error)
        self.assertIn("no access to that kind of record", result.content)
        self.assertIn("in their language", result.content)
        self.assertIn("never naming a technical model", result.content)
        self.assertIn("never inventing a menu path", result.content)
        told = self.run_tool("describe_model", {"model": "sale.order"}, user=self.no_sales)
        self.assertIn("no access to that kind of record", told.content)

    def test_sec04_the_rules_never_show_the_words_of_a_tool(self):
        for text in ("How much did we sell this month?", "كم بعنا هذا الشهر؟"):
            with self.subTest(text=text):
                answer = self.answer(text, self.no_sales)
                self.assertIn("You do not have access to", answer)
                self.assertNotIn("sale.order", answer)
                self.assertNotIn("There is no model", answer)

    def test_sec04_a_person_with_the_rights_gets_the_figure(self):
        seller = new_test_user(self.env, login="nudge_with_sales", groups="nudge.group_nudge_agent,sales_team.group_sale_manager")
        answer = self.answer("How much did we sell this month?", seller)
        self.assertNotIn("You do not have access", answer)

    def test_sec05_a_total_of_nothing_is_what_they_can_see(self):
        seller = new_test_user(self.env, login="nudge_seller_0", groups="nudge.group_nudge_agent,sales_team.group_sale_salesman")
        answer = self.answer(f"What is the total sales of {self.carol.name}?", seller)
        self.assertIn("You can see no", answer)
        self.assertNotIn(": 0", answer)

    def test_sec05_the_system_rule_is_in_both_modes(self):
        for persona in (prompt.SYSTEM_PERSONA, prompt.SYSTEM_PERSONA_COMPACT):
            self.assertIn('is "you can see none", never "there are none"', persona)

    def told_for(self, user, tool, arguments):
        adapter = self.use_adapter(script=[asking(call(tool, arguments)), saying(SAID)])
        conversation = self.env["nudge.conversation"].with_user(user)._chat_open(None)
        job = conversation._chat_prepare("How many are there?", mode="agent")
        list(loop.run(job, conversation._chat_record))
        return adapter.calls[-1]["turns"][-1]["results"][0]["content"]

    def test_a_read_that_finds_nothing_says_it_is_only_what_the_person_may_read(self):
        from odoo.addons.nudge.tools.records import SEES_NONE
        nothing = {"model": "res.partner", "conditions": [{"field": "name", "operator": "contains", "value": "zz-none-zz"}]}
        some = {"model": "res.partner", "conditions": [{"field": "name", "operator": "contains", "value": "Nudge Carol"}]}
        zero_sum = {"model": "res.partner", "conditions": nothing["conditions"], "measures": ["count"]}
        admin = new_test_user(self.env, login="nudge_sees_all", groups="nudge.group_nudge_agent,base.group_system")
        for user in (self.agent, admin):
            with self.subTest(user=user.login):
                self.assertIn(SEES_NONE, self.told_for(user, "search_records", nothing))
                self.assertIn(SEES_NONE, self.told_for(user, "aggregate_records", zero_sum))
                self.assertNotIn(SEES_NONE, self.told_for(user, "search_records", some))
                self.assertNotIn(SEES_NONE, self.told_for(
                    user, "aggregate_records", {"model": "res.partner", "conditions": some["conditions"]}))
        self.assertIn("never that there are none", SEES_NONE)
        self.assertIn("do not quote record ids", SEES_NONE)

    def test_sec01_what_odoo_fills_offers_no_other_way(self):
        adapter = self.use_adapter(script=[asking(call("prepare_write", {
            "model": "res.partner", "ids": self.carol.ids, "values": values(contact_address="x")})), saying(SAID)])
        conversation = self.env["nudge.conversation"].with_user(self.agent)._chat_open(None)
        job = conversation._chat_prepare("Can you set the address of Nudge Carol to x?", mode="agent")
        list(loop.run(job, conversation._chat_record))
        told = adapter.calls[-1]["turns"][-1]["results"][0]["content"]
        self.assertIn("is filled by Odoo", told)
        self.assertIn("do not offer a button or another way", told)
        self.assertNotIn("contact_address", told.replace("Name the field", ""), "The field is named by its label")
        label = self.env["res.partner"]._fields["contact_address"].get_description(self.env, ["string"])["string"]
        self.assertIn(repr(label), told)
        self.assertIn("never by its technical name", told)

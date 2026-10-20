from types import SimpleNamespace

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.models.nudge_conversation import DEVELOPER_PARAM
from odoo.addons.nudge.tools import approval_flow, approval_sentence

from .common import NudgeCase, require_modules

SENTENCES = [
    # English
    ("Confirming a sales order above 50,000 needs the sales manager",
     "sale.order", [(50000, "sales_team.group_sale_manager")]),
    ("Any purchase order over 50k needs the finance manager approval before confirmation",
     "purchase.order", [(50000, "account.group_account_manager")]),
    ("Purchase orders above 20000 need the purchase manager, then the CEO above 200,000",
     "purchase.order", [(20000, "purchase.group_purchase_manager"), (200000, None)]),
    ("Posting a vendor bill over 10,000 requires approval from the accounting manager",
     "account.move", [(10000, "account.group_account_manager")]),
    ("Quotations of more than 5k must be approved by the sales manager before they are confirmed",
     "sale.order", [(5000, "sales_team.group_sale_manager")]),
    ("Before confirming any sales order exceeding 1,000,000, the general manager must approve",
     "sale.order", [(1000000, None)]),
    # Arabic: Levantine, MSA, Egyptian, Gulf, Iraqi
    ("أي طلب شراء فوق 50 ألف بدو موافقة المدير المالي قبل التأكيد",
     "purchase.order", [(50000, "account.group_account_manager")]),
    ("تأكيد أمر البيع أكبر من 50,000 يحتاج موافقة مدير المبيعات",
     "sale.order", [(50000, "sales_team.group_sale_manager")]),
    ("اي امر شراء اكثر من ٢٠ الف لازم يوافق عليه مدير المشتريات وبعدين المدير العام لو فوق ٢٠٠ الف",
     "purchase.order", [(20000, "purchase.group_purchase_manager"), (200000, None)]),
    ("طلبات الشراء اللي فوق 50000 تبي موافقة المدير المالي قبل التأكيد",
     "purchase.order", [(50000, "account.group_account_manager")]),
    ("فاتورة المورد فوق 10 آلاف تحتاج موافقة المحاسب قبل الترحيل",
     "account.move", [(10000, "account.group_account_manager")]),
    ("أي عرض سعر يتعدى 100 الف ضروري يوافق مدير المبيعات",
     "sale.order", [(100000, "sales_team.group_sale_manager")]),
]


class TestApprovalSentence(TransactionCase):
    def test_twelve_sentences_are_read_without_ai(self):
        for sentence, model, levels in SENTENCES:
            parsed = approval_sentence.parse(sentence)
            self.assertTrue(parsed, sentence)
            self.assertEqual(parsed["model"], model, sentence)
            self.assertEqual([(level["amount"], level["group"]) for level in parsed["levels"]], levels, sentence)

    def test_what_is_not_an_approval_rule_is_left_alone(self):
        for sentence in ("Create a purchase order for Raghad", "كم عدد طلبات الشراء؟", "hello", "",
                         "Any purchase order above 50k"):
            self.assertIsNone(approval_sentence.parse(sentence), sentence)


@tagged("post_install", "-at_install")
class TestApprovalFlow(NudgeCase, TransactionCase):
    """An approval flow from a sentence, made by the builders of Developer mode, after a yes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        require_modules(cls, "account", "purchase")
        cls.admin = new_test_user(
            cls.env, login="nudge_flow_admin",
            groups="nudge.group_nudge_agent,base.group_system,base.group_partner_manager,"
                   "purchase.group_purchase_manager,sales_team.group_sale_manager,account.group_account_manager")
        cls.buyer = new_test_user(cls.env, login="nudge_flow_buyer", groups="base.group_user,purchase.group_purchase_user")
        cls.finance = new_test_user(
            cls.env, login="nudge_flow_finance", groups="base.group_user,account.group_account_manager")
        cls.env["ir.config_parameter"].sudo().set_param(DEVELOPER_PARAM, "yes")
        cls.supplier = cls.env["res.partner"].create({"name": "Raghad Supplies"})
        cls.product = cls.env["product.product"].create({"name": "Ebrahim Cement"})

    def setUp(self):
        # Fields made in a test go with its rollback: the registry forgets them after it (cleanups run last in, first out)
        self.addCleanup(self.registry.reset_changes)
        super().setUp()
        if "base.automation" not in self.env or "purchase.order" not in self.env:
            self.skipTest("Automation Rules and Purchase are needed")
        conversation = self.env["nudge.conversation"].with_user(self.admin)._chat_open()
        self.run_ = self.env["nudge.run"].sudo().create({
            "name": "Approval flow", "conversation_id": conversation.id, "user_id": self.admin.id,
            "is_developer": True})
        self.dev = self.env(user=self.admin, context={"nudge_mode": "dev", "nudge_conversation_id": conversation.id})

    def ask(self, arguments):  # noqa: D401 - the card, as the model asks for it
        return tools.base.call(self.dev, "prepare_approval_flow", arguments, None, "dev")

    def make(self, arguments=None):
        asked = self.ask(arguments or {"sentence": SENTENCES[1][0]})
        self.assertFalse(asked.is_error, asked.content)
        tool = tools.base.TOOLS["prepare_approval_flow"]
        step = SimpleNamespace(values=asked.pending["values"], res_model=asked.pending["res_model"], res_ids=[],
                               run_id=self.run_, call_id="flow")
        done = tool.execute(self.dev, step)
        self.assertFalse(done.is_error, done.content)
        model, ids, _names = done.records
        self.env["nudge.step"].sudo().create({
            "run_id": self.run_.id, "tool": "prepare_approval_flow", "kind": "create", "state": "done",
            "res_model": model, "res_ids": ids})
        return asked, done

    def order(self, amount, user=None):
        return self.env["purchase.order"].with_user(user or self.buyer).create({
            "partner_id": self.supplier.id,
            "order_line": [(0, 0, {"product_id": self.product.id, "name": "Cement", "product_qty": 1,
                                   "price_unit": amount})]})

    def action(self, key, order, user):
        name = {"request": "request approval", "approve": ": approve", "refuse": ": refuse"}[key]
        action = self.env["ir.actions.server"].sudo().search([
            ("model_name", "=", "purchase.order"), ("name", "ilike", name), ("name", "ilike", "Nudge approval")], limit=1)
        self.assertTrue(action, key)
        return action.with_user(user).with_context(
            active_model="purchase.order", active_ids=order.ids, active_id=order.id).run()

    # ------------------------------------------------------------ the card
    def test_the_card_says_in_words_what_will_be_built_and_nothing_is_built_before_the_yes(self):
        rules_before = self.env["base.automation"].sudo().search_count([])
        asked = self.ask({"sentence": SENTENCES[2][0]})
        self.assertFalse(asked.is_error, asked.content)
        lines = {line["label"]: line["value"] for line in asked.pending["preview"]["lines"]}
        self.assertIn("Level 1", lines)
        self.assertIn("Level 2", lines)
        self.assertIn("20,000", lines["Level 1"])
        self.assertIn("200,000", lines["Level 2"])
        for label in ("Stops", "Asks", "Buttons", "Fields added", "After the yes"):
            self.assertIn(label, lines)
        self.assertEqual(self.env["base.automation"].sudo().search_count([]), rules_before)
        self.assertNotIn(approval_flow.STATE_FIELD, self.env["purchase.order"]._fields)

    def test_only_an_administrator_in_developer_mode_is_offered_the_tool(self):
        offered = [tool.name for tool in tools.base.available(self.env(user=self.buyer), "dev")]
        self.assertNotIn("prepare_approval_flow", offered)
        self.assertIn("prepare_approval_flow", [t.name for t in tools.base.available(self.dev, "dev")])

    def test_a_role_named_in_words_resolves_to_the_group_of_that_app(self):
        for text, xmlid in (
                ("purchase manager", "purchase.group_purchase_manager"),
                ("the purchase administrator", "purchase.group_purchase_manager"),
                ("manager of purchase", "purchase.group_purchase_manager"),
                ("sales manager", "sales_team.group_sale_manager"),
                ("مدير المشتريات", "purchase.group_purchase_manager")):
            if not self.env.ref(xmlid, raise_if_not_found=False):
                continue
            group, note = approval_flow.find_group(self.env, text)
            self.assertEqual(group, self.env.ref(xmlid), text)
            self.assertFalse(note, text)

    def test_a_role_of_an_unknown_app_is_reported_and_not_replaced_silently(self):
        group, note = approval_flow.find_group(self.env, "Raghad Ebrahim Foundry manager")
        self.assertIn("Raghad Ebrahim Foundry manager", note)
        self.assertIn("No group is named", note)

    def test_a_sentence_that_is_no_rule_is_refused_in_words(self):
        refused = self.ask({"sentence": "Create a purchase order for Raghad"})
        self.assertTrue(refused.is_error)
        self.assertIn("not read as an approval rule", refused.content)

    # ------------------------------------------------------------ built, blocked, allowed
    def test_confirmation_is_blocked_until_the_approver_approves(self):
        _asked, done = self.make()
        self.assertIn("Blocked, as it should be", done.content)
        Order = self.env["purchase.order"]
        for name in (approval_flow.STATE_FIELD, approval_flow.APPROVER_FIELD, approval_flow.LEVEL_FIELD):
            self.assertIn(name, Order._fields)
        small = self.order(1000)
        small.button_confirm()
        self.assertEqual(small.state, "purchase", "Under the amount nothing changes")

        big = self.order(60000)
        with self.assertRaises(UserError) as stopped:
            big.button_confirm()
        self.assertIn("Request approval", str(stopped.exception))
        self.assertEqual(big.state, "draft")

        self.action("request", big, self.buyer)
        self.assertEqual(big.x_nudge_approval_state, "pending")
        activities = self.env["mail.activity"].search([
            ("res_model", "=", "purchase.order"), ("res_id", "=", big.id), ("user_id", "=", self.finance.id)])
        self.assertEqual(len(activities), 1)
        self.assertIn(approval_flow.APPROVAL_TAG, activities.summary)
        self.assertIn("approval", " ".join(big.message_ids.mapped("body")).lower())
        with self.assertRaises(UserError):
            big.button_confirm()

        # the one who is not an approver cannot approve
        with self.assertRaises(UserError):
            self.action("approve", big, self.buyer)
        self.action("approve", big, self.finance)
        self.assertEqual((big.x_nudge_approval_state, big.x_nudge_approver_id), ("approved", self.finance))
        self.assertFalse(self.env["mail.activity"].search([
            ("res_model", "=", "purchase.order"), ("res_id", "=", big.id), ("user_id", "=", self.finance.id)]))
        big.button_confirm()
        self.assertEqual(big.state, "purchase")

    def test_the_approval_fields_cannot_be_written_to_skip_the_approval(self):
        self.make()
        big = self.order(60000)
        for context in ({}, {"nudge_approval_step": True}, {"nudge_approval_step": "guess"}):
            with self.subTest(context=context), self.assertRaises(UserError):
                big.with_user(self.buyer).with_context(**context).write({
                    approval_flow.LEVEL_FIELD: 1, approval_flow.STATE_FIELD: "approved"})
        self.assertFalse(big.x_nudge_approval_state)
        with self.assertRaises(UserError):
            big.button_confirm()

    def test_a_refusal_stops_it_and_a_copy_does_not_inherit_the_approval(self):
        self.make()
        order = self.order(70000)
        self.action("request", order, self.buyer)
        self.action("refuse", order, self.finance)
        self.assertEqual(order.x_nudge_approval_state, "refused")
        with self.assertRaises(UserError) as stopped:
            order.button_confirm()
        self.assertIn("refused", str(stopped.exception))
        self.action("request", order, self.buyer)
        self.action("approve", order, self.finance)
        self.assertEqual(order.copy().x_nudge_approval_state, False)

    def test_two_levels_are_asked_in_order(self):
        boss = new_test_user(self.env, login="nudge_flow_boss", groups="base.group_user,base.group_erp_manager,purchase.group_purchase_manager")
        self.make({"model": "purchase.order", "levels": [
            {"amount": 50000, "group": "account.group_account_manager"},
            {"amount": 200000, "group": "base.group_erp_manager"}]})
        order = self.order(300000)
        self.action("request", order, self.buyer)
        self.action("approve", order, self.finance)
        self.assertEqual((order.x_nudge_approval_state, order.x_nudge_approval_level), ("pending", 1))
        with self.assertRaises(UserError):
            order.button_confirm()
        self.assertTrue(self.env["mail.activity"].search([
            ("res_model", "=", "purchase.order"), ("res_id", "=", order.id), ("user_id", "=", boss.id)]))
        with self.assertRaises(UserError):
            self.action("approve", order, self.finance)
        self.action("approve", order, boss)
        order.button_confirm()
        self.assertEqual(order.state, "purchase")

    def test_the_generated_actions_lock_the_row_before_they_read_it(self):
        """Release-2 review S7: two approvers at the same instant are told one after the other"""
        self.make({"model": "purchase.order", "levels": [{"amount": 50000, "group": "account.group_account_manager"}]})
        for key in ("request", "approve", "refuse"):
            name = {"request": "request approval", "approve": ": approve", "refuse": ": refuse"}[key]
            action = self.env["ir.actions.server"].sudo().search([
                ("model_name", "=", "purchase.order"), ("name", "ilike", name), ("name", "ilike", "Nudge approval")],
                limit=1)
            code = action.code
            lock = code.index("record.write({'x_nudge_approval_state': record.x_nudge_approval_state})")
            self.assertLess(lock, code.index("x_nudge_approval_level"), key)
            self.assertIn("record.invalidate_recordset()", code)
        # a second press after the first one is read as it is, not as it was
        order = self.order(80000)
        self.action("request", order, self.buyer)
        self.action("approve", order, self.finance)
        with self.assertRaises(UserError):
            self.action("approve", order, self.finance)
        self.assertEqual(order.x_nudge_approval_level, 1)

    def test_buttons_are_on_the_form_and_the_approvers_only_see_approve(self):
        self.make()
        arch = self.env["purchase.order"].with_user(self.admin).get_view(False, "form")["arch"]
        self.assertIn("Request approval", arch)
        self.assertIn("Approve", arch)
        views = self.env["ir.ui.view"].sudo().search([
            ("model", "=", "purchase.order"), ("arch_db", "like", "Nudge approval")], limit=1)
        approve = self.env["ir.ui.view"].sudo().search([
            ("model", "=", "purchase.order"), ("name", "ilike", "Button Approve")], limit=1)
        self.assertTrue(approve)
        self.assertIn("groups=", approve.arch_db)
        self.assertFalse(views and views.name == "")

    # ------------------------------------------------------------ undone as a set
    def test_the_whole_set_is_undone_in_one_go(self):
        self.make()
        Server = self.env["ir.actions.server"].sudo()
        Automation = self.env["base.automation"].sudo()
        self.assertTrue(Automation.search([("name", "=like", "Nudge approval:%")]))
        self.assertEqual(Server.search_count([("name", "=like", "Nudge approval:%"), ("model_name", "=", "purchase.order")]) >= 3, True)
        asked = tools.base.call(self.dev, "prepare_undo", {}, None, "dev")
        self.assertFalse(asked.is_error, asked.content)
        self.assertGreaterEqual(len(asked.pending["preview"]["records"]), 10)
        done = tools.base.TOOLS["prepare_undo"].execute(self.dev, SimpleNamespace(
            values=asked.pending["values"], res_model="nudge.run", res_ids=asked.pending["res_ids"]))
        self.assertFalse(done.is_error, done.content)
        self.assertFalse(Automation.search([("name", "=like", "Nudge approval:%")]))
        self.assertFalse(Server.search([("name", "=like", "Nudge approval:%"), ("model_name", "=", "purchase.order")]))
        self.assertNotIn(approval_flow.STATE_FIELD, self.env["purchase.order"]._fields)
        self.assertNotIn("Request approval", self.env["purchase.order"].with_user(self.admin).get_view(False, "form")["arch"])
        # and the purchase order confirms as before
        self.order(900000).button_confirm()

    def test_a_second_flow_on_the_same_model_is_refused_until_the_first_is_undone(self):
        self.make()
        again = self.ask({"sentence": SENTENCES[1][0]})
        self.assertTrue(again.is_error)
        self.assertIn("already", again.content)

    # ------------------------------------------------------------ the way in: a sentence, no AI
    def test_a_sentence_in_developer_mode_gets_a_card_without_any_ai_and_a_yes_builds_it(self):
        from odoo.addons.nudge.llm import loop
        self.provider.active = False
        self.env["ir.config_parameter"].sudo().set_param("nudge.noai_first", "1")
        conversation = self.env["nudge.conversation"].with_user(self.admin)._chat_open()
        for sentence in (SENTENCES[6][0], SENTENCES[1][0]):
            job = conversation._chat_prepare(sentence, mode="dev")
            events = list(loop.run(job, conversation._chat_record))
            cards = [event for event in events if event["type"] == "confirm"]
            self.assertEqual(len(cards), 1, events)
            self.assertIn("approval flow", cards[0]["title"])
            self.assertEqual(events[-1]["type"], "waiting")
            self.assertNotIn(approval_flow.STATE_FIELD, self.env["purchase.order"]._fields)
            if sentence == SENTENCES[6][0]:
                # Said no: nothing is built, and the next sentence is a new request
                job = conversation._agent_decide(conversation.sudo().waiting_step_id.id, "decline")
                list(loop.run(job, conversation._chat_record))
        job = conversation._agent_decide(conversation.sudo().waiting_step_id.id, "confirm")
        done = list(loop.run(job, conversation._chat_record))
        self.assertEqual(done[-1]["type"], "done", done)
        self.assertIn(approval_flow.STATE_FIELD, self.env["purchase.order"]._fields)
        self.assertTrue(self.env["base.automation"].search([("name", "=like", "Nudge approval:%")]))
        # and it is one request of What Nudge Did: undone in one go
        run = conversation.sudo().run_id or self.env["nudge.run"].sudo().search([("conversation_id", "=", conversation.id)], limit=1)
        steps = self.env["nudge.step"].sudo().search([("run_id", "in", self.env["nudge.run"].sudo().search([("conversation_id", "=", conversation.id)]).ids), ("kind", "=", "create"), ("state", "=", "done")])
        self.assertGreaterEqual(len(steps), 8, run)

    # ------------------------------------------------------------ Studio
    def test_with_studio_installed_its_rules_are_used_instead(self):
        self.patch(approval_flow, "studio_here", lambda env: True)
        asked = self.ask({"sentence": SENTENCES[2][0]})
        self.assertFalse(asked.is_error, asked.content)
        lines = {line["label"]: line["value"] for line in asked.pending["preview"]["lines"]}
        self.assertIn("Studio", lines["How"])
        model = self.env["purchase.order"]
        builds = tools.base.TOOLS["prepare_approval_flow"]._plan(
            self.dev, model, "purchase_order", approval_flow.PROFILES["purchase_order"],
            [{"amount": 20000, "group": self.env.ref("purchase.group_purchase_manager")},
             {"amount": 200000, "group": self.env.ref("base.group_system")}])
        self.assertEqual([build["tool"] for build in builds], ["prepare_approval_rule"] * 2)
        self.assertEqual([build["arguments"]["step"] for build in builds], [1, 2])
        self.assertEqual(builds[0]["arguments"]["method"], "button_confirm")

    def test_the_real_studio_rule_is_made_when_studio_is_installed(self):
        if "studio.approval.rule" not in self.env or "prepare_approval_rule" not in tools.base.TOOLS:
            self.skipTest("Studio approval rules are not installed here")
        self.make({"sentence": SENTENCES[1][0]})
        self.assertTrue(self.env["studio.approval.rule"].sudo().search([("model_name", "=", "purchase.order")]))

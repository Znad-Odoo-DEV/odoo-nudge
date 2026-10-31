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

    def test_a_vendor_invoice_is_a_vendor_bill_not_a_customer_invoice(self):
        for sentence in ("Vendor invoices above 10,000 need the CFO", "Any supplier invoice over 10k needs the finance manager",
                         "A purchase invoice above 10000 requires approval from the accounting manager"):
            parsed = approval_sentence.parse(sentence)
            self.assertTrue(parsed, sentence)
            self.assertEqual(parsed["kind"], "vendor_bill", sentence)
        self.assertEqual(approval_sentence.parse("Customer invoices above 10,000 need the CFO")["kind"], "customer_invoice")
        self.assertEqual(approval_sentence.parse("Invoices above 10,000 need the CFO")["kind"], "customer_invoice")

    def test_what_is_not_an_approval_rule_is_left_alone(self):
        for sentence in ("Create a purchase order for Raghad", "كم عدد طلبات الشراء؟", "hello", "",
                         "Any purchase order above 50k", "كم عدد أوامر الشراء؟", "أوامر الشراء فوق 50,000"):
            self.assertIsNone(approval_sentence.parse(sentence), sentence)

    def test_the_article_on_any_word_of_an_arabic_phrase(self):
        # R3-23: "أوامر الشراء ... مدير المشتريات" (the shot list) was not read: only "اوامر شراء" was known, so the
        # AI answered in place of the switch to Developer mode
        for sentence, model, levels in (
            ("أوامر الشراء فوق 50,000 تحتاج موافقة مدير المشتريات",
             "purchase.order", [(50000, "purchase.group_purchase_manager")]),
            ("أوامر المبيعات فوق 50,000 تحتاج موافقة مدير المبيعات",
             "sale.order", [(50000, "sales_team.group_sale_manager")]),
            ("أوامر البيع فوق 50 ألف تحتاج موافقة مدير المبيعات",
             "sale.order", [(50000, "sales_team.group_sale_manager")]),
            ("أوامر الشراء فوق ٥٠٬٠٠٠ تحتاج موافقة مدير مشتريات",
             "purchase.order", [(50000, "purchase.group_purchase_manager")]),
            ("طلبات الشراء التي تزيد عن 50000 تتطلب موافقة مدير المشتريات",
             "purchase.order", [(50000, "purchase.group_purchase_manager")]),
            ("فواتير الموردين فوق 10,000 تحتاج اعتماد المدير المالي",
             "account.move", [(10000, "account.group_account_manager")]),
            ("عروض الأسعار فوق 20 ألف لازم يوافق عليها مدير المبيعات",
             "sale.order", [(20000, "sales_team.group_sale_manager")]),
        ):
            with self.subTest(sentence=sentence):
                parsed = approval_sentence.parse(sentence)
                self.assertTrue(parsed, sentence)
                self.assertEqual(parsed["model"], model, sentence)
                self.assertEqual([(level["amount"], level["group"]) for level in parsed["levels"]], levels, sentence)
        self.assertEqual(approval_sentence.parse("فواتير الموردين فوق 10,000 تحتاج اعتماد المدير المالي")["kind"],
                         "vendor_bill")


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
        # No purchase tax: the amounts compared are those of the lines, whatever taxes the database adds by default
        cls.product = cls.env["product.product"].create({"name": "Ebrahim Cement", "supplier_taxes_id": [(6, 0, [])]})

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

    def test_a_role_said_in_arabic_or_english_reaches_its_own_group_and_never_another(self):
        """Studio filming: "مدير النظام" produced Sales / Manager. The reading is shared with the Studio card."""
        for text, xmlid in (
                ("مدير النظام", "base.group_system"), ("system administrator", "base.group_system"),
                ("مسؤول النظام", "base.group_system"),
                ("مدير المبيعات", "sales_team.group_sale_manager"), ("the sales manager", "sales_team.group_sale_manager"),
                ("المدير المالي", "account.group_account_manager"), ("the finance manager", "account.group_account_manager"),
                ("مدير المشتريات", "purchase.group_purchase_manager")):
            expected = self.env.ref(xmlid, raise_if_not_found=False)
            if not expected:
                continue
            self.assertEqual(approval_flow.role_group(self.env, text), expected, text)
        # when unsure it finds nothing, so that the caller asks
        for text in ("المدير", "manager", "مدير المبيعات والمشتريات", "Raghad Ebrahim Foundry manager", ""):
            self.assertFalse(approval_flow.role_group(self.env, text), text)
        # a role word and a sales word never mix
        sales = self.env.ref("sales_team.group_sale_manager", raise_if_not_found=False)
        if sales:
            self.assertNotEqual(approval_flow.role_group(self.env, "مدير النظام"), sales)
            self.assertNotEqual(approval_flow.role_group(self.env, "مدير المبيعات"), self.env.ref("base.group_system"))

    def test_the_words_of_a_card_shared_by_the_modules_are_in_the_language_of_the_person(self):
        """Studio filming: Model, Button and Only for stayed English on the approval rule card in Arabic."""
        from odoo.addons.nudge.tools import building
        self.env["res.lang"]._activate_lang("ar_001")
        self.env["ir.module.module"].search([("name", "=", "nudge")])._update_translations(["ar_001"])
        english = building.card_labels(self.env(context=dict(self.env.context, lang="en_US")))
        arabic = building.card_labels(self.env(context=dict(self.env.context, lang="ar_001")))
        self.assertEqual(english, {"model": "Model", "button": "Button", "only_for": "Only for"})
        for key, word in arabic.items():
            self.assertNotEqual(word, english[key], key)
            self.assertRegex(word, "[؀-ۿ]", key)

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

    def test_an_approval_holds_for_the_amount_it_was_given_for(self):
        """Review (functional 1): approved at 60,000 and edited to 5,000,000 must be asked again"""
        self.make({"model": "purchase.order", "levels": [{"amount": 50000, "group": "account.group_account_manager"}]})
        order = self.order(60000)
        self.action("request", order, self.buyer)
        self.action("approve", order, self.finance)
        self.assertAlmostEqual(order.x_nudge_approved_amount, 60000, places=2)
        order.order_line.price_unit = 5000000
        with self.assertRaises(UserError) as stopped:
            order.button_confirm()
        self.assertIn("approved", str(stopped.exception))
        self.assertEqual(order.state, "draft")
        # asked again, approved again, then it goes through
        self.action("request", order, self.buyer)
        self.assertEqual(order.x_nudge_approval_state, "pending")
        self.action("approve", order, self.finance)
        order.button_confirm()
        self.assertEqual(order.state, "purchase")
        # a smaller amount than the approved one is still approved
        other = self.order(60000)
        self.action("request", other, self.buyer)
        self.action("approve", other, self.finance)
        other.order_line.price_unit = 55000
        other.button_confirm()
        self.assertEqual(other.state, "purchase")

    def test_the_limit_is_in_the_currency_of_the_company(self):
        self.make({"model": "purchase.order", "levels": [{"amount": 50000, "group": "account.group_account_manager"}]})
        company = self.env.company
        foreign = self.env["res.currency"].with_context(active_test=False).search(
            [("id", "!=", company.currency_id.id)], limit=1)
        foreign.active = True
        self.env["res.currency.rate"].create({
            "currency_id": foreign.id, "company_id": company.id, "rate": 0.5, "name": "2000-01-01"})
        # 30,000 of the foreign currency are 60,000 of the company's
        big = self.order(30000)
        big.currency_id = foreign
        # On the date of this rate: the rates the database holds for today are not those of the test
        big.date_order = "2000-06-01"
        with self.assertRaises(UserError) as stopped:
            big.button_confirm()
        self.assertIn(company.currency_id.name, str(stopped.exception))
        # at a rate of 4 of the foreign currency to one of the company's, 100,000 are 25,000 and pass
        self.env["res.currency.rate"].create({
            "currency_id": foreign.id, "company_id": company.id, "rate": 4, "name": "2001-01-01"})
        small = self.order(100000)
        small.currency_id = foreign
        small.date_order = "2001-06-01"
        small.button_confirm()
        self.assertEqual(small.state, "purchase")

    def test_the_system_and_the_visitor_are_not_stopped_but_the_person_is(self):
        self.make({"model": "purchase.order", "levels": [{"amount": 50000, "group": "account.group_account_manager"}]})
        portal = new_test_user(self.env, login="nudge_flow_portal", groups="base.group_portal")
        big = self.order(60000)
        with self.assertRaises(UserError):
            big.button_confirm()
        # the scheduled actions run as OdooBot, a website payment as the visitor or the portal customer
        big.with_user(portal).sudo().button_confirm()
        self.assertEqual(big.state, "purchase")
        again = self.order(70000)
        again.with_user(1).button_confirm()
        self.assertEqual(again.state, "purchase")

    def test_who_made_the_document_does_not_approve_it_unless_alone(self):
        maker = new_test_user(self.env, login="nudge_flow_maker", groups="base.group_user,purchase.group_purchase_user")
        solo = self.env["res.groups"].create({"name": "Nudge solo approvers"})
        users_field = approval_flow.users_field(self.env)
        solo[users_field] = [(6, 0, [maker.id])]
        self.make({"model": "purchase.order", "levels": [{"amount": 50000, "group": "Nudge solo approvers"}]})
        mine = self.order(60000, user=maker)
        self.action("request", mine, maker)
        # the only one of the group: allowed
        self.action("approve", mine, maker)
        self.assertEqual(mine.x_nudge_approval_state, "approved")
        # with a second member, the maker is refused and the other approves
        second = new_test_user(self.env, login="nudge_flow_second", groups="base.group_user,purchase.group_purchase_user")
        solo[users_field] = [(4, second.id)]
        theirs = self.order(60000, user=maker)
        self.action("request", theirs, maker)
        with self.assertRaises(UserError) as stopped:
            self.action("approve", theirs, maker)
        self.assertIn("You made this document", str(stopped.exception))
        self.action("approve", theirs, second)
        theirs.button_confirm()
        self.assertEqual(theirs.state, "purchase")

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

    # ------------------------------------------------------------ each user reads it in their own language
    def _two_languages(self):
        self.env["res.lang"]._activate_lang("ar_001")
        self.env["ir.module.module"].search([("name", "=", "nudge")])._update_translations(["ar_001"])
        english, arabic = self.env(context=dict(self.env.context, lang="en_US")), self.env(context=dict(self.env.context, lang="ar_001"))
        self.assertNotEqual(arabic._("Request approval"), "Request approval", "the Arabic of the button is needed")
        return english, arabic

    def _in_each_language(self, sentence, builder_lang):
        english, arabic = self._two_languages()
        group = self.env.ref("account.group_account_manager")
        group.update_field_translations("name", {"ar_001": "مدير الحسابات لهذا الفحص"})
        self.dev = self.dev(context=dict(self.dev.context, lang=builder_lang))
        self.make({"sentence": sentence})
        Order = self.env["purchase.order"].with_user(self.admin)
        for lang, env in (("en_US", english), ("ar_001", arabic)):
            other = arabic if lang == "en_US" else english
            arch = Order.with_context(lang=lang).get_view(False, "form")["arch"]
            for source in ("Request approval", "Approve", "Refuse"):
                self.assertIn('string="%s"' % env._(source), arch, (lang, source))
                self.assertNotIn('string="%s"' % other._(source), arch, (lang, source))
            field = self.env["ir.model.fields"].sudo().with_context(lang=lang).search([
                ("model", "=", "purchase.order"), ("name", "=", approval_flow.STATE_FIELD)])
            self.assertEqual(field.field_description, env._("Approval"), lang)
            selection = field.selection_ids.filtered(lambda choice: choice.value == "refused")
            self.assertEqual(selection.name, env._("Refused"), lang)
            action = self.env["ir.actions.server"].sudo().with_context(lang=lang).search([
                ("model_name", "=", "purchase.order"), ("name", "=like", "Nudge approval: %approval of a%")
                if lang == "en_US" else ("name", "ilike", env._("%(prefix)s: request approval of a %(doc)s",
                                                                  prefix="Nudge approval", doc=env._("purchase order")))],
                limit=1)
            self.assertTrue(action, lang)
            self.assertEqual(action.name, env._("%(prefix)s: request approval of a %(doc)s",
                                                 prefix="Nudge approval", doc=env._("purchase order")), lang)
        # the refusal of a confirmation is in the language of the person refused
        big = self.order(60000)
        for lang, env in (("en_US", english), ("ar_001", arabic)):
            with self.assertRaises(UserError) as stopped:
                big.with_user(self.buyer).with_context(lang=lang).button_confirm()
            said = str(stopped.exception)
            self.assertIn(env._("needs the approval of"), said, lang)
            self.assertEqual("مدير الحسابات لهذا الفحص" in said, lang == "ar_001", said)
            self.assertIn(env._("before it can be %(done)s. Press Request approval; they approve it from the form.",
                                done=env._("confirmed")), said, lang)
        # and what is written for the base source stays English for whoever has no translation
        self.assertEqual(self.env["ir.actions.server"].sudo().with_context(lang="en_US").search([
            ("model_name", "=", "purchase.order"), ("name", "=like", "Nudge approval: approve%")]).mapped("name"),
            ["Nudge approval: approve a purchase order"])

    def test_a_flow_from_an_arabic_sentence_is_read_in_the_language_of_each_user(self):
        self._in_each_language("أي طلب شراء فوق 50 ألف بدو موافقة المدير المالي قبل التأكيد", "ar_001")

    def test_a_flow_from_an_english_sentence_is_read_in_the_language_of_each_user(self):
        self._in_each_language(SENTENCES[1][0], "en_US")

    def test_a_flow_from_an_english_sentence_by_an_arabic_user_is_read_in_the_language_of_each_user(self):
        self._in_each_language(SENTENCES[1][0], "ar_001")

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
        # H20: the person may read it as it is, with no model in between
        self.assertNotIn("prepare_undo", again.content)
        self.assertNotIn("purchase.order", again.content)

    # ------------------------------------------------------------ orphan buttons
    def _buttons(self, active=True):
        return self.env["ir.ui.view"].sudo().with_context(active_test=False).search([
            ("model", "=", "purchase.order"), ("name", "like", "Button "), ("active", "=", active),
            ("name", "in", ["Button Request approval on Purchase Order", "Button Approve on Purchase Order",
                            "Button Refuse on Purchase Order"])])

    def test_deleting_the_actions_of_a_flow_switches_its_buttons_off(self):
        self.make()
        self.assertEqual(len(self._buttons()), 3)
        actions = self.env["ir.actions.server"].sudo().search([
            ("model_name", "=", "purchase.order"), ("name", "=like", "Nudge approval:%")])
        self.env["base.automation"].sudo().search([("name", "=like", "Nudge approval:%")]).unlink()
        actions.exists().unlink()
        self.assertFalse(self._buttons())
        self.assertEqual(len(self._buttons(False)), 3)
        self.env["purchase.order"].with_user(self.admin).get_view(False, "form")

    def test_a_button_of_another_action_is_left_alone(self):
        self.make()
        other = self.env["ir.actions.server"].sudo().create({
            "name": "Something else", "model_id": self.env["ir.model"]._get_id("purchase.order"),
            "state": "code", "code": "pass"})
        self.env["ir.actions.server"].sudo().search([
            ("model_name", "=", "purchase.order"), ("name", "=like", "Nudge approval: approve%")]).unlink()
        other.unlink()
        self.assertEqual(len(self._buttons()), 2)

    def test_a_new_flow_after_an_orphan_has_one_set_of_buttons(self):
        self.make()
        # the rules and the actions are deleted by hand, the buttons were left (as before the fix)
        self.env["base.automation"].sudo().search([("name", "=like", "Nudge approval:%")]).unlink()
        self.env["ir.actions.server"].sudo().search([
            ("model_name", "=", "purchase.order"), ("name", "=like", "Nudge approval:%")]).unlink()
        # Odoo refuses to switch such a view on again: written to the table, as an old database has it
        self.env.cr.execute("UPDATE ir_ui_view SET active = true WHERE id IN %s", [tuple(self._buttons(False).ids)])
        self.env["ir.ui.view"].invalidate_model(["active"])
        self.assertEqual(len(self._buttons()), 3)
        asked, done = self.make()
        self.assertEqual(len(self._buttons()), 3)
        self.assertIn("Old buttons replaced", str(done.card_lines))
        arch = self.env["purchase.order"].with_user(self.admin).get_view(False, "form")["arch"]
        self.assertEqual(arch.count('string="Approve"'), 1)
        # and the flow works
        self.assertTrue(self.action("request", self.order(900000), self.buyer) is not None)

    def test_a_second_build_over_a_live_flow_is_still_refused_and_keeps_its_buttons(self):
        self.make()
        self.assertTrue(self.ask({"sentence": SENTENCES[1][0]}).is_error)
        self.assertEqual(len(self._buttons()), 3)

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


@tagged("post_install", "-at_install")
class TestApprovalInAgentMode(NudgeCase, TransactionCase):
    """H20: an approval said in a sentence in Agent mode, where nothing builds it. The model is told that
    it asks for a flow, built in Developer mode, rather than for the records it would apply to."""

    SENTENCE = "Purchase orders above 50,000 need Purchase Manager approval"

    def sent(self, user, mode="agent"):
        from odoo.addons.nudge.llm import base as llm, loop
        adapter = self.use_adapter()
        conversation = self.env["nudge.conversation"].with_user(user)._chat_open()
        events = list(loop.run(conversation._chat_prepare(self.SENTENCE, mode=mode), conversation._chat_record))
        self.assertFalse([event for event in events if event["type"] == "confirm"], "Nothing to say yes to here")
        self.assertTrue(adapter.calls, "The model answers it")
        return "\n".join(llm.system_texts(adapter.calls[0]["system"])), adapter.calls[0]

    def test_the_model_is_told_it_is_built_in_developer_mode(self):
        self.env["ir.config_parameter"].sudo().set_param(DEVELOPER_PARAM, "yes")
        # The rules answer an approval sentence first (the next test); here the model is the one asked
        self.env["ir.config_parameter"].sudo().set_param("nudge.noai_first", "0")
        admin = new_test_user(self.env, login="nudge_h20_admin",
                              groups="nudge.group_nudge_agent,base.group_system,base.group_partner_manager")
        system, call = self.sent(admin)
        self.assertNotIn("prepare_approval_flow", [tool["name"] for tool in call["tools"]])
        rules = system[system.index("## Mode: Agent"):]
        self.assertIn("an approval rule or an approval flow", rules)
        self.assertIn("even when it reads as a statement", rules)
        self.assertIn("offer to switch", rules)
        # Someone who may not build is sent to their administrator, approvals included
        system, _call = self.sent(self.agent)
        self.assertIn("approval rules", system[system.index("## Mode: Agent"):])

    def test_the_rules_say_it_is_built_in_developer_mode_and_offer_the_switch(self):
        # Round 5: no call to a provider for a sentence the rules read as an approval rule
        from odoo.addons.nudge.llm import loop
        self.env["ir.config_parameter"].sudo().set_param(DEVELOPER_PARAM, "yes")
        self.env["ir.config_parameter"].sudo().set_param("nudge.noai_first", "1")
        admin = new_test_user(self.env, login="nudge_r5_admin",
                              groups="nudge.group_nudge_agent,base.group_system,base.group_partner_manager")
        for user, offered in ((admin, True), (self.agent, False)):
            with self.subTest(user=user.login):
                adapter = self.use_adapter()
                conversation = self.env["nudge.conversation"].with_user(user)._chat_open()
                events = list(loop.run(conversation._chat_prepare(self.SENTENCE, mode="agent"),
                                       conversation._chat_record))
                self.assertFalse(adapter.calls, "No provider is called")
                said = "".join(event.get("text", "") for event in events if event["type"] == "delta")
                self.assertIn("built in Developer mode", said)
                chips = [command for event in events if event["type"] == "found"
                         for command in event.get("commands") or []]
                self.assertEqual(bool(chips), offered, events)
                if offered:
                    self.assertEqual((chips[0]["kind"], chips[0]["mode"], chips[0]["text"]),
                                     ("switch_mode", "dev", self.SENTENCE))
        # Anything else is not taken for an approval rule
        adapter = self.use_adapter()
        conversation = self.env["nudge.conversation"].with_user(admin)._chat_open()
        list(loop.run(conversation._chat_prepare("hello", mode="agent"),
                      conversation._chat_record))
        self.assertTrue(adapter.calls)

    def test_the_arabic_sentence_gets_the_same_switch(self):
        # R3-23: the Arabic sentence of the shot list was answered by the AI (Gemini) or met with questions
        # (Claude), with no switch to Developer mode
        from odoo.addons.nudge.llm import loop
        self.env["ir.config_parameter"].sudo().set_param(DEVELOPER_PARAM, "yes")
        self.env["ir.config_parameter"].sudo().set_param("nudge.noai_first", "1")
        admin = new_test_user(self.env, login="nudge_r323_admin",
                              groups="nudge.group_nudge_agent,base.group_system,base.group_partner_manager")
        for sentence in ("أوامر الشراء فوق 50,000 تحتاج موافقة مدير المشتريات",
                         "أوامر المبيعات فوق 50,000 تحتاج موافقة مدير المبيعات"):
            with self.subTest(sentence=sentence):
                adapter = self.use_adapter()
                conversation = self.env["nudge.conversation"].with_user(admin)._chat_open()
                events = list(loop.run(conversation._chat_prepare(sentence, mode="agent"), conversation._chat_record))
                self.assertFalse(adapter.calls, "No provider is called")
                chips = [command for event in events if event["type"] == "found"
                         for command in event.get("commands") or []]
                self.assertEqual([(chip["kind"], chip["mode"], chip["text"]) for chip in chips],
                                 [("switch_mode", "dev", sentence)])

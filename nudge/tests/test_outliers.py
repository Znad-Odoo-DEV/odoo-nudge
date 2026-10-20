from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.tools import outliers

from .common import NudgeCase, require_modules

BILLS = [1000, 1010, 990, 1005, 995, 1000, 1010, 990, 1000, 1000]


@tagged("post_install", "-at_install")
class TestOutliers(NudgeCase, TransactionCase):
    """Unusual figures: robust statistics per group, as the person, no AI."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        require_modules(cls, "account", "hr", "hr_attendance", "hr_expense", "purchase", "stock")
        groups = ["nudge.group_nudge_manager", "nudge.group_nudge_user"] + [
            xmlid for xmlid in (
                "account.group_account_manager", "purchase.group_purchase_manager",
                "sales_team.group_sale_manager", "stock.group_stock_manager",
                "hr_attendance.group_hr_attendance_manager", "hr_expense.group_hr_expense_manager",
                "hr.group_hr_manager")
            if cls.env.ref(xmlid, raise_if_not_found=False)]
        cls.boss = new_test_user(cls.env, login="outlier_boss", groups=",".join(groups))
        cls.Move = cls.env["account.move"]
        cls.expense_account = cls.env["account.account"].search(
            [("account_type", "=", "expense")], limit=1)
        cls.supplier = cls.env["res.partner"].create({"name": "Raghad Supplies"})

    # ------------------------------------------------------------ helpers

    def bills(self, partner, amounts, **extra):
        moves = self.Move
        for amount in amounts:
            moves |= self.Move.create(dict({
                "move_type": "in_invoice", "partner_id": partner.id,
                "invoice_date": fields.Date.today(),
                "invoice_line_ids": [(0, 0, {"name": "Goods", "quantity": 1, "price_unit": amount,
                                             "account_id": self.expense_account.id, "tax_ids": False})],
            }, **extra))
        moves.action_post()
        return moves

    def found(self, check, key, user=None, k=None):
        """:return: the finding ``key`` of ``check`` as ``user``, or ``None``"""
        env = self.env(user=user or self.boss)
        if k:
            env = env(context=dict(env.context, nudge_outlier_k=k))
        items, skipped = tools.health.run_all(env, {check})
        self.assertEqual(skipped, [])
        return next((item for item in items if item["key"] == key), None)

    # ------------------------------------------------------------ the statistics

    def test_median_and_mad_are_not_moved_by_the_outlier(self):
        self.assertEqual(outliers.median([3, 1, 2]), 2)
        self.assertEqual(outliers.median([1, 2, 3, 10]), 2.5)
        self.assertIsNone(outliers.median([]))
        values = BILLS + [3400]
        self.assertEqual(outliers.median(values), 1000)
        self.assertLess(outliers.mad(values), 10)
        self.assertGreater(outliers.distance(3400, values, floor=1), 20)
        self.assertIsNone(outliers.distance(3400, values[:6]), "below the minimum sample nothing is judged")

    def test_the_sensitivity_is_two_three_or_four(self):
        env = self.env
        self.assertEqual(outliers.sensitivity(env), 3)
        self.assertEqual(outliers.sensitivity(env(context={'nudge_outlier_k': 2})), 2)
        self.assertEqual(outliers.sensitivity(env(context={'nudge_outlier_k': 9})), 3)
        env.company.nudge_outlier_sensitivity = "4"
        self.assertEqual(outliers.sensitivity(env), 4)

    # ------------------------------------------------------------ vendor bills

    def test_a_bill_far_above_the_supplier_usual_is_found_in_words(self):
        normal = self.bills(self.supplier, BILLS)
        big = self.bills(self.supplier, [3400])
        item = self.found("outliers_invoices", "bill_amount")
        self.assertIsNotNone(item)
        self.assertIn(big.id, item["ids"])
        self.assertFalse(set(normal.ids) & set(item["ids"]), "the usual bills are not accused")
        self.assertEqual(item["figure"], 3.4)
        self.assertEqual(item["severity"], tools.health.CRITICAL)
        text = tools.health.BY_KEY["outliers_invoices"].describe(self.env, item)
        self.assertIn("3.4×", text)
        self.assertIn("this supplier", text)

    def test_a_normal_set_yields_nothing(self):
        normal = self.bills(self.supplier, BILLS + [1005, 995])
        item = self.found("outliers_invoices", "bill_amount")
        self.assertFalse(item and set(normal.ids) & set(item["ids"]))

    def test_the_minimum_sample_is_respected(self):
        small = self.bills(self.supplier, BILLS[:6] + [3400])  # seven bills
        item = self.found("outliers_invoices", "bill_amount")
        self.assertFalse(item and set(small.ids) & set(item["ids"]), "seven bills are too few to judge")
        small |= self.bills(self.supplier, [1000])  # the eighth
        item = self.found("outliers_invoices", "bill_amount")
        self.assertTrue(item and set(small.ids) & set(item["ids"]))

    def test_the_sensitivity_decides_how_far_is_far(self):
        self.bills(self.supplier, BILLS)
        mild = self.bills(self.supplier, [1120])  # 2.4 spreads
        self.assertIn(mild.id, (self.found("outliers_invoices", "bill_amount", k=2) or {"ids": []})["ids"])
        for k in (3, 4):
            item = self.found("outliers_invoices", "bill_amount", k=k)
            self.assertNotIn(mild.id, (item or {"ids": []})["ids"])

    def test_a_user_without_the_rights_sees_nothing(self):
        self.bills(self.supplier, BILLS + [3400])
        # A Nudge manager who does not work in accounting reads no bill
        poor = new_test_user(self.env, login="outlier_poor", groups="nudge.group_nudge_manager")
        items, skipped = tools.health.run_all(self.env(user=poor), {"outliers_invoices"})
        self.assertEqual(items, [])
        self.assertEqual([item["reason"] for item in skipped], ["access"])

    def test_the_record_rules_are_kept(self):
        self.env["ir.rule"].create({
            "name": "Ebrahim hides his bills", "model_id": self.env.ref("account.model_account_move").id,
            "domain_force": "[('ref', '!=', 'Ebrahim private')]", "global": True})
        partner = self.env["res.partner"].create({"name": "Ebrahim Supplies"})
        hidden = self.bills(partner, BILLS + [3400], ref="Ebrahim private")
        item = self.found("outliers_invoices", "bill_amount")
        self.assertFalse(item and set(hidden.ids) & set(item["ids"]))
        # The one who may read them sees the same bills as unusual
        self.env["ir.rule"].search([("name", "=", "Ebrahim hides his bills")]).active = False
        item = self.found("outliers_invoices", "bill_amount")
        self.assertTrue(item and set(hidden.ids) & set(item["ids"]))

    def test_a_bill_above_its_purchase_order_is_found(self):
        if "purchase.order" not in self.env:
            self.skipTest("purchase is not installed")
        product = self.env["product.product"].create({"name": "Raghad Cement"})
        order = self.env["purchase.order"].create({
            "partner_id": self.supplier.id,
            "order_line": [(0, 0, {"product_id": product.id, "product_qty": 10, "price_unit": 100})]})
        order.button_confirm()
        line = order.order_line
        bill = self.Move.create({
            "move_type": "in_invoice", "partner_id": self.supplier.id, "invoice_date": fields.Date.today(),
            "invoice_line_ids": [(0, 0, {
                "name": "Cement", "quantity": 10, "price_unit": 150, "purchase_line_id": line.id,
                "account_id": self.expense_account.id, "tax_ids": False})]})
        bill.action_post()
        item = self.found("outliers_invoices", "bill_vs_order")
        self.assertIn(bill.id, item["ids"])
        self.assertEqual(item["figure"], 1.5)
        # A bill for a partial delivery is smaller, and no one's problem
        bill.button_draft()
        bill.invoice_line_ids.price_unit = 60
        bill.action_post()
        item = self.found("outliers_invoices", "bill_vs_order")
        self.assertFalse(item and bill.id in item["ids"])
        # A bill in another currency than its order is not compared with it, amount to amount
        euro = self.env.ref("base.EUR")
        euro.active = True
        bill.button_draft()
        bill.invoice_line_ids.price_unit = 150
        bill.currency_id = euro
        bill.action_post()
        item = self.found("outliers_invoices", "bill_vs_order")
        self.assertFalse(item and bill.id in item["ids"])

    # ------------------------------------------------------------ purchase lines

    def test_a_purchase_price_far_above_the_last_five(self):
        if "purchase.order" not in self.env:
            self.skipTest("purchase is not installed")
        product = self.env["product.product"].create({"name": "Raghad Steel"})
        order = self.env["purchase.order"].create({
            "partner_id": self.supplier.id,
            "order_line": [(0, 0, {"product_id": product.id, "product_qty": 1, "price_unit": price})
                           for price in (10, 10, 11, 10, 10, 10)]})
        item = self.found("outliers_purchases", "price_above_last")
        self.assertFalse(item and set(order.order_line.ids) & set(item["ids"]))
        pricey = self.env["purchase.order.line"].create({
            "order_id": order.id, "product_id": product.id, "product_qty": 1, "price_unit": 25})
        item = self.found("outliers_purchases", "price_above_last")
        self.assertIn(pricey.id, item["ids"])
        self.assertEqual(item["figure"], 2.5)

    # ------------------------------------------------------------ sales lines

    def test_a_discount_above_the_usual_and_a_price_under_cost(self):
        if "sale.order" not in self.env:
            self.skipTest("sale is not installed")
        product = self.env["product.product"].create(
            {"name": "Raghad Paint", "list_price": 100, "standard_price": 80})
        customer = self.env["res.partner"].create({"name": "Ebrahim Trading"})
        lines = [(0, 0, {"product_id": product.id, "product_uom_qty": 1, "price_unit": 100, "discount": 5})
                 for _ in range(10)]
        lines.append((0, 0, {"product_id": product.id, "product_uom_qty": 1, "price_unit": 100, "discount": 40}))
        order = self.env["sale.order"].create({"partner_id": customer.id, "order_line": lines})
        worst = order.order_line.sorted("discount")[-1]
        item = self.found("outliers_sales", "discount_product")
        self.assertIn(worst.id, item["ids"])
        self.assertNotIn(order.order_line.sorted("discount")[0].id, item["ids"])
        below = self.found("outliers_sales", "price_below_cost")
        self.assertEqual(set(order.order_line.ids) & set(below["ids"]), {worst.id},
                         "100 less 40% is 60, the cost is 80")
        self.assertEqual(below["figure"], 1.3)

    # ------------------------------------------------------------ attendance and expenses

    def test_a_day_far_from_the_persons_usual(self):
        if "hr.attendance" not in self.env:
            self.skipTest("attendance is not installed")
        person = self.env["hr.employee"].create({"name": "Raghad Worker"})
        start = fields.Datetime.now().replace(hour=1, minute=0, second=0, microsecond=0)
        days = []
        for index in range(1, 13):
            check_in = start - timedelta(days=index)
            hours = 8 if index > 1 else 19
            days.append(self.env["hr.attendance"].create({
                "employee_id": person.id, "check_in": check_in, "check_out": check_in + timedelta(hours=hours)}))
        item = self.found("outliers_attendance", "attendance_hours")
        self.assertIn(days[0].id, item["ids"])
        self.assertFalse({day.id for day in days[1:]} & set(item["ids"]))
        self.assertGreater(item["figure"], 2)

    def test_an_expense_far_above_its_category(self):
        if "hr.expense" not in self.env:
            self.skipTest("expenses is not installed")
        person = self.env["hr.employee"].create({"name": "Raghad Expense"})
        product = self.env["product.product"].create({"name": "Raghad Taxi", "can_be_expensed": True})
        expenses = self.env["hr.expense"]
        for amount in (50, 55, 48, 52, 50, 49, 51, 50, 500):
            expenses |= self.env["hr.expense"].create({
                "name": "Taxi", "employee_id": person.id, "product_id": product.id,
                "total_amount_currency": amount})
        item = self.found("outliers_expenses", "expense_amount")
        self.assertIn(expenses[-1].id, item["ids"])
        self.assertFalse(set(expenses[:-1].ids) & set(item["ids"]))

    # ------------------------------------------------------------ stock

    def test_goods_received_at_no_cost_and_a_move_far_above_the_usual(self):
        if "stock.move" not in self.env:
            self.skipTest("stock is not installed")
        product = self.env["product.product"].create({"name": "Raghad Sand", "is_storable": True})
        supplier_location = self.env.ref("stock.stock_location_suppliers")
        stock = self.env.ref("stock.stock_location_stock")
        Move = self.env["stock.move"]

        def move(quantity, price):
            return Move.create({
                "name": "Raghad move", "product_id": product.id, "product_uom_qty": quantity, "quantity": quantity,
                "location_id": supplier_location.id, "location_dest_id": stock.id, "price_unit": price,
                "state": "done", "date": fields.Datetime.now()})

        usual = [move(10 + index % 3, 5) for index in range(9)]
        huge = move(400, 5)
        free = move(10, 0)
        item = self.found("outliers_stock", "move_quantity")
        self.assertIn(huge.id, item["ids"])
        self.assertFalse({one.id for one in usual} & set(item["ids"]))
        cost = self.found("outliers_stock", "move_cost")
        self.assertIn(free.id, cost["ids"])
        self.assertFalse({one.id for one in usual} & set(cost["ids"]))

    # ------------------------------------------------------------ payslips are Enterprise

    def test_payslips_are_only_offered_where_they_exist(self):
        check = tools.health.BY_KEY["outliers_payslips"]
        self.assertEqual(check.available(self.env), "hr.payslip" in self.env.registry
                         or "hr.attendance" in self.env.registry)
        self.assertEqual(outliers.BY_RULE["payslip_net_own"].available(self.env), "hr.payslip" in self.env.registry)
        # The overtime rule needs attendance only
        self.assertEqual(
            outliers.BY_RULE["overtime_hours"].available(self.env), "hr.attendance" in self.env.registry)

    # ------------------------------------------------------------ the health report

    def test_the_report_keeps_the_figure_and_show_me_opens_the_bills(self):
        self.bills(self.supplier, BILLS)
        big = self.bills(self.supplier, [3400])
        report = self.env["nudge.health.report"].with_user(self.boss).run_check()
        line = report.finding_ids.filtered(lambda f: f.check == "outliers_invoices" and f.key == "bill_amount")
        self.assertEqual(line.figure, 3.4)
        self.assertIn("3.4×", line.message)
        action = line.action_show()
        found = self.env["account.move"].with_user(self.boss).search(action["domain"])
        self.assertIn(big, found)
        self.assertTrue(tools.health.labels(self.env)["outliers_invoices"].startswith("Unusual figures"))

    def test_every_family_has_a_check_and_a_label(self):
        labels = tools.health.labels(self.env)
        for family in outliers.FAMILIES:
            self.assertIn(f"outliers_{family}", labels)
            self.assertIn(f"outliers_{family}", tools.health.BY_KEY)
        self.assertEqual(set(outliers.TEXTS), set(outliers.BY_RULE))

    def test_nothing_is_changed(self):
        bills = self.bills(self.supplier, BILLS + [3400])
        before = bills.mapped("write_date") + bills.mapped("amount_total")
        self.found("outliers_invoices", "bill_amount")
        self.assertEqual(bills.mapped("write_date") + bills.mapped("amount_total"), before)

    # ------------------------------------------------------------ the task kind

    def test_a_task_watches_one_family_and_says_only_what_is_new(self):
        self.bills(self.supplier, BILLS)
        self.bills(self.supplier, [3400])
        Task = self.env["nudge.task"].with_user(self.boss)
        info = Task.action_setup_outliers("invoices", "3", send_email=False)
        task = self.env["nudge.task"].sudo().browse(info["id"])
        self.assertEqual((task.engine, task.outlier_family, task.outlier_sensitivity), ("outliers", "invoices", "3"))
        self.assertEqual(Task.action_setup_outliers("invoices", "3")["id"], info["id"], "one task per family")
        with self.assertRaises(UserError):
            Task.action_setup_outliers("nothing", "3")
        with self.assertRaises(UserError):
            Task.action_setup_outliers("invoices", "7")
        with self.assertRaises(AccessError):
            self.env["nudge.task"].with_user(self.outsider).action_setup_outliers("invoices", "3")
        task._start(fields.Datetime.now())
        self.assertEqual(task.run_count, 1)
        message = self.env["nudge.message"].sudo().search(
            [("conversation_id", "=", task.conversation_id.id)], order="id desc", limit=1)
        self.assertIn("3.4×", message.content)
        self.assertIn("Show me", message.content)
        link = self.env["nudge.digest.link"].sudo().search([("task_id", "=", task.id)])
        self.assertTrue(link)
        self.assertEqual(link.user_id, self.boss)
        # The same figure is not announced twice
        task._start(fields.Datetime.now())
        self.assertEqual(task.run_count, 1)
        self.assertEqual(task.skip_count, 1)
        # A new one is
        self.bills(self.supplier, [3600])
        task._start(fields.Datetime.now())
        self.assertEqual(task.run_count, 2)

    def test_a_task_of_a_person_without_the_rights_finds_nothing(self):
        self.bills(self.supplier, BILLS + [3400])
        poor = new_test_user(self.env, login="outlier_poor2", groups="nudge.group_nudge_user")
        info = self.env["nudge.task"].with_user(poor).action_setup_outliers("invoices", "3")
        task = self.env["nudge.task"].sudo().browse(info["id"])
        task._start(fields.Datetime.now())
        self.assertEqual((task.run_count, task.skip_count), (0, 1))

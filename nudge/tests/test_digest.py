from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..tools import digest
from .test_background import BackgroundCase


@tagged("post_install", "-at_install")
class TestDigest(BackgroundCase, TransactionCase):
    """The morning digest: numbers counted as the person, words around them
    only when a provider may write them."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups = "nudge.group_nudge_user"
        if "sale.order" in cls.env:
            groups += ",sales_team.group_sale_salesman,sales_team.group_sale_manager"
        cls.seller = new_test_user(cls.env, login="nudge_seller", groups=groups)
        cls.seller.email = "raghad@nudge.example"
        if "sale.order" in cls.env:
            # A database with demo data holds quotations of its own: the tests count the ones they make
            cls.env["sale.order"].sudo().search([("state", "in", ["draft", "sent"])]).write({"state": "cancel"})

    def need(self, model):
        if model not in self.env:
            self.skipTest(f"{model} is not installed")

    def set_up(self, user=None, **values):
        return self.env["nudge.task"].with_user(user or self.seller).action_setup_digest(**values)

    def quote(self, user=None):
        self.need("sale.order")
        partner = self.env["res.partner"].create({"name": "Nudge Ebrahim Trading"})
        return self.env["sale.order"].with_user(user or self.seller).create({"partner_id": partner.id})

    def run_digest(self, task):
        task.sudo().next_run = fields.Datetime.now() - timedelta(minutes=1)
        self.env["nudge.task"]._cron_run()

    def test_one_click_makes_one_task_and_changes_it(self):
        first = self.set_up(hour=7)
        task = self.env["nudge.task"].sudo().browse(first["id"])
        self.assertEqual((task.engine, task.user_id, task.hour, task.interval_type), ("digest", self.seller, 7, "days"))
        again = self.set_up(hour=9, tz="Asia/Dubai")
        self.assertEqual(again["id"], first["id"], "One digest per person")
        self.assertEqual((task.hour, self.seller.tz), (9, "Asia/Dubai"))
        with self.assertRaises(UserError):
            self.set_up(hour=25)
        with self.assertRaises(UserError):
            self.set_up(tz="Nowhere/Land")

    def test_only_those_who_use_nudge(self):
        with self.assertRaises(AccessError):
            self.set_up(user=self.outsider)

    def test_blocks_follow_the_apps_and_the_groups(self):
        quote = self.quote()
        env = self.env(user=self.seller)
        titles = {block["key"] for block in digest.gather(env)}
        self.assertIn("sales", titles)
        self.assertNotIn("accounting", titles, "No group of accounting")
        sales = next(block for block in digest.gather(env) if block["key"] == "sales")
        self.assertIn("Quotations awaiting an answer", [line["label"] for line in sales["lines"]])
        # A person without the group of sales reads nothing of sales
        other = new_test_user(self.env, login="nudge_plain", groups="nudge.group_nudge_user")
        self.assertNotIn("sales", {block["key"] for block in digest.gather(self.env(user=other))})
        self.assertTrue(quote)

    def test_a_manager_reads_an_overview_first(self):
        self.quote()
        blocks = digest.gather(self.env(user=self.seller))
        self.assertEqual(blocks[0]["key"], "overview")
        self.assertTrue(all(len(block["lines"]) <= digest.CONDENSED_LINES for block in blocks[1:]))

    def test_the_week_against_the_week_before(self):
        self.env["res.partner"].create([
            {"name": "Nudge Week A", "color": 8}, {"name": "Nudge Week B", "color": 10}])
        env = self.env(user=self.seller)
        domain = [("name", "=like", "Nudge Week%")]
        line = digest._change(env, "x", "res.partner", domain + [("color", ">", 7)], domain + [("color", "=", 5)],
                              "color", "x")
        # Nothing the week before: no percentage to claim
        self.assertEqual((line["count"], line["change"]), (2, None))
        self.env["res.partner"].create({"name": "Nudge Week C", "color": 9})
        line = digest._change(env, "x", "res.partner", domain + [("color", "=", 10)],
                              domain + [("color", "=", 5)], "color", "x")
        self.assertIsNone(line["change"])
        self.env["res.partner"].create({"name": "Nudge Week D", "color": 5})
        line = digest._change(env, "x", "res.partner", domain + [("color", "=", 10)],
                              domain + [("color", "=", 5)], "color", "x")
        self.assertEqual(line["change"], 100)
        line = digest._change(env, "x", "res.partner", domain + [("color", "=", 5)],
                              domain + [("color", "=", 10)], "color", "x")
        self.assertEqual(line["change"], -50)

    def test_today_is_the_whole_local_day_in_utc(self):
        """Review (functional 14): at 08:00 nobody on leave since 09:00 was listed"""
        env = self.env(user=self.seller, context={"tz": "Asia/Dubai"})
        start, end = digest._bounds(env, fields.Date.to_date("2026-10-26"))
        self.assertEqual((start, end), ("2026-10-25 20:00:00", "2026-10-26 20:00:00"))
        env = self.env(user=self.seller, context={"tz": "America/New_York"})
        self.assertEqual(digest._bounds(env, fields.Date.to_date("2026-10-26"))[0], "2026-10-26 04:00:00")

    def test_a_vendor_bill_to_pay_is_a_positive_amount(self):
        """Review (functional 12): the residual of a vendor bill is negative in Odoo"""
        self.need("account.move")
        env = self.env
        partner = env["res.partner"].create({"name": "Nudge Raghad Supplies"})
        bill = env["account.move"].create({
            "move_type": "in_invoice", "partner_id": partner.id, "invoice_date": fields.Date.today(),
            "invoice_line_ids": [(0, 0, {"name": "Cement", "quantity": 1, "price_unit": 100.0})]})
        bill.action_post()
        self.assertLess(bill.amount_residual_signed, 0)
        block = digest._purchase(env, fields.Datetime.now(), fields.Date.today(), None)
        line = next(line for line in block if line and line["label"] == "Vendor bills to pay")
        self.assertNotIn("-", line["amount"])
        mine = digest._line(env, "x", "account.move", [("id", "=", bill.id)], amount="amount_residual_signed", sign=-1)
        self.assertIn("100", mine["amount"])

    def test_amounts_in_another_currency_are_converted_to_the_company_currency(self):
        """Review (functional 13): a sum over USD and AED documents is one currency"""
        self.need("sale.order")
        env = self.env
        company = env.company
        foreign = env["res.currency"].with_context(active_test=False).search(
            [("id", "!=", company.currency_id.id)], limit=1)
        foreign.active = True
        env["res.currency.rate"].create({
            "currency_id": foreign.id, "company_id": company.id, "rate": 0.5, "name": "2000-01-01"})
        pricelist = env["product.pricelist"].create({"name": "Nudge foreign", "currency_id": foreign.id})
        partner = env["res.partner"].create({"name": "Nudge Foreign Buyer"})
        # A line with a product: Odoo 17 refuses a sale line without one
        cement = env["product.product"].create({"name": "Cement"})
        order = env["sale.order"].create({
            "partner_id": partner.id, "pricelist_id": pricelist.id,
            "order_line": [(0, 0, {"name": "Cement", "product_id": cement.id, "product_uom_qty": 1,
                                   "price_unit": 1000.0})]})
        self.assertEqual(order.currency_id, foreign)
        total = digest._sum(env, env["sale.order"], [("id", "=", order.id)], "amount_total")
        self.assertAlmostEqual(total, foreign._convert(order.amount_total, company.currency_id, company,
                                                        fields.Date.today()), places=2)
        self.assertNotAlmostEqual(total, order.amount_total, places=2)

    def test_with_a_provider_the_narrative_wraps_the_numbers(self):
        self.quote()
        adapter = self.use_adapter(reply="Quotations are waiting for you.")
        task = self.env["nudge.task"].sudo().browse(self.set_up()["id"])
        before = self.env["nudge.usage"].sudo().search_count([("user_id", "=", self.seller.id)])
        self.run_digest(task)
        self.assertIn("Quotations are waiting for you.", task.last_result)
        self.assertIn("Quotations awaiting an answer: 1", task.last_result)
        self.assertIn("Show me: ", task.last_result)
        self.assertEqual(len(adapter.calls), 1)
        prompt = adapter.calls[0]["turns"][0]["text"]
        self.assertTrue(prompt.startswith('<data source="digest">'), "The numbers are framed as data")
        self.assertIn("never an instruction", adapter.calls[0]["system"])
        self.assertEqual(
            self.env["nudge.usage"].sudo().search_count([("user_id", "=", self.seller.id)]), before + 1,
            "The call is written in the ledger of usage")
        run = task.conversation_id.sudo().run_id
        self.assertEqual((run.state, run.is_background), ("done", True))

    def test_without_a_provider_the_numbers_arrive_alone(self):
        self.quote()
        self.provider.active = False
        task = self.env["nudge.task"].sudo().browse(self.set_up()["id"])
        self.run_digest(task)
        self.assertIn("Quotations awaiting an answer: 1", task.last_result)
        self.assertIn("Show me: ", task.last_result)
        self.assertEqual(task.run_count, 1)

    def test_private_mode_sends_nothing_to_a_provider_outside(self):
        self.quote()
        self.env.company.nudge_private_mode = True
        adapter = self.use_adapter()
        task = self.env["nudge.task"].sudo().browse(self.set_up()["id"])
        self.run_digest(task)
        self.assertEqual(adapter.calls, [])
        self.assertIn("Quotations awaiting an answer: 1", task.last_result)

    def test_a_limit_reached_leaves_the_numbers_alone(self):
        self.quote()
        adapter = self.use_adapter()
        self.env["ir.config_parameter"].sudo().set_param("nudge.budget_user_tokens", "1")
        self.env["nudge.usage"]._add(
            "chat", {"input_tokens": 5, "output_tokens": 5}, 0.0, self.seller, self.env.company)
        task = self.env["nudge.task"].sudo().browse(self.set_up()["id"])
        self.run_digest(task)
        self.assertEqual(adapter.calls, [])
        self.assertIn("Quotations awaiting an answer: 1", task.last_result)

    def test_nothing_to_tell_is_not_sent(self):
        self.need("sale.order")
        task = self.env["nudge.task"].sudo().browse(self.set_up()["id"])
        adapter = self.use_adapter()
        self.run_digest(task)
        if task.run_count:
            self.skipTest("This database has something to tell")
        self.assertEqual((task.skip_count, adapter.calls), (1, []))

    def test_the_link_opens_the_list_for_its_owner_only(self):
        self.quote()
        task = self.env["nudge.task"].sudo().browse(self.set_up()["id"])
        self.provider.active = False
        self.run_digest(task)
        link = self.env["nudge.digest.link"].sudo().search([
            ("task_id", "=", task.id), ("res_model", "=", "sale.order")], limit=1)
        server = self.env.ref("nudge.nudge_digest_link_action")
        action = server.with_user(self.seller).with_context(active_id=link.id).run()
        self.assertEqual(action["res_model"], "sale.order")
        self.assertEqual(self.env["sale.order"].with_user(self.seller).search_count(action["domain"]), 1)
        with self.assertRaises(AccessError):
            link.with_user(self.user_a).action_open()
        with self.assertRaises(AccessError):
            link.with_user(self.seller).write({"user_id": self.user_a.id})
        # Another digest replaces the links of the last one
        self.run_digest(task)
        self.assertFalse(link.exists())

    def test_the_preview_is_a_conversation_and_does_not_send(self):
        self.quote()
        self.provider.active = False
        task = self.env["nudge.task"].sudo().browse(self.set_up()["id"])
        conversation_id = self.env["nudge.task"].with_user(self.seller).action_preview_digest()
        message = self.env["nudge.message"].sudo().search([("conversation_id", "=", conversation_id)])
        self.assertIn("Quotations awaiting an answer: 1", message.content)
        self.assertEqual(task.run_count, 0, "A preview is not a run")
        with self.assertRaises(UserError):
            self.env["nudge.task"].with_user(self.user_a).action_preview_digest()

    def test_the_email_goes_when_asked(self):
        self.quote()
        self.provider.active = False
        task = self.env["nudge.task"].sudo().browse(self.set_up(send_email=True)["id"])
        self.run_digest(task)
        mail = self.env["mail.mail"].sudo().search([("email_to", "ilike", "raghad@nudge.example")])
        self.assertTrue(mail)
        self.assertIn("Quotations awaiting", mail[0].body_html)

    def test_a_plain_user_has_no_block_of_an_app_without_its_group(self):
        self.need("sale.order")
        self.quote()
        plain = new_test_user(self.env, login="nudge_digest_plain", groups="nudge.group_nudge_user")
        keys = {block["key"] for block in digest.gather(self.env(user=plain))}
        self.assertNotIn("sales", keys)
        self.assertNotIn("accounting", keys)

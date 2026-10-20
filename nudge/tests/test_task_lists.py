import json
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.llm import base

from .test_agent import asking, call, saying
from .test_background import BackgroundCase

WATCHED = [{"field": "name", "operator": "contains", "value": "Nudge Acme"}]
FILTER = call("filter_screen", {
    "model": "res.partner", "conditions": WATCHED, "text": "Showing the contacts"}, "call_f")
SEARCH = call("search_records", {"model": "res.partner", "conditions": WATCHED}, "call_s")
SILENCE = [base.stop_event(base.STOP_END)]


@tagged("post_install", "-at_install")
class TestTaskLists(BackgroundCase, TransactionCase):
    """A list asked for in the background is answered in the answer, with a link."""

    def background(self, script, text="List the Nudge Acme contacts"):
        adapter = self.use_adapter(script=script)
        conversation, _events = self.leave(self.agent, text)
        run = conversation.sudo().run_id
        self.work(run)
        return adapter, conversation, run

    def delivered(self, conversation):
        return conversation.sudo().message_ids.filtered(lambda m: m.role == "assistant")[-1].content

    def open_link(self, text, run):
        self.assertIn("Open the list", text)
        self.assertIn("/nudge/step/", text)
        step = run.step_ids.filtered(lambda s: s.tool in ("search_records", "filter_screen"))[-1]
        action = step.with_user(self.agent).action_open_list()
        self.assertEqual(action["res_model"], "res.partner")
        self.assertEqual(self.env["res.partner"].search(action["domain"]), self.acme)

    def test_a_screen_tool_is_refused_and_the_loop_recovers(self):
        adapter, conversation, run = self.background([
            asking(FILTER), asking(SEARCH), saying("| name |\n|---|\n| Nudge Acme |")])
        self.assertEqual(run.state, "done")
        steps = {step.tool: step for step in run.step_ids}
        self.assertEqual(steps["filter_screen"].state, "failed")
        self.assertIn("no screen", steps["filter_screen"].error)
        self.assertIn("<data", steps["filter_screen"].error)
        text = self.delivered(conversation)
        self.assertIn("Nudge Acme", text)
        self.open_link(text, run)

    def test_the_tools_and_the_prompt_of_a_background_run(self):
        adapter, _conversation, _run = self.background([saying("Done.")])
        offered = {tool["name"] for tool in adapter.calls[0]["tools"]}
        self.assertIn("search_records", offered)
        self.assertFalse(offered & tools.base.SCREEN_TOOLS)
        self.assertIn("No screen", "\n".join(adapter.calls[0]["system"]))

    def test_an_answer_without_words_delivers_the_rows(self):
        _adapter, conversation, run = self.background([asking(SEARCH), SILENCE, SILENCE])
        self.assertEqual(run.state, "done")
        text = self.delivered(conversation)
        self.assertIn("| name", text)
        self.assertIn("Nudge Acme", text)
        self.open_link(text, run)

    def test_the_interactive_run_still_offers_the_screen(self):
        adapter = self.use_adapter(script=[saying("Here.")])
        self.order(self.agent, "Show me the Nudge contacts")
        offered = {tool["name"] for tool in adapter.calls[0]["tools"]}
        self.assertIn("filter_screen", offered)
        self.assertNotIn("No screen", "\n".join(adapter.calls[0]["system"]))

    def test_a_scheduled_list_of_overdue_invoices(self):
        if "account.move" not in self.env:
            self.skipTest("accounting is not installed")
        field = "group_ids" if "group_ids" in self.env["res.users"]._fields else "groups_id"
        self.agent.write({field: [(4, self.env.ref("account.group_account_invoice").id)]})
        today = fields.Date.context_today(self.env["res.users"])
        invoice = self.env["account.move"].create({
            "move_type": "out_invoice", "partner_id": self.acme.id,
            "invoice_date": today - timedelta(days=40), "invoice_date_due": today - timedelta(days=10),
            "invoice_line_ids": [(0, 0, {"name": "Overdue work", "quantity": 1, "price_unit": 100.0})],
        })
        invoice.action_post()
        conditions = [
            {"field": "move_type", "operator": "=", "value": "out_invoice"},
            {"field": "payment_state", "operator": "!=", "value": "paid"},
            {"field": "invoice_date_due", "operator": "<", "value": str(today)},
        ]
        search = call("search_records", {
            "model": "account.move", "conditions": conditions,
            "fields": ["name", "partner_id", "amount_total"]}, "call_i")
        adapter = self.use_adapter(script=[
            asking(call("filter_screen", {
                "model": "account.move", "conditions": conditions, "text": "Overdue"}, "call_x")),
            asking(search), SILENCE, SILENCE])
        task = self.env["nudge.task"].with_user(self.agent).create({
            "name": "Overdue invoices", "instruction": "Every morning list the overdue invoices"})
        task.sudo().next_run = fields.Datetime.now() - timedelta(minutes=1)
        self.env["nudge.task"]._cron_run()
        run = task.conversation_id.sudo().run_id
        self.work(run)
        self.assertEqual(run.state, "done")
        self.assertIn(invoice.name, task.last_result)
        self.assertIn("Open the list", task.last_result)
        self.assertTrue(adapter.calls)


@tagged("post_install", "-at_install")
class TestStepListLink(BackgroundCase, HttpCase):

    def test_the_link_redirects_to_the_filtered_list(self):
        self.use_adapter(script=[asking(SEARCH), saying("Found.")])
        conversation, _events = self.leave(self.agent, "List the Nudge Acme contacts")
        run = conversation.sudo().run_id
        self.work(run)
        step = run.step_ids[-1]
        self.authenticate("nudge_g", "nudge_g")
        response = self.url_open(f"/nudge/step/{step.id}/open", allow_redirects=False)
        self.assertIn(response.status_code, (301, 302, 303))
        location = response.headers["Location"]
        server = self.env.ref("nudge.nudge_step_list_action")
        self.assertIn(f"action-{server.id}", location)
        self.assertIn(f"/{step.id}/", location)
        action = server.with_user(self.agent).with_context(active_id=step.id).run()
        self.assertEqual(action["res_model"], "res.partner")
        self.assertEqual(self.env["res.partner"].search(action["domain"]), self.acme)
        # Nothing is stored per step, however often it is opened
        count = self.env["ir.actions.act_window"].search_count([])
        self.url_open(f"/nudge/step/{step.id}/open", allow_redirects=False)
        self.assertEqual(self.env["ir.actions.act_window"].search_count([]), count)
        self.assertFalse(self.env["ir.actions.act_window"].search([("name", "like", "Nudge list")]))
        # The web client runs the action as the person and gets the filtered list
        loaded = self.make_jsonrpc_request("/web/action/run", {
            "action_id": server.id, "context": {"active_id": step.id}})
        self.assertEqual(loaded["res_model"], "res.partner")
        self.assertEqual(self.env["res.partner"].search(loaded["domain"]), self.acme)
        # Someone else's step is not theirs to open
        self.authenticate("nudge_c", "nudge_c")
        self.assertEqual(self.url_open(f"/nudge/step/{step.id}/open", allow_redirects=False).status_code, 403)
        other = self.env["res.users"].search([("login", "=", "nudge_c")])
        with self.assertRaises(AccessError):
            server.with_user(other).with_context(active_id=step.id).run()

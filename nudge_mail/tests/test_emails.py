from types import SimpleNamespace

from lxml import etree

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.mail.tests.common import MockEmail
from odoo.addons.nudge import tools
from odoo.addons.nudge.models.nudge_conversation import DEVELOPER_PARAM
from odoo.addons.nudge.tests.common import NudgeCase
from odoo.addons.nudge_mail.tools import designs
from odoo.addons.nudge_mail.tools.emails import TEST_PREFIX


@tagged("post_install", "-at_install")
class TestEmails(NudgeCase, MockEmail, TransactionCase):
    """Designed emails, a test to the administrator, and the window a button opens."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = new_test_user(
            cls.env, login="nudge_mail_admin", name="Nudge Mail Admin", email="admin@acme.example",
            groups="nudge.group_nudge_agent,base.group_system,base.group_partner_manager")
        cls.clerk = new_test_user(
            cls.env, login="nudge_mail_clerk", name="Nudge Mail Clerk", email="clerk@acme.example",
            groups="base.group_user,base.group_partner_manager")
        cls.env["ir.config_parameter"].sudo().set_param(DEVELOPER_PARAM, "yes")
        cls.partner = cls.env["res.partner"].create({
            "name": "Rana Haddad", "email": "rana@bluesky.example", "company_name": "Blue Sky Logistics",
            "city": "Dubai", "user_id": cls.admin.id,
        })

    def dev_env(self, user=None):
        return self.env(user=user or self.admin, context={"nudge_mode": "dev"})

    def run_tool(self, name, arguments, mode="dev"):
        return tools.base.call(self.env(user=self.admin, context={"nudge_mode": mode}), name, arguments, None, mode)

    def do(self, name, result):
        step = SimpleNamespace(values=result.pending["values"], res_model=result.pending["res_model"],
                               res_ids=result.pending["res_ids"])
        return tools.base.TOOLS[name].execute(self.dev_env(), step)

    def design(self, **changes):
        arguments = {
            "name": "Welcome",
            "model": "res.partner",
            "style": "modern",
            "to": ["email"],
            "cc": ["user_id"],
            "subject": "Welcome, {{ object.name }}",
            "greeting": "Dear {{ object.name }},",
            "intro": "Thank you for choosing us.\n\nYour account in {{ object.city }} is ready.",
            "details": {"title": "Your account", "rows": [
                {"field": "email"}, {"field": "city", "label": "City"}, {"field": "is_company", "label": "A company"}]},
            "box": {"title": "Requested from you", "items": ["Your tax number: ______", "A signed contract"]},
            "button": {"label": "Open in Odoo"},
            "closing": "Best regards,",
            "record_id": self.partner.id,
        }
        arguments.update(changes)
        return self.run_tool("prepare_email_design", arguments)

    @staticmethod
    def preview(result):
        return next(line for line in result.pending["preview"]["lines"] if line.get("html"))

    def made(self, **changes):
        result = self.design(**changes)
        self.assertFalse(result.is_error, result.content)
        done = self.do("prepare_email_design", result)
        self.assertFalse(done.is_error, done.content)
        return self.env["mail.template"].browse(done.records[1])

    # ------------------------------------------------------------------ offer
    def test_offered_in_developer_mode_only(self):
        offered = [tool.name for tool in tools.base.available(self.env(user=self.admin), "dev")]
        for name in ("prepare_email_design", "send_test_email", "prepare_email_review"):
            self.assertIn(name, offered)
        agent = [tool.name for tool in tools.base.available(self.env(user=self.admin), "agent")]
        self.assertNotIn("prepare_email_design", agent)
        self.assertNotIn("prepare_email_review", [
            tool.name for tool in tools.base.available(self.env(user=self.clerk), "dev")])

    # ----------------------------------------------------------------- design
    def test_an_email_is_always_a_designed_one(self):
        plain = {"to": ["email"], "cc": ["user_id"], "subject": "Welcome", "body": "Hello"}
        rule = self.run_tool("prepare_automation", {
            "name": "Welcome", "model": "res.partner", "trigger": "on_create",
            "actions": [{"kind": "email", "email": plain}]})
        self.assertTrue(rule.is_error)
        has_rules = "base.automation" in self.env
        if has_rules:
            self.assertIn("prepare_email_design", rule.content)
            self.assertIn("style", rule.content)
        else:
            self.assertIn("Automation Rules", rule.content, "Without the app, the rule itself is refused")
        template = self.run_tool("prepare_email_template", {"name": "Welcome", "model": "res.partner", "email": plain})
        self.assertTrue(template.is_error)
        self.assertIn("prepare_email_design", template.content)
        if not has_rules:
            return
        # A designed email goes on a rule by its template
        designed = self.made()
        rule = self.run_tool("prepare_automation", {
            "name": "Welcome", "model": "res.partner", "trigger": "on_create",
            "actions": [{"kind": "email", "template_id": designed.id}]})
        self.assertFalse(rule.is_error, rule.content)

    def test_recipients_read_as_people_read_them(self):
        line = next(item for item in self.design().pending["preview"]["lines"] if item["label"] == "Copy to")
        self.assertIn("Salesperson", line["value"])

    def test_the_card_shows_the_email_as_it_will_be_read(self):
        result = self.design()
        self.assertFalse(result.is_error, result.content)
        line = self.preview(result)
        html = line["html"]
        self.assertIn("Dear <span>Rana Haddad</span>,", html)
        self.assertIn("Your account in <span>Dubai</span> is ready.", html)
        self.assertIn("max-width:600px", html, "Laid out for mail clients")
        self.assertIn("A company", html)
        self.assertIn(">No<", html, "A checkbox reads as a word")
        self.assertIn("Your tax number: ______", html)
        self.assertIn(f"/mail/view?model=res.partner&amp;res_id={self.partner.id}", html)
        self.assertIn("/logo.png?company=", html, "The logo closes the email")
        self.assertIn(self.env.company.name, html)
        self.assertIn("Rana Haddad", line["value"], "The plain text of it for the log")
        labels = [item["label"] for item in result.pending["preview"]["lines"]]
        self.assertIn("Style", labels)
        style = next(item for item in result.pending["preview"]["lines"] if item["label"] == "Style")
        self.assertEqual(style["value"], designs.style_text(self.env, "modern"), "Said in the person's words")
        self.assertTrue(style["value"].startswith("Modern: "))
        subject = next(item for item in result.pending["preview"]["lines"] if item["label"] == "Subject")
        self.assertEqual(subject["value"], "Welcome, Rana Haddad")
        self.assertEqual(self.env["mail.template"].search_count([("name", "=", "Welcome")]), 0, "Nothing made yet")

    def test_made_after_the_yes_and_registered(self):
        template = self.made()
        self.assertEqual(template.model, "res.partner")
        self.assertIn('t-field="object.city"', template.body_html)
        self.assertEqual(template.subject, "Welcome, {{ object.name }}")
        self.assertEqual(template.email_to, "{{ object.email }}")
        self.assertEqual(template.email_cc, "{{ object.user_id.partner_id.email_formatted }}")
        self.assertTrue(template.get_external_id()[template.id].startswith("nudge_custom."))

    def test_every_style_renders_its_own_way(self):
        bodies = {}
        for style in designs.STYLES:
            result = self.design(style=style)
            self.assertFalse(result.is_error, f"{style}: {result.content}")
            bodies[style] = self.preview(result)["html"]
            self.assertIn("Rana Haddad", bodies[style], style)
        self.assertEqual(len(set(bodies.values())), len(designs.STYLES), "Four styles, four looks")
        self.assertIn("Georgia", bodies["formal"])
        self.assertIn("Reference", bodies["formal"])

    def test_arabic_text_makes_it_right_to_left(self):
        result = self.design(intro="شكراً لاختيارك لنا.", closing="مع التحية،")
        html = self.preview(result)["html"]
        self.assertIn('dir="rtl"', html)
        self.assertIn("نعم", self.design(intro="مرحبا", details={"rows": [{"field": "active"}]}).pending["preview"]["lines"][-2]["html"])

    def test_the_company_under_it_can_be_left_out(self):
        result = self.design(style="classic", company_footer=False)
        self.assertNotIn("/logo.png", self.preview(result)["html"])
        self.assertIn("Nothing: no logo", str(result.pending["preview"]["lines"]))

    def test_written_text_is_text(self):
        # Everywhere text goes, before and after the values of the record,
        # the lines and the box: what QWeb would print as markup is caught
        self.env["res.partner"].create({"name": "Omar", "parent_id": self.partner.id})
        for style in designs.STYLES:
            result = self.design(
                style=style,
                title="<i>Title</i> {{ object.name }}",
                greeting="<u>Dear</u> {{ object.name }},",
                intro="<script>alert(1)</script> & <b>{{ object.name }}</b>",
                details={"title": "<em>Account</em>", "rows": [{"field": "is_company", "label": "<s>Company</s>"}]},
                lines={"title": "<em>Lines</em>", "field": "child_ids", "columns": [{"field": "name", "label": "<q>Name</q>"}]},
                box={"title": "<mark>Box</mark>", "items": ["<del>one</del> {{ object.city }}", "<ins>two</ins>"]},
                button={"label": "<big>Open</big>"},
                closing="<small>Regards</small>,",
            )
            self.assertFalse(result.is_error, result.content)
            html = self.preview(result)["html"]
            for tag in ("script", "i", "u", "b", "em", "s", "q", "mark", "del", "ins", "big", "small"):
                self.assertNotIn(f"<{tag}>", html, f"{style}: <{tag}> came through")
            self.assertIn("&lt;script&gt;", html)
            self.assertIn("Rana Haddad", html)

    def test_what_cannot_go_in(self):
        cases = [
            {"intro": "{{ object.sudo().name }}"},
            {"intro": "{{ user.password }}"},
            {"details": {"rows": [{"field": "child_ids.name"}]}},
            {"details": {"rows": [{"field": "image_1920"}]}},
            {"details": {"rows": [{"field": "no_such_field"}]}},
            {"subject": "About {{ object.type }}"},
        ]
        for changes in cases:
            result = self.design(**changes)
            self.assertTrue(result.is_error, f"{changes}: {result.content}")

    def test_lines_are_a_table(self):
        self.env["res.partner"].create({"name": "Omar", "parent_id": self.partner.id, "email": "omar@bluesky.example"})
        result = self.design(lines={"title": "Contacts", "field": "child_ids",
                                    "columns": [{"field": "name"}, {"field": "email", "label": "Email"}]})
        html = self.preview(result)["html"]
        self.assertIn("omar@bluesky.example", html)
        self.assertIn("Contacts", html)
        refused = self.design(lines={"field": "city", "columns": [{"field": "name"}]})
        self.assertTrue(refused.is_error)

    def test_only_a_design_made_here_is_changed(self):
        other = self.env["mail.template"].create({
            "name": "Hand made", "model_id": self.env["ir.model"]._get("res.partner").id, "subject": "x"})
        self.assertTrue(self.design(template_id=other.id).is_error)
        template = self.made()
        result = self.design(template_id=template.id, style="formal")
        self.assertFalse(result.is_error, result.content)
        self.assertTrue(result.pending["summary"].startswith("Change"))
        self.do("prepare_email_design", result)
        self.assertIn("Georgia", template.body_html)

    # ------------------------------------------------------------------- test
    def test_a_test_goes_to_the_administrator_only(self):
        template = self.made()
        result = self.run_tool("send_test_email", {"template_id": template.id, "record_id": self.partner.id})
        self.assertFalse(result.is_error, result.content)
        self.assertIn("admin@acme.example", str(result.pending["preview"]["lines"]))
        with self.mock_mail_gateway():
            done = self.do("send_test_email", result)
        self.assertFalse(done.is_error, done.content)
        self.assertEqual(len(self._mails), 1)
        sent = self._mails[0]
        self.assertTrue(sent["subject"].startswith(TEST_PREFIX))
        self.assertEqual(sent["email_to"], ['"Nudge Mail Admin" <admin@acme.example>'])
        self.assertFalse(sent.get("email_cc"), "Nobody in copy of a test")
        self.assertNotIn("rana@bluesky.example", str(sent["email_to"]))

    # ----------------------------------------------------------------- review
    def review(self, **changes):
        template = self.made(name="Company created")
        arguments = {
            "name": "Create the company, and say so",
            "model": "res.partner",
            "template_id": template.id,
            "label": "Create and Email",
            "method": "create_company",
            "hide_original": True,
            "required_fields": ["phone"],
            "without_email": True,
        }
        arguments.update(changes)
        return self.run_tool("prepare_email_review", arguments)

    def open_window(self, flow, user=None):
        user = user or self.clerk
        action = flow.action_id.with_user(user).with_context(
            active_model="res.partner", active_ids=self.partner.ids, active_id=self.partner.id).run()
        self.assertEqual(action["res_model"], "nudge.email.review")
        return self.env["nudge.email.review"].with_user(user).with_context(action["context"]).create({})

    def made_flow(self, **changes):
        result = self.review(**changes)
        self.assertFalse(result.is_error, result.content)
        done = self.do("prepare_email_review", result)
        self.assertFalse(done.is_error, done.content)
        return self.env["nudge.email.flow"].browse(done.records[1])

    def test_the_template_of_the_model_is_taken_when_none_is_given(self):
        # A small model asked for a review button invented a template id
        from odoo.addons.nudge_mail.tools.emails import template_of  # noqa: PLC0415

        from odoo.addons.nudge.tools.records import Refused  # noqa: PLC0415

        with self.assertRaisesRegex(Refused, "has no email template"):
            env = self.dev_env()
            template_of(env, env["res.partner.category"])
        without = {"name": "Create and say so", "model": "res.partner", "label": "Create and Email",
                   "method": "create_company"}
        template = self.made(name="Company created")
        result = self.run_tool("prepare_email_review", without)
        if self.env["mail.template"].search_count([("model", "=", "res.partner")]) == 1:
            self.assertFalse(result.is_error, result.content)
            self.made(name="Another one")
            result = self.run_tool("prepare_email_review", without)
        self.assertIn("several email templates", result.content)
        self.assertIn(f"#{template.id} Company created", result.content)

    def test_the_button_takes_the_place_of_the_original(self):
        result = self.review()
        self.assertFalse(result.is_error, result.content)
        self.assertIn("Create and Email", str(result.pending["preview"]["lines"]))
        self.assertFalse(self.env["nudge.email.flow"].search([]), "Nothing made before the yes")
        flow = self.made_flow()
        self.assertTrue(flow.view_id and flow.action_id)
        arch = etree.fromstring(self.env["res.partner"].get_view(view_type="form")["arch"])
        new = arch.xpath(f"//button[@name='{flow.action_id.id}']")
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0].get("string"), "Create and Email")
        original = flow.view_id.inherit_id._get_combined_arch().xpath("//button[@name='create_company']")
        self.assertEqual(original[0].get("invisible"), "1", "The original is hidden behind it")

    def test_a_method_not_on_the_form_is_refused(self):
        refused = self.review(method="action_archive")
        self.assertTrue(refused.is_error)
        refused = self.review(method="name_search")
        self.assertTrue(refused.is_error)
        self.assertIn("create_company", refused.content, "The buttons there are listed")

    def test_send_waits_for_the_fields_then_does_both(self):
        flow = self.made_flow()
        window = self.open_window(flow)
        self.assertIn("rana@bluesky.example", window.email_to)
        self.assertIn("Rana Haddad", window.body)
        self.assertTrue(window.missing, "Phone is still empty")
        with self.assertRaises(UserError):
            window.action_send()
        self.partner.phone = "+971 4 555 0142"
        window = self.open_window(flow)
        window.email_cc = "manager@acme.example, it@acme.example"
        with self.mock_mail_gateway():
            window.action_send()
        self.assertTrue(self.partner.parent_id, "What the button does was done")
        self.assertEqual(self.partner.parent_id.name, "Blue Sky Logistics")
        self.assertEqual(len(self._mails), 1)
        self.assertEqual(sorted(self._mails[0]["email_cc"]), ["it@acme.example", "manager@acme.example"])
        self.assertIn("Email sent", self.partner.message_ids[0].body)

    def test_a_template_of_odoo_goes_to_the_recipients_of_the_record(self):
        # The Review Email of a quotation, made with Odoo's "Sales: Send Quotation", opened with
        # nobody in To: that template names nobody, and goes to the customer of the record
        stock = self.env["mail.template"].create({
            "name": "Contact: Send", "model_id": self.env["ir.model"]._get_id("res.partner"),
            "subject": "Hello {{ object.name }}", "body_html": "<p>Hello</p>", "use_default_to": True,
        })
        flow = self.made_flow(template_id=stock.id)
        self.assertEqual(self.open_window(flow).email_to, self.partner.email_formatted)
        # A template that names its recipients is followed, as designed
        designed = self.made_flow(template_id=self.made(name="To the salesperson", to=["user_id"]).id)
        self.assertEqual(self.open_window(designed).email_to, self.admin.partner_id.email_formatted)
        # Neither named nor the record's: nobody, which Send refuses
        stock.use_default_to = False
        self.assertFalse(self.open_window(flow).email_to)

    def test_without_the_email(self):
        flow = self.made_flow()
        window = self.open_window(flow)
        with self.mock_mail_gateway():
            window.action_without_email()
        self.assertTrue(self.partner.parent_id)
        self.assertFalse(self._mails)
        self.assertIn("without the email", self.partner.message_ids[0].body)
        flow.without_email = False
        with self.assertRaises(UserError):
            self.open_window(flow).action_without_email()

    def test_closed_to_those_the_original_button_is_closed_to(self):
        flow = self.made_flow()
        flow.button_groups = "base.group_system"
        with self.assertRaises(UserError):
            self.open_window(flow)

    def test_switched_off_and_deleted_with_its_button(self):
        flow = self.made_flow()
        view, action = flow.view_id, flow.action_id
        flow.active = False
        self.assertFalse(view.active, "The button leaves the form with it")
        result = self.run_tool("prepare_delete", {"model": "nudge.email.flow", "ids": flow.ids})
        self.assertFalse(result.is_error, result.content)
        self.do("prepare_delete", result)
        self.assertFalse(flow.exists() or view.exists() or action.exists())

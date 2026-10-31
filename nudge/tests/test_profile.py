import json
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.llm import base, loop, prompt
from odoo.addons.nudge.models.nudge_provider_cache import stable_first
from odoo.addons.nudge.profile import builder, collectors, retrieval

from .common import NudgeCase


def heard(call):
    """S8: what a call was told of the overview: its system prompt and, with the first question of a
    conversation, the rest of the overview in the context of that question"""
    question = next(turn for turn in reversed(call["turns"]) if turn["role"] == "user")
    return call["system"][1] + "\n" + (question.get("context") or "")

# Standard modules taken as if they were custom ones, to have something to
# find in a database that has none
AS_CUSTOM = {"web_tour"}


@tagged("post_install", "-at_install")
class TestCollectors(NudgeCase, TransactionCase):

    def slices(self, collector, custom=AS_CUSTOM):
        return {piece["key"]: piece for piece in collector(self.env, set(custom))}

    def test_custom_modules_are_those_outside_odoo(self):
        custom = collectors.custom_module_names(self.env)
        self.assertIn("nudge", custom)
        for standard in ("base", "web", "web_tour", "bus"):
            self.assertNotIn(standard, custom)

    def test_a_module_licensed_oeel_does_not_make_a_community_database_enterprise(self):
        # project_hr_skills ships in the Community tree under OEEL-1
        Module = self.env["ir.module.module"].sudo()
        if Module.search_count([("name", "=", "web_enterprise"), ("state", "=", "installed")]):
            self.skipTest("an Enterprise database")
        Module.search([("name", "=", "web_tour")]).license = "OEEL-1"
        self.assertFalse(collectors.is_enterprise(self.env))
        overview = self.slices(collectors.collect_modules)["overview"]
        self.assertEqual(overview["data"]["edition"], "Community")
        self.assertIn("Odoo 18.0 Community", overview["summary"])

    def test_overview(self):
        overview = self.slices(collectors.collect_modules)["overview"]
        edition = "Enterprise" if self.env["ir.module.module"].sudo().search_count(
            [("name", "=", "web_enterprise"), ("state", "=", "installed")]) else "Community"
        self.assertIn(f"Odoo 18.0 {edition}", overview["summary"])
        self.assertIn("Tours (web_tour)", overview["summary"])
        self.assertFalse(overview["restricted"])
        self.assertNotIn("nudge", [module["name"] for module in overview["data"]["custom_modules"]],
                         "Nudge does not describe itself as a customization")
        self.assertEqual(overview["data"]["edition"], edition)

        standard = self.slices(collectors.collect_modules, custom={"nudge"})["overview"]
        self.assertIn("No custom module", standard["summary"])

    def test_custom_module(self):
        module = self.slices(collectors.collect_modules)["module:web_tour"]
        self.assertEqual(module["kind"], "module")
        self.assertIn("custom module by Odoo S.A.", module["summary"])
        self.assertIn("web", module["data"]["depends"])
        self.assertIn("web_tour.tour", module["data"]["models"])

    def test_custom_model_and_fields_added_to_standard_ones(self):
        slices = self.slices(collectors.collect_models)

        tour = slices["model:web_tour.tour"]
        self.assertTrue(tour["data"]["custom"])
        self.assertEqual(tour["model_name"], "web_tour.tour")
        self.assertIn("custom kind of record, added by web_tour", tour["summary"])
        names = [field["name"] for field in tour["data"]["fields"]]
        self.assertIn("name", names)
        self.assertNotIn("create_uid", names)

        users = slices["model:res.users"]
        self.assertFalse(users["data"]["custom"])
        self.assertIn("standard Odoo model that this database extends", users["summary"])
        added = {field["name"]: field for field in users["data"]["fields"]}
        self.assertEqual(added["tour_enabled"]["added_by"], "web_tour")
        self.assertNotIn("login", added, "Standard fields are not repeated")
        self.assertNotIn("model:res.partner", slices, "Nothing to say of what is standard")
        self.assertFalse([key for key in slices if key.startswith("model:nudge.")])

    def test_field_created_in_the_database(self):
        self.env["ir.model.fields"].create({
            "name": "x_nudge_loyalty",
            "field_description": "Loyalty Level",
            "model_id": self.env.ref("base.model_res_partner").id,
            "ttype": "selection",
            "selection_ids": [
                (0, 0, {"value": "gold", "name": "Gold"}),
                (0, 0, {"value": "silver", "name": "Silver"}),
            ],
            "help": "How much we value this customer.",
        })
        partner = self.slices(collectors.collect_models, custom=set())["model:res.partner"]
        field = partner["data"]["fields"][0]
        self.assertEqual(field["name"], "x_nudge_loyalty")
        self.assertEqual(field["added_by"], "created in the database")
        self.assertEqual(field["choices"], ["Gold", "Silver"])
        self.assertIn("Loyalty Level (x_nudge_loyalty)", partner["summary"])

    def test_view_changed_in_the_database(self):
        self.env["ir.ui.view"].create({
            "name": "partner.form.nudge.test",
            "model": "res.partner",
            "inherit_id": self.env.ref("base.view_partner_form").id,
            "arch": """
                <field name="website" position="after">
                    <field name="ref"/>
                    <button name="open_commercial_entity" type="object" string="Check"/>
                </field>""",
        })
        partner = self.slices(collectors.collect_models, custom=set())["model:res.partner"]
        change = partner["data"]["view_changes"][0]
        self.assertEqual(change["by"], "edited in the database")
        self.assertEqual(change["fields"], ["ref", "website"])
        self.assertEqual(change["buttons"], ["open_commercial_entity"])
        self.assertIn("changes its form view", partner["summary"])

    def test_restricted_parts(self):
        security = self.slices(collectors.collect_security)["security"]
        automation = self.slices(collectors.collect_automation)["automation"]
        self.assertTrue(security["restricted"])
        self.assertTrue(automation["restricted"])
        self.assertGreater(security["data"]["internal_users"], 4)
        self.assertIn("scheduled actions are active", automation["summary"])
        # The numbers everybody reads carry no name
        self.assertIn("scheduled actions are active", automation["data"]["public"])
        self.assertIn(f"{security['data']['internal_users']} internal users", security["data"]["public"])
        for cron in automation["data"]["scheduled_actions"]:
            self.assertNotIn(cron["name"], automation["data"]["public"])

    def test_the_names_are_capped_and_the_counts_are_not(self):
        self.patch(collectors, "MAX_LISTED", 1)
        crons = self.env["ir.cron"].search_count([("active", "=", True)])
        self.assertGreater(crons, 1)
        automation = self.slices(collectors.collect_automation)["automation"]
        self.assertEqual(len(automation["data"]["scheduled_actions"]), 1)
        self.assertIn(f"{crons} scheduled actions are active", automation["summary"])
        self.assertIn(f"{crons} scheduled actions are active", automation["data"]["public"])

    def test_nothing_secret_is_read(self):
        self.env["ir.config_parameter"].set_param("nudge.test_secret", "s3cr3t-value")
        text = json.dumps(collectors.collect(self.env), default=str)
        for secret in ("s3cr3t-value", "sk-test-secret", self.env["ir.config_parameter"].get_param(
                "database.secret")):
            self.assertNotIn(secret, text)

    def test_a_failing_collector_does_not_hide_the_rest(self):
        def explode(env, custom):
            raise RuntimeError("boom")

        self.patch(collectors, "COLLECTORS", (explode, collectors.collect_modules))
        with self.assertLogs("odoo.addons.nudge.profile.collectors", "ERROR"):
            slices = collectors.collect(self.env)
        self.assertIn("overview", [piece["key"] for piece in slices])


@tagged("post_install", "-at_install")
class TestProfile(NudgeCase, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Profile = cls.env["nudge.profile"]
        cls.Profile.search([]).unlink()

    def use_custom(self, names=AS_CUSTOM):
        self.patch(collectors, "custom_module_names", lambda env: set(names) | {"nudge"})

    def test_versions(self):
        self.use_custom()
        first = builder.build(self.env, "install")
        self.assertTrue(first.is_current)
        self.assertFalse(first.changes, "Nothing to compare the first reading with")
        self.assertIn("Tours (web_tour)", first.overview_text)
        self.assertEqual(first.slice_count, len(first.slice_ids))
        self.assertTrue(first.fingerprint)

        second = builder.build(self.env, "manual")
        self.assertEqual(self.Profile._get_current(), second)
        self.assertFalse(first.is_current)
        self.assertEqual(second.changes, {"added": [], "removed": [], "changed": []})
        self.assertEqual(second.changes_text, "Nothing changed.")

        self.use_custom({"web_tour", "bus"})
        third = builder.build(self.env, "modules")
        self.assertIn("module:bus", third.changes["added"])
        self.assertIn("overview", third.changes["changed"])
        self.assertIn("Added: ", third.changes_text)

    def test_where_the_work_is_leaves_the_configuration_out(self):
        # Records people changed this week, of kinds that are set up once rather than worked in
        country = self.env.ref("base.ae")
        self.env["res.country.state"].create({"name": "Nudge State", "code": "NS", "country_id": country.id})
        self.env["res.currency.rate"].create({"currency_id": self.env.ref("base.EUR").id, "rate": 1.5,
                                              "name": fields.Date.today() - timedelta(days=3000)})
        self.env["resource.calendar"].create({"name": "Nudge Hours"})
        self.env["res.partner"].create({"name": "Nudge Busy Contact"})
        profile = builder.build(self.env, "manual")
        pieces = profile.slice_ids.filtered(lambda piece: piece.kind == "activity")
        for model in ("res.country.state", "res.currency.rate", "resource.calendar.attendance"):
            self.assertTrue(pieces.filtered(lambda piece: piece.model_name == model and piece.data.get("changed")),
                            f"{model} is changed, so only the rule can leave it out")
        work = retrieval.where_the_work_is(self.env, profile)
        self.assertIn("(res.partner)", work)
        for model in ("res.country.state", "res.currency.rate", "resource.calendar.attendance", "res.lang"):
            self.assertNotIn(f"({model})", work, model)
        for model in ("sale.order", "account.move", "stock.picking", "hr.leave", "project.task",
                      "account.move.line", "res.partner", "product.template"):
            self.assertFalse(retrieval.is_configuration(model), model)
        for model in ("account.tax.repartition.line", "account.tax", "uom.uom", "res.country.state",
                      "res.currency.rate", "res.lang", "resource.calendar.attendance", "res.partner.category"):
            self.assertTrue(retrieval.is_configuration(model), model)

    def test_old_versions_are_removed(self):
        for _index in range(builder.KEPT_VERSIONS + 3):
            builder.build(self.env, "manual")
        self.assertEqual(self.Profile.search_count([]), builder.KEPT_VERSIONS)
        self.assertEqual(self.Profile.search_count([("is_current", "=", True)]), 1)

    def test_corrections_are_kept(self):
        self.use_custom()
        first = builder.build(self.env, "manual")
        piece = first.slice_ids.filtered(lambda piece: piece.key == "module:web_tour")
        piece.with_user(self.manager).override_summary = "Tours teach new employees the basics."

        second = builder.build(self.env, "manual")
        piece = second.slice_ids.filtered(lambda piece: piece.key == "module:web_tour")
        self.assertEqual(piece._get_summary(), "Tours teach new employees the basics.")
        self.assertIn("custom module", piece.summary, "What was read stays next to the correction")

    def test_refresh_when_needed(self):
        self.assertEqual(self.Profile._is_outdated(), "install")
        self.Profile._cron_refresh()
        current = self.Profile._get_current()
        self.assertEqual(current.trigger, "install")
        self.assertFalse(self.Profile._is_outdated())

        self.Profile._cron_refresh()
        self.assertEqual(self.Profile._get_current(), current, "Nothing changed: nothing read")

        current.fingerprint = "modules were different"
        self.assertEqual(self.Profile._is_outdated(), "modules")
        current.fingerprint = builder.fingerprint(self.env)
        self.env.cr.execute(
            "UPDATE nudge_profile SET create_date = %s WHERE id = %s",
            [fields.Datetime.now() - timedelta(days=2), current.id])
        current.invalidate_recordset()
        self.assertEqual(self.Profile._is_outdated(), "scheduled")

    def test_managers_refresh_and_correct(self):
        profile = builder.build(self.env, "install")
        action = profile.with_user(self.manager).action_refresh()
        self.assertEqual(self.Profile.browse(action["res_id"]).trigger, "manual")
        for user in (self.user_a, self.outsider):
            with self.assertRaises(AccessError):
                profile.with_user(user).action_refresh()
            with self.assertRaises(AccessError):
                self.Profile.with_user(user).search([])
            with self.assertRaises(AccessError):
                self.env["nudge.note"].with_user(user).search([])

    def test_notes_open_every_conversation(self):
        self.use_custom()
        builder.build(self.env, "install")
        Note = self.env["nudge.note"].with_user(self.manager)
        Note.create({"name": "Words", "body": 'We call quotations\n"offers".', "sequence": 2})
        Note.create({"name": "Approval", "body": "Orders above 10,000 need the manager.", "sequence": 1})
        Note.create({"name": "Old", "body": "No longer true.", "active": False})

        overview = self.Profile.with_user(self.user_a)._get_overview()
        self.assertIn("## This database", overview)
        self.assertIn("Tours (web_tour)", overview)
        self.assertIn('- Words: We call quotations "offers".', overview)
        self.assertLess(overview.index("Approval"), overview.index("Words"))
        self.assertNotIn("No longer true", overview)

        adapter = self.use_adapter()
        self.ask(self.user_a, "Hello")
        system = adapter.calls[0]["system"]
        self.assertEqual(len(system), 4)
        self.assertIn("## Where the work is", system[2])
        kept, rest = prompt.split_overview(overview)
        self.assertEqual(system[1], kept)
        if rest:
            # S8: the rest of the overview goes with the first question of the conversation
            self.assertIn(prompt.render_overview_rest(rest), heard(adapter.calls[0]))
        self.ask(self.user_b, "Hello")
        self.assertEqual(adapter.calls[1]["system"], system, "The same for everybody who may know the same: read once")
        # What depends on the person comes after what is shared: a manager
        # reads more of the database, and their own rules
        self.ask(self.manager, "Hello")
        self.assertEqual(adapter.calls[2]["system"][:1], system[:1])
        self.assertIn("## Automation", heard(adapter.calls[2]))
        self.assertNotEqual(adapter.calls[2]["system"][-1], system[-1])

    def test_what_the_overview_tells_whom(self):
        self.use_custom()
        # A group of a custom module, and a company the person does not work in
        group = self.env["res.groups"].create({"name": "Nudge Tour Keepers"})
        self.env["ir.model.data"].create({
            "name": "group_nudge_tour_keepers", "module": "web_tour", "model": "res.groups", "res_id": group.id})
        self.env["res.company"].create({"name": "Nudge Second Company"})
        builder.build(self.env, "install")

        told = self.Profile.with_user(self.user_a)._get_overview()
        manager = self.Profile.with_user(self.manager)._get_overview()
        for text in (told, manager):
            self.assertIn("## This database", text)
            self.assertIn("scheduled actions are active", text, "The numbers are for everybody")
            self.assertIn("internal users", text)
        self.assertIn("1 group(s) added by custom modules", told, "The number, without the names")
        self.assertNotIn("Nudge Tour Keepers", told, "The names are not for a person who cannot read them")
        self.assertNotIn("Nudge Second Company", told, "Nor the companies they do not work in")
        self.assertIn("Groups added by custom modules: Nudge Tour Keepers", manager)
        self.assertIn("Companies: ", manager)
        self.assertIn("Nudge Second Company", manager)
        self.assertIn("Nudge Tour Keepers", self.Profile._get_overview(), "An administrator reads as a manager")
        # In the conversation too
        adapter = self.use_adapter()
        self.ask(self.user_a, "Hello")
        self.assertNotIn("Nudge Tour Keepers", "".join(adapter.calls[0]["system"]) + heard(adapter.calls[0]))
        self.assertIn("scheduled actions are active", heard(adapter.calls[0]))
        self.ask(self.manager, "Hello")
        self.assertIn("Nudge Tour Keepers", heard(adapter.calls[1]))

    def test_first_question_reads_the_database(self):
        self.assertFalse(self.Profile._get_current())
        adapter = self.use_adapter()
        self.ask(self.user_a, "Hello")
        self.assertEqual(self.Profile._get_current().trigger, "install")
        self.assertIn("## This database", adapter.calls[0]["system"][1])


@tagged("post_install", "-at_install")
class TestProfileTools(NudgeCase, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["nudge.profile"].search([]).unlink()
        custom = AS_CUSTOM | {"nudge"}
        cls.classPatch(collectors, "custom_module_names", lambda env: set(custom))
        cls.profile = builder.build(cls.env, "install")

    def run_tool(self, name, arguments, user=None):
        return tools.base.call(self.env(user=user or self.user_a), name, arguments)

    def keys(self, query, user=None, kind=None):
        env = self.env(user=user or self.user_a)
        return [piece.key for piece in retrieval.search(env, self.profile, query, kind)]

    def test_search(self):
        self.assertEqual(self.keys("tours")[0], "module:web_tour")
        self.assertEqual(self.keys("web_tour.tour")[0], "model:web_tour.tour")
        self.assertEqual(self.keys("tours", kind="model")[0], "model:web_tour.tour", "The tours first, then their steps")
        self.assertEqual(self.keys("zzzzzz"), [])
        self.assertEqual(self.keys(""), [])

    def test_people_are_told_what_they_may_know(self):
        self.assertTrue(self.env["res.users"].with_user(self.user_a).has_access("read"))
        self.assertIn("model:res.users", self.keys("users tour_enabled"))
        self.assertNotIn("security", self.keys("users access internal"))
        self.assertNotIn("automation", self.keys("scheduled actions"))
        self.assertIn("security", self.keys("users access internal", user=self.manager))
        self.assertIn("automation", self.keys("scheduled actions", user=self.manager))

        piece = self.profile.slice_ids.filtered(lambda piece: piece.key == "model:web_tour.tour")
        piece.model_name = "ir.cron"
        self.assertFalse(self.env["ir.cron"].with_user(self.user_a).has_access("read"))
        self.assertNotIn("model:web_tour.tour", self.keys("web_tour.tour"))
        piece.model_name = "model.that.is.gone"
        self.assertNotIn("model:web_tour.tour", self.keys("web_tour.tour", user=self.manager))

    def test_search_tool(self):
        result = self.run_tool("search_database_profile", {"query": "tours"})
        self.assertFalse(result.is_error)
        self.assertIn("## Tours [module:web_tour]", result.content)
        self.assertLessEqual(len(result.content), retrieval.MAX_CHARS + 200)

        result = self.run_tool("search_database_profile", {"query": "scheduled actions"})
        self.assertNotIn("scheduled actions are active", result.content)
        self.assertIn("Nothing in the profile", result.content)
        result = self.run_tool("search_database_profile", {"query": "x"})
        self.assertTrue(result.is_error, "Too short to mean anything")

    def test_describe_model(self):
        result = self.run_tool("describe_model", {"model": "res.users", "words": "tour"})
        described = json.loads(result.content)
        self.assertEqual(described["label"], "User")
        fields_found = {field["name"]: field for field in described["fields"]}
        self.assertEqual(fields_found["tour_enabled"]["added_by"], "web_tour")
        self.assertIn("this database extends", described["in_this_database"])

        result = self.run_tool("describe_model", {"model": "res.users"})
        self.assertLessEqual(len(result.content), tools.base.MAX_RESULT_CHARS)
        described = json.loads(result.content)
        self.assertEqual(described["fields"][0]["added_by"], "web_tour", "What is added comes first")
        self.assertLess(len(described["fields"]), described["fields_found"])
        self.assertIn("Give words", described["note"])

        # A field kept for a group is listed to the members of that group only
        arguments = {"model": "nudge.provider", "words": "key"}
        described = json.loads(self.run_tool("describe_model", arguments, user=self.manager).content)
        names = [field["name"] for field in described["fields"]]
        self.assertIn("has_api_key", names)
        self.assertNotIn("api_key", names)
        self.assertNotIn("note", described)
        admin = self.env.ref("base.user_admin")
        described = json.loads(self.run_tool("describe_model", arguments, user=admin).content)
        self.assertIn("api_key", [field["name"] for field in described["fields"]])
        self.assertNotIn("sk-test-secret", json.dumps(described), "Fields are described, not read")

    def test_describe_model_does_not_reveal_what_cannot_be_read(self):
        hidden = self.run_tool("describe_model", {"model": "ir.cron"})
        missing = self.run_tool("describe_model", {"model": "no.such.model"})
        self.assertTrue(hidden.is_error)
        self.assertEqual(hidden.content.replace("ir.cron", "X"), missing.content.replace("no.such.model", "X"))
        self.assertFalse(self.run_tool("describe_model", {"model": "ir.cron"},
                                       user=self.env.ref("base.user_admin")).is_error)
        for name in ("res.users; DROP", "Res.Users", "../etc", ""):
            self.assertTrue(self.run_tool("describe_model", {"model": name}).is_error)

    def test_answer_from_the_profile(self):
        adapter = self.use_adapter(script=[
            [base.tool_use_event("call_1", "search_database_profile", {"query": "tours"}),
             base.stop_event(base.STOP_TOOL_USE)],
            [base.text_event("Tours is a custom module here."), base.stop_event(base.STOP_END)],
        ])
        conversation = self.env["nudge.conversation"].with_user(self.user_a)._chat_open()
        job = conversation._chat_prepare("What is the tours module here?")
        events = list(loop.run(job, conversation._chat_record))

        self.assertEqual(events[-1]["state"], "complete")
        result = adapter.calls[1]["turns"][-1]["results"][0]
        self.assertIn("custom module by Odoo S.A.", result["content"])
        # NP-02: with the reading tools of the Ask tier; NP-03: the core of Talk and the groups of the
        # question (a module, a tour), not the tools of other work
        offered = [tool["name"] for tool in adapter.calls[0]["tools"]]
        self.assertLessEqual(
            {"aggregate_records", "describe_model", "describe_module", "filter_screen", "find_record",
             "find_screen", "locate_data", "more_tools", "open_record", "open_screen", "read_record",
             "remember_lesson", "search_company_files", "search_database_profile", "search_odoo_documentation",
             "search_records", "show_menu_path", "show_on_screen", "show_tour", "write_in_field"}, set(offered))
        self.assertFalse({"test_import", "summarize_record_history", "search_products", "assess_database",
                          "prepare_write", "prepare_create"} & set(offered), offered)
        # In one order, the list is part of the cached prompt: S8, those of every question first
        self.assertEqual(offered, [tool["name"] for tool in stable_first([{"name": name} for name in sorted(offered)], "talk")])

    def made_to_run(self, model_name, name):
        """:return: the domain of the server actions of ``model_name``, after one named ``name`` is made on it, in
        its Actions menu: after the profile was read"""
        model = self.env["ir.model"]._get(model_name)
        # First of its list, whatever the modules installed put on that model
        self.env["ir.actions.server"].create({"name": name, "model_id": model.id, "state": "code", "code": "pass",
                                              "binding_model_id": model.id, "sequence": 0})
        domain = [("usage", "=", "ir_actions_server"), ("model_name", "=", model_name)]
        if "base_automation_id" in self.env["ir.actions.server"]._fields:
            domain.append(("base_automation_id", "=", False))
        return domain

    def test_what_runs_on_a_model_is_read_now(self):
        # D14, filmed in Talk: "what runs by itself on quotations?" searched the automation of the profile for
        # sale.order and read 2 rules, on purchase.order: "no server actions", while sale.order had 8
        domain = self.made_to_run("res.partner.category", "Raghad: tag checked")
        count = self.env["ir.actions.server"].search_count(domain)
        told = f"Server actions (they run when someone picks them in the Actions menu or presses their button): {count}"
        for kind in ("automation", None):
            with self.subTest(kind=kind):
                arguments = {"query": "res.partner.category", **({"kind": kind} if kind else {})}
                manager = self.run_tool("search_database_profile", arguments, user=self.manager).content
                self.assertIn("## What runs on res.partner.category, read now", manager)
                self.assertIn(told + ": ", manager)
                self.assertIn("Raghad: tag checked", manager)
                self.assertEqual("Automation rules" in manager, "base.automation" in self.env)
                # The numbers for everybody, the names for the managers, as the automation of the profile
                user = self.run_tool("search_database_profile", arguments, user=self.user_a).content
                self.assertIn(told + ".", user)
                self.assertNotIn("Raghad: tag checked", user)
        # Not for the fields of a model, nor for words that name no model, nor for a model the person cannot read
        self.assertNotIn("What runs on", self.run_tool(
            "search_database_profile", {"query": "res.partner.category", "kind": "model"}, user=self.manager).content)
        self.assertNotIn("What runs on", self.run_tool(
            "search_database_profile", {"query": "tags of contacts"}, user=self.manager).content)
        self.assertNotIn("What runs on", self.run_tool(
            "search_database_profile", {"query": "ir.cron", "kind": "automation"}, user=self.user_a).content)
        # The profile says that server actions run too, numbers for everybody
        automation = collectors.collect_automation(self.env, set())[0]
        self.assertIn("server actions, run from an Actions menu or a button.", automation["data"]["public"])

    def test_what_runs_by_itself_on_quotations_in_talk(self):
        if "sale.order" not in self.env:
            self.skipTest("Sales is not installed")
        domain = self.made_to_run("sale.order", "Raghad: send the quotation again")
        count = self.env["ir.actions.server"].search_count(domain)
        manager = new_test_user(self.env, login="nudge_d14",
                                groups="nudge.group_nudge_manager,sales_team.group_sale_salesman")
        searched = {"query": "sale.order", "kind": "automation"}
        for question in ("What runs by itself on quotations?", "شو في أشياء بتشتغل لحالها على عروض الأسعار؟"):
            with self.subTest(question=question):
                adapter = self.use_adapter(script=[
                    [base.tool_use_event("call_1", "search_database_profile", searched),
                     base.stop_event(base.STOP_TOOL_USE)],
                    [base.text_event("Server actions run on quotations."), base.stop_event(base.STOP_END)],
                ])
                conversation = self.env["nudge.conversation"].with_user(manager)._chat_open()
                job = conversation._chat_prepare(question)
                events = list(loop.run(job, conversation._chat_record))
                self.assertEqual(events[-1]["state"], "complete")
                asked = self.env["nudge.message"].search([("conversation_id", "=", conversation.id),
                                                          ("role", "=", "user")])
                self.assertEqual(asked.mode, "talk", "Read in Talk, where read_automations is not offered")
                result = adapter.calls[1]["turns"][-1]["results"][0]
                self.assertFalse(result["is_error"], result)
                self.assertIn(f"Server actions (they run when someone picks them in the Actions menu or presses their "
                              f"button): {count}: ", result["content"])
                self.assertIn("Raghad: send the quotation again", result["content"])

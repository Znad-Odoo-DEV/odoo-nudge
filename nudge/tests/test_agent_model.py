"""Nudge Agents, the data model and its security: an agent never adds a right. Who may use it (audience and
company), the companies and tools it works with (an intersection with the person's own, never a developer
tool), the publish gate, the history of its changes and the limits of a service binding. No provider is reached."""
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge.models.nudge_agent import DEFAULT_READ_TOOLS, check_access
from odoo.addons.nudge.tools import base

from .common import NudgeCase


@tagged("post_install", "-at_install")
class TestAgentModel(NudgeCase, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Agent = cls.env["nudge.agent"]
        cls.company = cls.env.company
        cls.other = cls.env["res.company"].create({"name": "Agents Other Company"})
        cls.elsewhere = new_test_user(
            cls.env, login="nudge_agent_far", groups="nudge.group_nudge_agent,base.group_partner_manager",
            company_id=cls.other.id, company_ids=[(6, 0, [cls.other.id])])
        cls.both = new_test_user(
            cls.env, login="nudge_agent_both", groups="nudge.group_nudge_agent,base.group_partner_manager",
            company_ids=[(6, 0, [cls.company.id, cls.other.id])])

    def made(self, su=False, **values):
        values.setdefault("name", "Test agent")
        values.setdefault("company_ids", [(6, 0, [self.company.id])])
        return (self.Agent if su else self.Agent.with_user(self.manager)).create(values)

    def live(self, agent):
        agent.sudo().write({"state": "published"})
        return agent

    def assertRefused(self, call):
        """``call`` raises a ValidationError or an AccessError: which one comes first is not the same on every
        version, and the assertRaises of Odoo takes a single class"""
        try:
            with self.env.cr.savepoint():
                call()
        except (ValidationError, AccessError):
            return
        self.fail("Not refused")

    # ---------------------------------------------------------------- audience and company
    def test_partner_made_and_kept(self):
        agent = self.made(name="Sara")
        self.assertTrue(agent.partner_id)
        self.assertEqual(agent.partner_id.name, "Sara")
        self.assertFalse(agent.partner_id.user_ids, "The face of an agent is a contact, never a user")
        agent.name = "Sara Two"
        self.assertEqual(agent.partner_id.name, "Sara Two")

    def test_can_use_audience_and_company(self):
        agent = self.live(self.made(user_ids=[(6, 0, [self.user_a.id, self.elsewhere.id])]))
        self.assertTrue(agent._can_use(self.user_a))
        self.assertFalse(agent._can_use(self.user_b), "Not in the audience")
        self.assertFalse(agent._can_use(self.elsewhere), "In the audience, but of another company")
        by_group = self.live(self.made(name="By group", group_ids=[(6, 0, [self.env.ref("nudge.group_nudge_agent").id])]))
        self.assertTrue(by_group._can_use(self.agent))
        self.assertFalse(by_group._can_use(self.user_a))
        nobody = self.live(self.made(name="Nobody"))
        self.assertFalse(nobody._can_use(self.user_a), "No audience: nobody but the managers who see it")

    def test_record_rules(self):
        agent = self.made(user_ids=[(6, 0, [self.user_a.id, self.elsewhere.id])])
        Agent = self.Agent
        self.assertIn(agent, Agent.with_user(self.user_a).search([]))
        self.assertNotIn(agent, Agent.with_user(self.user_b).search([]), "Hidden from users outside the audience")
        self.assertNotIn(agent, Agent.with_user(self.elsewhere).search([]), "Hidden from users outside the companies")
        self.assertIn(agent, Agent.with_user(self.manager).search([]), "Managers see it, in their companies")
        far_manager = new_test_user(
            self.env, login="nudge_agent_mfar", groups="nudge.group_nudge_manager",
            company_id=self.other.id, company_ids=[(6, 0, [self.other.id])])
        self.assertNotIn(agent, Agent.with_user(far_manager).search([]))
        with self.assertRaises(AccessError):
            agent.with_user(self.user_a).write({"name": "Mine now"})
        with self.assertRaises(AccessError):
            Agent.with_user(self.user_a).create({"name": "x", "company_ids": [(6, 0, [self.company.id])]})

    def test_companies_for_is_an_intersection(self):
        agent = self.made(su=True, company_ids=[(6, 0, [self.company.id, self.other.id])])
        self.assertEqual(agent._companies_for(self.user_a), self.company)
        self.assertEqual(agent._companies_for(self.elsewhere), self.other)
        self.assertEqual(agent._companies_for(self.both), self.company | self.other)
        narrow = self.made(su=True, name="Narrow", company_ids=[(6, 0, [self.other.id])])
        self.assertFalse(narrow._companies_for(self.user_a), "No company in common: none")
        self.assertEqual(narrow._companies_for(self.both), self.other)

    # ---------------------------------------------------------------- tools
    def test_tools_never_include_a_developer_tool(self):
        admin = self.env.ref("base.user_admin")
        agent = self.made(user_ids=[(6, 0, [admin.id])])
        for mode in ("talk", "agent", "dev"):
            names = set(agent._tools_for(admin, mode))
            for name in names:
                tool = base.TOOLS[name]
                self.assertNotEqual(tool.group, "base.group_system", name)
                self.assertNotEqual(tool.kind, "code", name)
            self.assertFalse(names & {"run_python", "run_sql", "try_automation", "prepare_automation"}, mode)
        self.assertEqual(agent._tools_for(admin, "dev"), [], "Developer mode is not for agents")
        with self.assertRaises(ValidationError):
            agent.tool_keys = "run_sql"
        with self.assertRaises(ValidationError):
            agent.tool_keys = "no_such_tool_here"

    def test_default_tools_are_the_read_tools(self):
        agent = self.made()
        talk = agent._tools_for(self.user_contacts, "talk")
        self.assertEqual(talk, sorted(talk))
        for name in talk:
            self.assertIn(base.TOOLS[name].kind, ("read", "show"), name)
            self.assertIn(name, DEFAULT_READ_TOOLS, name)
        offered = {tool.name for tool in base.available(self.env(user=self.user_contacts), "talk")}
        self.assertTrue(set(talk) <= offered)
        as_agent = agent._tools_for(self.agent, "agent")
        self.assertTrue(as_agent)
        self.assertNotIn("prepare_write", as_agent, "Writing needs the tool to be named")

    def test_tools_shrink_with_tool_keys_and_never_grow_past_the_person(self):
        agent = self.made()
        everything = set(agent._tools_for(self.agent, "agent"))
        agent.tool_keys = "search_records, read_record"
        self.assertEqual(agent._tools_for(self.agent, "agent"), ["read_record", "search_records"])
        agent.tool_keys = "search_records,prepare_write"
        self.assertEqual(agent._tools_for(self.agent, "agent"), ["prepare_write", "search_records"])
        # A person without the Agent group is not given the writing tool because the agent names it
        self.assertNotIn("prepare_write", agent._tools_for(self.user_a, "agent"))
        self.assertNotIn("prepare_write", agent._tools_for(self.user_a, "talk"))
        self.assertTrue(set(agent._tools_for(self.agent, "agent")) <= everything | {"prepare_write"})

    # ---------------------------------------------------------------- publish gate
    def test_publish_gate(self):
        agent = self.made()
        with self.assertRaises(UserError):
            agent.action_publish()  # no evaluation at all
        evaluation = self.env["nudge.agent.eval"].with_user(self.manager).create({
            "agent_id": agent.id, "question": "Hello?", "expected": "answered"})
        evaluation.sudo().passed = True
        agent.sudo().score = 79.0
        with self.assertRaises(UserError):
            agent.action_publish()
        agent.sudo().score = 80.0
        evaluation.sudo().passed = False
        with self.assertRaises(UserError):
            agent.action_publish()
        evaluation.sudo().passed = True
        agent.action_publish()
        self.assertEqual(agent.state, "published")
        with self.assertRaises(UserError):
            self.made(name="Sneaky").write({"state": "published"})
        agent.action_archive()
        self.assertEqual(agent.state, "archived")
        agent.action_draft()
        self.assertEqual(agent.state, "draft")
        with self.assertRaises(AccessError):
            agent.with_user(self.user_a).action_publish()

    # ---------------------------------------------------------------- revisions
    def test_changes_are_kept(self):
        agent = self.made(instructions="Be brief.")
        self.assertEqual(len(agent.revision_ids), 1, "Created")
        agent.with_user(self.manager).write({"instructions": "Be brief and kind.", "tool_keys": "search_records"})
        # The newest: a revision made in the same transaction is added at the end of the cached list, whatever
        # the order of the model
        last = agent.revision_ids.sorted("id")[-1]
        self.assertEqual(last.user_id, self.manager)
        self.assertIn("Be brief and kind.", last.diff_shown)
        self.assertIn("search_records", last.diff_shown)
        count = len(agent.revision_ids)
        agent.write({"role": "Helper", "greeting": "Hi"})
        self.assertEqual(len(agent.revision_ids), count, "Wording of the face is not tracked")
        agent.write({"group_ids": [(6, 0, [self.env.ref("nudge.group_nudge_user").id])]})
        agent.write({"user_ids": [(4, self.user_a.id)]})
        agent.sudo().write({"company_ids": [(4, self.other.id)]})
        self.assertEqual(len(agent.revision_ids), count + 3)
        with self.assertRaises(AccessError):
            self.env["nudge.agent.revision"].with_user(self.user_a).search([])

    # ---------------------------------------------------------------- bindings
    def binding(self, agent, **values):
        values.setdefault("channel", "discuss")
        return self.env["nudge.agent.binding"].with_user(self.manager).create(dict(values, agent_id=agent.id))

    def service_user(self, login="nudge_agent_service", groups="nudge.group_agent_service"):
        return new_test_user(self.env, login=login, groups=groups)

    def test_service_binding_constraints(self):
        agent = self.made()
        self.binding(agent)  # a person on Discuss is fine
        with self.assertRaises(ValidationError):
            self.binding(agent, acts_as="service", service_user_id=self.service_user().id)  # on Discuss
        with self.assertRaises(ValidationError):
            self.binding(agent, channel="website", acts_as="service")  # no service user
        with self.assertRaises(ValidationError):
            self.binding(agent, channel="website", acts_as="service", service_user_id=self.user_a.id)
        with self.assertRaises(ValidationError):  # holds the Nudge user group beside the service group
            self.binding(agent, channel="website", acts_as="service", service_user_id=self.service_user(
                "nudge_agent_service2", "nudge.group_agent_service,nudge.group_nudge_user").id)
        ok = self.binding(agent, channel="website", acts_as="service", model_allowlist="product.template",
                          service_user_id=self.service_user("nudge_agent_service3").id)
        self.assertFalse(ok.published)
        self.assertEqual(len(agent.binding_ids), 2)
        self.assertIn("channel", agent.revision_ids[0].diff_shown.lower())

    def test_only_a_system_administrator_publishes_a_service_binding(self):
        agent = self.made()
        service = self.binding(agent, channel="website", acts_as="service",
                               service_user_id=self.service_user("nudge_agent_service4").id)
        with self.assertRaises(AccessError):
            service.with_user(self.manager).write({"published": True})
        service.with_user(self.env.ref("base.user_admin")).write({"published": True})
        self.assertTrue(service.published)
        # A manager who changes what a published service binding may read takes it back to unpublished
        service.with_user(self.manager).write({"model_allowlist": "product.template"})
        self.assertFalse(service.published)
        # A person binding is published by a manager
        person = self.binding(agent)
        person.write({"published": True})
        self.assertTrue(person.published)
        with self.assertRaises(AccessError):
            self.env["nudge.agent.binding"].with_user(self.user_a).search([])

    # ---------------------------------------------------------------- shipped data and wizard
    def test_templates_are_drafts_without_bindings(self):
        found = self.Agent.sudo().search([("recipe_key", "like", "nudge-agent-template-")])
        self.assertGreaterEqual(len(found), 4)
        for agent in found:
            self.assertEqual(agent.state, "draft")
            self.assertFalse(agent.binding_ids)
            self.assertFalse(agent.tool_keys)
        accounting = found.filtered(lambda a: a.recipe_key.endswith("accounting"))
        self.assertEqual(len(accounting.skill_ids), 8)

    def test_access_preview(self):
        agent = self.made(user_ids=[(6, 0, [self.user_a.id])])
        wizard = self.env["nudge.agent.preview"].with_user(self.manager).create({
            "agent_id": agent.id, "user_id": self.user_a.id})
        self.assertIn(self.company.name, wizard.preview)
        self.assertIn(agent.name, wizard.preview)
        self.assertTrue(wizard.can_use)
        wizard.user_id = self.user_b
        self.assertFalse(wizard.can_use)
        self.assertTrue(wizard.preview)

    # ---------------------------------------------------------------- security findings
    def test_partner_id_cannot_be_forged(self):
        admin_partner = self.env.ref("base.user_admin").partner_id
        with self.assertRaises(AccessError):
            self.made(partner_id=admin_partner.id)
        agent = self.made()
        with self.assertRaises(AccessError):
            agent.write({"partner_id": admin_partner.id})
        # Superuser may set it; even then, the sync never renames a user's or a company's partner
        agent.sudo().write({"partner_id": admin_partner.id})
        before = admin_partner.name
        agent.write({"name": "Hijack"})
        self.assertEqual(admin_partner.name, before)
        company_partner = self.company.partner_id
        agent.sudo().write({"partner_id": company_partner.id})
        before = company_partner.name
        agent.write({"name": "Hijack two"})
        self.assertEqual(company_partner.name, before)

    def test_preview_is_for_managers_and_safe_targets(self):
        agent = self.made(user_ids=[(6, 0, [self.user_a.id])])
        Preview = self.env["nudge.agent.preview"]
        with self.assertRaises(AccessError):
            Preview.with_user(self.user_a).default_get(["agent_id"])
        with self.assertRaises(AccessError):
            Preview.with_user(self.user_a).new({"agent_id": agent.id, "user_id": self.user_a.id})._build()
        root = self.env.ref("base.user_root")
        with self.assertRaises(AccessError):
            Preview.with_user(self.manager).new({"agent_id": agent.id, "user_id": root.id})._build()
        with self.assertRaises(AccessError):  # a system user, by a manager who is not one
            Preview.with_user(self.manager).new(
                {"agent_id": agent.id, "user_id": self.env.ref("base.user_admin").id})._build()
        allowed, html = Preview.with_user(self.manager).new({"agent_id": agent.id, "user_id": self.user_a.id})._build()
        self.assertTrue(allowed)

    def test_preview_shows_only_companies_of_the_caller(self):
        agent = self.made(su=True, user_ids=[(6, 0, [self.both.id])],
                          company_ids=[(6, 0, [self.company.id, self.other.id])])
        _allowed, html = self.env["nudge.agent.preview"].with_user(self.manager).new(
            {"agent_id": agent.id, "user_id": self.both.id})._build()
        self.assertIn(self.company.name, html)
        self.assertNotIn(self.other.name, html)

    def test_child_rules_follow_agent_companies(self):
        agent = self.made(su=True, company_ids=[(6, 0, [self.other.id])])
        self.env["nudge.agent.eval"].create({"agent_id": agent.id, "question": "Q"})
        self.env["nudge.agent.binding"].create({"agent_id": agent.id, "channel": "discuss"})
        for name in ("nudge.agent.eval", "nudge.agent.binding", "nudge.agent.revision"):
            self.assertFalse(self.env[name].with_user(self.manager).search([("agent_id", "=", agent.id)]), name)
        # a manager cannot push into a foreign company
        self.assertRefused(lambda: self.made(company_ids=[(6, 0, [self.other.id])]))
        mine = self.made()
        self.assertRefused(lambda: mine.write({"company_ids": [(4, self.other.id)]}))
        with self.assertRaises(AccessError):  # an agent partly in a foreign company is not theirs to change
            self.made(su=True, company_ids=[(6, 0, [self.company.id, self.other.id])]).with_user(
                self.manager).write({"name": "Mine now"})

    def test_service_channel_publication_and_users(self):
        agent = self.made()
        service = self.binding(agent, channel="website", acts_as="service",
                               service_user_id=self.service_user("nudge_agent_service5").id)
        self.assertTrue(service._service_user_ok())
        for channel in ("website", "whatsapp", "telegram", "mcp"):
            with self.assertRaises(AccessError, msg=channel):
                self.binding(agent, channel=channel, published=True)
        mcp = self.binding(agent, channel="mcp")
        with self.assertRaises(AccessError):
            mcp.write({"published": True})
        manager_group = self.service_user(
            "nudge_agent_service6", "nudge.group_agent_service,base.group_erp_manager")
        with self.assertRaises(ValidationError):
            self.binding(agent, channel="website", acts_as="service", service_user_id=manager_group.id)
        # An administrator gives the service user a group afterwards (a Nudge manager may not change users)
        service.service_user_id.sudo().write({"group_ids" if "group_ids" in service.service_user_id._fields
                                              else "groups_id": [(4, self.env.ref("base.group_erp_manager").id)]})
        self.assertFalse(service._service_user_ok(), "Asked again at answer time")

    def test_agent_change_unpublishes_channels(self):
        agent = self.made()
        mcp = self.binding(agent, channel="mcp")
        mcp.sudo().published = True
        discuss = self.binding(agent)
        discuss.published = True
        count = len(agent.revision_ids)
        agent.with_user(self.manager).write({"instructions": "New rules"})
        self.assertFalse(mcp.published)
        self.assertTrue(discuss.published)
        self.assertGreater(len(agent.revision_ids), count)
        self.assertTrue(any("unpublished" in (r.what or "").lower() for r in agent.revision_ids))

    def test_model_allowlist_denies_sensitive_and_unknown_models(self):
        agent = self.made()
        user = self.service_user("nudge_agent_service7")
        for bad in ("res.users", "res.partner.bank", "ir.config_parameter", "hr.employee", "payment.provider",
                    "account.move", "res.groups", "no.such.model"):
            with self.assertRaises(ValidationError, msg=bad):
                self.binding(agent, channel="website", acts_as="service", service_user_id=user.id,
                             model_allowlist=f"product.template,{bad}")
        self.binding(agent, channel="website", acts_as="service", service_user_id=user.id,
                     model_allowlist="product.template")

    def test_publish_gate_cannot_be_forged(self):
        agent = self.made()
        evaluation = self.env["nudge.agent.eval"].with_user(self.manager).create({
            "agent_id": agent.id, "question": "Q"})
        with self.assertRaises(AccessError):
            evaluation.write({"passed": True})
        with self.assertRaises(AccessError):
            evaluation.write({"last_answer": "forged"})
        with self.assertRaises(AccessError):
            self.env["nudge.agent.eval"].with_user(self.manager).create({
                "agent_id": agent.id, "question": "Q2", "passed": True})
        with self.assertRaises(AccessError):
            agent.write({"score": 100.0})
        with self.assertRaises(AccessError):
            agent.write({"state": "tested"})
        agent.sudo().write({"score": 100.0, "state": "tested"})  # the superuser (the runner, tests) may
        self.assertEqual(agent.state, "tested")

    def test_unpublished_agent_is_for_managers_only(self):
        agent = self.made(user_ids=[(6, 0, [self.user_a.id])])
        self.assertFalse(agent._can_use(self.user_a), "Draft")
        self.assertTrue(agent._can_use(self.manager), "Rehearsal")
        self.live(agent)
        self.assertTrue(agent._can_use(self.user_a))
        agent.sudo().write({"active": False})
        self.assertFalse(agent._can_use(self.user_a), "Archived")
        self.assertTrue(agent._can_use(self.manager))

    def test_default_tools_are_an_explicit_list(self):
        self.assertNotIn("remember_lesson", DEFAULT_READ_TOOLS)
        self.assertNotIn("write_in_field", DEFAULT_READ_TOOLS)
        agent = self.made()
        for mode in ("talk", "agent"):
            self.assertFalse(set(agent._tools_for(self.agent, mode)) - DEFAULT_READ_TOOLS, mode)

    def test_tool_catalogue_hides_developer_tools_and_marks_risk(self):
        catalogue = {item["name"]: item for item in self.Agent.tool_catalogue()}
        for name, tool in base.TOOLS.items():
            from odoo.addons.nudge.models.nudge_agent import developer_tool
            if developer_tool(tool):
                self.assertNotIn(name, catalogue, name)
        for name, item in catalogue.items():
            if item["risk"] == "read":
                self.assertIn(name, DEFAULT_READ_TOOLS, name)

    def test_agents_are_archived_not_deleted(self):
        agent = self.made()
        with self.assertRaises(AccessError):
            agent.with_user(self.user_a).unlink()
        with self.assertRaises(UserError):
            agent.unlink()  # a manager
        agent.sudo().unlink()
        self.assertFalse(agent.exists())

    # ---------------------------------------------------------------- second security review
    def test_a_scheduled_task_is_never_given_to_an_agent(self):
        # 2.2: a task runs later as plain Nudge, outside the agent
        agent = self.made()
        with self.assertRaises(ValidationError):
            agent.tool_keys = "search_records,prepare_task"
        self.assertNotIn("prepare_task", agent._tools_for(self.agent, "agent"))
        self.assertNotIn("prepare_task", {item["name"] for item in self.Agent.tool_catalogue()})
        # Refused whatever list the context carries
        named = self.env(user=self.agent, context=dict(
            self.env.context, nudge_agent_tools=("prepare_task", "search_records")))
        self.assertFalse(base.agent_allows(named, "prepare_task"))
        self.assertTrue(base.agent_allows(named, "search_records"))
        self.assertTrue(base.agent_allows(self.env(user=self.agent), "prepare_task"), "Plain Nudge keeps it")

    def test_a_shared_agent_is_managed_only_by_one_of_all_its_companies(self):
        # 2.3: the record rules show it to a manager of one of its companies; changing it needs all of them
        shared = self.made(su=True, company_ids=[(6, 0, [self.company.id, self.other.id])])
        evaluation = self.env["nudge.agent.eval"].create({"agent_id": shared.id, "question": "Q"})
        binding = self.env["nudge.agent.binding"].create({"agent_id": shared.id, "channel": "discuss"})
        evaluation.sudo().write({"passed": True})
        shared.sudo().write({"score": 100.0})
        Eval, Binding = self.env["nudge.agent.eval"].with_user(self.manager), self.env["nudge.agent.binding"].with_user(
            self.manager)
        self.assertIn(evaluation, Eval.search([("agent_id", "=", shared.id)]), "Seen, as the rule says")
        with self.assertRaises(AccessError):
            Eval.create({"agent_id": shared.id, "question": "Mine"})
        with self.assertRaises(AccessError):
            evaluation.with_user(self.manager).write({"question": "Changed"})
        with self.assertRaises(AccessError):
            evaluation.with_user(self.manager).unlink()
        with self.assertRaises(AccessError):
            Binding.create({"agent_id": shared.id, "channel": "mcp"})
        with self.assertRaises(AccessError):
            binding.with_user(self.manager).write({"published": True})
        with self.assertRaises(AccessError):
            binding.with_user(self.manager).unlink()
        as_manager = shared.with_user(self.manager)
        for button in ("action_publish", "action_archive", "action_draft", "action_run_evaluation"):
            with self.assertRaises(AccessError, msg=button):
                getattr(as_manager, button)()
        self.assertEqual(shared.state, "draft")
        # A manager of both companies may
        both_manager = new_test_user(
            self.env, login="nudge_agent_mboth", groups="nudge.group_nudge_manager",
            company_ids=[(6, 0, [self.company.id, self.other.id])])
        shared.with_user(both_manager).action_publish()
        self.assertEqual(shared.state, "published")
        # The changes are read in full by them only
        revision = shared.revision_ids[:1]
        self.assertIn("Shown only", revision.with_user(self.manager).diff_shown)
        self.assertEqual(revision.with_user(both_manager).diff_shown, revision.sudo().diff)
        with self.assertRaises(AccessError):
            revision.with_user(self.manager).read(["diff"])

    def test_an_evaluation_answer_is_shown_to_its_runner_only(self):
        # 2.3: the answer was given with the rights of the one who ran it
        self.use_adapter(reply="The answer of the runner.")
        agent = self.made(user_ids=[(6, 0, [self.manager.id])])
        evaluation = self.env["nudge.agent.eval"].with_user(self.manager).create({
            "agent_id": agent.id, "question": "Hello?", "expected": "answered"})
        agent.action_run_evaluation()
        self.assertEqual(evaluation.sudo().run_by, self.manager)
        self.assertEqual(evaluation.answer_shown, "The answer of the runner.")
        other = new_test_user(self.env, login="nudge_agent_m2", groups="nudge.group_nudge_manager")
        seen = evaluation.with_user(other)
        self.assertTrue(seen.passed)
        self.assertNotIn("runner", seen.answer_shown)
        with self.assertRaises(AccessError):
            seen.read(["last_answer"])
        with self.assertRaises(AccessError):
            evaluation.write({"run_by": other.id})

    def test_state_and_score_cannot_be_set_at_creation(self):
        # 2.4: the publish gate is not passed by create
        for vals in ({"state": "published"}, {"state": "tested"}, {"score": 100.0}):
            with self.assertRaises(AccessError, msg=vals):
                self.made(name="Gate", **vals)
        with self.assertRaises(AccessError):
            self.Agent.with_user(self.manager).with_context(default_state="published").create({
                "name": "Gate by default", "company_ids": [(6, 0, [self.company.id])]})
        # What the form sends for a new agent: a draft with no score
        self.assertEqual(self.made(name="From the form", state="draft", score=0.0).state, "draft")
        self.assertEqual(self.made(su=True, name="Shipped", state="published").state, "published",
                         "Nudge's own code (data, tests) may")

    def test_access_check_works_on_every_version(self):
        # 2.10: check_access of Odoo 18 and 19, its two halves on 17
        agent = self.made()
        with self.assertRaises(AccessError):
            check_access(agent.with_user(self.user_a), "write")
        check_access(agent.with_user(self.manager), "write")

        class Seventeen:
            """A recordset of Odoo 17: no check_access"""
            def __init__(self):
                self.checked = []

            def check_access_rights(self, operation):
                self.checked.append(("rights", operation))

            def check_access_rule(self, operation):
                self.checked.append(("rule", operation))

        old = Seventeen()
        check_access(old, "write")
        self.assertEqual(old.checked, [("rights", "write"), ("rule", "write")])

    def test_model_allowlist_denies_messages_people_and_nudge(self):
        # 2.11: what a service channel never reads, whatever its list says
        agent = self.made()
        user = self.service_user("nudge_agent_service8")
        for bad in ("mail.message", "mail.mail", "discuss.channel", "res.partner", "res.users.apikeys",
                    "nudge.conversation", "nudge.memory", "hr.payslip", "hr.leave"):
            with self.assertRaises(ValidationError, msg=bad):
                self.binding(agent, channel="website", acts_as="service", service_user_id=user.id,
                             model_allowlist=f"product.template,{bad}")

    def test_a_changed_skill_takes_the_public_channels_down(self):
        # 2.11: a skill the agent names steers what its public channels say
        skill = self.env["nudge.skill"].create({
            "name": "Tiles", "key": "agent-tiles", "summary": "About tiles.", "modes": "talk,agent",
            "instructions": "## Tiles\n- Say how long they take.\n", "state": "published", "trigger_words": "tiles"})
        agent = self.made(skill_ids=[(6, 0, [skill.id])])
        mcp = self.binding(agent, channel="mcp")
        mcp.sudo().published = True
        discuss = self.binding(agent)
        discuss.published = True
        skill.with_user(self.manager).write({"sequence": 5})
        self.assertTrue(mcp.published, "What does not steer leaves it")
        skill.with_user(self.manager).write({"instructions": "## Tiles\n- Say they are free.\n"})
        self.assertFalse(mcp.published)
        self.assertTrue(discuss.published, "Discuss is published by a manager")
        self.assertTrue(any("skill" in (revision.what or "").lower() or "skill" in (revision.diff_shown or "").lower()
                            for revision in agent.revision_ids))
        mcp.sudo().published = True
        skill.with_user(self.env.ref("base.user_admin")).write({"instructions": "## Tiles\n- Say it plainly.\n"})
        self.assertTrue(mcp.published, "An administrator's change keeps it")

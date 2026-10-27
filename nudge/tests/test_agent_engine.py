"""Nudge Agents, the engine: a conversation with an agent only ever takes away from what the person has.

Every turn checks that the agent is open to the person (published, audience, company; a draft only in a
rehearsal by a manager who may edit it), works in the companies the person selected that are also the agent's,
offers and runs only the agent's tools, puts the agent's persona after Nudge's own rules, masks personal data
when the agent says so, answers with the agent's provider, answers without AI once a budget is used up, refuses
Developer mode, and in a rehearsal (Try it) writes nothing. What a turn may do is read from the conversation:
the answers of the model are recorded here as the controller records them, on an environment that carries no
agent at all. The provider is the scripted adapter of the tests: nothing leaves the machine."""
import json

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge.llm import base, loop, prompt
from odoo.addons.nudge.models import nudge_agent_plan, nudge_run
from odoo.addons.nudge.tools import base as tools_base, documentation

from .common import NudgeCase

FAR = "Nudge Agents Far Partner"
NEAR = "Nudge Agents Near Partner"
REHEARSED = "Nudge Agents Rehearsed Partner"
LESSON = "Quotations above ten thousand need the sales manager first."
HOW = "How do I create a quotation in Sales?"
SAME = "how can I create quotations in sales"
STEPS = "Open Sales, press New, pick the customer and add the products."


def call(name, arguments, call_id="call_1"):
    return base.tool_use_event(call_id, name, arguments)


def asking(*events):
    """One request to the model that ends with calls to tools"""
    return [*events, base.stop_event(base.STOP_TOOL_USE)]


def saying(text):
    return [base.text_event(text), base.stop_event(base.STOP_END)]


@tagged("post_install", "-at_install")
class TestAgentEngine(NudgeCase, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.other = cls.env["res.company"].create({"name": "Agents Engine Other Company"})
        # Works in both companies: an agent of one of them must never show the other
        cls.both = new_test_user(
            cls.env, login="nudge_engine_both", groups="nudge.group_nudge_agent,base.group_partner_manager",
            company_ids=[(6, 0, [cls.company.id, cls.other.id])])
        # A manager of Nudge who may edit the agents of the company: the one who tries them
        cls.rehearser = new_test_user(
            cls.env, login="nudge_engine_rehearser", groups="nudge.group_nudge_manager,base.group_partner_manager")
        cls.Usage = cls.env["nudge.usage"]

    def made(self, state="published", **values):
        values.setdefault("name", "Engine agent")
        values.setdefault("company_ids", [(6, 0, [self.company.id])])
        agent = self.env["nudge.agent"].with_user(self.manager).create(values)
        if state != "draft":
            # The publish gate (an evaluation) is the model's, tested there
            agent.sudo().write({"state": state})
        return agent

    def talk(self, user, agent, text, conversation_id=None, mode="talk", rehearsal=False):
        """One question to ``agent`` the way the panel asks it: the agent in the context of the question only.
        Each answer of the model is recorded as the controller records it, on an environment of its own that
        carries no agent and no rehearsal: what the turn may do comes from the conversation.
        :return: (conversation, events)"""
        Conversation = self.env["nudge.conversation"].with_user(user).with_context(
            nudge_agent_id=agent.id, nudge_rehearsal=rehearsal)
        conversation = Conversation._chat_open(conversation_id)
        job = conversation._chat_prepare(text, mode=mode)
        plain = self.env["nudge.conversation"].with_user(user).browse(conversation.id)
        return conversation, list(loop.run(job, plain._chat_record))

    @staticmethod
    def tool_text(conversation):
        """:return: everything the tools answered in ``conversation``, as one text"""
        messages = conversation.sudo().message_ids.filtered(lambda message: message.role == "tool")
        return "\n".join(
            str(result.get("content") or "") for message in messages for result in message.tool_results or [])

    def conversation_of(self, result):
        return self.env["nudge.conversation"].browse(result["conversation_id"]).sudo()

    # ------------------------------------------------------------------ who
    def test_the_agent_is_attached_once_and_never_changed(self):
        agent = self.made(user_ids=[(6, 0, [self.user_a.id])])
        other = self.made(name="Other engine agent", user_ids=[(6, 0, [self.user_a.id])])
        self.use_adapter(reply="Fine.")
        conversation, _events = self.talk(self.user_a, agent, "Hello")
        self.assertEqual(conversation.sudo().agent_id, agent)
        with self.assertRaises(AccessError):
            conversation.with_user(self.manager).write({"agent_id": other.id})
        with self.assertRaises(AccessError):
            conversation.sudo().write({"agent_id": other.id})
        with self.assertRaises(AccessError):
            conversation.sudo().write({"agent_rehearsal": True})
        # A manager may create a conversation, but not choose its agent: that is the engine's, after its checks
        made = self.env["nudge.conversation"].with_user(self.manager).create({"agent_id": agent.id})
        self.assertFalse(made.sudo().agent_id)

    def test_a_conversation_keeps_its_agent_and_what_it_began_as(self):
        agent = self.made(user_ids=[(6, 0, [self.user_a.id, self.rehearser.id])])
        other = self.made(name="Other engine agent", user_ids=[(6, 0, [self.user_a.id])])
        self.use_adapter(reply="Fine.")
        conversation, _events = self.talk(self.user_a, agent, "Hello")
        Conversation = self.env["nudge.conversation"].with_user(self.user_a)
        with self.assertRaisesRegex(UserError, "not one of this agent"):
            Conversation.with_context(nudge_agent_id=other.id)._chat_open(conversation.id)
        # History reopens a conversation of plain Nudge while the panel is open for an agent
        plain, _last = self.ask(self.user_a, "Hello")
        with self.assertRaisesRegex(UserError, "not one of this agent"):
            Conversation.with_context(nudge_agent_id=agent.id)._chat_open(plain.id)
        # A real conversation does not become a rehearsal because the panel says so
        live, _events = self.talk(self.rehearser, agent, "Hello")
        with self.assertRaisesRegex(UserError, "not a rehearsal"):
            self.env["nudge.conversation"].with_user(self.rehearser).with_context(
                nudge_agent_id=agent.id, nudge_rehearsal=True)._chat_open(live.id)

    def test_an_agent_not_open_to_the_person_is_refused(self):
        agent = self.made(user_ids=[(6, 0, [self.user_a.id])])
        with self.assertRaisesRegex(UserError, "not open to you"):
            self.env["nudge.conversation"].with_user(self.user_b).with_context(nudge_agent_id=agent.id)._chat_open()
        self.assertEqual(agent.with_user(self.user_b)._answer("Hello")["source"], "refused")
        # Taken out of the audience after the conversation began: the next question is refused
        self.use_adapter(reply="Fine.")
        conversation, _events = self.talk(self.user_a, agent, "Hello")
        agent.sudo().user_ids = [(5, 0, 0)]
        with self.assertRaisesRegex(UserError, "not open to you"):
            self.talk(self.user_a, agent, "Hello again", conversation_id=conversation.id)

    def test_a_draft_agent_is_only_rehearsed_by_a_manager_who_may_edit_it(self):
        draft = self.made(state="draft", user_ids=[(6, 0, [self.user_a.id])])
        self.use_adapter(reply="Fine.")
        with self.assertRaisesRegex(UserError, "not open to you"):
            self.talk(self.user_a, draft, "Hello")
        self.assertEqual(draft.with_user(self.rehearser)._answer("Hello")["source"], "refused", "Not for real")
        result = draft.with_user(self.rehearser)._answer("Hello", rehearsal=True)
        self.assertEqual((result["source"], result["text"]), ("ai", "Fine."))
        self.assertTrue(self.conversation_of(result).agent_rehearsal)
        # Asked by somebody who may not edit the agent, a rehearsal is ignored: the turn is a real one
        agent = self.made(user_ids=[(6, 0, [self.user_a.id])])
        result = agent.with_user(self.user_a)._answer("Hello", rehearsal=True)
        self.assertEqual(result["source"], "ai")
        self.assertFalse(self.conversation_of(result).agent_rehearsal)
        # Archived: nobody talks to it for real any more, its audience included
        agent.sudo().write({"state": "archived"})
        self.assertEqual(agent.with_user(self.user_a)._answer("Hello")["source"], "refused")

    def test_a_service_user_needs_a_published_service_channel(self):
        service = new_test_user(self.env, login="nudge_engine_service", groups="base.group_user,nudge.group_agent_service")
        agent = self.made(user_ids=[(6, 0, [service.id])])
        self.assertFalse(agent._open_to(service), "No service channel names this user")
        binding = self.env["nudge.agent.binding"].create({
            "agent_id": agent.id, "channel": "website", "acts_as": "service", "service_user_id": service.id,
            "published": True})
        self.assertTrue(agent._open_to(service))
        binding.published = False
        self.assertFalse(agent._open_to(service), "The channel was taken down")
        binding.published = True
        # Given a group of Nudge since: no longer only the service group, checked again at every turn
        group = self.env.ref("nudge.group_nudge_user")
        group.write({("user_ids" if "user_ids" in group._fields else "users"): [(4, service.id)]})
        self.env.invalidate_all()
        self.assertFalse(agent._open_to(service))

    def test_developer_mode_is_refused(self):
        agent = self.made(user_ids=[(6, 0, [self.manager.id])])
        conversation = self.env["nudge.conversation"].with_user(self.manager).with_context(
            nudge_agent_id=agent.id)._chat_open()
        with self.assertRaisesRegex(UserError, "Developer mode"):
            conversation._chat_prepare("Add a button that confirms the order", mode="dev")

    # ------------------------------------------------------------------ companies
    def test_a_record_of_another_company_is_not_found(self):
        Partner = self.env["res.partner"]
        Partner.create({"name": FAR, "company_id": self.other.id})
        Partner.create({"name": NEAR, "company_id": self.company.id})
        agent = self.made(user_ids=[(6, 0, [self.both.id])], tool_keys="search_records,read_record")
        look = asking(call("search_records", {
            "model": "res.partner", "fields": ["name"],
            "conditions": [{"field": "name", "operator": "contains", "value": "Nudge Agents"}]}))
        # Without the agent the person reads both companies
        self.use_adapter(script=[look, saying("Found them.")])
        conversation, _last = self.ask(self.both, "Find the partners of Nudge Agents")
        self.assertIn(FAR, self.tool_text(conversation))
        # With it, only the companies of the agent, though the tools run where no context names it
        self.use_adapter(script=[look, saying("Found them.")])
        conversation, _events = self.talk(self.both, agent, "Find the partners of Nudge Agents")
        found = self.tool_text(conversation)
        self.assertIn(NEAR, found)
        self.assertNotIn(FAR, found)

    # ------------------------------------------------------------------ tools
    def test_a_write_tool_outside_the_agent_is_neither_offered_nor_run(self):
        acme = self.env["res.partner"].create({"name": "Nudge Engine Acme"})
        agent = self.made(user_ids=[(6, 0, [self.agent.id])], tool_keys="search_records,read_record")
        rename = asking(call("prepare_write", {
            "model": "res.partner", "ids": [acme.id], "values": [{"field": "name", "value": "Renamed"}]}))
        # Plain Nudge offers the change in Agent mode
        adapter = self.use_adapter(script=[saying("Fine.")])
        self.order(self.agent, "Rename Nudge Engine Acme")
        self.assertIn("prepare_write", {tool["name"] for tool in adapter.calls[0]["tools"]})
        # The agent does not; called all the same, it is refused and nothing waits for a yes
        adapter = self.use_adapter(script=[rename, saying("I cannot.")])
        conversation, _events = self.talk(self.agent, agent, "Rename Nudge Engine Acme", mode="agent")
        offered = {tool["name"] for tool in adapter.calls[0]["tools"]}
        self.assertIn("search_records", offered)
        self.assertNotIn("prepare_write", offered)
        self.assertLessEqual(offered, {"search_records", "read_record"})
        self.assertIn("There is no tool named", self.tool_text(conversation))
        self.assertFalse(conversation.sudo().waiting_step_id)
        self.assertEqual(acme.name, "Nudge Engine Acme")

    def test_an_answer_read_by_others_reads_no_record(self):
        agent = self.made(
            user_ids=[(6, 0, [self.agent.id])], tool_keys="search_records,read_record,search_odoo_documentation")
        adapter = self.use_adapter(script=[
            asking(call("search_records", {"model": "res.partner", "fields": ["name"], "limit": 1})),
            saying("I can tell you about Odoo, not about the records."),
        ])
        result = agent.with_user(self.agent).with_context(nudge_agent_knowledge_only=True)._answer(
            "Who are our customers?")
        offered = {tool["name"] for tool in adapter.calls[0]["tools"]}
        self.assertNotIn("search_records", offered)
        self.assertLessEqual(offered, {"search_odoo_documentation", "use_skill"})
        self.assertIn(prompt.AGENT_KNOWLEDGE_ONLY, "\n".join(base.system_texts(adapter.calls[0]["system"])))
        self.assertEqual(result["tool_calls"], ["search_records"])
        self.assertIn("There is no tool named", self.tool_text(self.conversation_of(result)))
        self.assertEqual(result["text"], "I can tell you about Odoo, not about the records.")

    # ------------------------------------------------------------------ prompt, masking, provider
    def test_the_persona_comes_after_the_rules_of_nudge(self):
        agent = self.made(
            user_ids=[(6, 0, [self.user_a.id])], role="Answers questions about quotations",
            instructions="End every answer with the words BLUE HARBOUR.", greeting="Hello from Sales")
        adapter = self.use_adapter(reply="Fine.")
        self.talk(self.user_a, agent, "Hello")
        system = "\n".join(base.system_texts(adapter.calls[0]["system"]))
        self.assertIn("BLUE HARBOUR", system)
        self.assertIn("Answers questions about quotations", system)
        self.assertIn(prompt.AGENT_GUARD, system)
        start = prompt.SYSTEM_PERSONA.strip().splitlines()[0]
        self.assertIn(start, system, "Nudge's own rules are kept")
        self.assertLess(system.index(start), system.index("BLUE HARBOUR"), "The persona comes after them")

    def test_masking_is_forced_by_the_agent(self):
        self.env.company.nudge_redact_pii = False
        agent = self.made(user_ids=[(6, 0, [self.user_a.id])], mask_personal_data=True)
        adapter = self.use_adapter(reply="Fine.")
        self.talk(self.user_a, agent, "Write to raghad@nudge.example about the order")
        sent = json.dumps(adapter.calls[0]["turns"], ensure_ascii=False)
        self.assertNotIn("raghad@nudge.example", sent)
        self.assertRegex(sent, r"EMAIL_\d")

    def test_the_provider_of_the_agent_answers(self):
        own = self.env["nudge.provider"].create({
            "name": "Agent provider", "kind": "openai_compatible", "base_url": "https://agent.invalid/v1",
            "api_key": "sk-agent-secret", "model_name": "agent-model"})
        agent = self.made(user_ids=[(6, 0, [self.user_a.id])], provider_id=own.id)
        self.use_adapter(reply="Fine.")
        conversation, _events = self.talk(self.user_a, agent, "Hello")
        self.assertEqual(conversation.sudo().provider_id, own)

    # ------------------------------------------------------------------ budgets
    def test_over_the_budget_of_the_day_it_answers_without_ai(self):
        agent = self.made(user_ids=[(6, 0, [self.user_a.id])], budget_user_day=100)
        self.Usage._add("chat", {"input_tokens": 90, "output_tokens": 20}, 0.5, self.user_a, self.company,
                        agent_id=agent.id)
        adapter = self.use_adapter(reply="From the AI")
        result = agent.with_user(self.user_a)._answer("Tell me a story about the sea")
        self.assertEqual(adapter.calls, [], "No provider is called")
        self.assertEqual(result["source"], "budget")
        self.assertIn("without AI", result["text"])
        self.assertEqual(self.conversation_of(result).agent_id, agent)

    def test_over_the_budget_of_the_month_it_answers_without_ai(self):
        agent = self.made(user_ids=[(6, 0, [self.user_a.id])], budget_month=1.0)
        # Spent by somebody else: the budget of the month is the agent's, for everybody
        self.Usage._add("chat", {"input_tokens": 10, "output_tokens": 10}, 2.0, self.user_b, self.company,
                        agent_id=agent.id)
        adapter = self.use_adapter(reply="From the AI")
        _conversation, events = self.talk(self.user_a, agent, "Tell me a story about the sea")
        self.assertEqual(adapter.calls, [])
        said = "".join(event.get("text", "") for event in events if event["type"] == "delta")
        self.assertIn("without AI", said)

    def test_a_rehearsal_counts_in_the_day_not_in_the_month(self):
        tokens = {"input_tokens": 500, "output_tokens": 500}
        monthly = self.made(user_ids=[(6, 0, [self.user_a.id])], budget_month=1.0)
        self.Usage._add("chat", tokens, 5.0, self.user_a, self.company, agent_id=monthly.id, rehearsal=True)
        adapter = self.use_adapter(reply="From the AI")
        result = monthly.with_user(self.user_a)._answer("Tell me a story about the sea")
        self.assertEqual((result["source"], result["text"]), ("ai", "From the AI"), "Try it is not billed")
        self.assertEqual(len(adapter.calls), 1)
        daily = self.made(name="Daily", user_ids=[(6, 0, [self.user_a.id])], budget_user_day=100)
        self.Usage._add("chat", tokens, 5.0, self.user_a, self.company, agent_id=daily.id, rehearsal=True)
        self.assertEqual(daily.with_user(self.user_a)._answer("Tell me a story about the sea")["source"], "budget",
                         "The tokens of the day are the person's, rehearsed or not")

    # ------------------------------------------------------------------ _answer
    def test_answer_through_the_provider(self):
        agent = self.made(user_ids=[(6, 0, [self.user_a.id])])
        self.use_adapter(reply="Hello from the agent.")
        result = agent.with_user(self.user_a)._answer("What can you do?")
        self.assertEqual(result["source"], "ai")
        self.assertEqual(result["text"], "Hello from the agent.")
        self.assertFalse(result["error"])
        conversation = self.conversation_of(result)
        self.assertEqual(conversation.agent_id, agent)
        self.assertEqual(conversation.user_id, self.user_a)
        usage = self.Usage.sudo().search([("conversation_id", "=", conversation.id)])
        self.assertTrue(usage)
        self.assertEqual(usage.agent_id, agent, "Every line of the ledger carries the agent")
        self.assertFalse(any(usage.mapped("rehearsal")))
        # The same conversation goes on
        again = agent.with_user(self.user_a)._answer("And then?", conversation=conversation.with_user(self.user_a))
        self.assertEqual(again["conversation_id"], conversation.id)
        # A conversation of another agent is not taken over
        other = self.made(name="Other engine agent", user_ids=[(6, 0, [self.user_a.id])])
        refused = other.with_user(self.user_a)._answer("Hi", conversation=conversation.with_user(self.user_a))
        self.assertEqual(refused["source"], "refused")

    def test_answer_from_memory_calls_no_provider(self):
        self.env["ir.config_parameter"].sudo().set_param("nudge.answer_memory", "True")
        self.env["nudge.memory"].create({"question": HOW, "answer": STEPS, "state": "approved"})
        agent = self.made(user_ids=[(6, 0, [self.user_a.id])])
        adapter = self.use_adapter(reply="From the AI")
        result = agent.with_user(self.user_a)._answer(SAME)
        self.assertEqual(result["source"], "memory")
        self.assertIn(STEPS, result["text"])
        self.assertEqual(adapter.calls, [])
        # An agent told not to use the memory asks the provider
        forgetful = self.made(name="Forgetful", user_ids=[(6, 0, [self.user_a.id])], use_memory=False)
        result = forgetful.with_user(self.user_a)._answer(SAME)
        self.assertEqual(result["source"], "ai")
        self.assertEqual(len(adapter.calls), 1)

    # ------------------------------------------------------------------ rehearsal
    def test_a_rehearsal_writes_nothing(self):
        agent = self.made(state="draft", tool_keys="search_records,prepare_create,remember_lesson")
        adapter = self.use_adapter(script=[
            asking(
                call("remember_lesson", {"lesson": LESSON, "why": "The person said so."}, "call_1"),
                call("prepare_create", {"model": "res.partner", "values": [
                    {"field": "name", "value": REHEARSED}]}, "call_2")),
            saying("I would create the contact."),
        ])
        # The panel of Try it; the answers are recorded where no context says it is a rehearsal
        conversation, events = self.talk(
            self.rehearser, agent, f"Create a contact named {REHEARSED}", mode="agent", rehearsal=True)
        conversation = conversation.sudo()
        self.assertTrue(conversation.agent_rehearsal)
        self.assertEqual(len(adapter.calls), 2)
        self.assertEqual(events[-1]["type"], "done", "Nothing waits for a yes")
        self.assertFalse(conversation.waiting_step_id)
        self.assertFalse(self.env["res.partner"].search_count([("name", "=", REHEARSED)]), "Nothing created")
        self.assertFalse(self.env["nudge.note"].sudo().search_count([("body", "ilike", "ten thousand")]),
                         "What a tool wrote is undone")
        self.assertFalse(self.env["nudge.step"].sudo().search_count([
            ("conversation_id", "=", conversation.id), ("state", "in", ("pending", "done")),
            ("kind", "in", ("create", "write", "method"))]), "No change in the log, prepared or done")
        told = self.tool_text(conversation)
        self.assertIn("Rehearsal", told)
        self.assertIn(REHEARSED, told, "The model is told what the card would have said")
        usage = self.Usage.sudo().search([("conversation_id", "=", conversation.id)])
        self.assertTrue(usage)
        self.assertTrue(all(usage.mapped("rehearsal")), "Try it is never billed as real")
        # No yes is taken, whatever the request says
        with self.assertRaisesRegex(UserError, "rehearsal"):
            self.env["nudge.conversation"].with_user(self.rehearser).browse(conversation.id)._agent_decide(
                0, "confirm")
        # A lesson the question itself asks to remember is not written down either
        self.use_adapter(reply="Noted.")
        result = agent.with_user(self.rehearser)._answer(f"Remember: {LESSON}", rehearsal=True)
        self.assertTrue(self.conversation_of(result).agent_rehearsal)
        self.assertFalse(self.env["nudge.note"].sudo().search_count([("body", "ilike", "ten thousand")]))

    # ------------------------------------------------------------------ audit, dashboard, statistics
    def test_the_requests_carry_the_agent(self):
        agent = self.made(user_ids=[(6, 0, [self.agent.id])], tool_keys="search_records")
        self.use_adapter(script=[
            asking(call("search_records", {"model": "res.partner", "fields": ["name"], "limit": 1})),
            saying("Done."),
        ])
        conversation, _events = self.talk(self.agent, agent, "Show me one contact", mode="agent")
        run = conversation.sudo().run_ids
        self.assertTrue(run)
        self.assertEqual(run.agent_id, agent)
        self.assertEqual(run.step_ids.agent_id, agent)

    def test_dashboard_and_statistics_leave_the_rehearsals_out(self):
        agent = self.made(user_ids=[(6, 0, [self.user_a.id])])
        tokens = {"input_tokens": 100, "output_tokens": 50}
        self.Usage._add("chat", tokens, 1.0, self.user_a, self.company, agent_id=agent.id)
        self.Usage._add("noai", {}, 0.0, self.user_a, self.company, agent_id=agent.id)
        self.Usage._add("chat", tokens, 7.0, self.user_a, self.company, agent_id=agent.id, rehearsal=True)
        self.env.flush_all()
        rows = self.env["nudge.token.dashboard"].search([("agent_id", "=", agent.id)])
        self.assertAlmostEqual(sum(rows.mapped("cost")), 1.0)
        self.assertEqual(sum(rows.mapped("answers")), 2)
        agent.invalidate_recordset()
        self.assertEqual(agent.answers_count, 2)
        self.assertAlmostEqual(agent.no_ai_share, 50.0)
        self.assertAlmostEqual(agent.cost_month, 1.0)

    # ------------------------------------------------------------------ second security review
    def test_an_answer_read_by_others_is_stored_and_gives_only_the_memory_for_everyone(self):
        # 2.1: knowledge only is stored on the conversation; only the remembered answers kept for no group
        self.env["ir.config_parameter"].sudo().set_param("nudge.answer_memory", "True")
        memory = self.env["nudge.memory"].create({
            "question": HOW, "answer": STEPS, "state": "approved",
            "group_ids": [(6, 0, [self.env.ref("nudge.group_nudge_user").id])]})
        agent = self.made(user_ids=[(6, 0, [self.user_a.id])])
        adapter = self.use_adapter(reply="From the AI")
        asked = agent.with_user(self.user_a)
        self.assertEqual(asked._answer(SAME)["source"], "memory", "Read by the person alone")
        shared = asked.with_context(nudge_agent_knowledge_only=True)._answer(SAME)
        self.assertEqual((shared["source"], len(adapter.calls)), ("ai", 1), "Kept for a group: not where others read")
        conversation = self.conversation_of(shared)
        self.assertTrue(conversation.agent_knowledge_only)
        memory.group_ids = [(5, 0, 0)]
        again = asked.with_context(nudge_agent_knowledge_only=True)._answer(
            SAME, conversation=conversation.with_user(self.user_a))
        self.assertEqual((again["source"], again["conversation_id"]), ("memory", conversation.id))
        # Answers for the person alone never go on in it, nor the other way round
        private = asked._answer("And then?", conversation=conversation.with_user(self.user_a))
        self.assertNotEqual(private["conversation_id"], conversation.id)
        self.assertFalse(self.conversation_of(private).agent_knowledge_only)
        with self.assertRaisesRegex(UserError, "cannot go on here"):
            self.env["nudge.conversation"].with_user(self.user_a).with_context(
                nudge_agent_id=agent.id)._chat_open(conversation.id)
        with self.assertRaises(AccessError):
            conversation.write({"agent_knowledge_only": False})

    def test_knowledge_only_holds_for_answers_recorded_without_it(self):
        # 2.1: the controller records each answer where no context says so: the stored flag decides
        agent = self.made(user_ids=[(6, 0, [self.agent.id])], tool_keys="search_records,search_odoo_documentation")
        adapter = self.use_adapter(script=[
            asking(call("search_records", {"model": "res.partner", "fields": ["name"], "limit": 1})),
            saying("Only about Odoo."),
        ])
        conversation = self.env["nudge.conversation"].with_user(self.agent).with_context(
            nudge_agent_id=agent.id, nudge_agent_knowledge_only=True)._chat_open()
        job = conversation._chat_prepare("Who are our customers?")
        plain = self.env["nudge.conversation"].with_user(self.agent).browse(conversation.id)
        list(loop.run(job, plain._chat_record))
        self.assertTrue(conversation.sudo().agent_knowledge_only)
        self.assertEqual(len(adapter.calls), 2)
        for sent in adapter.calls:
            self.assertNotIn("search_records", {tool["name"] for tool in sent["tools"] or []})
        self.assertIn("There is no tool named", self.tool_text(conversation))

    def test_past_the_budget_a_yes_and_the_background_call_no_model(self):
        # 2.5: the budget is asked again after a card and before each piece in the background
        acme = self.env["res.partner"].create({"name": "Nudge Engine Budget Acme"})
        agent = self.made(user_ids=[(6, 0, [self.agent.id])], tool_keys="prepare_write", budget_user_day=100000)
        adapter = self.use_adapter(script=[asking(call("prepare_write", {
            "model": "res.partner", "ids": [acme.id], "values": [{"field": "name", "value": "Renamed"}]}))])
        conversation, _events = self.talk(self.agent, agent, "Rename Nudge Engine Budget Acme", mode="agent")
        step = conversation.sudo().waiting_step_id
        self.assertTrue(step)
        asked = len(adapter.calls)
        self.Usage._add("chat", {"input_tokens": 200000, "output_tokens": 0}, 0.0, self.agent, self.company,
                        agent_id=agent.id)
        mine = self.env["nudge.conversation"].with_user(self.agent).browse(conversation.id)
        job = mine._agent_decide(step.id, "confirm")
        self.assertEqual(job.get("agent_source"), "budget")
        self.assertIn("without AI", "".join(event.get("text", "") for event in job["first_events"]))
        self.assertEqual(len(adapter.calls), asked, "No provider is called")
        self.assertEqual(acme.name, "Nudge Engine Budget Acme", "Nothing done that the model would follow")
        self.assertEqual(step.state, "cancelled")
        with self.assertRaisesRegex(UserError, "without AI"):
            mine._background_job()

    def test_plan_pieces_run_in_the_scope_of_the_agent(self):
        # 2.6: a plan worked on in the background, or corrected before the yes, has the agent's companies and tools
        agent = self.made(user_ids=[(6, 0, [self.both.id])], tool_keys="search_records")
        self.use_adapter(reply="Fine.")
        conversation, _events = self.talk(self.both, agent, "Hello")
        seen = []

        def look(record, *args):
            env = (args[1] if record._name == "nudge.run" else record).env
            seen.append((env.context.get("allowed_company_ids"), env.context.get("nudge_agent_tools")))
            return True

        self.patch(nudge_run.NudgeRun, "_work_plan", look)
        self.patch(nudge_agent_plan.NudgeConversation, "_plan_edit", look)
        self.patch(nudge_agent_plan.NudgeConversation, "_plan_remove", look)
        plain = self.env["nudge.conversation"].with_user(self.both).with_context(
            allowed_company_ids=[self.company.id, self.other.id]).browse(conversation.id)
        Step = self.env["nudge.step"]
        self.env["nudge.run"].sudo()._work_plan(Step, plain)
        plain._plan_edit(Step, 1, "name", "Renamed")
        plain._plan_remove(Step, 1)
        self.assertEqual(seen, [([self.company.id], ("search_records",))] * 3)

    def test_a_rehearsal_keeps_what_the_ai_calls_of_a_tool_cost(self):
        # 2.7: what a tool wrote is undone, what its AI calls cost is kept, and counts in the person's day
        agent = self.made(state="draft", budget_user_day=40)

        def spend(tool, env, arguments, screen):
            env["res.partner"].create({"name": REHEARSED})
            env["nudge.usage"]._add("chat", {"input_tokens": 40, "output_tokens": 2}, 0.25, env.user, env.company,
                                    agent_id=agent.id)
            return tools_base.ToolResult("Spent.")

        self.patch(documentation.SearchOdooDocumentation, "run", spend)
        Usage = self.Usage.sudo()
        result = tools_base.rehearse(self.env(user=self.rehearser), "search_odoo_documentation", {"query": "quotation"})
        self.assertFalse(result.is_error, result.content)
        self.assertFalse(self.env["res.partner"].search_count([("name", "=", REHEARSED)]), "Undone")
        kept = Usage.search([("agent_id", "=", agent.id)])
        self.assertEqual(len(kept), 1, "What its AI calls cost is kept")
        self.assertEqual((kept.tokens, kept.user_id, kept.rehearsal), (42, self.rehearser, True))
        self.assertEqual(agent._over_budget(self.rehearser, rehearsal=True), "day")

    def test_only_the_conversation_the_panel_opens_takes_the_agent(self):
        # 2.8: a conversation made by anything else in an agent's request is plain Nudge
        agent = self.made(user_ids=[(6, 0, [self.user_a.id])])
        Conversation = self.env["nudge.conversation"].with_user(self.user_a).with_context(
            nudge_agent_id=agent.id, nudge_rehearsal=True)
        self.assertFalse(Conversation.create({}).sudo().agent_id)
        opened = Conversation._chat_open()
        self.assertEqual(opened.sudo().agent_id, agent)
        self.assertFalse(opened.env["nudge.conversation"].create({}).sudo().agent_id, "Nor what is made after it")
        jobs = self.env["nudge.run"].with_context(nudge_agent_id=agent.id, nudge_rehearsal=True)._job_conversation(
            self.user_a)
        self.assertFalse(jobs.sudo().agent_id)
        self.assertFalse(jobs.sudo().agent_rehearsal)

    def test_a_tool_reached_outside_the_scope_of_an_agent_turn_is_refused(self):
        # 2.12: fails closed: a conversation with an agent and no list of its tools
        agent = self.made(user_ids=[(6, 0, [self.user_a.id])])
        self.use_adapter(reply="Fine.")
        conversation, _events = self.talk(self.user_a, agent, "Hello")
        arguments = {"query": "quotation"}
        unscoped = self.env(user=self.user_a, context={"nudge_conversation_id": conversation.id})
        refused = tools_base.call(unscoped, "search_odoo_documentation", arguments)
        self.assertTrue(refused.is_error)
        self.assertIn("There is no tool named", refused.content)
        scoped = self.env["nudge.conversation"].with_user(self.user_a).browse(conversation.id)._agent_scope("talk")
        allowed = tools_base.call(scoped.with_context(nudge_conversation_id=conversation.id).env,
                                  "search_odoo_documentation", arguments)
        self.assertNotIn("There is no tool named", allowed.content)
        # Plain Nudge is not touched
        plain, _last = self.ask(self.user_a, "Hello")
        alone = tools_base.call(self.env(user=self.user_a, context={"nudge_conversation_id": plain.id}),
                                "search_odoo_documentation", arguments)
        self.assertNotIn("There is no tool named", alone.content)

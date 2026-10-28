from unittest.mock import patch

import contextlib

from odoo import fields, http
from odoo.exceptions import UserError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

from ..controllers import main
from ..tools import agent_door, assistant
from .common import WebsiteCase

PUBLIC_ANSWER = {"text": "Our showroom opens at nine.", "source": "ai", "tool_calls": [], "error": False,
                 "conversation_id": False}


class AgentCase(WebsiteCase):
    """A website with agents bound to it; the engine's two entrances are mocked, never the AI itself."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.service = new_test_user(
            cls.env, login="web_service_raghad", groups="base.group_portal,nudge.group_agent_service")
        cls.agent = cls.make_agent("Raghad Front Desk")
        # The class of the registry: the engine's methods (_answer, _answer_public, _front_desk) live on an
        # _inherit of nudge.agent, so a patch of the first Python class would never reach them
        cls.Agent = type(cls.env["nudge.agent"])

    @classmethod
    def make_agent(cls, name, **values):
        return cls.env["nudge.agent"].create(dict({
            "name": name, "state": "published", "company_ids": [(6, 0, cls.website.company_id.ids)],
            "budget_month": 100.0, "budget_user_day": 100000}, **values))

    @classmethod
    def bind(cls, agent, **values):
        return cls.env["nudge.agent.binding"].create(dict({
            "agent_id": agent.id, "channel": "website", "acts_as": "service",
            "service_user_id": cls.service.id, "published": True}, **values))


class TestAgentDoor(AgentCase, TransactionCase):

    def serve(self, user, logged_in, text="opening hours", **kwargs):
        return agent_door.serve(self.env, self.website, user, text, logged_in, "visitor-hash", **kwargs)

    def test_no_published_binding_is_the_old_path(self):
        self.assertIsNone(self.serve(self.public_user, False))
        self.bind(self.agent, published=False)
        self.assertIsNone(self.serve(self.public_user, False))
        # Another channel is not the website
        self.bind(self.agent, channel="discuss", acts_as="person", service_user_id=False)
        self.assertIsNone(self.serve(self.public_user, False))

    def test_a_public_visitor_goes_through_answer_public_only(self):
        binding = self.bind(self.agent)
        calls = []

        def fake(agent, text, bnd, lang=None, conversation=None, visitor_key=None, consent=False, company=None):
            calls.append((agent.id, bnd.id, visitor_key))
            return dict(PUBLIC_ANSWER)

        with patch.object(self.Agent, "_answer_public", fake, create=True), \
                patch.object(self.Agent, "_answer", side_effect=AssertionError("a visitor is never a person")):
            result = self.serve(self.public_user, False)
        self.assertEqual(calls, [(self.agent.id, binding.id, "visitor-hash")])
        self.assertEqual((result["text"], result["answered"], result["used_ai"]), (PUBLIC_ANSWER["text"], True, True))
        self.assertEqual(result["binding_id"], binding.id)

    def test_a_person_binding_does_not_serve_a_public_visitor(self):
        self.bind(self.agent, acts_as="person", service_user_id=False)
        self.assertIsNone(self.serve(self.public_user, False))

    def test_off_hours_text_is_passed_as_given(self):
        self.bind(self.agent)
        text = "We are closed. Back at 08:00."
        with patch.object(self.Agent, "_answer_public", lambda *a, **k: {"text": text, "source": "noai", "error": False},
                          create=True):
            result = self.serve(self.public_user, False)
        self.assertEqual((result["text"], result["used_ai"]), (text, False))

    def test_the_front_desk_chooses_among_several(self):
        first, second = self.bind(self.agent), self.bind(self.make_agent("Ebrahim Sales"))
        seen = []

        def fake(agent, text, bnd, **kwargs):
            seen.append(bnd.id)
            return dict(PUBLIC_ANSWER)

        with patch.object(self.Agent, "_front_desk", lambda self_, text, bindings: second, create=True), \
                patch.object(self.Agent, "_answer_public", fake, create=True):
            self.serve(self.public_user, False)
        self.assertEqual(seen, [second.id])
        self.assertNotEqual(first, second)

    def test_a_portal_customer_is_answered_like_a_visitor(self):
        binding = self.bind(self.agent)
        calls = []

        def fake(agent, text, bnd, lang=None, conversation=None, visitor_key=None, consent=False, company=None):
            calls.append((bnd.id, visitor_key, consent, company))
            return dict(PUBLIC_ANSWER)

        # A portal user holds no Nudge right: the engine would refuse them in _answer; the service user answers,
        # within the models of the channel, in the company of the website (L4), with the consent the visitor gave
        # to this agent (L6)
        with patch.object(self.Agent, "_answer_public", fake, create=True), \
                patch.object(self.Agent, "_answer", side_effect=AssertionError("a customer is not served as a user")):
            result = self.serve(self.portal_user, True, memory={}, consent=[binding.id])
            self.serve(self.portal_user, True, memory={}, consent=[binding.id + 1000])
        self.assertEqual(calls, [(binding.id, "visitor-hash", True, self.website.company_id),
                                 (binding.id, "visitor-hash", False, self.website.company_id)])
        self.assertTrue(result["answered"])

    def test_the_budget_and_the_places_of_the_website_hold_for_the_agents(self):
        # H1: the company's daily budget for visitors counts the agents; past it (or none), the plain assistant
        self.bind(self.agent)
        company = self.website.company_id
        with patch.object(self.Agent, "_answer_public", lambda *a, **k: dict(PUBLIC_ANSWER), create=True):
            company.sudo().nudge_public_budget = 0
            self.assertIsNone(self.serve(self.public_user, False))
            company.sudo().nudge_public_budget = 100
            self.env["nudge.usage"].sudo().create({
                "kind": "chat", "user_id": self.service.id, "company_id": company.id,
                "date": fields.Date.context_today(self.env["res.users"]), "input_tokens": 150, "origin": "website"})
            self.assertIsNone(self.serve(self.public_user, False))
            company.sudo().nudge_public_budget = 100000
            self.assertEqual(self.serve(self.public_user, False)["text"], PUBLIC_ANSWER["text"])

        @contextlib.contextmanager
        def taken(env):
            raise assistant.Busy()
            yield  # noqa: unreachable - a generator

        with patch.object(assistant, "ai_slot", taken), \
                patch.object(self.Agent, "_answer_public", side_effect=AssertionError("no place"), create=True):
            busy = self.serve(self.public_user, False)
        self.assertTrue(busy["busy"])
        self.assertFalse(busy["answered"])

    def test_a_visitor_never_reads_an_error_of_the_engine(self):
        # M4
        self.bind(self.agent)
        with patch.object(self.Agent, "_answer_public", side_effect=UserError("nudge.secret.model(42) is not yours"),
                          create=True):
            result = self.serve(self.public_user, False)
        self.assertNotIn("secret", result["text"])
        self.assertFalse(result["answered"])

    def test_an_employee_is_served_as_themselves_and_refused_when_not_allowed(self):
        service_binding = self.bind(self.agent)
        user = new_test_user(self.env, login="web_employee_raghad", groups="base.group_user,nudge.group_nudge_user")
        seen = []

        def fake(agent, text, **kwargs):
            seen.append((agent.env.uid, kwargs.get("binding")))
            return {"text": "Your order is on its way.", "source": "ai", "error": False, "conversation_id": False}

        memory = {}
        # Not in the audience of the agent: refused, the old assistant does not answer in its place
        with patch.object(self.Agent, "_can_use", lambda self_, u, ignore_state=False: False), \
                patch.object(self.Agent, "_answer", fake):
            refused = self.serve(user, True, refusal="Not open to you.", memory=memory)
        self.assertEqual((refused["text"], refused["answered"]), ("Not open to you.", False))
        self.assertEqual(seen, [])
        # Allowed: served as that user, never through the public entrance; a service binding answers as its
        # service user only, so the employee is answered without it
        with patch.object(self.Agent, "_can_use", lambda self_, u, ignore_state=False: True), \
                patch.object(self.Agent, "_answer", fake), \
                patch.object(self.Agent, "_answer_public", side_effect=AssertionError("not public"), create=True):
            result = self.serve(user, True, memory=memory)
            self.assertEqual(seen, [(user.id, None)])
            self.assertTrue(result["answered"])
            self.assertEqual(result["binding_id"], service_binding.id)
            # A binding for people of the agent: the employee's turn goes through it (its office hours, hand-over)
            own = self.bind(self.agent, acts_as="person", service_user_id=False)
            self.serve(user, True, memory=memory)
        self.assertEqual(seen[-1], (user.id, own))

    def test_hand_over_needs_an_escalation_user_and_passes_consent(self):
        binding = self.bind(self.agent)
        given = []
        with patch.object(self.Agent, "_hand_to_person", lambda self_, bnd, summary, **kw: given.append(kw), create=True):
            self.assertFalse(agent_door.hand_over(self.env, binding.id, self.website, "x", "n", "e@x.co", True, False))
            # An employee (M3: never a portal user, as the service user now is)
            binding.escalation_user_id = self.env.ref("base.user_admin")
            self.assertTrue(agent_door.hand_over(self.env, binding.id, self.website, "x", "n", "e@x.co", False, False))
        self.assertEqual(len(given), 1)
        self.assertIs(given[0]["consent"], False)


@tagged("post_install", "-at_install")
class TestAgentRoute(AgentCase, HttpCase):

    def test_the_bubble_answers_through_the_agent_and_keeps_the_limits(self):
        self.env["ir.config_parameter"].sudo().set_param("nudge_website.rate_messages", "2")
        self.bind(self.agent)
        self.authenticate(None, None)
        with patch.object(self.Agent, "_answer_public", lambda *a, **k: dict(PUBLIC_ANSWER), create=True):
            first = self.make_jsonrpc_request("/nudge_website/ask", {"message": "opening hours"})
            self.make_jsonrpc_request("/nudge_website/ask", {"message": "opening hours again"})
            limited = self.make_jsonrpc_request("/nudge_website/ask", {"message": "and a third time"})
        self.assertEqual(first["text"], PUBLIC_ANSWER["text"])
        self.assertEqual(limited["error"], "limited")

    def test_consent_is_kept_for_the_agent_past_the_robot_checks_and_the_lead_is_the_forms(self):
        # M1, L6
        self.need("crm.lead")
        binding = self.bind(self.agent, escalation_user_id=self.env.ref("base.user_admin").id)
        self.env["ir.config_parameter"].sudo().set_param("nudge_website.handover_max", "10")
        now = [1_000_000.0]
        self.patch(main, "_clock", lambda: now[0])
        self.authenticate(None, None)
        with patch.object(self.Agent, "_answer_public", lambda *a, **k: dict(PUBLIC_ANSWER), create=True):
            self.make_jsonrpc_request("/nudge_website/ask", {"message": "opening hours"})
        details = {"name": "Raghad Visitor", "email": "raghad.agent@visitor.example", "message": "Call me"}
        self.make_jsonrpc_request("/nudge_website/handover", {})
        now[0] += 10
        # A robot fills the hidden field: thanked, and nothing kept
        self.make_jsonrpc_request("/nudge_website/handover", dict(details, consent=True, **{main.HONEYPOT: "x"}))
        self.assertFalse(http.root.session_store.get(self.session.sid).get(agent_door.CONSENT_KEY))
        activities = self.env["mail.activity"].sudo().search_count([])
        self.make_jsonrpc_request("/nudge_website/handover", dict(details, consent=True))
        self.assertEqual(http.root.session_store.get(self.session.sid).get(agent_door.CONSENT_KEY), [binding.id])
        leads = self.env["crm.lead"].sudo().search([("email_from", "=", "raghad.agent@visitor.example")])
        self.assertEqual(len(leads), 1, "One lead: the form's")
        self.assertEqual(self.env["mail.activity"].sudo().search_count([]), activities + 1)
        self.assertTrue(self.env["mail.activity"].sudo().search_count(
            [("res_model", "=", "crm.lead"), ("res_id", "=", leads.id)]))

    def test_without_a_binding_the_old_assistant_answers(self):
        self.authenticate(None, None)
        with patch.object(self.Agent, "_answer_public", side_effect=AssertionError("no agent"), create=True):
            result = self.make_jsonrpc_request("/nudge_website/ask", {"message": "shipping to Dubai"})
        self.assertIn("three days", result["text"])

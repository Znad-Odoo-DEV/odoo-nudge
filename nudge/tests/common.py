import unittest

from odoo.tests import new_test_user

import requests

from odoo.addons.nudge.llm import loop, ollama
from odoo.addons.nudge.llm.fake_adapter import FakeAdapter


def _no_server(*args, **kwargs):
    raise requests.exceptions.ConnectionError("no server in tests")


def require_modules(cls, *names):
    """Skip the whole class when an app it builds its data on is not installed in this database."""
    found = cls.env["ir.module.module"].sudo().search([("name", "in", names), ("state", "=", "installed")])
    missing = sorted(set(names) - set(found.mapped("name")))
    if missing:
        raise unittest.SkipTest("needs " + ", ".join(missing))


class NudgeCase:
    """Mixin giving users, one ready provider and a scripted adapter.

    Automated tests never reach a real provider: the adapter of the provider
    model is replaced by :class:`FakeAdapter`.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The AI path is what these tests ask for: common requests understood by rules first is on
        # by default since 57.31, and the tests that want it switch it on themselves
        cls.env["ir.config_parameter"].sudo().set_param("nudge.noai_first", "0")
        cls.user_a = new_test_user(cls.env, login="nudge_a", groups="nudge.group_nudge_user")
        cls.user_b = new_test_user(cls.env, login="nudge_b", groups="nudge.group_nudge_user")
        cls.manager = new_test_user(cls.env, login="nudge_m", groups="nudge.group_nudge_manager")
        cls.outsider = new_test_user(cls.env, login="nudge_out", groups="base.group_user")
        # Reads and edits contacts: a screen with a record to look at
        cls.user_contacts = new_test_user(
            cls.env, login="nudge_c", groups="nudge.group_nudge_user,base.group_partner_manager")
        # May ask Nudge to do the work, on contacts
        cls.agent = new_test_user(
            cls.env, login="nudge_g", groups="nudge.group_nudge_agent,base.group_partner_manager")

        Provider = cls.env["nudge.provider"]
        Provider.search([]).active = False
        cls.provider = Provider.create({
            "name": "Test provider",
            "kind": "openai_compatible",
            "base_url": "https://llm.invalid/v1",
            "api_key": "sk-test-secret",
            "model_name": "test-model",
            "price_input": 2.0,
            "price_output": 10.0,
        })

    def use_adapter(self, **kwargs):
        """:return: the adapter every provider answers with from now on"""
        adapter = FakeAdapter(**kwargs)
        self.patch(type(self.env["nudge.provider"]), "_get_adapter", lambda provider: adapter)
        # No Ollama answers behind a fake provider, unless a test plays one
        self.patch(ollama.requests, "request", _no_server)
        return adapter

    def ask(self, user, text, conversation_id=None, snapshot=None):
        """Run one question the way the controller does, without HTTP.
        :return: (conversation, last event)"""
        Conversation = self.env["nudge.conversation"].with_user(user)
        conversation = Conversation._chat_open(conversation_id)
        job = conversation._chat_prepare(text, snapshot=snapshot)
        events = list(loop.run(job, conversation._chat_record))
        return conversation, events[-1]

    def order(self, user, text, conversation_id=None, snapshot=None):
        """Ask Nudge to do the work, the way the controller does.
        :return: (conversation, events)"""
        Conversation = self.env["nudge.conversation"].with_user(user)
        conversation = Conversation._chat_open(conversation_id)
        job = conversation._chat_prepare(text, snapshot=snapshot, mode="agent")
        return conversation, list(loop.run(job, conversation._chat_record))

    def decide(self, conversation, decision="confirm", step=None):
        """Say yes or no to what waits, the way the controller does.
        :return: events"""
        step = step or conversation.sudo().waiting_step_id
        job = conversation._agent_decide(step.id, decision)
        return list(loop.run(job, conversation._chat_record))

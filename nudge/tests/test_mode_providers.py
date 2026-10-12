from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge.models.nudge_provider import (
    AGENT_PROVIDER_PARAM,
    DEFAULT_PROVIDER_PARAM,
    DEVELOPER_PROVIDER_PARAM,
)

from .test_agent import AgentCase


@tagged("post_install", "-at_install")
class TestModeProviders(AgentCase, TransactionCase):
    """A free model for the questions, a stronger one for the work."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.work = cls.env["nudge.provider"].create({
            "name": "Work provider",
            "kind": "openai_compatible",
            "base_url": "https://work.invalid/v1",
            "api_key": "sk-work-secret",
            "model_name": "work-model",
        })
        cls.Parameter = cls.env["ir.config_parameter"].sudo()
        cls.Parameter.set_param(DEFAULT_PROVIDER_PARAM, str(cls.provider.id))

    def test_without_a_provider_for_the_work_the_default_answers_everything(self):
        conversation = self.env["nudge.conversation"].with_user(self.agent).create({})
        for mode in ("talk", "agent", "dev"):
            self.assertEqual(conversation._chat_provider(mode), self.provider, mode)

    def test_the_work_has_its_own(self):
        self.Parameter.set_param(AGENT_PROVIDER_PARAM, str(self.work.id))
        self.use_adapter()
        conversation, _event = self.ask(self.agent, "What is a quotation?")
        self.assertEqual(conversation.sudo().provider_id, self.provider)
        self.order(self.agent, "List the contacts", conversation_id=conversation.id)
        self.assertEqual(conversation.sudo().provider_id, self.work)
        # Back to a question: back to the default one
        self.ask(self.agent, "And an invoice?", conversation_id=conversation.id)
        self.assertEqual(conversation.sudo().provider_id, self.provider)
        # Developer mode follows the agent, unless it has its own
        self.assertEqual(conversation._chat_provider("dev"), self.work)
        developer = self.work.copy({"name": "Builder", "api_key": "sk-builder"})
        self.Parameter.set_param(DEVELOPER_PROVIDER_PARAM, str(developer.id))
        self.assertEqual(conversation._chat_provider("dev"), developer)
        self.assertEqual(conversation._chat_provider("agent"), self.work)

    def test_a_provider_that_is_not_ready_is_passed_over(self):
        self.Parameter.set_param(AGENT_PROVIDER_PARAM, str(self.work.id))
        self.work.active = False
        conversation = self.env["nudge.conversation"].with_user(self.agent).create({})
        self.assertEqual(conversation._chat_provider("agent"), self.provider)

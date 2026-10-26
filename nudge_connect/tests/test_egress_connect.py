"""NP-15: a connector off the list of allowed destinations is refused before any request."""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge.tests.common import NudgeCase
from odoo.addons.nudge.tools.records import Refused

from odoo.addons.nudge.tools import pinned_http

from .. import mcp_client


@tagged("post_install", "-at_install")
class TestConnectorEgress(NudgeCase, TransactionCase):

    def setUp(self):
        super().setUp()
        self.connector = self.env["nudge.connector"].create({
            "name": "Sister Odoo", "key": "sister", "url": "https://sister.example.test/mcp"})

    def test_a_connector_off_the_list_is_refused_before_a_request(self):
        self.env.company.nudge_egress_hosts = "allowed.test"
        with patch.object(pinned_http, "post") as posted:
            result = self.connector.action_test()
        posted.assert_not_called()
        self.assertIn("allowed destinations", result["params"]["message"])
        with self.assertRaises(mcp_client.McpError) as error:
            self.connector._client()
        self.assertEqual(error.exception.code, "egress")
        self.assertIn("sister.example.test", error.exception.detail)

    def test_the_token_address_is_checked_too(self):
        self.env.company.nudge_egress_hosts = "sister.example.test"
        self.connector.write({"auth_type": "oauth", "oauth_token_url": "https://auth.example.test/token",
                              "oauth_client_id": "a", "oauth_client_secret": "b"})
        with self.assertRaises(mcp_client.McpError) as error:
            self.connector._client()
        self.assertIn("auth.example.test", error.exception.detail)

    def test_on_the_list_or_with_no_list_the_client_is_built(self):
        for hosts in (False, "sister.example.test", "*.example.test"):
            self.env.company.nudge_egress_hosts = hosts
            self.assertTrue(self.connector._client())

    def test_the_usable_check_says_so_in_words(self):
        self.env.company.nudge_egress_hosts = "allowed.test"
        with patch.object(type(self.connector), "_is_for", lambda self, user: True):
            self.connector.write({"state": "active"})
            with self.assertRaises(Refused) as error:
                self.connector.with_user(self.agent)._check_usable()
        self.assertIn("allowed destinations", str(error.exception))
        self.assertIn("Nothing was sent", str(error.exception))

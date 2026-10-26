"""S11: a connector whose name answers a public address to the check and 127.0.0.1 afterwards reaches
nothing inside: the MCP call, the sign-in discovery and the token request connect to the checked address."""
from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge.tests.common import NudgeCase
from odoo.addons.nudge.tests.rebind import NAME, PUBLIC, Listener, RebindCase, Rebinding

from .. import mcp_client


@tagged("post_install", "-at_install")
class TestConnectorRebinding(RebindCase, NudgeCase, TransactionCase):

    def assertNothingReached(self, call):
        with Listener() as listener:
            url = f"https://{NAME}:{listener.port}"
            with Rebinding(first=PUBLIC, then="127.0.0.1"):
                with self.assertRaises(mcp_client.McpError) as error:
                    call(url)
        self.assertEqual(error.exception.code, "private")
        self.assertEqual(listener.connections, 0, "Nothing reached the address inside")

    def connector(self, url):
        return self.env["nudge.connector"].create({
            "name": "Sister Odoo", "key": "sister", "url": f"{url}/mcp", "auth_type": "oauth",
            "oauth_token_url": f"{url}/token", "oauth_client_id": "raghad", "oauth_client_secret": "ebrahim"})

    def test_the_mcp_call(self):
        self.assertNothingReached(lambda url: mcp_client.Client(f"{url}/mcp", seconds=5).discover())

    def test_the_sign_in_discovery(self):
        def discover(url):
            connector = self.connector(url)
            connector._oauth_http(f"{url}/.well-known/oauth-protected-resource")
        self.assertNothingReached(discover)

    def test_the_token_request(self):
        self.assertNothingReached(lambda url: self.connector(url)._oauth_token())

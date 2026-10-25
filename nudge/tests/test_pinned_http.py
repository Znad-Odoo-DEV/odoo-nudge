"""S11: an outbound request connects to the address that was checked, never to a second answer of the DNS."""
import shutil
from unittest.mock import patch

import requests

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge.models import nudge_recipe_market as market
from odoo.addons.nudge.tools import pinned_http

from .rebind import NAME, OTHER, PUBLIC, Listener, RebindCase, Rebinding, Server, tls_context


@tagged("post_install", "-at_install")
class TestPinnedHttp(RebindCase, TransactionCase):

    def test_the_socket_goes_to_the_address_that_was_judged(self):
        opened = []

        def connect(address, *args, **kwargs):
            opened.append(address)
            raise OSError("not in a test")

        with Rebinding(first=PUBLIC, then="127.0.0.1") as dns, patch.object(pinned_http, "create_connection", connect):
            with self.assertRaises(requests.ConnectionError):
                pinned_http.get(f"https://{NAME}/index.json", timeout=5)
        self.assertEqual(opened, [(PUBLIC, 443)])
        self.assertEqual(dns.lookups, 1, "The name is not asked again for the connection")

    def test_a_name_that_points_inside_is_refused_before_any_connection(self):
        for inside in ("127.0.0.1", "169.254.169.254", "10.0.0.5", "100.64.0.1", "::1", "0.0.0.0"):
            with Listener() as listener, Rebinding(first=inside) as dns:
                with self.assertRaises(pinned_http.Refused) as refused:
                    pinned_http.get(f"https://{NAME}:{listener.port}/", timeout=5)
            self.assertEqual(refused.exception.code, "private", inside)
            self.assertEqual((dns.lookups, listener.connections), (1, 0), inside)
        with self.assertRaises(pinned_http.Refused) as refused:
            pinned_http.get("ftp://files.example.test/x")
        self.assertEqual(refused.exception.code, "scheme")

    def test_over_http_the_host_header_keeps_the_name(self):
        # Allowed inside (a server of the company): the first answer is used, the second never asked
        with Server() as server, Rebinding(first="127.0.0.1", then="10.255.255.1") as dns:
            response = pinned_http.get(f"http://{NAME}:{server.port}/x", allow_private=True, timeout=5)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(server.hosts, [f"{NAME}:{server.port}"])
        self.assertEqual(dns.lookups, 1)

    def test_a_redirect_is_never_followed(self):
        with Server() as server, Rebinding(first="127.0.0.1") as dns:
            response = pinned_http.get(f"http://{NAME}:{server.port}/away", allow_private=True,
                                       allow_redirects=True, timeout=5)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(server.hosts), 1)
        self.assertEqual(dns.lookups, 1)

    def test_tls_is_verified_for_the_name_not_for_the_address(self):
        try:
            context, certfile, directory = tls_context(NAME)
        except ImportError:
            self.skipTest("cryptography is not installed")
        self.addCleanup(shutil.rmtree, directory, True)
        with Server(context) as server, Rebinding(first="127.0.0.1", then="10.255.255.1"):
            response = pinned_http.get(f"https://{NAME}:{server.port}/", allow_private=True, verify=certfile,
                                       timeout=5)
        self.assertEqual((response.status_code, response.text), (200, f"{NAME}:{server.port}"))
        # The same server under another name: its certificate does not match, the request fails
        with Server(context) as server, Rebinding(OTHER, first="127.0.0.1"):
            with self.assertRaises(requests.exceptions.SSLError):
                pinned_http.get(f"https://{OTHER}:{server.port}/", allow_private=True,
                                verify=certfile, timeout=5)

    def test_recipes_and_the_compliance_feed_do_not_follow_a_rebound_name(self):
        with Listener() as listener, Rebinding(first=PUBLIC, then="127.0.0.1"):
            with self.assertRaises(market.FetchError) as refused:
                market._download(f"https://{NAME}:{listener.port}/index.json")
        self.assertEqual(refused.exception.code, "private")
        self.assertEqual(listener.connections, 0, "Nothing reached the address inside")

        Parameter = self.env["ir.config_parameter"].sudo()
        Parameter.set_param("nudge.compliance_feed_key", "AAAA")
        with Listener() as listener, Rebinding(first=PUBLIC, then="127.0.0.1"):
            Parameter.set_param("nudge.compliance_feed_url", f"https://{NAME}:{listener.port}/feed.json")
            with self.assertRaises(UserError) as refused:
                self.env["nudge.compliance.update"]._fetch()
        self.assertIn("(private)", str(refused.exception))
        self.assertEqual(listener.connections, 0)

"""S11: the link check and the download of a drawn picture connect to the address that was checked."""
from unittest.mock import patch

import requests

from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge.tests.rebind import NAME, PUBLIC, Listener, RebindCase, Rebinding

from ..tools import checks, pictures


class Drawn:
    """The answer of an images endpoint that gives an address instead of the bytes"""

    def __init__(self, url):
        self.url = url

    def raise_for_status(self):
        return None

    def json(self):
        return {"data": [{"url": self.url}]}


@tagged("post_install", "-at_install")
class TestBuilderRebinding(RebindCase, TransactionCase):

    def test_a_link_whose_name_rebinds_inside_is_not_visited(self):
        with Listener() as listener, Rebinding(first=PUBLIC, then="127.0.0.1"):
            self.assertEqual(checks.head_status(f"https://{NAME}:{listener.port}/page"), 0)
        self.assertEqual(listener.connections, 0, "Nothing reached the address inside")

    def test_a_picture_is_fetched_from_inside_only_for_a_provider_of_our_own(self):
        provider = self.env["nudge.provider"].create({
            "name": "Pictures of Raghad", "kind": "openai_compatible", "base_url": "https://images.example.test/v1",
            "model_name": "image-1"})
        for local, reached in ((False, 0), (True, 1)):
            provider.runs_locally = local
            with Listener() as listener, Rebinding(first="127.0.0.1"):
                url = f"https://{NAME}:{listener.port}/picture.png"
                with patch.object(requests, "post", lambda *args, **kwargs: Drawn(url)):
                    self.assertIsNone(pictures.draw_picture(provider, "image-1", "an oak chair", timeout=5))
            self.assertEqual(listener.connections, reached, f"runs locally: {local}")

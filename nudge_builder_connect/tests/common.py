"""A fake web for the tests: ``pinned_http.request`` is replaced, so nothing leaves the machine."""
import io
import json
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from odoo.addons.nudge.tools import pinned_http

TOKEN = "ya29.raghad-access-token"
REFRESH = "1//raghad-refresh-token"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
BUSINESS_SCOPE = "https://www.googleapis.com/auth/business.manage"
FOLDER = "1AbCdEfGhIjKlMnOpQrStUv"


class FakeResponse:
    def __init__(self, status=200, body=b""):
        self.status_code = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, size):
        for start in range(0, len(self._body), size):
            yield self._body[start:start + size]


class FakeWeb:
    """Routes by a piece of the address (and, when given, a subset of the query); the first that fits answers."""

    def __init__(self):
        self.routes = []
        self.calls = []
        self._patch = patch.object(pinned_http, "request", self)

    def __enter__(self):
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()
        return False

    def add(self, needle, body=None, status=200, params=None):
        """:param body: a dict (JSON), bytes, or a function of the request returning ``(status, body)``"""
        self.routes.append((needle, params or {}, status, body))
        return self

    def __call__(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        query = {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}
        query.update(kwargs.get("params") or {})
        for needle, params, status, body in self.routes:
            if needle in url.split("?")[0] and all(str(query.get(key)) == str(value) for key, value in params.items()):
                if callable(body):
                    status, body = body(self.calls[-1])
                if isinstance(body, (dict, list)):
                    body = json.dumps(body).encode()
                return FakeResponse(status, body or b"")
        return FakeResponse(404, b"{}")

    def to(self, needle):
        return [call for call in self.calls if needle in call["url"].split("?")[0]]

    def hosts(self):
        return {urlsplit(call["url"]).hostname for call in self.calls}


def picture(kind="JPEG", exif=False, size=(8, 8)):
    from PIL import Image
    buffer = io.BytesIO()
    image = Image.new("RGB", size, (200, 10, 10))
    if exif:
        data = Image.Exif()
        data[0x010F] = "SecretCam"
        image.save(buffer, kind, exif=data)
    else:
        image.save(buffer, kind)
    return buffer.getvalue()

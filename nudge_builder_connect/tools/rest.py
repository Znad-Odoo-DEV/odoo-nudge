"""One careful way to ask a provider for data: REST, the sign-in of the person, a timeout, a cap on the size
of an answer, a cap on the number of calls and on the time of a whole pull. Addresses go through the egress
list of the company and through ``pinned_http`` (no redirect is followed, no private address is reached).

Nothing here writes the address, the token or an answer to a log: an error says what happened in words.
"""
import json
import time
from urllib.parse import urlparse

import requests

from odoo.exceptions import UserError

from odoo.addons.nudge.tools import pinned_http
from odoo.addons.nudge_connect import mcp_client

MAX_CALLS = 30
BUDGET_SECONDS = 50
MAX_JSON = 2 * 1024 * 1024


class Limit(Exception):
    """The number of calls or the time of a pull ran out: what was read so far is still good"""


class Rest:

    def __init__(self, env, connector, scopes=(), max_calls=MAX_CALLS, budget=BUDGET_SECONDS):
        """:param connector: the connector of the provider, usable by the person of ``env``
        :param scopes: the permissions the person must have given at the consent screen"""
        self.env = env
        self.connector = connector.sudo()
        self.calls = 0
        self.max_calls = max_calls
        self.deadline = time.monotonic() + budget
        self._token = None
        self._scopes = scopes

    # ------------------------------------------------------------------ the person's sign-in
    def _signin_words(self):
        return self.env._("Connect your %(name)s account first (the Connect button), then try again.",
                          name=self.connector.name)

    def token(self):
        if self._token is None:
            Token = self.env["nudge.connector.token"]
            try:
                self._token = Token._bearer(self.connector)
            except mcp_client.McpError as error:
                if error.code == "signin":
                    raise UserError(self.env._(
                        "Your sign-in to %(name)s ended or was withdrawn. Connect it again (the Connect button).",
                        name=self.connector.name)) from None
                raise UserError(self.connector._failure(error)) from None
            self.check_scopes()
        return self._token

    def own_token_row(self):
        # sudo: Nudge's own store; the line is looked up by this person
        return self.env["nudge.connector.token"].sudo().search(
            [("connector_id", "=", self.connector.id), ("user_id", "=", self.env.uid)], limit=1)

    def check_scopes(self):
        granted = (self.own_token_row().scope or "").split()
        if granted and any(scope not in granted for scope in self._scopes):
            raise UserError(self.env._(
                "You did not allow everything that is needed at the %(name)s consent screen. Disconnect, connect "
                "again and tick every box.", name=self.connector.name))

    def forget_token(self):
        self.own_token_row().unlink()
        self._token = None

    # ------------------------------------------------------------------ the call
    def _words(self, status):
        if status == 401:
            self.forget_token()
            return self.env._("%(name)s refused your sign-in (it ended or you withdrew it). Connect it again.",
                     name=self.connector.name)
        if status == 403:
            return self.env._("%(name)s did not allow this: your account may lack the right, or the service is not "
                     "switched on in the project of the administrator.", name=self.connector.name)
        if status == 404:
            return self.env._("%(name)s found nothing at that place: check the link and your access to it.",
                     name=self.connector.name)
        if status == 429:
            return self.env._("%(name)s asks to wait: try again in a few minutes.", name=self.connector.name)
        if status >= 500:
            return self.env._("%(name)s had a problem of its own: try again later.", name=self.connector.name)
        return self.env._("%(name)s answered in a way that cannot be used (code %(status)s).",
                 name=self.connector.name, status=status)

    def _send(self, url, params, auth, cap, accept):
        self.calls += 1
        remaining = self.deadline - time.monotonic()
        if self.calls > self.max_calls or remaining <= 1:
            raise Limit()
        parts = urlparse(url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise UserError(self.env._("The address must start with https://."))
        self.env["nudge.egress"].check(url, "connector")
        headers = {"Accept": accept}
        if auth:
            headers["Authorization"] = "Bearer " + self.token()
        seconds = min(self.connector._seconds(), remaining)
        try:
            with pinned_http.request(
                    "GET", url, params=params, headers=headers, timeout=seconds, allow_redirects=False,
                    stream=True) as response:
                status = response.status_code
                if status != 200:
                    raise UserError(self._words(status))
                return mcp_client.read_capped(response, cap, seconds)
        except mcp_client.McpError as error:
            if error.code == "toobig":
                raise UserError(self.env._("An answer was bigger than the limit and was refused.")) from None
            raise UserError(self.env._("%(name)s did not answer in time.", name=self.connector.name)) from None
        except pinned_http.Refused:
            raise UserError(self.env._("%(name)s cannot be reached from this server.",
                                       name=self.connector.name)) from None
        except requests.Timeout:
            raise UserError(self.env._("%(name)s did not answer in time.", name=self.connector.name)) from None
        except requests.RequestException:
            raise UserError(self.env._("%(name)s cannot be reached from this server.",
                                       name=self.connector.name)) from None

    def json(self, url, params=None):
        """:return: the dict ``url`` answers with the sign-in of the person
        :raise Limit: when the calls or the time of the pull ran out
        :raise UserError: in words, for anything else"""
        raw = self._send(url, params, True, MAX_JSON, "application/json")
        try:
            answer = json.loads(raw or b"{}")
        except ValueError:
            raise UserError(self.env._("%(name)s answered in a way that cannot be read.",
                                       name=self.connector.name)) from None
        return answer if isinstance(answer, dict) else {}

    def bytes(self, url, params=None, auth=True, cap=0):
        """:return: the bytes of a file, at most ``cap`` of them (``auth`` False for a public address)"""
        return self._send(url, params, auth, cap, "*/*")

"""What Instagram does differently from the sign-in of Nudge Connectors, and nothing else.

- Its token address answers ``{"data": [{"access_token": ...}]}``: the first entry is read as the answer.
- The token it gives at first lasts an hour. Right after the sign-in it is exchanged for the one that lasts
  60 days, and that one is renewed before it ends (Instagram has no refresh token).

The tokens stay in the store of Nudge Connectors, one per person; nothing is logged.
"""
import logging
from datetime import timedelta
from urllib.parse import urlencode

from odoo import api, fields, models

from odoo.addons.nudge_connect import mcp_client

_logger = logging.getLogger(__name__)

INSTAGRAM_KEY = "builder_instagram"
EXCHANGE_URL = "https://graph.instagram.com/access_token"
REFRESH_URL = "https://graph.instagram.com/refresh_access_token"
RENEW_WITHIN_DAYS = 7
REFRESH_AFTER_HOURS = 24


class NudgeConnector(models.Model):
    _inherit = "nudge.connector"

    def _oauth_http(self, url, method="GET", data=None, body=None):
        status, answer = super()._oauth_http(url, method, data=data, body=body)
        if self.sudo().key == INSTAGRAM_KEY:
            rows = answer.get("data")
            if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                answer = dict(rows[0])
        return status, answer


class NudgeConnectorOauthState(models.Model):
    _inherit = "nudge.connector.oauth.state"

    @api.model
    def _complete(self, state, code):
        connector = super()._complete(state, code)
        if connector.sudo().key == INSTAGRAM_KEY:
            self.env["nudge.connector.token"]._instagram_renew(connector, "ig_exchange_token")
        return connector


class NudgeConnectorToken(models.Model):
    _inherit = "nudge.connector.token"

    @api.model
    def _instagram_renew(self, connector, grant):
        """Swap the short token of this person for the long one (``ig_exchange_token``), or lengthen the long
        one (``ig_refresh_token``). A failure is only noted: the token in hand is still good for a while."""
        connector = connector.sudo()
        row = self.sudo().search([("connector_id", "=", connector.id), ("user_id", "=", self.env.uid)], limit=1)
        if not row or not row.access_token:
            return False
        if grant == "ig_exchange_token":
            if not connector.oauth_client_secret:
                return False
            params = {"grant_type": grant, "client_secret": connector.oauth_client_secret,
                      "access_token": row.access_token}
            url = EXCHANGE_URL
        else:
            params = {"grant_type": grant, "access_token": row.access_token}
            url = REFRESH_URL
        try:
            status, answer = connector._oauth_http(url + "?" + urlencode(params))
        except mcp_client.McpError as error:
            _logger.warning("Nudge Builder Connect: Instagram did not lengthen a token (%s)", error.code)
            return False
        token = answer.get("access_token")
        if status != 200 or not isinstance(token, str) or not token:
            _logger.warning("Nudge Builder Connect: Instagram did not lengthen a token (HTTP %s)", status)
            return False
        self._keep(connector, self.env.user, answer)
        return True

    @api.model
    def _bearer(self, connector):
        if connector.sudo().key == INSTAGRAM_KEY:
            row = self.sudo().search([("connector_id", "=", connector.id), ("user_id", "=", self.env.uid)], limit=1)
            now = fields.Datetime.now()
            if (row and row.expires and now < row.expires <= now + timedelta(days=RENEW_WITHIN_DAYS)
                    and (row.write_date or now) <= now - timedelta(hours=REFRESH_AFTER_HOURS)):
                self._instagram_renew(connector, "ig_refresh_token")
        return super()._bearer(connector)

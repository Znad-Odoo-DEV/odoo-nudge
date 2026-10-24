"""The two pages of the sign-in of a connector, for a person signed in to Odoo.

``start`` sends the person to the provider; ``callback`` is where the provider sends them back. The callback
reads only ``state`` and ``code``: where the tokens go, and the address used to ask for them, come from the
sign-in that was begun, never from the request.
"""
from html import escape

from odoo import http
from odoo.exceptions import AccessError, UserError
from odoo.http import request

HEADERS = [("Content-Type", "text/html; charset=utf-8"), ("Cache-Control", "no-store"),
           ("Referrer-Policy", "no-referrer"), ("X-Frame-Options", "DENY")]


def _page(title, message, status=200):
    body = (f"<!doctype html><html><head><meta charset='utf-8'><title>{escape(title)}</title>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'></head><body>"
            f"<h1>{escape(title)}</h1><p>{escape(message)}</p><p><a href='/odoo'>Odoo</a></p></body></html>")
    return request.make_response(body, headers=HEADERS, status=status)


class ConnectOAuth(http.Controller):

    @http.route("/nudge_connect/oauth/start/<int:connector_id>", type="http", auth="user", methods=["GET"])
    def start(self, connector_id, **_ignored):
        connector = request.env["nudge.connector"].sudo().browse(connector_id).exists()
        if not connector:
            return _page("Nudge", "There is no such connector.", 404)
        try:
            url = connector.with_env(request.env)._oauth_begin()
        except (UserError, AccessError) as refused:
            return _page("Nudge", str(refused.args[0] if refused.args else refused), 403)
        return request.redirect(url, local=False)

    @http.route("/nudge_connect/oauth/callback", type="http", auth="user", methods=["GET"])
    def callback(self, state=None, code=None, error=None, **_ignored):
        # Any redirect_uri a request carries is ignored, on purpose
        if error:
            # The state is spent: a refused sign-in cannot be finished later
            try:
                request.env["nudge.connector.oauth.state"]._consume(state, request.env.uid)
            except UserError:
                pass
            return _page("Nudge", "The provider did not let you sign in.", 400)
        try:
            connector = request.env["nudge.connector.oauth.state"]._complete(state, code)
        except UserError as refused:
            return _page("Nudge", str(refused.args[0]), 400)
        if request.env.user.has_group("base.group_system"):
            return request.redirect(
                f"/web#model=nudge.connector&id={connector.id}&view_type=form", code=303)
        return _page("Nudge", f"You are signed in to {connector.name}. You can close this tab and ask again.")

    @http.route("/nudge_connect/oauth/signout/<int:connector_id>", type="http", auth="user", methods=["POST"],
                csrf=True)
    def sign_out(self, connector_id, **_ignored):
        connector = request.env["nudge.connector"].sudo().browse(connector_id).exists()
        if not connector:
            return _page("Nudge", "There is no such connector.", 404)
        connector.with_env(request.env).action_sign_out()
        return _page("Nudge", f"You are signed out of {connector.name}.")

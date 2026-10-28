"""The public "Request a quote" form of a built website.

Anyone may open it, so every field is checked and cut, nothing the visitor wrote is ever echoed except through
QWeb (which escapes), and a request makes at most one lead (or one note) within the limits below. Same guards
as the "talk to a person" form of nudge_website, kept here because this module does not depend on it:
a honeypot field, a minimum time between showing and sending the form, a rate limit per address and per website,
and a consent box. A robot is thanked as a person is, so it cannot tell, and nothing is made for it.
"""
import hashlib
import hmac
import re
import time

from odoo import _, http
from odoo.http import request
from odoo.tools import plaintext2html

MAX_NAME = 100
MAX_EMAIL = 200
MAX_PHONE = 30
MAX_MESSAGE = 1000
MAX_SERVICE = 100
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
PHONE = re.compile(r"^\+?[0-9 ()\-]{5,30}$")
HONEYPOT = "homepage"
MIN_FILL_SECONDS = 3
SHOWN_KEY = "nudge_builder_quote_shown"
PER_ADDRESS_HOUR = 5
PER_WEBSITE_DAY = 60
_clock = time.time


class NudgeBuilderQuote(http.Controller):

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _site(website):
        """:return: the site of the website that asked for a quote form, or an empty recordset.
        sudo: a visitor has no right on sites; only the question "is there one" and its owner are read."""
        Site = request.env["nudge.builder.site"].sudo()
        domain = [("website_id", "=", website.id)]
        if "quote_form" in Site._fields:
            domain.append(("quote_form", "=", True))
        return Site.search(domain, order="id desc", limit=1)

    @staticmethod
    def _hash_address():
        secret = request.env["ir.config_parameter"].sudo().get_param("database.secret") or "nudge_builder"
        address = request.httprequest.remote_addr or ""
        return hmac.new(secret.encode(), address.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def _clean(value, limit):
        return " ".join(str(value or "").split())[:limit] if isinstance(value, (str, int)) else ""

    def _render(self, website, values=None, errors=None, status=200):
        values = values or {}
        return request.render("nudge_builder.quote_form", {
            "values": values, "errors": errors or {}, "honeypot": HONEYPOT,
            "action": "/request-a-quote/send"}, status=status)

    # ------------------------------------------------------------------
    # Doors
    # ------------------------------------------------------------------
    @http.route("/request-a-quote", type="http", auth="public", website=True, methods=["GET"], sitemap=False)
    def quote_form(self, service=None, **kwargs):
        website = request.website
        if not self._site(website):
            return request.not_found()
        request.session[SHOWN_KEY] = _clock()
        return self._render(website, {"service": self._clean(service, MAX_SERVICE)})

    @http.route("/request-a-quote/send", type="http", auth="public", website=True, methods=["POST"],
                sitemap=False)
    def quote_send(self, **post):
        website = request.website
        site = self._site(website)
        if not site:
            return request.not_found()
        env = request.env
        values = {
            "name": self._clean(post.get("name"), MAX_NAME),
            "phone": self._clean(post.get("phone"), MAX_PHONE),
            "email": self._clean(post.get("email"), MAX_EMAIL),
            "service": self._clean(post.get("service"), MAX_SERVICE),
            "message": str(post.get("message") or "").strip()[:MAX_MESSAGE]
            if isinstance(post.get("message"), str) else "",
        }
        errors = {}
        if not values["name"]:
            errors["name"] = _("Please write your name.")
        if values["phone"] and not PHONE.match(values["phone"]):
            errors["phone"] = _("Please write a valid phone number.")
        if values["email"] and not EMAIL.match(values["email"]):
            errors["email"] = _("Please write a valid email address.")
        if not values["phone"] and not values["email"]:
            errors["contact"] = _("Please give a phone number or an email address.")
        if len(values["message"]) < 5:
            errors["message"] = _("Please tell us what you need.")
        if post.get("consent") not in ("on", "1", "true", "yes"):
            errors["consent"] = _("Please agree that we may contact you about this request.")
        if errors:
            return self._render(website, values, errors, status=400)
        # Robots: thanked, nothing made
        shown = request.session.get(SHOWN_KEY)
        if (str(post.get(HONEYPOT) or "").strip() or not isinstance(shown, (int, float))
                or _clock() - shown < MIN_FILL_SECONDS):
            return request.render("nudge_builder.quote_thanks", {})
        # The place is taken, and made visible to the other requests, before anything is made
        Log = env["nudge.builder.quote.log"].sudo()
        ip_hash = self._hash_address()
        Log._forget_old()
        own, per_site = Log._count_recent(website, ip_hash)
        if own >= PER_ADDRESS_HOUR or per_site >= PER_WEBSITE_DAY:
            return self._render(website, values, {"limit": _(
                "You have sent a lot of requests in a short time. Please try again later.")}, status=429)
        line = Log.create({"website_id": website.id, "ip_hash": ip_hash})
        env.cr.commit()
        self._make(site, website, values)
        line.write({"done": True})
        request.session.pop(SHOWN_KEY, None)
        return request.render("nudge_builder.quote_thanks", {})

    # ------------------------------------------------------------------
    # The request becomes a lead, or a note for the owner of the site
    # ------------------------------------------------------------------
    def _make(self, site, website, values):
        env = request.env
        lines = []
        if values["phone"]:
            lines.append(_("Phone: %s", values["phone"]))
        if values["email"]:
            lines.append(_("Email: %s", values["email"]))
        if values["service"]:
            lines.append(_("Service: %s", values["service"]))
        lines += ["", values["message"], "", _("The visitor agreed to be contacted about this request.")]
        body = "\n".join(lines)
        title = _("Quote request: %s", values["name"])
        # sudo: a visitor has no right on leads or partners. These are the only records the form makes, asked for
        # by the visitor, with fields chosen here and no others; the text goes through plaintext2html (escaped).
        if "crm.lead" in env.registry:
            lead = {"name": title, "contact_name": values["name"], "email_from": values["email"] or False,
                    "phone": values["phone"] or False, "user_id": False, "description": plaintext2html(body),
                    "company_id": website.company_id.id}
            if "website_id" in env["crm.lead"]._fields:
                lead["website_id"] = website.id
            env["crm.lead"].sudo().create(lead)
            return "lead"
        partner = website.company_id.partner_id.sudo()
        owner = site.create_uid if site.create_uid.active else env.ref("base.user_admin", raise_if_not_found=False)
        partner.activity_schedule(
            "mail.mail_activity_data_todo", summary=title, note=plaintext2html(body), user_id=(owner or env.user).id)
        return "note"

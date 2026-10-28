"""The public booking request of a car workshop: /workshop-booking.

Anyone may open it, so every field is checked and cut, nothing the visitor wrote is echoed except through QWeb
(which escapes), and a request makes at most one lead (or one to-do) within the limits below. The guards are those
of the quote form of the Website Builder (controllers/quote.py), whose helpers and limits are used as they are:
a honeypot field, a minimum time between showing and sending the form, a rate limit per address and per website
(the log is the quote form's, so a visitor's quote requests and booking requests count together), and a consent
box. A robot is thanked as a person is, so it cannot tell, and nothing is made for it.
"""
import time
from datetime import date, timedelta

from odoo import fields, http
from odoo.http import request
from odoo.tools import plaintext2html

from odoo.addons.nudge_builder.controllers.quote import (
    EMAIL, HONEYPOT, MAX_EMAIL, MAX_MESSAGE, MAX_NAME, MAX_PHONE, MIN_FILL_SECONDS, PER_ADDRESS_HOUR, PER_WEBSITE_DAY,
    PHONE, NudgeBuilderQuote)

MAX_CAR = 100
MAX_SERVICE = 100
MAX_DAYS_AHEAD = 180
SHOWN_KEY = "nudge_builder_workshop_shown"
KIND = "workshop_service"
_clock = time.time


def has_crm(env):
    """:return: True when CRM is installed: the request is then a lead"""
    return "crm.lead" in env.registry


class NudgeBuilderWorkshopBooking(http.Controller):

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _site(website):
        """:return: the newest site of the website that has a bookable service, or an empty recordset.
        sudo: a visitor has no right on sites; only the question "is there one" and its owner are read."""
        Site = request.env["nudge.builder.site"].sudo()
        for site in Site.search([("website_id", "=", website.id)], order="id desc"):
            if site._kind_records(KIND).filtered("bookable"):
                return site
        return Site.browse()

    @staticmethod
    def _services(site):
        """:return: the names of the services that can be booked, in the language of the visitor"""
        return site.with_context(lang=request.env.lang)._kind_records(KIND).filtered("bookable").mapped("name")

    @staticmethod
    def _day(value):
        """:return: the day asked as a date, between today and half a year ahead; None when it is not one"""
        try:
            day = date.fromisoformat(str(value or "").strip()[:10])
        except ValueError:
            return None
        today = fields.Date.context_today(request.env["res.users"])
        return day if today <= day <= today + timedelta(days=MAX_DAYS_AHEAD) else None

    def _render(self, site, values=None, errors=None, status=200):
        return request.render("nudge_builder_workshop.booking_form", {
            "values": values or {}, "errors": errors or {}, "honeypot": HONEYPOT, "services": self._services(site),
            "today": fields.Date.context_today(request.env["res.users"]),
            "last_day": fields.Date.context_today(request.env["res.users"]) + timedelta(days=MAX_DAYS_AHEAD)},
            status=status)

    # ------------------------------------------------------------------
    # Doors
    # ------------------------------------------------------------------
    @http.route("/workshop-booking", type="http", auth="public", website=True, methods=["GET"], sitemap=False)
    def booking_form(self, service=None, **kwargs):
        site = self._site(request.website)
        if not site:
            return request.not_found()
        request.session[SHOWN_KEY] = _clock()
        service = NudgeBuilderQuote._clean(service, MAX_SERVICE)
        return self._render(site, {"service": service if service in self._services(site) else ""})

    @http.route("/workshop-booking/send", type="http", auth="public", website=True, methods=["POST"],
                sitemap=False)
    def booking_send(self, **post):
        website = request.website
        site = self._site(website)
        if not site:
            return request.not_found()
        env = request.env
        clean = NudgeBuilderQuote._clean
        values = {
            "name": clean(post.get("name"), MAX_NAME),
            "phone": clean(post.get("phone"), MAX_PHONE),
            "email": clean(post.get("email"), MAX_EMAIL),
            "car": clean(post.get("car"), MAX_CAR),
            "service": clean(post.get("service"), MAX_SERVICE),
            "day": clean(post.get("day"), 10),
            "message": str(post.get("message") or "").strip()[:MAX_MESSAGE] if isinstance(post.get("message"), str) else "",
        }
        errors = {}
        if not values["name"]:
            errors["name"] = env._("Please write your name.")
        if values["phone"] and not PHONE.match(values["phone"]):
            errors["phone"] = env._("Please write a valid phone number.")
        if values["email"] and not EMAIL.match(values["email"]):
            errors["email"] = env._("Please write a valid email address.")
        if not values["phone"] and not values["email"]:
            errors["contact"] = env._("Please give a phone number or an email address.")
        if not values["car"]:
            errors["car"] = env._("Please tell us the make and model of the car.")
        if values["service"] not in self._services(site):
            errors["service"] = env._("Please choose one of the services.")
        if not self._day(values["day"]):
            errors["day"] = env._("Please choose a day from today to six months ahead.")
        if post.get("consent") not in ("on", "1", "true", "yes"):
            errors["consent"] = env._("Please agree that we may contact you about this request.")
        if errors:
            return self._render(site, values, errors, status=400)
        # Robots: thanked, nothing made
        shown = request.session.get(SHOWN_KEY)
        if (str(post.get(HONEYPOT) or "").strip() or not isinstance(shown, (int, float))
                or _clock() - shown < MIN_FILL_SECONDS):
            return request.render("nudge_builder_workshop.booking_thanks", {})
        # The place is taken, and made visible to the other requests, before anything is made
        Log = env["nudge.builder.quote.log"].sudo()
        ip_hash = NudgeBuilderQuote._hash_address()
        Log._forget_old()
        own, per_site = Log._count_recent(website, ip_hash)
        if own >= PER_ADDRESS_HOUR or per_site >= PER_WEBSITE_DAY:
            return self._render(site, values, {"limit": env._(
                "You have sent a lot of requests in a short time. Please try again later.")}, status=429)
        line = Log.create({"website_id": website.id, "ip_hash": ip_hash})
        env.cr.commit()
        self._make(site, website, values)
        line.write({"done": True})
        request.session.pop(SHOWN_KEY, None)
        return request.render("nudge_builder_workshop.booking_thanks", {})

    # ------------------------------------------------------------------
    # The request becomes a lead, or a to-do for the owner of the site
    # ------------------------------------------------------------------
    def _make(self, site, website, values):
        env = request.env
        lines = [env._("Car: %s", values["car"]), env._("Service: %s", values["service"]),
                 env._("Day wanted: %s", values["day"])]
        if values["phone"]:
            lines.append(env._("Phone: %s", values["phone"]))
        if values["email"]:
            lines.append(env._("Email: %s", values["email"]))
        lines += ["", values["message"], "", env._("The visitor agreed to be contacted about this request.")]
        body = "\n".join(lines)
        title = env._("Workshop booking: %s", values["name"])
        # sudo: a visitor has no right on leads or partners. These are the only records the form makes, asked for
        # by the visitor, with fields chosen here and no others; the text goes through plaintext2html (escaped).
        if has_crm(env):
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

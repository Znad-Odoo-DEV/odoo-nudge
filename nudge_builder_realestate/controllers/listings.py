"""/listings: the published listings of the website, filtered on the server.

Anyone may open it, so the choices of the visitor are read as plain values, checked against what exists (a
type or a city that is not one of the listings is ignored) and never echoed except through QWeb, which
escapes. Nothing is written. The listings are read with sudo because a visitor has no right on them, and only
the published ones of the site of this website are ever read.
"""
import math

from markupsafe import Markup

from odoo import http
from odoo.http import request

from odoo.addons.nudge_builder.tools import compat

from ..tools import listings

MAX_TEXT = 100
MAX_PRICE = 10 ** 12
MAX_CARDS = 24


class NudgeBuilderListings(http.Controller):

    @staticmethod
    def _site(website):
        """:return: the newest site of the website that has a listing, or an empty recordset"""
        for site in request.env["nudge.builder.site"].sudo().search([("website_id", "=", website.id)], order="id desc"):
            if "nudge.builder.plugin" in request.env and site._kind_records(listings.KIND):
                return site
        return request.env["nudge.builder.site"].sudo().browse()

    @staticmethod
    def _number(value, whole=False):
        try:
            number = float(str(value or "").strip())
        except ValueError:
            return None
        if not math.isfinite(number) or number < 0 or number > MAX_PRICE:
            return None
        return int(number) if whole else number

    @classmethod
    def _values(cls, post, items):
        """:return: the choices of the visitor that are valid: ``{field: value}``"""
        offers, types, cities, most = listings.options(items)
        values = {}
        if post.get("offer") in offers:
            values["offer"] = post["offer"]
        for name, found in (("type", types), ("city", cities)):
            text = " ".join(str(post.get(name) or "").split())[:MAX_TEXT] if isinstance(post.get(name), str) else ""
            if text in found:
                values[name] = text
        beds = cls._number(post.get("bedrooms"), whole=True) if isinstance(post.get("bedrooms"), str) else None
        if beds and 1 <= beds <= most:
            values["bedrooms"] = beds
        for name in ("price_min", "price_max"):
            number = cls._number(post.get(name)) if isinstance(post.get(name), str) else None
            if number is not None:
                values[name] = number
        return values

    @staticmethod
    def _domain(site, values):
        domain = [("site_id", "=", site.id), ("published", "=", True),
                  ("listing_status", "in", [values["offer"]] if values.get("offer") else list(listings.OFFERS))]
        if values.get("type"):
            domain.append(("category", "=", values["type"]))
        if values.get("city"):
            domain.append(("location", "=", values["city"]))
        if values.get("bedrooms"):
            domain.append(("bedrooms", ">=", values["bedrooms"]))
        if values.get("price_min") is not None:
            domain.append(("price", ">=", values["price_min"]))
        if values.get("price_max") is not None:
            domain.append(("price", "<=", values["price_max"]))
        return domain

    @http.route("/listings", type="http", auth="public", website=True, methods=["GET"], sitemap=False)
    def listings(self, **post):
        website = request.website
        site = self._site(website)
        if not site:
            return request.not_found()
        env = request.env
        lang = env["res.lang"]._lang_get(env.lang) or env["res.lang"]._lang_get("en_US")
        site = site.with_context(lang=lang.code)
        everything = [site._record_item(listings.KIND, record, lang) for record in site._kind_records(listings.KIND)]
        values = self._values(post, everything)
        found = env["nudge.builder.project"].sudo().with_context(lang=lang.code).search(
            self._domain(site, values), limit=MAX_CARDS)
        cx = site._plugin_ctx(listings.KIND, lang)
        uid = compat.unique_id()
        form = listings.filter_form(cx, everything, values, uid)
        section = listings.cards(
            [site._record_item(listings.KIND, record, lang) for record in found], None, site.direction or "a",
            "", "", cx, uid)
        return request.render("nudge_builder_realestate.listings_page", {
            "form": Markup(compat.to_string(form)), "cards": Markup(compat.to_string(section)) if section is not None else "",
            "count": len(found), "words": cx["labels"]})

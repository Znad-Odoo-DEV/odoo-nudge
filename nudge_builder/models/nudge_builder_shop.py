import json
import logging

from odoo import models
from odoo.tools import file_open

from ..tools import checks, shop

_logger = logging.getLogger(__name__)


class NudgeBuilderSite(models.Model):
    _inherit = "nudge.builder.site"

    def _shop_template(self):
        """:return: the template of the site (a dict), {} when it has none"""
        self.ensure_one()
        for key in (self.template_key, "generic"):
            if not key:
                continue
            try:
                with file_open("nudge_builder/data/templates/%s.json" % key) as handle:
                    return json.load(handle)
            except (OSError, ValueError):
                continue
        return {}

    def _shop_plan(self):
        """:return: the plan of the shop (for the card the person confirms); nothing is changed"""
        self.ensure_one()
        return shop.plan_shop(self.env, self.website_id, self._shop_template())

    def _build_shop(self, plan=None):
        """Arrange the shop of the site: categories, descriptions, filters, layout, banners, menu.
        Called by the plan after the pages; ``plan`` is the one the person confirmed.

        :return: the log lines"""
        self.ensure_one()
        result = shop.build_shop(self.env, self.website_id, self._shop_template(), plan=plan)
        lines = ["%s: %s" % (line["label"], line["value"]) for line in result["made"]] + result["notes"]
        lines += ["%s %s" % ("[x]" if item["ok"] else "[ ]", item["label"]) for item in result["readiness"]]
        return lines

    def _run_checks(self):
        """The last step of a build: the site check. Returns ``[{level, page_key, message}]``"""
        self.ensure_one()
        found, _skipped = checks.check_site(self.env, self.website_id)
        urls = {page.url: page.key for page in self.page_ids}
        out = []
        for item in found:
            keys = [urls.get(url, "") for url in item["urls"] if url in urls]
            out.append({"level": item["severity"], "page_key": keys[0] if keys else "",
                        "message": checks.finding_text(self.env, item["key"], item["count"], item["extra"])})
        return out

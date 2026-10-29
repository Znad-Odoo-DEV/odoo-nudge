"""Compliance packs that age well: the deadline notices (NP-01) and the signed update feed (NP-08).

The notices: every day each company is looked at against the packs of its country; a check with a ``deadline``
that is 90, 30 or 7 days away (or less) is told once, to the accounting managers of the company. The told
notice is a row, so a second run, a second worker or a restart never tells it twice.

The feed: off until an administrator sets an address and the public key of the publisher. A fetch checks the
Ed25519 signature, validates every pack like a bundled one and stores it as *pending* with the difference to the
pack in force. Only a system administrator approves it; the bundled file stays the fallback and a rollback
puts the previous pack back.
"""
import hashlib
import json
import logging
from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from ..tools import compliance, compliance_feed
from ..tools.bus import notify_user
from .nudge_recipe_market import FetchError, _download

_logger = logging.getLogger(__name__)

FEED_URL_PARAM = "nudge.compliance_feed_url"
FEED_KEY_PARAM = "nudge.compliance_feed_key"
FEED_STATUS_PARAM = "nudge.compliance_feed_status"
THRESHOLDS = (90, 30, 7)
CREDENTIAL_THRESHOLDS = (60, 30, 7, 0)  # NP-17: 0 is the day of the expiry itself, and after it
CREDENTIAL_PACK = "credential"
EXPIRED_STILL_NEWS = 30  # an expiry older than this many days is old news and is not told
ACCOUNTING_MANAGERS = "account.group_account_manager"


class NudgeComplianceNotice(models.Model):
    """A deadline that was told to the accounting managers of a company, at one of 90, 30 and 7 days."""

    _name = "nudge.compliance.notice"
    _description = "Compliance Deadline Notice"
    _order = "deadline, id"

    company_id = fields.Many2one("res.company", required=True, ondelete="cascade", index=True)
    pack = fields.Char(required=True)
    check_key = fields.Char(required=True)
    threshold = fields.Integer(required=True)
    deadline = fields.Date(required=True)
    title = fields.Char(readonly=True)
    notified_on = fields.Datetime(default=fields.Datetime.now, readonly=True)
    days_left = fields.Integer(compute="_compute_days_left")

    if hasattr(models, "Constraint"):
        _once = models.Constraint("unique(company_id, pack, check_key, threshold)",
                                  "A deadline is told once for each threshold.")
    else:  # Odoo 17 and 18
        _sql_constraints = [("once", "unique(company_id, pack, check_key, threshold)",
                             "A deadline is told once for each threshold.")]

    @api.depends("deadline")
    def _compute_days_left(self):
        today = fields.Date.context_today(self)
        for notice in self:
            notice.days_left = (notice.deadline - today).days if notice.deadline else 0

    @api.model
    def _recipients(self, company):
        """:return: the users to tell for ``company``: its accounting managers, else the Nudge managers"""
        group = self.env.ref(ACCOUNTING_MANAGERS, raise_if_not_found=False)
        xmlid = ACCOUNTING_MANAGERS if group else "nudge.group_nudge_manager"
        users = self.env["res.users"].sudo().search([("share", "=", False), ("company_ids", "in", company.id)])
        return users.filtered(lambda user: user.has_group(xmlid))

    @api.model
    def _threshold_for(self, days):
        """:return: the tightest of 90, 30 and 7 that ``days`` left has reached, or 0 when it is far or past"""
        if days < 0 or days > THRESHOLDS[0]:
            return 0
        return min(t for t in THRESHOLDS if days <= t)

    @api.model
    def _cron_deadlines(self, today=None):
        """Every day, for every company: tell the deadlines of its country that came within 90, 30 or 7 days.
        When the cron missed days, only the tightest threshold reached is told."""
        today = today or fields.Date.context_today(self)
        for company in self.env["res.company"].sudo().search([]):
            try:
                with self.env.cr.savepoint():
                    self.with_company(company)._deadlines_of(company, today)
                    self.with_company(company)._credentials_of(company, today)
            except Exception:  # noqa: BLE001 - one company must not stop the others
                _logger.exception("Nudge: the compliance deadlines of company %s could not be told", company.id)
            if not self.env.context.get("nudge_keep_transaction"):
                self.env.cr.commit()

    def _deadlines_of(self, company, today):
        for pack in compliance.packs_for(self.env, company):
            for entry in pack.checks:
                if not entry.deadline:
                    continue
                days = (date.fromisoformat(entry.deadline) - today).days
                threshold = self._threshold_for(days)
                if not threshold:
                    continue
                # sudo: Nudge's own record of what was told; the managers only read it
                Notice = self.sudo()
                if Notice.search_count([("company_id", "=", company.id), ("pack", "=", pack.id),
                                        ("check_key", "=", entry.key), ("deadline", "=", entry.deadline),
                                        ("threshold", "<=", threshold)]):
                    continue
                notice = Notice.create({
                    "company_id": company.id, "pack": pack.id, "check_key": entry.key, "threshold": threshold,
                    "deadline": entry.deadline, "title": compliance.pick(entry.title, self.env)})
                notice._tell(company, entry, days)

    @api.model
    def _credential_threshold(self, days):
        """:return: the tightest of 60, 30, 7 that ``days`` left has reached, 0 on the day and after it,
        or None when the date is far (or expired long ago)"""
        if days > CREDENTIAL_THRESHOLDS[0] or days < -EXPIRED_STILL_NEWS:
            return None
        return min(t for t in CREDENTIAL_THRESHOLDS if days <= t)

    def _credentials_of(self, company, today):
        """NP-17: tell, once for each threshold, the credentials and certificates of ``company`` that expire.
        Only the date, the name and the company are read, never the certificate or the key."""
        for source, record in self.env["nudge.credential"]._expiries(company):
            days = (record["date"] - today).days
            threshold = self._credential_threshold(days)
            if threshold is None:
                continue
            # the date is part of the key: a renewed credential has a new date and is watched afresh
            key = f"{source}:{record['id']}:{record['date'].isoformat()}"
            # sudo: Nudge's own record of what was told; the managers only read it
            Notice = self.sudo()
            if Notice.search_count([("company_id", "=", company.id), ("pack", "=", CREDENTIAL_PACK),
                                    ("check_key", "=", key), ("threshold", "<=", threshold)]):
                continue
            notice = Notice.create({
                "company_id": company.id, "pack": CREDENTIAL_PACK, "check_key": key, "threshold": threshold,
                "deadline": record["date"], "title": record["name"]})
            notice._tell_credential(company, record["name"], days, source)

    def _tell_credential(self, company, name, days, source="manual"):
        self.ensure_one()
        for user in self._recipients(company):
            env = self.with_context(lang=user.lang or self.env.lang).env
            if days > 0:
                subject = _("%(name)s expires in %(days)s days", name=name, days=days)
            elif days == 0:
                subject = _("%(name)s expires today", name=name)
            else:
                subject = _("%(name)s has expired", name=name)
            # a certificate of the e-invoicing modules stops e-invoicing; what a person entered by hand is not
            # known to: it only stops working
            einvoicing = source != "manual"
            if days > 0:
                advice = (_("Renew it before e-invoicing stops.") if einvoicing
                          else _("Renew it before it stops working."))
            elif days == 0:
                advice = (_("Renew it today: e-invoicing stops after today.") if einvoicing
                          else _("Renew it today: it stops working after today."))
            else:
                advice = (_("Renew it now: e-invoicing is no longer possible with it.") if einvoicing
                          else _("Renew it now: it no longer works."))
            body = _("%(subject)s (%(date)s). %(advice)s", subject=subject, date=self.deadline, advice=advice)
            try:
                notify_user(user, "simple_notification", {"title": subject, "message": body, "type": "warning"})
                if "mail.thread" in self.env:
                    user.partner_id.message_notify(partner_ids=[user.partner_id.id], subject=subject, body=body)
            except Exception:  # noqa: BLE001
                _logger.info("Nudge: a manager could not be told of an expiring credential", exc_info=True)

    def _tell(self, company, entry, days):
        self.ensure_one()
        for user in self._recipients(company):
            env = self.with_context(lang=user.lang or self.env.lang).env
            title = compliance.pick(entry.title, env)
            body = _("%(title)s: %(date)s, %(days)s days left. A checklist, not legal advice; "
                         "confirm with the authority.", title=title, date=entry.deadline, days=days)
            subject = _("Compliance deadline in %(days)s days", days=days)
            try:
                notify_user(user, "simple_notification", {"title": subject, "message": body, "type": "warning"})
                if "mail.thread" in self.env:  # the inbox keeps what the bubble forgets
                    user.partner_id.message_notify(partner_ids=[user.partner_id.id], subject=subject, body=body)
            except Exception:  # noqa: BLE001
                _logger.info("Nudge: a manager could not be told of a compliance deadline", exc_info=True)


class NudgeComplianceUpdate(models.Model):
    """A pack from the signed feed, waiting for an administrator, or approved, or put aside."""

    _name = "nudge.compliance.update"
    _description = "Compliance Pack Update"
    _order = "id desc"

    pack_id = fields.Char(string="Pack", required=True, readonly=True, index=True)
    issued = fields.Char(string="Issued", readonly=True)
    payload = fields.Text(readonly=True)
    digest = fields.Char(readonly=True, index=True)
    changes_json = fields.Text(readonly=True)
    diff = fields.Text(string="What Changes", compute="_compute_diff")
    state = fields.Selection([
        ("pending", "Waiting for approval"), ("applied", "In force"), ("rejected", "Declined"),
        ("superseded", "Replaced by a newer one"), ("rolled_back", "Rolled back")],
        default="pending", required=True, readonly=True, index=True)
    approved_by = fields.Many2one("res.users", readonly=True)
    approved_on = fields.Datetime(readonly=True)

    @api.depends("changes_json", "pack_id")
    def _compute_diff(self):
        words = {"+": _("added"), "-": _("removed"), "~": _("changed")}
        for update in self:
            lines = []
            for sign, key, what, before, after in json.loads(update.changes_json or "[]"):
                line = f"{sign} {key} ({words.get(sign, sign)})"
                if what:
                    line += f": {what}"
                    if before or after:
                        line += f" {before or '-'} -> {after or '-'}"
                lines.append(line)
            update.diff = "\n".join(lines) or _("Nothing changes.")

    # ------------------------------------------------------------------ rights
    @api.model
    def _check_admin(self):
        if not self.env.is_superuser() and not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only a system administrator decides on a compliance pack update."))

    # ------------------------------------------------------------------ fetching
    @api.model
    def _feed_settings(self):
        Parameter = self.env["ir.config_parameter"].sudo()
        return (Parameter.get_param(FEED_URL_PARAM) or "").strip(), (Parameter.get_param(FEED_KEY_PARAM) or "").strip()

    @api.model
    def _cron_fetch(self):
        """Every day: read the feed when an address and a key are set. Off (and silent) otherwise."""
        try:
            self._fetch(quiet=True)
        except Exception:  # noqa: BLE001
            _logger.exception("Nudge: the compliance feed could not be read")

    @api.model
    def _fetch(self, quiet=False):
        """:return: the updates made pending by this fetch (a recordset)
        :raise UserError: when it is not allowed or the feed is refused (unless ``quiet`` and it is only off)"""
        url, key = self._feed_settings()
        if not url or not key:
            if quiet:
                return self.browse()
            raise UserError(_("The feed is off: set its address and the public key in the settings."))
        if self.env["nudge.provider"]._is_private_mode():
            if quiet:
                return self.browse()
            raise UserError(_("Private mode is on: the feed is not fetched."))
        try:
            self.env["nudge.egress"].check(url, "feed")  # NP-15
            raw = _download(url, compliance_feed.MAX_FEED_BYTES,
                            check=lambda hop: self.env["nudge.egress"].check(hop, "feed"))
            issued, packs = compliance_feed.verify(raw, key)
        except FetchError as error:
            raise UserError(_("The feed could not be read (%s).", error.code)) from None
        except compliance_feed.FeedError as error:
            _logger.warning("Nudge compliance feed refused: %s", error)
            raise UserError(_("The feed is refused: %s", str(error))) from None
        made = self._store(issued, packs)
        self._remember_check(packs, len(made))
        return made

    @api.model
    def _remember_check(self, packs, changed):
        """Keep what the last read of a feed whose signature was verified found, to show it: how many countries
        the feed covers (read from its packs) and how many packs would change. A refused feed is told to the
        person by the error itself and keeps the last good result."""
        countries = {str(code).upper() for pack in packs for code in (pack.get("countries") or ())}
        status = {"countries": len(countries), "changed": changed,
                  "at": fields.Datetime.to_string(fields.Datetime.now())}
        self.env["ir.config_parameter"].sudo().set_param(FEED_STATUS_PARAM, json.dumps(status))

    @api.model
    def _status_text(self):
        """:return: the result of the last read of the feed, in words ("" when it was never read)"""
        try:
            status = json.loads(self.env["ir.config_parameter"].sudo().get_param(FEED_STATUS_PARAM) or "")
        except ValueError:
            return ""
        if not isinstance(status, dict):
            return ""
        return _("Signature verified: %(countries)s countries, %(changed)s packs would change (%(at)s)",
                          countries=status.get("countries", 0), changed=status.get("changed", 0),
                          at=status.get("at", ""))

    @api.model
    def _store(self, issued, packs):
        """Validate each pack of a feed whose signature was checked, and keep the ones that differ from the pack
        in force as pending updates. A pack that does not load is left out (and logged), the others go on."""
        current, _refused = compliance.load_all(self.env)
        made = self.browse()
        # sudo: Nudge's own infrastructure rows; approving them is the administrator's act (action_approve)
        Update = self.sudo()
        for raw in packs:
            try:
                new = compliance.load_pack(raw, "feed")
            except compliance.PackError as error:
                _logger.warning("Nudge compliance feed: a pack is refused: %s", error)
                continue
            changes = compliance_feed.changes(current.get(new.id), new)
            if not changes:
                continue
            text = json.dumps(raw, ensure_ascii=False, sort_keys=True)
            digest = hashlib.sha256(text.encode()).hexdigest()
            if Update.search_count([("pack_id", "=", new.id), ("digest", "=", digest),
                                    ("state", "in", ("pending", "applied", "rejected"))]):
                continue
            Update.search([("pack_id", "=", new.id), ("state", "=", "pending")]).write({"state": "superseded"})
            made += Update.create({
                "pack_id": new.id, "issued": issued, "payload": text, "digest": digest,
                "changes_json": json.dumps(changes)})
        if made:
            self._tell_admins(made)
        return made

    @api.model
    def _tell_admins(self, updates):
        admins = self.env["res.users"].sudo().search([("share", "=", False)]).filtered(
            lambda user: user.has_group("base.group_system"))
        for user in admins:
            try:
                env = self.with_context(lang=user.lang or self.env.lang).env
                notify_user(user, "simple_notification", {
                    "title": _("Compliance pack update available"),
                    "message": _("%(count)s pack(s) wait for approval: %(packs)s.", count=len(updates),
                                     packs=", ".join(updates.mapped("pack_id"))), "type": "info"})
            except Exception:  # noqa: BLE001
                _logger.info("Nudge: an administrator could not be told of a pack update", exc_info=True)

    # ------------------------------------------------------------------ deciding
    def action_approve(self):
        """A system administrator puts the pending pack in force, in place of the bundled one."""
        self._check_admin()
        for update in self:
            if update.state != "pending":
                raise UserError(_("Only an update that waits for approval can be approved."))
            # sudo: the rights were checked above; the table is read-only for everybody else
            update.sudo().write({"state": "applied", "approved_by": self.env.user.id,
                                 "approved_on": fields.Datetime.now()})
        return True

    def action_reject(self):
        self._check_admin()
        for update in self:
            if update.state != "pending":
                raise UserError(_("Only an update that waits for approval can be declined."))
            update.sudo().write({"state": "rejected", "approved_by": self.env.user.id,
                                 "approved_on": fields.Datetime.now()})
        return True

    def action_rollback(self):
        """Put the previous pack back: the one approved before, or the bundled file."""
        self._check_admin()
        for update in self:
            if update.state != "applied":
                raise UserError(_("Only a pack in force can be rolled back."))
            update.sudo().write({"state": "rolled_back"})
        return True

    @api.model
    def action_check_now(self):
        """The button of the settings: read the feed now. :return: the list of updates"""
        self._check_admin()
        self._fetch()
        action = self.env["ir.actions.actions"]._for_xml_id("nudge.nudge_compliance_update_action")
        status = self._status_text()
        if status:
            # The title of the list says what the check found. The web client (17, 18 and 19) titles an action with
            # its display_name before its name, and _for_xml_id reads both: both say it
            action["name"] = action["display_name"] = status
        return action

import hashlib
import ipaddress
import logging
from datetime import timedelta

from odoo import api, fields, models

RATE_MESSAGES_PARAM = "nudge_website.rate_messages"
RATE_MINUTES_PARAM = "nudge_website.rate_minutes"
HANDOVER_MAX_PARAM = "nudge_website.handover_max"
DEFAULT_MESSAGES = 20
DEFAULT_MINUTES = 10
DEFAULT_HANDOVER_MAX = 3
HANDOVER_MINUTES = 60
ARCHIVE_BATCH = 2000
TRANSCRIPT_TURNS = 20
TRANSCRIPT_MINUTES = 120

_logger = logging.getLogger(__name__)


class NudgeWebMessage(models.Model):
    """One message of a visitor: when, from where (a hash, never the address),
    what was asked and whether the assistant could answer. It is what the
    limit per address counts, and what the company reads to know what its
    visitors want. Lines older than the days set on the company are archived
    (never deleted) by a daily cron. Nothing here is ever given to the staff
    assistant as an instruction."""
    _name = "nudge.web.message"
    _description = "Nudge Visitor Question"
    _order = "id desc"
    _rec_name = "question"

    active = fields.Boolean(default=True, index=True)
    website_id = fields.Many2one("website", ondelete="cascade", index=True, readonly=True)
    company_id = fields.Many2one("res.company", index=True, readonly=True, ondelete="cascade")
    user_id = fields.Many2one("res.users", string="Visitor", readonly=True, ondelete="set null")
    ip_hash = fields.Char(index=True, readonly=True)
    session_hash = fields.Char(index=True, readonly=True)
    kind = fields.Selection(
        [("ask", "Question"), ("handover", "Talk to a Person")], default="ask", readonly=True)
    question = fields.Char(readonly=True)
    answer = fields.Char(readonly=True, help="What the assistant said; it builds the conversation of a request.")
    answered = fields.Boolean(readonly=True)
    used_ai = fields.Boolean(string="Written by the AI", readonly=True)

    @api.model
    def _hash_address(self, address):
        """The address as Odoo reports it (``proxy_mode`` decides whether a proxy header is
        believed; none is read here). An IPv6 address counts by its /64, which one machine owns."""
        try:
            ip = ipaddress.ip_address(address)
            if ip.version == 6:
                address = str(ipaddress.ip_network(f"{ip}/64", strict=False).network_address)
        except ValueError:
            pass
        salt = self.env["ir.config_parameter"].sudo().get_param("database.uuid") or ""
        return hashlib.sha256(f"{salt}:{address}".encode()).hexdigest()[:32]

    @api.model
    def _limits(self):
        """:return: ``(messages, minutes)`` one address may use"""
        Parameter = self.env["ir.config_parameter"].sudo()
        out = []
        for param, default in ((RATE_MESSAGES_PARAM, DEFAULT_MESSAGES), (RATE_MINUTES_PARAM, DEFAULT_MINUTES)):
            try:
                out.append(max(1, int(Parameter.get_param(param) or default)))
            except ValueError:
                out.append(default)
        return out

    @api.model
    def _is_limited(self, ip_hash, session_hash=None, kind="ask"):
        """:return: whether this address, or this session, used its messages of the last minutes.
        A request for a person has its own, smaller, allowance per hour."""
        messages, minutes = self._limits()
        if kind == "handover":
            try:
                messages = max(1, int(self.env["ir.config_parameter"].sudo().get_param(
                    HANDOVER_MAX_PARAM) or DEFAULT_HANDOVER_MAX))
            except ValueError:
                messages = DEFAULT_HANDOVER_MAX
            minutes = HANDOVER_MINUTES
        since = fields.Datetime.now() - timedelta(minutes=minutes)
        base = [("kind", "=", kind), ("create_date", ">=", since)]
        Message = self.sudo().with_context(active_test=False)
        if Message.search_count(base + [("ip_hash", "=", ip_hash)]) >= messages:
            return True
        return bool(session_hash) and Message.search_count(
            base + [("session_hash", "=", session_hash)]) >= messages

    @api.model
    def _reserve(self, website, user, ip_hash, session_hash, question, kind="ask"):
        """Take a place before anything is paid for: the lock of the address makes the count and
        the line one step, so parallel requests cannot all pass. The caller commits, then answers.

        :return: the line written, or an empty recordset when the address or session is limited"""
        self.env.cr.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"nudge_web_ip:{ip_hash}",))
        if self._is_limited(ip_hash, session_hash, kind):
            return self.browse()
        return self._log(website, user, ip_hash, question, False, kind=kind, session_hash=session_hash)

    @api.model
    def _log(self, website, user, ip_hash, question, answered, kind="ask", used_ai=False,
             session_hash=False, answer=False):
        """Written by the system for a visitor. A manager reads it as the list of what
        visitors asked; it never becomes a note, a lesson or a remembered answer."""
        text = " ".join((question or "").split())[:400]
        return self.sudo().create({
            "website_id": website.id, "company_id": website.company_id.id, "user_id": user.id,
            "ip_hash": ip_hash, "session_hash": session_hash, "kind": kind, "question": text,
            "answered": answered, "used_ai": used_ai, "answer": " ".join((answer or "").split())[:500]})

    @api.model
    def _transcript(self, session_hash):
        """:return: ``[(role, text)]`` of this session as Nudge itself stored it, oldest first.
        What the browser claims was said is never used."""
        if not session_hash:
            return []
        since = fields.Datetime.now() - timedelta(minutes=TRANSCRIPT_MINUTES)
        rows = self.sudo().search([
            ("session_hash", "=", session_hash), ("kind", "=", "ask"), ("create_date", ">=", since)],
            order="id desc", limit=TRANSCRIPT_TURNS)
        turns = []
        for row in reversed(rows):
            turns.append(("user", row.question or ""))
            if row.answer:
                turns.append(("assistant", row.answer))
        return turns

    @api.model
    def _cron_archive_old(self):
        """Archive (never delete) the visitor lines older than the days each company keeps them.
        0 days: that company keeps them."""
        for company in self.env["res.company"].sudo().search([("nudge_web_keep_days", ">", 0)]):
            limit = fields.Datetime.now() - timedelta(days=company.nudge_web_keep_days)
            rows = self.sudo().search(
                [("company_id", "=", company.id), ("create_date", "<", limit)], limit=ARCHIVE_BATCH)
            rows.write({"active": False})
            if rows:
                _logger.info("Nudge: %s visitor lines of %s archived", len(rows), company.name)
        return True

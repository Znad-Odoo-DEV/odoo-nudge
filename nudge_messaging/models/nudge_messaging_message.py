import hashlib
import json
from datetime import timedelta

from psycopg2 import IntegrityError

from odoo import api, fields, models

RATE_MESSAGES_PARAM = "nudge_messaging.rate_messages"
RATE_MINUTES_PARAM = "nudge_messaging.rate_minutes"
DEFAULT_MESSAGES = 20
DEFAULT_MINUTES = 10
RETENTION_PARAM = "nudge_messaging.retention_days"
DEFAULT_RETENTION_DAYS = 30


class NudgeMessagingMessage(models.Model):
    """One message that reached a webhook after it was proved genuine: which
    channel, which message (its id there makes a repeat harmless), who sent it
    (a hash, never the number) and what became of it. It is what the limit per
    sender counts. The text is not kept here: the Request in What Nudge Did
    holds what the person asked."""
    _name = "nudge.messaging.message"
    _description = "Nudge Messaging Log"
    _order = "id desc"

    channel = fields.Selection(
        [("whatsapp", "WhatsApp"), ("telegram", "Telegram")], required=True, index=True, readonly=True)
    ext_id = fields.Char(string="Message Id", required=True, readonly=True)
    sender_hash = fields.Char(index=True, readonly=True)
    user_id = fields.Many2one("res.users", readonly=True, ondelete="set null")
    state = fields.Selection(
        [("received", "Received"), ("limited", "Too Many"), ("unknown", "Unknown Sender"),
         ("linked", "Phone Linked"), ("answered", "Answered"), ("failed", "Failed"),
         ("wrong_code", "Wrong Code")],
        default="received", readonly=True, index=True)
    # The message waits here, in the words the channel gave, until the scheduled action answers it. The
    # phone number is part of it, so it is erased as soon as the message is over.
    payload = fields.Text(readonly=True, groups="base.group_system")
    tries = fields.Integer(readonly=True)
    started_at = fields.Datetime(readonly=True, help="When an answer was last begun: one that never ended is begun again.")

    if hasattr(models, "Constraint"):
        _unique_message = models.Constraint("unique(channel, ext_id)", "A message is taken once.")
    else:  # Odoo 17 and 18
        _sql_constraints = [("unique_message", "unique(channel, ext_id)", "A message is taken once.")]

    @api.model
    def _cron_purge(self):
        """Forget the log after the retention. A body captured earlier is refused by its date, not by this table."""
        self._gc_old()

    @api.model
    def _hash_sender(self, channel, sender):
        salt = self.env["ir.config_parameter"].sudo().get_param("database.uuid") or ""
        return hashlib.sha256(f"{salt}:{channel}:{sender}".encode()).hexdigest()[:32]

    @api.model
    def _limits(self):
        """:return: ``(messages, minutes)`` one sender may use"""
        Parameter = self.env["ir.config_parameter"].sudo()
        out = []
        for param, default in ((RATE_MESSAGES_PARAM, DEFAULT_MESSAGES), (RATE_MINUTES_PARAM, DEFAULT_MINUTES)):
            try:
                out.append(max(1, int(Parameter.get_param(param) or default)))
            except ValueError:
                out.append(default)
        return out

    @api.model
    def _retention_days(self):
        try:
            value = int(self.env["ir.config_parameter"].sudo().get_param(RETENTION_PARAM) or 0)
        except ValueError:
            value = 0
        return value if value > 0 else DEFAULT_RETENTION_DAYS

    @api.autovacuum
    def _gc_old(self):
        """The log is kept for the retention (30 days unless the settings say otherwise): the limit per
        sender needs minutes of it, the repeat check of a message needs hours. The codes used or expired
        go with it."""
        old = fields.Datetime.now() - timedelta(days=self._retention_days())
        self.sudo().search([("create_date", "<", old)], limit=10000).unlink()
        self.env["nudge.messaging.code"].sudo().search([
            ("expires_at", "<", fields.Datetime.now() - timedelta(days=1))]).unlink()

    @api.model
    def _reserve(self, channel, ext_id, sender, event=None):
        """Take the place of a message before anything is paid for, with what it says: whatever happens to
        the worker, the message is still here to be answered.

        :return: the line, or an empty recordset when this message was taken already"""
        self = self.sudo()
        try:
            with self.env.cr.savepoint():
                return self.create({
                    "channel": channel, "ext_id": str(ext_id)[:200],
                    "sender_hash": self._hash_sender(channel, sender),
                    "payload": json.dumps(event) if event else False})
        except IntegrityError:
            return self.browse()

    @api.model
    def _count_recent(self, sender_hash):
        messages, minutes = self._limits()
        since = fields.Datetime.now() - timedelta(minutes=minutes)
        count = self.sudo().search_count([("sender_hash", "=", sender_hash), ("create_date", ">=", since)])
        return count, messages

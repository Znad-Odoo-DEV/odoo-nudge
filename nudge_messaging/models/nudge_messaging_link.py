import hmac
import secrets
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError
from odoo.tools.misc import hmac as odoo_hmac

CODE_MINUTES = 10
MAX_BAD_PER_SENDER = 3
SENDER_WINDOW_MINUTES = 10
MAX_BAD_PER_MINUTE = 30
# Wrong codes in an hour, from all the phones together, after which no code is tried
MAX_WRONG_CODES = 60
CHANNELS = [("whatsapp", "WhatsApp"), ("telegram", "Telegram")]


class NudgeMessagingLink(models.Model):
    """One phone (a WhatsApp number or a Telegram chat) tied to one person.

    A person has at most one phone per channel and a phone belongs to one
    person; linking again moves it. A link is revoked (archived), never
    deleted."""
    _name = "nudge.messaging.link"
    _description = "Nudge Linked Phone"
    _order = "id desc"

    active = fields.Boolean(default=True, index=True)
    user_id = fields.Many2one("res.users", string="Person", required=True, index=True, ondelete="cascade")
    channel = fields.Selection(CHANNELS, required=True, index=True)
    sender_id = fields.Char(string="Phone Identifier", required=True, index=True, readonly=True)
    sender_hint = fields.Char(string="Phone", compute="_compute_sender_hint")

    @api.depends("sender_id")
    def _compute_sender_hint(self):
        for link in self:
            sender = link.sender_id or ""
            link.sender_hint = ("..." + sender[-4:]) if len(sender) > 4 else sender

    def action_revoke(self):
        """The person (or a manager) unlinks the phone: it is archived, the history stays"""
        for link in self.sudo():
            if link.user_id != self.env.user and not self.env.user.has_group("nudge.group_nudge_manager"):
                raise AccessError(self.env._("This is not your phone."))
            link.active = False
        return True

    @api.model
    def _link_for(self, channel, sender):
        # sudo: a phone is mapped to its person before anybody is identified; nothing else is read
        return self.sudo().search([("channel", "=", channel), ("sender_id", "=", str(sender)),
                                   ("active", "=", True)], limit=1)


class NudgeMessagingCode(models.Model):
    """The one-time code a person makes in Preferences and sends from the phone.

    Only a hash is kept. It is good for ten minutes and once."""
    _name = "nudge.messaging.code"
    _description = "Nudge Linking Code"

    user_id = fields.Many2one("res.users", required=True, index=True, ondelete="cascade")
    code_hash = fields.Char(required=True, index=True)
    expires_at = fields.Datetime(required=True)
    used = fields.Boolean(default=False)

    @api.autovacuum
    def _gc_old(self):
        """A code is good for ten minutes: a day after, used or not, it is nothing but a row"""
        self.sudo().search([("expires_at", "<", fields.Datetime.now() - timedelta(days=1))]).unlink()

    @api.model
    def _hash(self, code):
        return odoo_hmac(self.env(su=True), "nudge_messaging.code", str(code))[:32]

    @api.model
    def _make(self, user):
        """:return: a new code of six digits for ``user``; earlier ones of theirs are void"""
        self = self.sudo()
        self.search([("user_id", "=", user.id), ("used", "=", False)]).write({"used": True})
        while True:
            code = f"{secrets.randbelow(10 ** 6):06d}"
            taken = self.search_count([("code_hash", "=", self._hash(code)), ("used", "=", False),
                                       ("expires_at", ">", fields.Datetime.now())])
            if not taken:
                break
        self.create({"user_id": user.id, "code_hash": self._hash(code),
                     "expires_at": fields.Datetime.now() + timedelta(minutes=CODE_MINUTES)})
        return code

    @api.model
    def _guard(self, channel, sender):
        """Whether this sender may try a code now. A sender who guessed wrong three times in ten minutes
        is refused; thirty wrong guesses in one minute from the phones together (:meth:`_wrong_codes`)
        lock every outstanding code, so the people who made one must make a new one.

        :return: whether the guess may be tried"""
        Message = self.env["nudge.messaging.message"].sudo()
        now = fields.Datetime.now()
        if self._wrong_codes(now - timedelta(minutes=1)) >= MAX_BAD_PER_MINUTE:
            self.sudo().search([("used", "=", False)]).write({"used": True})
            return False
        mine = Message.search_count([
            ("state", "=", "wrong_code"), ("sender_hash", "=", Message._hash_sender(channel, sender)),
            ("create_date", ">=", now - timedelta(minutes=SENDER_WINDOW_MINUTES))])
        return mine < MAX_BAD_PER_SENDER

    @api.model
    def _redeem(self, code, channel, sender):
        """:return: the user the code belongs to, now linked to ``sender``, or an empty recordset"""
        self = self.sudo()
        wanted = self._hash(code)
        found = self.browse()
        # Every live code is compared, in constant time, so the answer does not say how close the guess was
        for candidate in self.search([("used", "=", False), ("expires_at", ">", fields.Datetime.now())]):
            if hmac.compare_digest(candidate.code_hash or "", wanted):
                found = candidate
        if not found:
            return self.env["res.users"]
        # Taken in one statement: of two phones sending the same code in the same instant, one wins
        self.env.cr.execute(
            "UPDATE nudge_messaging_code SET used = true WHERE id = %s AND used IS NOT TRUE RETURNING id", [found.id])
        if not self.env.cr.fetchone():
            return self.env["res.users"]
        found.invalidate_recordset(["used"])
        user = found.user_id
        Link = self.env["nudge.messaging.link"].sudo()
        # One phone per person and one person per phone, per channel
        Link.search([("channel", "=", channel), ("active", "=", True), "|",
                     ("user_id", "=", user.id), ("sender_id", "=", str(sender))]).write({"active": False})
        Link.create({"user_id": user.id, "channel": channel, "sender_id": str(sender)})
        return user

    @api.model
    def _too_many_wrong(self):
        """:return: whether the wrong codes of the last hour, from any phone, are too many to try another"""
        return self._wrong_codes(fields.Datetime.now() - timedelta(hours=1)) >= MAX_WRONG_CODES

    @api.model
    def _wrong_codes(self, since):
        """:return: the wrong codes sent since ``since`` by the phones together, each phone counted
        ``MAX_BAD_PER_SENDER`` times at most: one phone, or two, cannot lock the codes of everybody"""
        groups = self.env["nudge.messaging.message"].sudo()._read_group(
            [("state", "=", "wrong_code"), ("create_date", ">=", since)], ["sender_hash"], ["__count"])
        return sum(min(count, MAX_BAD_PER_SENDER) for _sender, count in groups)

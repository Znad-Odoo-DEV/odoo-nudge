import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..tools import egress
from ..tools.bus import notify_user

_logger = logging.getLogger(__name__)

ALERT_PARAM = "nudge.egress.alert"


class NudgeEgress(models.AbstractModel):
    """NP-15: the one check every request that leaves the server goes through, and the admin alert for a
    shared screen that held a credential."""
    _name = "nudge.egress"
    _description = "Nudge Outbound Destinations"

    @api.model
    def _purposes(self):
        from odoo import _  # noqa: PLC0415
        return {
            "chat": _("an AI provider"),
            "voice": _("the voice transcription"),
            "picture": _("picture reading or drawing"),
            "connector": _("a connector"),
            "recipes": _("the recipe marketplace"),
            "model": _("the download of the search model"),
            "feed": _("the compliance pack feed"),
        }

    @api.model
    def _entries(self):
        # sudo: the list is the company's setting; everybody whose request leaves the server must obey it
        return egress.parse(self.env.company.sudo().nudge_egress_hosts)

    @api.model
    def is_allowed(self, url):
        return egress.is_allowed(self._entries(), url)

    @api.model
    def check(self, url, purpose="chat"):
        """Refuse, before anything is sent, a host the company did not allow. Does nothing when the list
        is empty.

        :param str url: where the request goes
        :param str purpose: ``chat``, ``voice``, ``picture``, ``connector``, ``recipes``, ``model``, ``feed``
        :raise UserError: naming the host and where to allow it"""
        entries = self._entries()
        if not entries or egress.is_allowed(entries, url):
            return
        host, port = egress.target_of(url)
        raise UserError(_(
            "%(host)s is not on the list of allowed destinations of your company, so nothing was sent to it "
            "(%(purpose)s). An administrator can allow it in Settings, Nudge, Allowed Destinations.",
            host=f"{host}:{port}" if host else (url or "?"),
            purpose=self._purposes().get(purpose, purpose)))

    @api.model
    def check_all(self, urls, purpose="chat"):
        for url in urls:
            if url:
                self.check(url, purpose)

    # ------------------------------------------------------------------ the alert
    @api.model
    def _claim_alert(self, key):
        """:return: whether this call is the first of the day for ``key`` (atomic, so two workers do not both alert)"""
        today = str(fields.Date.context_today(self))
        cr = self.env.cr
        cr.execute("""
            INSERT INTO ir_config_parameter (key, value, create_date, write_date, create_uid, write_uid)
            VALUES (%s, %s, now() at time zone 'UTC', now() at time zone 'UTC', 1, 1)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, write_date = EXCLUDED.write_date
            WHERE ir_config_parameter.value < EXCLUDED.value
            RETURNING 1""", (key, today))
        return bool(cr.fetchone())

    @api.model
    def alert_credential_screen(self, snapshot):
        """Tell the system administrators that someone shared a screen with a password-type field. The value was
        hidden before anything was sent; the alert only says where it happened. At most once a day per screen model
        and company.

        :param dict snapshot: the clean snapshot (see ``llm.snapshot``)"""
        model = str((snapshot or {}).get("model") or "?")[:80]
        company = self.env.company
        if not self._claim_alert(f"{ALERT_PARAM}.{company.id}.{model}"):
            return False
        # sudo: finds the administrators of the company; the notice is Nudge's own
        admins = self.env["res.users"].sudo().search([
            ("share", "=", False), ("company_ids", "in", company.id),
            ("all_group_ids" if "all_group_ids" in self.env["res.users"]._fields else "groups_id", "in",
             self.env.ref("base.group_system").id)])
        subject = _("A screen with a password field was shared with Nudge")
        body = _(
            "Someone shared the screen of %(model)s with Nudge and it held a password or key field. The value was "
            "hidden before anything was sent. If it should not have been shown there, tell the person and review "
            "who may open that screen.", model=model)
        for admin in admins:
            try:
                admin.partner_id.message_notify(partner_ids=[admin.partner_id.id], subject=subject, body=body)
                notify_user(admin, "simple_notification", {"title": subject, "message": body, "type": "warning"})
            except Exception:  # noqa: BLE001 - an alert that cannot be sent must not break the answer
                _logger.info("Nudge: the credential-screen alert could not be sent to %s", admin.id, exc_info=True)
        return True

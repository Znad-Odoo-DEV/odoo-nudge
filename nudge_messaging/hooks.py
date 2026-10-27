import logging

from odoo.addons.nudge.tools import pinned_http

from .models.nudge_messaging_api import PARAM, TELEGRAM_API, TIMEOUT

_logger = logging.getLogger(__name__)


def uninstall_hook(env):
    """Uninstalling the messaging apps takes their keys away: the webhook is removed at Telegram, at
    best (a Telegram out of reach never stops the uninstall), then every ``nudge_messaging.*`` system
    parameter goes, the keys of WhatsApp and Telegram with them."""
    Parameter = env["ir.config_parameter"].sudo()
    token = (Parameter.get_param(PARAM + "telegram.bot_token") or "").strip()
    if token:
        try:
            reply = pinned_http.post(f"{TELEGRAM_API}/bot{token}/deleteWebhook", timeout=TIMEOUT)
            if reply.status_code >= 400:
                _logger.info("Nudge messaging uninstall: Telegram kept the webhook (%s)", reply.status_code)
        except Exception as error:  # noqa: BLE001 - whatever Telegram does, the uninstall goes on
            # The address holds the token: only the kind of failure is kept
            _logger.info("Nudge messaging uninstall: the webhook could not be removed (%s)", type(error).__name__)
    Parameter.search([("key", "=like", PARAM + "%")]).filtered(lambda param: param.key.startswith(PARAM)).unlink()

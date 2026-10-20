"""Telling a person something on screen, the one way that works on every version."""


def notify_user(user, kind, message):
    """Send ``message`` of type ``kind`` (``simple_notification``, ``nudge.run``) to ``user``'s browser.

    Odoo 18 and 19 send to a user; Odoo 17 sends to the partner of the user through the bus."""
    if hasattr(user, "_bus_send"):
        return user._bus_send(kind, message)
    return user.env["bus.bus"]._sendone(user.partner_id, kind, message)

"""What every provider call shares to stay inside its deadline.

With no Odoo import, like the rest of this package. Three things live here:

* the time a connection may take, cut to what is left of the job's deadline;
* a watchdog that closes a stream the provider has gone silent on, so that a
  read stuck between two chunks ends with the deadline and not after it;
* a cool-down remembered by the process for each provider: after a "busy"
  answer the next requests fail at once, with the time to wait, instead of
  hammering a provider that has just asked for peace.
"""
import hashlib
import threading
import time

# A provider that said it is at its limit without saying for how long, and one
# that failed with a server error: left alone this long
DEFAULT_RATE_COOLDOWN = 15.0
DEFAULT_ERROR_COOLDOWN = 5.0
# Whatever a provider asks, the next request is made at the latest after this
MAX_COOLDOWN = 60.0
# A wait is worth it only when this much time is left for the answer after it
MIN_ANSWER_SECONDS = 10.0
# A Retry-After longer than this is not waited for, whatever the deadline
MAX_RETRY_AFTER = 30.0
RETRY_DELAYS = (1.0, 3.0)

_lock = threading.Lock()
_until = {}


def cooldown_key(config):
    """:return: what names one provider account: the same key on the same
    address is one limit, whatever the model"""
    secret = hashlib.sha256((config.api_key or "").encode()).hexdigest()[:12]
    return (config.kind, (config.base_url or "").rstrip("/"), secret)


def cooldown_left(key):
    """:return: the seconds the provider is still to be left alone, 0 when it is not"""
    with _lock:
        left = _until.get(key, 0.0) - time.monotonic()
        if left <= 0:
            _until.pop(key, None)
            return 0.0
        return left


def start_cooldown(key, seconds):
    """Leave the provider alone for ``seconds`` (never shortening a longer cool-down)."""
    seconds = min(max(float(seconds), 0.0), MAX_COOLDOWN)
    with _lock:
        until = time.monotonic() + seconds
        if until > _until.get(key, 0.0):
            _until[key] = until


def reset(key=None):
    """Forget one cool-down, or all of them."""
    with _lock:
        if key is None:
            _until.clear()
        else:
            _until.pop(key, None)


def busy_message(seconds):
    return f"The provider is busy, try again in {max(int(seconds + 0.999), 1)} s."


def connect_timeout(deadline, cap):
    """:return: the time to connect: ``cap``, or what is left of the deadline if less"""
    if deadline is None:
        return cap
    return max(0.1, min(cap, deadline - time.monotonic()))


def fits(delay, deadline):
    """:return: True when waiting ``delay`` still leaves time for an answer"""
    return deadline is None or time.monotonic() + delay + MIN_ANSWER_SECONDS < deadline


def retry_after(headers):
    """:return: the seconds a ``Retry-After`` header asks for, None when absent or not a number"""
    try:
        value = (headers or {}).get("retry-after") or (headers or {}).get("Retry-After")
    except AttributeError:
        return None
    try:
        return max(float(value), 0.0) if value else None
    except (TypeError, ValueError):
        return None


class watch:  # noqa: N801 - used as ``with watch(...)``
    """Call ``on_expire`` when the deadline passes, unless the block ended first.

    Closing a response from the timer ends a read that would otherwise wait for
    the provider's next chunk long after the deadline."""

    def __init__(self, deadline, on_expire):
        self.deadline = deadline
        self.on_expire = on_expire
        self.timer = None

    def _fire(self):
        try:
            self.on_expire()
        except Exception:  # noqa: BLE001 - closing a connection that is already gone
            pass

    def __enter__(self):
        if self.deadline is not None:
            self.timer = threading.Timer(max(self.deadline - time.monotonic(), 0.0), self._fire)
            self.timer.daemon = True
            self.timer.start()
        return self

    def __exit__(self, *args):
        if self.timer is not None:
            self.timer.cancel()
        return False

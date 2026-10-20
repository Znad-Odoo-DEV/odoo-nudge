"""How long a call to a provider may last: never longer than the worker that makes it.

A worker past ``limit_time_real`` (``limit_time_real_cron`` in a scheduled action) is killed
with whatever it was doing. Every wait for a provider, a download or a recording is given
``min(what was configured, what is left of the worker less ten seconds)`` through
:func:`cap` or :func:`until`, so a slow provider ends in an answer "too slow" and not in a dead
request. A request knows when it began (Odoo notes it); a piece of work in the background says
so with :func:`window`."""
import contextlib
import threading
import time

from odoo.tools import config

# What is kept back for writing the answer and closing the request
SAFETY = 10.0
# Never less than this: a call that cannot start is better said than started
FLOOR = 5.0

_local = threading.local()


def worker_limit(cron=False):
    """:return: seconds a worker may last, 0 when it is not limited"""
    if cron:
        limit = config["limit_time_real_cron"]
        if limit is not None and limit >= 0:
            return limit or 0
    return config["limit_time_real"] or 0


@contextlib.contextmanager
def window(cron=True):
    """The work inside begins now and has the time of a scheduled action (or of a request)"""
    before = getattr(_local, "window", None)
    _local.window = (time.monotonic(), worker_limit(cron))
    try:
        yield
    finally:
        _local.window = before


def remaining():
    """:return: seconds left to this worker, or None when nothing limits it"""
    began = getattr(_local, "window", None)
    if began:
        started, limit = began
        elapsed = time.monotonic() - started
    else:
        limit = worker_limit()
        began_at = getattr(threading.current_thread(), "perf_t0", None)
        elapsed = time.time() - began_at if began_at else 0.0
    if not limit or limit <= 0:
        return None
    return limit - elapsed


def cap(seconds):
    """:return: ``seconds`` or, when the worker has less, what it has less ten seconds (at least a few)"""
    left = remaining()
    if left is None or not seconds:
        return seconds
    return max(FLOOR, min(seconds, left - SAFETY))


def until(seconds):
    """:return: the time (``time.monotonic()``) a call that may last ``seconds`` must end by"""
    return time.monotonic() + cap(seconds)

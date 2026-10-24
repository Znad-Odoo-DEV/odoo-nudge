"""NP-15: the allow-list of hosts Nudge may send to. Pure functions, no database.

An entry is a host name (``api.anthropic.com``), a host with a port (``localhost:11434``) or a whole domain
(``*.example.com``, which is the domain and everything under it). Without a port any port of the host is
allowed; with one, only that port. An empty list restricts nothing."""
import re
from urllib.parse import urlparse

DEFAULT_PORTS = {"http": 80, "https": 443}
_SPLIT = re.compile(r"[\s,;]+")


def parse(text):
    """:return: the entries of ``text`` (one per line, commas also split), lower case, without duplicates"""
    seen = []
    for part in _SPLIT.split((text or "").strip().lower()):
        part = part.strip()
        if "//" in part:
            part = urlparse(part).netloc
        part = part.rstrip("/.")
        if part and part not in seen:
            seen.append(part)
    return seen


def target_of(url):
    """:return: ``(host, port)`` of ``url`` in lower case, or ``("", 0)`` when it has no host"""
    parsed = urlparse(url if "//" in (url or "") else f"//{url or ''}")
    try:
        port = parsed.port
    except ValueError:
        port = None
    host = (parsed.hostname or "").lower().rstrip(".")
    scheme = (parsed.scheme or "https").lower()
    return host, port or DEFAULT_PORTS.get(scheme, 443)


def _matches(entry, host, port):
    pattern, _sep, wanted = entry.rpartition(":")
    if not _sep or not wanted.isdigit():  # no port in the entry ("[::1]" ends with a bracket)
        pattern, wanted = entry, ""
    if wanted and int(wanted) != port:
        return False
    if pattern.startswith("*."):
        return host == pattern[2:] or host.endswith(pattern[1:])
    return host == pattern


def is_allowed(entries, url):
    """:return: whether ``url`` may be reached under ``entries`` (everything, when there are none)"""
    if not entries:
        return True
    host, port = target_of(url)
    return bool(host) and any(_matches(entry, host, port) for entry in entries)

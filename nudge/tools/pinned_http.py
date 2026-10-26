"""S11: an outbound request that connects to the address it judged. Plain Python, no database.

Resolving a name to check it and then letting ``requests`` resolve it again leaves a gap: a name with a short
life can answer a public address to the check and 127.0.0.1 or 169.254.169.254 to the connection (DNS
rebinding). Here the name is resolved once, every address it gives is judged, and the socket is opened to those
addresses and no other. The name still goes in the Host header and in TLS (SNI), and the certificate is
verified for the name, not for the address.

One request per call and redirects are never followed: a caller that follows one asks again with the new
address, which is resolved and judged in its turn. Through a proxy set in the environment (``HTTPS_PROXY``),
the proxy opens the connection, as it did before."""
import ipaddress
import socket
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import ConnectTimeoutError, NewConnectionError
from urllib3.util.connection import create_connection

DEFAULT_PORTS = {"http": 80, "https": 443}


class Refused(requests.exceptions.ConnectionError):
    """Nothing was sent. ``code`` says why: ``scheme`` (not an HTTP address), ``network`` (the name has no
    address) or ``private`` (an address of the name is this machine or a private network)."""

    def __init__(self, code, detail=""):
        super().__init__(detail or code)
        self.code = code


def resolve(host, port):
    """:return: the addresses of ``host``, in the order the system prefers them"""
    return [info[4][0] for info in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)]


def is_public(address):
    """:return: whether ``address`` is on the public internet: not this machine, not a private, shared or
    link-local network (the metadata address of the clouds is one), not multicast"""
    try:
        ip = ipaddress.ip_address(str(address).split("%")[0])
    except ValueError:
        return False
    return ip.is_global and not ip.is_multicast


def addresses(host, port, allow_private=False):
    """:return: the addresses of ``host``, every one of them public unless ``allow_private``
    :raise Refused: ``network`` when the name has none, ``private`` when one of them is not public"""
    try:
        found = list(dict.fromkeys(resolve(host, port)))
    except (OSError, UnicodeError):
        raise Refused("network", "the address cannot be found") from None
    if not found:
        raise Refused("network", "the address cannot be found")
    if not allow_private and not all(is_public(address) for address in found):
        raise Refused("private")
    return found


def _connect(connection, found):
    """The socket of ``connection``, opened to the first of the judged addresses that answers"""
    error = None
    for address in found:
        try:
            return create_connection((address, connection.port), connection.timeout,
                                     source_address=connection.source_address,
                                     socket_options=connection.socket_options)
        except OSError as exc:
            error = exc
    if isinstance(error, socket.timeout):
        raise ConnectTimeoutError(connection, f"Connection to {connection.host} timed out.") from error
    raise NewConnectionError(connection, f"Failed to establish a new connection: {error}") from error


def _pool_classes(found):
    """The pools of urllib3 for one request: their connections open their socket to ``found`` only"""

    class PinnedHTTPConnection(HTTPConnection):
        def _new_conn(self):
            return _connect(self, found)

    class PinnedHTTPSConnection(HTTPSConnection):
        def _new_conn(self):
            return _connect(self, found)

    class PinnedHTTPPool(HTTPConnectionPool):
        ConnectionCls = PinnedHTTPConnection

    class PinnedHTTPSPool(HTTPSConnectionPool):
        ConnectionCls = PinnedHTTPSConnection

    return {"http": PinnedHTTPPool, "https": PinnedHTTPSPool}


class PinnedAdapter(HTTPAdapter):
    """A transport of ``requests`` whose connections go to the addresses it was given"""

    def __init__(self, found, **kwargs):
        self._found = list(found)
        super().__init__(**kwargs)

    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = _pool_classes(self._found)


def request(method, url, allow_private=False, **kwargs):
    """:return: the response of one request to ``url``, made as ``requests.request`` makes it, except that
        the socket goes to an address resolved once and judged: every address of the host must be public
        unless ``allow_private``. Redirects are never followed (``allow_redirects`` is ignored).
    :raise Refused: before anything is sent, when the address is not allowed or has no address
    :raise requests.RequestException: as ``requests`` raises it"""
    parsed = urlsplit(url or "")
    scheme = (parsed.scheme or "").lower()
    try:
        port = parsed.port or DEFAULT_PORTS.get(scheme)
    except ValueError:
        port = None
    if scheme not in DEFAULT_PORTS or not parsed.hostname or not port:
        raise Refused("scheme")
    found = addresses(parsed.hostname, port, allow_private)
    kwargs["allow_redirects"] = False
    with requests.Session() as session:
        adapter = PinnedAdapter(found)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session.request(method, url, **kwargs)


def get(url, **kwargs):
    return request("GET", url, **kwargs)


def head(url, **kwargs):
    return request("HEAD", url, **kwargs)


def post(url, **kwargs):
    return request("POST", url, **kwargs)

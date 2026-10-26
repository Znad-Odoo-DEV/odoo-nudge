"""S11: a resolver that rebinds a name, and servers on 127.0.0.1 that say whether anything reached them.

``Rebinding`` stands in for the DNS of a name with a very short life: the first lookup of the name gets one
answer, every later lookup another. Every other name and every address written as an address is resolved as
usual. The other modules of the tests import it (nudge_connect, nudge_builder).
"""
import datetime
import os
import socket
import ssl
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from odoo.tests import common

NAME = "rebind.example.test"
OTHER = "other.example.test"
PUBLIC = "93.184.216.34"


class RebindCase:
    """Mixin of a test case: the requests to the names of these tests are sent. The tests of Odoo block every
    request to a name other than localhost before it is made; these names reach a server of the test on
    127.0.0.1, or nothing."""

    @classmethod
    def _request_handler(cls, s, r, /, **kw):
        if urlsplit(r.url).hostname in (NAME, OTHER):
            return common._super_send(s, r, **kw)
        return super()._request_handler(s, r, **kw)


class Rebinding:
    """``with Rebinding(NAME, first=PUBLIC, then="127.0.0.1") as dns:`` and ``dns.lookups`` counts the
    lookups of the name"""

    def __init__(self, name=NAME, first=PUBLIC, then="127.0.0.1"):
        self.name, self.first, self.then = name, first, then
        self.lookups = 0
        self._real = None

    def __enter__(self):
        self._real = socket.getaddrinfo
        socket.getaddrinfo = self._resolve
        return self

    def __exit__(self, *exc):
        socket.getaddrinfo = self._real

    def _resolve(self, host, port, *args, **kwargs):
        if str(host).lower().rstrip(".") != self.name:
            return self._real(host, port, *args, **kwargs)
        self.lookups += 1
        address = self.first if self.lookups == 1 else self.then
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port or 0))]


class Listener:
    """A TCP port on 127.0.0.1 that accepts and counts connections, and closes them at once"""

    def __init__(self):
        self.connections = 0
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self._sock.settimeout(0.2)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(2)
        self._sock.close()

    def _run(self):
        while not self._stop.is_set():
            try:
                conn, _peer = self._sock.accept()
            except (TimeoutError, socket.timeout, OSError):
                continue
            self.connections += 1
            conn.close()


class Server:
    """An HTTP (or HTTPS, given ``context``) server on 127.0.0.1 that answers 200 with the Host it was sent,
    or a redirect when the path is ``/away``"""

    def __init__(self, context=None):
        hosts = self.hosts = []

        class Handler(BaseHTTPRequestHandler):
            def _answer(self):
                hosts.append(self.headers.get("Host"))
                if self.path == "/away":
                    self.send_response(302)
                    self.send_header("Location", "http://elsewhere.example.test/")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = (self.headers.get("Host") or "").encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            do_GET = do_HEAD = do_POST = _answer

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        if context:
            self._server.socket = context.wrap_socket(self._server.socket, server_side=True)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()


def certificate(name, directory):
    """:return: ``(certfile, keyfile)`` of a self-signed certificate for ``name``, which a client given
    ``verify=certfile`` trusts"""
    from cryptography import x509  # noqa: PLC0415
    from cryptography.hazmat.primitives import hashes, serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric import ec  # noqa: PLC0415
    from cryptography.x509.oid import NameOID  # noqa: PLC0415

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    public = key.public_key()
    cert = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(public)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=False, data_encipherment=False,
            key_agreement=False, key_cert_sign=True, crl_sign=False, encipher_only=False, decipher_only=False),
            critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(public), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(public), critical=False)
        .sign(key, hashes.SHA256()))
    certfile, keyfile = os.path.join(directory, "cert.pem"), os.path.join(directory, "key.pem")
    with open(certfile, "wb") as out:
        out.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(keyfile, "wb") as out:
        out.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()))
    return certfile, keyfile


def tls_context(name):
    """:return: ``(server_context, certfile, directory)``; the caller removes the directory"""
    directory = tempfile.mkdtemp(prefix="nudge-s11-")
    certfile, keyfile = certificate(name, directory)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile, keyfile)
    return context, certfile, directory

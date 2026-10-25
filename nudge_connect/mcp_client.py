"""A client of the Model Context Protocol over Streamable HTTP, in plain Python.

It speaks the revision 2026-07-28: no session and no handshake, every request carries its protocol
version and the client in ``_meta`` and in the headers ``MCP-Protocol-Version``, ``Mcp-Method`` and
``Mcp-Name`` (https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http).
A server of 2025-11-25 or before answers such a request with a 4xx that holds no modern error: the
client then opens a session the old way (``initialize``, ``Mcp-Session-Id``), as the section
Backward Compatibility of that page says, and goes on in it.

Nothing of Odoo here: the caller gives the address, the headers of its credential and the seconds it
has. Nothing is logged: not the headers, not the arguments, not what comes back.
"""
import base64
import ipaddress
import itertools
import json
import re
import socket
import time
from urllib.parse import urlparse

import requests

from odoo.addons.nudge.tools import pinned_http

PROTOCOL = "2026-07-28"
# The revisions of the handshake era this client can go on in, newest first
LEGACY_PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26")
MODERN = "modern"
CLIENT_INFO = {"name": "Nudge", "version": "1.0"}
ACCEPT = "application/json, text/event-stream"
# The errors only a modern server answers with: the request was read as a modern one
HEADER_MISMATCH, MISSING_CAPABILITY, UNSUPPORTED_VERSION, METHOD_NOT_FOUND = -32020, -32021, -32022, -32601
INVALID_REQUEST = -32600
MODERN_ERRORS = frozenset({HEADER_MISMATCH, MISSING_CAPABILITY, UNSUPPORTED_VERSION})
MAX_BYTES = 2 * 1024 * 1024
MAX_TOOLS = 200
MAX_PAGES = 10
LOOPBACK_NAMES = ("localhost",)
# An HTTP field name (RFC 9110 token): what x-mcp-header may hold
TOKEN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
SENTINEL = ("=?base64?", "?=")
# Values of a schema that are data, not schemas: an x-mcp-header inside them is no annotation
NOT_SCHEMAS = frozenset({"default", "examples", "const", "enum"})


class McpError(Exception):
    """A call that was refused or failed.

    :param code: why, in one word: scheme, private, network, timeout, redirect, auth, http, toobig,
        protocol, version, remote, input
    :param detail: what the server or the network said, short; may quote the server
    """

    def __init__(self, code, detail=""):
        self.code = code
        self.detail = str(detail or "")[:300]
        super().__init__(f"{code}: {self.detail}" if self.detail else code)


def _is_loopback(host):
    if host in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_url(url, allow_private=False):
    """:raise McpError: unless ``url`` may be called: HTTPS, or HTTP to this machine only; no
    name and password in it; and, unless ``allow_private`` (the server runs on the company's own
    network), a host whose every address is public. The resolver is asked, so that ``127.1``, a
    number or a name that points inside are seen for what they are."""
    parsed = urlparse(url or "")
    host = parsed.hostname or ""
    if not host or parsed.scheme not in ("https", "http") or parsed.username or parsed.password:
        raise McpError("scheme")
    local = _is_loopback(host)
    if parsed.scheme == "http" and not local:
        raise McpError("scheme")
    if allow_private:
        return
    if local:
        raise McpError("private")
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        raise McpError("network", "the address cannot be found") from None
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0].split("%")[0])
        except ValueError:
            raise McpError("private") from None
        if not address.is_global or address.is_multicast:
            raise McpError("private")


def read_capped(response, cap, seconds):
    """:return: the bytes of a streamed ``response``, at most ``cap`` of them, all within ``seconds``
    :raise McpError: ``toobig`` or ``timeout``"""
    limit = time.monotonic() + seconds
    data = bytearray()
    for block in response.iter_content(1 << 14):
        data += block
        if len(data) > cap:
            raise McpError("toobig")
        if time.monotonic() > limit:
            raise McpError("timeout")
    return bytes(data)


def header_value(value):
    """:return: ``value`` as an HTTP header may carry it: as it is when it is plain visible ASCII,
    else Base64 between the sentinels of the specification"""
    text = ("true" if value else "false") if isinstance(value, bool) else str(value)
    plain = (all(32 <= ord(char) <= 126 or char == "\t" for char in text) and text == text.strip()
             and not (text.startswith(SENTINEL[0]) and text.endswith(SENTINEL[1])))
    if plain:
        return text
    return SENTINEL[0] + base64.b64encode(text.encode()).decode() + SENTINEL[1]


def header_params(schema):
    """:return: ``[[path, name], ...]``, the arguments a tool asks to be repeated in a header
    ``Mcp-Param-<name>`` (``x-mcp-header``)
    :raise ValueError: when an annotation breaks the rules of the specification; the tool is then
        left out, as a client must"""
    found, seen = [], set()

    def walk(node, path, reachable):
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            if key == "x-mcp-header":
                if not reachable or not path:
                    raise ValueError("x-mcp-header outside of the plain properties")
                if not isinstance(value, str) or not TOKEN.match(value):
                    raise ValueError(f"x-mcp-header {value!r} is not a header name")
                if node.get("type") not in ("string", "integer", "boolean"):
                    raise ValueError(f"x-mcp-header {value!r} on a {node.get('type')!r}")
                if value.lower() in seen:
                    raise ValueError(f"x-mcp-header {value!r} twice")
                seen.add(value.lower())
                found.append([list(path), value])
            elif key == "properties" and isinstance(value, dict):
                for name, child in value.items():
                    walk(child, (*path, name), reachable)
            elif key in NOT_SCHEMAS:
                continue
            elif isinstance(value, dict):
                walk(value, path, False)
            elif isinstance(value, list):
                for item in value:
                    walk(item, path, False)

    walk(schema, (), True)
    return found


def result_text(result):
    """:return: ``(text, is_error)`` of a ``tools/call`` result: its text, a link written as text,
    a file or a picture named but left out; the structured content when nothing else is there"""
    parts = []
    for item in result.get("content") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "text":
            parts.append(str(item.get("text") or ""))
        elif kind == "resource_link":
            parts.append(f"[link: {item.get('name') or ''} {item.get('uri') or ''}]".replace(" ]", "]"))
        elif kind == "resource":
            resource = item.get("resource") if isinstance(item.get("resource"), dict) else {}
            if isinstance(resource.get("text"), str):
                parts.append(resource["text"])
            else:
                parts.append(f"[file left out: {resource.get('uri') or ''} {resource.get('mimeType') or ''}]")
        else:
            parts.append(f"[{kind or 'content'} left out]")
    if not any(parts) and result.get("structuredContent") is not None:
        parts.append(json.dumps(result["structuredContent"], ensure_ascii=False, sort_keys=True))
    return "\n".join(part for part in parts if part), bool(result.get("isError"))


class Client:
    """One piece of work with one server: a few requests, all within ``seconds``.

    :param era: what the server was found to speak before: ``MODERN``, a legacy revision, or None
        to find out with the first request
    """

    def __init__(self, url, headers=None, seconds=20, allow_private=False, era=None, max_bytes=MAX_BYTES):
        self.url = url
        self.headers = dict(headers or {})
        self.allow_private = allow_private
        self.era = era
        self.max_bytes = max_bytes
        self.deadline = time.monotonic() + max(1.0, float(seconds))
        self.server_info = {}
        self._ids = itertools.count(1)
        self._session = None
        self._opened = False

    # ------------------------------------------------------------------ what a caller uses
    def discover(self):
        """:return: ``{"era", "server"}``: what the server speaks and how it names itself"""
        if self.era in (None, MODERN):
            outcome = self._modern("server/discover", {})
            if outcome is not None:
                info = outcome.get("serverInfo") or (outcome.get("_meta") or {}).get(
                    "io.modelcontextprotocol/serverInfo") or {}
                self.server_info = info if isinstance(info, dict) else {}
                return {"era": MODERN, "server": self._server_name()}
        if not self._session_open():
            self._initialize()
        return {"era": self.era, "server": self._server_name()}

    def list_tools(self):
        """:return: the tools the server offers, as it describes them (at most ``MAX_TOOLS``)"""
        found, cursor = [], None
        for _page in range(MAX_PAGES):
            params = {"cursor": cursor} if cursor else {}
            result = self.request("tools/list", params)
            tools = result.get("tools")
            if not isinstance(tools, list):
                raise McpError("protocol", "tools/list answered without a list of tools")
            found += [tool for tool in tools if isinstance(tool, dict) and isinstance(tool.get("name"), str)]
            cursor = result.get("nextCursor")
            if not cursor or len(found) >= MAX_TOOLS:
                break
        return found[:MAX_TOOLS]

    def call_tool(self, name, arguments, params=()):
        """:param params: ``[[path, header], ...]`` of :func:`header_params`
        :return: the result of the call, as the server wrote it"""
        extra = {}
        for path, header in params or ():
            value = arguments
            for part in path:
                value = value.get(part) if isinstance(value, dict) else None
            if value is not None and not isinstance(value, (dict, list)):
                extra[f"Mcp-Param-{header}"] = header_value(value)
        return self.request("tools/call", {"name": name, "arguments": arguments}, name=name, extra=extra)

    def request(self, method, params=None, name=None, extra=None):
        """:return: the result of ``method``, in the era the server speaks
        :raise McpError: when it was refused or failed"""
        if self.era in (None, MODERN):
            outcome = self._modern(method, params, name, extra)
            if outcome is not None:
                return outcome
        if not self._session_open():
            self._initialize()
        return self._legacy(method, params, name, extra)

    # ------------------------------------------------------------------ the two eras
    def _modern(self, method, params, name=None, extra=None):
        """:return: the result, or None when the server is one of the handshake era"""
        params = dict(params or {})
        params["_meta"] = dict(params.get("_meta") or {}, **{
            "io.modelcontextprotocol/protocolVersion": PROTOCOL,
            "io.modelcontextprotocol/clientInfo": CLIENT_INFO,
            "io.modelcontextprotocol/clientCapabilities": {},
        })
        headers = {"MCP-Protocol-Version": PROTOCOL, **(extra or {})}
        status, _headers, message = self._post(method, params, name, headers)
        error = (message or {}).get("error") if isinstance(message, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        modern_error = code in MODERN_ERRORS or (status == 404 and code == METHOD_NOT_FOUND)
        # A server already known as modern that refuses with an error of its own is not taken for an old one
        known = self.era == MODERN and isinstance(error, dict)
        # An old server may say "no such method" in a 200 answer with a JSON-RPC error, not in the status
        unknown_method = code in (METHOD_NOT_FOUND, INVALID_REQUEST)
        if (status in (400, 404, 405) or unknown_method) and not modern_error and not known:
            self.era = None
            return None
        self.era = MODERN
        return self._result(status, message)

    def _session_open(self):
        """:return: whether this client opened a session the old way: each piece of work opens its own"""
        return self._opened

    def _initialize(self):
        status, headers, message = self._post("initialize", {
            "protocolVersion": LEGACY_PROTOCOLS[0], "capabilities": {}, "clientInfo": CLIENT_INFO,
        }, None, {})
        result = self._result(status, message)
        version = result.get("protocolVersion")
        if version not in LEGACY_PROTOCOLS:
            raise McpError("version", f"the server speaks {str(version)[:40]}")
        self.era = version
        self._opened = True
        self._session = headers.get("Mcp-Session-Id") or None
        info = result.get("serverInfo")
        self.server_info = info if isinstance(info, dict) else {}
        # Said, not asked: the server answers 202 with nothing
        self._post("notifications/initialized", None, None, self._legacy_headers(), notify=True)

    def _legacy_headers(self, extra=None):
        headers = {"MCP-Protocol-Version": self.era, **(extra or {})}
        if self._session:
            headers["Mcp-Session-Id"] = self._session
        return headers

    def _legacy(self, method, params, name=None, extra=None):
        status, _headers, message = self._post(method, params or {}, name, self._legacy_headers(extra))
        return self._result(status, message)

    def _server_name(self):
        info = self.server_info or {}
        return " ".join(str(info.get(key) or "") for key in ("name", "version")).strip()[:120]

    # ------------------------------------------------------------------ the wire
    def _left(self):
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise McpError("timeout")
        return left

    def _post(self, method, params, name, headers, notify=False):
        """:return: ``(status, headers, message)``: the JSON-RPC answer to this request, or None"""
        check_url(self.url, self.allow_private)
        body = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        message_id = None
        if not notify:
            message_id = next(self._ids)
            body["id"] = message_id
        sent = {
            **self.headers,
            "Content-Type": "application/json",
            "Accept": ACCEPT,
            "Mcp-Method": method,
            **headers,
        }
        if name is not None:
            sent["Mcp-Name"] = header_value(name)
        try:
            # S11: the socket goes to an address pinned_http judged, never to a second answer of the DNS
            with pinned_http.post(self.url, allow_private=self.allow_private, headers=sent,
                                  data=json.dumps(body, ensure_ascii=False).encode(),
                                  timeout=self._left(), stream=True, allow_redirects=False) as response:
                if 300 <= response.status_code < 400:
                    raise McpError("redirect", response.status_code)
                if response.status_code in (401, 403):
                    raise McpError("auth", response.status_code)
                if notify or response.status_code == 202:
                    return response.status_code, response.headers, None
                kind = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if kind == "text/event-stream":
                    message = self._read_stream(response, message_id)
                else:
                    message = self._read_json(response)
                return response.status_code, response.headers, message
        except pinned_http.Refused as refused:
            raise McpError(refused.code) from None
        except requests.Timeout:
            raise McpError("timeout") from None
        except requests.RequestException as error:
            raise McpError("network", type(error).__name__) from None

    def _chunks(self, response):
        """The bytes of the answer, while it is small enough and in time"""
        size = 0
        for block in response.iter_content(1 << 14):
            size += len(block)
            if size > self.max_bytes:
                raise McpError("toobig")
            self._left()
            yield block

    def _read_json(self, response):
        data = b"".join(self._chunks(response))
        if not data.strip():
            return None
        try:
            message = json.loads(data)
        except ValueError:
            if response.status_code >= 400:
                return None
            raise McpError("protocol", "the answer is not JSON") from None
        return message if isinstance(message, dict) else None

    def _read_stream(self, response, message_id):
        """:return: the answer to ``message_id`` among the events of the stream; the notifications
        before it (progress, log lines) are passed over"""
        buffer, data = b"", []
        for block in self._chunks(response):
            buffer += block
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.rstrip(b"\r").decode("utf-8", "replace")
                if line == "":
                    message = self._event(data, message_id)
                    data = []
                    if message is not None:
                        return message
                elif line.startswith("data:"):
                    data.append(line[5:][1:] if line[5:].startswith(" ") else line[5:])
        message = self._event(data, message_id)
        if message is None:
            raise McpError("protocol", "the stream ended without the answer")
        return message

    @staticmethod
    def _event(data, message_id):
        if not data:
            return None
        try:
            message = json.loads("\n".join(data))
        except ValueError:
            return None
        if isinstance(message, dict) and message.get("id") == message_id and (
                "result" in message or "error" in message):
            return message
        return None

    @staticmethod
    def _result(status, message):
        """:return: the result of a JSON-RPC answer
        :raise McpError: for an error, an answer of another shape, or one that asks for more"""
        if not isinstance(message, dict):
            raise McpError("http" if status >= 400 else "protocol", status)
        error = message.get("error")
        if isinstance(error, dict):
            if error.get("code") == UNSUPPORTED_VERSION:
                supported = (error.get("data") or {}).get("supported") if isinstance(error.get("data"), dict) else None
                raise McpError("version", ", ".join(map(str, supported or []))[:120])
            raise McpError("remote", error.get("message") or error.get("code"))
        result = message.get("result")
        if not isinstance(result, dict):
            raise McpError("protocol", "the answer holds no result")
        # Absent in the answers of the servers before 2026-07-28: a result is complete
        if result.get("resultType", "complete") == "input_required":
            raise McpError("input")
        return result

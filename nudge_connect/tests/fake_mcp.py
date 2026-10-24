"""A small MCP server for the tests: Streamable HTTP on 127.0.0.1, in a thread.

It speaks 2026-07-28 (headers and ``_meta`` on every request), or the handshake era of 2025-11-25
when ``legacy`` is set. It keeps every request it got, so that a test can say what was sent, and
when nothing was.
"""
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl

MODERN = "2026-07-28"
LEGACY = "2025-11-25"
SESSION = "session-1"
CLIENT_ID = "nudge-raghad"
CLIENT_SECRET = "client-secret-ebrahim-42"
ACCESS_TOKEN = "access-token-raghad-9d1"

TOOLS = [
    {
        "name": "search_contacts",
        "title": "Search contacts",
        "description": "Find the contacts of the sister company by name.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Words of the name"}},
            "required": ["query"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "post_message",
        "description": "Post a message in a channel.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "x-mcp-header": "Channel"},
                "text": {"type": "string", "pattern": "^(?!\\s*$).+"},
                "api_token": {"type": "string"},
            },
            "required": ["channel", "text"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        # A header on a number breaks the rules: a client leaves the tool out
        "name": "bad_header",
        "description": "Broken.",
        "inputSchema": {"type": "object", "properties": {"amount": {"type": "number", "x-mcp-header": "Amount"}}},
    },
    {
        "name": "Get.Weather",
        "description": "Weather of a city.",
        "inputSchema": {"type": "object", "properties": {"city": {"type": "string"}}},
    },
]


def text_result(text, is_error=False):
    return {"content": [{"type": "text", "text": text}], "isError": is_error, "resultType": "complete"}


class FakeMcpServer:

    def __init__(self):
        self.reset()
        self._server = None

    def reset(self):
        self.tools = json.loads(json.dumps(TOOLS))
        self.answers = {}
        self.legacy = False
        self.sse = False
        self.token = None
        self.requests = []

    @property
    def url(self):
        return f"http://127.0.0.1:{self._server.server_address[1]}/mcp"

    def start(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                headers = {key.lower(): value for key, value in self.headers.items()}
                if self.path == "/token":
                    return fake.grant(self, raw.decode(), headers)
                body = json.loads(raw or b"{}")
                fake.requests.append({"method": body.get("method"), "headers": headers, "body": body})
                if fake.token and headers.get("authorization") != f"Bearer {fake.token}":
                    return self.reply(401, None)
                fake.handle(self, body, headers)

            def reply(self, status, message, extra=None):
                self.send_response(status)
                for key, value in (extra or {}).items():
                    self.send_header(key, value)
                if message is None:
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if fake.sse and status == 200:
                    progress = {"jsonrpc": "2.0", "method": "notifications/progress",
                                "params": {"progressToken": 1, "progress": 1}}
                    data = (": keep-alive\n\n"
                            f"event: message\ndata: {json.dumps(progress)}\n\n"
                            f"event: message\ndata: {json.dumps(message)}\n\n").encode()
                    self.send_header("Content-Type", "text/event-stream")
                else:
                    data = json.dumps(message).encode()
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()

    def grant(self, handler, form, headers):
        """The token address of OAuth client credentials: a token for the right client, for this server"""
        values = dict(parse_qsl(form))
        expected = "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        self.requests.append({"method": "token", "headers": headers, "body": values})
        if headers.get("authorization") != expected or values.get("grant_type") != "client_credentials":
            return handler.reply(401, None)
        self.token = ACCESS_TOKEN
        return handler.reply(200, {"access_token": ACCESS_TOKEN, "token_type": "Bearer", "expires_in": 3600})

    def calls(self, method="tools/call"):
        return [request for request in self.requests if request["method"] == method]

    # ------------------------------------------------------------------
    def handle(self, handler, body, headers):
        method, message_id = body.get("method"), body.get("id")
        params = body.get("params") or {}
        if self.legacy:
            if method == "initialize":
                return handler.reply(200, {"jsonrpc": "2.0", "id": message_id, "result": {
                    "protocolVersion": LEGACY, "capabilities": {"tools": {}},
                    "serverInfo": {"name": "Fake legacy", "version": "1"}}}, {"Mcp-Session-Id": SESSION})
            if method == "notifications/initialized":
                return handler.reply(202, None)
            if headers.get("mcp-session-id") != SESSION:
                return handler.reply(400, {"jsonrpc": "2.0", "id": None, "error": {
                    "code": -32000, "message": "Bad Request: No valid session ID provided"}})
        else:
            meta = params.get("_meta") or {}
            if headers.get("mcp-protocol-version") != MODERN or meta.get(
                    "io.modelcontextprotocol/protocolVersion") != MODERN or headers.get("mcp-method") != method:
                return handler.reply(400, {"jsonrpc": "2.0", "id": message_id, "error": {
                    "code": -32020, "message": "Header mismatch"}})
        if method == "server/discover":
            result = {"supportedVersions": [MODERN], "capabilities": {"tools": {}},
                      "serverInfo": {"name": "Fake MCP", "version": "2"}, "resultType": "complete"}
        elif method == "tools/list":
            result = {"tools": self.tools, "resultType": "complete", "ttlMs": 0, "cacheScope": "private"}
        elif method == "tools/call":
            name = params.get("name")
            if headers.get("mcp-name") != name:
                return handler.reply(400, {"jsonrpc": "2.0", "id": message_id, "error": {
                    "code": -32020, "message": "Header mismatch"}})
            answer = self.answers.get(name, text_result(f"{name} done"))
            result = answer(params.get("arguments") or {}, headers) if callable(answer) else answer
        else:
            return handler.reply(404, {"jsonrpc": "2.0", "id": message_id, "error": {
                "code": -32601, "message": "Method not found"}})
        return handler.reply(200, {"jsonrpc": "2.0", "id": message_id, "result": result})

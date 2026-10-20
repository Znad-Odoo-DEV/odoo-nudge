"""The Model Context Protocol, spoken as plain JSON-RPC over HTTP.

The controller authenticates the key and hands each message to
:meth:`NudgeMcp._dispatch`, which answers it as the person who holds the key:
with their rights, their record rules and their companies, never in sudo for
data. Only the log (``nudge.run`` and ``nudge.step``) is written in sudo, as
Nudge itself does: nobody else can write it.
"""
import base64
import json
import logging
import re
import time
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, LockError, UserError
from odoo.addons.base.models.res_users import INDEX_SIZE
from odoo.tools.misc import hmac as odoo_hmac

from odoo.addons.nudge.tools import base as tool_base
from odoo.addons.nudge.tools.bus import notify_user

_logger = logging.getLogger(__name__)

SCOPE = "nudge_mcp"
ENABLED_PARAM = "nudge_mcp.enabled"
EXPOSURE_PARAM = "nudge_mcp.exposure"
GROUP = "nudge_mcp.group_nudge_mcp"
AGENT_GROUP = "nudge.group_nudge_agent"
CONVERSATION_TITLE = "MCP"

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

# What an assistant may use: closed lists, the tools for developers never
READ_TOOLS = (
    "search_odoo_documentation", "search_company_files", "describe_model", "locate_data",
    "search_database_profile", "find_screen", "show_menu_path", "search_records", "read_record",
    "find_record", "aggregate_records", "search_products", "find_products",
)
WRITE_TOOLS = ("prepare_create", "prepare_write", "call_method")
CONFIRM_TOOL = "confirm"
DECLINE_TOOL = "decline"

# A token is good for this long
TOKEN_MINUTES = 30
MAX_DOC_CHARS = 60000
PAGE_SIZE = 100

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, SERVER_ERROR = (
    -32700, -32600, -32601, -32602, -32000)

# Most calls a person's assistants may make in a minute, and the longest preview of a result kept
CALLS_PER_MINUTE = 60
CALLS_PARAM = "nudge_mcp.calls_per_minute"
LOCAL_PARAM = "nudge_mcp.assistant_is_local"
RESULT_PREVIEW = 500


class BadParams(UserError):
    """The parameters of a message are wrong: told as a protocol error"""


def error_response(message_id, code, text):
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": text}}


def clean_client(name):
    """:return: the name of the assistant, safe to write in the log"""
    name = re.sub(r"[^\w .@/+:()-]", "", str(name or ""))
    return " ".join(name.split())[:60] or "unknown"


class NudgeMcp(models.AbstractModel):
    _name = "nudge.mcp"
    _description = "Nudge MCP Server"

    # ------------------------------------------------------------------
    # What the administrator allowed
    # ------------------------------------------------------------------
    @api.model
    def _is_enabled(self):
        return self.env["ir.config_parameter"].sudo().get_param(ENABLED_PARAM) in ("True", "true", "1")

    @api.model
    def _exposure(self):
        """:return: "write" or "read", what the assistants may use"""
        value = self.env["ir.config_parameter"].sudo().get_param(EXPOSURE_PARAM)
        return "write" if value == "write" else "read"

    @api.model
    def _writes_open(self):
        """:return: whether this person's assistants may prepare changes: the
        administrator allows it, the company allows it, Nudge may act, and the
        person is an Agent User"""
        env = self.env
        return (
            self._exposure() == "write"
            and bool(env.company.sudo().nudge_mcp_allow_writes)
            and env["nudge.conversation"]._is_agent_allowed()
            and env.user.has_group(AGENT_GROUP))

    @api.model
    def _self_confirm_open(self):
        """:return: whether the company let assistants confirm their own changes
        (unsafe): never for a sensitive one"""
        return bool(self.env.company.sudo().nudge_mcp_self_confirm)

    @api.model
    def _server_url(self):
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url") or ""
        return base_url.rstrip("/") + "/nudge/mcp"

    # ------------------------------------------------------------------
    # Who is asking
    # ------------------------------------------------------------------
    @api.model
    def _authenticate(self, key):
        """:param str key: an API key of the scope ``nudge_mcp``
        :return: the user it belongs to, or an empty recordset when the key is
            unknown, expired, or its owner may no longer hold one"""
        users = self.env["res.users"]
        if not key or len(key) > 256:
            return users
        try:
            uid = self.env["res.users.apikeys"]._check_credentials(scope=SCOPE, key=key)
        except (AssertionError, ValueError):
            return users
        if uid:
            # Odoo also lets a key without a scope through: here only the keys made for assistants
            self.env.cr.execute(
                "SELECT 1 FROM res_users_apikeys WHERE index = %s AND scope = %s AND user_id = %s",
                [key[:INDEX_SIZE], SCOPE, uid])
            uid = uid if self.env.cr.rowcount else None
        user = users.sudo().browse(uid).exists() if uid else users
        if not user or not user.active or not user.has_group(GROUP):
            return users
        return users.browse(user.id)

    def _session_token(self, uid, client):
        payload = base64.urlsafe_b64encode(
            json.dumps({"u": uid, "c": client}).encode()).decode().rstrip("=")
        return f"{payload}.{odoo_hmac(self.env(su=True), 'nudge_mcp.session', payload)[:32]}"

    @api.model
    def _session_client(self, uid, token):
        """:return: the name of the assistant a session was opened for, or
        None when the token is not one of ours, or is another person's"""
        try:
            payload, signature = (token or "").rsplit(".", 1)
            if signature != odoo_hmac(self.env(su=True), "nudge_mcp.session", payload)[:32]:
                return None
            data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        except (ValueError, TypeError):
            return None
        return clean_client(data.get("c")) if data.get("u") == uid else None

    # ------------------------------------------------------------------
    # The messages
    # ------------------------------------------------------------------
    def _dispatch(self, message, client="unknown"):
        """:param message: one JSON-RPC message, decoded
        :param client: name of the assistant, from its session
        :return: ``(response, headers)``; the response is None for what needs none"""
        env = self.env
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return error_response(None, INVALID_REQUEST, env._("This is not a JSON-RPC 2.0 message.")), {}
        message_id = message.get("id")
        method = message.get("method")
        if method is None and ("result" in message or "error" in message):
            # An answer of the client to us: nothing to do
            return None, {}
        if not isinstance(method, str):
            return error_response(message_id, INVALID_REQUEST, env._("The method is missing.")), {}
        if "id" not in message:
            # A notification: heard, never answered
            return None, {}
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return error_response(
                message_id, INVALID_PARAMS, env._("The parameters must be an object.")), {}
        handler = {
            "initialize": self._rpc_initialize,
            "ping": lambda params, client: ({}, {}),
            "tools/list": self._rpc_tools_list,
            "tools/call": self._rpc_tools_call,
            "resources/list": self._rpc_resources_list,
            "resources/templates/list": lambda params, client: ({"resourceTemplates": []}, {}),
            "resources/read": self._rpc_resources_read,
        }.get(method)
        if handler is None:
            return error_response(message_id, METHOD_NOT_FOUND, env._("Nothing answers to %s.", method)), {}
        try:
            result, headers = handler(params, client)
        except BadParams as error:
            return error_response(message_id, INVALID_PARAMS, str(error)), {}
        except (UserError, AccessError) as error:
            return error_response(message_id, SERVER_ERROR, str(error)), {}
        return {"jsonrpc": "2.0", "id": message_id, "result": result}, headers

    def _rpc_initialize(self, params, client):
        info = params.get("clientInfo") if isinstance(params.get("clientInfo"), dict) else {}
        client = clean_client(info.get("name"))
        wanted = params.get("protocolVersion")
        result = {
            "protocolVersion": wanted if wanted in PROTOCOLS else PROTOCOLS[0],
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
            },
            "serverInfo": {"name": "Nudge", "title": "Nudge for Odoo", "version": self._version()},
            "instructions": self.env._(
                "Nudge works inside Odoo as the person whose key you hold: it sees only what they "
                "may see. Look up records and documentation freely. A change is never made at once: "
                "the prepare tools return a token and what will change, and the person is asked in "
                "Odoo. Tell them it waits for them there; call confirm with the token only after "
                "they have said yes in Odoo. Confirm refuses a change they have not approved."),
        }
        return result, {"Mcp-Session-Id": self._session_token(self.env.uid, client)}

    @api.model
    def _version(self):
        module = self.env["ir.module.module"].sudo().search([("name", "=", "nudge_mcp")], limit=1)
        return module.installed_version or module.latest_version or ""

    # --- tools --------------------------------------------------------
    def _visible_tools(self):
        """:return: ``{name: tool}`` of Nudge this person's assistants may call now"""
        names = list(READ_TOOLS)
        if self._writes_open():
            names += WRITE_TOOLS
        found = {}
        for name in names:
            tool = tool_base.TOOLS.get(name)
            if tool is not None and tool.is_available(self.env, tool_base.MODE_AGENT):
                found[name] = tool
        return found

    def _local_tools(self):
        """:return: the two tools of this module, which settle a prepared change"""
        if not self._writes_open():
            return {}
        return {
            CONFIRM_TOOL: self.env._(
                "Make the change a prepare tool described, after the person approved it in Odoo. "
                "Give the token the prepare tool returned. It is refused until they have approved; "
                "nothing is changed before this."),
            DECLINE_TOOL: self.env._(
                "Drop a change the person did not agree to. Give the token the prepare tool returned."),
        }

    def _rpc_tools_list(self, params, client):
        listed = []
        for name, tool in sorted(self._visible_tools().items()):
            writes = isinstance(tool, tool_base.Action)
            description = tool.description
            if writes:
                description += " " + self.env._(
                    "Nothing is changed by this call: it returns a token and what will change, and asks "
                    "the person in Odoo. Once they have approved it there, call confirm with the token.")
            listed.append({
                "name": name,
                "description": description,
                "inputSchema": tool.input_schema,
                "annotations": {
                    "readOnlyHint": not writes, "destructiveHint": False, "openWorldHint": False},
            })
        for name, description in sorted(self._local_tools().items()):
            listed.append({
                "name": name,
                "description": description,
                "inputSchema": {
                    "type": "object", "required": ["token"], "additionalProperties": False,
                    "properties": {"token": {"type": "string"}}},
                "annotations": {
                    "readOnlyHint": False, "destructiveHint": name == CONFIRM_TOOL,
                    "openWorldHint": False},
            })
        return {"tools": listed}, {}

    def _rpc_tools_call(self, params, client):
        name = params.get("name")
        arguments = params.get("arguments") if params.get("arguments") is not None else {}
        if not isinstance(name, str) or not isinstance(arguments, dict):
            raise BadParams(self.env._("A tool call needs a name and its arguments as an object."))
        visible, local = self._visible_tools(), self._local_tools()
        if name not in visible and name not in local:
            raise BadParams(self.env._("There is no tool named %s.", name))
        started = time.monotonic()
        self._check_private()
        self._check_rate()
        run = self._open_run(client, name)
        try:
            self._check_capacity()
            self.env["nudge.usage"]._check_budget()
            if name in local:
                text, is_error = self._settle(run, name, arguments, started)
            else:
                text, is_error = self._run_tool(run, visible[name], arguments, started)
        except (UserError, AccessError) as error:
            text, is_error = str(error), True
            if run.state == "running":
                self._log_step(run, name, "read", "failed", arguments, text, started)
                run._end("failed")
        return {"content": [{"type": "text", "text": text}], "isError": is_error}, {}

    def _check_private(self):
        """Private mode promises that nothing leaves the server: an assistant
        on the internet is outside it, unless the administrator says it is not."""
        if self.env["nudge.provider"]._is_private_mode() and not self._assistant_is_local():
            raise UserError(self.env._(
                "Private mode is on: what Nudge answers would leave your server for the assistant. "
                "An administrator can say that the assistant runs on your own server."))

    @api.model
    def _assistant_is_local(self):
        return self.env["ir.config_parameter"].sudo().get_param(LOCAL_PARAM) in ("True", "true", "1")

    def _check_rate(self):
        """At most so many calls a minute for the keys of one person: counted
        in the log, which every worker shares"""
        raw = self.env["ir.config_parameter"].sudo().get_param(CALLS_PARAM)
        limit = int(raw) if (raw or "").isdigit() and int(raw) > 0 else CALLS_PER_MINUTE
        recent = self.env["nudge.run"].sudo().search_count([
            ("user_id", "=", self.env.uid), ("origin", "=like", "MCP: %"),
            ("create_date", ">=", fields.Datetime.now() - timedelta(minutes=1))])
        if recent >= limit:
            raise UserError(self.env._("Too many calls in a minute. Wait a moment."))

    def _check_capacity(self):
        """The answers of Nudge and the calls of the assistants hold the same
        workers of the server: the limit of Nudge applies to them together.
        A call in progress is a mark on the log's conversation, as a stream is
        on its own: every worker reads the same table, and the mark expires."""
        Conversation = self.env["nudge.conversation"].sudo().with_context(active_test=False)
        Provider = self.env["nudge.provider"].sudo()
        longest = max(Conversation._get_stream_seconds(provider)
                      for provider in (Provider._get_default(), Provider._get_for_mode("dev")))
        busy = Conversation.search_count([
            ("stream_started_at", ">=", fields.Datetime.now() - timedelta(seconds=longest))])
        if busy >= Conversation._get_max_streams():
            raise UserError(self.env._("Nudge is answering other people right now. Try again in a moment."))

    def _mark_call(self, active):
        """Mark (or clear) the call in the table, committed at once on a cursor of its own
        so that the other workers see it before this one has finished"""
        conversation = self._conversation()
        with self.env.registry.cursor() as cr:
            cr.execute(
                "UPDATE nudge_conversation SET stream_started_at = %s WHERE id = %s",
                [fields.Datetime.now() if active else None, conversation.id])
        conversation.invalidate_recordset(["stream_started_at"])

    def _run_tool(self, run, tool, arguments, started):
        env = self.env
        if getattr(tool, "uses_provider", False) and env["nudge.provider"]._is_private_mode():
            raise UserError(env._(
                "Private mode is on: this tool needs an AI provider, and nothing is sent to one "
                "that does not run on your own server."))
        env = env(context=dict(
            env.context, nudge_conversation_id=run.conversation_id.id,
            nudge_mode=tool_base.MODE_AGENT, nudge_mcp=True))
        self._mark_call(True)
        try:
            result = tool_base.call(env, tool.name, arguments, None, tool_base.MODE_AGENT)
        finally:
            self._mark_call(False)
        if result.pending and not result.is_error:
            step = self._log_step(run, tool.name, tool.kind, "pending", arguments, result, started)
            run.state = "waiting"
            lines = tool_base.as_data("preview", "\n".join(step._preview_lines()) or step.summary)
            self._ask_person(step, run)
            if self._may_confirm_alone(step):
                return env._(
                    "Nothing has been changed yet. This is what would change:\n%(lines)s\n\n"
                    "Show it to the person. If they agree, call confirm with the token %(token)s ; "
                    "if they do not, call decline with it. The token is good for %(minutes)s minutes.",
                    lines=lines, token=self._token(step), minutes=TOKEN_MINUTES), False
            return env._(
                "Nothing has been changed yet. This is what would change:\n%(lines)s\n\n"
                "The person has been asked in Odoo (Nudge, Assistant Approvals). Tell them it waits "
                "for their yes there. Once they have approved it, call confirm with the token "
                "%(token)s ; confirm is refused until then. If they do not agree, call decline with "
                "it. The token is good for %(minutes)s minutes.",
                lines=lines, token=self._token(step), minutes=TOKEN_MINUTES), False
        state = "failed" if result.is_error else "done"
        self._log_step(run, tool.name, tool.kind, state, arguments, result, started)
        run._end(state)
        content = tool_base.as_data(tool.name, result.content, result.is_error)
        return content, result.is_error

    def _may_confirm_alone(self, step):
        """:return: whether the assistant may confirm this step without the
        person's yes in Odoo: only when the company chose so, never for a sensitive one"""
        return self._self_confirm_open() and not step.is_sensitive

    def _ask_person(self, step, run):
        """Tell the person, wherever they are in Odoo, that an assistant waits for their yes"""
        title = self.env._("An external assistant wants to make a change")
        message = self.env._(
            "%(client)s: %(summary)s. Say yes or no in Nudge, Assistant Approvals.",
            client=(run.origin or "").removeprefix("MCP: ") or "MCP", summary=step.summary or step.tool)
        notify_user(self.env.user.sudo(), "simple_notification", {
            "title": title, "message": message, "sticky": True, "type": "warning"})

    # --- the log ------------------------------------------------------
    def _conversation(self):
        Conversation = self.env["nudge.conversation"].sudo().with_context(active_test=False)
        conversation = Conversation.search(
            [("user_id", "=", self.env.uid), ("name", "=", CONVERSATION_TITLE)], limit=1)
        # Archived: it holds the log, and nobody chats in it
        return conversation or Conversation.create({
            "user_id": self.env.uid, "name": CONVERSATION_TITLE, "active": False})

    def _open_run(self, client, name):
        """:return: the run of one call, in sudo: the log is written by Nudge only"""
        return self.env["nudge.run"].sudo().create({
            "name": f"MCP: {client} · {name}",
            "conversation_id": self._conversation().id,
            "user_id": self.env.uid,
            "company_id": self.env.company.id,
            "state": "running",
            "origin": f"MCP: {client}",
        })

    def _log_step(self, run, name, kind, state, arguments, result, started):
        """:param result: a ToolResult, or the text of what went wrong
        :return: the step, in sudo"""
        if not isinstance(result, tool_base.ToolResult):
            result = tool_base.ToolResult(result, is_error=state == "failed")
        pending = result.pending or {}
        model, ids, names = result.records or (False, [], [])
        tool = tool_base.TOOLS.get(name)
        return self.env["nudge.step"].sudo().create({
            "run_id": run.id,
            "tool": name,
            "kind": kind,
            "state": state,
            "arguments": arguments or {},
            "summary": pending.get("summary") or (tool and tool.summary and self.env._(tool.summary)) or name,
            "res_model": pending.get("res_model") or model,
            "res_ids": pending.get("res_ids") or list(ids)[:50],
            "record_names": pending.get("record_names") or ", ".join(map(str, names[:10]))[:500],
            "values": pending.get("values") or False,
            "method": pending.get("method") or False,
            "preview": pending.get("preview") or False,
            "is_sensitive": bool(pending.get("is_sensitive")),
            "result": False if state == "pending" else self._preview(result.content),
            "error": result.content if state == "failed" else False,
            "duration_ms": int((time.monotonic() - started) * 1000),
        })

    @staticmethod
    def _preview(content):
        """What the log keeps of a result: a short start, as for any tool, never the rows"""
        content = content if isinstance(content, str) else str(content or "")
        return content[:RESULT_PREVIEW]

    # --- the token ----------------------------------------------------
    def _token(self, step):
        signature = odoo_hmac(self.env(su=True), "nudge_mcp.token", f"{step.id}:{self.env.uid}")
        return f"{step.id}.{signature[:24]}"

    def _step_of(self, token):
        """:return: the step a token opens for this person, in sudo, or nothing"""
        Step = self.env["nudge.step"].sudo()
        try:
            step_id, signature = str(token).split(".", 1)
            step_id = int(step_id)
        except ValueError:
            return Step
        expected = odoo_hmac(self.env(su=True), "nudge_mcp.token", f"{step_id}:{self.env.uid}")[:24]
        if signature != expected:
            return Step
        return Step.browse(step_id).exists()

    def _settle(self, run, name, arguments, started):
        """Make the change a person agreed to, or drop it. The log of the
        change stays on the run that prepared it; this call has a run too."""
        env = self.env

        def finish(state, text):
            self._log_step(run, name, "read", state, {k: v for k, v in arguments.items() if k != "token"},
                           text, started)
            run._end(state)
            return text, state == "failed"

        gone = env._("This token is not valid, or it was used already. Nothing was changed.")
        step = self._step_of(arguments.get("token"))
        if not step or step.state not in ("pending", "approved") or step.user_id.id != env.uid:
            return finish("failed", gone)
        prepared = step.run_id
        if step.create_date < fields.Datetime.now() - timedelta(minutes=TOKEN_MINUTES):
            step.write({"state": "cancelled", "decided_at": fields.Datetime.now()})
            prepared._end("stopped")
            return finish("failed", env._("This token has expired. Nothing was changed. Prepare the change again."))
        if name == DECLINE_TOOL:
            step.write({"state": "declined", "decided_at": fields.Datetime.now(),
                        "result": tool_base.NOTHING_MADE})
            prepared._end("stopped")
            return finish("done", env._("Dropped. Nothing was changed."))
        tool = tool_base.TOOLS.get(step.tool)
        if not isinstance(tool, tool_base.Action) or step.tool not in self._visible_tools():
            return finish("failed", env._("This can no longer be done. Nothing was changed."))
        if step.state == "pending" and not self._may_confirm_alone(step):
            # The assistant cannot say yes for the person: only they can, in Odoo
            return finish("failed", env._(
                "The person has not approved this yet. They are asked in Odoo (Nudge, Assistant "
                "Approvals). Nothing was changed. Wait for their yes, then confirm again."))
        try:
            step.lock_for_update()
        except LockError:
            return finish("failed", env._("This is being done already."))
        step.invalidate_recordset(["state"])
        if step.state not in ("pending", "approved"):
            return finish("failed", gone)
        user_env = env(context=dict(
            env.context, nudge_conversation_id=prepared.conversation_id.id,
            nudge_mode=tool_base.MODE_AGENT, nudge_mcp=True))
        # In the environment of the person, never in sudo; only the step is the log's
        result = tool_base.guarded(user_env, step.tool, lambda: tool.execute(user_env, step))
        values = {
            "state": "failed" if result.is_error else "done",
            "result": self._preview(result.content),
            "error": result.content if result.is_error else False,
            "decided_at": fields.Datetime.now(),
        }
        if result.records:
            values["res_ids"] = list(result.records[1])[:50]
        if not result.is_error and result.card_lines is not None:
            values["preview"] = dict(step.preview or {}, lines=result.card_lines)
        step.write(values)
        prepared._end("failed" if result.is_error else "done")
        if result.is_error:
            return finish("failed", f"{tool_base.as_data(step.tool, result.content, True)} {tool_base.NOTHING_MADE}")
        return finish("done", env._("Done. %s", tool_base.as_data(step.tool, result.content)))

    # --- resources ----------------------------------------------------
    def _rpc_resources_list(self, params, client):
        env = self.env
        try:
            offset = max(int(params.get("cursor") or 0), 0)
        except (TypeError, ValueError):
            raise BadParams(env._("The cursor is not one of ours.")) from None
        files = env["nudge.source"]._for_person() if env["nudge.source"].has_access("read") \
            else env["nudge.source"]
        entries = [{
            "uri": f"nudge://file/{source.id}", "name": source.name, "mimeType": "text/plain",
            "description": source.purpose or "",
        } for source in files][offset:offset + PAGE_SIZE]
        Page = env["nudge.doc.page"]
        room = PAGE_SIZE - len(entries)
        doc_offset = max(offset - len(files), 0)
        pages = Page.search([], order="app, title, id", limit=room, offset=doc_offset) if room else Page
        entries += [{
            "uri": f"nudge://doc/{page.id}", "name": page.title, "mimeType": "text/plain",
            "description": page.description or "",
        } for page in pages]
        result = {"resources": entries}
        if len(entries) == PAGE_SIZE and doc_offset + len(pages) < Page.search_count([]):
            result["nextCursor"] = str(offset + len(entries))
        return result, {}

    def _rpc_resources_read(self, params, client):
        env = self.env
        self._check_private()
        uri = params.get("uri")
        match = re.fullmatch(r"nudge://(doc|file)/(\d+)", uri) if isinstance(uri, str) else None
        if not match:
            raise BadParams(env._("There is no resource %s.", uri))
        kind, record_id = match.group(1), int(match.group(2))
        if kind == "file":
            record = env["nudge.source"]._for_person().filtered(lambda source: source.id == record_id)
        else:
            record = env["nudge.doc.page"].browse(record_id).exists()
        if not record:
            raise BadParams(env._("There is no resource %s.", uri))
        started = time.monotonic()
        run = self._open_run(client, "resources/read")
        passages = record.passage_ids.sorted(lambda passage: (passage.sequence, passage.id))
        if kind == "file":
            text = tool_base.as_data("search_company_files", "\n\n".join(p.body for p in passages)[:MAX_DOC_CHARS])
        else:
            text = "\n\n".join(
                (f"## {p.heading}\n" if p.heading else "") + p.body for p in passages)[:MAX_DOC_CHARS]
        self._log_step(run, "resources/read", "read", "done", {"uri": uri}, uri, started)
        run._end("done")
        return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": text}]}, {}

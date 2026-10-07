import json
import logging

from werkzeug.exceptions import Forbidden

from odoo import _, api, http
from odoo.exceptions import AccessError, UserError
from odoo.http import Response, request
from odoo.modules.registry import Registry

from ..llm import loop

_logger = logging.getLogger(__name__)

STREAM_HEADERS = [
    ("Content-Type", "application/x-ndjson; charset=utf-8"),
    ("Cache-Control", "no-cache, no-transform"),
    # Ask reverse proxies not to buffer the answer
    ("X-Accel-Buffering", "no"),
]


def _line(payload):
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode()


class NudgeChat(http.Controller):

    @http.route("/nudge/chat/stream", type="http", auth="user", methods=["POST"])
    def chat_stream(self, message=None, conversation_id=None, snapshot=None, spoken=None,
                    read_aloud=None, mode=None, background=None, explain=None, file=None,
                    report=None, **kwargs):
        """Answer one message as a stream of JSON lines.

        :param snapshot: JSON description of the screen, when the user shares it
        :param spoken: "1" when the message was said rather than typed
        :param read_aloud: "1" when the answer will be read to the user
        :param mode: "agent" when Nudge is asked to do the work
        :param background: "1" when the work is to be done while the user
            does something else
        :param explain: "screen" or "text", how the user wants the answer
            this time; anything else leaves it to the model
        :param file: a file sent with the question (PDF, text, image), read
            on the server and given to the model as data
        :param report: "sheet" or "chat", how the user wants a comparison
            or a report; anything else leaves it to the model

        Events: ``start``, then ``delta`` and what tools do on the screen
        (``guidance``, ``navigate``, ``write``, ``open``, ``confirm``) in any
        number, then ``done``, ``waiting``, ``queued`` or ``error``.
        """
        env = request.env
        if not env.user.has_group("nudge.group_nudge_user"):
            raise Forbidden()
        try:
            conversation = env["nudge.conversation"]._chat_open(self._to_id(conversation_id))
            job = conversation._chat_prepare(
                message, snapshot=snapshot, spoken=spoken == "1", read_aloud=read_aloud == "1",
                mode="agent" if mode == "agent" else "talk", background=background == "1",
                explain=explain if explain in ("screen", "text") else None,
                attachment=self._read_upload(file),
                report=report if report in ("sheet", "chat") else None)
        except (UserError, AccessError) as exc:
            return self._reject(exc)
        return self._respond(job)

    @staticmethod
    def _read_upload(file):
        """:return: (name, bytes, mimetype) of the uploaded file, or None"""
        if file is None or not getattr(file, "filename", None):
            return None
        return (file.filename, file.read(), file.mimetype or "")

    @http.route("/nudge/agent/decide", type="http", auth="user", methods=["POST"])
    def agent_decide(self, conversation_id=None, step_id=None, decision=None, **kwargs):
        """Say yes or no to what Nudge is about to do, and let it go on.

        On a yes, what was shown is done here, as the user, before anything
        is sent back. The answer is a stream of JSON lines, as for a message:
        ``start``, ``decided``, what was done, then what the model goes on with.
        """
        env = request.env
        if not env.user.has_group("nudge.group_nudge_agent"):
            raise Forbidden()
        try:
            conversation_id = self._to_id(conversation_id)
            if not conversation_id:
                raise UserError(_("This is no longer waiting for you."))
            conversation = env["nudge.conversation"]._chat_open(conversation_id)
            job = conversation._agent_decide(self._to_id(step_id), decision)
        except (UserError, AccessError) as exc:
            return self._reject(exc)
        return self._respond(job)

    @staticmethod
    def _to_id(value):
        try:
            return int(value) if value else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _reject(error):
        request.env.cr.rollback()
        return Response(
            _line({"type": "error", "state": "rejected", "message": str(error)}),
            headers=STREAM_HEADERS,
        )

    def _respond(self, job):
        # The cursor of the request is committed and closed before the body is
        # sent, and ``request`` is unbound: the stream opens its own cursor.
        env = request.env
        job.update(dbname=env.cr.dbname, uid=env.uid, context=dict(env.context))
        return Response(self._stream(job), headers=STREAM_HEADERS, direct_passthrough=True)

    def _stream(self, job):
        for event in loop.run(job, lambda outcome: self._record(job, outcome)):
            yield _line(event)

    def _record(self, job, outcome):
        """Store one answer of the model and run its tools, in a transaction
        of its own, as the user who asked."""
        try:
            with Registry(job["dbname"]).cursor() as cr:
                env = api.Environment(cr, job["uid"], job["context"])
                conversation = env["nudge.conversation"].browse(job["conversation_id"])
                return conversation._chat_record(outcome)
        except Exception:  # noqa: BLE001
            _logger.exception(
                "Nudge: answer of conversation %s could not be stored", job["conversation_id"])
            return {"event": {"type": "error", "state": "error", "message": ""}}

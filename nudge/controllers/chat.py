import json
import logging

from werkzeug.exceptions import Forbidden

from odoo import api, http
from odoo.exceptions import AccessError, UserError
from odoo.http import Response, request
from odoo.modules.registry import Registry

from ..llm import attachments, loop
from ..models.nudge_slot import NudgeInLine

_logger = logging.getLogger(__name__)

# A file, the snapshot of the screen and the fields of the form around them:
# a request announced larger carries a file too large, told before it is read
MAX_REQUEST_BYTES = attachments.MAX_BYTES + 2 * 1024 * 1024

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
                    report=None, fresh=None, slot=None, **kwargs):
        """Answer one message as a stream of JSON lines.

        :param snapshot: JSON description of the screen, when the user shares it
        :param spoken: "1" when the message was said rather than typed
        :param read_aloud: "1" when the answer will be read to the user
        :param mode: "agent" when Nudge is asked to do the work, "dev" to build
        :param background: "1" when the work is to be done while the user
            does something else
        :param explain: "screen" or "text", how the user wants the answer
            this time; anything else leaves it to the model
        :param file: a file sent with the question (PDF, text, image), read
            on the server and given to the model as data
        :param report: "sheet" or "chat", how the user wants a comparison
            or a report; anything else leaves it to the model
        :param slot: the place in line the question waited in, when its turn came

        Events: ``start``, then ``delta`` and what tools do on the screen
        (``guidance``, ``navigate``, ``write``, ``open``, ``confirm``) in any
        number, then ``done``, ``waiting``, ``queued`` or ``error``. Or one
        ``in_line`` alone: every place is taken, the question waits for its
        turn (``slot_id``, ``position``, ``seconds``) and is asked again then.
        """
        env = request.env
        if not env.user.has_group("nudge.group_nudge_user"):
            raise Forbidden()
        try:
            conversation = env["nudge.conversation"]._chat_open(self._to_id(conversation_id))
            job = conversation._chat_prepare(
                message, snapshot=snapshot, spoken=spoken == "1", read_aloud=read_aloud == "1",
                mode=mode if mode in ("agent", "dev") else "talk", background=background == "1",
                explain=explain if explain in ("screen", "text") else None,
                attachment=self._read_upload(file),
                report=report if report in ("sheet", "chat") else None, fresh=fresh == "1",
                slot_id=self._to_id(slot))
        except NudgeInLine as waiting:
            # Nothing of the question is kept but its place in line: no worker waits with it
            env.cr.rollback()
            line = env["nudge.slot"]._wait(waiting.line)
            return Response(_line(dict(line, type="in_line", message=str(waiting))), headers=STREAM_HEADERS)
        except (UserError, AccessError) as exc:
            return self._reject(exc)
        return self._respond(job)

    @staticmethod
    def _read_upload(file):
        """:return: (name, bytes, mimetype) of the uploaded file, or None
        :raise UserError: for a file of a kind that is not read, or too
            large, said before the file is read in full"""
        if file is None or not getattr(file, "filename", None):
            return None
        env = request.env
        mimetype = file.mimetype or ""
        if attachments.kind_of(file.filename, mimetype) is None:
            raise UserError(env._(attachments.WRONG_KIND))
        too_large = env._(
            attachments.TOO_LARGE_AUDIO if attachments.kind_of(file.filename, mimetype) == "audio"
            else attachments.TOO_LARGE, attachments.MAX_BYTES // (1024 * 1024))
        if (request.httprequest.content_length or 0) > MAX_REQUEST_BYTES:
            raise UserError(too_large)
        # One byte more than allowed is enough to know that it is too much
        data = file.read(attachments.MAX_BYTES + 1)
        if len(data) > attachments.MAX_BYTES:
            raise UserError(too_large)
        return (file.filename, data, mimetype)

    @http.route("/nudge/bulk/start", type="http", auth="user", methods=["POST"])
    def bulk_start(self, conversation_id=None, model=None, operation=None, field=None, ids=None,
                   domain=None, instruction=None, language=None, source_fields=None, **kwargs):
        """Put the same AI work on the records selected in a list before the
        user as a card: the sample the AI proposes for, and what the rest
        costs. The answer is a stream of JSON lines, as for a message.

        :param ids: JSON list of the ids selected
        :param domain: JSON domain of the list, for every record that matches
            it (up to the limit of a plan) when none is selected
        """
        env = request.env
        if not env.user.has_group("nudge.group_nudge_agent"):
            raise Forbidden()
        try:
            conversation = env["nudge.conversation"]._chat_open(self._to_id(conversation_id))
            arguments = {
                "model": model or "", "operation": operation or "", "field": field or "",
                "instruction": (instruction or "")[:500], "language": (language or "")[:60],
            }
            sources = self._json_list(source_fields)
            if sources:
                arguments["source_fields"] = [str(name) for name in sources][:12]
            job = env["nudge.bulk"]._start(
                conversation, arguments, ids=self._json_list(ids), domain=self._json_list(domain))
        except (UserError, AccessError) as exc:
            return self._reject(exc)
        return self._respond(job)

    @staticmethod
    def _json_list(value):
        try:
            found = json.loads(value) if value else None
        except ValueError:
            return None
        return found if isinstance(found, list) else None

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
                raise UserError(env._("This is no longer waiting for you."))
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
        message = str(error)
        if isinstance(error, AccessError):
            # Said to be closed, not why: the why names what is closed to them
            _logger.info("Nudge: refused for user %s: %s", request.env.uid, error)
            message = request.env._("This is not open to you.")
        return Response(
            _line({"type": "error", "state": "rejected", "message": message}),
            headers=STREAM_HEADERS,
        )

    def _respond(self, job):
        # The cursor of the request is committed and closed before the body is
        # sent, and ``request`` is unbound: the stream opens its own cursor.
        env = request.env
        job.update(dbname=env.cr.dbname, uid=env.uid, context=dict(env.context))
        if job.get("serial"):
            # PERF-02: the stop of the person, read between the words of the provider
            job["cancelled"] = lambda: self._stopped(job)
        return Response(self._stream(job), headers=STREAM_HEADERS, direct_passthrough=True)

    def _stream(self, job):
        try:
            for event in loop.run(job, lambda outcome: self._record(job, outcome)):
                yield _line(event)
        finally:
            if job.get("slot_id"):
                self._finish(job)

    @staticmethod
    def _stopped(job):
        """:return: whether the person stopped the answer, on a cursor of its own"""
        try:
            with Registry(job["dbname"]).cursor() as cr:
                env = api.Environment(cr, job["uid"], job["context"])
                return env["nudge.conversation"].browse(job["conversation_id"])._chat_stopped(job["serial"])
        except Exception:  # noqa: BLE001
            _logger.warning("Nudge: could not read whether the answer was stopped", exc_info=True)
            return False

    @staticmethod
    def _finish(job):
        """The answer is over however it ended: its place goes to the next in line"""
        try:
            with Registry(job["dbname"]).cursor() as cr:
                env = api.Environment(cr, job["uid"], job["context"])
                env["nudge.slot"]._finish(job["slot_id"])
        except Exception:  # noqa: BLE001
            _logger.exception("Nudge: the place of conversation %s could not be freed", job["conversation_id"])

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

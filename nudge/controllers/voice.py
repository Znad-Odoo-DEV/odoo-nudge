from werkzeug.exceptions import Forbidden

from odoo import http
from odoo.exceptions import UserError
from odoo.http import request

from ..llm import transcription

# The recording, and the fields of the form around it
MAX_REQUEST_BYTES = transcription.MAX_BYTES + 64 * 1024


class NudgeVoice(http.Controller):

    @http.route(
        "/nudge/voice/transcribe", type="http", auth="user", methods=["POST"],
        max_content_length=MAX_REQUEST_BYTES)
    def transcribe(self, audio=None, language=None, hints=None, **kwargs):
        """Turn a recording into text, for the browsers that cannot.

        :param audio: WAV file
        :param language: code of the language most likely spoken
        :param hints: JSON list of words written on the screen
        :return: JSON with ``text``, or with ``error`` when it could not be done

        The recording is not stored.
        """
        env = request.env
        if not env.user.has_group("nudge.group_nudge_user"):
            raise Forbidden()
        # One byte more than allowed is enough to know that it is too much
        data = audio.read(transcription.MAX_BYTES + 1) if hasattr(audio, "read") else b""
        try:
            text = env["nudge.voice"]._transcribe(data, language=language, hints=hints)
        except UserError as error:
            return request.make_json_response({"error": str(error)})
        return request.make_json_response({"text": text})

import logging
import time

from odoo import models
from odoo.exceptions import UserError

from ..tools import base as tool_base
from ..tools import meeting
from ..tools.deadline import cap
from ..tools.records import Refused
from .nudge_run import register_job

_logger = logging.getLogger(__name__)

# The summary needs this much of a piece; less, and the next piece does it
SUMMARY_SECONDS = 60


class NudgeConversationMeeting(models.Model):
    _inherit = "nudge.conversation"

    def _meeting_piece(self, run, deadline):
        """The work in the background of a long recording: write it out, then summarise it, each committed
        when it is done. The summary is kept for the next question of the person, who is told it is ready.

        :return: whether it is all done"""
        self.ensure_one()
        env = self.with_context(nudge_conversation_id=self.id, nudge_meeting_job=True).env
        attachment, kind = meeting.find_recording(env)
        if not attachment:
            raise UserError(env._("The recording is not there any more. Send it again."))
        try:
            provider = self.sudo()._chat_provider(tool_base.MODE_AGENT)
            provider._check_private()
            text = meeting.transcribe(env, self.id, attachment, kind)
            run.progress = env._("The recording is written out")
            run._commit()
            if run._job_stopped():
                return False
            if cap(SUMMARY_SECONDS) < SUMMARY_SECONDS or _late(deadline):
                return False  # the summary is the next piece's
            summary = meeting.summarise(env, provider, text, (run.job_args or {}).get("language", ""))
        except (Refused, UserError) as error:
            run._say(str(error))
            return True
        meeting.keep_summary(env, self.id, summary)
        run._say(env._(
            "The summary of your meeting is ready. Ask me for the summary of the meeting to see it and to "
            "make tasks, notes or events from it.\n\n%s", summary.get("summary") or ""))
        return True


def _late(deadline):
    return time.monotonic() > deadline - SUMMARY_SECONDS


register_job("meeting", "nudge.conversation", "_meeting_piece")

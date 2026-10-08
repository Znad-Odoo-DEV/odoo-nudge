from odoo.exceptions import AccessError, UserError
from odoo import _, fields, models


class NudgeMessage(models.Model):
    """One turn of a conversation. Rows are written by the server only: they
    are both the history sent to the provider and the record of what was
    spent. One thing changes in them: an answer that waits for the person
    becomes complete once they have decided."""
    _name = "nudge.message"
    _description = "Nudge Message"
    _order = "id"
    _rec_name = "content"

    conversation_id = fields.Many2one(
        "nudge.conversation", required=True, index=True, ondelete="cascade")
    user_id = fields.Many2one(
        related="conversation_id.user_id", store=True, index=True)
    role = fields.Selection(
        [("user", "User"), ("assistant", "Nudge"), ("tool", "Tool")], required=True)
    content = fields.Text()
    is_spoken = fields.Boolean(
        string="Spoken", help="The message was said, and turned into text by a machine.")
    mode = fields.Selection(
        [("talk", "Talk"), ("agent", "Agent"), ("dev", "Developer")],
        help="Talk: Nudge explains and shows. Agent: Nudge does the work.")
    context_text = fields.Text(
        groups="base.group_system",
        string="Context", help="Facts added by Odoo in front of the user's message.")
    snapshot = fields.Json(
        groups="base.group_system",
        string="Screen", help="What the user was looking at when sending the message.")
    snapshot_hash = fields.Char(string="Screen Fingerprint")
    # A file sent with the question: its name for everybody, the file itself
    # (an image, re-shown to the model) for the system
    attachment_name = fields.Char(string="File")
    # A focused command (/count, /explain): answered with its tools and its
    # brief only, from this question on
    command = fields.Char(readonly=True)
    # Said by the person about an answer: useful or not, and why
    feedback = fields.Selection([("up", "Useful"), ("down", "Not Useful")], readonly=True, index=True)
    feedback_note = fields.Char(readonly=True)
    attachment_id = fields.Many2one(
        "ir.attachment", string="File Sent", ondelete="set null", groups="base.group_system")
    content_blocks = fields.Json(
        groups="base.group_system",
        string="Provider Content", help="Answer as returned by the provider, replayed unchanged.")
    tool_calls = fields.Json(
        groups="base.group_system",
        help="What Nudge asked Odoo to do, as the model wrote it.")
    tool_results = fields.Json(
        groups="base.group_system",
        help="What Odoo answered to the calls of the previous message.")
    screen_events = fields.Json(
        string="Shown on Screen", help="What the answer did on the person's screen.")
    pending_results = fields.Json(
        groups="base.group_system",
        help="Results of the calls of an answer that waits for the person to decide.")
    step_ids = fields.One2many("nudge.step", "message_id", string="Steps")
    state = fields.Selection(
        [
            ("complete", "Complete"),
            ("waiting", "Waiting for the Person"),
            ("truncated", "Truncated"),
            ("refused", "Refused"),
            ("aborted", "Stopped"),
            ("error", "Error"),
        ],
        required=True, default="complete")
    error_code = fields.Char()
    error_message = fields.Char()
    error_detail = fields.Char(groups="nudge.group_nudge_manager")
    stop_reason = fields.Char()
    provider_kind = fields.Char(string="Protocol")
    model_name = fields.Char(string="Model")
    request_id = fields.Char(string="Provider Request")
    duration_ms = fields.Integer(string="Duration (ms)")
    input_tokens = fields.Integer()
    output_tokens = fields.Integer()
    cache_read_tokens = fields.Integer()
    cache_write_tokens = fields.Integer()
    cost = fields.Float(digits=(12, 6))

    def rate(self, value, note=""):
        """Said by the person who asked, about an answer of Nudge: useful or
        not, and why. A why becomes a lesson a manager of Nudge approves."""
        if value not in ("up", "down"):
            raise UserError(_("A rating is up or down."))
        note = " ".join((note or "").split())[:400]
        for message in self:
            record = message.sudo()
            if record.user_id.id != self.env.uid or record.role != "assistant":
                raise AccessError(_("Only the person who asked may rate an answer."))
            record.write({"feedback": value, "feedback_note": note or False})
            if value == "down" and note:
                question = record.conversation_id.message_ids.filtered(
                    lambda earlier: earlier.role == "user" and earlier.id < record.id)[-1:]
                self.env["nudge.note"].sudo().create({
                    "name": note[:60] + ("…" if len(note) > 60 else ""),
                    "body": note,
                    "kind": "lesson",
                    "state": "proposed",
                    "reason": _("Said after the answer to: %s", (question.content or "")[:200]),
                    "proposed_by": self.env.uid,
                    "conversation_id": record.conversation_id.id,
                })
        return True

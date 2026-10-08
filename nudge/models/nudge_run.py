import json
import logging
import time

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import config

from ..llm import loop

_logger = logging.getLogger(__name__)

RUN_STATES = [
    ("queued", "Waiting for Its Turn"),
    ("running", "Working"),
    ("waiting", "Waiting for the Person"),
    ("done", "Done"),
    ("failed", "Failed"),
    ("stopped", "Stopped"),
]

STEP_KINDS = [
    ("read", "Read"),
    ("show", "Shown on Screen"),
    ("create", "Create"),
    ("write", "Change"),
    ("method", "Button"),
    ("code", "Code"),
    ("delete", "Delete"),
]

STEP_STATES = [
    ("pending", "Waiting for the Person"),
    ("working", "Being Done"),
    ("done", "Done"),
    ("failed", "Failed"),
    ("declined", "Declined"),
    ("cancelled", "Left Undecided"),
]

# Kinds of steps that change what the database holds
CHANGES = ("create", "write", "method", "delete")

# States of a run that is not over
OPEN_STATES = ("queued", "running", "waiting")

# A run in the background is worked on by pieces: each one ends well before
# the limit of time of a scheduled action, and the next one takes over
PIECE_MAX_SECONDS = 90
PIECE_MIN_SECONDS = 20
PIECE_SAFETY_MARGIN = 25
# Pieces a run may take: what bounds a run is the number of things done for it
MAX_PIECES = 12
STOPPED = "The person stopped this request. Nothing more is done for it."
EXCERPT_CHARS = 300


class NudgeLog(models.AbstractModel):
    """What Nudge did is written by the server and changed by nobody: a log
    that can be edited tells nothing."""
    _name = "nudge.log"
    _description = "Nudge Log"

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(self.env._("The log of Nudge is written by Nudge only."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            raise AccessError(self.env._("The log of Nudge cannot be changed."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_never(self):
        raise AccessError(self.env._("The log of Nudge cannot be deleted."))


class NudgeRun(models.Model):
    """One request made to Nudge in Agent mode, from the question to the
    last thing done for it."""
    _name = "nudge.run"
    _inherit = "nudge.log"
    _description = "Nudge Agent Run"
    _order = "id desc"

    name = fields.Char(string="Request", required=True)
    # A conversation in which Nudge acted stays: it can be archived, not deleted
    conversation_id = fields.Many2one(
        "nudge.conversation", required=True, index=True, ondelete="restrict")
    question_id = fields.Many2one("nudge.message", string="Question", ondelete="restrict")
    user_id = fields.Many2one(
        "res.users", string="Asked By", required=True, index=True, ondelete="restrict")
    company_id = fields.Many2one("res.company", string="Company")
    state = fields.Selection(RUN_STATES, required=True, default="running", index=True)
    step_ids = fields.One2many("nudge.step", "run_id", string="Steps")
    step_count = fields.Integer(string="Number of Steps", compute="_compute_counts", store=True)
    change_count = fields.Integer(string="Changes", compute="_compute_counts", store=True)
    rounds = fields.Integer(string="Calls to the Model")
    model_name = fields.Char(string="Model")
    input_tokens = fields.Integer()
    output_tokens = fields.Integer()
    cost = fields.Float(digits=(12, 6))
    ended_at = fields.Datetime(string="Ended On")
    is_developer = fields.Boolean(
        string="Developer Mode", help="Asked in Developer mode: Nudge built rather than did.")
    is_background = fields.Boolean(
        string="In the Background",
        help="Worked on while the person does something else. They are told when it is "
             "done, and when their answer is needed.")
    pieces = fields.Integer(string="Times Taken Up")
    progress = fields.Char(help="What Nudge was doing when it was last heard of.")
    is_unseen = fields.Boolean(
        string="News for the Person",
        help="The request ended, or waits for an answer, and the person has not looked yet.")

    @api.depends("step_ids.state", "step_ids.kind")
    def _compute_counts(self):
        for run in self:
            run.step_count = len(run.step_ids)
            run.change_count = len(run.step_ids.filtered(
                lambda step: step.kind in CHANGES and step.state == "done"))

    def _add_usage(self, message):
        """:param message: answer of the model that belongs to this run"""
        self.ensure_one()
        self.write({
            "rounds": self.rounds + 1,
            "model_name": message.model_name or self.model_name,
            "input_tokens": self.input_tokens + message.input_tokens
            + message.cache_read_tokens + message.cache_write_tokens,
            "output_tokens": self.output_tokens + message.output_tokens,
            "cost": self.cost + message.cost,
        })

    def _end(self, state):
        ended = self.filtered(lambda run: run.state in OPEN_STATES)
        ended.write({"state": state, "ended_at": fields.Datetime.now()})
        ended.filtered("is_background")._tell(state)

    # ------------------------------------------------------------------
    # In the background
    # ------------------------------------------------------------------
    @api.model
    def _get_piece_seconds(self):
        """:return: how long a piece of work may last: well under the limit
        of time of a scheduled action, past which the server stops it"""
        limit = config["limit_time_real_cron"]
        if limit is None or limit < 0:
            limit = config["limit_time_real"] or 0
        if limit <= 0:
            return PIECE_MAX_SECONDS
        return max(PIECE_MIN_SECONDS, min(PIECE_MAX_SECONDS, limit - PIECE_SAFETY_MARGIN))

    def _queue(self):
        """Leave the run to the scheduled action, which takes it up as soon
        as it can."""
        self.write({"state": "queued"})
        self.env.ref("nudge.cron_agent_runs").sudo()._trigger()

    def _tell(self, kind, card=None):
        """Tell the person, wherever they are in Odoo, what becomes of what
        they asked.

        :param kind: "progress", or the state the run is in
        """
        for run in self.sudo():
            answer = self.env["nudge.message"].sudo().search([
                ("conversation_id", "=", run.conversation_id.id),
                ("role", "=", "assistant"),
                ("content", "!=", False),
            ], order="id desc", limit=1)
            if kind != "progress":
                run.is_unseen = True
            run.user_id._bus_send("nudge.run", {
                "kind": kind,
                "run_id": run.id,
                "conversation_id": run.conversation_id.id,
                "state": run.state,
                "request": run.name,
                "progress": run.progress or "",
                "text": (answer.content or "")[:EXCERPT_CHARS] if kind != "progress" else "",
                "card": card,
            })

    def _as_person(self):
        """:return: the conversation of the run, in the environment of the
        person who asked: their rights, their company, their language"""
        self.ensure_one()
        run = self.sudo()
        user = run.user_id
        context = dict(user.with_user(user).context_get())
        companies = (run.company_id & user.company_ids) | user.company_id
        context["allowed_company_ids"] = companies.ids
        env = self.env(user=user.id, su=False, context=context)
        return env["nudge.conversation"].browse(run.conversation_id.id)

    def _commit(self):
        # Tests run in one transaction, which they undo
        if not self.env.context.get("nudge_keep_transaction"):
            self.env.cr.commit()

    def _work(self):
        """Work on one piece of a run: call the model, run what it asks for,
        until it is done, waits for the person, or the time of the piece is up.

        Done as the person who asked. What changes records still waits for
        their yes: a run in the background never decides for them.
        """
        self.ensure_one()
        run = self.sudo()
        if run.state != "queued" or not run.is_background:
            return
        if run.pieces >= MAX_PIECES:
            run.conversation_id._agent_give_up(run)
            return
        run.write({"state": "running", "pieces": run.pieces + 1})
        self._commit()

        conversation = run._as_person()
        working = run.step_ids.filtered(lambda step: step.state == "working")[:1]
        if working and not run._work_plan(working, conversation):
            # More to do: taken up again in the next piece
            return
        try:
            job = conversation._background_job()
        except UserError as error:
            run.conversation_id._agent_give_up(run, str(error))
            self._commit()
            return

        def record(outcome):
            result = conversation._chat_record(outcome)
            self._commit()
            return result

        started = time.monotonic()
        try:
            for event in loop.run(job, record):
                if event["type"] == "confirm":
                    run._tell("waiting", card={
                        key: value for key, value in event.items() if key != "type"})
        except Exception:  # noqa: BLE001
            _logger.exception("Nudge: run %s failed in the background", run.id)
            self.env.cr.rollback()
            run.invalidate_recordset()
            run.conversation_id._agent_give_up(run)
        self._commit()
        _logger.info(
            "Nudge: run %s of user %s worked on for %.1f s in the background, now %s",
            run.id, run.user_id.id, time.monotonic() - started, run.state)

    def _work_plan(self, step, conversation):
        """Do a piece of a plan the person said yes to.

        :return: whether it is all done, and the model may go on
        """
        from .. import tools  # noqa: PLC0415
        from .nudge_conversation import DONE, FAILED  # noqa: PLC0415

        run = self.sudo()
        tool = tools.base.TOOLS.get(step.tool)
        env = conversation.with_context(nudge_conversation_id=conversation.id, nudge_mode="agent").env
        deadline = tools.plans.now() + self._get_piece_seconds()

        def save():
            card = step._get_card()
            run.progress = self.env._("%(done)s done, %(failed)s did not go through",
                                      done=card["done_count"], failed=card["failed_count"])
            run._tell("progress")
            self._commit()

        result = tools.base.guarded(env, step.tool, lambda: tool.execute(env, step, deadline=deadline, save=save))
        if result is None:
            save()
            run._queue()
            self._commit()
            return False
        state = "failed" if result.is_error else "done"
        conversation.sudo()._agent_settle(step, state, tools.base.ToolResult(
            (FAILED if result.is_error else DONE) % result.content,
            is_error=result.is_error, records=result.records), [result.event] if result.event else [])
        self._commit()
        return True

    @api.model
    def _cron_work(self):
        """Take up the runs that wait for their turn, one piece each."""
        Cron = self.env["ir.cron"]
        seconds = self._get_piece_seconds()
        while True:
            runs = self.sudo().search(
                [("state", "=", "queued"), ("is_background", "=", True)], order="write_date, id")
            run = runs.try_lock_for_update(limit=1)
            if not run:
                break
            run._work()
            left = Cron._commit_progress(1, remaining=max(len(runs) - 1, 0))
            if left < seconds + PIECE_SAFETY_MARGIN:
                # Not the time for another piece: what is left is taken up next
                if len(runs) > 1 or run.state == "queued":
                    self.env.ref("nudge.cron_agent_runs").sudo()._trigger()
                break

    def action_undo_built(self):
        """Delete what Nudge built for this request in Developer mode, the last
        made first. Its own requests only, for an administrator."""
        from ..tools.developer import undo_built

        if not self.env.user.has_group("base.group_system"):
            raise AccessError(self.env._("Only an administrator undoes what was built."))
        runs = self.filtered(lambda run: run.is_developer and run.user_id == self.env.user)
        if not runs:
            raise UserError(self.env._("Only your own requests in Developer mode can be undone here."))
        deleted, kept = undo_built(self.env, runs)
        message = self.env._("Deleted: %s.", ", ".join(deleted)) if deleted else self.env._("Nothing made here still exists.")
        if kept:
            message += " " + self.env._("Kept: %s.", "; ".join(kept))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": self.env._("Undo"), "message": message, "sticky": bool(kept),
                       "type": "warning" if kept else "success",
                       "next": {"type": "ir.actions.client", "tag": "reload"}},
        }

    def action_stop(self):
        """Stop what was asked, by the one who asked it or by a manager. What
        was done stays done."""
        is_manager = self.env.user.has_group("nudge.group_nudge_manager")
        for run in self:
            if run.user_id != self.env.user and not is_manager:
                raise AccessError(self.env._("This request is not yours."))
            run = run.sudo()
            if run.state not in OPEN_STATES:
                continue
            run.conversation_id._agent_leave_undecided(STOPPED)
            run.progress = False
            run._end("stopped")
            if run.conversation_id.run_id == run:
                run.conversation_id.stream_started_at = False
        return True

    def action_seen(self):
        """The person looked at what became of their requests."""
        mine = self.filtered(lambda run: run.user_id == self.env.user)
        mine.sudo().filtered("is_unseen").write({"is_unseen": False})
        return True

    @api.model
    def get_news(self):
        """:return: what the person has going on in the background, and what
        ended since they last looked"""
        runs = self.search([
            ("user_id", "=", self.env.uid),
            ("is_background", "=", True),
            "|", ("state", "in", OPEN_STATES), ("is_unseen", "=", True),
        ], order="id desc", limit=20)
        return [{
            "run_id": run.id,
            "conversation_id": run.conversation_id.id,
            "state": run.state,
            "request": run.name,
            "progress": run.progress or "",
            "is_unseen": run.is_unseen,
        } for run in runs]


class NudgeStep(models.Model):
    """One thing Nudge did, or is about to do, for a run."""
    _name = "nudge.step"
    _inherit = "nudge.log"
    _description = "Nudge Agent Step"
    _order = "id"
    _rec_name = "summary"

    run_id = fields.Many2one("nudge.run", required=True, index=True, ondelete="restrict")
    conversation_id = fields.Many2one(
        related="run_id.conversation_id", store=True, index=True)
    user_id = fields.Many2one(related="run_id.user_id", store=True, index=True)
    message_id = fields.Many2one(
        "nudge.message", string="Answer", ondelete="restrict",
        help="Answer of the model that asked for this step.")
    call_id = fields.Char(string="Call")
    tool = fields.Char(required=True)
    kind = fields.Selection(STEP_KINDS, required=True, default="read", index=True)
    state = fields.Selection(STEP_STATES, required=True, default="done", index=True)
    summary = fields.Char(help="What the step does, in the words shown to the person.")
    res_model = fields.Char(string="Records Of", index=True)
    res_ids = fields.Json(string="Records")
    record_names = fields.Char(string="Names")
    arguments = fields.Json(help="What the model asked for, as it wrote it.")
    values = fields.Json(
        help="What is written in the records, once checked and read as the fields expect it.")
    method = fields.Char(string="Button")
    preview = fields.Json(help="What was shown to the person before they decided.")
    is_sensitive = fields.Boolean(
        string="Sensitive", help="Posts, validates, cancels, or touches money or stock.")
    result = fields.Text(help="What was told to the model once the step was over.")
    error = fields.Text()
    decided_at = fields.Datetime(string="Decided On")
    duration_ms = fields.Integer(string="Duration (ms)")
    # For the one who reads the log
    res_ids_text = fields.Char(string="Ids", compute="_compute_texts")
    arguments_text = fields.Text(string="Asked by the Model", compute="_compute_texts")
    values_text = fields.Text(string="Written in the Records", compute="_compute_texts")
    preview_text = fields.Text(string="Shown to the Person", compute="_compute_texts")

    @api.depends("res_ids", "arguments", "values", "preview")
    def _compute_texts(self):
        def write(value):
            if not value:
                return False
            return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)

        for step in self:
            step.res_ids_text = ", ".join(map(str, step.res_ids or [])) or False
            step.arguments_text = write(step.arguments)
            step.values_text = write(step.values)
            step.preview_text = "\n".join(step._preview_lines()) or False

    def _preview_lines(self, lines=None, indent=""):
        """:return: what the person was shown, one line of text per line"""
        self.ensure_one()
        preview = self.preview or {}
        written = []
        if lines is None:
            written += [f"{record.get('name')} ({record.get('id')})"
                        for record in preview.get("records") or []]
            lines = preview.get("lines") or []
        for line in lines:
            if line.get("lines") is not None:
                written.append(f"{indent}{line.get('label')}:")
                written += self._preview_lines(line["lines"], indent + "    ")
            elif "before" in line:
                written.append(
                    f"{indent}{line.get('label')}: {line.get('before')} → {line.get('value')}")
            else:
                written.append(f"{indent}{line.get('label')}: {line.get('value')}")
        return written

    def _get_card(self):
        """:return: what the person is shown of a step that waits for them, or
        that waited"""
        self.ensure_one()
        preview = self.preview or {}
        values = self.values if isinstance(self.values, dict) else {}
        progress = values.get("progress") or {}
        return {
            "step_id": self.id,
            "kind": self.kind,
            "state": self.state,
            "title": self.summary or "",
            "records": preview.get("records") or [],
            "lines": preview.get("lines") or [],
            "sensitive": self.is_sensitive,
            "error": self.error or "",
            # Many records at once: how many go through, why the others do not
            "plan": preview.get("plan") or False,
            "done_count": progress.get("done", 0),
            "failed_count": len(progress.get("failed") or {}),
            # What was written can be taken back, once
            "can_undo": self.state == "done" and bool(self._undo_data()) and not values.get("undone"),
            "undone": bool(values.get("undone")),
        }

    def _undo_data(self):
        values = self.values if isinstance(self.values, dict) else {}
        return values.get("undo") or (values.get("progress") or {}).get("undo") or {}

    @api.model
    def undo_changes(self, step_id):
        """Write back what a step wrote, as the person who said yes to it;
        a record changed since is left as it is.

        :return: ``{"message", "card"}``
        """
        from ..tools.plans import undo_changes  # noqa: PLC0415

        step = self.sudo().browse(int(step_id)).exists()
        if not step or step.user_id != self.env.user:
            raise AccessError(self.env._("This is not yours to undo."))
        values = step.values if isinstance(step.values, dict) else {}
        if step.state != "done" or not step._undo_data() or values.get("undone"):
            raise UserError(self.env._("There is nothing to undo here."))
        restored, kept = undo_changes(self.env, step._undo_data())
        step.values = dict(values, undone=True)
        message = self.env._("%s records are back as they were.", restored)
        if kept:
            message += " " + self.env._("Left as they are: %s", "; ".join(
                f"{name} ({why})" for name, why in kept[:10]))
        _logger.info("Nudge: step %s undone by user %s: %s back, %s left", step.id, self.env.uid,
                     restored, len(kept))
        return {"message": message, "card": step._get_card()}

    @api.model
    def get_cards(self, conversation_id):
        """:return: the steps of a conversation that were put before the
        person, for the one who reads the conversation again"""
        steps = self.search([
            ("conversation_id", "=", conversation_id),
            ("kind", "in", CHANGES),
        ])
        # What was refused before the person saw it was never a card
        return {
            step.message_id.id: step._get_card()
            for step in steps if step.message_id and (step.preview or step.decided_at)
        }

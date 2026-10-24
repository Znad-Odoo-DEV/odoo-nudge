"""The answers Nudge streams at the same time, and the line of the questions that wait for one (NP-04).

An answer holds one http worker while it streams: a few at once leave none for the screens of
everyone else, so their number is capped (``nudge.max_streams``), and a provider may give only so
many at once (a model on one machine answers one question at a time). Past the cap a question is
not refused: it waits in line without holding a worker (the browser asks where it stands, and the
bus tells it its turn), and its answer starts when a place frees.

What takes a place:
- the mark of a conversation that is answering (``stream_started_at``), as before: the calls of
  the external assistants and of the messaging apps carry it too;
- a place claimed in the instant between the claim and the commit of the question (``claimed``):
  the claim is committed at once, on a cursor of its own, so that every worker sees it;
- a place kept for the first in line until they come for it (``ready``).
The claim and the hand-out of the places are made under one advisory lock: two questions that
arrive together cannot both take the last place, nor one person start two answers (PERF-04).
"""
import contextlib
import logging
import math
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.modules import module as odoo_module

from ..tools.bus import notify_user

_logger = logging.getLogger(__name__)

SLOT_STATES = [
    ("waiting", "Waiting"),
    ("ready", "Its Turn"),
    ("claimed", "Starting"),
    ("streaming", "Answering"),
    ("done", "Done"),
    ("stopped", "Stopped"),
    ("cancelled", "Cancelled"),
    ("expired", "Expired"),
]
OPEN_STATES = ("waiting", "ready", "claimed", "streaming")
LINE_PARAM = "nudge.max_line"
# How many questions may wait at once, for everybody together
DEFAULT_LINE = 20
# A question waits this long at most: then the person is offered to ask again, with their text kept
WAIT_SECONDS = 120
# A browser that stopped asking where its question stands is gone: its place goes to the next
HEARTBEAT_SECONDS = 20
# A turn that is not taken in this time passes to the next in line
READY_SECONDS = 20
# A place claimed by a question that never committed stops counting
HANDOVER_SECONDS = 60
# How long an answer takes, before any was measured
ANSWER_SECONDS = 15
ANSWERS_MEASURED = 50
# What is over is forgotten after a day: the log of usage keeps what was spent
KEEP_DAYS = 1
LOCK_KEY = "nudge.slot"
LOCK_TIMEOUT = "10s"
# Who leaves the line, and when: who stopped asking, who waited too long, a turn not taken, a
# claim whose question never committed
_SWEEP = """
    UPDATE nudge_slot SET state = '{new}', ended_at = %(now)s, write_date = %(now)s
     WHERE id IN (SELECT id FROM nudge_slot WHERE state = '{state}' AND {since} < %(limit)s
                  FOR UPDATE SKIP LOCKED)
"""
SWEEPS = [
    (_SWEEP.format(new="cancelled", state="waiting", since="seen_at"), HEARTBEAT_SECONDS),
    (_SWEEP.format(new="expired", state="waiting", since="waiting_since"), WAIT_SECONDS),
    (_SWEEP.format(new="expired", state="ready", since="ready_at"), READY_SECONDS),
    (_SWEEP.format(new="done", state="claimed", since="started_at"), HANDOVER_SECONDS),
]


class NudgeInLine(UserError):
    """The question waits for a place. Nothing of it is kept but its place in line, written once
    what the question started is rolled back (:meth:`NudgeSlot._wait`)."""

    def __init__(self, message, line):
        super().__init__(message)
        self.line = line


class NudgeBusy(UserError):
    """No place is free for a caller that cannot wait in the line of the browsers (an external
    assistant, a messaging app). ``retry_after``: about how many seconds until one frees."""

    def __init__(self, message, retry_after=ANSWER_SECONDS):
        super().__init__(message)
        self.retry_after = retry_after


class NudgeSlot(models.Model):
    _name = "nudge.slot"
    _description = "Nudge Answer Slot"
    _order = "id"

    user_id = fields.Many2one("res.users", string="Asked By", required=True, index=True, ondelete="cascade")
    company_id = fields.Many2one("res.company", string="Company", ondelete="cascade")
    # A plain id: a new conversation is not committed yet when its place is claimed on another cursor
    conversation_ref = fields.Integer(string="Conversation", index=True)
    provider_id = fields.Many2one("nudge.provider", string="Provider", ondelete="set null")
    state = fields.Selection(SLOT_STATES, required=True, default="waiting", index=True)
    waiting_since = fields.Datetime()
    seen_at = fields.Datetime(string="Last Asked", help="When the browser last asked where the question stands.")
    ready_at = fields.Datetime(string="Turn Given")
    started_at = fields.Datetime(string="Answer Started")
    ended_at = fields.Datetime()

    # ------------------------------------------------------------------
    # Infrastructure
    # ------------------------------------------------------------------
    @contextlib.contextmanager
    def _own(self):
        """:return: this model in a transaction of its own, committed at once: what the other
        workers must see before the request that claims it ends. A test works in one transaction
        it never commits: there, in the same one. In sudo: places are bookkeeping of Nudge, and
        a person sees only their own through the methods below."""
        if odoo_module.current_test:
            yield self.sudo()
            return
        with self.env.registry.cursor() as cr:
            yield self.with_env(self.env(cr=cr, su=True))

    def _lock(self):
        """Every claim and hand-out, one at a time, until the transaction ends (a few milliseconds).
        Never waited for long: a worker is not left hanging on it"""
        self.env.cr.execute("SET LOCAL lock_timeout = %s", [LOCK_TIMEOUT])
        self.env.cr.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [LOCK_KEY])

    def _window_start(self):
        """:return: the oldest mark of a conversation that still counts: a worker that died
        mid-answer leaves its mark, which expires with the longest time an answer may take"""
        Provider = self.env["nudge.provider"].sudo()
        Conversation = self.env["nudge.conversation"]
        providers = (Provider._get_default(), Provider._get_for_mode("dev"), Provider._get_fallback())
        longest = max(Conversation._get_stream_seconds(provider) for provider in providers)
        return fields.Datetime.now() - timedelta(seconds=longest)

    def _taken(self, provider=None):
        """:param provider: count the places of this provider only
        :return: how many places are taken: answers streaming, places claimed an instant ago,
            places kept for the first in line"""
        now = fields.Datetime.now()
        marks = [("stream_started_at", ">=", self._window_start())]
        slots = ["|", "&", ("state", "=", "claimed"), ("started_at", ">=", now - timedelta(seconds=HANDOVER_SECONDS)),
                 "&", ("state", "=", "ready"), ("ready_at", ">=", now - timedelta(seconds=READY_SECONDS))]
        if provider:
            marks.append(("provider_id", "=", provider.id))
            slots.append(("provider_id", "=", provider.id))
        Conversation = self.env["nudge.conversation"].sudo().with_context(active_test=False)
        return Conversation.search_count(marks) + self.search_count(slots)

    def _is_answering(self, user):
        """:return: whether ``user`` is being answered: one answer at a time per person"""
        Conversation = self.env["nudge.conversation"].sudo().with_context(active_test=False)
        if Conversation.search_count([
                ("user_id", "=", user.id), ("stream_started_at", ">=", self._window_start())], limit=1):
            return True
        return bool(self.search_count([
            ("user_id", "=", user.id), ("state", "=", "claimed"),
            ("started_at", ">=", fields.Datetime.now() - timedelta(seconds=HANDOVER_SECONDS))], limit=1))

    def _max_line(self):
        value = self.env["ir.config_parameter"].sudo().get_param(LINE_PARAM) or ""
        try:
            wanted = int(float(value))
        except ValueError:
            return DEFAULT_LINE
        return wanted if wanted > 0 else DEFAULT_LINE

    def _sweep(self):
        """What waits for nobody any more leaves the line; what is long over is forgotten. A place
        a request is writing at this instant is passed over (SKIP LOCKED): never waited for"""
        now = fields.Datetime.now()
        self.flush_model()
        for query, seconds in SWEEPS:
            self.env.cr.execute(query, {"now": now, "limit": now - timedelta(seconds=seconds)})
        self.env.cr.execute("""
            DELETE FROM nudge_slot WHERE id IN (
                SELECT id FROM nudge_slot WHERE write_date < %s AND state NOT IN ('waiting', 'ready', 'claimed')
                 LIMIT 500 FOR UPDATE SKIP LOCKED)
        """, [now - timedelta(days=KEEP_DAYS)])
        self.invalidate_model()

    def _hand_out(self, asking=None, provider=None):
        """Give the free places to those first in line.

        :param asking: the place of a question asking to start now, when it already waits
        :param provider: the provider of the question asking to start now
        :return: (the places waiting that get a turn, whether the question asking gets one);
            a question that does not wait yet comes after every one that does"""
        free = self.env["nudge.conversation"]._get_max_streams() - self._taken()
        left = {}

        def fits(provider):
            nonlocal free
            if free <= 0:
                return False
            limit = provider._get_max_parallel() if provider else 0
            if limit:
                if provider.id not in left:
                    left[provider.id] = limit - self._taken(provider=provider)
                if left[provider.id] <= 0:
                    # Behind a provider that is full, the questions for another may go first
                    return False
                left[provider.id] -= 1
            free -= 1
            return True

        given = self.browse()
        for waiting in self.search([("state", "=", "waiting")], order="id"):
            if asking and waiting == asking:
                return given, fits(provider)
            if fits(waiting.provider_id):
                given |= waiting
        return given, provider is not None and fits(provider)

    def _give_turns(self, slots):
        now = fields.Datetime.now()
        for slot in slots:
            slot.write({"state": "ready", "ready_at": now})
            notify_user(slot.user_id, "nudge.slot", {"slot_id": slot.id, "state": "ready"})

    def _answer_seconds(self):
        """:return: about how long an answer takes, from the last ones"""
        self.env.cr.execute("""
            SELECT avg(duration_ms) FROM (
                SELECT duration_ms FROM nudge_message
                 WHERE role = 'assistant' AND duration_ms > 0 ORDER BY id DESC LIMIT %s) last
        """, [ANSWERS_MEASURED])
        average = self.env.cr.fetchone()[0]
        return max(5, int(average / 1000)) if average else ANSWER_SECONDS

    def _place(self):
        """:return: where this question stands, as the person is told it: never anything of the others"""
        self.ensure_one()
        position = 0
        seconds = 0
        if self.state == "waiting":
            position = self.search_count([("state", "=", "waiting"), ("id", "<", self.id)]) + 1
            places = self.env["nudge.conversation"]._get_max_streams()
            limit = self.provider_id._get_max_parallel() if self.provider_id else 0
            if limit:
                places = min(places, limit)
            seconds = math.ceil(position / max(1, places)) * self._answer_seconds()
        return {"slot_id": self.id, "state": self.state, "position": position, "seconds": seconds}

    # ------------------------------------------------------------------
    # A question asks for a place
    # ------------------------------------------------------------------
    @api.model
    def _claim(self, conversation, provider, slot_id=None):
        """Take a place for the answer of the question being asked in ``conversation``, or let
        it wait in line.

        :param provider: the provider that answers
        :param slot_id: the place in line the question waited in, when it comes for its turn
        :return: the place, in the environment of the question: handed over with the mark of the
            conversation, in the same transaction
        :raise NudgeInLine: when the question waits; UserError when the person is answered
            already, or the line is full"""
        user = self.env.user
        line = None
        with self._own() as Slot:
            Slot._lock()
            Slot._sweep()
            if Slot._is_answering(user):
                raise UserError(_("Nudge is still answering your previous message."))
            mine = Slot.browse(slot_id).exists() if slot_id else Slot.browse()
            if mine and (mine.user_id != user or mine.state not in ("waiting", "ready")):
                mine = Slot.browse()
            if not mine:
                # One place in line per person: asked again, the question takes the place of the last
                mine = Slot.search([("user_id", "=", user.id), ("state", "in", ("waiting", "ready"))], limit=1)
            now = fields.Datetime.now()
            values = {"conversation_ref": conversation.id, "provider_id": provider.id, "seen_at": now}
            if mine.state == "ready":
                mine.write(dict(values, state="claimed", started_at=now))
                slot_id = mine.id
            else:
                given, fits = Slot._hand_out(asking=mine, provider=provider)
                Slot._give_turns(given)
                if fits:
                    values.update(state="claimed", started_at=now)
                    if mine:
                        mine.write(values)
                    else:
                        mine = Slot.create(dict(values, user_id=user.id, company_id=self.env.company.id))
                    slot_id = mine.id
                elif not mine and Slot.search_count([("state", "=", "waiting")]) >= Slot._max_line():
                    raise UserError(_(
                        "Nudge is answering other people right now. Try again in a moment."))
                else:
                    line = dict(values, slot_id=mine.id or None, company_id=self.env.company.id)
        if line:
            raise NudgeInLine(_(
                "Nudge is answering other people right now. Your question waits for its turn."), line)
        if not odoo_module.current_test:
            # What the question started is rolled back (an error after the claim): its place is
            # given back at once. A test claims in its own transaction, rolled back with it
            self.env.cr.postrollback.add(lambda: self._release(slot_id))
        return self.sudo().browse(slot_id)

    @api.model
    def _claim_channel(self, conversation, provider):
        """Take a place for a call that does not come from the browser of the person: a tool call
        of an external assistant (MCP), an answer to a message on a phone. It shares the places and
        the order of the line with the browsers: it never goes before a question that waits. It does
        not wait itself (nobody keeps asking for it): the caller is told to come back, and decides
        how (MCP answers "busy", messaging keeps the message for its cron).

        :param conversation: the log conversation of the call (a stop of it frees the place)
        :param provider: the provider that answers, empty when the call uses none
        :return: the place, in sudo; give it back with :meth:`_finish`
        :raise NudgeBusy: when no place is free for this provider"""
        user = self.env.user
        retry = None
        slot_id = None
        with self._own() as Slot:
            Slot._lock()
            Slot._sweep()
            given, fits = Slot._hand_out(provider=provider)
            Slot._give_turns(given)
            if fits:
                now = fields.Datetime.now()
                slot_id = Slot.create({
                    "user_id": user.id, "company_id": self.env.company.id,
                    "conversation_ref": conversation.id, "provider_id": provider.id or False,
                    "state": "claimed", "started_at": now, "seen_at": now}).id
            else:
                ahead = Slot.search_count([("state", "=", "waiting")]) + 1
                places = self.env["nudge.conversation"]._get_max_streams()
                limit = provider._get_max_parallel() if provider else 0
                if limit:
                    places = min(places, limit)
                retry = math.ceil(ahead / max(1, places)) * Slot._answer_seconds()
        if retry is not None:
            raise NudgeBusy(_(
                "Nudge is answering other people right now. Try again in %(seconds)s seconds.", seconds=retry), retry)
        return self.sudo().browse(slot_id)

    @api.model
    def _wait(self, line):
        """Write the place in line of a question that waits, once what it started was rolled back.

        :param dict line: what :class:`NudgeInLine` carries
        :return: where it stands"""
        Slot = self.sudo()
        now = fields.Datetime.now()
        values = {key: line[key] for key in ("conversation_ref", "provider_id")}
        slot = Slot.browse(line.get("slot_id")).exists() if line.get("slot_id") else Slot.browse()
        if slot and slot.state in ("waiting", "ready") and slot.user_id == self.env.user:
            # Its turn may have come meanwhile: the browser finds it out at once
            slot.write(dict(values, seen_at=now))
        else:
            slot = Slot.create(dict(values, user_id=self.env.uid, company_id=line.get("company_id"),
                                    state="waiting", waiting_since=now, seen_at=now))
        return slot._place()

    def _release(self, slot_id):
        """A place claimed by a question that failed before it was answered is free again"""
        with self._own() as Slot:
            slot = Slot.browse(slot_id).exists()
            if slot.state == "claimed":
                slot.write({"state": "cancelled", "ended_at": fields.Datetime.now()})

    def _waited_ms(self):
        """:return: how long the question of this place waited in line, in milliseconds; 0 when
        it did not wait. Read on a cursor of its own: the place was committed after the request
        began, and the request's snapshot (repeatable read) does not hold it"""
        if not self:
            return 0
        with self._own() as Slot:
            slot = Slot.browse(self.ids[:1]).exists()
            if not (slot.waiting_since and slot.started_at):
                return 0
            return max(0, int((slot.started_at - slot.waiting_since).total_seconds() * 1000))

    def _handover(self, mark_committed=False):
        """The answer starts: once the transaction that writes the mark of the conversation is
        committed, the place stops counting and the mark counts instead (for that instant both
        count: never one place too many). The place is written on a cursor of its own: the request
        cannot see it, committed after it began (repeatable read)

        :param mark_committed: the mark was committed already, on a cursor of its own (the calls of
            the external assistants and of the messaging apps): the place stops counting at once"""
        if not self:
            return
        if odoo_module.current_test:
            self.sudo().filtered(lambda slot: slot.state == "claimed").state = "streaming"
            return
        ids = self.ids
        if mark_committed:
            self._streaming(ids)
        else:
            self.env.cr.postcommit.add(lambda: self._streaming(ids))

    @api.model
    def _streaming(self, ids):
        with self._own() as Slot:
            Slot.browse(ids).exists().filtered(lambda slot: slot.state == "claimed").state = "streaming"

    @api.model
    def _finish(self, slot_id):
        """The stream is over, however it ended: its place goes to the next in line"""
        with self._own() as Slot:
            Slot._lock()
            slot = Slot.browse(slot_id).exists() if slot_id else Slot.browse()
            if slot.state in ("claimed", "streaming"):
                slot.write({"state": "done", "ended_at": fields.Datetime.now()})
            Slot._sweep()
            given, _fits = Slot._hand_out()
            Slot._give_turns(given)

    @api.model
    def _stop(self, conversation_ids):
        """The person stopped the answer: the place and the mark of the conversation are free at
        once, the next in line is given its turn, and the stream that may still wait for its
        provider is told (its serial is the one stopped)."""
        with self._own() as Slot:
            # What the transaction holds is written first, so that it does not overwrite the stop;
            # and the conversation before the lock: a stream writing its answer is waited for
            # without holding up the questions of everyone else
            Slot.env["nudge.conversation"].flush_model(["stream_started_at", "stream_serial", "stream_stopped"])
            Slot.env.cr.execute("""
                UPDATE nudge_conversation
                   SET stream_stopped = stream_serial, stream_started_at = NULL
                 WHERE id = ANY(%s)
            """, [list(conversation_ids)])
            Slot.env["nudge.conversation"].invalidate_model(["stream_stopped", "stream_started_at"])
            Slot._lock()
            Slot.search([("conversation_ref", "in", list(conversation_ids)),
                         ("state", "in", ("claimed", "streaming"))]).write(
                {"state": "stopped", "ended_at": fields.Datetime.now()})
            given, _fits = Slot._hand_out()
            Slot._give_turns(given)
        self.env["nudge.conversation"].invalidate_model(["stream_stopped", "stream_started_at"])

    # ------------------------------------------------------------------
    # The browser of the person who waits
    # ------------------------------------------------------------------
    def _check_mine(self):
        self.ensure_one()
        if self.sudo().user_id != self.env.user:
            raise AccessError(_("This is not open to you."))

    def wait_status(self):
        """Where the question of the person stands; asked every few seconds by their browser,
        which tells that they still wait. A free place is given to the first in line here too,
        when the end of an answer did not hand it out (a worker that died).

        :return: ``slot_id``, ``state`` (waiting, ready, expired, cancelled...), ``position``
            (1 is next) and ``seconds``, about how long it still waits"""
        self._check_mine()
        with self._own() as Slot:
            Slot._lock()
            Slot._sweep()
            mine = Slot.browse(self.id)
            if mine.state == "waiting":
                mine.seen_at = fields.Datetime.now()
                given, _fits = Slot._hand_out()
                Slot._give_turns(given)
            place = mine._place()
        return place

    def cancel_wait(self):
        """The person no longer waits: their place goes to the next"""
        self._check_mine()
        with self._own() as Slot:
            Slot._lock()
            mine = Slot.browse(self.id)
            if mine.state in ("waiting", "ready"):
                mine.write({"state": "cancelled", "ended_at": fields.Datetime.now()})
                given, _fits = Slot._hand_out()
                Slot._give_turns(given)
        return True


class NudgeMessage(models.Model):
    _inherit = "nudge.message"

    # Read by the latency panel (NP-20), in milliseconds, on the answers; 0: not measured
    queue_wait_ms = fields.Integer(
        string="Wait in Line (ms)", readonly=True,
        help="How long the question waited in line for a place before this answer started (NP-04).")
    first_line_ms = fields.Integer(
        string="First Words (ms)", readonly=True,
        help="From the question reaching the server to the first words of the answer.")

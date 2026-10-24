"""The exchange between the model and Odoo for one question.

The model answers; when it calls tools, Odoo runs them and the model is
called again to read what they returned, until it has nothing more to ask
or the number of rounds is used up.

Written as a generator of events for the browser, with no Odoo import: the
caller says how an answer is recorded, which is where the database is, and
how a stop of the person is read (``job["cancelled"]``).
"""
import logging
import queue
import threading
import time

from . import base, prompt

_logger = logging.getLogger(__name__)

# When the person asked for the pointer, the model must reach for a tool: for
# this many rounds, or until something is shown on the screen
FORCED_ROUNDS = 2
SHOWN_EVENTS = ("guidance", "navigate")
# A round is not started when the time left would not see it through: this
# long at least, or most of what the round before took
MIN_ROUND_SECONDS = 15
# The last round is told in words as well: tool_choice "none" is not enough
# for every model (Gemini then tries a call it cannot make, and says nothing)
LAST_ROUND_NOTE = (
    "No round is left to call a tool. Answer now, in words, from what you have read: what you "
    "found, what is left to do, and what the person can ask next.")
# An answer with neither words nor a call (Gemini: a call it could not
# write) is asked for once more: nothing of it reached the person
EMPTY_RETRIES = 1
# A small model asked to do the work may write about it instead: told once
PUSH_NOTE = (
    "You wrote about it instead of doing it. The person asked you to do it: call now the tool "
    "that does it; the card is where they say yes. Only if no tool can, say why in one sentence.")
# PERF-02: how often the stop of the person is looked for while the provider says nothing
STOP_CHECK_SECONDS = 1.0
# A provider still silent this long past the deadline is not waited for: the worker is let go
DEADLINE_GRACE_SECONDS = 5


class Stopped(Exception):
    """The person stopped the answer"""


def _read(events, cancelled, deadline):
    """The events of one call to the model, read in a thread of their own: a provider that says
    nothing does not keep the stop of the person from being seen, nor the worker past the deadline.
    The provider's stream is closed by that thread when its next word comes, or when it gives up.

    :param cancelled: callable telling whether the person stopped the answer
    :raise Stopped: when they did"""
    box = queue.Queue()
    gone = threading.Event()
    over = object()

    def pump():
        try:
            for event in events:
                box.put((None, event))
                if gone.is_set():
                    break
        except Exception as error:  # noqa: BLE001
            box.put((error, None))
        finally:
            try:
                events.close()
            except Exception:  # noqa: BLE001
                _logger.debug("Nudge: the provider's stream did not close cleanly", exc_info=True)
            box.put((None, over))

    threading.Thread(target=pump, name="nudge-provider-stream", daemon=True).start()
    checked = time.monotonic()
    try:
        while True:
            try:
                error, event = box.get(timeout=STOP_CHECK_SECONDS)
            except queue.Empty:
                error = event = None
            now = time.monotonic()
            if now - checked >= STOP_CHECK_SECONDS or event is None:
                checked = now
                if cancelled():
                    raise Stopped()
            if error is not None:
                raise error
            if event is over:
                return
            if event is not None:
                yield event
            elif deadline is not None and now > deadline + DEADLINE_GRACE_SECONDS:
                yield base.error_event(base.ERROR_TIMEOUT, "the provider said nothing before the deadline")
                return
    finally:
        gone.set()


def is_empty(outcome):
    """:return: whether a call to the model brought nothing at all"""
    stop = outcome.get("stop")
    return (
        stop is not None and "error" not in outcome and not outcome.get("aborted")
        and not outcome["text"] and not outcome["tool_calls"]
        and stop.get("reason") in (base.STOP_END, base.STOP_TOOL_USE))


def add_usage(stop, spent):
    """What the calls asked again consumed is counted with the last one."""
    usage = dict(stop.get("usage") or {})
    for key, value in spent.items():
        usage[key] = int(usage.get(key) or 0) + value
    return dict(stop, usage=usage)


def time_for_a_round(deadline, last_duration, now=None):
    """:param last_duration: seconds the round before took
    :return: whether a round may be started before the deadline"""
    if deadline is None:
        return True
    needed = max(MIN_ROUND_SECONDS, 0.8 * last_duration)
    return (time.monotonic() if now is None else now) + needed < deadline


def run(job, record):
    """:param dict job: what ``nudge.conversation._chat_prepare`` returned
    :param record: callable storing the outcome of one call to the model and
        running its tools; see ``nudge.conversation._chat_record``
    :return: generator of events: ``start``, then ``delta`` and what tools do
        on the screen in any number (``fallback`` when the model chosen to stand
        in answers), then ``done``, ``waiting`` or ``error``
    """
    yield {
        "type": "start",
        "conversation_id": job["conversation_id"],
        "message_id": job["user_message_id"],
        "title": job["title"],
        "version": job.get("version", ""),
    }
    # What was done before the model is called: a decision of the person
    yield from job.get("first_events") or []
    if job.get("answered"):
        # Answered from the memory of answers: no provider is called
        yield job["answered"]
        return
    if job.get("queued"):
        # The work is left to the background: nothing to wait for here
        yield {"type": "queued", "run_id": job["queued"], "state": "queued", "message": ""}
        return
    if job.get("fell_back"):
        # NP-10: the model chosen to stand in answers, and the person is told
        yield job["fell_back"]
    cancelled = job.get("cancelled")
    # For the latency panel: the wait in line goes with the first answer stored, the time to
    # the first words with the answer that has them
    queue_wait = int(job.get("queue_wait_ms") or 0)
    received = job.get("received")
    first_line = None
    started = time.monotonic()
    deadline = started + job["seconds"]
    turns = job["turns"]
    said = acted = False
    forced = job.get("tool_choice") if job["tools"] else None
    last_duration = 0.0
    round_number = 0
    while round_number < job["rounds"]:
        round_number += 1
        if round_number > 1 and not time_for_a_round(deadline, last_duration):
            # Out of time: what was read is kept, and the person is told
            # rather than left waiting for a call that would be cut
            step = record({
                "text": "", "tool_calls": [], "said_before": said, "duration_ms": 0,
                "stop": base.stop_event(base.STOP_DEADLINE), "last_round": True,
                "serial": job.get("serial"), "fell_back": job.get("fell_back"),
            })
            yield from step.get("screen_events") or []
            yield step["event"]
            return
        round_started = time.monotonic()
        # The last round is for the answer: what was read is told, rather
        # than one more call left without a round to read its result
        last = round_number == job["rounds"] > 1 and bool(job["tools"])
        if round_number <= FORCED_ROUNDS < job["rounds"]:
            tool_choice = forced
        elif last:
            tool_choice = "none"
        else:
            tool_choice = None
        sent = [*turns, {"role": "user", "text": LAST_ROUND_NOTE}] if last else turns
        spent, retries = {}, EMPTY_RETRIES
        while True:
            # Pushed only when it answered in words alone: once a tool was
            # called, what it says is about what the tool did
            outcome = {"text": "", "tool_calls": [], "said_before": said,
                       "may_push": bool(job.get("push_to_act")) and not job.get("pushed") and not acted,
                       # Work left by what was said yes to: a question is no answer to it
                       "push_on_question": bool(job.get("push_on_question")),
                       # The number of the answer, which a stop names (PERF-02)
                       "serial": job.get("serial"),
                       # NP-10: the model standing in is asked once, before anything was said or done
                       "may_fall_back": round_number == 1 and not said and not job.get("fell_back"),
                       "fell_back": job.get("fell_back"),
                       "queue_wait_ms": queue_wait}
            try:
                events = job["adapter"].stream_chat(
                    job["system"], sent, deadline=deadline, tools=job["tools"] or None,
                    tool_choice=tool_choice)
                if cancelled:
                    events = _read(events, cancelled, deadline)
                for event in events:
                    if event["type"] == "text":
                        if first_line is None and received is not None:
                            first_line = outcome["first_line_ms"] = int((time.monotonic() - received) * 1000)
                        if said and not outcome["text"]:
                            # What follows a tool is a new paragraph
                            yield {"type": "delta", "text": "\n\n"}
                        outcome["text"] += event["text"]
                        yield {"type": "delta", "text": event["text"]}
                    elif event["type"] == "tool_use":
                        outcome["tool_calls"].append(event)
                    elif event["type"] in ("stop", "error"):
                        outcome[event["type"]] = event
            except GeneratorExit:
                # The browser closed the connection: keep what was generated
                outcome.update(aborted=True, last_round=True)
                outcome["duration_ms"] = int((time.monotonic() - round_started) * 1000)
                record(outcome)
                raise
            except Stopped:
                # PERF-02: the person stopped it, through another worker: what was written is kept,
                # the provider's stream is let go, and the answer ends here as stopped
                outcome.update(aborted=True, last_round=True, tool_calls=[])
                outcome["duration_ms"] = int((time.monotonic() - round_started) * 1000)
                step = record(outcome)
                yield step.get("event") or {"type": "done", "state": "aborted", "message": ""}
                return
            except Exception:  # noqa: BLE001
                _logger.exception("Nudge: stream failed for conversation %s", job["conversation_id"])
                outcome["error"] = base.error_event(base.ERROR_UNKNOWN)
            if "stop" not in outcome and "error" not in outcome:
                outcome["error"] = base.error_event(base.ERROR_UNKNOWN, "stream ended without a result")
            if not (retries and is_empty(outcome) and time_for_a_round(deadline, 0)):
                break
            retries -= 1
            for key, value in outcome["stop"]["usage"].items():
                spent[key] = spent.get(key, 0) + value
            _logger.info(
                "Nudge: empty answer in round %s for conversation %s, asked once more",
                round_number, job["conversation_id"])
        if spent and "stop" in outcome:
            outcome["stop"] = add_usage(outcome["stop"], spent)
        last_duration = time.monotonic() - round_started
        outcome["duration_ms"] = int(last_duration * 1000)
        outcome["last_round"] = round_number == job["rounds"] or base.expired(deadline)
        if outcome["tool_calls"] and cancelled and cancelled():
            # Stopped while the model wrote its calls: none of them is run
            outcome.update(aborted=True, last_round=True, tool_calls=[])
        said = said or bool(outcome["text"])
        # Asking for more tools is not acting: what it says after may still be talk
        acted = acted or any(call.get("name") not in ("more_tools", "use_skill") for call in outcome["tool_calls"])

        step = record(outcome)
        if not step.get("fallback"):
            queue_wait = 0
        if step.get("fallback"):
            # NP-10: the provider could not answer; the model chosen to stand in is asked the
            # same, once, in the same round
            switch = step["fallback"]
            job.update(adapter=switch["adapter"], system=switch["system"], tools=switch["tools"],
                       fell_back=switch["notice"])
            turns = switch["turns"]
            yield switch["notice"]
            round_number -= 1
            continue
        if any(event.get("type") in SHOWN_EVENTS for event in step.get("screen_events") or []):
            forced = None
        yield from step.get("screen_events") or []
        if "event" in step:
            yield step["event"]
            return
        turns = step["turns"]
        if step.get("pushed"):
            job["pushed"] = True
            turns = [*turns, {"role": "user", "text": PUSH_NOTE}]
        if step.get("tools"):
            # A small model asked for more tools: offered from now on
            job["tools"] = step["tools"]
        if step.get("skills"):
            # NP-03: the skill the model asked for with use_skill joins its instructions from now on
            job["system"] = prompt.with_skills(job["system"], loaded=step["skills"])

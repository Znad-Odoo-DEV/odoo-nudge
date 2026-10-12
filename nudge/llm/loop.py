"""The exchange between the model and Odoo for one question.

The model answers; when it calls tools, Odoo runs them and the model is
called again to read what they returned, until it has nothing more to ask
or the number of rounds is used up.

Written as a generator of events for the browser, with no Odoo import: the
caller says how an answer is recorded, which is where the database is.
"""
import logging
import time

from . import base

_logger = logging.getLogger(__name__)

# When the person asked for the pointer, the model must reach for a tool: for
# this many rounds, or until something is shown on the screen
FORCED_ROUNDS = 2
SHOWN_EVENTS = ("guidance", "navigate")
# A round is not started when the time left would not see it through: this
# long at least, or most of what the round before took
MIN_ROUND_SECONDS = 15


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
        on the screen in any number, then ``done``, ``waiting`` or ``error``
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
    if job.get("queued"):
        # The work is left to the background: nothing to wait for here
        yield {"type": "queued", "run_id": job["queued"], "state": "queued", "message": ""}
        return
    started = time.monotonic()
    deadline = started + job["seconds"]
    turns = job["turns"]
    said = False
    forced = job.get("tool_choice") if job["tools"] else None
    last_duration = 0.0
    for round_number in range(1, job["rounds"] + 1):
        if round_number > 1 and not time_for_a_round(deadline, last_duration):
            # Out of time: what was read is kept, and the person is told
            # rather than left waiting for a call that would be cut
            step = record({
                "text": "", "tool_calls": [], "said_before": said, "duration_ms": 0,
                "stop": base.stop_event(base.STOP_DEADLINE), "last_round": True,
            })
            yield from step.get("screen_events") or []
            yield step["event"]
            return
        round_started = time.monotonic()
        outcome = {"text": "", "tool_calls": [], "said_before": said}
        # The last round is for the answer: what was read is told, rather
        # than one more call left without a round to read its result
        if round_number <= FORCED_ROUNDS < job["rounds"]:
            tool_choice = forced
        elif round_number == job["rounds"] > 1 and job["tools"]:
            tool_choice = "none"
        else:
            tool_choice = None
        try:
            events = job["adapter"].stream_chat(
                job["system"], turns, deadline=deadline, tools=job["tools"] or None,
                tool_choice=tool_choice)
            for event in events:
                if event["type"] == "text":
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
        except Exception:  # noqa: BLE001
            _logger.exception("Nudge: stream failed for conversation %s", job["conversation_id"])
            outcome["error"] = base.error_event(base.ERROR_UNKNOWN)
        if "stop" not in outcome and "error" not in outcome:
            outcome["error"] = base.error_event(base.ERROR_UNKNOWN, "stream ended without a result")
        last_duration = time.monotonic() - round_started
        outcome["duration_ms"] = int(last_duration * 1000)
        outcome["last_round"] = round_number == job["rounds"] or base.expired(deadline)
        said = said or bool(outcome["text"])

        step = record(outcome)
        if any(event.get("type") in SHOWN_EVENTS for event in step.get("screen_events") or []):
            forced = None
        yield from step.get("screen_events") or []
        if "event" in step:
            yield step["event"]
            return
        turns = step["turns"]

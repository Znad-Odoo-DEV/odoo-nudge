# Busy times, Stop and the fallback model

## The waiting room (NP-04)

One answer holds one worker of the server while it streams, so the answers to everybody together
are capped (**Settings ‣ Nudge ‣ Budgets ‣ Answers at the Same Time**; 0 is the workers of the
server less two). Past the cap a question is not refused any more:

- it waits in line, and nothing of it is kept on the server but its place: no worker waits with it;
- the panel shows "You are number 2 in line (about 30 s)" with **Cancel**; Stop cancels too;
- when an answer ends, the first in line is told through the bus and its question is sent again
  by itself; the browser also asks where it stands every few seconds;
- after two minutes in line the person is offered **Try again**, with the question kept;
- one place per person: asked again, the question keeps the place of the last one;
- **Settings ‣ Nudge ‣ Busy Times and Fallback ‣ Questions Waiting at Most** (20): past it, a
  question is refused as before. A browser that stops asking for 20 seconds gives its place up,
  and a turn not taken in 20 seconds passes to the next.

A provider may give fewer answers at once: **Answers at Once** on the provider (0 is automatic:
one for Ollama, which answers one question at a time on one machine, otherwise only the cap of
Nudge). Behind a provider that is full, a question for another provider goes first.

Requests understood by rules and remembered answers never wait: no provider is called. Work in
the background does not count. A person sees only their own place, never anything of the others.

Each answer keeps how long its question waited in line (`queue_wait_ms`) and the time from the
question to its first words (`first_line_ms`), in milliseconds, for the latency panel.

The places are claimed under one advisory lock, in a transaction committed at once, so that every
worker sees them before the question itself is committed: two questions arriving together cannot
take the last place, nor one person start two answers from two tabs.

## Stop (PERF-02)

Stop writes in the database which answer was stopped. The answer reads it every second, even
while the provider says nothing (the provider is read in a thread of its own), and before running
the tools the model asked for. The worker is let go at once, the place goes to the next in line,
the person can ask again at once, and what was written is kept as stopped. A provider that is
still silent five seconds past the deadline of the answer is not waited for either.

## The fallback model (NP-10)

**Settings ‣ Nudge ‣ Busy Times and Fallback ‣ Fallback Model** names a provider that answers in
place of the usual one, once:

- when the provider fails before it said or did anything (no answer, a rejected key, a model it
  does not know, its limit reached, a time-out); not when it refused the request;
- when the provider named for the mode has no key or is archived;
- when the budget of a person is used up, only if the fallback runs on your own server (it costs
  nothing, its tokens are still counted);
- in private mode, when the provider named for the mode is outside: only if the fallback runs on
  your own server. A fallback outside is never used in private mode.

Each answer it gives says "Answered by the local model (name)", also when the conversation is
read again. It reads what the usual provider would have read, no more. The next question goes to
the usual provider again. Without a fallback, the person is told why there is no answer and the
requests understood without AI still work.

Nothing goes to a provider the administrator did not choose: the provider named for the mode, the
default, or the fallback.

## For load tests (LEAD-02)

Each mode follows its own provider: **Default Provider** answers Talk, **Agent Provider** answers
Agent mode and the scheduled tasks, **Developer Provider** answers Developer mode. A test copy of a
database keeps the providers it was copied with: point all three at the mock provider (and remove
the keys of the others) before a load test. The audit of 2026-10-22 pointed only the default at
the mock, so its Agent and Developer questions were answered by the provider the copied database
named for them.

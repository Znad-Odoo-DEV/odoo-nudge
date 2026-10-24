"""Work a scheduled action does a batch at a time, until it is all done or its time is up."""
import time

# A pass works this long at most: the rest is taken up by the next pass, triggered at once
PASS_SECONDS = 60


def in_batches(env, cron_xmlid, step, batch, seconds=PASS_SECONDS):
    """Call ``step(batch)``, which does at most ``batch`` items and returns how many it did, until
    one does fewer (nothing is left) or the time of the pass is up. Each full batch is committed;
    what is left when the time is up is taken up by a new pass of the scheduled action
    ``cron_xmlid``, triggered at once.

    :return: how many items in all"""
    Cron = env["ir.cron"]
    started = time.monotonic()
    total = 0
    while True:
        begun = time.monotonic()
        done = step(batch)
        total += done
        if done < batch:
            return total
        left = min(Cron._commit_progress(done), seconds - (time.monotonic() - started))
        if left <= time.monotonic() - begun:
            # Not the time for another batch
            env.ref(cron_xmlid).sudo()._trigger()
            return total

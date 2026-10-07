"""Building of a version of the profile, and what changed since the last one."""
import hashlib
import json
import time

from . import collectors

KEPT_VERSIONS = 5


def dump(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def digest(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()


def fingerprint(env):
    """Changes when a module is installed, removed or upgraded."""
    modules = env["ir.module.module"].sudo().search_read(
        [("state", "=", "installed")], ["name", "latest_version"], order="name")
    return digest([(module["name"], module["latest_version"]) for module in modules])


def compare(previous, current):
    """:param previous: {key: hash} of the last version
    :param current: {key: hash} of the new one
    :return: what was added, removed and changed, by key; the use of the
        data changes every day and is not a change of the database"""
    previous = {key: value for key, value in previous.items() if not key.startswith("activity:")}
    current = {key: value for key, value in current.items() if not key.startswith("activity:")}
    return {
        "added": sorted(set(current) - set(previous)),
        "removed": sorted(set(previous) - set(current)),
        "changed": sorted(key for key in set(current) & set(previous) if current[key] != previous[key]),
    }


def build(env, trigger="manual"):
    """:return: the new current ``nudge.profile``"""
    started = time.monotonic()
    Profile = env["nudge.profile"].sudo()
    previous = Profile._get_current()

    slices = collectors.collect(env)
    for piece in slices:
        piece["content_hash"] = digest([piece["title"], piece["summary"], piece["data"]])
    overrides = {
        piece.key: piece.override_summary
        for piece in previous.slice_ids if piece.override_summary
    }
    overview = next((piece for piece in slices if piece["key"] == "overview"), None)

    profile = Profile.create({
        "trigger": trigger,
        "fingerprint": fingerprint(env),
        "overview_text": overview["summary"] if overview else "",
        "changes": compare(
            {piece.key: piece.content_hash for piece in previous.slice_ids},
            {piece["key"]: piece["content_hash"] for piece in slices},
        ) if previous else False,
        "slice_ids": [
            (0, 0, dict(piece, override_summary=overrides.get(piece["key"]) or False))
            for piece in slices
        ],
    })
    previous.write({"is_current": False})
    profile.write({
        "is_current": True,
        "duration_ms": int((time.monotonic() - started) * 1000),
    })
    Profile.search([("is_current", "=", False)], order="id desc", offset=KEPT_VERSIONS - 1).unlink()
    return profile

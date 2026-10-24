"""The signed feed of the country packs: checking a signature and telling what changed.

A feed is a JSON envelope ``{"format": 1, "payload": "<text>", "signature": "<base64>"}``. The payload is a text
(so that the bytes that were signed are the bytes that are checked) holding ``{"issued": "YYYY-MM-DD", "packs": [..]}``,
each pack in the form of ``data/compliance``. The signature is an Ed25519 signature of the payload text, checked
with the public key an administrator pinned in the settings. Nothing in a feed is code: a pack is data that
``compliance.load_pack`` validates like any other.
"""
import base64
import binascii
import json
import re

MAX_FEED_BYTES = 1024 * 1024
MAX_PACKS = 30
FORMAT = 1


class FeedError(ValueError):
    """A feed that is refused; the message says why in a few words."""


def _crypto():
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:  # pragma: no cover - Odoo needs cryptography
        raise FeedError("The Python package cryptography is missing on the server.") from None
    return InvalidSignature, serialization, Ed25519PublicKey


def parse_public_key(text):
    """:return: the Ed25519 public key written as PEM, or as the 32 bytes in base64
    :raise FeedError: when it is neither"""
    _invalid, serialization, Ed25519PublicKey = _crypto()
    text = (text or "").strip()
    if not text:
        raise FeedError("No public key is set.")
    try:
        if text.startswith("-----BEGIN"):
            key = serialization.load_pem_public_key(text.encode())
        else:
            key = Ed25519PublicKey.from_public_bytes(base64.b64decode(re.sub(r"\s+", "", text), validate=True))
    except (ValueError, binascii.Error, TypeError):
        raise FeedError("The public key is not an Ed25519 key (PEM, or 32 bytes in base64).") from None
    if not isinstance(key, Ed25519PublicKey):
        raise FeedError("The public key is not an Ed25519 key (PEM, or 32 bytes in base64).")
    return key


def verify(raw, public_key_text):
    """:param raw: the bytes the feed address answered
    :return: ``(issued, [pack objects])``, only when the signature is the one of the pinned key
    :raise FeedError: when the envelope is malformed, the signature is not valid or the payload is not as described"""
    invalid, _serialization, _key = _crypto()
    key = parse_public_key(public_key_text)
    if len(raw) > MAX_FEED_BYTES:
        raise FeedError("The feed is too large.")
    try:
        envelope = json.loads(raw)
        payload = envelope["payload"]
        signature = base64.b64decode(envelope["signature"], validate=True)
    except (ValueError, KeyError, TypeError, binascii.Error):
        raise FeedError("The feed is not a signed envelope.") from None
    if envelope.get("format") != FORMAT or not isinstance(payload, str):
        raise FeedError("The feed is not a signed envelope.")
    try:
        key.verify(signature, payload.encode("utf-8"))
    except invalid:
        raise FeedError("The signature is not valid for the pinned public key.") from None
    try:
        data = json.loads(payload)
    except ValueError:
        raise FeedError("The payload is not JSON.") from None
    packs = data.get("packs") if isinstance(data, dict) else None
    issued = data.get("issued") if isinstance(data, dict) else None
    if not isinstance(packs, list) or not packs or len(packs) > MAX_PACKS or not all(isinstance(p, dict) for p in packs):
        raise FeedError("The payload lists no pack, or too many.")
    return (issued if isinstance(issued, str) else ""), packs


def sign(payload_text, private_key):
    """:return: the envelope (a text) of ``payload_text`` signed by the Ed25519 ``private_key`` object. Used by
    ``scripts/sign_pack_feed.py`` and by the tests, with a key that lives in memory only."""
    signature = private_key.sign(payload_text.encode("utf-8"))
    return json.dumps({"format": FORMAT, "payload": payload_text,
                       "signature": base64.b64encode(signature).decode()}, ensure_ascii=False)


def _by_key(pack):
    return {entry.key: entry for entry in pack.checks}


def changes(old, new):
    """:return: what differs between two loaded packs, as a list of ``(sign, key, what, before, after)``; ``old``
    is None for a pack that is new. Signs: ``+`` added, ``-`` removed, ``~`` changed."""
    if old is None:
        return [("+", entry.key, "", "", entry.deadline) for entry in new.checks]
    out = []
    before, after = _by_key(old), _by_key(new)
    for key, entry in after.items():
        if key not in before:
            out.append(("+", key, "", "", entry.deadline))
            continue
        was = before[key]
        for what in ("deadline", "severity", "source", "reviewed"):
            if getattr(was, what) != getattr(entry, what):
                out.append(("~", key, what, getattr(was, what), getattr(entry, what)))
        for what in ("title", "explanation", "reference"):
            if getattr(was, what) != getattr(entry, what):
                out.append(("~", key, what, "", ""))
        if was.check != entry.check or was.params != entry.params:
            out.append(("~", key, "check", was.check, entry.check))
    out += [("-", key, "", "", "") for key in before if key not in after]
    return out

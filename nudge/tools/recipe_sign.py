"""Signed recipes (S2): who made this file, and was it changed.

A recipe file may carry ``"signature": {"format": 1, "algorithm": "ed25519", "key_id": "<16 hex>",
"value": "<base64>"}``, an Ed25519 signature of the canonical JSON of ``recipe`` (the same bytes the checksum is
made of). The signature names the key by its fingerprint, never the publisher: the name shown is the one of the
trusted publisher whose key it verifies against. The primitives are the ones of the signed pack feed (NP-08,
``tools/compliance_feed.py``): the key is read by ``parse_public_key`` and the signature is made by ``sign``.
"""
import base64
import binascii
import hashlib
import json
import re

from . import compliance_feed
from .compliance_feed import FeedError

FORMAT = 1
ALGORITHM = "ed25519"
KEY_ID = re.compile(r"^[0-9a-f]{16}$")


def canonical(body):
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def key_id_of(public_key_text):
    """:return: the fingerprint (16 hex) of an Ed25519 public key given as PEM or base64
    :raise FeedError: when it is not one"""
    _invalid, serialization, _key = compliance_feed._crypto()
    key = compliance_feed.parse_public_key(public_key_text)
    raw = key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()[:16]


def load_private(pem_text):
    """:return: ``(private key object, its public key as PEM)``
    :raise FeedError: when the text is not an unprotected Ed25519 private key; the text is never quoted"""
    _invalid, serialization, _key = compliance_feed._crypto()
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: PLC0415
    try:
        key = serialization.load_pem_private_key((pem_text or "").strip().encode(), password=None)
    except (ValueError, TypeError, UnicodeError):
        raise FeedError("This is not an unprotected Ed25519 private key in PEM form.") from None
    if not isinstance(key, Ed25519PrivateKey):
        raise FeedError("This is not an unprotected Ed25519 private key in PEM form.")
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    return key, public.decode()


def sign(body, private_pem):
    """:return: the ``signature`` object for the recipe ``body``, made with the PEM private key"""
    key, public_pem = load_private(private_pem)
    envelope = json.loads(compliance_feed.sign(canonical(body), key))
    return {"format": FORMAT, "algorithm": ALGORITHM, "key_id": key_id_of(public_pem), "value": envelope["signature"]}


def well_formed(signature):
    """:return: whether ``signature`` is an object shaped like one of ours (nothing is checked yet)"""
    return (isinstance(signature, dict) and signature.get("format") == FORMAT
            and signature.get("algorithm") == ALGORITHM and isinstance(signature.get("key_id"), str)
            and bool(KEY_ID.match(signature["key_id"])) and isinstance(signature.get("value"), str))


def verify(body, signature, public_key_text):
    """:return: True when ``signature`` is the signature of ``body`` by that public key, False when it is not
    (a changed body, another key, a damaged value)"""
    invalid, _serialization, _key = compliance_feed._crypto()
    try:
        key = compliance_feed.parse_public_key(public_key_text)
        value = base64.b64decode(signature["value"], validate=True)
        key.verify(value, canonical(body).encode("utf-8"))
    except (FeedError, KeyError, TypeError, ValueError, binascii.Error, invalid):
        return False
    return True

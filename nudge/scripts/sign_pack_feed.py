"""Sign a feed of country packs for Nudge (NP-08).

    python -I sign_pack_feed.py --key /path/to/ed25519_private.pem --payload feed.json --out signed_feed.json

``feed.json`` is ``{"issued": "YYYY-MM-DD", "packs": [<pack>, ...]}``, each pack in the form of
``addons/nudge/data/compliance``. The output is the envelope the feed address serves. The private key is read
from the path given and is never written, printed or generated here: keep it off the repository and the server.
The public key (PEM, or the 32 bytes in base64) is what the administrators paste into the Nudge settings.
"""
import argparse
import base64
import json
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--key", required=True, help="path of the Ed25519 private key (PEM)")
    parser.add_argument("--payload", required=True, help="the feed to sign (JSON)")
    parser.add_argument("--out", required=True, help="where the signed envelope is written")
    args = parser.parse_args(argv)
    with open(args.key, "rb") as handle:
        key = serialization.load_pem_private_key(handle.read(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        sys.exit("The key is not an Ed25519 private key.")
    with open(args.payload, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("packs"), list) or not data["packs"]:
        sys.exit("The feed needs a list of packs.")
    text = json.dumps(data, ensure_ascii=False, sort_keys=True)
    signature = base64.b64encode(key.sign(text.encode("utf-8"))).decode()
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump({"format": 1, "payload": text, "signature": signature}, handle, ensure_ascii=False)
    print("Signed %d pack(s) -> %s" % (len(data["packs"]), args.out))


if __name__ == "__main__":
    main()

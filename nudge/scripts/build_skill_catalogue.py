"""Build the skill catalogue that ships with Nudge (S2), signed with the Nudge publisher key.

    python -I build_skill_catalogue.py --key /path/to/ed25519_private.pem

Reads ``skill_catalogue_src.json`` (a list of skills: key, title, summary, instructions, tools, domain, languages...)
and writes ``../data/skill_catalogue.json``: for each skill one recipe, as ``nudge.recipe`` exports it, with its
checksum and an Ed25519 signature of the canonical JSON of the recipe (``tools/recipe_sign.py``). The private key
is read from the path given and is never written, printed or generated here: keep it off the repository.
Change an instruction, build again, commit the data file: a file changed by hand no longer verifies and is not listed.
"""
import argparse
import base64
import hashlib
import json
import os
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

HERE = os.path.dirname(os.path.abspath(__file__))
CREATED = "2026-10-24 00:00:00"


def canonical(body):
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--key", required=True, help="path of the Ed25519 private key (PEM)")
    parser.add_argument("--src", default=os.path.join(HERE, "skill_catalogue_src.json"))
    parser.add_argument("--out", default=os.path.join(HERE, "..", "data", "skill_catalogue.json"))
    args = parser.parse_args(argv)
    with open(args.key, "rb") as handle:
        key = serialization.load_pem_private_key(handle.read(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        sys.exit("The key is not an Ed25519 private key.")
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    key_id = hashlib.sha256(public).hexdigest()[:16]
    with open(args.src, encoding="utf-8") as handle:
        skills = json.load(handle)
    packages = []
    for data in skills:
        data = dict(data, test={"sentence": "", "expect": ""})
        body = {
            "format": 1, "nudge_version": "19.0.1.58.0", "odoo_series": "", "created": CREATED, "company": "Nudge",
            "items": [{"kind": "skill", "key": "nudge-skill-" + data["key"], "name": data["title"]["en"], "data": data}],
        }
        signature = base64.b64encode(key.sign(canonical(body).encode("utf-8"))).decode()
        packages.append({
            "recipe": body, "checksum": hashlib.sha256(canonical(body).encode("utf-8")).hexdigest(),
            "signature": {"format": 1, "algorithm": "ed25519", "key_id": key_id, "value": signature}})
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump({"format": 1, "skills": packages}, handle, ensure_ascii=False, indent=1, sort_keys=True)
        handle.write("\n")
    print("Signed %d skill(s) with key %s -> %s" % (len(packages), key_id, os.path.normpath(args.out)))


if __name__ == "__main__":
    main()

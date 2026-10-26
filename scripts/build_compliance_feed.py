"""Build the unsigned feed of Nudge country packs, ready to be signed.

    <odoo venv python> scripts/build_compliance_feed.py

Reads every pack of ``addons/nudge/data/compliance/*.json`` and ``feed/packs/*.json`` (a pack of ``feed/packs``
replaces the bundled one of the same id), checks each one with ``tools/compliance.load_pack`` as
``scripts/validate_packs.py`` does, and writes ``feed/feed.json`` = ``{"issued": "<today>", "packs": [...]}`` sorted
by id. Nothing is signed here: the command to run with the publisher key (kept off the repository) is printed.
Exits 1, writing nothing, when a pack is refused. The Odoo 19 source is found as in ``validate_packs.py``
(``ODOO_SOURCE`` or ``../odoo/odoo_bootcamp``).
"""
import glob
import json
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
ODOO = os.environ.get("ODOO_SOURCE") or os.path.join(ROOT, "..", "odoo", "odoo_bootcamp")
BUNDLED = os.path.join(ROOT, "addons", "nudge", "data", "compliance", "*.json")
DRAFTS = os.path.join(ROOT, "feed", "packs", "*.json")
OUT = os.path.join(ROOT, "feed", "feed.json")


def main():
    sys.path.insert(0, ODOO)
    import odoo.addons
    odoo.addons.__path__.append(os.path.join(ROOT, "addons"))
    from odoo.addons.nudge.tools import compliance

    packs = {}
    refused = 0
    for source, pattern in (("bundled", BUNDLED), ("feed", DRAFTS)):
        for path in sorted(glob.glob(pattern)):
            name = os.path.basename(path)
            try:
                with open(path, encoding="utf-8") as handle:
                    data = json.load(handle)
                pack = compliance.load_pack(data, name)
            except (ValueError, compliance.PackError) as error:
                refused += 1
                print(f"REFUSED {source}/{name}: {error}")
                continue
            if data.get("id") in packs and source == "feed":
                print(f"{source}/{name}: replaces the bundled pack {data['id']}")
            packs[data.get("id")] = data
            print(f"ok {source}/{name}: {len(pack.checks)} checks for {', '.join(pack.countries)}")
    if refused:
        print(f"{refused} pack(s) refused: nothing written.")
        return 1
    feed = {"issued": date.today().isoformat(), "packs": [packs[key] for key in sorted(packs)]}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(feed, handle, ensure_ascii=False, indent=1, sort_keys=True)
    print(f"Wrote {len(feed['packs'])} pack(s) -> feed/feed.json")
    print("Now sign it with the publisher key, kept off the repository:")
    print("  python -I addons/nudge/scripts/sign_pack_feed.py --key <path to the private key> "
          "--payload feed/feed.json --out feed/compliance/feed.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

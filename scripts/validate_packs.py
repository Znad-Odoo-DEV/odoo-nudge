"""Check country packs with the loader Nudge itself uses (``tools/compliance.load_pack``), without a database.

    <odoo venv python> scripts/validate_packs.py feed/packs/*.json

Prints one line per file: ok with the number of checks, or REFUSED with the reason the loader gives. Exits 1 when
a file is refused. The Odoo 19 source is looked for next to this repository (``../odoo/odoo_bootcamp``), or in
the folder named by ``ODOO_SOURCE``.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ODOO = os.environ.get("ODOO_SOURCE") or os.path.join(HERE, "..", "..", "odoo", "odoo_bootcamp")


def main(paths):
    sys.path.insert(0, ODOO)
    import odoo.addons
    odoo.addons.__path__.append(os.path.join(HERE, "..", "addons"))
    from odoo.addons.nudge.tools import compliance

    refused = 0
    for path in paths:
        try:
            with open(path, encoding="utf-8") as handle:
                pack = compliance.load_pack(json.load(handle), os.path.basename(path))
        except (ValueError, compliance.PackError) as error:
            refused += 1
            print(f"REFUSED {error}")
            continue
        print(f"ok {os.path.basename(path)}: {len(pack.checks)} checks for {', '.join(pack.countries)}")
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

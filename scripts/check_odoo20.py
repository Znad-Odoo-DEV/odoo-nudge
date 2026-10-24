"""Grep-level Odoo 20 readiness check of the Nudge modules (stdlib only, read only).

    python -I scripts/check_odoo20.py [folder] [--fail-on-new N] [--summary]

`folder` defaults to ``addons`` next to this script's repo root; every ``nudge*`` module in it is scanned.
It prints one line per finding (``module/file:line  RULE  text``) and a count per rule. It reports, it does not
fail the build: the exit code is 0, unless ``--fail-on-new N`` is given and there are more than N findings.
The rules come from docs/odoo20_readiness.md (each marked there "verified in code" or "per the Odoo 20 notes").
"""
import argparse
import io
import os
import re
import sys

KNOWN_FINDINGS = 458  # findings of this script on the tree it was written against (see docs/odoo20_readiness.md)

EXT = (".py", ".xml", ".js", ".csv", ".scss")
SKIP_DIRS = {"lib", "i18n", "node_modules", "__pycache__", ".git", "doc", "docs"}

# (id, file suffixes, regex, what Odoo 20 wants, effort)
RULES = [
    ("ACCESS_CSV", (".csv",), None, "ir.model.access.csv becomes ir.access.csv (id,name,model_id,group_id/id,operation,domain)", "M"),
    ("IR_RULE", (".xml",), r'model="ir\.rule"', "ir.rule merged into ir.access (domain on the access right)", "L"),
    ("MODEL_ACCESS_REF", (".py",), r'ir\.model\.access(?!\.csv)', "code that reads or writes ir.model.access must target ir.access", "M"),
    ("OWL_STATE", (".js",), r'\buseState\(|\breactive\(', "OWL 3: useState/reactive replaced by proxy", "M"),
    ("OWL_T_ESC", (".xml",), r'\bt-esc=', "OWL 3 lists t-esc among its breaking changes (t-out is the long-standing equivalent)", "S"),
    ("OWL_REMOVED", (".js",), r'\b(useComponent|useExternalListener|onWillUpdateProps)\b', "OWL 3 removed or renamed hooks", "M"),
    ("OWL_PROPS_ENV", (".js",), r'\bthis\.(props|env)\b', "OWL 3: this.props / this.env removed", "L"),
    ("OWL_PORTAL", (".xml",), r'\bt-portal=', "OWL 3: t-portal changed", "S"),
    ("READ_GROUP_OLD", (".py", ".js"), r'(?<![_\w"])read_group\(|\.readGroup\(|webReadGroup', "read_group has a new signature (domain, groupby, aggregates, having, offset, limit, order)", "M"),
    ("TRACKING_VALUE", (".py",), r'mail\.tracking\.value', "tracking values removed (generated on the fly; mail_tracking module)", "L"),
    ("FA_ICON", (".xml", ".js", ".scss"), r'\bfa-[a-z]|\bfa fa-', "Font Awesome replaced by Material Symbols", "S"),
    ("JSONRPC_ROUTE", (".py", ".js"), r'/jsonrpc|/xmlrpc', "/jsonrpc and /xmlrpc deprecated (db service removed); JSON-2 /json/2", "S"),
    ("ROUTE_TYPE_JSON", (".py",), r"""type=['"]json['"]""", "routes type='json' become 'jsonrpc' (18.1)", "S"),
    ("DEPRECATED_ORM", (".py",), r'\b(_check_access|_check_field_access|_init_column|_table_has_rows|_table_query)\b', "removed or renamed ORM methods", "M"),
    ("CR_UID_CONTEXT", (".py",), r'\b\w+\._(cr|uid|context)\b', "record._cr/_uid/_context deprecated since 19.0", "S"),
    ("ATTRS_STATES", (".xml",), r'\s(attrs|states)="', "attrs/states are refused since 17.0", "S"),
    ("TREE_VIEW", (".xml",), r'<tree[\s>]|view_mode">[^<]*\btree\b', "view type tree is list since 17.5", "S"),
    ("REMOVED_DEPENDS", ("__manifest__.py",), r'["\'](base_vat|base_iban|stock_picking_batch|website_sale_wishlist|website_sale_comparison|hr_org_chart|hr_homeworking|delivery_mondialrelay|transifex|hr_work_entry_holidays)["\']', "module removed or merged in 20.0: fix depends", "S"),
]
RULES = [(i, s, re.compile(r) if r else None, w, e) for i, s, r, w, e in RULES]


def files(root):
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for n in names:
            if n.endswith(EXT):
                yield os.path.join(base, n)


def scan(folder):
    """Yield (path, line number, rule id, effort, text) for every finding of every nudge* module."""
    for module in sorted(os.listdir(folder)):
        mdir = os.path.join(folder, module)
        if not module.startswith("nudge") or not os.path.isfile(os.path.join(mdir, "__manifest__.py")):
            continue
        for path in files(mdir):
            rel = os.path.relpath(path, folder).replace(os.sep, "/")
            name = os.path.basename(path)
            try:
                lines = io.open(path, encoding="utf-8").read().splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for rule, suffixes, rx, _what, effort in RULES:
                if not any(name == s or (s.startswith(".") and name.endswith(s)) for s in suffixes):
                    continue
                if rx is None:  # one finding per file, by name
                    if name == "ir.model.access.csv":
                        yield rel, 1, rule, effort, "ir.model.access.csv"
                    continue
                for number, line in enumerate(lines, 1):
                    if rx.search(line):
                        yield rel, number, rule, effort, line.strip()[:110]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("folder", nargs="?", default=os.path.join(here, "..", "addons"))
    parser.add_argument("--fail-on-new", type=int, metavar="COUNT", help="exit 1 when there are more than COUNT findings")
    parser.add_argument("--summary", action="store_true", help="print only the count per rule")
    args = parser.parse_args()
    found = list(scan(os.path.abspath(args.folder)))
    if not args.summary:
        for rel, number, rule, effort, text in found:
            print(f"{rel}:{number}  {rule}  {text}")
    counts = {}
    for _rel, _n, rule, effort, _t in found:
        counts[rule] = counts.get(rule, 0) + 1
    print("\n== per rule (rule, effort, findings)")
    efforts = {r[0]: r[4] for r in RULES}
    for rule, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"{rule:18} {efforts[rule]}  {count}")
    print(f"== total {len(found)} findings (known: {KNOWN_FINDINGS})")
    if args.fail_on_new is not None and len(found) > args.fail_on_new:
        print(f"FAIL: {len(found)} findings, more than the {args.fail_on_new} allowed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

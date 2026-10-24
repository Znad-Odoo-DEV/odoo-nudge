"""Run the Nudge quality gate on a database and compare the release with the one before.

    python scripts/quality_gate.py --db nudge_gate --odoo-dir <odoo checkout> [--addons-path ...]

It runs ``--test-tags /nudge:TestQualityGate`` (scenarios of ``addons/nudge/tests/scenarios``, scripted
with the fake adapter: no provider, no Ollama, no network), reads the report the test class writes
(``NUDGE_GATE_REPORT``), computes passed/total overall, per mode and per language, and writes
``<out>/<version>.json`` and ``<out>/<version>.md``. The baseline is the newest report in ``<out>``
of another version (or ``--baseline FILE``). Exit status: 0 when no scenario that passed before fails
now and the score did not drop, 1 otherwise, 2 when the run itself failed.

Use ``--report FILE`` to skip the run and only score a report already written.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "addons" / "nudge" / "__manifest__.py"
DEFAULT_OUT = ROOT / "docs" / "quality_gate"
LOCK_TOOL = Path("C:/Users/Ibrahim Elzenad/SSC_LLC_dev0/tools/nudge_audit/odoo_lock.py")
MODES = ("talk", "agent", "dev")
LANGUAGES = ("en", "ar", "arabizi")


def version_of(manifest=MANIFEST):
    found = re.search(r"""["']version["']\s*:\s*["']([^"']+)["']""", manifest.read_text(encoding="utf-8"))
    return found.group(1) if found else "unknown"


def version_key(name):
    return [int(part) if part.isdigit() else 0 for part in re.split(r"[.\-]", name)]


def score(results):
    """:return: passed/total overall, by mode, by language and by domain (skipped scenarios are not counted)"""
    skipped = [r for r in results if r.get("skipped")]
    results = [r for r in results if not r.get("skipped")]
    def part(rows):
        total = len(rows)
        passed = sum(1 for row in rows if row["passed"])
        return {"passed": passed, "total": total, "score": round(passed / total, 4) if total else None}

    return {
        "overall": part(results),
        "by_mode": OrderedDict((mode, part([r for r in results if r["mode"] == mode])) for mode in MODES),
        "by_language": OrderedDict((lang, part([r for r in results if r["lang"] == lang])) for lang in LANGUAGES),
        "by_domain": OrderedDict(
            (domain, part([r for r in results if r["domain"] == domain]))
            for domain in sorted({r["domain"] for r in results})),
        "skipped": [r["id"] for r in skipped],
        "tokens": {
            "max": max([r.get("tokens", 0) for r in results] or [0]),
            "mean": round(sum(r.get("tokens", 0) for r in results) / len(results)) if results else 0,
        },
    }


def compare(now, before):
    """:return: (regressions, fixed, new, removed, token changes) against an earlier report"""
    old = {r["id"]: r for r in before["results"] if not r.get("skipped")}
    new = {r["id"]: r for r in now["results"] if not r.get("skipped")}
    regressions = sorted(i for i in new if i in old and old[i]["passed"] and not new[i]["passed"])
    fixed = sorted(i for i in new if i in old and not old[i]["passed"] and new[i]["passed"])
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    heavier = sorted(
        (i for i in new if i in old and new[i].get("tokens", 0) > old[i].get("tokens", 0) * 1.1 + 50),
        key=lambda i: old[i].get("tokens", 0) - new[i].get("tokens", 0))
    return regressions, fixed, added, removed, heavier


def percent(part):
    return "n/a" if part["score"] is None else f"{part['passed']}/{part['total']} ({part['score'] * 100:.0f}%)"


def markdown(report, before=None):
    scores = report["score"]
    results = [r for r in report["results"] if not r.get("skipped")]
    lines = [f"# Nudge quality gate, {report['version']}", "",
             f"Score: **{percent(scores['overall'])}**. Largest request {scores['tokens']['max']} tokens "
             f"(mean {scores['tokens']['mean']})."
             + (f" Skipped, an app is missing: {', '.join(scores['skipped'])}." if scores["skipped"] else ""), ""]
    for title, key in (("Mode", "by_mode"), ("Language", "by_language"), ("Domain", "by_domain")):
        lines += [f"| {title} | passed |", "|---|---|"]
        lines += [f"| {name} | {percent(part)} |" for name, part in scores[key].items()]
        lines.append("")
    failing = [r for r in results if not r["passed"]]
    if failing:
        lines += ["## Failing scenarios", ""]
        lines += [f"- `{r['id']}` ({r['domain']}, {r['mode']}, {r['lang']}): {'; '.join(r['failures'])}" for r in failing]
        lines.append("")
    if before:
        regressions, fixed, added, removed, heavier = compare(report, before)
        lines += [f"## Against {before['version']}", "",
                  f"Score {percent(before['score']['overall'])} -> {percent(scores['overall'])}.", ""]
        for title, ids in (("Regressions", regressions), ("Fixed", fixed), ("New scenarios", added),
                           ("Removed scenarios", removed), ("Requests more than 10% heavier", heavier)):
            if ids:
                lines.append(f"- {title}: " + ", ".join(f"`{i}`" for i in ids))
        lines.append("")
    lines += ["## Tokens of the largest request, by scenario", "", "| scenario | mode | tokens | budget |", "|---|---|---|---|"]
    lines += [f"| {r['id']} | {r['mode']} | {r.get('tokens', 0)} | {r.get('budget', '')} |"
              for r in sorted(results, key=lambda r: -r.get("tokens", 0))[:10]]
    return "\n".join(lines) + "\n"


def run_odoo(args, report_path):
    odoo_dir = Path(args.odoo_dir)
    addons = args.addons_path or ",".join([str(odoo_dir / "addons"), str(ROOT / "addons")])
    command = [
        args.python, "odoo-bin", f"--addons-path={addons}", "-d", args.db, "--db_host", args.db_host,
        "--db_user", args.db_user, "--db_password", args.db_password, "-u", args.update,
        "--test-tags", "/nudge:TestQualityGate", "--stop-after-init", f"--http-port={args.port}"]
    if args.install:
        command[command.index("-u"):command.index("-u")] = ["-i", args.install]
    env = dict(os.environ, NUDGE_GATE_REPORT=str(report_path), MSYS_NO_PATHCONV="1")
    log = Path(args.log)
    locked = args.lock_label and LOCK_TOOL.exists()
    if locked:
        subprocess.run([args.python, "-I", str(LOCK_TOOL), "acquire", args.lock_label], check=True)
    try:
        with log.open("w", encoding="utf-8") as handle:
            code = subprocess.run(command, cwd=odoo_dir, env=env, stdout=handle, stderr=subprocess.STDOUT).returncode
    finally:
        if locked:
            subprocess.run([args.python, "-I", str(LOCK_TOOL), "release", args.lock_label])
    return code, log


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default="nudge_gate")
    parser.add_argument("--odoo-dir", default=os.environ.get("ODOO_DIR", "C:/Users/Ibrahim Elzenad/odoo/odoo_bootcamp"))
    parser.add_argument("--addons-path", default="")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--db-host", default="localhost")
    parser.add_argument("--db-user", default="odoo")
    parser.add_argument("--db-password", default=os.environ.get("PGPASSWORD", "odoo"))
    parser.add_argument("--update", default="nudge", help="modules for -u (the gate needs nudge updated)")
    parser.add_argument("--install", default="base_automation",
                        help="apps the scenarios of Developer mode build on (-i; empty to skip)")
    parser.add_argument("--port", default="8069")
    parser.add_argument("--lock-label", default="", help="take the shared Odoo lock under this label")
    parser.add_argument("--log", default="quality_gate.log")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--baseline", default="", help="report to compare with (default: newest of another version)")
    parser.add_argument("--report", default="", help="score this report file instead of running Odoo")
    args = parser.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    version = version_of()
    report_path = Path(args.report) if args.report else out / f".{version}.raw.json"
    if not args.report:
        code, log = run_odoo(args, report_path)
        if not report_path.exists():
            print(f"The gate wrote no report (odoo exit {code}); see {log}", file=sys.stderr)
            return 2
    raw = json.loads(report_path.read_text(encoding="utf-8"))
    if not args.report:
        report_path.unlink()
    report = {"version": version, "results": raw["results"], "score": score(raw["results"])}

    baseline = None
    if args.baseline:
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    else:
        others = [p for p in out.glob("*.json") if p.stem != version]
        if others:
            baseline = json.loads(max(others, key=lambda p: version_key(p.stem)).read_text(encoding="utf-8"))
    (out / f"{version}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    text = markdown(report, baseline)
    (out / f"{version}.md").write_text(text, encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(text)

    regressions = compare(report, baseline)[0] if baseline else []
    dropped = bool(baseline) and (report["score"]["overall"]["score"] or 0) < (baseline["score"]["overall"]["score"] or 0)
    return 1 if regressions or dropped else 0


if __name__ == "__main__":
    sys.exit(main())

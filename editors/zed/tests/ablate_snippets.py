#!/usr/bin/env python3
"""Require one invalid Workflow snippet to fail after all unchanged snippets pass."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def consume(script, binary, out):
    run = subprocess.run([sys.executable, str(script), str(binary), str(out)], capture_output=True, timeout=90)
    (out / "consumer.stdout").write_bytes(run.stdout)
    (out / "consumer.stderr").write_bytes(run.stderr)
    rows = json.loads((out / "results.json").read_text())
    if len(rows) != 5 or len({row["name"] for row in rows}) != 5:
        raise ValueError("incomplete or duplicate snippet evidence")
    for row in rows:
        checks = row.get("assertions")
        required = {"exactKinds", "oneSnippetPerKind"} if row["name"] == "four-owned-resource-kinds" else {"exitZero", "completeAnalysis", "oneActualResource", "cleanDiagnostics", "documentedTarget"}
        if not checks or set(checks) != required or any(type(value) is not bool for value in checks.values()) or type(row.get("pass")) is not bool or row["pass"] != all(checks.values()):
            raise ValueError("invalid snippet assertions")
    return run.returncode, {row["name"]: row for row in rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    binary, out = args.binary.resolve(), args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {"pass": False, "mutation": "remove only the Workflow spec", "binarySha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
    try:
        code, baseline = consume(ROOT / "tests/snippets_e2e.py", binary, out / "baseline")
        if code != 0 or not all(row["pass"] for row in baseline.values()):
            raise ValueError("unchanged snippet baseline must pass")
        with tempfile.TemporaryDirectory(prefix="wf-snippet-ablation-") as temporary:
            copy = Path(temporary)
            (copy / "tests").mkdir()
            (copy / "snippets").mkdir()
            shutil.copy2(ROOT / "tests/snippets_e2e.py", copy / "tests/snippets_e2e.py")
            snippets = json.loads((ROOT / "snippets/yaml.json").read_text())
            names = [name for name, snippet in snippets.items() if "kind: Workflow" in snippet["body"]]
            if len(names) != 1:
                raise ValueError("mutation must select exactly one Workflow")
            name = names[0]
            snippets[name]["body"] = snippets[name]["body"][:snippets[name]["body"].index("spec:")]
            (copy / "snippets/yaml.json").write_text(json.dumps(snippets))
            (out / "mutant-snippets.json").write_text(json.dumps(snippets, indent=2) + "\n")
            code, actual = consume(copy / "tests/snippets_e2e.py", binary, out / "consumer")
            complete = set(baseline) == set(actual) and all(set(baseline[key]["assertions"]) == set(actual[key]["assertions"]) for key in baseline)
            failed = [key for key, row in actual.items() if not row["pass"]]
            report.update(consumerExit=code, failedCases=failed, completeAssertions=complete,
                          pass_=code == 1 and complete and failed == [name] and
                          actual[name]["assertions"]["exitZero"] is False and actual[name]["assertions"]["cleanDiagnostics"] is False)
            report["pass"] = report.pop("pass_")
    finally:
        (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

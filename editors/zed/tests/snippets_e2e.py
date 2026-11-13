#!/usr/bin/env python3
"""Expand every shipped snippet and validate its actual bytes with the native CLI."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {"Workflow", "WorkflowTemplate", "ClusterWorkflowTemplate", "CronWorkflow"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    binary, out = args.binary.resolve(), args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    snippets = json.loads((ROOT / "snippets/yaml.json").read_text())
    rows, seen = [], []
    with tempfile.TemporaryDirectory(prefix="wf-snippets-") as temporary:
        work = Path(temporary)
        for name, snippet in snippets.items():
            text = "\n".join(snippet["body"]) + "\n"
            text = re.sub(r"\$\{\d+:([^}]+)\}", lambda match: match[1], text)
            text = re.sub(r"\$\d+", "", text)
            kind = next((line.split(":", 1)[1].strip() for line in text.splitlines() if line.startswith("kind:")), "")
            seen.append(kind)
            (work / "case.yaml").write_bytes(text.encode())
            (out / (kind + ".input.yaml")).write_bytes(text.encode())
            command = [str(binary), "check", "--root", str(work), "--target-version", "3.6.0", "--format", "json"]
            run = subprocess.run(command, capture_output=True, timeout=15)
            (out / (kind + ".stdout")).write_bytes(run.stdout)
            (out / (kind + ".stderr")).write_bytes(run.stderr)
            report = json.loads(run.stdout)
            checks = {
                "exitZero": run.returncode == 0,
                "completeAnalysis": report.get("analysisComplete") is True and report.get("notEvaluated") == [] and report.get("configurationErrors") == [],
                "oneActualResource": report.get("filesAnalyzed") == report.get("resourcesAnalyzed") == 1,
                "cleanDiagnostics": report.get("diagnostics") == [],
                "documentedTarget": snippet["prefix"].startswith("argo-wf-") and "3.6.0" in snippet["description"],
            }
            rows.append({"name": name, "kind": kind, "command": command, "exitCode": run.returncode,
                         "assertions": checks, "pass": all(checks.values())})
    checks = {"exactKinds": set(seen) == EXPECTED, "oneSnippetPerKind": len(seen) == len(set(seen)) == 4}
    rows.append({"name": "four-owned-resource-kinds", "assertions": checks, "pass": all(checks.values())})
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    (out / "results.json").write_text(json.dumps(rows, indent=2) + "\n")
    (out / "manifest.json").write_text(json.dumps({"binarySha256": sha(binary), "testSha256": sha(Path(__file__)),
        "snippetSha256": sha(ROOT / "snippets/yaml.json"), "expectedKinds": sorted(EXPECTED)}, indent=2) + "\n")
    return 0 if len(rows) == 5 and all(row["pass"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())

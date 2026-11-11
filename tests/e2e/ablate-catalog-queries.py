#!/usr/bin/env python3
"""Compile removed catalog guards and require their specific consumer failures."""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("binary", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
out = args.output.resolve()
out.mkdir(parents=True, exist_ok=False)
env = os.environ | {"GOWORK": "off", "GOTOOLCHAIN": "local", "CGO_ENABLED": "1", "GOPROXY": "off", "GOSUMDB": "off"}
go = str(Path(env["GOROOT"]) / "bin/go") if env.get("GOROOT") else "go"
consumer = ROOT / "tests/e2e/wf-catalog-queries.py"


def run(folder, label, command, cwd=ROOT, timeout=180):
    result = subprocess.run([str(x) for x in command], cwd=cwd, env=env, capture_output=True, timeout=timeout)
    (folder / (label + ".stdout")).write_bytes(result.stdout)
    (folder / (label + ".stderr")).write_bytes(result.stderr)
    return result


baseline = out / "baseline"
baseline.mkdir()
baseline_run = run(baseline, "consumer", [sys.executable, consumer, args.binary.resolve(), baseline / "e2e"])
baseline_states = run(baseline, "state-guards", [go, "test", "-mod=vendor", "-count=1", "-timeout=60s", "./internal/lsp",
                                             "-run", "TestClosedReadRepairsOnlyItsScopeProof|TestReadinessTracksPendingFailureRetargetAndRemoval|TestReadOnlyClosureKeepsSnapshotsWithTheirProofs"])
rows = [{"id": "baseline", "consumerExit": baseline_run.returncode, "stateExit": baseline_states.returncode,
         "passed": baseline_run.returncode == 0 and baseline_states.returncode == 0}]
variants = [
    ("closed-read-stamp", "internal/lsp/catalog_inputs.go",
     "proof.files[source.URI(uri)] = catalogInput{file.stamp, hash}",
     "proof.files[source.URI(uri)] = catalogInput{stamp, file.hash}",
     "repair", "closed-current-file-repair", "no_self_content_modified"),
    ("self-repair-scope-proof", "internal/lsp/catalog_inputs.go",
     "if observed.repaired {", "if false && observed.repaired {",
     "repair", "closed-read-repair", "no_self_content_modified"),
    ("candidate-prefix", "internal/analysis/index.go",
     'known.SymbolsWithPrefix(scope, index.Key(prefix), "", 1000)', 'known.Symbols(scope, "", 1000)',
     "candidates", "candidate-prefix-budget", "unrelated_namespaces_do_not_hide_candidate"),
    ("candidate-truncation", "internal/analysis/index.go",
     "if candidates.Truncated {", "if false && candidates.Truncated {",
     "truncation", "candidate-matching-budget", "matching_overflow_explicit"),
    ("empty-scope-generation", "internal/lsp/catalog_readiness.go",
     'if root.members[scope] == 0 {', 'if false && root.members[scope] == 0 {',
     "go", "TestClosedReadRepairsOnlyItsScopeProof", "query rejected its own closed-file repair"),
    ("worker-reservation-release", "internal/lsp/catalog_readiness.go",
     "\tc.refreshReadiness(job.URI)\n", "",
     "go", "TestReadinessTracksPendingFailureRetargetAndRemoval", "successful parse did not publish ready scope"),
    ("old-worker-buffer", "internal/lsp/stdio.go",
     "\tif s.context().Err() == nil && s.workspaces != nil {",
     "\tfor n, d := range w.Documents {\n\t\tif b, opened := s.buffers[string(d.Snapshot.URI())]; opened && b.unavailable == \"\" {\n\t\t\tw.Documents[n].Snapshot = b.snapshot\n\t\t}\n\t}\n\tif s.context().Err() == nil && s.workspaces != nil {",
     "go", "TestReadOnlyClosureKeepsSnapshotsWithTheirProofs", "new catalog proof was paired with the worker's old dependency source"),
]
if rows[0]["passed"]:
    for name, file, old, new, case, required_case, required_assertion in variants:
        folder = out / name
        folder.mkdir()
        row = {"id": name, "passed": False}
        try:
            with tempfile.TemporaryDirectory(prefix="wf-catalog-ablation-") as temporary:
                source = Path(temporary)
                for directory in ("cmd", "internal", "compat", "schemas", "vendor"):
                    shutil.copytree(ROOT / directory, source / directory)
                for filename in ("go.mod", "go.sum"):
                    shutil.copyfile(ROOT / filename, source / filename)
                path = source / file
                before = path.read_text()
                if before.count(old) != 1:
                    raise ValueError("mutation must match exactly once: " + name)
                after = before.replace(old, new, 1)
                path.write_text(after)
                (folder / "mutation.diff").write_text("".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), file, file)))
                binary = source / ("mutant.exe" if os.name == "nt" else "mutant")
                command = [go, "test", "-c", "-mod=vendor", "-o", binary, "./internal/lsp"] if case == "go" else [
                    go, "build", "-mod=vendor", "-buildvcs=false", "-trimpath", "-o", binary, "./cmd/argo-workflows-ls"]
                build = run(folder, "build", command, source)
                row["buildExit"] = build.returncode
                if build.returncode != 0:
                    raise RuntimeError("mutant failed to compile")
                row["binarySHA256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
                if case == "go":
                    probe = run(folder, "probe", [binary, "-test.run", "^" + required_case + "$", "-test.count=1", "-test.timeout=60s"], source)
                    text = probe.stdout.decode(errors="replace") + probe.stderr.decode(errors="replace")
                    row["passed"] = probe.returncode == 1 and "--- FAIL: " + required_case in text and required_assertion in text
                else:
                    probe = run(folder, "probe", [sys.executable, consumer, binary, folder / "e2e", "--cases", case])
                    results = json.loads((folder / "e2e/results.json").read_text())
                    target = next((r for r in results if r["case"] == required_case), None)
                    row["passed"] = probe.returncode == 1 and target is not None and target["assertions"].get(required_assertion) is False
                row.update(probeExit=probe.returncode, requiredCase=required_case, requiredAssertion=required_assertion)
        except Exception as error:
            row["error"] = repr(error)
        rows.append(row)
        (out / "results.json").write_text(json.dumps(rows, indent=2) + "\n")
        print(name, "PASS" if row["passed"] else "FAIL", flush=True)
report = {"passed": len(rows) == len(variants) + 1 and all(row["passed"] for row in rows), "rows": rows}
(out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
sys.exit(0 if report["passed"] else 1)

#!/usr/bin/env python3
"""Count glob parsing in an instrumented native public CLI invocation.

The build fixture emits one marker immediately before the real glob parser.
Results and target identity still come from the normal CLI. This deterministic
work budget complements, and does not replace, the ordinary 100 ms query gate.
"""
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("wire", HERE / "wf-audit-gaps.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

SCOPES = 1000
config = 'schemaVersion: 1\ntargetVersion: "3.6.0"\nreferencePolicy: closed\nscopes:\n'
for number in range(SCOPES):
    config += f'  - name: group-{number:04d}\n    include: ["group-{number:04d}/*.yaml"]\n'
text = ('apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: budget}\n'
        'spec:\n  entrypoint: main\n  templates:\n    - name: main\n      container: {image: alpine}\n')
dest, work = m.setup("literal-prefix-work", {"group-0000/case.yaml": text}, config)
run = m.command(dest, "check", [m.BINARY, "check", "--root", work, "--format", "json"])
report = json.loads(run.stdout)
parses = run.stderr.count(b"WF_TEST_GLOB_PARSE\n")
m.result("literal-prefix-work", {
    "complete_cli": run.returncode == 0 and report["diagnostics"] == []
                    and report["configurationErrors"] == [] and report["filesAnalyzed"] == 1
                    and report["resourcesAnalyzed"] == 1 and report["analysisComplete"] is True
                    and report["notEvaluated"] == [],
    "selected_scope": len(report["targets"]) == 1 and report["targets"][0]["scope"] == "group-0000",
    "instrumentation_observed": parses > 0,
    "slow_path_budget": 0 < parses <= 32,
}, {"globParses": parses, "unrelatedScopes": SCOPES - 1, "maximumParses": 32, "cli": report})
m.save(m.OUT / "manifest.json", {"binarySha256": m.sha(m.BINARY), "testSha256": m.sha(Path(__file__)),
       "classification": "instrumented native CLI work budget; ordinary latency/resource gates remain separate",
       "scopeCount": SCOPES, "inputHashes": {str(p.relative_to(m.OUT)): m.sha(p) for p in work.rglob("*") if p.is_file()}})
raise SystemExit(0 if len(m.RESULTS) == 1 and all(row["pass"] for row in m.RESULTS) else 1)

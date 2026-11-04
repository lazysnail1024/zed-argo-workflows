#!/usr/bin/env python3
"""Real CLI/LSP checks of independently specified bounded-module gaps."""
import hashlib
import importlib.util
import json
import pathlib
import sys

SOURCE = pathlib.Path(__file__).resolve().parents[2]
BINARY = pathlib.Path(sys.argv[1]).resolve()
OUT = pathlib.Path(sys.argv[2]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
sys.argv = [sys.argv[0], str(BINARY), str(OUT)]
spec = importlib.util.spec_from_file_location("audit_client", SOURCE / "tests/e2e/wf-audit-gaps.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

BASE = "apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: bounded}\nspec:\n  entrypoint: main\n  templates:\n    - name: main\n      container: {image: alpine}\n"

def record(name, checks, details=None):
    m.result(name, checks, details)

for name in ["application", "root"]:
    text = BASE.replace("  entrypoint: main", "  parallelism: wrong\n  entrypoint: main")
    config = m.CONFIG + 'scopes:\n  - name: ' + name + '\n    include: ["selected/*.yaml"]\n'
    dest, work = m.setup("named-scope-" + name, {"selected/case.yaml": text}, config)
    cmd = [str(BINARY), "check", "--root", str(work), "--scope", name, "--format", "json"]
    run = m.command(dest, "selected-cli", cmd)
    report = json.loads(run.stdout)
    record("selected-cli-" + name, {"exitIncomplete": run.returncode == 2, "oneFile": report.get("filesAnalyzed") == 1, "oneResource": report.get("resourcesAnalyzed") == 1, "exactDiagnostic": [d["code"] for d in report.get("diagnostics", [])] == ["SCHEMA_TYPE"], "noConfigurationError": report.get("configurationErrors") == []}, report)
    client = m.LSP(dest, work)
    try:
        path = work / "selected/case.yaml"
        before = client.open(path, text)
        after = client.change(path, text.replace("parallelism: wrong", "parallelism: 2"), 2)
        record("current-lsp-" + name, {"exactInitialDiagnostic": [d["code"] for d in before] == ["SCHEMA_TYPE"], "currentChangeCleared": after == []}, {"before": before, "after": after})
    finally:
        client.close()

dest, work = m.setup("refused-existing-buffer", {"case.yaml": BASE.replace("main", "disk-only")})
client = m.LSP(dest, work)
path = work / "case.yaml"
uri = path.as_uri()
try:
    old = BASE.replace("main", "old-buffer")
    client.open(path, old)
    symbols = client.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}})
    record("authoritative-before-refusal", {"oldBufferOnly": [x["name"] for x in symbols.get("result", [])] == ["old-buffer"]})
    huge = BASE + "#" + "x" * (5 * 1024 * 1024)
    client.change(path, huge, 2)
    params = {"textDocument": {"uri": uri}, "position": {"line": 5, "character": 2}}
    checks = {}
    responses = {}
    for method in ["textDocument/documentSymbol", "textDocument/completion", "textDocument/definition", "textDocument/prepareRename"]:
        response = client.request(method, params)
        responses[method] = response
        checks[method] = response.get("error", {}).get("code") == -32602 and "result" not in response
    workspace = client.request("workspace/symbol", {"query": ""})
    checks["noOldOrDiskSymbols"] = workspace.get("result") == []
    record("existing-refused-authority", checks, {"responses": responses, "workspace": workspace})
    client.send("textDocument/didChange", {"textDocument": {"uri": uri, "version": 3}, "contentChanges": [{"range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 0}}, "text": "# incremental\n"}]})
    refused = client.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}})
    record("incremental-cannot-revive-refused-text", {"explicitRefusal": refused.get("error", {}).get("code") == -32602 and "result" not in refused}, refused)
    fresh = BASE.replace("main", "replacement")
    client.change(path, fresh, 4)
    accepted = client.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}})
    record("full-replacement-recovers", {"onlyNewSymbols": [x["name"] for x in accepted.get("result", [])] == ["replacement"]}, accepted)
    client.change(path, huge, 5)
    client.send("textDocument/didClose", {"textDocument": {"uri": uri}})
    reopened = BASE.replace("main", "reopened")
    client.open(path, reopened, 1)
    accepted = client.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}})
    record("close-reopen-recovers", {"onlyReopenedSymbols": [x["name"] for x in accepted.get("result", [])] == ["reopened"]}, accepted)
finally:
    client.close()

bad = BASE.replace("      container: {image: alpine}", "      container:\n        image: alpine\n        args: [" + json.dumps("{{=" + "(" * 257 + "1" + ")" * 257 + "}}") + "]")
foreign = "apiVersion: v1\nkind: ConfigMap\nmetadata: {name: foreign}\n"
for label, files, expected_files in [("mixed-stream", {"case.yaml": BASE + "---\n" + foreign + "---\n" + bad}, 0), ("separate-files", {"good.yaml": BASE, "bad.yaml": bad}, 1)]:
    dest, work = m.setup(label, files)
    code, report = m.cli(dest, work)
    record(label + "-completed-counts", {"incompleteExit": code == 2, "files": report["filesAnalyzed"] == expected_files, "resources": report["resourcesAnalyzed"] == 1, "explicitDepthReason": any("expression syntax depth" in x for x in report["notEvaluated"])}, report)

for label, config, selected, accepted in [
    ("implicit-root", m.CONFIG, "root", True),
    ("unassigned", m.CONFIG + 'scopes:\n  - name: application\n    include: ["selected/*.yaml"]\n', "unassigned", True),
    ("unknown-root", m.CONFIG + 'scopes:\n  - name: application\n    include: ["selected/*.yaml"]\n', "root", False),
    ("unknown-name", m.CONFIG, "missing", False),
]:
    dest, work = m.setup(label, {"outside.yaml": BASE.replace("  entrypoint: main", "  parallelism: wrong\n  entrypoint: main")}, config)
    run = m.command(dest, "check", [BINARY, "check", "--root", work, "--scope", selected, "--format", "json"])
    report = json.loads(run.stdout)
    record(label, {"exitIncomplete": run.returncode == 2, "counts": report["filesAnalyzed"] == int(accepted) and report["resourcesAnalyzed"] == int(accepted), "selection": ([d["code"] for d in report["diagnostics"]] == ["SCHEMA_TYPE"] and not report["configurationErrors"]) if accepted else bool(report["configurationErrors"])}, report)

dest, work = m.setup("config-overlay-root", {"selected/case.yaml": BASE.replace("  entrypoint: main", "  parallelism: wrong\n  entrypoint: main")}, m.CONFIG + 'scopes:\n  - name: application\n    include: ["selected/*.yaml"]\n')
client = m.LSP(dest, work)
try:
    path = work / "selected/case.yaml"; text = path.read_text(); client.open(path, text)
    config_path = work / ".argo-workflows-ls.yaml"
    client.send("textDocument/didOpen", {"textDocument": {"uri": config_path.as_uri(), "version": 1, "languageId": "yaml", "text": config_path.read_text().replace("name: application", "name: root")}})
    diagnostics = client.diagnostics(path.as_uri(), 1)
    after = client.change(path, text.replace("parallelism: wrong", "parallelism: 2"), 2)
    record("opened-config-root-authority", {"diagnosticPreserved": [d["code"] for d in diagnostics] == ["SCHEMA_TYPE"], "currentBufferCleared": after == []})
finally:
    client.close()

sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
m.save(OUT / "manifest.json", {"command": [sys.executable, str(pathlib.Path(__file__)), str(BINARY), str(OUT)], "binarySha256": sha(BINARY), "testSha256": sha(pathlib.Path(__file__)), "clientSha256": sha(SOURCE / "tests/e2e/wf-audit-gaps.py"), "inputHashes": {str(p.relative_to(OUT)): sha(p) for p in OUT.rglob("*") if p.is_file() and "input" in p.parts}, "python": sys.version})
sys.exit(0 if len(m.RESULTS) == 16 and all(row["pass"] for row in m.RESULTS) else 1)

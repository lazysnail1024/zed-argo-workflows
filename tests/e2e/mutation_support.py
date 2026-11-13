#!/usr/bin/env python3
"""Compile selected fault injections and require their expected semantic failures.

The selected mutations include historical ablate-* recipes and targeted re-audit
regressions. Behavior belongs to real consumers or explicitly named Go tests;
this module validates the baseline, compilation, complete evidence and the
particular assertion each mutation must invalidate. A crash or an incomplete run
is never a kill.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
E2E = ROOT / "tests/e2e"


@dataclass(frozen=True)
class GoTestOracle:
    package: str
    test: str
    failure: str


@dataclass(frozen=True)
class Mutation:
    name: str
    group: str
    origin: str
    file: str
    replacements: tuple[tuple[str, str], ...]
    suite: str
    required: tuple[tuple[str, str], ...]
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    timeout: int = 240
    tool: str = ""
    go_test: GoTestOracle | None = None


MUTATIONS = (
    Mutation(
        "toolkit-depth-boundary", "contracts", "ablate-toolkit-budget.py",
        "vendor/github.com/lazysnail1024/k8s-crd-toolkit/yamlview/internal/parser/parser.go",
        (("if depth > 256 {", "if depth >= 256 {"),),
        "wf-toolkit-budget.py", (("cli-depth-at-limit", "complete"),),
    ),
    Mutation(
        "schema-validator", "contracts", "ablate-wf3.py", "internal/analysis/schema.go",
        (("if err = schema.Validate(value); err != nil {", "if err = schema.Validate(value); err != nil && false {"),),
        "wf3-schema.py", (("mixed", "type_error"),),
    ),
    Mutation(
        "yaml-explicit-tag", "contracts", "ablate-yaml.py", "internal/analysis/projection.go",
        (("RejectExplicitTags: true", "RejectExplicitTags: false"),),
        "wf-shared-yaml-projection.py", (("explicit-standard-tag", "cliRefusal"),),
    ),
    Mutation(
        "field-existing-key", "contracts", "ablate-fields.py", "internal/analysis/queries.go",
        (("!field.existing[name] &&", "true &&"),),
        "wf3-lsp.py", (("stdio-buffer-lifecycle", "field_completion"),),
    ),
    Mutation(
        "scope-overlap", "contracts", "ablate-workspace.py", "internal/workspace/config.go",
        (("if count > 1 {", "if count > 1 && false {"),),
        "wf3-workspace.py", (("overlap", "reject"),),
    ),
    Mutation(
        "external-namespace", "contracts", "ablate-external.py", "internal/analysis/external.go",
        (("externalIdentity{candidate.document.Target.ID, candidate.kind, namespace, candidate.name}",
          "externalIdentity{candidate.document.Target.ID, candidate.kind, namespace[:0], candidate.name}"),
         ("externalIdentity{caller.document.Target.ID, wantedKind, namespace, name}",
          "externalIdentity{caller.document.Target.ID, wantedKind, namespace[:0], name}")),
        "wf4-external.py", (("wrong-namespace", "externalResolution"),),
    ),
    Mutation(
        "inherited-arguments", "contracts", "ablate-inheritance.py", "internal/analysis/external.go",
        (('args = append(args, workflowMember(callee.semanticSpec, "arguments").value)',
          '// inherited argument supply removed'),),
        "wf4-inheritance.py", (("inherited", "diagnostics"),),
    ),
    Mutation(
        "reference-visibility", "contracts", "ablate-references.py", "internal/analysis/references.go",
        (('if !ok {\n\t\t\t\t\t\t\temit(node, "WF_REFERENCE_SCOPE"',
          'if !ok && false {\n\t\t\t\t\t\t\temit(node, "WF_REFERENCE_SCOPE"'),),
        "wf4-local.py", (("dagscope", "domainCodes"), ("stepscope", "domainCodes")),
    ),
    Mutation(
        "official-version", "contracts", "ablate-official.py", "cmd/argo-workflows-ls/tools.go",
        (('if toolVersion != "argo: v"+version {', 'if false && toolVersion != "argo: v"+version {'),),
        "wf5-official.py", (("mismatch", "exit"),), tool="argo",
    ),
    Mutation(
        "render-capabilities", "contracts", "ablate-render.py", "cmd/argo-workflows-ls/tools.go",
        ((', "--kube-version", *kube', ''),),
        "wf5-render.py", (("real-helm-render", "actual_capabilities"),), tool="helm",
    ),
    Mutation(
        "closed-uri-pending", "contracts", "closed documents must receive an empty publication", "internal/lsp/publication.go",
        (("func (s *session) publishEmpty(uri string) error {",
          "func (s *session) publishEmpty(uri string) error {\n\tif _, opened := s.buffers[uri]; !opened { return nil }"),),
        "wf-lifecycle-gaps.py", (("close-clear-interrupted-1", "closed_uri_cleared"),),
    ),
    Mutation(
        "symbol-query-cap", "audit", "workspace display symbols retain their 20,000 result cap", "internal/lsp/catalog_symbols.go",
        (("const workspaceSymbolLimit = 20000", "const workspaceSymbolLimit = 20001"),),
        "wf-symbol-query-budget.py", (("stable-display-symbols", "same_first_20000_display_symbols"),),
    ),
    Mutation(
        "symbol-query-page-skip", "audit", "paged identity traversal must not skip known display symbols", "internal/lsp/catalog_symbols.go",
        (("\t\tcursor.next++", "\t\tcursor.next += 2"),),
        "wf-symbol-query-budget.py", (("stable-display-symbols", "same_first_20000_display_symbols"),),
    ),
    Mutation(
        "resource-rename-unique", "audit", "resource Rename needs a unique declaration", "internal/analysis/externalrename.go",
        (("if declarations != 1 {", "if declarations < 1 {"),),
        "wf-resource-rename.py", (("duplicate-definition", "prepareRefused"),), ("--cases", "duplicate-definition"),
    ),
    Mutation(
        "resource-rename-capture", "audit", "resource Rename must preserve every call binding", "internal/analysis/externalrename.go",
        (("if afterBindings[call] != target {", "if false && afterBindings[call] != target {"),),
        "wf-resource-rename.py", (("unresolved-capture", "renameRefused"),), ("--cases", "unresolved-capture"),
    ),
    Mutation(
        "diagnostic-ownership", "core", "unrelated YAML diagnostic isolation", "internal/lsp/serve.go",
        (("if state.ownsPublication(uri) {", "if true {"),),
        "wf-diagnostic-ownership.py",
        tuple((name, "no_workflows_diagnostics") for name in (
            "deployment", "application", "applicationset", "values", "mixed-foreign",
        )),
    ),
    Mutation(
        "report-deadline-exit", "core", "completed report / slow stdout", "cmd/argo-workflows-ls/main.go",
        (("\t\tif analysisErr != nil {\n\t\t\treturn 3\n\t\t}",
          "\t\tif ctx.Err() != nil {\n\t\t\treturn 3\n\t\t}"),),
        "wf-report-deadline.py",
        tuple((name, "exitMatchesPublishedOutcome") for name in (
            "json-success", "json-error", "human-success",
        )),
    ),
    Mutation(
        "unknown-target", "core", "ablate-wf2.py", "compat/catalog.go",
        ((
            'return fmt.Errorf("unknown target version: %s; use auto or an exact supported version from compat/catalog.lock.json; remove an editor targetVersion override to restore the file pin", version)',
            "return nil",
        ),),
        "wf-delivery.py", (("unknown", "envelope"),), ("--cases", "unknown"),
    ),
    Mutation(
        "document-version", "core", "ablate-lsp.py", "internal/lsp/stdio.go",
        (("p.TextDocument.Version <= b.version", "false"),),
        "wf3-lsp.py", (("stdio-buffer-lifecycle", "no_version_regression"),),
    ),
    Mutation(
        "dag-cycle", "core", "ablate-local.py", "internal/analysis/local.go",
        (("if !reported {", "if false && !reported {"),),
        "wf4-local.py", (("dag", "domainCodes"),),
    ),
    Mutation(
        "stale-action", "core", "ablate-edits.py", "internal/lsp/edits.go",
        ((
            "if err != nil || !opened || b.snapshot.Revision() != action.revision || currentFingerprint != action.fingerprint {",
            "if false && (err != nil || !opened || b.snapshot.Revision() != action.revision || currentFingerprint != action.fingerprint) {",
        ),),
        "wf-delivery.py", (("edits", "staleAction"),), ("--cases", "edits"),
    ),
    Mutation(
        "nested-root", "core", "longest registered root owns indexed files", "internal/lsp/catalog.go",
        (("&& len(root) > len(best)", "&& (best == \"\" || len(root) < len(best))"),),
        "wf-lifecycle.py", (("nested-root-single-owner", "one_nested_symbol"),),
    ),
    Mutation(
        "cancel-request", "core", "ablate-lifecycle.py", "internal/lsp/serve.go",
        ((
            'if m.Method == "$/cancelRequest" {',
            'if m.Method == "$/cancelRequest" { continue }; if m.Method == "$/cancelRequest" {',
        ),),
        "wf-lifecycle.py", (("cancel-acknowledged", "request_cancelled"),),
    ),
    Mutation(
        "yaml-string-edit", "core", "ablate-domain-repair.py", "internal/analysis/domain_queries.go",
        (("return strconv.Quote(name)", "return name"), ('\n\t"strconv"', "")),
        "wf-domain-repair.py",
        tuple((name, "0-textDocument/completion-exactBytes") for name in (
            "yaml-plain-template-true", "yaml-plain-template-null", "yaml-plain-template-123",
            "yaml-plain-argument-true", "yaml-external-argument-plain",
        )),
        ("--cases=yaml-plain-template-true,yaml-plain-template-null,yaml-plain-template-123,yaml-plain-argument-true,yaml-external-argument-plain",),
    ),
    Mutation(
        "metadata-raw-binding", "audit", "reaudit R-01", "internal/analysis/localrename.go",
        ((
            "\tmode := renameFieldMode(path)\n",
            """\tmode := renameFieldMode(path)
\tif len(path) > 0 {
\t\tswitch path[len(path)-1] {
\t\tcase "expression", "fromExpression":
\t\t\tmode = renameExpressionField
\t\tcase "from", "parameter":
\t\t\tmode = renameVariableField
\t\t}
\t}
""",
        ),),
        "wf-reaudit-rename.py", (("metadata-expression-literal", "exact_edits"),
                                 ("metadata-prose", "prepare_exact")),
        ("--cases", "metadata-expression-literal,metadata-prose,metadata-tagged,input-parameter-raw,input-artifact-raw,output-parameter-raw,output-artifact-raw"),
    ),
    Mutation(
        "incomplete-publication-acknowledgement", "audit", "local publication must not acknowledge external work", "internal/lsp/catalog_inputs.go",
        (("file.awaiting = local && !ready", "file.awaiting = false"),),
        "wf-index-publication.py", (("reconciliation-publication", "ready_republishes_unacknowledged_external_diagnostic"),), timeout=180,
    ),
    Mutation(
        "obsolete-publication-scope", "audit", "unchanged completed scopes must stay idle", "internal/lsp/catalog_discovery.go",
        ((
            "if state != root.lastState || file.awaiting && !globalFailure && !incomplete[file.target.ID] {",
            "if true || state != root.lastState || file.awaiting && !globalFailure && !incomplete[file.target.ID] {",
        ),),
        "wf-reaudit-reconciliation.py", (("scope-rebinding", "obsolete_scope_not_reconciled"),),
    ),
    Mutation(
        "opened-scope-action", "audit", "reaudit R-04", "internal/lsp/edits.go",
        ((
            "d, err = s.queryDocument(action.uri)",
            'd, err = s.queryDocument(action.uri); if err == nil && strings.HasPrefix(d.Target.Scope, "opened:") { d.Target.Scope = "root"; d.Target.ID = "root" }',
        ),),
        "wf-reaudit-actions.py", (("excluded", "immediateExactEdit"),
                                 ("internal-directory", "immediateExactEdit")),
    ),
    Mutation(
        "isolated-action-configuration", "audit", "reaudit R-04", "internal/lsp/edits.go",
        ((
            "[]string{fingerprint(w), w.Config.ReferencePolicy, w.Config.DefaultNamespace}",
            '[]string{fingerprint(w), "", ""}',
        ),),
        "wf-reaudit-actions.py", (("isolated-config-policy", "effectiveConfigChangeRefused"),
                                 ("isolated-config-namespace", "effectiveConfigChangeRefused")),
    ),
    Mutation(
        "configuration-diagnostic-path", "audit", "reaudit R-03", "internal/lsp/stdio.go",
        ((
            'if configPath, err := filePath(uri); err == nil && filepath.Base(configPath) == ".argo-workflows-ls.yaml" {',
            'if configPath, err := filePath(uri); err == nil && false && filepath.Base(configPath) == ".argo-workflows-ls.yaml" {',
        ),),
        "wf-reaudit-queries.py", (("configuration-recovery", "valid_config_clean"),),
        ("--cases", "configuration"),
    ),
    Mutation(
        "local-completion-scope-reads", "audit", "reaudit R-05", "internal/lsp/stdio.go",
        ((
            'if m.Method == "textDocument/definition" || m.Method == "textDocument/references" {',
            'if m.Method == "textDocument/completion" { s.focus = "" }; if m.Method == "textDocument/definition" || m.Method == "textDocument/references" || m.Method == "textDocument/completion" {',
        ),),
        "wf-reaudit-query-io.py", (("local-completion-io", "no_scope_reads"),),
        ("--cases", "local"),
    ),
    Mutation(
        "external-completion-scope-reads", "audit", "reaudit R-05", "internal/lsp/stdio.go",
        ((
            "w, workspaceErr := s.documentWorkspace(uri, document.Target.Scope)",
            's.focus = ""; w, workspaceErr := s.workspace(s.root(uri), "")',
        ),),
        "wf-reaudit-query-io.py", (("external-completion-io", "no_unrelated_scope_reads"),),
        ("--cases", "external"),
    ),
    Mutation(
        "scope-summary-generation", "audit", "detached resource candidates enter query identity", "internal/lsp/query_cache.go",
        (("generations[scope] = state.Generation", "generations[scope] = state.Generation * 0"),),
        "wf-index-boundaries.py", (("summary-candidate-generation", "new_summary_invalidates_cached_candidates"),),
    ),
    Mutation(
        "process-output-budget", "resources", "ablate-bounded.py", "cmd/argo-workflows-ls/tools.go",
        ((
            """func (b *boundedOutput) Write(p []byte) (int, error) {
\tb.budget.mu.Lock()
\tdefer b.budget.mu.Unlock()
\tremaining := 10*1024*1024 - b.budget.bytes
\tn := len(p)
\tif n > remaining {
\t\tn = remaining
\t}
\twritten, err := b.buffer.Write(p[:n])
\tb.budget.bytes += written
\tif n < len(p) {
\t\tb.budget.overflow = true
\t\tb.budget.cancel()
\t\treturn written, fmt.Errorf("combined command output exceeds 10 MiB")
\t}
\treturn written, err
}""",
            "func (b *boundedOutput) Write(p []byte) (int, error) { return b.buffer.Write(p) }",
        ),),
        "wf-bounded-process.py", (("helm-flood", "combinedOutputBound"), ("argo-flood", "combinedOutputBound")),
        env={"WF_PROCESS_CASES": "helm-flood,argo-flood"},
    ),
    Mutation(
        "process-tree-cleanup", "resources", "ablate-bounded.py", "cmd/argo-workflows-ls/process_unix.go",
        (("return func() { _ = syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL) }, nil", "return func() {}, nil"),),
        "wf-bounded-process.py", (("helm-inherited-pipe", "treeReclaimed"), ("argo-inherited-pipe", "treeReclaimed")),
        env={"WF_PROCESS_CASES": "helm-inherited-pipe,argo-inherited-pipe"},
    ),
)


class GateError(RuntimeError):
    pass


def save(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def execute(command: list[str], dest: Path, env: dict[str, str], cwd: Path, timeout: int, *, stdin: Path | None = None):
    """Record a bounded invocation without buffering potentially large logs in RAM."""
    dest.mkdir(parents=True, exist_ok=True)
    receipt = {"argv": command, "cwd": str(cwd), "timeoutSeconds": timeout, "timedOut": False}
    if stdin is not None:
        receipt["stdin"] = str(stdin)
    started = time.monotonic()
    with (dest / "stdout.log").open("wb") as stdout, (dest / "stderr.log").open("wb") as stderr, \
            (stdin.open("rb") if stdin is not None else nullcontext()) as input_stream:
        try:
            proc = subprocess.Popen(command, cwd=cwd, env=env, stdin=input_stream, stdout=stdout, stderr=stderr,
                                    start_new_session=os.name != "nt")
        except OSError as error:
            receipt.update(exitCode=None, seconds=time.monotonic() - started, spawnError=str(error))
            save(dest / "command.json", receipt)
            raise GateError(f"cannot start command: {command[0]}: {error}") from error
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            receipt["timedOut"] = True
            if os.name == "nt":
                proc.kill()
            else:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            proc.wait(timeout=15)
        receipt.update(exitCode=proc.returncode, seconds=time.monotonic() - started)
    save(dest / "command.json", receipt)
    return receipt


def read_results(dest: Path) -> dict[str, dict]:
    path = dest / "results.json"
    if not path.is_file():
        raise GateError(f"consumer produced no results: {path}")
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise GateError("consumer result must be a nonempty case list")
    indexed = {}
    for row in rows:
        name = row.get("case", row.get("name"))
        checks = row.get("assertions")
        if not name or name in indexed or name == "runner-error":
            raise GateError(f"missing/duplicate/error case: {name}")
        if not isinstance(checks, dict) or not checks or any(type(v) is not bool for v in checks.values()):
            raise GateError(f"{name}: assertions must be nonempty booleans; exceptions do not count")
        if type(row.get("pass")) is not bool or row["pass"] != all(checks.values()):
            raise GateError(f"{name}: inconsistent case status")
        for key in ("completed", "naturalExit", "lspExit", "parse", "exception", "terminalExit", "terminalJSON"):
            if checks.get(key) is False:
                raise GateError(f"{name}: incomplete consumer or abnormal LSP exit ({key})")
        if "shutdown" in checks and checks.get("exit") is False:
            raise GateError(f"{name}: protocol consumer did not observe a clean native exit")
        if row.get("harnessKilled") or row.get("timedOut"):
            raise GateError(f"{name}: safety harness terminated the process")
        code = row.get("exitCode")
        if code is not None and (type(code) is not int or code not in (0, 1, 2, 3)):
            raise GateError(f"{name}: abnormal native exit {code}")
        indexed[name] = row
    for log in dest.rglob("*.command.json"):
        command = json.loads(log.read_text(encoding="utf-8"))
        if command.get("timedOut"):
            raise GateError(f"native process timed out: {log}")
        code = command.get("exitCode")
        if type(code) is not int or code not in (0, 1, 2, 3):
            raise GateError(f"abnormal native exit {code}: {log}")
        if log.name == "lsp.command.json" and code != 0:
            raise GateError(f"LSP did not shut down normally: {log}")
    if list(dest.rglob("*-fatal.json")) or list(dest.rglob("error.txt")):
        raise GateError("consumer recorded an exception; it is not a semantic kill")
    return indexed


def run_consumer(mutation: Mutation, binary: Path, dest: Path, env: dict[str, str], prefix: list[str], tools_dir=None):
    run = dest / "run"
    if run.exists():
        raise GateError(f"refusing to reuse old consumer evidence: {run}")
    # A sudo/systemd service scrubs the caller environment. Forward the exact
    # case selection and only the Python runtime settings needed by setup-python
    # on hosted Linux (its interpreter may need LD_LIBRARY_PATH to load libpython).
    forwarded = {key: env[key] for key in ("LD_LIBRARY_PATH", "PYTHONUTF8", "PYTHONIOENCODING") if env.get(key)}
    forwarded.update(mutation.env)
    selected_env = (["env", *(key + "=" + value for key, value in forwarded.items())]
                    if prefix and forwarded else [])
    tool = [str(tools_dir / mutation.tool)] if mutation.tool else []
    command = prefix + selected_env + [sys.executable, str(E2E / mutation.suite), str(binary), *tool, str(run), *mutation.args]
    receipt = execute(command, dest, env | mutation.env, ROOT, mutation.timeout)
    if receipt["timedOut"] or receipt["exitCode"] not in (0, 1):
        raise GateError(f"consumer infrastructure failure: {receipt}")
    return receipt, read_results(run)


def go_test_pattern(mutation: Mutation) -> str:
    oracle = mutation.go_test
    if (oracle is None or not oracle.package.startswith("./") or ".." in Path(oracle.package).parts
            or Path(mutation.suite).parent != Path(oracle.package)
            or not mutation.suite.endswith("_test.go")
            or not re.fullmatch(r"Test[A-Za-z0-9_]+(?:/[A-Za-z0-9_-]+)*", oracle.test)
            or not oracle.failure or "\n" in oracle.failure
            or len(mutation.required) != 1 or mutation.required[0][0] != oracle.test
            or not mutation.required[0][1] or mutation.args or mutation.tool):
        raise GateError("Go oracle requires an exact package, source, test and one semantic assertion")
    # Go splits -test.run on slashes. Anchor every component, including parents.
    return "/".join("^" + re.escape(part) + "$" for part in oracle.test.split("/"))


def validate_go_test_events(mutation: Mutation, stdout: str, receipt: dict, stderr: str = "") -> list[dict]:
    """Accept a complete exact Go test run, never an incidental process failure."""
    go_test_pattern(mutation)
    oracle = mutation.go_test
    if (receipt.get("timedOut") or type(receipt.get("exitCode")) is not int
            or receipt["exitCode"] not in (0, 1) or stderr.strip()):
        raise GateError("Go test infrastructure failure cannot kill a mutation")
    try:
        events = [json.loads(line) for line in stdout.splitlines()]
    except (ValueError, TypeError) as error:
        raise GateError("Go test did not produce structured JSON events") from error
    if not events or any(not isinstance(event, dict) for event in events):
        raise GateError("Go test produced no structured events")
    parts = oracle.test.split("/")
    expected = {"/".join(parts[:index]) for index in range(1, len(parts) + 1)}
    running, finished, output, assertions = set(), {}, [], []
    package_status = None
    for index, event in enumerate(events):
        action, test = event.get("Action"), event.get("Test")
        if event.get("Package") != oracle.package or package_status is not None:
            raise GateError("Go test package or terminal event does not match the declared oracle")
        if index == 0:
            if action != "start" or test is not None:
                raise GateError("Go test is missing its package start event")
            continue
        if test is not None and test not in expected:
            raise GateError("Go test selected an undeclared test: " + str(test))
        if action == "run" and test is not None:
            parent = test.rpartition("/")[0]
            if test in running or test in finished or parent and parent not in running:
                raise GateError("Go test contains duplicate or unordered test selection")
            running.add(test)
        elif action == "output":
            if not isinstance(event.get("Output"), str) or test is not None and test not in running:
                raise GateError("Go test output is malformed or outside its named test")
            output.append((test, event["Output"]))
            if event.get("OutputType") == "error":
                assertions.append((test, event["Output"]))
        elif action in ("pass", "fail") and test is not None:
            if test not in running or any(name.startswith(test + "/") for name in running):
                raise GateError("Go test completion is missing, duplicate or out of order")
            running.remove(test)
            finished[test] = action
        elif action in ("pass", "fail") and test is None:
            if running or set(finished) != expected or index != len(events) - 1:
                raise GateError("Go test omitted or added named test completion")
            package_status = action
        else:
            raise GateError("Go test skipped, interrupted or emitted an unsupported event")
    if package_status is None or set(finished) != expected or any(status != package_status for status in finished.values()):
        raise GateError("Go test and parent results are incomplete or inconsistent")
    passed = package_status == "pass"
    if receipt["exitCode"] != (0 if passed else 1):
        raise GateError("Go test events disagree with the actual exit status")
    combined_output = "".join(value for _, value in output)
    if re.search(r"(?m)^\s*(?:panic:|fatal error:|runtime:|WARNING: DATA RACE)", combined_output):
        raise GateError("Go test crashed or reported a runtime failure")
    if not passed:
        assertion = re.compile(r"^\s*" + re.escape(Path(mutation.suite).name) + r":\d+: " + re.escape(oracle.failure) + r"\s*$")
        # The pinned Go toolchain distinguishes t.Fatal/t.Error from t.Log.
        known = [line for test, value in assertions if test == oracle.test
                 for line in value.splitlines() if assertion.fullmatch(line)]
        if len(known) != 1:
            raise GateError("named Go test failed without its exact declared semantic assertion")
    return [{"case": oracle.test, "assertions": {mutation.required[0][1]: passed}, "pass": passed}]


def run_go_test(mutation: Mutation, go: str, source: Path, binary: Path, dest: Path,
                env: dict[str, str], prefix: list[str]):
    run = dest / "run"
    if run.exists():
        raise GateError(f"refusing to reuse old Go test evidence: {run}")
    run.mkdir(parents=True)
    oracle = mutation.go_test
    pattern = go_test_pattern(mutation)
    forwarded = {key: env[key] for key in ("GOTOOLCHAIN", "GOWORK", "GOPROXY", "LD_LIBRARY_PATH") if env.get(key)}
    forwarded.update(mutation.env)
    selected_env = (["env", *(key + "=" + value for key, value in forwarded.items())] if prefix and forwarded else [])
    command = prefix + selected_env + [str(binary), "-test.v=test2json", "-test.run=" + pattern, "-test.count=1",
        "-test.timeout=" + str(mutation.timeout) + "s"]
    # test2json normalizes every nonzero child exit to 1. Run the test separately
    # so a crash/exit 2 cannot masquerade as an ordinary failed assertion.
    native = dest / "native"
    receipt = execute(command, native, env | mutation.env, source, mutation.timeout)
    manifest = {"oracleType": "go-test", "goTest": asdict(oracle),
        "testSource": mutation.suite, "testSourceSha256": digest(source / mutation.suite),
        "binary": str(binary), "binarySha256": digest(binary),
        "driverSha256": digest(Path(__file__)), "runPattern": pattern,
        "rawStdoutSha256": digest(native / "stdout.log"), "rawStderrSha256": digest(native / "stderr.log"),
        "command": receipt}
    save(run / "manifest.json", manifest)
    if receipt["timedOut"] or receipt["exitCode"] not in (0, 1):
        raise GateError("Go test binary did not exit normally; no semantic kill")
    conversion = execute([go, "tool", "test2json", "-t", "-p", oracle.package], dest,
                         env, source, mutation.timeout, stdin=native / "stdout.log")
    manifest.update(conversion=conversion, eventsSha256=digest(dest / "stdout.log"))
    save(run / "manifest.json", manifest)
    if conversion["timedOut"] or conversion["exitCode"] != 0 or (dest / "stderr.log").read_bytes():
        raise GateError("Go test JSON conversion did not complete normally")
    rows = validate_go_test_events(mutation, (dest / "stdout.log").read_text(encoding="utf-8"), receipt,
                                  (native / "stderr.log").read_text(encoding="utf-8"))
    save(run / "results.json", rows)
    return receipt, read_results(run)


def classify(mutation: Mutation, baseline: dict[str, dict], actual: dict[str, dict], receipt: dict):
    return classify_results(mutation.required, baseline, actual, receipt)


def classify_results(required, baseline: dict[str, dict], actual: dict[str, dict], receipt: dict):
    """Classify named semantic changes after read_results rejected broken runs."""
    if receipt.get("timedOut") or type(receipt.get("exitCode")) is not int or receipt["exitCode"] not in (0, 1):
        raise GateError("consumer infrastructure failure cannot kill a mutation")
    if not required or not baseline or not all(row["pass"] for row in baseline.values()):
        raise GateError("a nonempty passing baseline and explicit semantic requirements are mandatory")
    if set(actual) != set(baseline):
        raise GateError("baseline/mutant case sets differ; incomplete runs cannot kill a mutant")
    for name, row in baseline.items():
        if set(row["assertions"]) != set(actual[name]["assertions"]):
            raise GateError(f"{name}: baseline/mutant assertion sets differ")
    expected = []
    for case, assertion in required:
        before = baseline.get(case, {}).get("assertions", {}).get(assertion)
        after = actual.get(case, {}).get("assertions", {}).get(assertion)
        if before is not True:
            raise GateError(f"missing passing baseline assertion: {case}/{assertion}")
        expected.append({"case": case, "assertion": assertion, "baseline": before, "mutant": after})
    killed = receipt["exitCode"] == 1 and all(x["mutant"] is False for x in expected)
    return {
        "detected": killed,
        "status": "killed" if killed else "survived",
        "semanticEvidence": expected,
        "failedAssertions": {name: [key for key, passed in row["assertions"].items() if not passed]
                             for name, row in actual.items() if not row["pass"]},
    }


def build(go: str, source: Path, binary: Path, env: dict[str, str], timeout: int, oracle: GoTestOracle | None = None):
    command = ([go, "test", "-c"] if oracle else [go, "build"]) + [
        "-mod=vendor", "-trimpath", "-buildvcs=false", "-o", str(binary),
        oracle.package if oracle else "./cmd/argo-workflows-ls"]
    receipt = execute(command, binary.parent / "build", env, source, timeout)
    if receipt["timedOut"] or receipt["exitCode"] != 0:
        raise GateError(f"compilation failed; this is not a semantic kill: {binary.parent / 'build'}")
    return {"command": receipt, "sha256": digest(binary), "binary": str(binary),
            "kind": "go-test" if oracle else "server"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("group", choices=("core", "audit", "contracts", "legacy", "resources", "all"))
    parser.add_argument("output", type=Path, help="new output directory (must not already exist)")
    parser.add_argument("--variants", nargs="+", help="exact names from the selected group; unknown names fail")
    parser.add_argument("--test-prefix-json", default="[]", help="JSON command prefix for consumers only; builds remain outside a memory cgroup")
    parser.add_argument("--build-timeout", type=int, default=600)
    parser.add_argument("--tools", type=Path, help="explicit directory with checksum-pinned Argo/Helm tools")
    args = parser.parse_args(argv)
    candidates = [m for m in MUTATIONS if args.group == "all" or m.group == args.group]
    if args.variants:
        unknown = set(args.variants) - {m.name for m in candidates}
        if unknown:
            parser.error("unknown mutation(s): " + ", ".join(sorted(unknown)))
        candidates = [m for m in candidates if m.name in args.variants]
    if not candidates:
        parser.error("at least one mutation is required")
    tools_dir = args.tools.resolve() if args.tools else None
    for mutation in candidates:
        if mutation.tool and (tools_dir is None or not (tools_dir / mutation.tool).is_file()):
            parser.error("--tools must contain the pinned " + mutation.tool + " binary")
        if mutation.go_test:
            go_test_pattern(mutation)
    prefix = json.loads(args.test_prefix_json)
    if not isinstance(prefix, list) or any(not isinstance(x, str) for x in prefix):
        parser.error("--test-prefix-json must be a JSON array of strings")
    if args.build_timeout <= 0:
        parser.error("--build-timeout must be positive")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    env = os.environ | {"GOTOOLCHAIN": "local", "GOWORK": "off", "GOPROXY": "off", "CGO_ENABLED": "1"}
    go = shutil.which("go")
    if not go:
        save(out / "results.json", {"pass": False, "error": "go is not on PATH"})
        return 1
    report = {"pass": False, "group": args.group, "variants": [], "baseline": {},
              "consumerPrefix": prefix, "selected": [m.name for m in candidates],
              "driverSha256": digest(Path(__file__)), "python": sys.version}
    save(out / "results.json", report)
    try:
        with tempfile.TemporaryDirectory(prefix="wf-mutation-") as directory:
            source = Path(directory)
            for folder in ("cmd", "internal", "compat", "schemas", "vendor"):
                shutil.copytree(ROOT / folder, source / folder)
            for filename in ("go.mod", "go.sum"):
                shutil.copy2(ROOT / filename, source / filename)
            source_hashes = {str(p.relative_to(source)): digest(p) for p in source.rglob("*") if p.is_file()}
            save(out / "source-manifest.json", source_hashes)
            baseline_binary = out / "baseline" / ("server.exe" if os.name == "nt" else "server")
            if any(mutation.go_test is None for mutation in candidates):
                report["baselineBuild"] = build(go, source, baseline_binary, env, args.build_timeout)
            baselines = {}
            for mutation in candidates:
                # Same suite + arguments + environment shares one passing baseline.
                key = (mutation.suite, mutation.args, tuple(sorted(mutation.env.items())), mutation.go_test)
                if key not in baselines:
                    dest = out / "baseline" / mutation.name
                    if mutation.go_test:
                        test_binary = dest / ("oracle.test.exe" if os.name == "nt" else "oracle.test")
                        baseline_build = build(go, source, test_binary, env, args.build_timeout, mutation.go_test)
                        receipt, result = run_go_test(mutation, go, source, test_binary, dest, env, prefix)
                    else:
                        baseline_build = report["baselineBuild"]
                        receipt, result = run_consumer(mutation, baseline_binary, dest, env, prefix, tools_dir)
                    if receipt["exitCode"] != 0 or not all(row["pass"] for row in result.values()):
                        raise GateError(f"baseline failed: {mutation.suite}; no mutation is accepted")
                    baselines[key] = result
                    report["baseline"][mutation.name] = {"consumer": mutation.suite, "args": mutation.args,
                                                          "caseCount": len(result), "receipt": receipt,
                                                          "build": baseline_build}
                    if mutation.go_test:
                        report["baseline"][mutation.name]["goTest"] = asdict(mutation.go_test)
                    save(out / "results.json", report)
                print("BASELINE PASS", mutation.name, flush=True)
                dest = out / mutation.name
                dest.mkdir()
                row = {"variant": mutation.name, "origin": mutation.origin, "status": "error", "detected": False}
                if mutation.go_test:
                    row.update(consumerSource=mutation.suite, goTest=asdict(mutation.go_test))
                report["variants"].append(row)
                path = source / mutation.file
                original = path.read_text(encoding="utf-8")
                mutated = original
                try:
                    for before, after in mutation.replacements:
                        count = mutated.count(before)
                        if count != 1:
                            raise GateError(f"mutation selector must match once: {mutation.file}: found {count}: {before!r}")
                        mutated = mutated.replace(before, after, 1)
                    if mutated == original:
                        raise GateError("mutation did not alter the source")
                    (dest / "mutation.diff").write_text("".join(difflib.unified_diff(
                        original.splitlines(keepends=True), mutated.splitlines(keepends=True),
                        fromfile="a/" + mutation.file, tofile="b/" + mutation.file)), encoding="utf-8")
                    save(dest / "mutation.json", {"file": mutation.file, "origin": mutation.origin,
                         "beforeSha256": hashlib.sha256(original.encode()).hexdigest(),
                         "afterSha256": hashlib.sha256(mutated.encode()).hexdigest(),
                         "requiredAssertions": mutation.required, "consumerSource": mutation.suite,
                         "goTest": asdict(mutation.go_test) if mutation.go_test else None})
                    path.write_text(mutated, encoding="utf-8")
                    filename = "oracle.test" if mutation.go_test else "server"
                    binary = dest / (filename + ".exe" if os.name == "nt" else filename)
                    row["build"] = build(go, source, binary, env, args.build_timeout, mutation.go_test)
                    if mutation.go_test:
                        receipt, actual = run_go_test(mutation, go, source, binary, dest / "consumer", env, prefix)
                    else:
                        receipt, actual = run_consumer(mutation, binary, dest / "consumer", env, prefix, tools_dir)
                    row["consumer"] = receipt
                    row.update(classify(mutation, baselines[key], actual, receipt))
                except Exception as error:
                    row["error"] = str(error)
                finally:
                    path.write_text(original, encoding="utf-8")
                    save(out / "results.json", report)
                print(row["status"].upper(), mutation.name, row.get("error", ""), flush=True)
            report["pass"] = len(report["variants"]) == len(candidates) and all(row["detected"] for row in report["variants"])
    except Exception as error:
        report["error"] = str(error)
        print("GATE ERROR", error, file=sys.stderr, flush=True)
    save(out / "results.json", report)
    return 0 if report["pass"] else 1


def legacy_main(variants: list[str], default_output: str, group="core"):
    """Keep selected historical entry points while using the same strict gate."""
    output = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "artifacts" / default_output)
    return main([group, output, "--variants", *variants, *sys.argv[2:]])

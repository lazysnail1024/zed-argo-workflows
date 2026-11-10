#!/usr/bin/env python3
"""Configuration recovery and completion scope/cache behavior through real LSP."""
import argparse
import importlib.util
import json
import pathlib
import platform
import sys
import time
import traceback

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("binary", type=pathlib.Path)
parser.add_argument("output", type=pathlib.Path)
parser.add_argument("--cases", nargs="+", choices=("configuration", "completion"),
                    default=("configuration", "completion"))
args = parser.parse_args()
spec = importlib.util.spec_from_file_location("wire", pathlib.Path(__file__).with_name("wf-audit-gaps.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

PLAIN = """apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata: {name: local, namespace: argo}
spec:
  entrypoint: main
  templates:
    - name: main
      container: {image: alpine}
"""
SCOPES = m.CONFIG + """scopes:
  - name: selected
    include: ["callers/**", "alpha/**"]
  - name: other
    include: ["beta/**"]
"""


def setup(name, files, config=m.CONFIG):
    if (m.OUT / name).exists():
        raise RuntimeError("use a fresh output directory: " + str(m.OUT / name))
    return m.setup(name, files, config)


def codes(diagnostics):
    return sorted(d.get("code") for d in diagnostics)


def publication(client, path, version, after):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        matches = [x["params"] for x in client.received[after:]
                   if x.get("method") == "textDocument/publishDiagnostics"
                   and x["params"]["uri"] == path.as_uri()
                   and x["params"].get("version") == version]
        if matches:
            return matches[-1]["diagnostics"]
        if client.p.poll() is not None:
            raise RuntimeError("server exited before publication")
        time.sleep(.01)
    raise TimeoutError("missing current publication: " + path.as_uri())


def change_config(client, path, text, version, workflow):
    mark = len(client.received)
    client.send("textDocument/didChange", {
        "textDocument": {"uri": path.as_uri(), "version": version},
        "contentChanges": [{"text": text}],
    })
    return {
        "configuration": publication(client, path, version, mark),
        "workflow": publication(client, workflow, 1, mark),
    }


def configuration():
    broken = PLAIN.replace("  entrypoint:", "  parallelism: wrong\n  entrypoint:")
    dest, work = setup("configuration-recovery", {"workflow.yaml": broken})
    cfg, workflow = work / ".argo-workflows-ls.yaml", work / "workflow.yaml"
    client = m.LSP(dest, work)
    history = []
    checks = {}
    try:
        initial_config = client.open(cfg, m.CONFIG)
        initial_workflow = client.open(workflow, broken)
        checks["valid_config_clean"] = initial_config == []
        checks["independent_workflow_type_error"] = codes(initial_workflow) == ["SCHEMA_TYPE"]
        version = 1
        invalid_inputs = [
            ("unknown-target", m.CONFIG.replace("3.6.0", "99.99.99")),
            ("syntax", "schemaVersion: [\n"),
            ("unused-scope", m.CONFIG + 'scopes:\n  - name: unused\n    include: ["unused/**"]\n    targetVersion: "99.99.99"\n'),
        ]
        for name, invalid in invalid_inputs:
            version += 1
            unavailable = change_config(client, cfg, invalid, version, workflow)
            checks[name + "_config_error"] = (
                codes(unavailable["configuration"]) == ["WF_CONFIGURATION_ERROR"]
                and all(d.get("severity") == 1 and bool(d.get("message")) for d in unavailable["configuration"])
            )
            checks[name + "_workflow_unavailable"] = codes(unavailable["workflow"]) == ["WF_ANALYSIS_UNAVAILABLE"]
            version += 1
            recovered = change_config(client, cfg, m.CONFIG, version, workflow)
            checks[name + "_config_recovered_clean"] = recovered["configuration"] == []
            checks[name + "_workflow_error_restored"] = recovered["workflow"] == initial_workflow
            history.append({"change": name, "invalidText": invalid, "unavailable": unavailable, "recovered": recovered})
        mark = len(client.received)
        client.send("textDocument/didClose", {"textDocument": {"uri": cfg.as_uri()}})
        restored = publication(client, workflow, 1, mark)
        reopened = client.open(cfg, m.CONFIG, 1)
        checks["close_restores_disk_configuration"] = restored == initial_workflow
        checks["reopen_config_clean"] = reopened == []
        m.result("configuration-recovery", checks, {
            "initialConfiguration": initial_config, "initialWorkflow": initial_workflow,
            "transitions": history, "reopened": reopened,
        })
    finally:
        client.close()

    # A valid configuration is not a Workflow and is not incomplete merely
    # because some resource in its directory cannot be parsed.
    dest, work = setup("configuration-independent", {"broken.yaml": "apiVersion: [\n"})
    client = m.LSP(dest, work)
    try:
        path = work / ".argo-workflows-ls.yaml"
        accepted = client.open(path, m.CONFIG)
        experimental = client.change(path, m.CONFIG.replace("3.6.0", "3.5.0"), 2)
        m.result("configuration-independent", {
            "resource_failure_not_config_failure": accepted == [],
            "known_experimental_target_is_valid_configuration": experimental == [],
        }, {"accepted": accepted, "experimental": experimental})
    finally:
        client.close()


def labels(response):
    result = response.get("result")
    return sorted(x.get("label") for x in result) if isinstance(result, list) else []


def complete(client, path, text, needle, offset):
    return client.request("textDocument/completion", {
        "textDocument": {"uri": path.as_uri()}, "position": m.pos(text, needle, offset=offset),
    })


def library(template):
    return m.BASE.replace("work", template)


def completion():
    dest, work = setup("local-completion", {"workflow.yaml": PLAIN})
    client = m.LSP(dest, work)
    path = work / "workflow.yaml"
    try:
        client.open(path, PLAIN)
        first = complete(client, path, PLAIN, "entrypoint: main", 14)
        second = complete(client, path, PLAIN, "entrypoint: main", 14)
        fields = complete(client, path, PLAIN, "  templates:", 2)
        renamed = PLAIN.replace("main", "updated")
        client.change(path, renamed, 2)
        changed = complete(client, path, renamed, "entrypoint: updated", 14)
        m.result("local-completion", {
            "exact_local_name": labels(first) == ["main"],
            "repeat_exact_local_name": labels(second) == ["main"],
            "schema_fields_present": "activeDeadlineSeconds" in labels(fields),
            "changed_buffer_replaces_old_names": labels(changed) == ["updated"],
            "no_protocol_errors": all("error" not in r for r in [first, second, fields, changed]),
        }, {"first": first, "repeat": second, "fields": fields, "changed": changed})
    finally:
        client.close()

    caller = m.CALLER.replace("template: work", "template: wo")
    dest, work = setup("external-completion-cache", {
        "callers/caller.yaml": caller,
        "alpha/base.yaml": library("work-alpha"),
        "beta/base.yaml": library("work-beta"),
    }, SCOPES)
    client = m.LSP(dest, work)
    path, cfg = work / "callers/caller.yaml", work / ".argo-workflows-ls.yaml"
    responses = {}

    def ask(label):
        response = complete(client, path, caller, "template: wo", len("template: wo"))
        responses[label] = response
        return response

    try:
        client.open(path, caller)
        initial = ask("initial")
        cached = ask("repeat")
        # No watched-file notification: acquiring fresh scope inputs must still
        # invalidate an otherwise identical query's cached answer.
        alpha = work / "alpha/base.yaml"
        alpha.write_text(library("work-disk"))
        disk = ask("disk-change")
        alpha.unlink()
        deleted = ask("disk-delete")
        alpha.write_text(library("work-restored"))
        # A removed URI is no longer a known candidate. New membership is
        # event driven; the separate reconciliation suite omits this watcher
        # and requires natural discovery to converge within its real period.
        client.send('workspace/didChangeWatchedFiles', {'changes': [{'uri': alpha.as_uri(), 'type': 1}]})
        until = time.monotonic() + 5
        restored = ask("disk-restore")
        while labels(restored) != ["work-restored"] and time.monotonic() < until:
            time.sleep(.02)
            restored = ask("disk-restore")
        switched = SCOPES.replace('"alpha/**"', '"SWAP/**"').replace('"beta/**"', '"alpha/**"').replace('"SWAP/**"', '"beta/**"')
        client.open(cfg, switched)
        scope = ask("configuration-scope-change")
        beta = work / "beta/base.yaml"
        client.open(beta, library("work-overlay"))
        overlay = ask("unsaved-callee")
        client.send("textDocument/didClose", {"textDocument": {"uri": beta.as_uri()}})
        closed = ask("callee-close")
        immediate_close = closed
        until = time.monotonic() + 5
        while labels(closed) != ["work-beta"] and time.monotonic() < until:
            time.sleep(.02)
            closed = ask("callee-close")
        m.result("external-completion-cache", {
            "initial_scope_isolated": labels(initial) == ["work-alpha"],
            "repeat_exact_candidates": labels(cached) == ["work-alpha"],
            "disk_change_invalidates_query": labels(disk) == ["work-disk"],
            "disk_delete_invalidates_query": labels(deleted) == [],
            "disk_restore_invalidates_query": labels(restored) == ["work-restored"],
            "configuration_scope_invalidates_query": labels(scope) == ["work-beta"],
            "unsaved_callee_is_authoritative": labels(overlay) == ["work-overlay"],
            "callee_close_restores_disk": labels(closed) == ["work-beta"],
            "close_does_not_reuse_overlay_candidate": "work-overlay" not in labels(immediate_close),
            "no_protocol_errors": all("error" not in r for r in responses.values()),
        }, {"responses": responses, "changedConfiguration": switched})
    finally:
        client.close()

    # Explicitly opened excluded input has its own target identity. A multi-doc
    # file may resolve its own resource, but must not borrow another scope's.
    isolated = caller + "---\n" + library("work-isolated")
    dest, work = setup("isolated-completion", {
        "excluded/caller.yaml": isolated, "base.yaml": library("work-outside"),
    }, m.CONFIG + 'exclude: ["excluded/**"]\n')
    client = m.LSP(dest, work)
    path = work / "excluded/caller.yaml"
    try:
        client.open(path, isolated)
        response = complete(client, path, isolated, "template: wo", len("template: wo"))
        m.result("isolated-completion", {
            "only_isolated_target_candidates": labels(response) == ["work-isolated"],
            "no_protocol_error": "error" not in response,
        }, {"response": response})
    finally:
        client.close()

    implicit_namespace = caller.replace("name: caller, namespace: argo", "name: caller")
    isolated = (implicit_namespace + "---\n"
                + library("work-alpha").replace("namespace: argo", "namespace: alpha")
                + "---\n" + library("work-beta").replace("namespace: argo", "namespace: beta"))
    config = m.CONFIG + 'exclude: ["excluded/**"]\ndefaultNamespace: alpha\n'
    dest, work = setup("isolated-completion-namespace", {"excluded/caller.yaml": isolated}, config)
    client = m.LSP(dest, work)
    path, cfg = work / "excluded/caller.yaml", work / ".argo-workflows-ls.yaml"
    try:
        client.open(path, isolated)
        before = complete(client, path, isolated, "template: wo", len("template: wo"))
        repeated = complete(client, path, isolated, "template: wo", len("template: wo"))
        changed_config = client.open(cfg, config.replace("defaultNamespace: alpha", "defaultNamespace: beta"))
        after = complete(client, path, isolated, "template: wo", len("template: wo"))
        recovered_config = client.change(cfg, config, 2)
        restored = complete(client, path, isolated, "template: wo", len("template: wo"))
        m.result("isolated-completion-namespace", {
            "initial_effective_namespace": labels(before) == ["work-alpha"],
            "repeated_effective_namespace": labels(repeated) == ["work-alpha"],
            "default_namespace_invalidates_query": labels(after) == ["work-beta"],
            "restored_namespace_candidates": labels(restored) == ["work-alpha"],
            "config_changes_remain_clean": changed_config == [] and recovered_config == [],
            "no_protocol_errors": all("error" not in r for r in [before, repeated, after, restored]),
        }, {"before": before, "repeat": repeated, "after": after, "restored": restored})
    finally:
        client.close()


for name, run in [("configuration", configuration), ("completion", completion)]:
    if name not in args.cases:
        continue
    try:
        run()
    except Exception as error:
        m.result(name + "-runner", {"completed": False}, {
            "error": repr(error), "traceback": traceback.format_exc(),
        })

m.save(m.OUT / "manifest.json", {
    "command": sys.argv, "binarySha256": m.sha(m.BINARY),
    "testSha256": m.sha(pathlib.Path(__file__)),
    "clientSha256": m.sha(pathlib.Path(__file__).with_name("wf-audit-gaps.py")),
    "platform": platform.platform(), "python": sys.version,
    "cases": list(args.cases),
    "inputs": {str(p.relative_to(m.OUT)): m.sha(p) for p in m.OUT.rglob("*")
               if p.is_file() and "input" in p.parts},
})
sys.exit(0 if m.RESULTS and all(row["pass"] for row in m.RESULTS) else 1)

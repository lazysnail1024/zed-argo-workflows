#!/usr/bin/env python3
"""CI orchestration for the existing real-process E2E consumers (stdlib only)."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import runpy
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
E2E = ROOT / "tests/e2e"

# Explicit selection is intentional: several historical scripts require a
# synthetic binary, a second product, or a constrained Linux cgroup.
NATIVE = (
    "wf1", "wf-multi-version", "wf3-lsp", "wf5-edits", "wf-delivery",
    "wf-remediation-semantics", "wf-remediation-lifecycle",
    "wf-remediation-rename", "wf-remediation-targets",
    "wf-default-target-provenance", "wf-schema-queries",
    "wf-schema-query-targets", "wf-domain-queries", "wf-quickfixes",
    "wf-local-rename", "wf-bounded-scalars", "wf-bounded-completion",
    "wf-toolkit-budget", "wf-remediation-helm-boundary",
    "wf-reaudit-rename", "wf-reaudit-actions", "wf-reaudit-queries",
    "wf-reaudit-reconciliation", "wf-report-deadline",
)
EXTENDED = (
    "wf-audit-gaps", "wf-lifecycle", "wf-lifecycle-gaps",
    "wf-shutdown-active", "wf-bounded-config", "wf-bounded-input-files",
    "wf-bounded-scope-identity", "wf-bounded-schema-lifetime",
    "wf-bounded-external-counts", "wf-bounded-baseline",
    "wf-argument-supply", "wf-inheritance-family",
    "wf-inheritance-family-boundaries", "wf-aggregate-family",
    "wf-aggregate-transitions", "wf-dag-dependency-family",
    "wf-dag-dependency-boundaries", "wf-call-queries",
    "wf-inheritance-calls", "wf-inherited-workflow-parameters",
    "wf-domain-boundaries", "wf-domain-repair",
    "wf-domain-role-cardinality", "wf-domain-role-resources",
    "wf-domain-inexact-cardinality", "wf-domain-overlap",
    "wf-domain-ambiguous-references", "wf-domain-completion-context",
    "wf-domain-external-completion", "wf-domain-external-outputs",
    "wf-domain-external-references", "wf-external-candidates",
    "wf-semantic-origins", "wf-semantic-external-origins",
    "wf-semantic-external-arguments", "wf-semantic-shared-edits",
    "wf-projected-reference-diagnostics", "wf-projected-query-context",
    "wf-projected-lexical-bindings", "wf-query-fact-handoff",
    "wf-query-marker-collision", "wf-scope-glob-boundaries",
    "wf-preflight-budget", "wf-expr-error-text", "wf-schema-query-sibling",
    "wf-remediation-matrix", "wf-remediation-graph",
    "wf2-assets", "wf5-official", "wf5-render", "wf-remediation-render",
     "wf-reaudit-query-io",
)
AGGREGATED = {
    "wf-argument-supply": "case", "wf-inheritance-family": "version",
    "wf-inheritance-family-boundaries": "case", "wf-aggregate-family": "version",
    "wf-dag-dependency-family": "version", "wf-dag-dependency-boundaries": "case",
}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def host_platform():
    system = {"Linux": "linux", "Darwin": "darwin", "Windows": "windows"}[platform.system()]
    arch = {"x86_64": "amd64", "AMD64": "amd64", "aarch64": "arm64", "arm64": "arm64"}[platform.machine()]
    return system + "/" + arch


def terminate(process):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       capture_output=True, timeout=20, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=20)


def command(argv, evidence, *, timeout=300, env=None, cwd=ROOT):
    """Retain raw output even on timeout; a timeout is always a failed command."""
    evidence.mkdir(parents=True, exist_ok=True)
    argv = [str(x) for x in argv]
    record = {"command": argv, "cwd": str(cwd), "timeoutSeconds": timeout,
              "started": False, "timedOut": False, "exitCode": None, "pass": False}
    save(evidence / "command.json", record)
    started = time.monotonic()
    with (evidence / "stdout.log").open("wb") as stdout, (evidence / "stderr.log").open("wb") as stderr:
        try:
            options = {"start_new_session": True} if os.name != "nt" else {}
            process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=stdout, stderr=stderr, **options)
            record["started"] = True
            try:
                record["exitCode"] = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                record["timedOut"] = True
                terminate(process)
                record["exitCode"] = process.returncode
        except (OSError, subprocess.SubprocessError):
            record["error"] = traceback.format_exc()
    record["seconds"] = round(time.monotonic() - started, 3)
    record["pass"] = record["started"] and not record["timedOut"] and record["exitCode"] == 0
    save(evidence / "command.json", record)
    print(f'{"PASS" if record["pass"] else "FAIL"} {evidence.name} ({record["seconds"]:.2f}s)', flush=True)
    if not record["pass"]:
        for name in ("stdout.log", "stderr.log"):
            print((evidence / name).read_text(encoding="utf-8", errors="replace")[-6000:], flush=True)
    return record


def checked(argv, evidence, **kwargs):
    record = command(argv, evidence, **kwargs)
    if not record["pass"]:
        raise RuntimeError(f"command failed; see {evidence}")
    return (evidence / "stdout.log").read_text(encoding="utf-8")


def validate_results(path, *, pass_key="pass", assertion_key="assertions", require_assertions=False):
    """Never accept exit zero, missing evidence, empty rows, or truthy strings."""
    rows = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list) or not rows:
        raise ValueError("expected nonempty semantic results")
    for row in rows:
        if not isinstance(row, dict) or row.get(pass_key) is not True:
            raise ValueError(f"failed semantic result: {row}")
        if require_assertions or assertion_key in row:
            assertions = row.get(assertion_key)
            if not isinstance(assertions, dict) or not assertions or not all(v is True for v in assertions.values()):
                raise ValueError(f"non-boolean or failed assertions: {assertions}")
    return len(rows)


def consumer_results(name, destination):
    if name not in AGGREGATED:
        return validate_results(destination / "results.json")
    key = AGGREGATED[name]
    rows = json.loads((destination / "results.json").read_text(encoding="utf-8"))
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    expected = manifest["expectedTargets" if key == "version" else "expectedCases"]
    if not rows or not expected or [row[key] for row in rows] != expected:
        raise ValueError("worker selection is empty or incomplete")
    count = 0
    for row in rows:
        if row["exitCode"] != 0 or "--prepare" in row["command"]:
            raise ValueError("worker did not execute successfully")
        folder = destination / row[key]
        count += validate_results(folder / "result.json", assertion_key="checks", require_assertions=True)
        if (folder / "case-results.json").exists():
            count += validate_results(folder / "case-results.json", assertion_key="checks")
    return count


def summary(output, rows, classification):
    result = {"classification": classification, "platform": host_platform(), "tests": rows,
              "pass": bool(rows) and all(row["pass"] is True for row in rows),
              "editorAcceptance": "not-executed"}
    save(output / "summary.json", result)
    lines = [f"## {classification}", "", "| Check | Result | Seconds |", "| --- | --- | ---: |"]
    lines += [f'| {r["name"]} | {"PASS" if r["pass"] else "FAIL"} | {r.get("seconds", 0):.2f} |' for r in rows]
    lines += ["", "These are GitHub Runner / CLI / stdio LSP checks. Real Zed editor acceptance was not executed.", ""]
    text = "\n".join(lines)
    (output / "summary.md").write_text(text, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(text)
    return result["pass"]


def build(args):
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    env = os.environ | {"GOWORK": "off", "GOTOOLCHAIN": "local", "CGO_ENABLED": "1", "GOFLAGS": ""}
    expected = args.platform or host_platform()
    if expected != host_platform():
        raise ValueError(f"native execution required: requested {expected}, actual {host_platform()}")
    windows = expected == "windows/amd64"
    if windows:
        # Match the proven Argo CD native builder: inherited compiler/search
        # flags must not substitute an unreviewed static runtime or DLL import.
        allowed = {"PATH", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "TMPDIR", "TMP", "TEMP",
                   "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "LOCALAPPDATA", "APPDATA",
                   "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "GOROOT", "GOCACHE", "GOMODCACHE",
                   "GOTMPDIR", "CC"}
        env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        env.update(GOENV="off", GOWORK="off", GOTOOLCHAIN="local", CGO_ENABLED="1", GOFLAGS="",
                   GOPROXY="off", GOSUMDB="off", CGO_CFLAGS="-O2 -g", CGO_CPPFLAGS="",
                   CGO_CXXFLAGS="-O2 -g", CGO_FFLAGS="-O2 -g", CGO_LDFLAGS="-O2 -g", LC_ALL="C")
    binary = output / ("argo-workflows-ls.exe" if os.name == "nt" else "argo-workflows-ls")
    metadata = {"platform": expected, "python": sys.version, "machine": platform.platform(),
                "sourceCommit": checked(["git", "rev-parse", "HEAD"], output / "source").strip(),
                "sourceStatus": checked(["git", "status", "--porcelain"], output / "source-status"),
                "go": checked(["go", "version"], output / "go-version", env=env),
                "goEnvironment": checked(["go", "env", "-json"], output / "go-env", env=env),
                "compiler": checked([os.environ.get("CC", "cc"), "--version"], output / "compiler")}
    if windows:
        windows_spec = json.loads((ROOT / "scripts/windows-runtime.json").read_bytes())
        verify_windows_runtime = runpy.run_path(str(ROOT / "scripts/package-native.py"))["windows_runtime"]
        windows_legal = verify_windows_runtime(windows_spec, env=env)
        metadata["windowsRuntime"] = windows_spec
    save(output / "environment.json", metadata)
    release_version = args.release_version
    linker_flags = ""
    if release_version:
        release = runpy.run_path(str(ROOT / "scripts/release.py"))
        release["version_matches"](release_version)
        if metadata["sourceStatus"]:
            raise ValueError("release build requires an exact clean committed source")
        expected_go = "go" + next(line.split()[1] for line in (ROOT / "go.mod").read_text().splitlines() if line.startswith("go "))
        if json.loads(metadata["goEnvironment"])["GOVERSION"] != expected_go:
            raise ValueError("release build requires the pinned Go toolchain")
        module = "github.com/lazysnail1024/zed-argo-workflows/internal/buildinfo"
        linker_flags = f"-X {module}.ServerVersion={release_version} -X {module}.SourceCommit={metadata['sourceCommit']} -X {module}.Dirty=false -X {module}.BuildMode=release"
    if windows:
        linker_flags += ' -linkmode=external -extldflags "-static -Wl,-Map,workflows-native-link.map"'
    flags = ["-ldflags", linker_flags.strip()] if linker_flags else []
    compile_command = ["go", "build", "-mod=vendor", "-buildvcs=true", "-trimpath", *flags, "-o", binary, "./cmd/argo-workflows-ls"]

    def retain_windows_runtime(cwd):
        if not windows:
            return
        if verify_windows_runtime(windows_spec, env=env) != windows_legal:
            raise ValueError("Windows runtime inputs changed during native build")
        link_map = cwd / "workflows-native-link.map"
        if link_map.is_symlink() or not link_map.is_file() or link_map.stat().st_size > 128 * 1024 * 1024:
            raise ValueError("Missing or invalid Windows static linker map")
        retained = output / "native-link.map"
        shutil.move(link_map, retained)
        metadata["linkMap"] = {"file": retained.name, "sha256": sha(retained), "buildDirectory": str(cwd)}

    if release_version:
        # A disposable exact-commit worktree excludes ignored local Go/C/embed
        # files while retaining genuine Go VCS metadata for package verification.
        with tempfile.TemporaryDirectory(prefix="workflows-release-") as temporary:
            snapshot = Path(temporary) / "source"
            checked(["git", "worktree", "add", "--detach", snapshot, metadata["sourceCommit"]], output / "source-worktree")
            try:
                checked(compile_command, output / "compile", timeout=600, env=env, cwd=snapshot)
                retain_windows_runtime(snapshot)
                checked(["go", "vet", "-mod=vendor", "./..."], output / "vet", timeout=300, env=env, cwd=snapshot)
            finally:
                checked(["git", "worktree", "remove", "--force", snapshot], output / "source-worktree-cleanup")
    else:
        checked(compile_command, output / "compile", timeout=600, env=env)
        retain_windows_runtime(ROOT)
        checked(["go", "vet", "-mod=vendor", "./..."], output / "vet", timeout=300, env=env)
    checked(["go", "version", "-m", binary], output / "native-build", env=env)
    identity = json.loads(checked([binary, "version", "--json"], output / "identity"))
    if identity.get("product") != "argo-workflows" or identity.get("analysisReady") is not True:
        raise ValueError(f"wrong native identity: {identity}")
    if identity.get("defaultTargetVersion") != "auto" or not identity.get("acceptedTargetVersions"):
        raise ValueError(f"unexpected delivery targets: {identity}")
    if release_version and any(identity.get(key) != value for key, value in {
            "serverVersion": release_version, "sourceCommit": metadata["sourceCommit"],
            "dirty": False, "buildMode": "release"}.items()):
        raise ValueError("release identity differs from the requested source/version")
    save(output / "build.json", metadata | {"binary": str(binary), "binarySha256": sha(binary), "identity": identity})
    return True


def suite(args):
    names = NATIVE if args.suite == "native" else EXTENDED
    if args.shard:
        index, total = (int(value) for value in args.shard.split("/"))
        if not 0 <= index < total or total > len(names):
            raise ValueError("invalid shard; use a zero-based index/total with at least one test")
        names = names[index::total]
    rows = []
    for name in names:
        destination = args.output / name
        argv = [sys.executable, E2E / (name + ".py"), args.binary, destination]
        if name in ("wf-inheritance-family", "wf-aggregate-family", "wf-dag-dependency-family"):
            argv += ["--targets", "3.6.0"]
        env = os.environ.copy()
        if args.tools:
            env["WF_TEST_ARGO_BINARY"] = str(args.tools / "argo")
        if name in ("wf5-official", "wf5-render", "wf-remediation-render"):
            if not args.tools:
                raise ValueError("explicit offline integration tools are required")
            argv.insert(3, args.tools / ("argo" if name == "wf5-official" else "helm"))
        row = command(argv, args.output / "logs" / name, timeout=300, env=env)
        row["name"] = name
        try:
            row["caseCount"] = consumer_results(name, destination)
            row["resultsSha256"] = sha(destination / "results.json")
        except (OSError, ValueError, TypeError):
            row["pass"] = False
            row["evidenceError"] = traceback.format_exc()
            print(row["evidenceError"], flush=True)
        rows.append(row)
        summary(args.output, rows, args.suite + " E2E")
    return summary(args.output, rows, args.suite + " E2E")


def package(args):
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    source = output / "source.tar"
    checked(["git", "archive", "--format=tar", "--output", source, "HEAD"], output / "archive")
    folder = output / "package-e2e"
    checked([sys.executable, E2E / "native-package.py", args.binary, source, folder], output / "package-e2e-command", timeout=600)
    count = validate_results(folder / "results.json", pass_key="passed", assertion_key="checks", require_assertions=True)
    package_json = checked([sys.executable, ROOT / "scripts/package-native.py", "--binary", args.binary,
                            "--sha256", sha(args.binary), "--platform", host_platform(),
                            "--source-archive", source, "--source-sha256", sha(source),
                            "--output", output / "dist"], output / "package-command", timeout=300)
    produced = json.loads(package_json)
    manifest = json.loads((Path(produced["folder"]) / "native/manifest.json").read_text(encoding="utf-8"))
    if manifest.get("sourceIdentity", {}).get("status") != "verified-vcs":
        raise ValueError("CI packages require a clean native build tied to the exact source archive")
    tested = json.loads((folder / "first.stdout").read_text(encoding="utf-8"))
    if sha(Path(produced["zip"])) != produced["zipSha256"] or produced["zipSha256"] != tested["zipSha256"]:
        raise ValueError("delivered ZIP differs from the integrity-tested deterministic bundle")
    if manifest["binarySha256"] != sha(args.binary) or manifest["sourceArchiveSha256"] != sha(source):
        raise ValueError("delivered package inputs do not match the tested inputs")
    save(output / "package.json", produced | {"sourceCommit": manifest["sourceIdentity"]["nativeCommit"],
                                              "semanticCaseCount": count})
    (output / "dist/SHA256SUMS").write_text(f'{produced["zipSha256"]}  {Path(produced["zip"]).name}\n', encoding="utf-8")
    return True


def mutations(args):
    argv = [sys.executable, E2E / "ablate-ci.py", args.group, args.output / "gate"]
    if args.group == "resources":
        if platform.system() != "Linux":
            raise ValueError("resource mutation gate requires Linux")
        prefix = ["sudo", "-n", "systemd-run", "--wait", "--pipe", "--collect", "--quiet",
                  "--service-type=exec", "--uid=" + str(os.getuid()), "--gid=" + str(os.getgid()),
                  "--working-directory=" + str(ROOT), "-p", "MemoryMax=512M", "-p", "MemorySwapMax=0",
                  "-p", "RuntimeMaxSec=180", "-p", "TimeoutStopSec=5", "-p", "KillMode=control-group",
                  "-p", "SendSIGKILL=yes", "-p", "OOMPolicy=kill", "-p", "TasksMax=128", "--"]
        argv += ["--test-prefix-json", json.dumps(prefix)]
    row = command(argv, args.output / "driver", timeout=1200)
    row["name"] = args.group + " mutations"
    report_path = args.output / "gate/results.json"
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        variants = report.get("variants", [])
        row["pass"] = row["pass"] and report.get("pass") is True and bool(variants)
        row["pass"] = row["pass"] and len(variants) == len(report.get("selected", []))
        row["pass"] = row["pass"] and all(v.get("status") == "killed" and v.get("detected") is True for v in variants)
        row["caseCount"] = len(variants)
    else:
        row["pass"] = False
    return summary(args.output, [row], args.group + " semantic mutation gate")


def schema(args):
    """Existing synthetic schema fixtures test query logic separately from CRDs."""
    env = os.environ | {"GOROOT": subprocess.check_output(["go", "env", "GOROOT"], text=True).strip()}
    synthetic = args.output / "synthetic-build"
    checked([sys.executable, E2E / "build-wf-query-contract.py", synthetic], args.output / "build-log", env=env, timeout=600)
    cases = [("actual-boundaries", "wf-schema-query-boundaries", args.binary, ["actual"]),
             ("synthetic-contract", "wf-schema-query-contract", synthetic / "server", []),
             ("synthetic-boundaries", "wf-schema-query-boundaries", synthetic / "server", ["synthetic"]),
             ("synthetic-projection", "wf-projected-query-context", synthetic / "server", ["synthetic"])]
    rows = []
    for name, script, binary, extra in cases:
        destination = args.output / name
        row = command([sys.executable, E2E / (script + ".py"), binary, destination, *extra],
                      args.output / "logs" / name, timeout=300)
        row["name"] = name
        try:
            row["caseCount"] = validate_results(destination / "results.json")
        except (OSError, ValueError, TypeError) as error:
            row.update({"pass": False, "evidenceError": str(error)})
        rows.append(row)
    return summary(args.output, rows, "Actual CRD and separate synthetic query contracts")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("build")
    p.add_argument("--platform")
    p.add_argument("--release-version", default="", help="Build the pinned clean stable release from its exact source commit")
    p = sub.add_parser("suite")
    p.add_argument("--suite", choices=("native", "extended"), required=True)
    p.add_argument("--binary", type=Path, required=True)
    p.add_argument("--shard", help="optional zero-based index/total")
    p.add_argument("--tools", type=Path, help="directory with pinned offline Argo/Helm tools")
    p = sub.add_parser("package")
    p.add_argument("--binary", type=Path, required=True)
    p = sub.add_parser("mutations")
    p.add_argument("--group", choices=("core", "audit", "resources"), required=True)
    p = sub.add_parser("schema")
    p.add_argument("--binary", type=Path, required=True)
    for p in sub.choices.values():
        p.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    if hasattr(args, "binary"):
        args.binary = args.binary.resolve()
    if getattr(args, "tools", None):
        args.tools = args.tools.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        return 0 if globals()[args.action](args) else 1
    except Exception:
        save(args.output / "fatal.json", {"pass": False, "error": traceback.format_exc()})
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Real current-host release delivery, with separately labelled aggregation controls."""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
PLATFORMS = {"linux/amd64", "linux/arm64", "darwin/amd64", "darwin/arm64", "windows/amd64"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def synthetic_platform(folder, platform, template, template_files, *, omit=None,
                       different_binary=False, unsafe=False, wrong_source=False,
                       changed_license=False, wrong_go_platform=False, non_system_runtime=False,
                       changed_windows_notice=False, wrong_windows_runtime=False):
    """Parser inputs only: these bytes never claim to be executed native servers."""
    folder.mkdir(parents=True)
    suffix = platform.replace("/", "-")
    version = template["version"]
    root = f"argo-workflows-ls-{version}-{suffix}"
    binary_asset = f"argo-workflows-ls-{version}-{suffix}" + (".exe" if platform.startswith("windows/") else "")
    archive_name = f"argo-workflows-ls-{version}-{suffix}.zip"
    executable = "argo-workflows-ls" + (".exe" if platform.startswith("windows/") else "")
    binary = b"Synthetic aggregate fixture, not a native executable: " + platform.encode()
    files = {name: value for name, value in template_files.items()
             if name not in ("native/manifest.json", "argo-workflows-ls", "argo-workflows-ls.exe")}
    files[executable] = b"different archive bytes" if different_binary else binary
    package = json.loads(encoded(template["package"]))
    package.update(platform=platform, binarySha256=sha(binary), binarySize=len(binary))
    if platform.startswith("windows/"):
        package["windowsRuntime"] = json.loads(files["provenance/scripts/windows-runtime.json"])
        if wrong_windows_runtime:
            package["windowsRuntime"]["runtimeFiles"]["bin/gcc.exe"] = "0" * 64
    else:
        package.pop("windowsRuntime", None)
    if wrong_source:
        package["sourceIdentity"]["nativeCommit"] = "0" * 40
    os_name, arch = platform.split("/")
    metadata = []
    for line in files["provenance/native-build.txt"].decode().splitlines():
        if line.startswith("\tbuild\tGOOS="):
            line = "\tbuild\tGOOS=" + os_name
        if line.startswith("\tbuild\tGOARCH="):
            line = "\tbuild\tGOARCH=" + ("wrong" if wrong_go_platform else arch)
        metadata.append(line)
    files["provenance/native-build.txt"] = ("\n".join(metadata) + "\n").encode()
    if omit:
        del files[omit]
    if unsafe:
        files["../escape"] = b"unsafe"
    if changed_license:
        vendor_notice = next(name for name in files if name.startswith("licenses/modules/"))
        files[vendor_notice] = b"rewritten notice despite a self-consistent checksum"
    if changed_windows_notice:
        files["licenses/windows-runtime/winpthreads/COPYING"] = b"rewritten static runtime notice"
    package["filesSha256"] = {name: sha(value) for name, value in sorted(files.items())}
    manifest = encoded(package)
    files["native/manifest.json"] = manifest
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, value in sorted(files.items()):
            info = zipfile.ZipInfo(root + "/" + name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o100755 if name == executable else 0o100644) << 16
            archive.writestr(info, value)
    archive = buffer.getvalue()
    if platform.startswith("windows/"):
        libraries = ["KERNEL32.dll", "msvcrt.dll"]
        if non_system_runtime:
            libraries.append("libwinpthread-1.dll")
        libraries.sort()
        runtime = {"tool": "objdump -p", "exitCode": 0, "libraries": libraries,
                   "output": "Synthetic aggregation control only\n" + "".join(
                       "\tDLL Name: " + name + "\n" for name in libraries)}
    else:
        runtime = {"tool": "ldd" if platform.startswith("linux/") else "otool -L",
                   "exitCode": 0, "output": "Synthetic aggregation control only\n"}
    record = {"schemaVersion": 1, "product": "argo-workflows", "platform": platform,
              "version": version, "sourceCommit": template["sourceCommit"],
              "binaryAsset": binary_asset, "archiveAsset": archive_name, "archiveRoot": root,
              "binarySha256": sha(binary), "archiveSha256": sha(archive),
              "manifestSha256": sha(manifest), "package": package,
              "runtimeDependencies": runtime}
    (folder / binary_asset).write_bytes(binary)
    (folder / archive_name).write_bytes(archive)
    (folder / ("release-" + suffix + ".json")).write_bytes(encoded(record))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=sorted(PLATFORMS), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    fixture = output / "synthetic-source-history"
    rows, completed = [], False

    def record(name, checks, **details):
        rows.append({"id": name, "checks": checks,
                     "passed": bool(checks) and all(value is True for value in checks.values()), **details})

    def invoke(name, command, expected_error=None, *, timeout=300, env=None, cwd=None):
        process = subprocess.run([str(value) for value in command], cwd=cwd or fixture,
                                 capture_output=True, timeout=timeout, env=env)
        log = output / "logs" / name
        log.mkdir(parents=True)
        (log / "stdout").write_bytes(process.stdout)
        (log / "stderr").write_bytes(process.stderr)
        checks = {"expectedExit": process.returncode == (2 if expected_error else 0)}
        if expected_error:
            checks["specificRefusal"] = expected_error.lower() in process.stderr.decode(errors="replace").lower()
        record(name, checks, command=[str(value) for value in command], exit=process.returncode)
        if not all(checks.values()):
            raise AssertionError(name + ": " + process.stderr.decode(errors="replace")[-4000:]
                                 + process.stdout.decode(errors="replace")[-2000:])
        return process

    try:
        fixture.mkdir()
        names = subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT).decode().split("\0")
        for name in sorted(set(names) - {""}):
            if name.startswith("vendor/"):
                continue
            source = ROOT / name
            if not source.exists():  # A tracked deletion is absent from the tested source.
                continue
            if source.is_symlink() or not source.is_file():
                raise ValueError("Source fixture requires regular files")
            destination = fixture / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        env = os.environ | {"GIT_AUTHOR_NAME": "Release E2E fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                            "GIT_COMMITTER_NAME": "Release E2E fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
                            "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z"}
        for command in (["git", "init"], ["git", "-c", "core.autocrlf=false", "add", "."],
                        ["git", "-c", "commit.gpgsign=false", "commit", "-m", "Synthetic release E2E history only"]):
            subprocess.run(command, cwd=fixture, env=env, capture_output=True, check=True)
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=fixture, text=True).strip()
        version = (fixture / "VERSION").read_text().strip()
        tool = [sys.executable, fixture / "scripts/release.py"]
        common = ["--version", version, "--source-commit", commit]
        invoke("stable-tag", tool + ["version", "--tag", "v" + version, "--source-commit", commit])
        invoke("tag-version-mismatch", tool + ["version", "--tag", "v999.0.0", "--source-commit", commit], "must match")
        invoke("unsafe-tag", tool + ["version", "--tag", "v../escape", "--source-commit", commit], "stable semantic version")
        build = output / "native-release"
        invoke("build-real-native-release",
               [sys.executable, fixture / "tests/ci/run.py", "build", "--platform", args.platform,
                "--release-version", version, "--output", build], timeout=900)
        build_file = build / "build.json"
        build_record = json.loads(build_file.read_bytes())
        if args.platform == "windows/amd64":
            spec = json.loads((fixture / "scripts/windows-runtime.json").read_bytes())
            link_map = build_record["linkMap"]
            link_bytes = (build / link_map["file"]).read_bytes()
            record("pinned-windows-static-link", {
                "runtimeInputsPinned": build_record["windowsRuntime"] == spec,
                "linkerEvidenceRetained": bool(link_bytes) and sha(link_bytes) == link_map["sha256"],
                "pthreadStaticArchiveLinked": b"libwinpthread.a" in link_bytes or b"libpthread.a" in link_bytes})
        stage = output / "stage-one"
        stage_args = ["stage", "--build", build_file, *common]
        invoke("stage-real-native-release", tool + stage_args + ["--output", stage])
        second = output / "stage-two"
        invoke("repeat-staging", tool + stage_args + ["--output", second])
        first_bytes = {path.name: path.read_bytes() for path in stage.iterdir()}
        record("deterministic-staging", {
            "sameInventoryAndBytes": first_bytes == {path.name: path.read_bytes() for path in second.iterdir()}})
        stage_record = json.loads(next(stage.glob("release-*.json")).read_bytes())
        runtime = stage_record["runtimeDependencies"]
        record("native-runtime-inspected", {
            "actualNativeToolSucceeded": runtime["exitCode"] == 0,
            "platformTool": runtime["tool"] == {
                "linux": "ldd", "darwin": "otool -L", "windows": "objdump -p"
            }[args.platform.split("/")[0]],
            "nonemptyEvidence": bool(runtime["output"].strip()),
            "nativeNotSynthetic": "Synthetic aggregation control" not in runtime["output"]})
        executable = stage / stage_record["binaryAsset"]
        identity = json.loads(invoke("execute-staged-native", [executable, "version", "--json"]).stdout)
        record("exact-native-identity", {"sameIdentity": identity == build_record["identity"],
                                         "release": identity["buildMode"] == "release",
                                         "exactCommit": identity["sourceCommit"] == commit})
        if args.platform == "windows/amd64":
            # A developer runner has MinGW DLLs on PATH. Launch the raw asset
            # with only the OS search directory and no adjacent dependency files.
            isolated = output / "standalone-windows-startup"
            isolated.mkdir()
            standalone = isolated / executable.name
            shutil.copyfile(executable, standalone)
            system32 = Path(os.environ["SYSTEMROOT"]) / "System32"
            isolated_env = os.environ | {"PATH": str(system32)}
            observed = json.loads(invoke("execute-without-mingw-path",
                                        [standalone, "version", "--json"], env=isolated_env,
                                        cwd=isolated).stdout)
            record("standalone-windows-identity", {
                "exactIdentityWithoutToolchainDLLs": observed == identity,
                "onlyRawExecutableRequired": list(isolated.iterdir()) == [standalone]})
        with zipfile.ZipFile(stage / stage_record["archiveAsset"]) as archive:
            prefix = stage_record["archiveRoot"] + "/"
            template_files = {name[len(prefix):]: archive.read(name) for name in archive.namelist()}
        record("separate-native-archive", {
            "nativeOutsideExtension": ("argo-workflows-ls" + (".exe" if args.platform.startswith("windows/") else "")) in template_files,
            "noBundledPayload": not any(name.startswith("extension/") for name in template_files),
            "allSourceProvenance": stage_record["package"]["sourceIdentity"] == {
                "status": "verified-vcs", "archiveCommit": commit, "nativeCommit": commit, "nativeDirty": False}})
        invoke("existing-output-refused", tool + stage_args + ["--output", stage], "already exist")
        invoke("source-commit-mismatch", tool + ["stage", "--build", build_file, "--version", version,
               "--source-commit", "0" * 40, "--output", output / "bad-commit"], "exact clean committed source")
        readme = fixture / "README.md"
        original = readme.read_bytes()
        readme.write_bytes(original + b"\nchanged\n")
        invoke("dirty-source-refused", tool + stage_args + ["--output", output / "dirty"], "exact clean committed source")
        readme.write_bytes(original)
        invalid = json.loads(build_file.read_bytes())
        invalid["identity"]["buildMode"] = "development"
        development = build / "development.json"
        development.write_bytes(encoded(invalid))
        invoke("development-identity-refused", tool + ["stage", "--build", development, *common,
               "--output", output / "development"], "clean pinned release")
        native = Path(build_record["binary"])
        original_binary = native.read_bytes()
        native.write_bytes(original_binary + b"tampered")
        invoke("tampered-native-refused", tool + stage_args + ["--output", output / "tampered"],
               "Native binary checksum differs")
        native.write_bytes(original_binary)
        inputs = output / "aggregate-controls"
        inputs.mkdir()
        for platform in sorted(PLATFORMS):
            destination = inputs / platform.replace("/", "-")
            if platform == args.platform:
                shutil.copytree(stage, destination)
            else:
                synthetic_platform(destination, platform, stage_record, template_files)
        collect = ["collect", "--inputs", inputs, *common]
        combined = output / "combined"
        invoke("aggregate-five-platform-controls", tool + collect + ["--output", combined])
        lines = (combined / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        values = {name: digest for digest, name in (line.split("  ") for line in lines)}
        record("complete-checksum-manifest", {
            "allFifteenAssets": len(lines) == len(values) == 15,
            "everyDigestMatches": all(sha((combined / name).read_bytes()) == digest for name, digest in values.items()),
            "noSelfDigest": "SHA256SUMS" not in values})
        invoke("existing-collection-refused", tool + collect + ["--output", combined], "already exist")
        chosen = inputs / "linux-amd64"
        held = output / "held-platform"
        chosen.rename(held)
        invoke("missing-platform-refused", tool + collect + ["--output", output / "missing-platform"], "all five")
        held.rename(chosen)
        duplicate = inputs / "duplicate"
        shutil.copytree(chosen, duplicate)
        invoke("duplicate-platform-refused", tool + collect + ["--output", output / "duplicate-platform"],
               "Duplicate release platform")
        shutil.rmtree(duplicate)
        record_file = next(chosen.glob("release-*.json"))
        saved = record_file.read_bytes()
        altered = json.loads(saved)
        altered["sourceCommit"] = "0" * 40
        record_file.write_bytes(encoded(altered))
        invoke("mixed-source-provenance-refused", tool + collect + ["--output", output / "mixed-source"],
               "source commit mismatch")
        record_file.write_bytes(saved)
        extra = chosen / "unexpected.bin"
        extra.write_bytes(b"unexpected")
        invoke("extra-asset-refused", tool + collect + ["--output", output / "extra"], "Unexpected or missing")
        extra.unlink()
        for name, options, reason in (
                ("missing-go-license", {"omit": "licenses/go/LICENSE"}, "missing required legal"),
                ("different-archive-native", {"different_binary": True}, "differs from the legal archive"),
                ("unsafe-archive", {"unsafe": True}, "Unsafe or duplicate"),
                ("altered-vendor-notice", {"changed_license": True}, "source/license inventory"),
                ("mixed-native-commit", {"wrong_source": True}, "exact clean source commit"),
                ("wrong-native-platform", {"wrong_go_platform": True}, "Go platform or VCS metadata")):
            chosen.rename(held)
            synthetic_platform(chosen, "linux/amd64", stage_record, template_files, **options)
            invoke(name + "-refused", tool + collect + ["--output", output / name], reason)
            shutil.rmtree(chosen)
            held.rename(chosen)
        windows = inputs / "windows-amd64"
        held_windows = output / "held-windows"
        for name, options, reason in (
                ("non-system-windows-runtime", {"non_system_runtime": True}, "Non-system Windows runtime dependency"),
                ("altered-windows-runtime-notice", {"changed_windows_notice": True}, "source/license inventory"),
                ("wrong-windows-runtime-provenance", {"wrong_windows_runtime": True}, "Windows runtime provenance differs")):
            windows.rename(held_windows)
            synthetic_platform(windows, "windows/amd64", stage_record, template_files, **options)
            invoke(name + "-refused", tool + collect + ["--output", output / name], reason)
            shutil.rmtree(windows)
            held_windows.rename(windows)
        completed = True
    except Exception as error:
        record("runner", {"completed": False}, error=repr(error))
    result = {"scope": "Real current-host native release staging; other-platform aggregation records are synthetic controls, not native platform execution or a published product release.",
              "platform": args.platform, "completed": completed, "rows": rows,
              "passed": completed and bool(rows) and all(row["passed"] for row in rows),
              "editorAcceptance": "not-executed"}
    (output / "report.json").write_bytes(encoded(result))
    print(json.dumps({"cases": len(rows), "passed": result["passed"],
                      "failed": [row["id"] for row in rows if not row["passed"]]}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

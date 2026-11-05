#!/usr/bin/env python3
"""Stage and join pinned native releases using the existing verified dev packager."""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import runpy
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = {"linux/amd64", "linux/arm64", "darwin/amd64", "darwin/arm64", "windows/amd64"}
MAX_ASSET = 128 * 1024 * 1024
WINDOWS_SYSTEM_DLLS = {
    "kernel32.dll", "msvcrt.dll", "ntdll.dll", "ws2_32.dll", "advapi32.dll",
    "shell32.dll", "user32.dll", "iphlpapi.dll", "bcrypt.dll", "secur32.dll",
    "crypt32.dll", "ole32.dll", "oleaut32.dll", "combase.dll", "ucrtbase.dll",
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def content(path, limit=MAX_ASSET):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError("Expected bounded regular file: " + str(path))
    with path.open("rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Expected regular file: " + str(path))
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Release input exceeds its size limit: " + str(path))
    return data


def version_matches(version):
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", version):
        raise ValueError("Release version must be a stable semantic version")
    cargo = tomllib.loads(content(ROOT / "Cargo.toml").decode())
    extension = tomllib.loads(content(ROOT / "extension.toml").decode())
    if cargo["package"]["version"] != version or extension["version"] != version:
        raise ValueError("Release version must match Cargo.toml and extension.toml")


def clean_source(commit):
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Expected an exact source commit")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT)
    if head != commit or dirty:
        raise ValueError("Release assets require the exact clean committed source")


def identity_matches(identity, version, commit):
    expected = {"product": "argo-workflows", "serverVersion": version,
                "adapterContractVersion": 1, "analysisReady": True,
                "configSchemaVersions": [1], "reportSchemaVersions": [1],
                "defaultTargetVersion": "3.6.0", "acceptedTargetVersions": ["3.6.0"],
                "sourceCommit": commit, "dirty": False, "buildMode": "release"}
    if not isinstance(identity, dict) or any(identity.get(key) != value for key, value in expected.items()):
        raise ValueError("Native identity must match the clean pinned release and source commit")
    if not isinstance(identity.get("toolkitVersion"), str) or not identity["toolkitVersion"]:
        raise ValueError("Native release is missing its toolkit identity")
    if not re.fullmatch(r"[0-9a-f]{64}", identity.get("catalogHash", "")):
        raise ValueError("Native release is missing its catalog SHA-256")


def asset_name(version, platform):
    if platform not in PLATFORMS:
        raise ValueError("Unsupported release platform")
    return f"argo-workflows-ls-{version}-{platform.replace('/', '-')}" + (
        ".exe" if platform.startswith("windows/") else "")


def validate_runtime(runtime, platform):
    expected_tool = {"linux": "ldd", "darwin": "otool -L", "windows": "objdump -p"}[platform.split("/")[0]]
    if (not isinstance(runtime, dict) or runtime.get("tool") != expected_tool
            or runtime.get("exitCode") != 0 or not isinstance(runtime.get("output"), str)
            or not runtime["output"].strip()):
        raise ValueError("Missing native runtime dependency inspection")
    if platform.startswith("windows/"):
        libraries = sorted(set(re.findall(r"DLL Name:\s*(\S+)", runtime["output"])))
        if not libraries or libraries != runtime.get("libraries"):
            raise ValueError("Windows runtime import inventory differs from objdump evidence")
        unsupported = [name for name in libraries
                       if name.lower() not in WINDOWS_SYSTEM_DLLS
                       and not name.lower().startswith("api-ms-win-")]
        if unsupported:
            raise ValueError("Non-system Windows runtime dependency requires reviewed redistribution: "
                             + ", ".join(unsupported))


def runtime_dependencies(binary, platform):
    command = (["ldd"] if platform.startswith("linux/") else
               ["otool", "-L"] if platform.startswith("darwin/") else ["objdump", "-p"])
    process = subprocess.run([*command, str(binary)], cwd=ROOT, capture_output=True,
                             text=True, timeout=30)
    if process.returncode:
        raise ValueError("Native runtime dependency inspection failed: " + process.stderr.strip())
    # ldd includes ASLR addresses, which are not dependency identity. Normalize
    # them so repeated staging of the same native build remains deterministic.
    runtime = {"tool": " ".join(command), "exitCode": process.returncode,
               "output": re.sub(r"0x[0-9a-fA-F]+", "ADDRESS", process.stdout)}
    if platform.startswith("windows/"):
        runtime["libraries"] = sorted(set(re.findall(r"DLL Name:\s*(\S+)", runtime["output"])))
    validate_runtime(runtime, platform)
    return runtime


def source_inventory(commit, destination):
    """Use the packager's selection/validation, including all original licenses."""
    subprocess.run(["git", "archive", "--format=tar", "--output", str(destination), commit],
                   cwd=ROOT, check=True, capture_output=True, timeout=120)
    source = content(destination, 256 * 1024 * 1024)
    packager = runpy.run_path(str(ROOT / "scripts/package-dev.py"))
    selected, archived_commit = packager["source_files"](source)
    if archived_commit != commit:
        raise ValueError("Source archive does not identify the exact release commit")
    return digest(source), selected


def output_directory(output):
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("Output must not already exist")
    try:
        relative = output.relative_to(ROOT)
    except ValueError:
        relative = None
    if relative is not None and (not relative.parts or relative.parts[0] not in ("artifacts", "build", "dist")):
        raise ValueError("In-checkout outputs must be under artifacts, build or dist")
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def publish_directory(output, files):
    output = output_directory(output)
    with tempfile.TemporaryDirectory(prefix=".release-stage-", dir=output.parent) as temporary:
        stage = Path(temporary) / "assets"
        stage.mkdir()
        for name, value in sorted(files.items()):
            path = stage / name
            path.write_bytes(value)
            path.chmod(0o755 if name.startswith("argo-workflows-ls-") and not name.endswith(".zip") else 0o644)
        stage.rename(output)


def archive_files(data, root):
    files, total = {}, 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for entry in archive.infolist():
            path = PurePosixPath(entry.filename)
            if (not entry.filename.startswith(root + "/") or path.is_absolute()
                    or ".." in path.parts or "\\" in entry.filename or entry.filename in files):
                raise ValueError("Unsafe or duplicate release archive member")
            if (entry.is_dir() or (entry.external_attr >> 16) & 0o170000 != 0o100000
                    or entry.file_size > MAX_ASSET):
                raise ValueError("Release archive must contain bounded regular files")
            total += entry.file_size
            if total > 256 * 1024 * 1024:
                raise ValueError("Release archive content exceeds its size limit")
            files[entry.filename] = archive.read(entry)
    return {name[len(root) + 1:]: value for name, value in files.items()}


def validate_record(record, files, version, commit, source_hash, expected_source):
    platform = record.get("platform")
    binary_name = asset_name(version, platform)
    archive_name = f"argo-workflows-ls-{version}-{platform.replace('/', '-')}.zip"
    root = f"zed-argo-workflows-dev-{platform.replace('/', '-')}"
    if (record.get("schemaVersion") != 1 or record.get("product") != "argo-workflows"
            or record.get("version") != version or record.get("sourceCommit") != commit):
        raise ValueError("Release record version, product or source commit mismatch")
    if (record.get("binaryAsset") != binary_name or record.get("archiveAsset") != archive_name
            or record.get("archiveRoot") != root):
        raise ValueError("Release asset name or platform mismatch")
    validate_runtime(record.get("runtimeDependencies"), platform)
    binary, archive = files[binary_name], files[archive_name]
    if (not binary or digest(binary) != record.get("binarySha256")
            or digest(archive) != record.get("archiveSha256")):
        raise ValueError("Release binary or archive SHA-256 mismatch")
    members = archive_files(archive, root)
    manifest_bytes = members.get("native/manifest.json", b"")
    if digest(manifest_bytes) != record.get("manifestSha256"):
        raise ValueError("Release package manifest SHA-256 mismatch")
    package = json.loads(manifest_bytes)
    if package != record.get("package"):
        raise ValueError("Archived package provenance differs from release record")
    if (package.get("schemaVersion") != 1 or package.get("product") != "argo-workflows"
            or package.get("platform") != platform):
        raise ValueError("Native package identity or platform mismatch")
    actual_hashes = {name: digest(value) for name, value in members.items() if name != "native/manifest.json"}
    if actual_hashes != package.get("filesSha256"):
        raise ValueError("Release archive file inventory or checksum mismatch")
    required = {"extension/LICENSE", "licenses/go/LICENSE", "licenses/go/PATENTS",
                "licenses/schemas/LICENSE", "provenance/native-build.txt",
                "provenance/native-version.stdout", "provenance/native-version.stderr"}
    if not required.issubset(members):
        raise ValueError("Release archive is missing required legal or provenance files")
    # Reuse the package source selector to compare every native notice and
    # adapter/config source byte against the exact committed source, even if
    # someone also rewrote the package's own checksum inventory.
    if any(members.get(name) != value for name, value in expected_source.items()):
        raise ValueError("Release archive source/license inventory differs from exact source")
    if platform.startswith("windows/"):
        pinned_runtime = json.loads(expected_source["provenance/scripts/windows-runtime.json"])
        if package.get("windowsRuntime") != pinned_runtime:
            raise ValueError("Windows runtime provenance differs from the exact source pin")
        packager = runpy.run_path(str(ROOT / "scripts/package-dev.py"))
        packager["verify_windows_notices"](pinned_runtime, members)
    elif "windowsRuntime" in package:
        raise ValueError("Non-Windows package must not claim a Windows runtime build")
    if any(name.startswith("extension/native/") for name in members):
        raise ValueError("Native server must not be bundled inside the Zed extension")
    executable = "server/argo-workflows-ls" + (".exe" if platform.startswith("windows/") else "")
    if members.get(executable) != binary:
        raise ValueError("Raw release binary differs from the legal archive")
    if package.get("binarySha256") != digest(binary) or package.get("binarySize") != len(binary):
        raise ValueError("Package binary identity differs from release bytes")
    if package.get("sourceArchiveSha256") != source_hash:
        raise ValueError("Release package source archive checksum mismatch")
    expected_identity = {"status": "verified-vcs", "archiveCommit": commit,
                         "nativeCommit": commit, "nativeDirty": False}
    if package.get("sourceIdentity") != expected_identity:
        raise ValueError("Native package provenance is not the exact clean source commit")
    identity = package.get("identity", {})
    identity_matches(identity, version, commit)
    if json.loads(members["provenance/native-version.stdout"]) != identity:
        raise ValueError("Archived native identity differs from release provenance")
    if identity["catalogHash"] != digest(members["provenance/compat/catalog.lock.json"]):
        raise ValueError("Native catalog hash differs from the exact source catalog")
    settings = {}
    for line in members["provenance/native-build.txt"].decode().splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[1] == "build" and "=" in parts[2]:
            key, value = parts[2].split("=", 1)
            settings[key] = value
    os_name, arch = platform.split("/")
    expected_settings = {"GOOS": os_name, "GOARCH": arch, "vcs": "git",
                         "vcs.revision": commit, "vcs.modified": "false"}
    if any(settings.get(key) != value for key, value in expected_settings.items()):
        raise ValueError("Archived Go platform or VCS metadata differs from release")


def stage_assets(build_path, output, version, commit):
    version_matches(version)
    clean_source(commit)
    build = json.loads(content(build_path.absolute(), 4 * 1024 * 1024))
    identity_matches(build.get("identity"), version, commit)
    if build.get("sourceCommit") != commit or build.get("sourceStatus") != "":
        raise ValueError("Build report does not describe the exact clean source commit")
    platform = build.get("platform")
    binary_name = asset_name(version, platform)
    binary_path = Path(build["binary"])
    if not binary_path.is_absolute():
        raise ValueError("Native build report must contain an absolute binary path")
    binary = content(binary_path)
    if digest(binary) != build.get("binarySha256"):
        raise ValueError("Native binary checksum differs from the build report")
    with tempfile.TemporaryDirectory(prefix="workflows-release-package-") as temporary:
        folder = Path(temporary)
        source = folder / "source.tar"
        source_hash, expected_source = source_inventory(commit, source)
        if platform.startswith("windows/"):
            pinned_runtime = json.loads(expected_source["provenance/scripts/windows-runtime.json"])
            if build.get("windowsRuntime") != pinned_runtime:
                raise ValueError("Native build report lacks the exact pinned Windows runtime inputs")
        # The existing packager checks the actual native product, Go platform,
        # clean VCS revision, catalog provenance and all included notices.
        process = subprocess.run(
            [sys.executable, str(ROOT / "scripts/package-dev.py"),
             "--binary", str(binary_path), "--sha256", digest(binary), "--platform", platform,
             "--source-archive", str(source), "--source-sha256", source_hash,
             "--output", str(folder / "package")],
            cwd=ROOT, capture_output=True, text=True, timeout=240)
        if process.returncode:
            raise ValueError("Existing native package verification failed: " + process.stderr.strip())
        packaged = json.loads(process.stdout)
        archive = content(Path(packaged["zip"]))
        manifest_bytes = content(Path(packaged["folder"]) / "native/manifest.json", 4 * 1024 * 1024)
        package = json.loads(manifest_bytes)
        if (digest(archive) != packaged.get("zipSha256")
                or digest(binary) != packaged.get("binarySha256")
                or content(Path(packaged["binary"])) != binary
                or package["identity"] != build["identity"]):
            raise ValueError("Release inputs changed or identity differs during staging")
        runtime = runtime_dependencies(binary_path, platform)
        if content(binary_path) != binary:
            raise ValueError("Native binary changed during runtime dependency inspection")
        archive_name = f"argo-workflows-ls-{version}-{platform.replace('/', '-')}.zip"
        record_name = f"release-{platform.replace('/', '-')}.json"
        record = {"schemaVersion": 1, "product": "argo-workflows", "platform": platform,
                  "version": version, "sourceCommit": commit, "binaryAsset": binary_name,
                  "archiveAsset": archive_name, "archiveRoot": Path(packaged["folder"]).name,
                  "binarySha256": digest(binary), "archiveSha256": digest(archive),
                  "manifestSha256": digest(manifest_bytes), "package": package,
                  "runtimeDependencies": runtime}
        files = {binary_name: binary, archive_name: archive, record_name: canonical(record)}
        validate_record(record, files, version, commit, source_hash, expected_source)
        clean_source(commit)
        publish_directory(output, files)
    return {"platform": platform, "version": version, "files": sorted(files),
            "runtimeDependencies": runtime}


def collect_assets(inputs, output, version, commit):
    version_matches(version)
    clean_source(commit)
    records = sorted(inputs.glob("*/release-*.json"))
    platforms, files = set(), {}
    with tempfile.TemporaryDirectory(prefix="workflows-release-source-") as temporary:
        source_hash, expected_source = source_inventory(commit, Path(temporary) / "source.tar")
        for path in records:
            record = json.loads(content(path, 4 * 1024 * 1024))
            platform = record.get("platform")
            if platform in platforms:
                raise ValueError("Duplicate release platform")
            names = {record.get("binaryAsset"), record.get("archiveAsset"), path.name}
            if any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", name) for name in names):
                raise ValueError("Unsafe release asset filename")
            if {item.name for item in path.parent.iterdir()} != names:
                raise ValueError("Unexpected or missing platform release files")
            current = {name: content(path.parent / name) for name in names}
            validate_record(record, current, version, commit, source_hash, expected_source)
            if set(files) & set(current):
                raise ValueError("Duplicate release asset filename")
            platforms.add(platform)
            files.update(current)
    if platforms != PLATFORMS or len(records) != len(PLATFORMS):
        raise ValueError("Release requires all five native platforms")
    files["SHA256SUMS"] = "".join(
        f"{digest(value)}  {name}\n" for name, value in sorted(files.items())).encode()
    clean_source(commit)
    publish_directory(output, files)
    return {"version": version, "sourceCommit": commit, "platforms": sorted(platforms), "files": sorted(files)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    tag = sub.add_parser("version")
    tag.add_argument("--tag", required=True)
    tag.add_argument("--source-commit", required=True)
    for name in ("stage", "collect"):
        command = sub.add_parser(name)
        command.add_argument("--version", required=True)
        command.add_argument("--source-commit", required=True)
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--build" if name == "stage" else "--inputs", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "version":
            if not args.tag.startswith("v"):
                raise ValueError("Release tag must start with v")
            version_matches(args.tag[1:])
            clean_source(args.source_commit)
            print("version=" + args.tag[1:])
        elif args.command == "stage":
            print(json.dumps(stage_assets(args.build, args.output, args.version, args.source_commit)))
        else:
            print(json.dumps(collect_assets(args.inputs, args.output, args.version, args.source_commit)))
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError, tarfile.TarError, zipfile.BadZipFile) as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

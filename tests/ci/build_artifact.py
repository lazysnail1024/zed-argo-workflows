#!/usr/bin/env python3
"""Transfer one exact native producer to its two consumer runners, fail closed.

Unlike diagnostic evidence, this archive allows only regular files/directories.
The original build manifest is never rewritten. A separate manifest records the
verified local binary path for the existing release staging command.
"""

import argparse
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile

import evidence
import run as ci


ROOT = Path(__file__).resolve().parents[2]
BUILD_ROOT = Path("artifacts/ci/build")


def source_identity(expected):
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    status = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True)
    ci.require(actual == expected and not status, "consumer/producer checkout must be the exact clean source")


def expected_identity(identity, source_commit, version):
    required = {"product": "argo-workflows", "analysisReady": True, "sourceCommit": source_commit,
                "serverVersion": version, "dirty": False, "buildMode": "release", "defaultTargetVersion": "auto"}
    ci.require(isinstance(identity, dict) and all(identity.get(key) == value for key, value in required.items()),
               "native identity differs from exact release source/version")


def binary_identity(binary):
    return json.loads(subprocess.check_output([str(binary), "version", "--json"], cwd=ROOT,
                                              text=True, timeout=30))


def validate_producer(producer, source_commit, version, platform):
    ci.require(producer.get("schemaVersion") == 1 and producer.get("sourceCommit") == source_commit and
               producer.get("releaseVersion") == version and producer.get("platform") == platform,
               "producer source, version or platform mismatch")
    ci.require(platform == ci.host_platform(), "consumer must execute on the producer's native platform")
    ci.require(producer.get("runnerSha256") == ci.sha(ROOT / "tests/ci/run.py") and
               producer.get("transferSha256") == ci.sha(Path(__file__)), "producer runner source mismatch")
    expected_identity(producer.get("identity"), source_commit, version)


def pack(build_file, output, source_commit, version, platform):
    source_identity(source_commit)
    build_file = build_file.resolve()
    ci.require(build_file == ROOT / BUILD_ROOT / "build.json", "unexpected native build location")
    build = json.loads(build_file.read_bytes())
    binary = Path(build["binary"])
    ci.require(build.get("platform") == platform == ci.host_platform() and
               build.get("sourceCommit") == source_commit and build.get("sourceStatus") == "",
               "build source or native platform mismatch")
    ci.require(binary.parent == build_file.parent and binary.is_file() and not binary.is_symlink(),
               "producer binary must be a regular file in the build directory")
    ci.require(ci.sha(binary) == build.get("binarySha256"), "producer binary checksum mismatch")
    actual = binary_identity(binary)
    ci.require(actual == build.get("identity"), "producer runtime identity differs from build manifest")
    expected_identity(actual, source_commit, version)
    for path in evidence.paths(build_file.parent):
        ci.require(not path.is_symlink() and (path.is_dir() or path.is_file()), "nonregular producer input")
    receipt = evidence.pack([build_file.parent], output, ROOT)
    producer = {"schemaVersion": 1, "sourceCommit": source_commit, "releaseVersion": version,
                "platform": platform, "binarySha256": build["binarySha256"], "identity": actual,
                "buildManifestSha256": ci.sha(build_file), "archiveSha256": receipt["archiveSha256"],
                "receiptSha256": ci.sha(output / "evidence.json"),
                "runnerSha256": ci.sha(ROOT / "tests/ci/run.py"), "transferSha256": ci.sha(Path(__file__))}
    ci.save(output / "producer.json", producer)
    return producer


def extract_build(archive_path):
    destination = ROOT / BUILD_ROOT
    ci.require(not destination.exists() and not destination.is_symlink(), "consumer build destination already exists")
    ci.require(destination.parent.resolve() == destination.parent, "consumer build parent cannot be an alias")
    with tarfile.open(archive_path, "r|gz") as archive:
        for info in archive:
            if info.name == evidence.INVENTORY:
                continue
            name = PurePosixPath(info.name)
            ci.require("\\" not in info.name and ":" not in info.name and str(name) == info.name and
                       not name.is_absolute() and ".." not in name.parts and
                       name.is_relative_to(PurePosixPath(BUILD_ROOT.as_posix())), "unsafe producer archive member")
            target = ROOT.joinpath(*name.parts)
            ci.require(info.isdir() or info.isfile(), "producer archive contains a nonregular node")
            if info.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            # extractfile is used only for a verified regular member, never for links.
            with archive.extractfile(info) as source, target.open("xb") as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
            target.chmod(info.mode & 0o777)


def restore(inputs, output, source_commit, version, platform):
    source_identity(source_commit)
    producer_file, receipt_file = inputs / "producer.json", inputs / "evidence.json"
    ci.require(producer_file.is_file() and not producer_file.is_symlink() and
               receipt_file.is_file() and not receipt_file.is_symlink(), "missing regular producer metadata")
    producer = json.loads(producer_file.read_bytes())
    validate_producer(producer, source_commit, version, platform)
    ci.require(ci.sha(receipt_file) == producer.get("receiptSha256"), "producer receipt checksum mismatch")
    receipt = json.loads(receipt_file.read_bytes())
    ci.require(receipt.get("roots") == [BUILD_ROOT.as_posix()] and
               receipt.get("archiveSha256") == producer.get("archiveSha256"), "producer archive receipt mismatch")
    evidence.verify(receipt_file)
    extract_build(inputs / receipt["archive"])
    original_file = ROOT / BUILD_ROOT / "build.json"
    ci.require(ci.sha(original_file) == producer.get("buildManifestSha256"), "producer build manifest checksum mismatch")
    build = json.loads(original_file.read_bytes())
    binary = ROOT / BUILD_ROOT / ("argo-workflows-ls.exe" if platform.startswith("windows/") else "argo-workflows-ls")
    ci.require(binary.is_file() and not binary.is_symlink() and
               ci.sha(binary) == build.get("binarySha256") == producer.get("binarySha256"), "imported binary checksum mismatch")
    ci.require(build.get("platform") == platform and build.get("sourceCommit") == source_commit and
               build.get("sourceStatus") == "", "imported build provenance mismatch")
    actual = binary_identity(binary)
    ci.require(actual == build.get("identity") == producer.get("identity"), "imported native runtime identity mismatch")
    expected_identity(actual, source_commit, version)
    ci.require(not output.exists(), "relocated manifest output already exists")
    relocated = build | {"binary": str(binary), "originalBinaryPath": build["binary"],
                         "originalBuildManifestSha256": producer["buildManifestSha256"],
                         "producerRecordSha256": ci.sha(producer_file)}
    ci.save(output / "build.json", relocated)
    return {"verified": True, "sourceCommit": source_commit, "platform": platform,
            "binarySha256": producer["binarySha256"], "manifest": str(output / "build.json")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    producing = actions.add_parser("pack")
    producing.add_argument("--build", type=Path, required=True)
    importing = actions.add_parser("restore")
    importing.add_argument("--inputs", type=Path, required=True)
    for action in (producing, importing):
        action.add_argument("--output", type=Path, required=True)
        action.add_argument("--source-commit", required=True)
        action.add_argument("--version", required=True)
        action.add_argument("--platform", required=True)
    args = parser.parse_args()
    try:
        common = (args.output.resolve(), args.source_commit, args.version, args.platform)
        result = pack(args.build, *common) if args.action == "pack" else restore(args.inputs.resolve(), *common)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, tarfile.TarError) as error:
        print("Native transfer verification failed: " + str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

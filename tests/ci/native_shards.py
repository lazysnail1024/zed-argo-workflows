#!/usr/bin/env python3
"""Bind two native lanes to one build and require the complete five-host matrix.

These small receipts supplement, not replace, successful Actions jobs and the
retained raw evidence. They detect missing work and mixed artifacts; the trust
root remains the exact checkout and artifacts from the same workflow run.
"""

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "tests/ci"
PLATFORMS = ("linux/amd64", "linux/arm64", "darwin/amd64", "darwin/arm64", "windows/amd64")
LANES = 2
RELEASE_SHARDS = 4
MAX_RECEIPT_BYTES = 8 * 1024 * 1024

# The observed macOS Intel suite costs were 337.70s / 518.20s in run
# 43180797369. Lane zero also performs package/release checks (181s there).
# This is scheduling, not a new deadline or a promised runtime improvement.
LANE_ZERO = (
    "wf3-lsp", "wf-remediation-lifecycle", "wf-remediation-targets",
    "wf-schema-queries", "wf-domain-queries", "wf-quickfixes",
    "wf-bounded-scalars", "wf-toolkit-budget", "wf-remediation-helm-boundary",
    "wf-reaudit-rename", "wf-reaudit-actions", "wf-reaudit-queries",
    "wf-mixed-parent-index", "wf-persistent-slowio", "wf-shared-yaml-projection",
    "wf-responsive-editing", "wf-resource-rename",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def receipt_bytes(path):
    path = Path(path)
    require(not path.is_symlink() and path.is_file(), f"receipt is not a regular file: {path}")
    require(path.stat().st_size <= MAX_RECEIPT_BYTES, f"receipt exceeds its limit: {path}")
    raw = path.read_bytes()
    require(len(raw) <= MAX_RECEIPT_BYTES, f"receipt exceeds its limit: {path}")
    return raw


def read_json(path):
    raw = receipt_bytes(path)
    return json.loads(raw), digest(raw)


def ci_module():
    # Lazy loading permits run.py to call write_report without an import cycle.
    spec = importlib.util.spec_from_file_location("native_ci_inventory", HERE / "run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def selected_tests(lane, native=None):
    require(type(lane) is int and 0 <= lane < LANES, "native lane must be 0 or 1")
    native = tuple(ci_module().NATIVE if native is None else native)
    require(len(native) == len(set(native)) and set(LANE_ZERO) <= set(native),
            "native suite inventory or fixed lane selection is invalid")
    selected = tuple(name for name in native if (name in LANE_ZERO) == (lane == 0))
    require(bool(selected), "native lanes must be nonempty")
    return selected


def code_fingerprints():
    return {"runnerSha256": sha(HERE / "run.py"),
            "shardsSha256": sha(HERE / "native_shards.py"),
            "transferSha256": sha(HERE / "build_artifact.py"),
            "catalogSha256": sha(ROOT / "compat/catalog.lock.json")}


def release_inventory():
    lock = json.loads((ROOT / "compat/catalog.lock.json").read_text(encoding="utf-8"))
    versions = [row["version"] for row in lock["versions"]]
    pairs = {row["version"]: len(row["sources"]) for row in lock["versions"]}
    require(len(versions) == len(set(versions)) == 146 and sum(pairs.values()) == 1052,
            "locked release inventory changed; review native coverage")
    return versions, pairs


def checkout_identity():
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    status = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT)
    require(not status, "native coverage requires a clean committed checkout")
    return head, (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def binary_identity(binary):
    result = subprocess.run([str(binary), "version", "--json"], cwd=ROOT,
                            capture_output=True, text=True, timeout=20, check=True)
    return json.loads(result.stdout)


def valid_hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def validate_identity(identity, source_commit, version, fingerprints):
    require(isinstance(identity, dict), "missing native identity")
    expected = {"product": "argo-workflows", "serverVersion": version,
                "sourceCommit": source_commit, "buildMode": "release",
                "defaultTargetVersion": "auto", "catalogHash": fingerprints["catalogSha256"]}
    require(all(identity.get(key) == value for key, value in expected.items()),
            "native identity source, version or catalog mismatch")
    require(identity.get("analysisReady") is True and identity.get("dirty") is False,
            "native identity is not a clean analysis-ready release")
    versions, _ = release_inventory()
    require(identity.get("acceptedTargetVersions") == versions,
            "native identity does not contain the complete supported releases")


def validate_producer(producer, source_commit, version, fingerprints):
    require(isinstance(producer, dict) and type(producer.get("schemaVersion")) is int
            and producer["schemaVersion"] == 1, "invalid producer schema")
    require(producer.get("sourceCommit") == source_commit and producer.get("releaseVersion") == version,
            "producer source or version mismatch")
    require(producer.get("platform") in PLATFORMS, "unknown producer platform")
    for key in ("binarySha256", "buildManifestSha256", "archiveSha256", "receiptSha256"):
        require(valid_hash(producer.get(key)), f"invalid producer {key}")
    for key in ("runnerSha256", "transferSha256"):
        require(producer.get(key) == fingerprints[key], f"producer {key} mismatch")
    validate_identity(producer.get("identity"), source_commit, version, fingerprints)


def validate_command(row, script, binary):
    require(isinstance(row, dict) and row.get("started") is True and row.get("timedOut") is False
            and type(row.get("exitCode")) is int and row["exitCode"] == 0 and row.get("pass") is True,
            f"failed or incomplete command: {script}")
    argv = row.get("command")
    require(isinstance(argv, list) and len(argv) >= 4 and all(isinstance(value, str) for value in argv),
            f"missing command identity: {script}")
    require(argv[1].replace("\\", "/").rsplit("/", 1)[-1] == script + ".py"
            and argv[2] == binary and "--prepare" not in argv, f"wrong executed consumer: {script}")
    require(type(row.get("timeoutSeconds")) is int and row["timeoutSeconds"] == 300,
            f"consumer deadline differs: {script}")
    seconds = row.get("seconds")
    require(type(seconds) in (int, float) and math.isfinite(seconds) and seconds >= 0,
            f"missing command timing: {script}")


def multi_version_receipt(output):
    versions, pairs = release_inventory()
    manifests = []
    for index in range(RELEASE_SHARDS):
        path = Path(output) / "wf-multi-version" / f"shard-{index}" / "manifest.json"
        # Preserve CRLF bytes from Windows; text-mode universal newline
        # conversion would no longer fingerprint the retained original file.
        raw = receipt_bytes(path)
        manifests.append({"index": index, "manifestSha256": digest(raw), "manifestJson": raw.decode("utf-8")})
    return {"versions": versions, "releaseCount": len(versions),
            "resourceVersionPairs": sum(pairs.values()), "shards": manifests}


def validate_multi_version(row, proof, report, fingerprints):
    versions, pairs = release_inventory()
    require(isinstance(proof, dict) and proof.get("versions") == versions
            and type(proof.get("releaseCount")) is int and proof["releaseCount"] == 146
            and type(proof.get("resourceVersionPairs")) is int and proof["resourceVersionPairs"] == 1052,
            "missing, duplicated or mismatched exact release coverage")
    require(row.get("releaseCount") == 146 and row.get("resourceVersionPairs") == 1052
            and type(row.get("caseCount")) is int and row["caseCount"] >= 86,
            "multi-version verifier did not confirm the complete matrix and semantic controls")
    commands, manifests = row.get("shards"), proof.get("shards")
    require(isinstance(commands, list) and isinstance(manifests, list)
            and len(commands) == len(manifests) == RELEASE_SHARDS, "missing multi-version subshard")
    for index, (command, receipt) in enumerate(zip(commands, manifests)):
        validate_command(command, "wf-multi-version", report["executedBinary"])
        require(command["command"][4:] == ["--release-shard", f"{index}/{RELEASE_SHARDS}"],
                "wrong multi-version subshard command")
        require(isinstance(receipt, dict) and type(receipt.get("index")) is int and receipt["index"] == index,
                "duplicated or reordered multi-version subshard")
        raw = receipt.get("manifestJson")
        require(isinstance(raw, str) and digest(raw.encode("utf-8")) == receipt.get("manifestSha256"),
                "multi-version manifest hash mismatch")
        manifest = json.loads(raw)
        expected = versions[index::RELEASE_SHARDS]
        fields = {"releaseShard": f"{index}/{RELEASE_SHARDS}", "selectedVersions": expected,
                  "totalReleaseCount": 146, "releaseCount": len(expected),
                  "resourceVersionPairs": sum(pairs[version] for version in expected),
                  "binarySha256": report["binarySha256"], "catalogSha256": fingerprints["catalogSha256"],
                  "testSha256": sha(ROOT / "tests/e2e/wf-multi-version.py"),
                  "celRulesExercised": 10 if index == RELEASE_SHARDS - 1 else 0}
        require(isinstance(manifest, dict) and all(manifest.get(key) == value for key, value in fields.items()),
                "multi-version manifest has wrong coverage or source")


def validate_report(report, producer, producer_sha, source_commit, version, fingerprints):
    require(isinstance(report, dict) and type(report.get("schemaVersion")) is int
            and report["schemaVersion"] == 1, "invalid native lane schema")
    require(report.get("sourceCommit") == source_commit and report.get("releaseVersion") == version,
            "native lane source or version mismatch")
    require(report.get("platform") == producer["platform"], "native lane platform mismatch")
    lane = report.get("lane")
    require(type(lane) is int and lane in (0, 1) and type(report.get("laneCount")) is int
            and report["laneCount"] == LANES, "unknown native lane")
    require(report.get("producerSha256") == producer_sha, "native lane producer hash mismatch")
    for key in ("binarySha256", "buildManifestSha256", "archiveSha256", "receiptSha256", "identity"):
        require(report.get(key) == producer[key], f"native lane producer {key} mismatch")
    require(all(report.get(key) == value for key, value in fingerprints.items()),
            "native lane code or catalog hash mismatch")
    require(report.get("completed") is True and report.get("pass") is True and not report.get("evidenceError"),
            "failed or incomplete native lane")
    require(isinstance(report.get("executedBinary"), str) and bool(report["executedBinary"]),
            "missing executed binary path")
    names = selected_tests(lane)
    rows = report.get("tests")
    require(isinstance(rows, list) and all(isinstance(row, dict) for row in rows)
            and [row.get("name") for row in rows] == list(names), "missing, duplicated or unexpected native suite")
    expected_scripts = {name: sha(ROOT / "tests/e2e" / (name + ".py")) for name in names}
    require(report.get("consumerSha256") == expected_scripts, "native consumer hash mismatch")
    for row in rows:
        name = row["name"]
        require(row.get("pass") is True and not row.get("evidenceError"), f"failed native consumer: {name}")
        if name == "wf-multi-version":
            validate_multi_version(row, report.get("multiVersion"), report, fingerprints)
            continue
        validate_command(row, name, report["executedBinary"])
        require(type(row.get("caseCount")) is int and row["caseCount"] > 0 and valid_hash(row.get("resultsSha256")),
                f"missing semantic results: {name}")
    require(("wf-multi-version" in names) == (report.get("multiVersion") is not None),
            "unexpected multi-version receipt on the other lane")


def write_report(output, *, lane, producer, rows, binary, completed=True):
    """Write native-lane.json after the existing local semantic verifiers ran."""
    output, binary = Path(output), Path(binary).resolve()
    record, producer_sha = ((producer, digest(canonical(producer))) if isinstance(producer, dict)
                            else read_json(producer))
    source_commit, version = checkout_identity()
    fingerprints = code_fingerprints()
    validate_producer(record, source_commit, version, fingerprints)
    require(record["platform"] == ci_module().host_platform(), "producer differs from the actual native host")
    require(sha(binary) == record["binarySha256"], "executed binary differs from producer checksum")
    identity = binary_identity(binary)
    require(identity == record["identity"], "executed native identity differs from producer")
    names = selected_tests(lane)
    report = {"schemaVersion": 1, "sourceCommit": source_commit, "releaseVersion": version,
              "platform": record["platform"], "lane": lane, "laneCount": LANES,
              "producerSha256": producer_sha, "executedBinary": str(binary),
              "completed": completed is True, "pass": completed is True,
              "tests": rows, **fingerprints,
              "consumerSha256": {name: sha(ROOT / "tests/e2e" / (name + ".py")) for name in names}}
    for key in ("binarySha256", "buildManifestSha256", "archiveSha256", "receiptSha256", "identity"):
        report[key] = record[key]
    try:
        if "wf-multi-version" in names:
            report["multiVersion"] = multi_version_receipt(output)
        validate_report(report, record, producer_sha, source_commit, version, fingerprints)
    except (OSError, ValueError, TypeError, KeyError) as error:
        report.update({"pass": False, "evidenceError": str(error)})
    output.mkdir(parents=True, exist_ok=True)
    (output / "native-lane.json").write_bytes(canonical(report))
    return report


def verify_inputs(inputs, *, source_commit, version):
    """Verify only small receipts; never unpack or relocate raw CLI evidence."""
    require(isinstance(source_commit, str) and re.fullmatch(r"[0-9a-f]{40}", source_commit),
            "expected an exact source commit")
    require(checkout_identity() == (source_commit, version), "coverage gate checkout or version mismatch")
    inputs = Path(inputs)
    require(inputs.is_dir() and not inputs.is_symlink(), "coverage inputs must be a real directory")
    fingerprints = code_fingerprints()
    producers, reports = {}, []
    for directory, _, files in os.walk(inputs, followlinks=False):
        for name in ("producer.json", "native-lane.json"):
            if name not in files:
                continue
            record, record_sha = read_json(Path(directory) / name)
            if name == "native-lane.json":
                reports.append(record)
                require(len(reports) <= len(PLATFORMS) * LANES, "duplicate or unexpected native lane receipt")
                continue
            validate_producer(record, source_commit, version, fingerprints)
            platform = record["platform"]
            require(platform not in producers, "duplicate native producer")
            producers[platform] = (record, record_sha)
    require(set(producers) == set(PLATFORMS), "missing native producer platform")
    seen = set()
    for report in reports:
        require(isinstance(report, dict) and report.get("platform") in producers, "unknown native lane platform")
        producer, producer_sha = producers[report["platform"]]
        validate_report(report, producer, producer_sha, source_commit, version, fingerprints)
        key = (report["platform"], report["lane"])
        require(key not in seen, "duplicate native lane")
        seen.add(key)
    require(seen == {(platform, lane) for platform in PLATFORMS for lane in range(LANES)},
            "missing native lane or platform")
    return {"schemaVersion": 1, "sourceCommit": source_commit, "releaseVersion": version,
            "pass": True, "platforms": list(PLATFORMS), "laneCount": len(seen),
            "nativeSuitesPerPlatform": len(ci_module().NATIVE),
            "exactReleasesPerPlatform": 146, "resourceVersionPairsPerPlatform": 1052}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    verify = actions.add_parser("verify")
    verify.add_argument("--inputs", type=Path, required=True)
    verify.add_argument("--source-commit", required=True)
    verify.add_argument("--version", required=True)
    verify.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        result = verify_inputs(args.inputs, source_commit=args.source_commit, version=args.version)
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        result = {"pass": False, "error": str(error)}
    (args.output / "summary.json").write_bytes(canonical(result))
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["pass"] is True else 1


if __name__ == "__main__":
    sys.exit(main())

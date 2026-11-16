#!/usr/bin/env python3
"""Real receipt transport files, independent literal bytes and refusal controls.

This consumer validates a transport, not a successful native lane. Native domain
replay uses original completed executions with their pinned production validator.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "tests/ci/scenarios/receipt-transport.json"
SPEC = json.loads(SCENARIO.read_text())
CODEC = ROOT / "tests/ci/transport.py"
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--producer-only", action="store_true", help="Exercise portable pack inputs without POSIX-only hydration")
args = parser.parse_args()
out = args.output.resolve()
out.mkdir(parents=True, exist_ok=False)
rows = []


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def save():
    (out / "results.json").write_text(json.dumps(rows, indent=2) + "\n")


def row(name, passed, **details):
    rows.append({"case": name, "pass": bool(passed), **details})
    save()
    print(name, "PASS" if passed else "FAIL", flush=True)


def run(name, command, expected=None, expected_message=None):
    dest = out / "commands" / name
    dest.mkdir(parents=True, exist_ok=False)
    full = [sys.executable, str(CODEC), *map(str, command)]
    result = subprocess.run(full, cwd=ROOT, capture_output=True, timeout=30)
    (dest / "stdout.raw").write_bytes(result.stdout)
    (dest / "stderr.raw").write_bytes(result.stderr)
    (dest / "command.json").write_text(json.dumps({"command": full, "exitCode": result.returncode}, indent=2) + "\n")
    if expected is not None:
        message = None
        try:
            error = json.loads(result.stderr)["error"]
            code = error["code"]
            if expected_message is not None:
                message = error.get("message")
        except (ValueError, KeyError, TypeError):
            code = None
        passed = result.returncode == 1 and code == expected
        details = {"expectedCode": expected, "observedCode": code, "exitCode": result.returncode}
        if expected_message is not None:
            passed = passed and message == expected_message
            details.update(expectedMessage=expected_message, observedMessage=message)
        row(name, passed, **details)
    return result


def fixture(name="source", lane="native-linux-amd64", physical=False, extra_files=None):
    source = out / name
    payload = source / ("payload-" + lane)
    payload.mkdir(parents=True)
    files = {key: value.encode() for key, value in SPEC["literalFiles"].items()}
    duplicate = bytes(range(256)) * 1024
    files.update({"copies/executable": duplicate, "copies/plain": duplicate})
    files.update(extra_files or {})
    # Real executable files exercise cross-API path.stat/fstat signatures. On
    # Windows these suffixes can synthesize execute bits only in path.stat.
    if physical:
        executable = Path(sys.executable).read_bytes()
        for suffix in (".exe", ".bat", ".cmd", ".com"):
            files["physical/python" + suffix] = executable
    for name, value in files.items():
        path = payload / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        path.chmod(0o755 if name == "copies/executable" else 0o640)
    evidence = {name: sha_bytes(value) for name, value in files.items()}
    receipt = source / (lane + ".json")
    # Deliberately preserve CRLF, spaces and ordering. This is test-owned
    # transport material; it is never presented as an executed product receipt.
    raw = ('{\r\n "evidence" : ' + json.dumps(evidence, indent=3) + ',\r\n "lane" : ' + json.dumps(lane) +
           ', "schemaVersion" : 1, "identity" : {"runID":"fixed-local-transport","attempt":"7"}, "pass" : false\r\n}\r\n').encode()
    receipt.write_bytes(raw)
    receipt.chmod(0o644)
    originals = {receipt.name: raw, **{("payload-" + lane + "/" + name): value for name, value in files.items()}}
    modes = {name: stat.S_IMODE((source / name).stat().st_mode) for name in originals}
    return source, receipt, originals, modes


def zip_members(path):
    with zipfile.ZipFile(path) as archive:
        return {info.filename: archive.read(info) for info in archive.infolist()}


def rewrite(path, members, special=None, compression=zipfile.ZIP_DEFLATED):
    with zipfile.ZipFile(path, "w", compression=compression, compresslevel=6 if compression == zipfile.ZIP_DEFLATED else None) as archive:
        for name, value in members.items():
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.compress_type = compression
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            if special and name == special[0]:
                info.external_attr = special[1] << 16
            archive.writestr(info, value)


def verify_receipt_binding_input(archive, original_manifest, original_members, originals):
    """Prove the new input disagrees only with the unchanged original receipt."""
    target = "payload-native-linux-amd64/native/child/stdout.raw"
    original = SPEC["literalFiles"]["native/child/stdout.raw"].encode()
    replacement = b"X" * len(original)
    with zipfile.ZipFile(archive) as stream:
        member_count = len(stream.infolist())
        crc_valid = stream.testzip() is None
        actual = {info.filename: stream.read(info) for info in stream.infolist()}
    manifest = json.loads(actual["transport.json"])
    before = {entry["path"]: entry for entry in original_manifest["entries"]}
    entries = {entry["path"]: entry for entry in manifest["entries"]}
    files = {path: entry for path, entry in entries.items() if entry["type"] == "file"}
    objects = {entry["sha256"]: entry["size"] for entry in files.values()}
    expected_manifest = copy.deepcopy(original_manifest)
    next(entry for entry in expected_manifest["entries"] if entry["path"] == target)["sha256"] = sha_bytes(replacement)
    receipt_name = original_manifest["receipt"]
    receipt_raw = actual["objects/" + entries[receipt_name]["sha256"]]
    checks = {
        "zipCRCValid": crc_valid,
        "exactObjectInventory": member_count == len(actual) and set(actual) == {"transport.json", *("objects/" + digest for digest in objects)},
        "allObjectHashesAndSizesMatch": all(len(actual["objects/" + digest]) == size and
            sha_bytes(actual["objects/" + digest]) == digest for digest, size in objects.items()),
        "exactOriginalLiteral": originals[target] == original and original_members["objects/" + before[target]["sha256"]] == original,
        "originalTargetObjectIsUnique": sum(entry.get("sha256") == before[target]["sha256"] for entry in before.values()) == 1,
        "onlyTargetDigestChanged": manifest == expected_manifest,
        "sameSizeDifferentLiteral": len(replacement) == len(original) and replacement != original and
            actual["objects/" + entries[target]["sha256"]] == replacement,
        "exactOriginalReceipt": receipt_raw == originals[receipt_name],
        "originalReceiptStillFailedAndCRLF": b"\r\n" in receipt_raw and json.loads(receipt_raw)["pass"] is False,
        "originalEvidenceDigestRetained": json.loads(receipt_raw)["evidence"]["native/child/stdout.raw"] == sha_bytes(original),
        "otherLogicalBytesUnchanged": all(actual["objects/" + entry["sha256"]] == originals[path]
            for path, entry in files.items() if path != target),
    }
    witness = {"case": "receipt-evidence-mismatch", "target": target, "checks": checks,
        "archiveSha256": sha_bytes(archive.read_bytes()), "originalReceiptSha256": sha_bytes(receipt_raw),
        "originalEvidenceSha256": sha_bytes(original), "replacementEvidenceSha256": sha_bytes(replacement)}
    archive.with_suffix(".input.json").write_text(json.dumps(witness, indent=2) + "\n")
    assert all(checks.values()), "receipt binding input must isolate the original evidence mismatch"


def declared_size(path, member, size, crc=None):
    # Independently edit fixed ZIP header fields, preserving the real compressed
    # stream. This is deliberately not the codec's manifest/header implementation.
    raw = bytearray(path.read_bytes())
    end = raw.rfind(b"PK\x05\x06")
    position = struct.unpack_from("<I", raw, end + 16)[0]
    while raw[position:position + 4] == b"PK\x01\x02":
        names, extra, comments = struct.unpack_from("<HHH", raw, position + 28)
        name = bytes(raw[position + 46:position + 46 + names]).decode()
        if name == member:
            local = struct.unpack_from("<I", raw, position + 42)[0]
            struct.pack_into("<I", raw, position + 24, size)
            struct.pack_into("<I", raw, local + 22, size)
            if crc is not None:
                struct.pack_into("<I", raw, position + 16, crc)
                struct.pack_into("<I", raw, local + 14, crc)
            path.write_bytes(raw)
            return
        position += 46 + names + extra + comments
    raise AssertionError("independent ZIP member missing")


def finish(expected):
    assert {item["case"] for item in rows} == expected and len(rows) == len(expected)
    (out / "manifest.json").write_text(json.dumps({"command": sys.argv, "testSha256": sha_bytes(Path(__file__).read_bytes()),
        "scenarioSha256": sha_bytes(SCENARIO.read_bytes()), "codecSha256": sha_bytes(CODEC.read_bytes()), "python": sys.version,
        "platform": sys.platform, "producerOnly": args.producer_only, "selectedCases": sorted(expected),
        "scope": "real transport boundary; synthetic literal material is not a native domain execution"}, indent=2) + "\n")
    raise SystemExit(0 if all(item["pass"] for item in rows) else 1)


def edited_producer_files():
    source, receipt, originals, modes = fixture("edited-source", physical=True,
        extra_files={"edited/config.json": b'{"changed":true}\n', "edited/empty.log": b''})
    observations = {}
    for name, value in originals.items():
        path = source / name
        # Real producer logs/configuration are revised after their creation.
        # On Windows 3.14 this separates path birth-backed ctime from fd ctime.
        path.write_bytes(b"initial content before the final revision\n")
        path.write_bytes(value)
        before = path.lstat()
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
        observations[name] = {"path": {key: getattr(before, key, None) for key in
            ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_birthtime_ns", "st_mode")},
            "fd": {key: getattr(opened, key, None) for key in
            ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_birthtime_ns", "st_mode")}}
    archive = out / "edited-producer.zip"
    result = run("edited-producer-pack", ["pack", "--receipt", receipt, "--output", archive])
    exact = False
    if result.returncode == 0:
        members = zip_members(archive)
        manifest = json.loads(members["transport.json"])
        entries = {entry["path"]: entry for entry in manifest["entries"] if entry["type"] == "file"}
        exact = set(entries) == set(originals) and all(
            entries[name]["size"] == len(value) and entries[name]["sha256"] == sha_bytes(value) and
            entries[name]["mode"] == modes[name] and members["objects/" + sha_bytes(value)] == value
            for name, value in originals.items())
    alias_expected = sys.platform == "win32" and sys.version_info[:3] == (3, 14, 0)
    alias_observed = any(value["path"]["st_birthtime_ns"] is not None and
        value["path"]["st_birthtime_ns"] == value["fd"]["st_birthtime_ns"] and
        value["path"]["st_ctime_ns"] != value["fd"]["st_ctime_ns"] for value in observations.values())
    row("edited-producer-files", result.returncode == 0 and exact and (not alias_expected or alias_observed) and
        all((source / name).read_bytes() == value for name, value in originals.items()),
        exitCode=result.returncode, metadata=observations,
        windows314AliasExpected=alias_expected, windows314AliasObserved=alias_observed,
        inputSha256={name: sha_bytes(value) for name, value in originals.items()})


edited_producer_files()
if args.producer_only:
    finish({"edited-producer-files"})


source, receipt, originals, modes = fixture()
bundle = out / "valid.zip"
initial = run("initial-pack", ["pack", "--receipt", receipt, "--output", bundle])
if initial.returncode != 0:
    row("opaque-byte-roundtrip", False, prerequisite="pack must complete before any negative can count",
        exitCode=initial.returncode)
    raise SystemExit(1)
hydrated = out / "hydrated"
result = run("initial-hydrate", ["hydrate", "--archive", bundle, "--output", hydrated])
actual = {path.relative_to(hydrated).as_posix(): path.read_bytes() for path in hydrated.rglob("*") if path.is_file()} if hydrated.exists() else {}
row("opaque-byte-roundtrip", result.returncode == 0 and actual == originals and all(
    stat.S_IMODE((hydrated / name).stat().st_mode) == mode for name, mode in modes.items()),
    inputSha256={name: sha_bytes(value) for name, value in originals.items()},
    archiveSha256=sha_bytes(bundle.read_bytes()), files=len(actual))
left = hydrated / "payload-native-linux-amd64/copies/executable"
right = hydrated / "payload-native-linux-amd64/copies/plain"
before = right.read_bytes()
archive_before = sha_bytes(bundle.read_bytes())
left.write_bytes(b"one restored copy changed\n")
row("independent-duplicate-modes", not os.path.samefile(left, right) and
    stat.S_IMODE(left.stat().st_mode) == 0o755 and stat.S_IMODE(right.stat().st_mode) == 0o640 and
    right.read_bytes() == before and (source / "payload-native-linux-amd64/copies/executable").read_bytes() == before and
    sha_bytes(bundle.read_bytes()) == archive_before)
physical_source, physical_receipt, _, _ = fixture("physical-source", physical=True)
physical_bundle, physical_output = out / "physical.zip", out / "physical-hydrated"
physical_ok = run("physical-pack", ["pack", "--receipt", physical_receipt, "--output", physical_bundle]).returncode == 0 and run(
    "physical-hydrate", ["hydrate", "--archive", physical_bundle, "--output", physical_output]).returncode == 0
row("physical-executable-suffixes", physical_ok and all((physical_output / ("payload-native-linux-amd64/physical/python" + suffix)).read_bytes() ==
    Path(sys.executable).read_bytes() for suffix in (".exe", ".bat", ".cmd", ".com")))
# Deflate can consume its final compressed byte while retaining more decoded
# output than one bounded read permits. Exercise both sides of the production
# one-MiB boundary with independent literal bytes, including a second full
# output chunk and compressed data requiring multiple input reads.
boundary_files = {f"boundaries/repeated-{size}": b"a" * size
                  for size in (0, 1048575, 1048576, 1048577, 2097153)}
boundary_files["boundaries/nonrepeating"] = hashlib.shake_256(b"fixed transport input boundary").digest(2097153)
boundary_source, boundary_receipt, boundary_bytes, boundary_modes = fixture("boundary-source", extra_files=boundary_files)
boundary_bundle, boundary_output = out / "boundaries.zip", out / "boundary-hydrated"
boundary_pack = run("boundary-pack", ["pack", "--receipt", boundary_receipt, "--output", boundary_bundle])
boundary_restore = run("boundary-hydrate", ["hydrate", "--archive", boundary_bundle, "--output", boundary_output]) if boundary_pack.returncode == 0 else None
row("deflate-chunk-boundaries", boundary_restore is not None and boundary_restore.returncode == 0 and
    all((boundary_output / name).read_bytes() == value and
        stat.S_IMODE((boundary_output / name).stat().st_mode) == boundary_modes[name]
        for name, value in boundary_bytes.items()),
    sizes={name: len(value) for name, value in boundary_files.items()},
    inputSha256={name: sha_bytes(value) for name, value in boundary_files.items()})
detached = out / "source-detached"
source.rename(detached)
restored = out / "detached-restored"
result = run("detached-hydrate", ["hydrate", "--archive", bundle, "--output", restored])
row("detached-source", result.returncode == 0 and all((restored / name).read_bytes() == value for name, value in originals.items()))
detached.rename(source)

members = zip_members(bundle)
manifest = json.loads(members["transport.json"])
payload_file = next(entry for entry in manifest["entries"] if entry["type"] == "file" and entry["path"].startswith("payload-"))
object_name = "objects/" + payload_file["sha256"]
faults = out / "faults"
faults.mkdir()
for name, code in SPEC["refusalCases"].items():
    if name.startswith(("source-", "set-", "hydrate-", "pack-")) or name in ("extra-source-file", "report-inside-gate-tree"):
        continue
    changed = dict(members)
    value = copy.deepcopy(manifest)
    chosen = next(entry for entry in value["entries"] if entry["type"] == "file" and entry["path"].startswith("payload-"))
    special = None
    compression = zipfile.ZIP_DEFLATED
    expected_message = None
    if name == "duplicate-manifest-key":
        changed["transport.json"] = members["transport.json"].replace(b'{', b'{"schemaVersion":1,', 1)
    elif name == "duplicate-receipt-key":
        entry = next(entry for entry in value["entries"] if entry["path"] == value["receipt"])
        old = "objects/" + entry["sha256"]
        raw = changed.pop(old).replace(b'{', b'{"lane":"native-linux-amd64",', 1)
        entry["sha256"], entry["size"] = sha_bytes(raw), len(raw)
        changed["objects/" + entry["sha256"]] = raw
    elif name == "unknown-format": value["schemaVersion"] = 2
    elif name == "unknown-manifest-field": value["unknown"] = True
    elif name == "boolean-version": value["schemaVersion"] = True
    elif name == "boolean-size": chosen["size"] = True
    elif name == "negative-size": chosen["size"] = -1
    elif name == "oversized-file": chosen["size"] = SPEC["limits"]["fileBytes"] + 1
    elif name == "oversized-logical-total":
        for index in range(17):
            entry = copy.deepcopy(chosen)
            entry["path"], entry["size"] = f"payload-native-linux-amd64/large-{index}", SPEC["limits"]["fileBytes"]
            entry["sha256"] = sha_bytes(("independent large object " + str(index)).encode())
            value["entries"].append(entry)
    elif name == "invalid-mode": chosen["mode"] = True
    elif name == "duplicate-logical-path": value["entries"].append(copy.deepcopy(chosen))
    elif name == "case-alias":
        entry = copy.deepcopy(chosen);entry["path"] = entry["path"].upper();value["entries"].append(entry)
    elif name == "file-directory-collision": value["entries"].append({"path": chosen["path"], "type": "directory", "mode": 0o755})
    elif name in ("traversal-path", "absolute-path", "drive-path", "backslash-path", "empty-component", "dot-component", "trailing-dot", "reserved-device", "noncanonical-unicode"):
        chosen["path"] = {"traversal-path": "../outside", "absolute-path": "/outside", "drive-path": "C:/outside",
            "backslash-path": "payload-native-linux-amd64\\outside", "empty-component": "payload-native-linux-amd64//x",
            "dot-component": "payload-native-linux-amd64/./x", "trailing-dot": "payload-native-linux-amd64/x.",
            "reserved-device": "payload-native-linux-amd64/CON.txt", "noncanonical-unicode": "payload-native-linux-amd64/e\u0301"}[name]
    elif name == "path-byte-bound": chosen["path"] = "payload-native-linux-amd64/" + "x" * SPEC["limits"]["pathBytes"]
    elif name == "path-depth-bound": chosen["path"] = "payload-native-linux-amd64/" + "/".join(["x"] * SPEC["limits"]["pathDepth"])
    elif name == "decompression-overflow":
        old_digest = chosen["sha256"]
        empty_digest = sha_bytes(b"")
        changed["objects/" + empty_digest] = changed.pop("objects/" + old_digest)
        chosen["sha256"], chosen["size"] = empty_digest, 0
        receipt_entry = next(entry for entry in value["entries"] if entry["path"] == value["receipt"])
        original_receipt = json.loads(changed.pop("objects/" + receipt_entry["sha256"]))
        original_receipt["evidence"][chosen["path"].split("/", 1)[1]] = empty_digest
        new_receipt = json.dumps(original_receipt).encode()
        receipt_entry["sha256"], receipt_entry["size"] = sha_bytes(new_receipt), len(new_receipt)
        changed["objects/" + receipt_entry["sha256"]] = new_receipt
    elif name == "missing-object": changed.pop(object_name)
    elif name == "extra-object": changed["objects/" + sha_bytes(b"extra")] = b"extra"
    elif name == "wrong-object-hash": changed[object_name] = b"x" * len(changed[object_name])
    elif name == "receipt-evidence-mismatch":
        # Keep the receipt's exact CRLF/pass:false bytes and evidence digests.
        # Only the transport inventory and its real same-size object agree on
        # new bytes; object hashing alone cannot detect this disagreement.
        chosen = next(entry for entry in value["entries"] if entry["path"] == "payload-native-linux-amd64/native/child/stdout.raw")
        replacement = b"X" * len(SPEC["literalFiles"]["native/child/stdout.raw"].encode())
        changed.pop("objects/" + chosen["sha256"])
        chosen["sha256"] = sha_bytes(replacement)
        changed["objects/" + chosen["sha256"]] = replacement
        expected_message = "transport rewrites original evidence digests"
    elif name == "wrong-object-size": chosen["size"] += 1
    elif name == "symlink-zip-member": special = (object_name, stat.S_IFLNK | 0o777)
    elif name == "fifo-zip-member": special = (object_name, stat.S_IFIFO | 0o600)
    elif name == "unsupported-compression": compression = zipfile.ZIP_STORED
    elif name not in ("duplicate-zip-member", "truncated-archive", "manifest-byte-bound", "central-directory-bound"):
        raise AssertionError("unimplemented independent fault: " + name)
    if name != "duplicate-manifest-key": changed["transport.json"] = json.dumps(value).encode()
    archive = faults / (name + ".zip")
    rewrite(archive, changed, special, compression)
    if name == "receipt-evidence-mismatch": verify_receipt_binding_input(archive, manifest, members, originals)
    if name == "manifest-byte-bound": declared_size(archive, "transport.json", SPEC["limits"]["manifestBytes"] + 1)
    if name == "central-directory-bound":
        raw = bytearray(archive.read_bytes());end = raw.rfind(b"PK\x05\x06")
        struct.pack_into("<I", raw, end + 12, SPEC["limits"]["centralDirectoryBytes"] + 1);archive.write_bytes(raw)
    if name == "decompression-overflow": declared_size(archive, "objects/" + sha_bytes(b""), 0, 0)
    if name == "duplicate-zip-member":
        with zipfile.ZipFile(archive, "a", compression=zipfile.ZIP_DEFLATED) as extra: extra.writestr(object_name, members[object_name])
    if name == "truncated-archive": archive.write_bytes(archive.read_bytes()[:-15])
    run(name, ["hydrate", "--archive", archive, "--output", out / ("rejected-" + name)], code, expected_message)

for name in ("extra-source-file", "source-symlink", "source-directory-symlink", "source-dangling-symlink", "source-fifo"):
    current, current_receipt, _, _ = fixture("input-" + name)
    extra = current / "payload-native-linux-amd64/extra"
    if name == "extra-source-file": extra.write_bytes(b"not in original receipt")
    elif name == "source-fifo": os.mkfifo(extra)
    elif name == "source-directory-symlink": extra.symlink_to(source, target_is_directory=True)
    elif name == "source-dangling-symlink": extra.symlink_to(out / "missing")
    else: extra.symlink_to(receipt)
    run(name, ["pack", "--receipt", current_receipt, "--output", out / (name + ".zip")], SPEC["refusalCases"][name])

sentinel = out / "sentinel"
sentinel.mkdir()
(sentinel / "keep").write_bytes(b"outside bytes stay unchanged\n")
existing_archive = out / "existing.zip"
existing_archive.write_bytes(b"existing archive bytes\n")
run("pack-output-exists", ["pack", "--receipt", receipt, "--output", existing_archive], "DESTINATION_EXISTS")
run("pack-output-inside-source", ["pack", "--receipt", receipt, "--output", source / "new.zip"], "DESTINATION_UNSAFE")
run("hydrate-output-exists", ["hydrate", "--archive", bundle, "--output", sentinel], "DESTINATION_EXISTS")
link = out / "destination-link";link.symlink_to(sentinel, target_is_directory=True)
run("hydrate-output-symlink", ["hydrate", "--archive", bundle, "--output", link], "DESTINATION_UNSAFE")
run("hydrate-parent-symlink", ["hydrate", "--archive", bundle, "--output", link / "new"], "DESTINATION_UNSAFE")
run("report-inside-gate-tree", ["hydrate", "--archive", bundle, "--output", out / "reported", "--report", out / "reported/report.json"], "DESTINATION_UNSAFE")
assert (sentinel / "keep").read_bytes() == b"outside bytes stay unchanged\n" and sorted(p.name for p in sentinel.iterdir()) == ["keep"]
assert existing_archive.read_bytes() == b"existing archive bytes\n"

# Interpose one real os.read without editing the production codec. Its bytes
# are actually read, then the owner changes that file and restores mtime before
# releasing the read. Same-size/frozen-mtime mutation must still be refused.
mutation_source, mutation_receipt, _, _ = fixture("mutation-source")
target = mutation_source / "payload-native-linux-amd64/copies/executable"
ready, release = out / "read-ready", out / "read-release"
shim = '''import os,runpy,sys,time,json
from pathlib import Path
target,ready,release=map(Path,sys.argv[1:4]);wanted=target.stat();original=os.read;paused=False
def read(fd,count):
 global paused
 data=original(fd,count);current=os.fstat(fd)
 if not paused and (current.st_dev,current.st_ino)==(wanted.st_dev,wanted.st_ino):
  paused=True;ready.write_text("read");deadline=time.monotonic()+10
  while not release.exists():
   if time.monotonic()>deadline:raise TimeoutError("source mutation owner did not release")
   time.sleep(.005)
 return data
os.read=read;sys.argv=sys.argv[4:];runpy.run_path(sys.argv[0],run_name="__main__")
'''
command = [sys.executable, "-c", shim, str(target), str(ready), str(release), str(CODEC), "pack", "--receipt", str(mutation_receipt), "--output", str(out / "mutated.zip")]
process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
deadline = time.monotonic() + 10
while not ready.exists() and process.poll() is None and time.monotonic() < deadline: time.sleep(.005)
observed = ready.exists()
if observed:
    before = target.stat();target.write_bytes(b"z" * before.st_size);os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns));release.write_bytes(b"continue")
stdout, stderr = process.communicate(timeout=15)
(out / "source-mutation.stdout").write_bytes(stdout);(out / "source-mutation.stderr").write_bytes(stderr)
try: code = json.loads(stderr)["error"]["code"]
except (ValueError, KeyError, TypeError): code = None
row("source-mutation", observed and process.returncode == 1 and code == "SOURCE_CHANGED", actualReadPaused=observed, observedCode=code)

# All source-file read guards have completed when the manifest is written. A
# new directory member at this boundary must be caught by the final tree proof.
late_source, late_receipt, _, _ = fixture("late-source")
ready, release = out / "tree-ready", out / "tree-release"
shim = '''import runpy,sys,time,zipfile
from pathlib import Path
ready,release=map(Path,sys.argv[1:3]);original=zipfile.ZipFile.writestr
def write(self,name,*args,**kwargs):
 result=original(self,name,*args,**kwargs)
 if getattr(name,"filename",name)=="transport.json":
  ready.write_text("all file reads completed");deadline=time.monotonic()+10
  while not release.exists():
   if time.monotonic()>deadline:raise TimeoutError("tree mutation owner did not release")
   time.sleep(.005)
 return result
zipfile.ZipFile.writestr=write;sys.argv=sys.argv[3:];runpy.run_path(sys.argv[0],run_name="__main__")
'''
command = [sys.executable, "-c", shim, str(ready), str(release), str(CODEC), "pack", "--receipt", str(late_receipt), "--output", str(out / "late.zip")]
process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE);deadline = time.monotonic() + 10
while not ready.exists() and process.poll() is None and time.monotonic() < deadline: time.sleep(.005)
observed = ready.exists()
if observed:
    (late_source / "payload-native-linux-amd64/added-after-reads").write_bytes(b"new member");release.write_bytes(b"continue")
stdout, stderr = process.communicate(timeout=15)
(out / "source-tree-after-reads.stdout").write_bytes(stdout);(out / "source-tree-after-reads.stderr").write_bytes(stderr)
try: code = json.loads(stderr)["error"]["code"]
except (ValueError, KeyError, TypeError): code = None
row("source-tree-after-reads", observed and process.returncode == 1 and code == "SOURCE_CHANGED", finalTreeBarrierObserved=observed, observedCode=code)

download = out / "downloads"
(download / "lane-one").mkdir(parents=True)
shutil.copyfile(bundle, download / "lane-one/lane.zip")
set_output = out / "set-output"
result = run("initial-set", ["hydrate-set", "--inputs", download, "--output", set_output])
copies = list(set_output.rglob(receipt.name)) if set_output.exists() else []
row("hydrate-set", result.returncode == 0 and len(copies) == 1 and copies[0].read_bytes() == receipt.read_bytes() and
    all((copies[0].parent / name).read_bytes() == value for name, value in originals.items()))
docs, docs_receipt, docs_bytes, _ = fixture("docs-source", "docs")
docs_download = out / "docs-download";docs_download.mkdir();shutil.copytree(docs, docs_download / "docs-artifact")
docs_output = out / "docs-output"
result = run("docs-set", ["hydrate-set", "--inputs", docs_download, "--output", docs_output])
copies = list(docs_output.rglob("docs.json")) if docs_output.exists() else []
row("legacy-docs-only", result.returncode == 0 and len(copies) == 1 and all((copies[0].parent / name).read_bytes() == value for name, value in docs_bytes.items()))

replacement_source, replacement_receipt, _, _ = fixture("other-lane-source", "extended-0")
replacement = out / "other-lane.zip"
assert run("replacement-pack", ["pack", "--receipt", replacement_receipt, "--output", replacement]).returncode == 0
rotating = out / "rotating-download";shutil.copytree(download, rotating)
selected = rotating / "lane-one/lane.zip"
ready, release = out / "set-ready", out / "set-release"
shim = '''import os,runpy,sys,time
from pathlib import Path
target,ready,release=map(Path,sys.argv[1:4]);wanted=target.stat();original=os.close;paused=False
def close(fd):
 global paused
 current=os.fstat(fd);original(fd)
 if not paused and (current.st_dev,current.st_ino)==(wanted.st_dev,wanted.st_ino):
  paused=True;ready.write_text("preflight archive closed");deadline=time.monotonic()+10
  while not release.exists():
   if time.monotonic()>deadline:raise TimeoutError("set mutation owner did not release")
   time.sleep(.005)
os.close=close;sys.argv=sys.argv[4:];runpy.run_path(sys.argv[0],run_name="__main__")
'''
command = [sys.executable, "-c", shim, str(selected), str(ready), str(release), str(CODEC), "hydrate-set", "--inputs", str(rotating), "--output", str(out / "rotated-set-output")]
process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE);deadline = time.monotonic() + 10
while not ready.exists() and process.poll() is None and time.monotonic() < deadline: time.sleep(.005)
observed = ready.exists()
if observed:
    selected.write_bytes(replacement.read_bytes());release.write_bytes(b"continue")
stdout, stderr = process.communicate(timeout=15)
(out / "set-source-replacement.stdout").write_bytes(stdout);(out / "set-source-replacement.stderr").write_bytes(stderr)
try: code = json.loads(stderr)["error"]["code"]
except (ValueError, KeyError, TypeError): code = None
row("set-source-replacement", observed and process.returncode == 1 and code == "SOURCE_CHANGED", preflightBarrierObserved=observed, observedCode=code)
for name in ("set-mixed-raw-and-cas", "set-extra-file", "set-duplicate-lane", "set-native-raw"):
    current = out / ("download-" + name);shutil.copytree(download, current)
    if name == "set-mixed-raw-and-cas": shutil.copyfile(receipt, current / "lane-one/native-linux-amd64.json")
    elif name == "set-extra-file": (current / "lane-one/extra.txt").write_bytes(b"extra")
    elif name == "set-duplicate-lane": shutil.copytree(current / "lane-one", current / "lane-two")
    else:
        shutil.rmtree(current / "lane-one");shutil.copytree(source, current / "lane-one")
    run(name, ["hydrate-set", "--inputs", current, "--output", out / ("refused-" + name)], SPEC["refusalCases"][name])
expected = set(SPEC["positiveCases"]) | set(SPEC["refusalCases"])
finish(expected)

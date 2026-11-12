#!/usr/bin/env python3
"""Archive complete CI fixtures without opening links or special input nodes.

Only generated paths beneath this checkout's artifacts directory are accepted.
The JSON-lines inventory is inside the archive; the small outer receipt binds its
hash and the archive bytes. Verification streams members and never extracts them.
"""

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tarfile
import tempfile
import time


ROOT = Path(__file__).resolve().parents[2]
INVENTORY = "_evidence/inventory.jsonl"


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


class HashingReader:
    def __init__(self, source):
        self.source = source
        self.hash = hashlib.sha256()

    def read(self, size=-1):
        value = self.source.read(size)
        self.hash.update(value)
        return value


def paths(root):
    yield root
    if stat.S_ISDIR(root.lstat().st_mode):
        for child in sorted(root.iterdir()):
            yield from paths(child)


def member_record(info, content_hash=None):
    name = PurePosixPath(info.name)
    require(not name.is_absolute() and ".." not in name.parts and str(name) == info.name,
            "unsafe or noncanonical archive member: " + info.name)
    row = {"path": info.name, "mode": info.mode, "mtimeNs": info.pax_headers.get("ci.mtime_ns")}
    if info.isfile():
        row.update(type="file", size=info.size, sha256=content_hash)
    elif info.isdir():
        row.update(type="directory")
    elif info.issym():
        row.update(type="symlink", target=info.linkname)
    elif info.isfifo():
        row.update(type="fifo")
    else:
        raise ValueError("unsupported evidence member type: " + info.name)
    return row


def add_path(archive, path, workspace):
    before = path.lstat()
    info = tarfile.TarInfo(path.relative_to(workspace).as_posix())
    info.mode = stat.S_IMODE(before.st_mode)
    info.mtime = before.st_mtime_ns // 1_000_000_000
    info.pax_headers = {"ci.mtime_ns": str(before.st_mtime_ns)}
    content_hash = None
    if stat.S_ISREG(before.st_mode):
        info.type, info.size = tarfile.REGTYPE, before.st_size
        with path.open("rb") as source:
            reader = HashingReader(source)
            archive.addfile(info, reader)
            content_hash = reader.hash.hexdigest()
        after = path.lstat()
        require((before.st_size, before.st_mtime_ns, before.st_mode) ==
                (after.st_size, after.st_mtime_ns, after.st_mode),
                "evidence changed during archiving: " + str(path))
        return member_record(info, content_hash)
    if stat.S_ISDIR(before.st_mode):
        info.type = tarfile.DIRTYPE
    elif stat.S_ISLNK(before.st_mode):
        info.type, info.linkname = tarfile.SYMTYPE, os.readlink(path)
    elif stat.S_ISFIFO(before.st_mode):
        info.type = tarfile.FIFOTYPE
    else:
        raise ValueError("unsupported evidence node: " + str(path))
    archive.addfile(info)
    return member_record(info)


def verify(receipt_path):
    started = time.monotonic()
    receipt = json.loads(receipt_path.read_bytes())
    require(receipt.get("schemaVersion") == 1 and receipt.get("archive") == "evidence.tar.gz" and
            receipt.get("inventory") == INVENTORY,
            "invalid evidence receipt")
    archive_path = receipt_path.parent / receipt["archive"]
    require(archive_path.stat().st_size == receipt.get("archiveBytes"), "archive size mismatch")
    require(digest(archive_path) == receipt.get("archiveSha256"), "archive checksum mismatch")
    inventory_hash, seen, count, total_bytes = hashlib.sha256(), set(), 0, 0
    inventory_seen = False
    with tarfile.open(archive_path, "r|gz") as archive:
        for info in archive:
            require(info.name not in seen, "duplicate archive member: " + info.name)
            seen.add(info.name)
            require(not inventory_seen, "inventory must be the final archive member")
            if info.name == INVENTORY:
                require(info.isfile(), "inventory must be a regular file")
                with archive.extractfile(info) as source:
                    actual = hashlib.file_digest(source, "sha256").hexdigest()
                require(actual == inventory_hash.hexdigest() == receipt.get("inventorySha256"),
                        "evidence inventory checksum mismatch")
                inventory_seen = True
                continue
            content_hash = None
            if info.isfile():
                with archive.extractfile(info) as source:
                    content_hash = hashlib.file_digest(source, "sha256").hexdigest()
                total_bytes += info.size
            inventory_hash.update(encoded(member_record(info, content_hash)))
            count += 1
    require(inventory_seen and count > 0 and count == receipt.get("entryCount") and
            total_bytes == receipt.get("regularBytes"), "incomplete evidence inventory")
    return {"verified": True, "entryCount": count, "regularBytes": total_bytes,
            "seconds": round(time.monotonic() - started, 3)}


def pack(roots, output, workspace=ROOT):
    started = time.monotonic()
    workspace = workspace.resolve()
    boundary = workspace / "artifacts"
    output = output.resolve()
    roots = [root.absolute() for root in roots]
    require(bool(roots), "at least one existing evidence root is required")
    require(output.is_relative_to(boundary), "output must be beneath artifacts")
    require(not output.exists(), "evidence output already exists")
    for root in roots:
        require(root.exists() and not root.is_symlink() and root.is_dir(), "invalid evidence root: " + str(root))
        require(root == root.resolve() and root.is_relative_to(boundary), "evidence roots must be beneath artifacts without aliases")
        require(not output.is_relative_to(root), "evidence output cannot be inside an input root")
    require(all(left != right and not left.is_relative_to(right) and not right.is_relative_to(left)
                for index, left in enumerate(roots) for right in roots[index + 1:]), "overlapping evidence roots")
    output.mkdir(parents=True)
    archive_path = output / "evidence.tar.gz"
    inventory_hash, count, total_bytes = hashlib.sha256(), 0, 0
    with tempfile.TemporaryFile() as inventory, archive_path.open("xb") as target:
        with gzip.GzipFile(filename="", mode="wb", compresslevel=1, fileobj=target, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT) as archive:
                for root in sorted(roots):
                    for path in paths(root):
                        row = add_path(archive, path, workspace)
                        line = encoded(row)
                        inventory.write(line)
                        inventory_hash.update(line)
                        count += 1
                        total_bytes += row.get("size", 0)
                info = tarfile.TarInfo(INVENTORY)
                info.size, info.mode = inventory.tell(), 0o644
                inventory.seek(0)
                archive.addfile(info, inventory)
    receipt = {"schemaVersion": 1, "archive": archive_path.name, "archiveSha256": digest(archive_path),
               "inventory": INVENTORY, "inventorySha256": inventory_hash.hexdigest(),
               "entryCount": count, "regularBytes": total_bytes, "archiveBytes": archive_path.stat().st_size,
               "roots": [root.relative_to(workspace).as_posix() for root in roots],
               "packSeconds": round(time.monotonic() - started, 3),
               "scope": "Complete generated evidence; links/FIFOs are metadata only; no automatic extraction"}
    receipt_path = output / "evidence.json"
    receipt_path.write_bytes(encoded(receipt))
    result = verify(receipt_path)
    receipt["verification"] = result
    receipt_path.write_bytes(encoded(receipt))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    packing = actions.add_parser("pack")
    packing.add_argument("--root", action="append", type=Path, required=True)
    packing.add_argument("--optional-root", action="append", type=Path, default=[])
    packing.add_argument("--output", type=Path, required=True)
    checking = actions.add_parser("verify")
    checking.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == "pack":
            roots = args.root + [root for root in args.optional_root if root.exists() or root.is_symlink()]
            result = pack(roots, args.output)
        else:
            result = verify(args.receipt)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, tarfile.TarError) as error:
        print("Evidence verification failed: " + str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

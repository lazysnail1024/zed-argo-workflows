#!/usr/bin/env python3
"""Self-contained receipt transport; original acceptance remains in control.py.

Pack runs on native producers. Hydrate/hydrate-set require POSIX descriptor-relative
no-follow operations (the acceptance job runs on Linux). No command executes a
restored binary, rewrites original JSON, or treats transport integrity as a pass.
"""
from contextlib import contextmanager
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import tempfile
import time
import unicodedata
import zipfile
import zlib

FORMAT = "argo-workflows-receipt-cas"
MAX_ENTRIES = 1_000_000
MAX_FILE = 4 * 1024 ** 3
MAX_LOGICAL = 64 * 1024 ** 3
MAX_MANIFEST = 128 * 1024 ** 2
MAX_CENTRAL = 256 * 1024 ** 2
MAX_ARCHIVE = MAX_LOGICAL + MAX_CENTRAL
CHUNK = 1024 * 1024
SHA = re.compile(r"[0-9a-f]{64}\Z")
DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z", re.I)


class TransportError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(condition, code, message):
    if not condition:
        raise TransportError(code, message)


def strict_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, "INVALID_JSON", "duplicate JSON key")
            value[key] = item
        return value
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite number")))
    except TransportError:
        raise
    except (ValueError, UnicodeError, RecursionError) as error:
        raise TransportError("INVALID_JSON", "invalid UTF-8 JSON") from error


def logical_path(value):
    require(type(value) is str and value and len(value.encode("utf-8")) <= 4096,
            "UNSAFE_PATH", "invalid logical path")
    require(unicodedata.normalize("NFC", value) == value and not any(ord(c) < 32 or ord(c) == 127 for c in value),
            "UNSAFE_PATH", "noncanonical or control-character path")
    parts = value.split("/")
    require(len(parts) <= 128 and all(part not in ("", ".", "..") for part in parts), "UNSAFE_PATH", "unsafe path component")
    require(not any(c in value for c in "\\:*?\"<>|") and all(not part.endswith((".", " ")) and not DEVICE.fullmatch(part) for part in parts),
            "UNSAFE_PATH", "nonportable path alias")
    return parts


def register_path(seen, path):
    logical_path(path)
    key = path.casefold()
    require(key not in seen, "PATH_ALIAS", "duplicate or case-aliased path")
    seen.add(key)


def absolute(path):
    return Path(os.path.abspath(path))


def beneath(path, root):
    return path == root or root in path.parents


def path_signature(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns, value.st_mode)


def cross_signature(value):
    # Windows path.stat may synthesize executable bits for .exe/.bat/.cmd/.com
    # while fstat does not. Python 3.14 path ctime aliases birthtime, whereas fd
    # ctime is ChangeTime. Compare explicit birthtime across those APIs only;
    # same-API signatures still compare full ctime and modes around each read.
    timestamp = value.st_birthtime_ns if os.name == "nt" and hasattr(value, "st_birthtime_ns") else value.st_ctime_ns
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, timestamp, stat.S_IFMT(value.st_mode))


def safe_parents(path, create=False):
    for parent in reversed(path.parents):
        if not parent.exists() and not parent.is_symlink() and create:
            try:
                parent.mkdir()
            except FileExistsError:
                pass
        try:
            value = parent.lstat()
        except OSError as error:
            raise TransportError("DESTINATION_UNSAFE", "missing or unsafe parent") from error
        require(stat.S_ISDIR(value.st_mode) and not stat.S_ISLNK(value.st_mode), "DESTINATION_UNSAFE", "symlink or non-directory parent")


def new_output(path, forbidden=None):
    path = absolute(path)
    require(forbidden is None or not beneath(path, forbidden), "DESTINATION_UNSAFE", "output or report is inside the input/gate tree")
    safe_parents(path, create=True)
    require(not path.is_symlink(), "DESTINATION_UNSAFE", "symlink destination")
    require(not os.path.lexists(path), "DESTINATION_EXISTS", "destination already exists")
    return path


@contextmanager
def regular(path):
    path = absolute(path)
    # lstat before open and no-follow where available reject FIFOs and links
    # before any read. Descriptor identity is checked before consuming bytes.
    for parent in reversed(path.parents):
        value = parent.lstat()
        require(stat.S_ISDIR(value.st_mode) and not stat.S_ISLNK(value.st_mode), "SOURCE_TYPE", "source has a symlink parent")
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode), "SOURCE_TYPE", "source entry is not a regular file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        require(stat.S_ISREG(opened.st_mode) and cross_signature(before) == cross_signature(opened),
                "SOURCE_CHANGED", "source changed while opening")
        yield descriptor, before
        require(path_signature(os.fstat(descriptor)) == path_signature(opened) and
                path_signature(path.lstat()) == path_signature(before), "SOURCE_CHANGED", "source changed while reading")
    finally:
        os.close(descriptor)


def read_bytes(path, limit):
    with regular(path) as (descriptor, value):
        require(value.st_size <= limit, "LIMIT_EXCEEDED", "source exceeds byte limit")
        chunks, count = [], 0
        while True:
            chunk = os.read(descriptor, min(CHUNK, limit + 1 - count))
            if not chunk:
                break
            chunks.append(chunk)
            count += len(chunk)
            require(count <= limit, "LIMIT_EXCEEDED", "source grew beyond byte limit")
        require(count == value.st_size, "SOURCE_CHANGED", "source size changed while reading")
        return b"".join(chunks), stat.S_IMODE(value.st_mode)


def receipt_layout(raw, name):
    require(len(logical_path(name)) == 1 and name.endswith(".json"), "INVALID_INVENTORY", "receipt must be a top-level JSON file")
    value = strict_json(raw)
    require(type(value) is dict and type(value.get("schemaVersion")) is int and value["schemaVersion"] == 1,
            "INVALID_INVENTORY", "unsupported original receipt")
    lane, evidence = value.get("lane"), value.get("evidence")
    require(type(lane) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", lane) is not None and
            type(evidence) is dict and evidence, "INVALID_INVENTORY", "missing original lane inventory")
    logical_path(lane)
    require(len(evidence) < MAX_ENTRIES, "LIMIT_EXCEEDED", "too many original receipt files")
    seen = set()
    for relative, digest in evidence.items():
        register_path(seen, relative)
        require(type(digest) is str and SHA.fullmatch(digest), "INVALID_INVENTORY", "invalid original evidence digest")
    return value, "payload-" + lane


def source_inventory(receipt, raw):
    value, payload = receipt_layout(raw, receipt.name)
    root = receipt.parent
    require({entry.name for entry in root.iterdir()} == {receipt.name, payload}, "INVALID_INVENTORY", "receipt directory has extra or missing entries")
    entries, snapshots, seen = [], {"": path_signature(root.lstat())}, set()
    def walk(path):
        relative = path.relative_to(root).as_posix()
        current = path.lstat()
        require(stat.S_ISDIR(current.st_mode) or stat.S_ISREG(current.st_mode), "SOURCE_TYPE", "nonregular physical source entry")
        register_path(seen, relative)
        mode = stat.S_IMODE(current.st_mode)
        require(mode <= 0o777, "SOURCE_TYPE", "special permission bits are not transportable")
        snapshots[relative] = path_signature(current)
        if stat.S_ISDIR(current.st_mode):
            entries.append({"path": relative, "type": "directory", "mode": mode})
            for child in sorted(path.iterdir()):
                walk(child)
        else:
            digest = hashlib.sha256(raw).hexdigest() if path == receipt else value["evidence"].get(path.relative_to(root / payload).as_posix())
            require(digest is not None, "INVALID_INVENTORY", "source file is absent from original receipt")
            entries.append({"path": relative, "type": "file", "mode": mode, "size": current.st_size, "sha256": digest})
        require(len(entries) <= MAX_ENTRIES, "LIMIT_EXCEEDED", "too many physical entries")
    walk(receipt)
    walk(root / payload)
    manifest = {"schemaVersion": 1, "format": FORMAT, "receipt": receipt.name, "entries": sorted(entries, key=lambda row: row["path"])}
    validate_manifest(manifest)
    require({entry["path"] for entry in entries if entry["type"] == "file"} ==
            {receipt.name, *(payload + "/" + name for name in value["evidence"])}, "INVALID_INVENTORY", "original receipt inventory differs from source")
    return manifest, snapshots


def validate_manifest(value):
    require(type(value) is dict and type(value.get("schemaVersion")) is int and value["schemaVersion"] == 1 and value.get("format") == FORMAT,
            "UNSUPPORTED_FORMAT", "unsupported receipt transport format")
    require(set(value) == {"schemaVersion", "format", "receipt", "entries"} and type(value["entries"]) is list,
            "INVALID_INVENTORY", "unexpected transport fields")
    require(0 < len(value["entries"]) <= MAX_ENTRIES, "LIMIT_EXCEEDED", "invalid entry count")
    require(len(logical_path(value["receipt"])) == 1, "INVALID_INVENTORY", "invalid receipt path")
    seen, by_path, logical = set(), {}, 0
    for entry in value["entries"]:
        require(type(entry) is dict, "INVALID_INVENTORY", "entry must be an object")
        kind = entry.get("type")
        require(kind in ("file", "directory") and set(entry) == ({"path", "type", "mode", "size", "sha256"} if kind == "file" else {"path", "type", "mode"}),
                "INVALID_INVENTORY", "invalid entry type or fields")
        register_path(seen, entry["path"])
        require(type(entry["mode"]) is int and 0 <= entry["mode"] <= 0o777, "INVALID_INVENTORY", "invalid logical mode")
        by_path[entry["path"]] = entry
        if kind == "file":
            require(type(entry["size"]) is int and entry["size"] >= 0 and type(entry["sha256"]) is str and SHA.fullmatch(entry["sha256"]),
                    "INVALID_INVENTORY", "invalid file size or digest")
            require(entry["size"] <= MAX_FILE, "LIMIT_EXCEEDED", "file exceeds transport byte limit")
            logical += entry["size"]
    require(logical <= MAX_LOGICAL, "LIMIT_EXCEEDED", "logical payload exceeds transport byte limit")
    objects = {}
    for path, entry in by_path.items():
        for parent in Path(path).parents:
            if parent.as_posix() != ".":
                require(parent.as_posix() in by_path and by_path[parent.as_posix()]["type"] == "directory", "PATH_ALIAS", "missing directory or file-directory path collision")
        if entry["type"] == "file":
            digest = entry["sha256"]
            require(digest not in objects or objects[digest] == entry["size"], "OBJECT_MISMATCH", "one object has inconsistent logical sizes")
            objects[digest] = entry["size"]
    require(value["receipt"] in by_path and by_path[value["receipt"]]["type"] == "file" and
            by_path[value["receipt"]]["size"] <= MAX_MANIFEST, "INVALID_INVENTORY", "missing or oversized receipt file")
    return by_path, objects, logical


def zip_info(name):
    info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    info._compresslevel = 6
    return info


def pack(receipt, output):
    started = time.monotonic()
    receipt = absolute(receipt)
    output = new_output(output, receipt.parent)
    raw, _ = read_bytes(receipt, MAX_MANIFEST)
    manifest, snapshots = source_inventory(receipt, raw)
    files, objects, logical = validate_manifest(manifest)
    metadata = (json.dumps(manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode()
    require(len(metadata) <= MAX_MANIFEST, "LIMIT_EXCEEDED", "transport manifest exceeds byte limit")
    descriptor = os.open(output, os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    written = set()
    with os.fdopen(descriptor, "w+b") as stream, zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        for name, entry in sorted(files.items()):
            if entry["type"] != "file":
                continue
            digest, size = hashlib.sha256(), 0
            with regular(receipt.parent / name) as (source, source_stat):
                require(path_signature(source_stat) == snapshots[name], "SOURCE_CHANGED", "source changed after inventory")
                writer = archive.open(zip_info("objects/" + entry["sha256"]), "w", force_zip64=True) if entry["sha256"] not in written else None
                try:
                    while True:
                        chunk = os.read(source, CHUNK)
                        if not chunk:
                            break
                        size += len(chunk)
                        require(size <= entry["size"], "SOURCE_CHANGED", "source grew while packing")
                        digest.update(chunk)
                        if writer is not None:
                            writer.write(chunk)
                finally:
                    if writer is not None:
                        writer.close()
                require(size == entry["size"] and digest.hexdigest() == entry["sha256"], "SOURCE_CHANGED", "source bytes differ from original receipt")
            written.add(entry["sha256"])
        archive.writestr(zip_info("transport.json"), metadata)
    try:
        current, final_snapshots = source_inventory(receipt, raw)
    except (OSError, TransportError) as error:
        raise TransportError("SOURCE_CHANGED", "source inventory changed after its file reads") from error
    require(current == manifest and final_snapshots == snapshots, "SOURCE_CHANGED", "source tree changed while packing")
    with output.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"action": "pack", "format": FORMAT, "logicalFiles": sum(entry["type"] == "file" for entry in files.values()),
            "logicalBytes": logical, "uniqueObjects": len(objects), "uniqueBytes": sum(objects.values()),
            "archiveBytes": output.stat().st_size, "archiveSha256": digest, "seconds": time.monotonic() - started}


def central_bounds(stream, size):
    require(22 <= size <= MAX_ARCHIVE, "LIMIT_EXCEEDED", "archive size outside bounds")
    stream.seek(max(0, size - 65557))
    tail = stream.read()
    offset = tail.rfind(b"PK\x05\x06")
    require(offset >= 0 and offset + 22 <= len(tail), "UNSAFE_ARCHIVE", "missing ZIP end record")
    record = struct.unpack_from("<4s4H2IH", tail, offset)
    require(offset + 22 + record[7] == len(tail) and record[7] == 0 and record[1] == record[2] == 0,
            "UNSAFE_ARCHIVE", "ZIP comments, trailing data or multiple disks are not supported")
    count, length, start = record[4], record[5], record[6]
    end = size - len(tail) + offset
    if count == 0xffff or length == 0xffffffff or start == 0xffffffff:
        require(end >= 20, "UNSAFE_ARCHIVE", "missing ZIP64 locator")
        stream.seek(end - 20); locator = stream.read(20)
        require(len(locator) == 20, "UNSAFE_ARCHIVE", "short ZIP64 locator")
        signature, disk, position, disks = struct.unpack("<4sIQI", locator)
        require(signature == b"PK\x06\x07" and disk == 0 and disks == 1 and 0 <= position < end - 20,
                "UNSAFE_ARCHIVE", "invalid ZIP64 locator")
        stream.seek(position); raw = stream.read(56)
        require(len(raw) == 56, "UNSAFE_ARCHIVE", "short ZIP64 record")
        values = struct.unpack("<4sQ2H2I4Q", raw)
        require(values[0] == b"PK\x06\x06" and values[1] == 44 and values[4] == values[5] == 0 and
                values[6] == values[7] and position + 56 == end - 20, "UNSAFE_ARCHIVE", "unsupported ZIP64 layout")
        count, length, start, end = values[7], values[8], values[9], position
    else:
        require(record[3] == count, "UNSAFE_ARCHIVE", "inconsistent ZIP member count")
    require(0 < count <= MAX_ENTRIES + 1 and 0 <= length <= MAX_CENTRAL, "LIMIT_EXCEEDED", "ZIP directory exceeds bounds")
    require(start >= 0 and start + length == end, "UNSAFE_ARCHIVE", "invalid ZIP directory extent")
    return count, start


def zip_extras(raw):
    values, offset = {}, 0
    while offset < len(raw):
        require(offset + 4 <= len(raw), "UNSAFE_ARCHIVE", "short ZIP extra field")
        kind, size = struct.unpack_from("<HH", raw, offset)
        offset += 4
        require(kind == 1 and kind not in values and offset + size <= len(raw), "UNSAFE_ARCHIVE", "unsupported or duplicate ZIP extra field")
        values[kind] = raw[offset:offset + size]
        offset += size
    return values


class BundleReader:
    """Read actual bounded deflate output, not ZipExtFile's declared-size slice."""
    def __init__(self, stream, members, central_start):
        self.stream, self.members, self.offsets = stream, members, {}
        intervals = []
        for name, info in members.items():
            zip_extras(info.extra)
            stream.seek(info.header_offset)
            raw = stream.read(30)
            require(len(raw) == 30, "UNSAFE_ARCHIVE", "short local ZIP header")
            fields = struct.unpack("<4s5H3I2H", raw)
            require(fields[0] == b"PK\x03\x04" and fields[2] == info.flag_bits and fields[3] == info.compress_type and fields[6] == info.CRC,
                    "UNSAFE_ARCHIVE", "local and central ZIP headers differ")
            encoded = stream.read(fields[9])
            extra = zip_extras(stream.read(fields[10]))
            require(encoded == name.encode("ascii"), "UNSAFE_ARCHIVE", "local ZIP name differs")
            compressed, uncompressed = fields[7], fields[8]
            if compressed == 0xffffffff or uncompressed == 0xffffffff:
                sizes = extra.get(1, b"")
                require(len(sizes) == 16, "UNSAFE_ARCHIVE", "invalid local ZIP64 sizes")
                uncompressed, compressed = struct.unpack("<QQ", sizes)
            require((compressed, uncompressed) == (info.compress_size, info.file_size), "UNSAFE_ARCHIVE", "local ZIP sizes differ")
            start = info.header_offset + 30 + fields[9] + fields[10]
            require(start + compressed <= central_start, "UNSAFE_ARCHIVE", "ZIP object overlaps metadata")
            self.offsets[name] = start
            intervals.append((info.header_offset, start + compressed))
        previous = 0
        for start, end in sorted(intervals):
            require(start == previous and end >= start, "UNSAFE_ARCHIVE", "aliased, gapped or prefixed ZIP members")
            previous = end
        require(previous == central_start, "UNSAFE_ARCHIVE", "unexpected bytes before ZIP directory")

    def chunks(self, name):
        info = self.members[name]
        self.stream.seek(self.offsets[name])
        remaining, size, crc = info.compress_size, 0, 0
        decoder = zlib.decompressobj(-15)
        code = "OBJECT_MISMATCH" if name.startswith("objects/") else "UNSAFE_ARCHIVE"
        try:
            while remaining:
                encoded = self.stream.read(min(CHUNK, remaining))
                require(encoded, "UNSAFE_ARCHIVE", "truncated compressed object")
                remaining -= len(encoded)
                while True:
                    limit = min(CHUNK, info.file_size - size + 1)
                    chunk = decoder.decompress(encoded, limit)
                    encoded = decoder.unconsumed_tail
                    size += len(chunk)
                    require(size <= info.file_size, code, "actual decompression exceeds declared size")
                    crc = zlib.crc32(chunk, crc)
                    if chunk:
                        yield chunk
                    require(not decoder.unused_data, "UNSAFE_ARCHIVE", "trailing compressed stream data")
                    # max_length may consume all compressed input while zlib
                    # still retains output. Drain that output with the same
                    # size bound before requesting more input or checking EOF.
                    if not encoded and (decoder.eof or len(chunk) < limit):
                        break
                if decoder.eof:
                    require(remaining == 0, "UNSAFE_ARCHIVE", "compressed object ends before its declared boundary")
            require(decoder.eof and size == info.file_size and crc == info.CRC, "UNSAFE_ARCHIVE", "truncated stream, size or CRC mismatch")
        except zlib.error as error:
            raise TransportError("UNSAFE_ARCHIVE", "invalid deflate stream") from error

    def read(self, name):
        return b"".join(self.chunks(name))


@contextmanager
def opened_bundle(path):
    try:
        with regular(path) as (descriptor, status), os.fdopen(os.dup(descriptor), "rb") as stream:
            member_count, central_start = central_bounds(stream, status.st_size)
            with zipfile.ZipFile(stream) as archive:
                members = {}
                require(len(archive.infolist()) == member_count, "UNSAFE_ARCHIVE", "ZIP member count differs from end record")
                for info in archive.infolist():
                    name = info.filename
                    require(name == info.orig_filename and name not in members and
                            (name == "transport.json" or re.fullmatch(r"objects/[0-9a-f]{64}", name)),
                            "UNSAFE_ARCHIVE", "duplicate or unexpected physical ZIP member")
                    require(info.create_system == 3 and stat.S_ISREG(info.external_attr >> 16) and
                            info.compress_type == zipfile.ZIP_DEFLATED and info.flag_bits & ~0x800 == 0 and not info.comment,
                            "UNSAFE_ARCHIVE", "unsupported ZIP entry type or encoding")
                    limit = MAX_MANIFEST if name == "transport.json" else MAX_FILE
                    require(0 <= info.file_size <= limit and 0 <= info.compress_size <= MAX_ARCHIVE,
                            "LIMIT_EXCEEDED", "ZIP member exceeds bounds")
                    members[name] = info
                require("transport.json" in members, "INVALID_INVENTORY", "missing transport manifest")
                bundle = BundleReader(stream, members, central_start)
                manifest = strict_json(bundle.read("transport.json"))
                entries, objects, logical = validate_manifest(manifest)
                require(set(members) == {"transport.json", *("objects/" + digest for digest in objects)},
                        "OBJECT_MISMATCH", "missing or unexpected content object")
                require(all(members["objects/" + digest].file_size == size for digest, size in objects.items()),
                        "OBJECT_MISMATCH", "physical object size differs from logical inventory")
                entry = entries[manifest["receipt"]]
                raw = bundle.read("objects/" + entry["sha256"])
                require(len(raw) == entry["size"] and hashlib.sha256(raw).hexdigest() == entry["sha256"],
                        "OBJECT_MISMATCH", "receipt object hash mismatch")
                receipt, payload = receipt_layout(raw, manifest["receipt"])
                expected = {manifest["receipt"], *(payload + "/" + name for name in receipt["evidence"])}
                require({name for name, item in entries.items() if item["type"] == "file"} == expected and
                        all(name == manifest["receipt"] or name == payload or name.startswith(payload + "/") for name in entries),
                        "INVALID_INVENTORY", "transport and original receipt layouts differ")
                require(all(entries[payload + "/" + name]["sha256"] == digest for name, digest in receipt["evidence"].items()),
                        "OBJECT_MISMATCH", "transport rewrites original evidence digests")
                yield bundle, manifest, entries, objects, logical, receipt["lane"]
    except (zipfile.BadZipFile, EOFError, struct.error) as error:
        raise TransportError("UNSAFE_ARCHIVE", "invalid or truncated ZIP") from error


class DirectoryWriter:
    """New independent files through held POSIX directory descriptors."""
    def __init__(self, output):
        require(os.name == "posix" and hasattr(os, "O_NOFOLLOW") and os.open in os.supports_dir_fd and os.mkdir in os.supports_dir_fd,
                "UNSUPPORTED_HOST", "hydration requires POSIX no-follow directory descriptors; retain the raw artifact on other hosts")
        self.path = new_output(output)
        self.parent = self.open_absolute(self.path.parent)
        try:
            os.mkdir(self.path.name, 0o700, dir_fd=self.parent)
            self.root = os.open(self.path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.parent)
            status = os.fstat(self.root)
            self.identities = {"": (status.st_dev, status.st_ino)}
        except BaseException:
            os.close(self.parent)
            raise

    @staticmethod
    def open_absolute(path):
        descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in path.parts[1:]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor);descriptor = child
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def placement(self):
        safe_parents(self.path)
        visible, opened = self.path.lstat(), os.fstat(self.root)
        require(stat.S_ISDIR(visible.st_mode) and (visible.st_dev, visible.st_ino) == (opened.st_dev, opened.st_ino),
                "DESTINATION_UNSAFE", "hydration destination was replaced")

    def parent_for(self, relative):
        self.placement()
        parts = logical_path(relative)
        descriptor = os.dup(self.root)
        try:
            prefix = []
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor);descriptor = child
                prefix.append(part)
                status = os.fstat(descriptor)
                require(self.identities.get("/".join(prefix)) == (status.st_dev, status.st_ino), "DESTINATION_UNSAFE", "hydrated directory was replaced")
            return descriptor, parts[-1]
        except BaseException:
            os.close(descriptor)
            raise

    def directory(self, relative):
        parent, leaf = self.parent_for(relative)
        try:
            os.mkdir(leaf, 0o700, dir_fd=parent)
            status = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
            require(stat.S_ISDIR(status.st_mode), "DESTINATION_UNSAFE", "new directory was replaced")
            self.identities[relative] = (status.st_dev, status.st_ino)
        finally:
            os.close(parent)

    def file(self, relative):
        parent, leaf = self.parent_for(relative)
        try:
            return os.open(leaf, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        finally:
            os.close(parent)

    def finish(self, directories):
        for entry in sorted(directories, key=lambda row: row["path"].count("/"), reverse=True):
            parent, leaf = self.parent_for(entry["path"])
            try:
                descriptor = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    os.fchmod(descriptor, entry["mode"])
                finally:
                    os.close(descriptor)
            finally:
                os.close(parent)
        self.placement()
        os.fchmod(self.root, 0o755)

    def close(self):
        os.close(self.root)
        os.close(self.parent)


def write_all(descriptor, value):
    view = memoryview(value)
    while view:
        count = os.write(descriptor, view)
        require(count > 0, "DESTINATION_UNSAFE", "short destination write")
        view = view[count:]


def hydrate(archive_path, output):
    started = time.monotonic()
    output = new_output(output)
    with opened_bundle(archive_path) as (archive, manifest, entries, objects, logical, lane):
        writer = DirectoryWriter(output)
        try:
            directories = [row for row in entries.values() if row["type"] == "directory"]
            for entry in sorted(directories, key=lambda row: (row["path"].count("/"), row["path"])):
                writer.directory(entry["path"])
            groups = {}
            for entry in entries.values():
                if entry["type"] == "file":
                    groups.setdefault(entry["sha256"], []).append(entry)
            for digest, files in sorted(groups.items()):
                first = writer.file(files[0]["path"])
                try:
                    actual, size = hashlib.sha256(), 0
                    for chunk in archive.chunks("objects/" + digest):
                        size += len(chunk)
                        require(size <= objects[digest], "OBJECT_MISMATCH", "object exceeds declared size")
                        actual.update(chunk);write_all(first, chunk)
                    require(size == objects[digest] and actual.hexdigest() == digest, "OBJECT_MISMATCH", "content object hash mismatch")
                    for entry in files[1:]:
                        descriptor = writer.file(entry["path"])
                        try:
                            os.lseek(first, 0, os.SEEK_SET)
                            while True:
                                chunk = os.read(first, CHUNK)
                                if not chunk:
                                    break
                                write_all(descriptor, chunk)
                            os.fchmod(descriptor, entry["mode"])
                        finally:
                            os.close(descriptor)
                    os.fchmod(first, files[0]["mode"])
                finally:
                    os.close(first)
            writer.finish(directories)
        finally:
            writer.close()
    return {"action": "hydrate", "lane": lane, "logicalFiles": sum(row["type"] == "file" for row in entries.values()),
            "logicalBytes": logical, "uniqueObjects": len(objects), "seconds": time.monotonic() - started}


def input_tree_snapshot(root):
    records = {}
    def visit(path):
        value = path.lstat()
        require(stat.S_ISDIR(value.st_mode) or stat.S_ISREG(value.st_mode), "SOURCE_TYPE", "nonregular download input")
        records[path.relative_to(root).as_posix()] = path_signature(value)
        require(len(records) <= MAX_ENTRIES, "LIMIT_EXCEEDED", "too many download input entries")
        if stat.S_ISDIR(value.st_mode):
            for child in sorted(path.iterdir()):
                visit(child)
    visit(root)
    return records


def hydrate_set(inputs, output):
    inputs, output = absolute(inputs), new_output(output)
    require(not beneath(output, inputs), "DESTINATION_UNSAFE", "gate output is inside transport inputs")
    require(inputs.is_dir() and not inputs.is_symlink(), "SOURCE_TYPE", "download root must be a directory")
    initial = input_tree_snapshot(inputs)
    selections, lanes, names = [], set(), set()
    for artifact in sorted(inputs.iterdir()):
        register_path(names, artifact.name)
        require(artifact.is_dir() and not artifact.is_symlink(), "SOURCE_TYPE", "download entries must be artifact directories")
        children = list(artifact.iterdir())
        if len(children) == 1 and children[0].suffix == ".zip":
            with opened_bundle(children[0]) as (_, _, _, _, _, lane):
                pass
            selection = ("cas", children[0], lane)
        else:
            require({child.name for child in children} == {"docs.json", "payload-docs"}, "INVALID_INVENTORY", "mixed, extra or unsupported raw artifact entries")
            raw, _ = read_bytes(artifact / "docs.json", MAX_MANIFEST)
            value, _ = receipt_layout(raw, "docs.json")
            require(value["lane"] == "docs", "INVALID_INVENTORY", "only legacy docs may use raw layout")
            source_inventory(artifact / "docs.json", raw)
            selection = ("docs", artifact / "docs.json", "docs")
        register_path(lanes, selection[2])
        selections.append(selection)
    require(selections and (all(kind == "cas" for kind, _, _ in selections) or len(selections) == 1),
            "INVALID_INVENTORY", "empty or mixed raw/CAS artifact set")
    require(input_tree_snapshot(inputs) == initial, "SOURCE_CHANGED", "download inputs changed during preflight")
    owner = DirectoryWriter(output)
    results = []
    try:
        for kind, path, lane in selections:
            owner.placement()
            require(path_signature(path.lstat()) == initial[path.relative_to(inputs).as_posix()],
                    "SOURCE_CHANGED", "selected input changed after preflight")
            if kind == "cas":
                result = hydrate(path, output / lane)
            else:
                with tempfile.TemporaryDirectory(prefix="wf-docs-transport-") as scratch:
                    archive = Path(scratch) / "docs.zip"
                    pack(path, archive)
                    result = hydrate(archive, output / lane)
            require(result["lane"] == lane, "SOURCE_CHANGED", "restored lane differs from preflight")
            results.append(result)
        try:
            current = input_tree_snapshot(inputs)
        except (OSError, TransportError) as error:
            raise TransportError("SOURCE_CHANGED", "download inventory changed during hydration") from error
        require(current == initial, "SOURCE_CHANGED", "download inputs changed during hydration")
        owner.finish([])
    finally:
        owner.close()
    return {"action": "hydrate-set", "lanes": results, "acceptance": "not evaluated; run the unchanged control.py gate"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    command = actions.add_parser("pack");command.add_argument("--receipt", type=Path, required=True)
    command = actions.add_parser("hydrate");command.add_argument("--archive", type=Path, required=True)
    command = actions.add_parser("hydrate-set");command.add_argument("--inputs", type=Path, required=True)
    for command in actions.choices.values():
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    report_path = None
    try:
        if args.report:
            forbidden = absolute(args.receipt).parent if args.action == "pack" else absolute(args.output)
            selected_report = new_output(args.report, forbidden)
            if args.action == "hydrate-set":
                require(not beneath(selected_report, absolute(args.inputs)), "DESTINATION_UNSAFE", "report is inside transport inputs")
            report_path = selected_report
        if args.action == "pack":
            report = pack(args.receipt, args.output)
        elif args.action == "hydrate":
            report = hydrate(args.archive, args.output)
        else:
            report = hydrate_set(args.inputs, args.output)
        raw = (json.dumps(report, sort_keys=True) + "\n").encode()
        if report_path:
            with report_path.open("xb") as stream:
                stream.write(raw)
        sys.stdout.buffer.write(raw)
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        code = error.code if isinstance(error, TransportError) else "IO_ERROR"
        raw = json.dumps({"error": {"code": code, "message": str(error)}}) + "\n"
        if report_path is not None and not os.path.lexists(report_path):
            try:
                new_output(report_path)
                with report_path.open("xb") as stream:
                    stream.write(raw.encode())
            except (OSError, TransportError):
                pass
        sys.stderr.write(raw)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

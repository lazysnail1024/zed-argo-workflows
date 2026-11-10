#!/usr/bin/env python3
"""Linux read-cost checks through real LSP; count file opens, not elapsed time.

Run separately from the portable query/configuration suite. inotify watches the
actual fixture files shared with the native process, so no PID namespace or
absolute latency threshold is involved. Correct candidate content is mandatory.
"""
import argparse
import ctypes
import importlib.util
import os
import pathlib
import platform
import struct
import sys
import time
import traceback

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("binary", type=pathlib.Path)
parser.add_argument("output", type=pathlib.Path)
parser.add_argument("--cases", nargs="+", choices=("local", "external", "configuration"),
                    default=("local", "external", "configuration"))
args = parser.parse_args()
if sys.platform != "linux":
    parser.error("this read-cost suite requires Linux inotify; run the portable wf-reaudit-queries suite on other platforms")
spec = importlib.util.spec_from_file_location("wire", pathlib.Path(__file__).with_name("wf-audit-gaps.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

SCOPES = m.CONFIG + """scopes:
  - name: selected
    include: ["callers/**", "alpha/**"]
  - name: other
    include: ["beta/**"]
"""


class FileReads:
    OPEN = 0x20
    CLOSE_NOWRITE = 0x10
    OVERFLOW = 0x4000

    def __init__(self, paths):
        libc = ctypes.CDLL(None, use_errno=True)
        libc.inotify_init1.argtypes = [ctypes.c_int]
        libc.inotify_init1.restype = ctypes.c_int
        libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        libc.inotify_add_watch.restype = ctypes.c_int
        self.fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1")
        self.names = {}
        try:
            for name, path in paths.items():
                descriptor = libc.inotify_add_watch(self.fd, os.fsencode(path), self.OPEN | self.CLOSE_NOWRITE)
                if descriptor < 0:
                    raise OSError(ctypes.get_errno(), "inotify_add_watch: " + str(path))
                self.names[descriptor] = name
        except Exception:
            os.close(self.fd)
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        os.close(self.fd)

    def take(self):
        counts = {name: 0 for name in self.names.values()}
        while True:
            try:
                data = os.read(self.fd, 65536)
            except BlockingIOError:
                break
            if not data:
                break
            offset = 0
            while offset < len(data):
                descriptor, mask, cookie, length = struct.unpack_from("iIII", data, offset)
                offset += 16 + length
                if mask & self.OVERFLOW:
                    raise RuntimeError("inotify overflow; read counts are not trustworthy")
                if mask & self.OPEN:
                    counts[self.names[descriptor]] += 1
        return counts


def setup(name):
    if (m.OUT / name).exists():
        raise RuntimeError("use a fresh output directory")
    return m.setup(name, {
        "callers/caller.yaml": m.CALLER,
        "alpha/base.yaml": m.BASE,
        "beta/base.yaml": m.BASE.replace("work", "work-unrelated"),
    }, SCOPES)


def complete(client, path, needle, offset):
    started = time.monotonic()
    response = client.request("textDocument/completion", {
        "textDocument": {"uri": path.as_uri()},
        "position": m.pos(m.CALLER, needle, offset=offset),
    })
    return {"response": response, "seconds": time.monotonic() - started}


def labels(response):
    result = response.get("result")
    return sorted(x.get("label") for x in result) if isinstance(result, list) else []


def reads(work):
    return FileReads({"selected": work / "alpha/base.yaml", "unrelated": work / "beta/base.yaml"})


def local():
    dest, work = setup("local-completion-io")
    client = m.LSP(dest, work)
    path = work / "callers/caller.yaml"
    try:
        initial = client.open(path, m.CALLER)
        with reads(work) as counter:
            # Watch after initial diagnostics. No filesystem writes, edits,
            # pending document changes or unrelated processes touch this input.
            rows = []
            for index in range(20):
                if index % 2 == 0:
                    row = complete(client, path, "entrypoint: main", 14)
                    row["correct"] = labels(row["response"]) == ["main"]
                else:
                    row = complete(client, path, "  templates:", 2)
                    row["correct"] = "activeDeadlineSeconds" in labels(row["response"])
                rows.append(row)
            opened = counter.take()
        m.result("local-completion-io", {
            "initial_diagnostics_complete": initial == [],
            "all_candidates_correct": all(row["correct"] for row in rows),
            "no_protocol_errors": all("error" not in row["response"] for row in rows),
            "no_scope_reads": sum(opened.values()) == 0,
        }, {"fileOpens": opened, "queries": rows,
            "measurement": "IN_OPEN with IN_CLOSE_NOWRITE preventing adjacent opens from coalescing; elapsed times are descriptive only"})
    finally:
        client.close()


def external():
    dest, work = setup("external-completion-io")
    client = m.LSP(dest, work)
    path = work / "callers/caller.yaml"
    try:
        client.open(path, m.CALLER)
        with reads(work) as counter:
            rows = []
            for _ in range(3):
                row = complete(client, path, "template: work", len("template: wo"))
                row["fileOpens"] = counter.take()
                rows.append(row)
        m.result("external-completion-io", {
            "exact_scope_candidates": all(labels(row["response"]) == ["work"] for row in rows),
            "no_protocol_errors": all("error" not in row["response"] for row in rows),
            "one_scope_acquisition_per_query": all(row["fileOpens"]["selected"] == 1 for row in rows),
            "no_unrelated_scope_reads": all(row["fileOpens"]["unrelated"] == 0 for row in rows),
        }, {"queries": rows})
    finally:
        client.close()


def configuration():
    dest, work = setup("configuration-io")
    client = m.LSP(dest, work)
    try:
        path = work / ".argo-workflows-ls.yaml"
        initial = client.open(path, SCOPES)
        # The independent discovery worker may read resources after initialize.
        # Measure unchanged config validation after that first pass, so the
        # assertion concerns this input event rather than background indexing.
        deadline = time.monotonic() + 10
        while b'"state":"Ready"' not in client.stderr_raw:
            if time.monotonic() >= deadline:
                raise TimeoutError("initial discovery did not become Ready")
            time.sleep(.01)
        with reads(work) as counter:
            diagnostics = client.change(path, SCOPES, 2)
            opened = counter.take()
        m.result("configuration-io", {
            "valid_config_clean": initial == [] and diagnostics == [],
            "no_resource_reads": sum(opened.values()) == 0,
        }, {"diagnostics": diagnostics, "fileOpens": opened})
    finally:
        client.close()


for name, run in [("local", local), ("external", external), ("configuration", configuration)]:
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
    "cases": list(args.cases), "measurement": "Linux inotify file-open events; no PID or latency gate",
    "inputs": {str(p.relative_to(m.OUT)): m.sha(p) for p in m.OUT.rglob("*")
               if p.is_file() and "input" in p.parts},
})
sys.exit(0 if m.RESULTS and all(row["pass"] for row in m.RESULTS) else 1)

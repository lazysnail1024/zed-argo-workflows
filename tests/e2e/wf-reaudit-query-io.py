#!/usr/bin/env python3
"""Linux read-cost checks through real LSP; count file opens, not elapsed time.

Run separately from the portable query/configuration suite. inotify watches the
actual fixture files shared with the native process, so no PID namespace or
absolute latency threshold is involved. Correct candidate content is mandatory.
"""
import argparse
import ctypes
import importlib.util
import json
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
parser.add_argument("--cases", nargs="+", choices=("local", "external", "configuration", "work-trace", "cancel-trace"),
                    default=("local", "external", "configuration", "work-trace", "cancel-trace"))
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


def traced_client(dest, work, enabled):
    # The child observes its opt-in at process start; no production workload is
    # added merely to exercise a counter, and other consumers retain trace-off.
    old = os.environ.get('ARGO_WORKFLOWS_LS_TRACE')
    try:
        if enabled: os.environ['ARGO_WORKFLOWS_LS_TRACE'] = '1'
        else: os.environ.pop('ARGO_WORKFLOWS_LS_TRACE', None)
        return m.LSP(dest, work)
    finally:
        if old is None: os.environ.pop('ARGO_WORKFLOWS_LS_TRACE', None)
        else: os.environ['ARGO_WORKFLOWS_LS_TRACE'] = old


def work_records(client):
    rows = []
    for line in bytes(client.stderr_raw).splitlines():
        try: value = json.loads(line)
        except (ValueError, UnicodeDecodeError): continue
        if isinstance(value, dict) and value.get('phase') == 'work': rows.append(value)
    return rows


def wait_work(client, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = [r for r in work_records(client) if predicate(r)]
        if found: return found[-1]
        time.sleep(.002)
    return None


def await_ready(client):
    deadline = time.monotonic() + 10
    while b'"state":"Ready"' not in client.stderr_raw:
        if time.monotonic() >= deadline: raise TimeoutError('current index did not become Ready')
        time.sleep(.01)


def work_trace():
    snapshots, traced, samples = [], [], []
    for enabled in (False, True):
        dest, work = setup('trace-on' if enabled else 'trace-off')
        client = traced_client(dest, work, enabled)
        path = work / 'callers/caller.yaml'
        try:
            initial = client.open(path, m.CALLER); await_ready(client)
            changed = m.CALLER + '# an accepted, byte-distinct source revision\n'
            diagnostics = client.change(path, changed, 2)
            field = client.request('textDocument/completion', {'textDocument': {'uri': path.as_uri()},
                'position': m.pos(changed, '  templates:', offset=2)})
            queries = []
            with reads(work) as counter:
                for _ in range(3):
                    response = client.request('textDocument/completion', {'textDocument': {'uri': path.as_uri()},
                        'position': m.pos(changed, 'template: work', offset=len('template: wo'))})
                    queries.append({'id': response.get('id'), 'response': response, 'fileOpens': counter.take()})
            unfinished = changed.replace('  templates:\n', '  parall\n  templates:\n')
            client.change(path, unfinished, 3)
            recovery = client.request('textDocument/completion', {'textDocument': {'uri': path.as_uri()},
                'position': m.pos(unfinished, '  parall', offset=len('  parall'))})
            client.request('workspace/symbol', {'query': 'trace-wire-order-barrier'})
            # The source fixtures, expected candidate names and independent
            # inotify observations are fixed before any counter implementation.
            semantics = {'initial': initial, 'changed': diagnostics, 'fields': labels(field),
                'external': [labels(q['response']) for q in queries], 'recovered': labels(recovery)}
            snapshots.append(semantics)
            samples.append({'traceEnabled': enabled, 'fieldRequestId': field.get('id'),
                'recoveryRequestId': recovery.get('id'), 'externalQueries': queries})
        finally:
            client.close()
        records = work_records(client)
        (dest / 'work-records.json').write_text(json.dumps(records, indent=2) + '\n')
        if enabled: traced = records
        else: untraced = records
    by_id = {r.get('requestId'): r for r in traced if r.get('origin') == 'request' and r.get('event') == 'finish'}
    on = samples[1]
    field_work = by_id.get(on['fieldRequestId'], {}).get('work', {})
    recovery_work = by_id.get(on['recoveryRequestId'], {}).get('work', {})
    warm = [by_id.get(q['id'], {}).get('work', {}) for q in on['externalQueries'][1:]]
    checks = {
        'independent_candidates': all(s['initial'] == [] and s['changed'] == [] and
            'activeDeadlineSeconds' in s['fields'] and s['external'] == [['work']] * 3 and
            'parallelism' in s['recovered'] for s in snapshots),
        'trace_off_equivalent': snapshots[0] == snapshots[1] and untraced == [],
        'cold_work_visible': any(r.get('work', {}).get('schemaCompiles', 0) > 0 for r in traced) and
            any(r.get('work', {}).get('domainResources', 0) > 0 for r in traced) and
            any(r.get('work', {}).get('configDecodes', 0) > 0 for r in traced),
        'query_work_visible': field_work.get('schemaQueries', 0) > 0 and field_work.get('parseSyntheticCalls', 0) > 0 and
            recovery_work.get('parseRecoveryCalls', 0) > 0 and recovery_work.get('projectionCalls', 0) > 0,
        'acquisition_before_hit_visible': len(warm) == 2 and all(w.get('resultCacheHits', 0) == 1 and
            w.get('parseCalls', 0) > 0 and w.get('parseBytes', 0) > 0 and w.get('readFiles', 0) == 1 and
            w.get('readBytes', 0) > 0 for w in warm),
        # Frozen instrumentation-only 909d5b0 performs two source parses and
        # four total parses for each of these fixed warm requests. Reusing the
        # accepted open source must remove at least one source parse. The six
        # direct/synthetic query projections were not reduced by this reuse;
        # they and the independent selected-file read remain measured work.
        'warm_source_work_bounded': len(warm) == 2 and all(w.get('parseSourceCalls', 0) <= 1 and
            w.get('parseCalls', 0) <= 3 and w.get('projectionCalls', 0) <= 6 for w in warm),
        'independent_fresh_reads': all(q['fileOpens'] == {'selected': 1, 'unrelated': 0}
            for sample in samples for q in sample['externalQueries']),
        'correlated_lifecycle': len(by_id) >= 5 and all(r.get('sequence', 0) > 0 and
            isinstance(r.get('queueNanoseconds'), int) and r['queueNanoseconds'] >= 0 and
            r.get('releaseNanoseconds', -1) >= r.get('replyNanoseconds', -2) >= 0
            for r in by_id.values()),
    }
    m.result('work-trace-calibration', checks, {'semantics': snapshots, 'samples': samples,
        'requestWork': by_id, 'measurement': 'work performed, including acquisition before cache; times descriptive'})


def cancel_trace():
    dest, work = setup('cancel-trace'); path = work / 'callers/caller.yaml'
    client = traced_client(dest, work, True)
    old_id, new_id = 7001, 7002
    try:
        # A real cold query over a valid bounded buffer gives the client time
        # to observe dispatch. No artificial delay or counter-only task runs in
        # the service. The successor sees the small new accepted buffer.
        large = m.CALLER + '#' + 'x' * (4 * 1024 * 1024) + '\n'
        client.send('textDocument/didOpen', {'textDocument': {'uri': path.as_uri(), 'version': 1,
            'languageId': 'yaml', 'text': large}})
        params = {'textDocument': {'uri': path.as_uri()}, 'position': m.pos(m.CALLER, '  templates:', offset=2)}
        client.send('textDocument/completion', params, old_id)
        started = wait_work(client, lambda r: r.get('requestId') == old_id and r.get('event') == 'start', timeout=3)
        client.send('$/cancelRequest', {'id': old_id})
        client.send('textDocument/didChange', {'textDocument': {'uri': path.as_uri(), 'version': 2},
            'contentChanges': [{'text': m.CALLER}]})
        client.send('textDocument/completion', params, new_id)
        client.wait(lambda _: len([r for r in client.received if r.get('id') in (old_id, new_id)]) >= 2)
        replies = [r for r in client.received if r.get('id') in (old_id, new_id)]
        old = next(r for r in replies if r['id'] == old_id); new = next(r for r in replies if r['id'] == new_id)
        finished = wait_work(client, lambda r: r.get('requestId') == old_id and r.get('event') == 'finish', timeout=3)
        successor = wait_work(client, lambda r: r.get('requestId') == new_id and r.get('event') == 'finish', timeout=3)
        checks = {'worker_start_observed': started is not None,
            'one_terminal_each': sorted(r['id'] for r in replies) == [old_id, new_id],
            'current_successor': 'error' not in new and 'activeDeadlineSeconds' in labels(new),
            'cancel_reply_and_release': old.get('error', {}).get('code') == -32800 and finished is not None and
                finished.get('outcome') == 'cancelled' and
                finished.get('cancelToReleaseNanoseconds', -1) >= finished.get('cancelToReplyNanoseconds', -2) >= 0,
            'cancelled_work_retained': finished is not None and finished.get('work', {}).get('parseCalls', 0) > 0,
            'successor_slot_recovered': successor is not None and successor.get('outcome') == 'delivered' and
                successor.get('releaseNanoseconds', -1) >= successor.get('replyNanoseconds', -2) >= 0,
        }
        m.result('work-trace-cancellation', checks, {'responses': replies, 'start': started,
            'cancelled': finished, 'successor': successor, 'measurement': 'cancel-to-reply differs from cancel-to-worker-release'})
    finally:
        client.close()


for name, run in [("local", local), ("external", external), ("configuration", configuration),
                  ("work-trace", work_trace), ("cancel-trace", cancel_trace)]:
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
    "cases": list(args.cases), "measurement": "Linux inotify and opt-in actual work traces; no PID or new latency gate",
    "inputs": {str(p.relative_to(m.OUT)): m.sha(p) for p in m.OUT.rglob("*")
               if p.is_file() and "input" in p.parts},
})
sys.exit(0 if m.RESULTS and all(row["pass"] for row in m.RESULTS) else 1)

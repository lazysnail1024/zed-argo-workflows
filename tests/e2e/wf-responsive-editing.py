#!/usr/bin/env python3
"""Real stdio consumers for foreground isolation and current-input fences."""
import argparse
import contextlib
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import time
import traceback

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('binary', type=Path)
parser.add_argument('output', type=Path)
parser.add_argument('--cases', default='startup,churn,lanes,fences')
args = parser.parse_args()
HERE = Path(__file__).resolve().parent
OUT = args.output.resolve()
OUT.mkdir(parents=True, exist_ok=True)
spec = importlib.util.spec_from_file_location('wire', HERE / 'wf-lifecycle-gaps.py')
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
a.BINARY, a.OUT = args.binary.resolve(), OUT

SMALL = '''apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata: {name: current, namespace: argo}
spec:
  parallelism: 1
  entrypoint: work
  templates:
    - name: work
      container: {image: alpine}
'''
NOISE = 'apiVersion: v1\nkind: ConfigMap\nmetadata: {name: noise}\n'


def position(text, needle, offset=0):
    head = text[:text.index(needle) + offset]
    return {'line': head.count('\n'), 'character': len(head.rsplit('\n', 1)[-1].encode('utf-16-le')) // 2}


def params(path, text=SMALL):
    return {'textDocument': {'uri': path.as_uri()}, 'position': position(text, 'parallelism', 3)}


def large():
    return ('apiVersion: argoproj.io/v1alpha1\nkind: Workflow\n'
            'metadata: {name: many, namespace: argo}\nspec:\n  entrypoint: work0\n  templates:\n' +
            ''.join('    - name: work%d\n      container: {image: alpine}\n' % n for n in range(2500)))


@contextlib.contextmanager
def client(name, files):
    dest, work = a.setup(name, files)
    c = a.Client(dest, work)
    try:
        yield c, work
    finally:
        code, _ = c.close()
        assert code == 0, 'unclean server exit: %r' % code


def meaningful_hover(response):
    return 'error' not in response and 'parallelism' in json.dumps(response.get('result')).lower()


def startup():
    bad = SMALL.replace('parallelism', 'paralelism')
    files = {'z-current/current.yaml': bad}
    for n in range(6000):
        files['background/repo-%02d/file-%05d.yaml' % (n % 8, n)] = NOISE + '# ' + 'x' * 512 + '\n'
    with client('startup', files) as (c, work):
        path = work / 'z-current/current.yaml'
        started = time.monotonic()
        first = c.open(path, bad)
        elapsed = time.monotonic() - started
        completed = any('"state":"Ready"' in line.replace(' ', '') for line in bytes(c.stderrraw).decode().splitlines())
        point = position(bad, 'paralelism', 3)
        action = c.request('textDocument/codeAction', {'textDocument': {'uri': path.as_uri()},
                           'range': {'start': point, 'end': point}, 'context': {'diagnostics': []}})
        candidates = [x for x in action.get('result', []) if 'parallelism' in x.get('title', '')]
        resolved = c.request('codeAction/resolve', candidates[0]) if candidates else {}
        changes = resolved.get('result', {}).get('edit', {}).get('documentChanges', [])
        a.result('startup', {
            'current_diagnostic_before_full_discovery': not completed and any(d['code'] == 'SCHEMA_UNKNOWN_FIELD' for d in first['params']['diagnostics']),
            'current_diagnostic_under_two_seconds': elapsed < 2,
            'local_fix_available_during_discovery': bool(candidates),
            'local_fix_resolves_exact_open_version': len(changes) == 1 and changes[0]['textDocument'] == {'uri': path.as_uri(), 'version': 1},
            'fix_replaces_only_misspelled_key': len(changes) == 1 and len(changes[0]['edits']) == 1 and changes[0]['edits'][0]['newText'] == 'parallelism',
        }, {'firstDiagnosticSeconds': elapsed, 'backgroundFiles': 6000, 'action': action, 'resolved': resolved})


def churn():
    with client('churn', {'current.yaml': SMALL, 'noise.yaml': NOISE}) as (c, work):
        current, noise = work / 'current.yaml', work / 'noise.yaml'
        c.open(current, SMALL)
        c.open(noise, NOISE)
        c.index_ready(work)
        responses, milliseconds = [], []
        for n in range(40):
            started = time.monotonic()
            ident = 1000 + n
            c.batch([
                ('textDocument/hover', params(current), ident),
                ('textDocument/didChange', {'textDocument': {'uri': noise.as_uri(), 'version': n + 2},
                 'contentChanges': [{'text': NOISE + '# unrelated edit %d\n' % n}]}, None),
            ])
            responses.append(c.response(ident))
            milliseconds.append((time.monotonic() - started) * 1000)
        p95 = sorted(milliseconds)[37]
        a.result('churn', {
            'every_unrelated_edit_preserves_useful_hover': all(meaningful_hover(r) for r in responses),
            'foreground_p95_under_250_ms': p95 < 250,
        }, {'usefulResponses': sum(meaningful_hover(r) for r in responses), 'requests': 40,
            'p95Milliseconds': p95, 'medianMilliseconds': statistics.median(milliseconds), 'responses': responses})


def lanes():
    big = large()
    with client('lanes', {'current.yaml': SMALL, 'large.yaml': big}) as (c, work):
        current, heavy = work / 'current.yaml', work / 'large.yaml'
        c.open(current, SMALL)
        c.open(heavy, big)
        c.index_ready(work)
        reference = {'textDocument': {'uri': heavy.as_uri()}, 'position': position(big, 'name: work0', 8),
                     'context': {'includeDeclaration': True}}
        started = time.monotonic()
        c.batch([('textDocument/references', reference, 2000), ('textDocument/hover', params(current), 2001)])
        quick = c.response(2001)
        elapsed = time.monotonic() - started
        slow = c.response(2000)
        order = [r['message']['id'] for r in c.received if r['message'].get('id') in (2000, 2001)]
        # Saturate only the heavy lane. The next local query retains its own
        # admission slot even though several heavy requests must be refused.
        ids = list(range(2100, 2120))
        # Cancel in the same input burst: waiting for hover first can let all
        # heavy requests finish, leaving nothing eligible for cancellation.
        c.batch([('textDocument/references', reference, ident) for ident in ids] +
                [('textDocument/hover', params(current), 2200)] +
                [('$/cancelRequest', {'id': ident}, None) for ident in ids])
        admitted = c.response(2200)
        replies = [c.response(ident) for ident in ids]
        # A cancellation reply can precede the worker's exit. This heavy-lane
        # barrier completes only after that worker and its final result drain.
        barrier = c.request('textDocument/definition', {
            'textDocument': {'uri': current.as_uri()}, 'position': position(SMALL, 'entrypoint: work', 12)})
        # Compare the recovered query with a fresh process at the same final files.
        with client('lanes-cold', {'current.yaml': SMALL, 'large.yaml': big}) as (cold, cold_work):
            cold_current = cold_work / 'current.yaml'
            cold.open(cold_current, SMALL)
            cold.open(cold_work / 'large.yaml', big)
            cold.index_ready(cold_work)
            cold_result = cold.request('textDocument/definition', {
                'textDocument': {'uri': cold_current.as_uri()},
                'position': position(SMALL, 'entrypoint: work', 12)})
            recovered = json.dumps(barrier.get('result'), sort_keys=True).replace(work.as_uri(), 'ROOT')
            direct = json.dumps(cold_result.get('result'), sort_keys=True).replace(cold_work.as_uri(), 'ROOT')
        a.result('lanes', {
            'light_result_precedes_heavy_result': order == [2001, 2000],
            'light_query_has_useful_result': meaningful_hover(quick),
            'heavy_query_completes_correctly': 'error' not in slow and bool(slow.get('result')),
            'light_capacity_reserved_under_heavy_saturation': meaningful_hover(admitted),
            'heavy_queue_is_bounded': any(r.get('error', {}).get('message') == 'request queue is full' for r in replies),
            'cancellation_terminal_observed': any(r.get('error', {}).get('code') == -32800 for r in replies),
            'cancelled_state_equals_fresh': 'error' not in cold_result and recovered == direct,
            'heavy_lane_resumes_after_cancellation': 'error' not in barrier and bool(barrier.get('result')),
            'one_terminal_response_per_heavy_request': all(sum(r['message'].get('id') == ident for r in c.received) == 1 for ident in ids),
        }, {'lightMilliseconds': elapsed * 1000, 'order': order, 'light': quick, 'heavy': slow,
            'admittedDuringSaturation': admitted, 'heavyResponses': replies, 'afterCancellation': barrier})


def fences():
    big = large()
    with client('fences', {'large.yaml': big}) as (c, work):
        path = work / 'large.yaml'
        c.open(path, big)
        c.index_ready(work)
        symbol = {'textDocument': {'uri': path.as_uri()}}
        c.batch([
            ('textDocument/documentSymbol', symbol, 3000),
            ('textDocument/documentSymbol', symbol, 3001),
            ('textDocument/didChange', {'textDocument': {'uri': path.as_uri(), 'version': 2},
             'contentChanges': [{'text': SMALL}]}, None),
        ])
        changed = [c.response(i) for i in (3000, 3001)]
        fresh = c.request('textDocument/documentSymbol', symbol)
        c.change(path, big, 3)
        c.diagnostics(path, 3)
        c.index_ready(work)
        c.batch([
            ('textDocument/documentSymbol', symbol, 3100),
            ('textDocument/documentSymbol', symbol, 3101),
            ('workspace/didChangeWorkspaceFolders', {'event': {
                'removed': [{'uri': work.as_uri(), 'name': 'root'}],
                'added': [{'uri': work.as_uri(), 'name': 'root'}]}}, None),
        ])
        replaced = [c.response(i) for i in (3100, 3101)]
        a.result('fences', {
            'own_buffer_change_rejects_active_and_queued_results': all(r.get('error', {}).get('code') == -32801 for r in changed),
            'fresh_buffer_result_remains_available': [x['name'] for x in fresh.get('result', [])] == ['work'],
            'same_path_root_replacement_rejects_old_requests': all(r.get('error', {}).get('code') == -32801 for r in replaced),
        }, {'afterOwnChange': changed, 'fresh': fresh, 'afterRootReplacement': replaced})


available = {'startup': startup, 'churn': churn, 'lanes': lanes, 'fences': fences}
selected = args.cases.split(',')
assert selected and all(name in available for name in selected), selected
for name in selected:
    try:
        available[name]()
    except Exception:
        a.result(name, {'completed': False}, {'exception': traceback.format_exc()})
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
(OUT / 'manifest.json').write_text(json.dumps({'binarySha256': sha(a.BINARY), 'testSha256': sha(Path(__file__)),
    'wireSha256': sha(HERE / 'wf-lifecycle-gaps.py'), 'cases': selected}, indent=2) + '\n')
raise SystemExit(0 if len(a.RESULTS) == len(selected) and all(row['pass'] for row in a.RESULTS) else 1)

#!/usr/bin/env python3
"""Real stdio: resumable discovery, bounded retention, and dependency updates.

The larger count/padding options exercise cumulative input beyond the raw-input budget.
They do not increase the server's per-request deadline or provide test-only APIs.
"""
import argparse
import importlib.util
import json
import pathlib
import re
import shutil
import time
import traceback

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('binary', type=pathlib.Path)
parser.add_argument('output', type=pathlib.Path)
parser.add_argument('--count', type=int, default=4096)
parser.add_argument('--padding', type=int, default=128)
parser.add_argument('--resource-every', type=int, default=16)
parser.add_argument('--deadline', type=float, default=120)
parser.add_argument('--require-slow', action='store_true')
args = parser.parse_args()
assert args.count >= 128 and args.padding >= 0 and args.resource_every >= 0
spec = importlib.util.spec_from_file_location('wire', pathlib.Path(__file__).with_name('wf-audit-gaps.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.BINARY = args.binary.resolve()
out = args.output.resolve()
out.mkdir(parents=True, exist_ok=True)
root = out / 'input'
if root.exists():
    shutil.rmtree(root)
root.mkdir()
(root / '.argo-workflows-ls.yaml').write_text(m.CONFIG)
total = 0
ordinary = None
for n in range(args.count):
    resource = args.resource_every and n % args.resource_every == 0
    text = (m.BASE.replace('name: base,', 'name: unused-' + str(n) + ',') if resource else
            'apiVersion: v1\nkind: ConfigMap\nmetadata: {name: unused-' + str(n) + '}\n')
    text += '# ' + 'x' * args.padding + '\n'
    path = root / 'a-background' / ('repo-%02d' % (n % 8)) / ('file-%06d.yaml' % n)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    total += len(text.encode())
    if not resource:
        ordinary = path
caller = root / 'z-current' / 'caller.yaml'
target = root / 'zz-last' / 'base.yaml'
caller.parent.mkdir()
target.parent.mkdir()
text = m.CALLER.replace('  entrypoint:', '  paralelism: 1\n  entrypoint:')
caller.write_text(text)
target.write_text(m.BASE)
total += len(text.encode()) + len(m.BASE.encode())
checks, observed = {}, {'totalInputBytes': total}
client = None


def states():
    result = []
    for line in bytes(client.stderr_raw).splitlines():
        try:
            row = json.loads(line)
            if row.get('phase') == 'index' and row.get('root') == str(root):
                result.append(row)
        except (ValueError, UnicodeError):
            pass
    return result


def wait(predicate, label, timeout=None):
    deadline = time.monotonic() + (timeout or args.deadline)
    while time.monotonic() < deadline:
        if client.p.poll() is not None:
            raise RuntimeError('server exited: ' + str(client.p.returncode))
        value = predicate()
        if value:
            return value
        time.sleep(.02)
    raise TimeoutError(label)


def publications(marker=0):
    return [row['params'] for row in client.received[marker:]
            if row.get('method') == 'textDocument/publishDiagnostics'
            and row['params']['uri'] == caller.as_uri()]


def codes(row):
    return {diagnostic['code'] for diagnostic in row['diagnostics']}


def watch(path, kind):
    client.send('workspace/didChangeWatchedFiles', {'changes': [{'uri': path.as_uri(), 'type': kind}]})


def definition():
    return client.request('textDocument/definition', {
        'textDocument': {'uri': caller.as_uri()}, 'position': m.pos(text, 'template: work', offset=11)})


try:
    began = time.monotonic()
    client = m.LSP(out, root)
    first_at = time.monotonic()
    first = client.open(caller, text)
    observed['firstPublicationSeconds'] = time.monotonic() - first_at
    checks['current_workflow_local_diagnostic_before_discovery'] = any(d['code'] == 'SCHEMA_UNKNOWN_FIELD' for d in first)
    checks['first_publication_under_two_seconds'] = observed['firstPublicationSeconds'] < 2
    version, next_change = 1, time.monotonic()
    until = time.monotonic() + args.deadline
    while not any(row['state'] == 'Ready' for row in states()):
        assert time.monotonic() < until, 'discovery did not become Ready while input kept changing'
        if time.monotonic() >= next_change:
            version += 1
            client.send('textDocument/didChange', {'textDocument': {'uri': caller.as_uri(), 'version': version},
                        'contentChanges': [{'text': text + '# current revision ' + str(version) + '\n'}]})
            next_change = time.monotonic() + .15
        time.sleep(.01)
    first_ready = next(row for row in states() if row['state'] == 'Ready')
    observed['readySeconds'] = time.monotonic() - began
    observed['firstReady'], observed['editVersionsDuringDiscovery'] = first_ready, version
    checks['readiness_within_budget'] = observed['readySeconds'] <= args.deadline
    checks['discovery_finishes_without_worker_reset'] = first_ready['files'] == args.count + 2 and first_ready.get('pass') == 1
    checks['retained_text_bounded'] = first_ready['retainedTextBytes'] <= 128 * 1024 * 1024
    checks['complete_input_read'] = first_ready['readBytes'] >= total - len(text.encode())
    checks['no_missing_target_claim_during_discovery'] = all('WF_TEMPLATE_REF_UNRESOLVED' not in codes(row) for row in publications())
    if total > 128 * 1024 * 1024:
        checks['cumulative_input_exceeds_retained_text'] = first_ready['retainedTextBytes'] < total
    if args.require_slow:
        checks['discovery_exceeds_request_deadline'] = observed['readySeconds'] > 5 and version >= 20
    wait(lambda: any(row.get('version') == version and codes(row) == {'SCHEMA_UNKNOWN_FIELD'} for row in publications()), 'latest full diagnostics')
    response = wait(lambda: (r if 'result' in (r := definition()) and r['result'] else None), 'definition after complete discovery')
    checks['indexed_location_maps_to_source'] = response['result'] == [{'uri': target.as_uri(), 'range': {
        'start': m.pos(m.BASE, 'name: work', offset=6), 'end': m.pos(m.BASE, 'name: work', offset=10)}}]

    before = states()[-1]
    previous_states = len(states())
    client.send('textDocument/didSave', {'textDocument': {'uri': caller.as_uri()}})
    probe = ordinary or root / 'a-background/repo-00/file-000000.yaml'
    watch(probe, 2)
    watch(probe, 2)
    after = wait(lambda: states()[-1] if len(states()) > previous_states and states()[-1]['state'] == 'Ready' else None, 'unchanged event finishes')
    observed['unchangedEventDelta'] = {key: after[key] - before[key] for key in ('reads', 'parses')}
    checks['unchanged_events_do_not_reparse_workspace'] = after['parses'] == before['parses']
    checks['unchanged_events_read_only_related_inputs'] = after['reads'] - before['reads'] <= 4

    marker = len(client.received)
    changed = m.BASE.replace('      container:', '      inputs: {parameters: [{name: required}]}\n      container:')
    target.write_text(changed)
    watch(target, 2)
    wait(lambda: any('WF_ARGUMENT_MISSING' in codes(row) and row.get('version') == version for row in publications(marker)), 'changed template signature reaches caller')
    checks['signature_change_invalidates_caller'] = True

    marker = len(client.received)
    target.write_text(m.BASE)  # Deliberately omit the watcher.
    observed['missedWatchQuery'] = definition()
    wait(lambda: any(codes(row) == {'SCHEMA_UNKNOWN_FIELD'} and row.get('version') == version for row in publications(marker)), 'query observation still publishes repaired diagnostics')
    checks['query_observation_cannot_hide_diagnostic_update'] = True

    marker = len(client.received)
    target.unlink()
    watch(target, 3)
    wait(lambda: any('WF_TEMPLATE_REF_UNRESOLVED' in codes(row) for row in publications(marker)), 'known deletion')
    marker = len(client.received)
    unknown = root / 'new-unwatched-directory' / 'base.yaml'
    unknown.parent.mkdir()
    unknown.write_text(m.BASE)  # Unknown URI: only natural discovery may find it.
    reconciliation_started = time.monotonic()
    wait(lambda: any(row['state'] == 'Ready' and row['pass'] > first_ready['pass'] for row in states()), 'natural reconciliation completes its new pass', args.deadline)
    # The discovery itself has completed. An absent repaired publication is a
    # semantic failure, not a harness crash or a reason to hide the final index.
    try:
        repaired = wait(lambda: any(codes(row) == {'SCHEMA_UNKNOWN_FIELD'} for row in publications(marker)), 'complete reconciliation republishes repaired diagnostics', 3)
    except TimeoutError:
        repaired = False
    resolved = wait(lambda: (r if 'result' in (r := definition()) and len(r['result']) == 1 and r['result'][0]['uri'] == unknown.as_uri() else None), 'newly discovered URI has an exact definition')
    observed['unwatchedDefinition'] = resolved
    observed['unwatchedDiscoverySeconds'] = time.monotonic() - reconciliation_started
    checks['unknown_unwatched_uri_converges'] = repaired is True
    observed['finalState'] = states()[-1]
    checks['second_discovery_pass_is_complete'] = observed['finalState']['state'] == 'Ready' and observed['finalState']['pass'] > first_ready['pass'] and observed['finalState']['files'] == args.count + 2
    checks['reconciliation_reuses_unchanged_summaries'] = observed['finalState']['parses'] - after['parses'] <= 4
except Exception:
    checks['completed'] = False
    observed['exception'] = traceback.format_exc()
finally:
    if client is not None:
        try:
            status = pathlib.Path('/proc') / str(client.p.pid) / 'status'
            if status.exists():
                raw_status = status.read_text()
                (out / 'server-memory.status').write_text(raw_status)
                peak = re.search(r'^VmHWM:\s+(\d+) kB', raw_status, re.MULTILINE)
                if peak:
                    observed['peakRssBytes'] = int(peak[1]) * 1024
                    checks['peak_rss_under_512_mib'] = observed['peakRssBytes'] <= 512 * 1024 * 1024
            client.close()
            checks['clean_shutdown'] = True
        except Exception:
            checks['clean_shutdown'] = False
            observed['shutdownException'] = traceback.format_exc()
    result = {'command': __import__('sys').argv, 'checks': checks, 'observed': observed,
              'binarySha256': m.sha(args.binary), 'testSha256': m.sha(pathlib.Path(__file__)),
              'helperSha256': m.sha(pathlib.Path(__file__).with_name('wf-audit-gaps.py')),
              'pass': bool(checks) and all(value is True for value in checks.values())}
    (out / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2), flush=True)
raise SystemExit(0 if result['pass'] else 1)

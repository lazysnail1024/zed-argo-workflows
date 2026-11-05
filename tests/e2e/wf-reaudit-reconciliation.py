#!/usr/bin/env python3
"""Missed watcher recovery must survive foreground queries and preserve idle edits."""
import importlib.util
import pathlib
import sys
import time
import traceback

spec = importlib.util.spec_from_file_location('wire', pathlib.Path(__file__).with_name('wf-audit-gaps.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def inventory(diagnostics):
    return [(d.get('code'), d.get('severity'), d.get('range')) for d in diagnostics]


MISSING = [('WF_TEMPLATE_REF_UNRESOLVED', 1, {
    'start': m.pos(m.CALLER, 'name: base', offset=6),
    'end': m.pos(m.CALLER, 'name: base', offset=10),
})]


def publications(case):
    return [message['params'] for message in case['client'].received[case['marker']:]
            if message.get('method') == 'textDocument/publishDiagnostics'
            and message['params']['uri'] == case['file'].as_uri()]


clients, cases = [], []
try:
    # Independent clients keep a missed watcher in one root from being repaired
    # accidentally by another test's didOpen/change notification. Both directions
    # and their no-query controls observe the real, unchanged 30-second timer.
    for mode in ('create', 'delete'):
        for query in (True, False):
            name = mode + ('-after-query' if query else '-control')
            files = {'caller.yaml': m.CALLER}
            if mode == 'delete':
                files['base.yaml'] = m.BASE
            dest, work = m.setup(name, files)
            client = m.LSP(dest, work)
            clients.append(client)
            file = work / 'caller.yaml'
            before = client.open(file, m.CALLER)
            cases.append({'name': name, 'mode': mode, 'query': query,
                          'client': client, 'file': file, 'before': before})

    idle_text = m.REN_DAG.replace('  entrypoint:', '  paralelism: 1\n  entrypoint:')
    dest, work = m.setup('unchanged-idle-action', {'case.yaml': idle_text})
    idle = m.LSP(dest, work)
    clients.append(idle)
    idle_file = work / 'case.yaml'
    idle_ds = idle.open(idle_file, idle_text)
    p = m.pos(idle_text, 'paralelism')
    actions = idle.request('textDocument/codeAction', {
        'textDocument': {'uri': idle_file.as_uri()},
        'range': {'start': p, 'end': p}, 'context': {'diagnostics': idle_ds},
    })
    choices = [action for action in actions.get('result', []) if 'parallelism' in action.get('title', '')]

    scopes = m.CONFIG + 'scopes:\n  - name: original\n    include: ["alpha/**"]\n  - name: stable\n    include: ["beta/**"]\n'
    dest, work = m.setup('scope-rebinding', {'alpha/a.yaml': m.REN_DAG, 'beta/b.yaml': m.REN_DAG}, scopes)
    rebound = m.LSP(dest, work)
    clients.append(rebound)
    scoped_files = [work / 'alpha/a.yaml', work / 'beta/b.yaml']
    original_scopes = [rebound.open(path, m.REN_DAG) for path in scoped_files]
    config = work / '.argo-workflows-ls.yaml'
    config.write_bytes(scopes.replace('name: original', 'name: renamed').encode('utf-8'))
    marker = len(rebound.received)
    rebound.send('workspace/didChangeWatchedFiles', {'changes': [{'uri': config.as_uri(), 'type': 2}]})
    rebound_events = {}
    until = time.monotonic() + 20
    while time.monotonic() < until and len(rebound_events) != len(scoped_files):
        for message in rebound.received[marker:]:
            if message.get('method') == 'textDocument/publishDiagnostics':
                rebound_events[message['params']['uri']] = message['params']
        time.sleep(0.01)
    if len(rebound_events) != len(scoped_files):
        raise TimeoutError('scope rebind did not publish both current documents')
    rebound_marker = len(rebound.received)

    changed_at = time.monotonic()
    for case in cases:
        client, mode = case['client'], case['mode']
        case['marker'] = len(client.received)
        dependency = client.work / 'base.yaml'
        if mode == 'create':
            dependency.write_bytes(m.BASE.encode('utf-8'))
        else:
            dependency.unlink()
        if case['query']:
            case['definition'] = client.request('textDocument/definition', {
                'textDocument': {'uri': case['file'].as_uri()},
                'position': m.pos(m.CALLER, 'name: base', offset=7),
            })

    # Observe the real maintenance period, including an unchanged workspace.
    # No publication is a normal semantic failure, not a harness timeout or a
    # crash that could incorrectly count as mutation detection.
    deadline = changed_at + 37
    while time.monotonic() < deadline:
        if any(client.p.poll() is not None for client in clients):
            raise RuntimeError('server exited during reconciliation observation')
        if time.monotonic() >= changed_at + 32 and all(publications(case) for case in cases):
            break
        time.sleep(0.05)

    for case in cases:
        events = publications(case)
        expected_before = MISSING if case['mode'] == 'create' else []
        expected_after = [] if case['mode'] == 'create' else MISSING
        checks = {
            'initial_diagnostic_inventory': inventory(case['before']) == expected_before,
            'fresh_publication': bool(events),
            'current_diagnostic_inventory': bool(events) and inventory(events[-1]['diagnostics']) == expected_after,
            'current_document_version': bool(events) and all(event.get('version') == 1 for event in events),
        }
        if case['query']:
            expected_definition = [] if case['mode'] == 'delete' else [{
                'uri': (case['client'].work / 'base.yaml').as_uri(),
                'range': {'start': {'line': 2, 'character': 17}, 'end': {'line': 2, 'character': 21}},
            }]
            response = case['definition']
            checks['query_observes_disk_change'] = 'error' not in response and response.get('result') == expected_definition
        m.result(case['name'], checks, {
            'before': case['before'], 'definition': case.get('definition'),
            'newPublications': events, 'observationSeconds': time.monotonic() - changed_at,
        })

    resolved = idle.request('codeAction/resolve', choices[0]) if len(choices) == 1 else {}
    edit = resolved.get('result', {}).get('edit')
    changed = m.apply(idle_text, edit, idle_file.as_uri(), 1) if edit else None
    after = idle.change(idle_file, changed, 2) if changed is not None else None
    m.result('unchanged-idle-action', {
        'offered_one_fix': len(choices) == 1,
        'still_resolves_after_reconciliation': bool(edit) and 'error' not in resolved,
        'exact_edit_bytes': changed == idle_text.replace('paralelism:', 'parallelism:'),
        'applied_fix_clears_diagnostics': after == [],
    }, {'actions': actions, 'resolved': resolved, 'after': after})
    extra = [message['params'] for message in rebound.received[rebound_marker:]
             if message.get('method') == 'textDocument/publishDiagnostics']
    m.result('scope-rebinding', {
        'original_scopes_clean': original_scopes == [[], []],
        'rebound_scopes_clean': all(event['diagnostics'] == [] and event.get('version') == 1
                                    for event in rebound_events.values()),
        'obsolete_scope_not_reconciled': extra == [],
    }, {'reboundPublications': rebound_events, 'extraPublications': extra})
except Exception as error:
    m.result('runner-error', {'completed': False}, {'error': repr(error), 'traceback': traceback.format_exc()})
finally:
    for client in clients:
        client.close()
    m.save(m.OUT / 'manifest.json', {
        'command': sys.argv, 'binarySha256': m.sha(m.BINARY),
        'testSha256': m.sha(pathlib.Path(__file__)), 'expectedCases': 6,
        'dependencyWatcherNotifications': 0, 'scopeConfigWatcherNotifications': 1,
        'observationLimitSeconds': 37,
    })
sys.exit(0 if len(m.RESULTS) == 6 and all(row['pass'] for row in m.RESULTS) else 1)

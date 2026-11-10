#!/usr/bin/env python3
"""Independent stdio guards for scope isolation and changing candidate sets."""
import importlib.util
import pathlib
import sys
import time
import traceback

spec = importlib.util.spec_from_file_location('wire', pathlib.Path(__file__).with_name('wf-audit-gaps.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def eventually(action, wanted, label):
    deadline = time.monotonic() + 10
    last = None
    while time.monotonic() < deadline:
        last = action()
        if wanted(last):
            return last
        time.sleep(.02)
    raise AssertionError({'case': label, 'last': last})


def query(client, path, method, text, needle, offset=0):
    return client.request('textDocument/' + method, {
        'textDocument': {'uri': path.as_uri()}, 'position': m.pos(text, needle, offset=offset),
        'context': {'includeDeclaration': True}})


def scope_isolation():
    config = m.CONFIG + 'scopes:\n  - name: damaged\n    include: ["a/**"]\n  - name: healthy\n    include: ["b/**"]\n  - name: missing\n    include: ["c/**"]\n'
    broken = 'apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: {name: damaged, namespace: argo}\nspec: [\n'
    dest, work = m.setup('scope-local-failure', {'a/bad.yaml': broken, 'b/caller.yaml': m.CALLER,
        'b/base.yaml': m.BASE, 'c/caller.yaml': m.CALLER}, config)
    client = m.LSP(dest, work)
    try:
        healthy = work / 'b/caller.yaml'
        missing = work / 'c/caller.yaml'
        healthy_diagnostics = client.open(healthy, m.CALLER)
        missing_diagnostics = client.open(missing, m.CALLER)
        definition = query(client, healthy, 'definition', m.CALLER, 'template: work', 11)
        rename = query(client, healthy, 'prepareRename', m.CALLER, 'entrypoint: main', 14)
        m.result('scope-local-failure', {
            'healthy_scope_has_complete_diagnostics': healthy_diagnostics == [],
            'independent_missing_scope_proves_absence': [d['code'] for d in missing_diagnostics] == ['WF_TEMPLATE_REF_UNRESOLVED'] and missing_diagnostics[0]['severity'] == 1,
            'healthy_scope_definition': 'error' not in definition and len(definition.get('result', [])) == 1 and definition['result'][0]['uri'] == (work / 'b/base.yaml').as_uri(),
            'healthy_scope_edit_proof': 'error' not in rename and rename.get('result') is not None,
        }, {'healthy': healthy_diagnostics, 'missing': missing_diagnostics, 'definition': definition, 'prepareRename': rename})
    finally:
        client.close()


def candidate_changes():
    dest, work = m.setup('candidate-set-changes', {'caller.yaml': m.CALLER, 'base.yaml': m.BASE})
    client = m.LSP(dest, work)
    path, duplicate = work / 'caller.yaml', work / 'duplicate.yaml'
    definition = lambda: query(client, path, 'definition', m.CALLER, 'template: work', 11)
    try:
        initial = client.open(path, m.CALLER)
        duplicate.write_text('# distinct source location\n' + m.BASE)
        client.send('workspace/didChangeWatchedFiles', {'changes': [{'uri': duplicate.as_uri(), 'type': 1}]})
        ambiguous = eventually(definition, lambda r: 'error' not in r and len(r.get('result', [])) == 2, 'two known candidates')
        references = query(client, path, 'references', m.CALLER, 'template: work', 11)
        duplicate.unlink()
        client.send('workspace/didChangeWatchedFiles', {'changes': [{'uri': duplicate.as_uri(), 'type': 3}]})
        immediate = definition()
        resolved = eventually(definition, lambda r: 'error' not in r and len(r.get('result', [])) == 1, 'candidate deletion')
        m.result('candidate-set-changes', {
            'initial_complete': initial == [],
            'duplicates_kept_as_distinct_locations': {r['uri'] for r in ambiguous['result']} == {(work / 'base.yaml').as_uri(), duplicate.as_uri()},
            'ambiguous_reference_not_uniquely_bound': references.get('result') == [] and 'error' not in references,
            'delete_immediately_withdraws_old_candidate': immediate.get('error', {}).get('code') == -32801 or all(r['uri'] != duplicate.as_uri() for r in immediate.get('result', [])),
            'remaining_candidate_recovers': resolved['result'][0]['uri'] == (work / 'base.yaml').as_uri(),
        }, {'ambiguous': ambiguous, 'references': references, 'immediate': immediate, 'resolved': resolved})
    finally:
        client.close()


def summary_candidates():
    caller = m.CALLER.replace('name: base,', 'name: ba,')
    dest, work = m.setup('summary-candidate-generation', {'caller.yaml': caller, 'base.yaml': m.BASE})
    client = m.LSP(dest, work)
    path, added = work / 'caller.yaml', work / 'added.yaml'
    complete = lambda: query(client, path, 'completion', caller, 'name: ba,', len('name: ba'))
    labels = lambda response: sorted(row['label'] for row in response.get('result', []))
    try:
        client.open(path, caller)
        first, repeated = complete(), complete()
        added.write_text(m.BASE.replace('name: base,', 'name: basic,'))
        client.send('workspace/didChangeWatchedFiles', {'changes': [{'uri': added.as_uri(), 'type': 1}]})
        eventually(lambda: client.request('workspace/symbol', {'query': 'work'}),
                   lambda response: any(row['location']['uri'] == added.as_uri() for row in response.get('result', [])),
                   'new resource indexed before cached completion')
        updated = complete()
        m.result('summary-candidate-generation', {
            'initial_names': labels(first) == ['base'],
            'warm_names': labels(repeated) == ['base'],
            'new_summary_invalidates_cached_candidates': labels(updated) == ['base', 'basic'],
            'no_protocol_errors': all('error' not in response for response in (first, repeated, updated)),
        }, {'first': first, 'repeated': repeated, 'updated': updated})
    finally:
        client.close()


def publication_order():
    dest, work = m.setup('reopen-publication-order', {'caller.yaml': m.CALLER})
    client = m.LSP(dest, work)
    path = work / 'caller.yaml'
    histories = []
    checks = {}
    try:
        client.open(path, m.CALLER)
        for attempt in range(5):
            marker = len(client.received)
            client.send('textDocument/didClose', {'textDocument': {'uri': path.as_uri()}})
            client.send('textDocument/didOpen', {'textDocument': {'uri': path.as_uri(), 'version': 1, 'languageId': 'yaml', 'text': m.CALLER}})
            def events():
                return [row['params'] for row in client.received[marker:] if row.get('method') == 'textDocument/publishDiagnostics' and row['params']['uri'] == path.as_uri()]
            def domain(row):
                return any(item['code'] == 'WF_TEMPLATE_REF_UNRESOLVED' for item in row['diagnostics'])
            eventually(events, lambda rows: any(domain(row) for row in rows), 'reopen complete diagnostic')
            # Drain the ordinary request pipeline and then observe delayed
            # background output; a historical diagnostic alone is insufficient.
            client.request('textDocument/hover', {'textDocument': {'uri': path.as_uri()}, 'position': m.pos(m.CALLER, 'kind: Workflow', 0, 7)})
            time.sleep(.3)
            rows = events()
            first = next(n for n, row in enumerate(rows) if domain(row))
            checks['reopen_%d_final_domain_diagnostic' % attempt] = domain(rows[-1])
            checks['reopen_%d_no_late_local_clear' % attempt] = all(domain(row) for row in rows[first:])
            histories.append(rows)
        m.result('reopen-publication-order', checks, {'publications': histories})
    finally:
        client.close()


for name, action in [('scope-local-failure', scope_isolation), ('candidate-set-changes', candidate_changes), ('summary-candidate-generation', summary_candidates), ('reopen-publication-order', publication_order)]:
    try:
        action()
    except Exception:
        m.result(name, {'completed': False}, {'error': traceback.format_exc()})
m.save(m.OUT / 'manifest.json', {'command': sys.argv, 'binarySha256': m.sha(m.BINARY),
       'testSha256': m.sha(pathlib.Path(__file__)), 'expectedCases': 4,
       'helperSha256': m.sha(pathlib.Path(__file__).with_name('wf-audit-gaps.py'))})
raise SystemExit(0 if len(m.RESULTS) == 4 and all(row['pass'] for row in m.RESULTS) else 1)

#!/usr/bin/env python3
"""Real LSP action lifetimes for configured and editor-only document scopes."""
import importlib.util
import pathlib
import sys
import traceback

if len(sys.argv) not in (3, 5) or (len(sys.argv) == 5 and sys.argv[3] != '--cases'):
    print('usage: wf-reaudit-actions.py NATIVE_BINARY OUTPUT_DIRECTORY [--cases CASE,...]', file=sys.stderr)
    raise SystemExit(2)

HELPER = pathlib.Path(__file__).with_name('wf-audit-gaps.py')
spec = importlib.util.spec_from_file_location('wire', HELPER)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

VALID = '''apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata: {name: action-case, namespace: argo}
spec:
  entrypoint: main
  parallelism: 1
  templates:
    - name: main
      container: {image: alpine}
'''
BROKEN = VALID.replace('parallelism:', 'paralelism:')
PEER = VALID.replace('name: action-case', 'name: peer')
SCOPES = m.CONFIG + '''scopes:
  - name: alpha
    include: ["workflows/**"]
  - name: beta
    include: ["other/**"]
'''
CONFIG = SCOPES + 'exclude: ["generated/**"]\n'


def offer(client, path, text, diagnostics):
    point = m.pos(text, 'paralelism')
    response = client.request('textDocument/codeAction', {
        'textDocument': {'uri': path.as_uri()},
        'range': {'start': point, 'end': point},
        'context': {'diagnostics': diagnostics},
    })
    actions = [a for a in response.get('result', [])
               if a.get('title') == 'Replace field with parallelism']
    return response, actions[0] if len(actions) == 1 else None


def exact_edit(response, path, text, version):
    point = m.pos(text, 'paralelism')
    expected = {'documentChanges': [{
        'textDocument': {'uri': path.as_uri(), 'version': version},
        'edits': [{'range': {'start': point, 'end': point | {
            'character': point['character'] + len('paralelism')}},
            'newText': 'parallelism'}],
    }]}
    edit = response.get('result', {}).get('edit')
    return 'error' not in response and edit == expected and m.apply(
        text, edit, path.as_uri(), version) == text.replace('paralelism:', 'parallelism:', 1)


def refused(response):
    error = response.get('error', {})
    return error.get('code') == -32801 and bool(error.get('message')) and 'result' not in response


def clean(diagnostics):
    return not any(d['severity'] == 1 or d['code'].startswith('WF_ANALYSIS')
                   or d['code'] == 'SCHEMA_UNKNOWN_FIELD' for d in diagnostics)


def lifecycle(name, relative):
    dest, work = m.setup(name, {relative: BROKEN, 'other/peer.yaml': PEER}, CONFIG)
    path, peer = work / relative, work / 'other/peer.yaml'
    client = m.LSP(dest, work)
    checks, responses = {}, {}
    try:
        client.open(peer, PEER)
        diagnostics = client.open(path, BROKEN)
        checks['originalDiagnostic'] = any(d['code'] == 'SCHEMA_UNKNOWN_FIELD' for d in diagnostics)
        responses['offered'], action = offer(client, path, BROKEN, diagnostics)
        checks['actionOffered'] = action is not None
        if action is None:
            return checks, responses
        responses['immediate'] = client.request('codeAction/resolve', action)
        checks['immediateExactEdit'] = exact_edit(responses['immediate'], path, BROKEN, 1)

        client.change(peer, PEER + '# unrelated scope edit\n', 2)
        responses['unrelatedScope'] = client.request('codeAction/resolve', action)
        checks['unrelatedScopeStillValid'] = exact_edit(responses['unrelatedScope'], path, BROKEN, 1)

        # Apply the independently expected text through the client, then require
        # the original warning to disappear. The server never writes source files.
        repaired = client.change(path, VALID, 2)
        checks['appliedDiagnosticsClean'] = clean(repaired)
        draft = BROKEN + '# current draft\n'
        diagnostics = client.change(path, draft, 3)
        responses['freshOffer'], current = offer(client, path, draft, diagnostics)
        checks['freshActionOffered'] = current is not None
        if current is None:
            return checks, responses
        responses['freshResolve'] = client.request('codeAction/resolve', current)
        checks['currentVersionExactEdit'] = exact_edit(responses['freshResolve'], path, draft, 3)
        client.change(path, VALID + '# user changed the document\n', 4)
        responses['changedDocument'] = client.request('codeAction/resolve', current)
        checks['changedDocumentRefused'] = refused(responses['changedDocument'])

        diagnostics = client.change(path, BROKEN, 5)
        responses['targetOffer'], current = offer(client, path, BROKEN, diagnostics)
        checks['targetActionOffered'] = current is not None
        if current is None:
            return checks, responses
        client.send('workspace/didChangeConfiguration', {'settings': {'targetVersion': '3.5.0'}})
        experimental = client.diagnostics(path.as_uri(), 5)
        checks['supportedTargetClean'] = all(d['code'] != 'WF_EXPERIMENTAL_TARGET' for d in experimental)
        responses['changedTarget'] = client.request('codeAction/resolve', current)
        checks['changedTargetRefused'] = refused(responses['changedTarget'])
        responses['experimentalOffer'], _ = offer(client, path, BROKEN, experimental)
        checks['olderTargetEditsEnabled'] = bool(responses['experimentalOffer'].get('result'))

        client.send('workspace/didChangeConfiguration', {'settings': {'targetVersion': ''}})
        diagnostics = client.diagnostics(path.as_uri(), 5)
        responses['restoredOffer'], current = offer(client, path, BROKEN, diagnostics)
        checks['restoredActionOffered'] = current is not None
        if current is None:
            return checks, responses
        responses['restoredResolve'] = client.request('codeAction/resolve', current)
        checks['restoredTargetExactEdit'] = exact_edit(responses['restoredResolve'], path, BROKEN, 5)
        client.send('textDocument/didClose', {'textDocument': {'uri': path.as_uri()}})
        responses['closed'] = client.request('codeAction/resolve', current)
        checks['closedDocumentRefused'] = refused(responses['closed'])
        diagnostics = client.open(path, BROKEN, 1)
        responses['oldTokenAfterReopen'] = client.request('codeAction/resolve', current)
        checks['reopenDoesNotReviveToken'] = refused(responses['oldTokenAfterReopen'])
        responses['reopenedOffer'], reopened = offer(client, path, BROKEN, diagnostics)
        checks['reopenedActionOffered'] = reopened is not None
        if reopened is not None:
            responses['reopenedResolve'] = client.request('codeAction/resolve', reopened)
            checks['reopenedExactEdit'] = exact_edit(responses['reopenedResolve'], path, BROKEN, 1)
        checks['diskUnchanged'] = path.read_bytes() == BROKEN.encode() and peer.read_bytes() == PEER.encode()
        return checks, responses
    finally:
        client.close()


def exclusion_change():
    dest, work = m.setup('exclusion-change', {'generated/a.yaml': BROKEN}, CONFIG)
    path, config = work / 'generated/a.yaml', work / '.argo-workflows-ls.yaml'
    client = m.LSP(dest, work)
    checks, responses = {}, {}
    try:
        diagnostics = client.open(path, BROKEN)
        responses['offered'], action = offer(client, path, BROKEN, diagnostics)
        checks['actionOffered'] = action is not None
        if action is None:
            return checks, responses
        responses['immediate'] = client.request('codeAction/resolve', action)
        checks['immediateExactEdit'] = exact_edit(responses['immediate'], path, BROKEN, 1)
        config.write_bytes(SCOPES.encode())
        client.send('workspace/didChangeWatchedFiles', {'changes': [{'uri': config.as_uri(), 'type': 2}]})
        diagnostics = client.diagnostics(path.as_uri(), 1)
        responses['changedScope'] = client.request('codeAction/resolve', action)
        checks['changedExclusionRefused'] = refused(responses['changedScope'])
        responses['freshOffer'], current = offer(client, path, BROKEN, diagnostics)
        checks['freshActionOffered'] = current is not None
        if current is not None:
            responses['freshResolve'] = client.request('codeAction/resolve', current)
            checks['newScopeExactEdit'] = exact_edit(responses['freshResolve'], path, BROKEN, 1)
        return checks, responses
    finally:
        client.close()


def changed_dependency():
    dest, work = m.setup('changed-dependency', {
        'workflows/a.yaml': BROKEN, 'workflows/dependency.yaml': PEER,
    }, CONFIG)
    path, dependency = work / 'workflows/a.yaml', work / 'workflows/dependency.yaml'
    client = m.LSP(dest, work)
    checks, responses = {}, {}
    try:
        client.open(dependency, PEER)
        diagnostics = client.open(path, BROKEN)
        responses['offered'], action = offer(client, path, BROKEN, diagnostics)
        checks['actionOffered'] = action is not None
        if action is None:
            return checks, responses
        responses['immediate'] = client.request('codeAction/resolve', action)
        checks['immediateExactEdit'] = exact_edit(responses['immediate'], path, BROKEN, 1)
        client.change(dependency, PEER + '# same scope input changed\n', 2)
        responses['changedDependency'] = client.request('codeAction/resolve', action)
        checks['changedDependencyRefused'] = refused(responses['changedDependency'])
        responses['freshOffer'], current = offer(client, path, BROKEN, diagnostics)
        checks['freshActionOffered'] = current is not None
        if current is not None:
            responses['freshResolve'] = client.request('codeAction/resolve', current)
            checks['freshDependencyExactEdit'] = exact_edit(responses['freshResolve'], path, BROKEN, 1)
        return checks, responses
    finally:
        client.close()


def isolated_reference():
    caller = m.CALLER.replace('name: base, template: work', 'name: hidden, template: work')
    hidden = m.BASE.replace('name: base,', 'name: hidden,').replace('  entrypoint:', '  paralelism: 1\n  entrypoint:')
    dest, work = m.setup('isolated-reference', {
        'workflows/caller.yaml': caller, 'generated/library.yaml': hidden,
    }, CONFIG)
    library, path = work / 'generated/library.yaml', work / 'workflows/caller.yaml'
    client = m.LSP(dest, work)
    checks, responses = {}, {}
    try:
        diagnostics = client.open(library, hidden)
        caller_diagnostics = client.open(path, caller)
        checks['callerStillUnresolved'] = any(d['code'] == 'WF_TEMPLATE_REF_UNRESOLVED' for d in caller_diagnostics)
        params = {'textDocument': {'uri': path.as_uri()}, 'position': m.pos(caller, 'template: work', offset=10)}
        responses['before'] = client.request('textDocument/definition', params)
        checks['noCrossScopeDefinitionBefore'] = responses['before'].get('result') == []
        responses['offered'], action = offer(client, library, hidden, diagnostics)
        checks['isolatedActionOffered'] = action is not None
        if action is None:
            return checks, responses
        responses['resolve'] = client.request('codeAction/resolve', action)
        checks['isolatedExactEdit'] = exact_edit(responses['resolve'], library, hidden, 1)
        repaired = hidden.replace('paralelism:', 'parallelism:')
        checks['isolatedRepairClean'] = clean(client.change(library, repaired, 2))
        responses['after'] = client.request('textDocument/definition', params)
        checks['noCrossScopeDefinitionAfter'] = responses['after'].get('result') == []
        checks['callerUnmodified'] = path.read_bytes() == caller.encode()
        return checks, responses
    finally:
        client.close()


def isolated_config(name, relative, field):
    caller = m.CALLER.replace(', namespace: argo', '').replace(
        '  entrypoint:', '  paralelism: 1\n  entrypoint:')
    config = CONFIG + 'defaultNamespace: argo\n'
    if field == 'referencePolicy':
        # This unresolved call is informational under open policy and becomes
        # an error under closed policy without changing a single document byte.
        text = caller
        config = config.replace('referencePolicy: closed', 'referencePolicy: open')
    else:
        # Both resources are in the same isolated document. Changing only the
        # default namespace changes the caller's binding to the explicit argo
        # WorkflowTemplate, so it must invalidate the previous safety proof.
        text = caller + '---\n' + m.BASE
    dest, work = m.setup(name, {relative: text, 'other/peer.yaml': PEER}, config)
    path, config_path = work / relative, work / '.argo-workflows-ls.yaml'
    client = m.LSP(dest, work)
    checks, responses = {}, {}
    try:
        diagnostics = client.open(path, text)
        responses['offered'], action = offer(client, path, text, diagnostics)
        checks['actionOffered'] = action is not None
        if action is None:
            return checks, responses
        responses['immediate'] = client.request('codeAction/resolve', action)
        checks['immediateExactEdit'] = exact_edit(responses['immediate'], path, text, 1)
        unresolved = [d for d in diagnostics if d['code'] == 'WF_TEMPLATE_REF_UNRESOLVED']
        checks['initialReferenceState'] = (any(d['severity'] == 3 for d in unresolved)
                                           if field == 'referencePolicy' else unresolved == [])

        other_scope = config.replace('    include: ["other/**"]\n',
                                     '    include: ["other/**"]\n    targetVersion: "3.5.0"\n')
        config_path.write_bytes(other_scope.encode())
        client.send('workspace/didChangeWatchedFiles', {'changes': [{'uri': config_path.as_uri(), 'type': 2}]})
        client.diagnostics(path.as_uri(), 1)
        responses['unrelatedConfig'] = client.request('codeAction/resolve', action)
        checks['unrelatedScopeConfigStillValid'] = exact_edit(responses['unrelatedConfig'], path, text, 1)

        changed = (other_scope.replace('referencePolicy: open', 'referencePolicy: closed')
                   if field == 'referencePolicy' else
                   other_scope.replace('defaultNamespace: argo', 'defaultNamespace: other'))
        config_path.write_bytes(changed.encode())
        client.send('workspace/didChangeWatchedFiles', {'changes': [{'uri': config_path.as_uri(), 'type': 2}]})
        after = client.diagnostics(path.as_uri(), 1)
        checks['effectiveReferenceStateChanged'] = any(
            d['code'] == 'WF_TEMPLATE_REF_UNRESOLVED' and d['severity'] == 1 for d in after)
        responses['changedConfig'] = client.request('codeAction/resolve', action)
        checks['effectiveConfigChangeRefused'] = refused(responses['changedConfig'])
        responses['freshOffer'], current = offer(client, path, text, after)
        checks['freshActionOffered'] = current is not None
        if current is not None:
            responses['freshResolve'] = client.request('codeAction/resolve', current)
            checks['freshConfigExactEdit'] = exact_edit(responses['freshResolve'], path, text, 1)
        checks['documentBytesUnchanged'] = path.read_bytes() == text.encode()
        responses['beforeDiagnostics'] = diagnostics
        responses['afterDiagnostics'] = after
        return checks, responses
    finally:
        client.close()


CASES = (
    ('regular', lambda: lifecycle('regular', 'workflows/a.yaml')),
    ('excluded', lambda: lifecycle('excluded', 'generated/a.yaml')),
    ('internal-directory', lambda: lifecycle('internal-directory', '.argo-ls/a.yaml')),
    ('exclusion-change', exclusion_change),
    ('changed-dependency', changed_dependency),
    ('isolated-reference', isolated_reference),
    ('isolated-config-policy', lambda: isolated_config(
        'isolated-config-policy', 'generated/a.yaml', 'referencePolicy')),
    ('isolated-config-namespace', lambda: isolated_config(
        'isolated-config-namespace', '.argo-ls/a.yaml', 'defaultNamespace')),
)
selected = sys.argv[4].split(',') if len(sys.argv) == 5 else [name for name, _ in CASES]
known = {name for name, _ in CASES}
if not selected or len(selected) != len(set(selected)) or any(name not in known for name in selected):
    print('--cases must name a nonempty, unique selection of known cases', file=sys.stderr)
    raise SystemExit(2)
for name, run in CASES:
    if name not in selected:
        continue
    try:
        assertions, details = run()
        m.result(name, assertions, details)
    except Exception as error:
        m.result(name, {'completed': False}, {'error': repr(error), 'traceback': traceback.format_exc()})
m.save(m.OUT / 'manifest.json', {
    'command': sys.argv, 'binarySha256': m.sha(m.BINARY),
    'testSha256': m.sha(pathlib.Path(__file__)), 'helperSha256': m.sha(HELPER),
    'expectedCases': [name for name, _ in CASES if name in selected],
    'inputs': {str(path.relative_to(m.OUT)): m.sha(path)
               for path in m.OUT.rglob('*.yaml') if 'input' in path.parts},
})
sys.exit(0 if len(m.RESULTS) == len(selected) and all(row['pass'] for row in m.RESULTS) else 1)

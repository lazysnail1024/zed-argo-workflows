#!/usr/bin/env python3
"""Independent stdio contract for explicitly viewing unbound resource candidates.

Expected identities and locations come from tagged fixture bytes. Returned
actions are opaque handles, never the source of the semantic oracle.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import time
import traceback

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('wire', HERE / 'wf-audit-gaps.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
COMMAND = 'argo-workflows.openReferenceCandidate'
KIND = 'source.openReferenceCandidate'
LABEL = 'Same-name resource candidates; binding unproven'
CONFIG = 'schemaVersion: 1\ntargetVersion: "3.6.0"\nreferencePolicy: closed\n'
CALLER = '''apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata: {name: caller}
spec:
  workflowTemplateRef: {name: <reference>library</reference>}
'''
LIBRARY = '''apiVersion: argoproj.io/v1alpha1
kind: WorkflowTemplate
metadata: {name: <resource>library</resource>, namespace: team-a}
spec:
  entrypoint: work
  templates:
    - name: work
      container: {image: alpine}
'''
DAG = CALLER.split('spec:')[0] + '''spec:
  entrypoint: main
  templates:
    - name: main
      dag:
        tasks:
          - name: run
            templateRef: {name: <reference>library</reference>, template: work}
'''
SCOPED = CONFIG + '''exclude: ["allowed/excluded.yaml"]
scopes:
  - name: selected
    include: ["caller.yaml", "allowed/**"]
  - name: unrelated
    include: ["other/**"]
'''
QUOTED = LIBRARY.replace('metadata: {name: <resource>library</resource>, namespace: team-a}',
    'metadata: {annotations: {note: "\U0001f680"}, name: "<resource>library</resource>", namespace: team-a}')
MULTI = (QUOTED.replace('<resource>', '<first>').replace('</resource>', '</first>') + '---\n' +
    LIBRARY.replace('<resource>', '<second>').replace('</resource>', '</second>')).replace('\n', '\r\n')
DISPLAY = '\n---\n'.join(LIBRARY.replace('<resource>', '<r%d>' % index).replace(
    '</resource>', '</r%d>' % index) for index in range(21))
SCAN = '\n---\n'.join(LIBRARY.replace('<resource>library</resource>', 'library-old-%04d' % index)
                       for index in range(1001))
DISK_LIBRARY = LIBRARY.replace('- name: work', '- name: <template>work</template>')
CASES = [
    {'name': 'unknown-namespace', 'files': {'caller.yaml': CALLER, 'library.yaml': LIBRARY},
     'caller': 'caller.yaml', 'candidates': [('library.yaml', 'resource', 'team-a')]},
    {'name': 'multi-candidates', 'files': {'caller.yaml': DAG, 'multi.yaml': MULTI,
        'unknown.yaml': LIBRARY.replace(', namespace: team-a', ''),
        'near-name.yaml': LIBRARY.replace('<resource>library</resource>', 'library-old'),
        'cluster.yaml': LIBRARY.replace('kind: WorkflowTemplate', 'kind: ClusterWorkflowTemplate')},
     'caller': 'caller.yaml', 'candidates': [('multi.yaml', 'first', 'team-a'),
        ('multi.yaml', 'second', 'team-a'), ('unknown.yaml', 'resource', '')]},
    {'name': 'explicit-namespace', 'mode': 'hidden', 'files': {
        'caller.yaml': CALLER.replace('name: caller}', 'name: caller, namespace: team-a}'),
        'library.yaml': LIBRARY, 'other.yaml': LIBRARY.replace('namespace: team-a', 'namespace: team-b')},
     'caller': 'caller.yaml', 'candidates': [], 'definition': [('library.yaml', 'resource')], 'bound': True},
    {'name': 'cluster-binding', 'mode': 'hidden', 'files': {
        'caller.yaml': CALLER.replace('</reference>}', '</reference>, clusterScope: true}'),
        'library.yaml': LIBRARY.replace('kind: WorkflowTemplate', 'kind: ClusterWorkflowTemplate')},
     'caller': 'caller.yaml', 'candidates': [], 'definition': [('library.yaml', 'resource')], 'bound': True},
    {'name': 'scope-exclude-isolation', 'config': SCOPED, 'other_root': True, 'files': {
        'caller.yaml': CALLER, 'allowed/library.yaml': LIBRARY,
        'allowed/excluded.yaml': LIBRARY, 'other/library.yaml': LIBRARY},
     'caller': 'caller.yaml', 'candidates': [('allowed/library.yaml', 'resource', 'team-a')]},
    {'name': 'stale-source', 'mode': 'stale', 'files': {'caller.yaml': CALLER, 'library.yaml': LIBRARY,
        'other-library.yaml': LIBRARY.replace('<resource>library</resource>', '<resource>other-library</resource>')},
     'caller': 'caller.yaml', 'candidates': [('library.yaml', 'resource', 'team-a')],
     'change': ('caller.yaml', CALLER.replace('<reference>library</reference>', '<reference>other-library</reference>')),
     'fresh_name': 'other-library', 'fresh_candidates': [('other-library.yaml', 'resource', 'team-a')]},
    {'name': 'stale-target', 'mode': 'stale', 'files': {'caller.yaml': CALLER, 'library.yaml': LIBRARY},
     'caller': 'caller.yaml', 'candidates': [('library.yaml', 'resource', 'team-a')],
     'change': ('library.yaml', '# changed target position\n' + LIBRARY),
     'fresh_candidates': [('library.yaml', 'resource', 'team-a')]},
    {'name': 'stale-closed-target', 'mode': 'stale', 'closed_disk_change': True,
     'files': {'caller.yaml': CALLER, 'library.yaml': DISK_LIBRARY}, 'skip_open': ['library.yaml'],
     'caller': 'caller.yaml', 'candidates': [('library.yaml', 'resource', 'team-a')],
     'change': ('library.yaml', '# modified outside the editor\n' + DISK_LIBRARY),
     'fresh_candidates': [('library.yaml', 'resource', 'team-a')]},
    {'name': 'stale-config', 'mode': 'stale', 'files': {'caller.yaml': CALLER, 'library.yaml': LIBRARY,
        'remaining.yaml': LIBRARY.replace('namespace: team-a', 'namespace: team-b')},
     'caller': 'caller.yaml', 'candidates': [('library.yaml', 'resource', 'team-a'), ('remaining.yaml', 'resource', 'team-b')],
     'change': ('.argo-workflows-ls.yaml', CONFIG + 'exclude: ["library.yaml"]\n'),
     'fresh_candidates': [('remaining.yaml', 'resource', 'team-b')]},
    {'name': 'read-only-client', 'edit_capability': False, 'files': {'caller.yaml': CALLER, 'library.yaml': LIBRARY},
     'caller': 'caller.yaml', 'candidates': [('library.yaml', 'resource', 'team-a')]},
    {'name': 'no-show-document', 'show_document': False, 'files': {'caller.yaml': CALLER, 'library.yaml': LIBRARY},
     'caller': 'caller.yaml', 'candidates': [('library.yaml', 'resource', 'team-a')]},
    {'name': 'dynamic-name', 'mode': 'hidden', 'files': {'caller.yaml': CALLER.replace('<reference>library</reference>',
        '"<reference>{{workflow.parameters.library}}</reference>"'), 'library.yaml': LIBRARY},
     'caller': 'caller.yaml', 'candidates': [], 'definition': [], 'bound': False},
    {'name': 'alias-reference', 'mode': 'hidden', 'files': {'caller.yaml': CALLER.replace('name: caller}',
        'name: caller, annotations: {selected: &chosen library}}').replace('<reference>library</reference>',
        '<reference>*chosen</reference>'), 'library.yaml': LIBRARY},
     'caller': 'caller.yaml', 'candidates': [], 'definition': [], 'bound': False},
    {'name': 'merge-reference', 'mode': 'hidden', 'files': {'caller.yaml': CALLER.replace('spec:\n',
        'x-ref: &chosen {name: library}\nspec:\n').replace('name: <reference>library</reference>',
        '<<: <reference>*chosen</reference>'), 'library.yaml': LIBRARY},
     'caller': 'caller.yaml', 'candidates': [], 'definition': [], 'bound': False},
    {'name': 'incomplete-scope', 'scope_complete': False, 'files': {'caller.yaml': CALLER, 'library.yaml': LIBRARY,
        'broken.yaml': 'apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: broken}\nspec: {}\n'},
     'peer_damage': 'apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: broken}\nspec: [\n',
     'caller': 'caller.yaml', 'candidates': [('library.yaml', 'resource', 'team-a')]},
    {'name': 'display-cap', 'truncated': True, 'display_count': 20, 'files': {'caller.yaml': CALLER, 'many.yaml': DISPLAY},
     'caller': 'caller.yaml', 'candidates': [('many.yaml', 'r%d' % index, 'team-a') for index in range(21)]},
    {'name': 'query-cap-empty-exact-set', 'truncated': True, 'files': {'caller.yaml': CALLER, 'many.yaml': SCAN},
     'caller': 'caller.yaml', 'candidates': []},
]
ASSERTIONS = (
    'commandAdvertised', 'originalInputIdentity', 'candidateEnvelope',
    'candidateEnumerationComplete', 'candidateItemsExact', 'candidateMessages',
    'candidateActionsExact', 'candidateLabelsDistinct', 'listAndResolveDoNotOpen', 'opensExactSelection',
    'hostResponseAccepted', 'definitionUnchanged', 'referencesRemainUnproven',
    'renameRefused', 'actionKindRespected', 'openCapabilityRespected', 'noEdits', 'diskUnchanged', 'cleanLifecycle',
)
STALE_ASSERTIONS = ASSERTIONS + ('oldResolveRefused', 'oldExecuteRefused', 'noStaleOpen', 'freshCandidatesCurrent')
DISK_STALE_ASSERTIONS = STALE_ASSERTIONS + ('closedDiskRefresh',)
HIDDEN_ASSERTIONS = ('noCandidateListing', 'noCandidateActions', 'definitionUnchanged',
                     'bindingSafetyPreserved', 'noEdits', 'diskUnchanged', 'cleanLifecycle')


def save(path, value):
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))


m.save = save


def point(text):
    return {'line': text.count('\n'),
            'character': len(text.rsplit('\n', 1)[-1].encode('utf-16-le')) // 2}


def strip_tags(marked):
    text, spans, end = '', {}, 0
    for match in re.finditer(r'<([a-z0-9-]+)>([^<>]*)</\1>', marked):
        text += marked[end:match.start()]
        start = point(text)
        text += match.group(2)
        spans[match.group(1)] = {'start': start, 'end': point(text)}
        end = match.end()
    text += marked[end:]
    assert not re.search(r'</?[a-z0-9-]+>', text), 'unrecognized fixture marker'
    return text, spans


def norm(values):
    return sorted(json.dumps(value, sort_keys=True) for value in values)


class Client(m.LSP):
    def __init__(self, dest, work, capabilities):
        self.capabilities = capabilities
        super().__init__(dest, work)

    def send(self, method, params=None, ident=None):
        if method == 'initialize':
            params['capabilities'] = self.capabilities
        return super().send(method, params, ident)

    def respond(self, ident, result):
        value = {'jsonrpc': '2.0', 'id': ident, 'result': result}
        self.sent.append(value)
        payload = json.dumps(value).encode('utf-8')
        self.p.stdin.write(('Content-Length: %d\r\n\r\n' % len(payload)).encode() + payload)
        self.p.stdin.flush()


def query(client, path, method, position, **extra):
    return client.request(method, {'textDocument': {'uri': path.as_uri()},
                                   'position': position, **extra})


def explain_ready(client, path, position, scope_complete=True):
    deadline = time.monotonic() + 10
    while True:
        response = query(client, path, 'argo-workflows/explainAnalysis', position)
        actual = response.get('result', {}).get('completeness', {}).get('workspace')
        if scope_complete and actual is True:
            return response
        if not scope_complete and actual is False:
            return response
        if 'error' in response or time.monotonic() >= deadline:
            raise AssertionError('fixture scope did not become ready: ' + repr(response))
        time.sleep(.02)


def code_actions(client, path, span, kind=KIND):
    return client.request('textDocument/codeAction', {
        'textDocument': {'uri': path.as_uri()}, 'range': span,
        'context': {'diagnostics': [], 'only': [kind]}})


def selected_actions(response):
    return [action for action in response.get('result', [])
            if action.get('command', {}).get('command') == COMMAND]


def messages(client, method):
    return [message for message in client.received if message.get('method') == method]


def assertion_names(case):
    if case.get('mode') == 'hidden':
        return HIDDEN_ASSERTIONS
    if case.get('closed_disk_change'):
        return DISK_STALE_ASSERTIONS
    return STALE_ASSERTIONS if case.get('mode') == 'stale' else ASSERTIONS


def expected_items(work, spans, entries, name='library'):
    return [{'kind': 'WorkflowTemplate', 'name': name, 'namespace': namespace,
             'location': {'uri': (work / filename).as_uri(), 'range': spans[filename][tag]},
             'reasonCode': 'TARGET_UNKNOWN'} for filename, tag, namespace in entries]


def expected_locations(work, spans, entries):
    return [{'uri': (work / filename).as_uri(), 'range': spans[filename][tag]} for filename, tag in entries]


def prepare(case):
    files, spans = {}, {}
    for filename, marked in case['files'].items():
        files[filename], spans[filename] = strip_tags(marked)
    dest, work = m.setup(case['name'], files, case.get('config', CONFIG))
    path = work / case['caller']
    selection = {'uri': path.as_uri(), 'range': spans[case['caller']]['reference']}
    expected = expected_items(work, spans, case['candidates'])
    scope_complete, truncated = case.get('scope_complete', True), case.get('truncated', False)
    oracle = {'label': LABEL, 'selection': selection, 'reasonCode': 'TARGET_UNKNOWN',
        'scopeComplete': scope_complete, 'truncated': truncated, 'complete': scope_complete and not truncated,
        'items': expected, 'displayCount': case.get('display_count', len(expected)),
        'definition': expected_locations(work, spans, case.get('definition', [])),
        'referenceComplete': case.get('bound', False), 'actionKind': KIND, 'command': COMMAND}
    state = {'files': files, 'spans': spans, 'dest': dest, 'work': work, 'path': path,
             'selection': selection, 'items': expected, 'oracle': oracle}
    if case.get('peer_damage'):
        (dest / 'planned-peer-damage.yaml').write_bytes(case['peer_damage'].encode())
        oracle['transition'] = {'initialScopeComplete': True, 'changedFile': 'broken.yaml', 'version': 2}
    if case.get('other_root'):
        other = dest / 'other-root'
        other.mkdir()
        (other / '.argo-workflows-ls.yaml').write_bytes(CONFIG.encode())
        (other / 'library.yaml').write_bytes(strip_tags(LIBRARY)[0].encode())
        state['other_root'] = other
    if case.get('mode') == 'stale':
        filename, marked = case['change']
        changed, changed_spans = strip_tags(marked)
        new_spans, new_files = dict(spans), dict(files)
        new_spans[filename], new_files[filename] = changed_spans, changed
        fresh_selection = {'uri': path.as_uri(), 'range': new_spans[case['caller']]['reference']}
        fresh_items = expected_items(work, new_spans, case['fresh_candidates'], case.get('fresh_name', 'library'))
        state['fresh'] = {'filename': filename, 'text': changed, 'files': new_files, 'spans': new_spans, 'selection': fresh_selection, 'items': fresh_items}
        (dest / 'planned-change.yaml').write_bytes(changed.encode())
        oracle['fresh'] = {'selection': fresh_selection, 'items': fresh_items,
                           'oldResolveError': -32801, 'oldExecuteError': -32801, 'reasonCode': 'STALE_INPUT'}
        if case.get('closed_disk_change'):
            (dest / 'target-before.yaml').write_bytes(files[filename].encode())
            oracle['filesystemChange'] = {'path': filename, 'beforeSHA256': hashlib.sha256(files[filename].encode()).hexdigest(),
                'afterSHA256': hashlib.sha256(changed.encode()).hexdigest(),
                'currentTemplateSelection': new_spans[filename]['template'], 'notifications': []}
    save(dest / 'expected.json', oracle)
    return state


def start(case, state):
    capabilities = {}
    if case.get('edit_capability', True):
        capabilities['workspace'] = {'workspaceEdit': {'documentChanges': True}}
    if case.get('show_document', True):
        capabilities['window'] = {'showDocument': {'support': True}}
    client = Client(state['dest'], state['work'], capabilities)
    client.precondition = {}
    try:
        if state.get('other_root'):
            client.send('workspace/didChangeWorkspaceFolders', {'event': {
                'added': [{'uri': state['other_root'].as_uri(), 'name': 'other-root'}], 'removed': []}})
        for filename, text in state['files'].items():
            if filename not in case.get('skip_open', []):
                client.open(state['work'] / filename, text)
        if case.get('peer_damage'):
            client.precondition['completeScope'] = explain_ready(client, state['path'], state['selection']['range']['start'])
            client.precondition['damagedPeerDiagnostics'] = client.change(state['work'] / 'broken.yaml', case['peer_damage'], 2)
    except Exception:
        client.close()
        raise
    return client


def finish(case, state, client, checks, details):
    expected_disk = dict(state['files'])
    if case.get('closed_disk_change'):
        expected_disk[state['fresh']['filename']] = state['fresh']['text']
    checks['diskUnchanged'] = all((state['work'] / filename).read_bytes() == text.encode('utf-8')
                                  for filename, text in expected_disk.items())
    checks['diskUnchanged'] = checks['diskUnchanged'] and (state['work'] / '.argo-workflows-ls.yaml').read_bytes() == case.get('config', CONFIG).encode()
    if state.get('other_root'):
        checks['diskUnchanged'] = checks['diskUnchanged'] and (state['other_root'] / 'library.yaml').read_bytes() == strip_tags(LIBRARY)[0].encode()
    if client:
        try:
            client.close()
            checks['cleanLifecycle'] = 'error' not in details
        except Exception:
            details['closeError'] = traceback.format_exc()
    m.result(case['name'], checks, details)


def matches_display(actual, expected, count):
    if count == len(expected):
        return norm(actual) == norm(expected)
    return len(actual) == count and len(set(norm(actual))) == count and set(norm(actual)) <= set(norm(expected))


def project_items(items):
    return [{key: item.get(key) for key in ('kind', 'name', 'namespace', 'location', 'reasonCode')} for item in items]


def guarded_reads(client, path, position, status, case):
    refs = query(client, path, 'textDocument/references', position, context={'includeDeclaration': False})
    rename = query(client, path, 'textDocument/rename', position, newName='reviewed-library')
    scope_complete = case.get('scope_complete', True)
    # The caller remains parseable even when an unrelated peer is damaged.
    # Its empty result needs the separate, explicitly incomplete proof below.
    reference_result = refs.get('result') == [] and 'error' not in refs
    references_safe = (reference_result and status.get('references', {}).get('complete') is False and
        status.get('references', {}).get('reasonCode') == ('TARGET_UNKNOWN' if scope_complete else 'INDEX_INCOMPLETE'))
    rename_safe = (rename.get('error', {}).get('code') == -32602 and 'result' not in rename and
        status.get('operations', {}).get('rename', {}).get('allowed') is False)
    if scope_complete and case.get('edit_capability', True):
        rename_safe = rename_safe and rename.get('error', {}).get('data', {}).get('reasonCode') == 'TARGET_UNKNOWN'
    if not case.get('edit_capability', True):
        rename_safe = rename_safe and status.get('operations', {}).get('rename', {}).get('reasonCode') == 'CLIENT_CAPABILITY_MISSING'
    return references_safe, rename_safe, {'references': refs, 'rename': rename}


def open_actions(client, path, position, actions, expected, count, show_document):
    executed, opened, titles, accepted = [], [], True, True
    before_shows, before_messages = len(messages(client, 'window/showDocument')), len(messages(client, 'window/showMessage'))
    for action in actions:
        show_count = len(messages(client, 'window/showDocument'))
        response = client.request('workspace/executeCommand', action['command'])
        executed.append(response)
        # A normal request on the same lane observes the previous command's
        # notifications without treating a missing notification as a timeout.
        barrier = query(client, path, 'textDocument/definition', position)
        accepted = accepted and barrier.get('result') == [] and 'error' not in barrier
        current = messages(client, 'window/showDocument')[show_count:]
        for message in current:
            params = message.get('params', {})
            opened.append({'uri': params.get('uri'), 'range': params.get('selection')})
            client.respond(message['id'], {'success': True})
            after_ack = query(client, path, 'textDocument/definition', position)
            accepted = accepted and after_ack.get('result') == [] and 'error' not in after_ack
        location = response.get('result', {}).get('location')
        target = next((item for item in expected if item['location'] == location), None)
        title = action.get('title', '')
        titles = titles and target is not None
        if target:
            filename = next(Path(uri).name for uri in [target['location']['uri']])
            line = target['location']['range']['start']['line'] + 1
            titles = titles and target['kind'] in title and target['name'] in title and (target['namespace'] or 'unknown') in title and filename + ':' + str(line) in title
    expected_locations_only = [item['location'] for item in expected]
    returned = [response.get('result', {}).get('location') for response in executed]
    exact = matches_display(returned, expected_locations_only, count) and all(
        'error' not in response and isinstance(response.get('result', {}).get('message'), str) and response['result']['message'] and
        response['result'].get('opened') is not True for response in executed)
    shows = messages(client, 'window/showDocument')[before_shows:]
    if show_document:
        exact = exact and matches_display(opened, expected_locations_only, count)
        capability = len(shows) == count and all(message.get('params', {}).get('external') is False and
            message.get('params', {}).get('takeFocus') is True for message in shows)
    else:
        notifications = messages(client, 'window/showMessage')[before_messages:]
        capability = not shows and len(notifications) == count and all(
            isinstance(message.get('params', {}).get('message'), str) and message['params']['message'] for message in notifications)
    return {'opensExactSelection': exact, 'hostResponseAccepted': accepted and len(actions) == count,
            'candidateLabelsDistinct': titles and len(set(action.get('title', '') for action in actions)) == count,
            'openCapabilityRespected': capability}, executed


def run(case):
    state = prepare(case)
    files, dest, work, path, selection, expected = (state[key] for key in ('files', 'dest', 'work', 'path', 'selection', 'items'))
    position = selection['range']['start']
    checks, details, client = dict.fromkeys(assertion_names(case), False), {}, None
    try:
        client = start(case, state)
        details['initialize'] = client.init
        details['precondition'] = client.precondition
        checks['commandAdvertised'] = COMMAND in client.init.get('result', {}).get('capabilities', {}).get('executeCommandProvider', {}).get('commands', [])
        details['explanation'] = explain_ready(client, path, position, case.get('scope_complete', True))
        status = details['explanation'].get('result', {})
        identity = status.get('inputIdentity', {})
        checks['originalInputIdentity'] = (identity.get('uri') == path.as_uri() and identity.get('source') == 'buffer' and
            identity.get('version') == 1 and identity.get('sha256') == hashlib.sha256(files[case['caller']].encode()).hexdigest())
        candidates = status.get('referenceCandidates', {})
        checks['candidateEnvelope'] = candidates.get('label') == LABEL and candidates.get('selection') == selection and candidates.get('reasonCode') == 'TARGET_UNKNOWN'
        checks['candidateEnumerationComplete'] = all(candidates.get(key) is state['oracle'][key] for key in ('scopeComplete', 'truncated', 'complete'))
        items = candidates.get('items', [])
        display_count = state['oracle']['displayCount']
        checks['candidateItemsExact'] = matches_display(project_items(items), expected, display_count)
        checks['candidateMessages'] = bool(candidates) and all(isinstance(item.get('message'), str) and item['message'] and
            isinstance(item.get('nextStep'), str) and item['nextStep'] and item.get('relatedLocations') == [] for item in [candidates, *items])
        before_definition = query(client, path, 'textDocument/definition', position)
        checks['referencesRemainUnproven'], checks['renameRefused'], details['guardsBefore'] = guarded_reads(client, path, position, status, case)
        details['actions'] = code_actions(client, path, selection['range'])
        actions = selected_actions(details['actions'])
        checks['candidateActionsExact'] = len(actions) == display_count and all(
            action.get('kind') == KIND and 'candidate' in action.get('title', '').lower() and not action.get('disabled') for action in actions)
        details['resolved'] = [client.request('codeAction/resolve', action) for action in actions]
        checks['listAndResolveDoNotOpen'] = len(actions) == display_count and all(response.get('result') == action and 'error' not in response
            for action, response in zip(actions, details['resolved'])) and not messages(client, 'window/showDocument')
        details['quickfixActions'] = code_actions(client, path, selection['range'], 'quickfix')
        details['explanationActions'] = code_actions(client, path, selection['range'], 'source.explainAnalysis')
        checks['actionKindRespected'] = not selected_actions(details['quickfixActions']) and not selected_actions(details['explanationActions'])
        all_actions = list(actions)
        if case.get('mode') == 'stale':
            fresh = state['fresh']
            changed_path = work / fresh['filename']
            if case.get('closed_disk_change'):
                changed_path.write_bytes(fresh['text'].encode())
                deadline = time.monotonic() + 10
                refreshes = []
                while True:
                    refreshed = client.request('textDocument/documentSymbol', {'textDocument': {'uri': changed_path.as_uri()}})
                    refreshes.append(refreshed)
                    current = any(symbol.get('name') == 'work' and symbol.get('selectionRange') == fresh['spans'][fresh['filename']]['template']
                                  for symbol in refreshed.get('result', []))
                    if current or time.monotonic() >= deadline:
                        break
                    time.sleep(.02)
                details['targetRefresh'] = refreshes
                details['targetScopeReady'] = explain_ready(client, path, position)
                target_notifications = [message for message in client.sent if message.get('method') in
                    ('textDocument/didOpen', 'textDocument/didChange', 'textDocument/didSave', 'workspace/didChangeWatchedFiles') and
                    (message.get('params', {}).get('textDocument', {}).get('uri') == changed_path.as_uri() or message.get('method') == 'workspace/didChangeWatchedFiles')]
                checks['closedDiskRefresh'] = current and not target_notifications
            elif fresh['filename'] == '.argo-workflows-ls.yaml':
                client.open(changed_path, fresh['text'], 1)
            else:
                client.change(changed_path, fresh['text'], 2)
            show_count, info_count = len(messages(client, 'window/showDocument')), len(messages(client, 'window/showMessage'))
            details['staleResolve'] = [client.request('codeAction/resolve', action) for action in actions]
            details['staleExecute'] = [client.request('workspace/executeCommand', action['command']) for action in actions]
            def stale_refusal(response):
                return response.get('error', {}).get('code') == -32801 and response.get('error', {}).get('data', {}).get('reasonCode') == 'STALE_INPUT' and 'result' not in response
            checks['oldResolveRefused'] = bool(actions) and all(stale_refusal(response) for response in details['staleResolve'])
            checks['oldExecuteRefused'] = bool(actions) and all(stale_refusal(response) for response in details['staleExecute'])
            selection, expected = fresh['selection'], fresh['items']
            position, display_count = selection['range']['start'], len(expected)
            details['freshExplanation'] = explain_ready(client, path, position)
            checks['noStaleOpen'] = bool(actions) and len(messages(client, 'window/showDocument')) == show_count and len(messages(client, 'window/showMessage')) == info_count
            fresh_view = details['freshExplanation'].get('result', {}).get('referenceCandidates', {})
            checks['freshCandidatesCurrent'] = fresh_view.get('selection') == selection and norm(project_items(fresh_view.get('items', []))) == norm(expected) and fresh_view.get('complete') is True
            details['freshActions'] = code_actions(client, path, selection['range'])
            actions = selected_actions(details['freshActions'])
            all_actions += actions
        opened_checks, details['executed'] = open_actions(client, path, position, actions, expected, display_count, case.get('show_document', True))
        checks.update(opened_checks)
        after_definition = query(client, path, 'textDocument/definition', position)
        details['definitions'] = [before_definition, after_definition]
        checks['definitionUnchanged'] = all(response.get('result') == [] and 'error' not in response for response in details['definitions'])
        details['explanationAfter'] = explain_ready(client, path, position, case.get('scope_complete', True))
        refs_safe, rename_safe, details['guardsAfter'] = guarded_reads(client, path, position, details['explanationAfter'].get('result', {}), case)
        checks['referencesRemainUnproven'] = checks['referencesRemainUnproven'] and refs_safe
        checks['renameRefused'] = checks['renameRefused'] and rename_safe
        checks['noEdits'] = all('edit' not in action for action in all_actions) and not messages(client, 'workspace/applyEdit')
    except Exception:
        details['error'] = traceback.format_exc()
    finally:
        finish(case, state, client, checks, details)


def run_hidden(case):
    state = prepare(case)
    path, selection = state['path'], state['selection']
    position = selection['range']['start']
    checks, details, client = dict.fromkeys(HIDDEN_ASSERTIONS, False), {}, None
    try:
        client = start(case, state)
        details['explanation'] = explain_ready(client, path, position) if case.get('bound') else query(client, path, 'argo-workflows/explainAnalysis', position)
        status = details['explanation'].get('result', {})
        checks['noCandidateListing'] = not status.get('referenceCandidates', {}).get('items')
        details['actions'] = code_actions(client, path, selection['range'])
        details['allSourceActions'] = code_actions(client, path, selection['range'], 'source')
        checks['noCandidateActions'] = not selected_actions(details['actions']) and not selected_actions(details['allSourceActions'])
        details['definition'] = query(client, path, 'textDocument/definition', position)
        checks['definitionUnchanged'] = details['definition'].get('result') == state['oracle']['definition'] and 'error' not in details['definition']
        if case.get('bound'):
            details['prepareRename'] = query(client, path, 'textDocument/prepareRename', position)
            checks['bindingSafetyPreserved'] = (status.get('references', {}).get('complete') is True and
                status.get('operations', {}).get('rename', {}).get('allowed') is True and
                details['prepareRename'].get('result') == {'range': selection['range'], 'placeholder': 'library'})
        else:
            details['rename'] = query(client, path, 'textDocument/rename', position, newName='reviewed-library')
            checks['bindingSafetyPreserved'] = (status.get('references', {}).get('complete') is False and
                status.get('operations', {}).get('rename', {}).get('allowed') is False and
                'error' in details['rename'] and 'result' not in details['rename'])
        checks['noEdits'] = not messages(client, 'workspace/applyEdit') and not messages(client, 'window/showDocument')
    except Exception:
        details['error'] = traceback.format_exc()
    finally:
        finish(case, state, client, checks, details)


source = m.OUT / 'source'
source.mkdir(parents=True, exist_ok=True)
for filename in ('wf-reference-candidates.py', 'wf-audit-gaps.py'):
    (source / filename).write_bytes((HERE / filename).read_bytes())
for case in CASES:
    try:
        (run_hidden if case.get('mode') == 'hidden' else run)(case)
    except Exception:
        m.result(case['name'], dict.fromkeys(assertion_names(case), False), {'error': traceback.format_exc()})
head = subprocess.run(['git', '-C', str(HERE.parent.parent), 'rev-parse', 'HEAD'],
                      capture_output=True, text=True, check=False)
save(m.OUT / 'manifest.json', {
    'command': sys.argv, 'caseCount': len(CASES), 'sourceHead': head.stdout.strip(),
    'binarySHA256': m.sha(m.BINARY), 'consumerSHA256': m.sha(Path(__file__)),
    'helperSHA256': m.sha(HERE / 'wf-audit-gaps.py'),
    'inputHashes': {str(path.relative_to(m.OUT)): m.sha(path) for path in sorted(m.OUT.rglob('*.yaml'))},
    'expectedHashes': {str(path.relative_to(m.OUT)): m.sha(path) for path in sorted(m.OUT.rglob('expected.json'))},
})
sys.exit(0 if len(m.RESULTS) == len(CASES) and all(row['pass'] for row in m.RESULTS) else 1)

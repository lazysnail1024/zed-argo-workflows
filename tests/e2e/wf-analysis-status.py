#!/usr/bin/env python3
"""Explain current input and real operation refusals through CLI and stdio."""
import hashlib
import importlib.util
import json
import pathlib
import sys
import time
import traceback
from support.session import Session

HELPER = pathlib.Path(__file__).with_name('wf-audit-gaps.py')
spec = importlib.util.spec_from_file_location('wire', HELPER)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

CONFIG = 'schemaVersion: 1\ntargetVersion: "3.6.0"\nreferencePolicy: closed\n'
LIBRARY = '''apiVersion: argoproj.io/v1alpha1
kind: WorkflowTemplate
metadata: {name: library, namespace: argo}
spec:
  entrypoint: work
  templates:
    - name: work
      container: {image: alpine}
'''
CALLER = '''apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata: {name: caller, namespace: argo}
spec:
  workflowTemplateRef: {name: library}
'''


def explain(client, path, text, needle='name: library'):
    return client.request('argo-workflows/explainAnalysis', {
        'textDocument': {'uri': path.as_uri()},
        'position': m.pos(text, needle, offset=6),
    })


def wait_status(client, path, text, predicate):
    deadline = time.monotonic() + 10
    while True:
        response = explain(client, path, text)
        if predicate(response.get('result', {})) or 'error' in response or time.monotonic() >= deadline:
            return response
        time.sleep(.02)


def input_sources():
    dest, work = m.setup('input-sources', {'library.yaml': LIBRARY, 'caller.yaml': CALLER}, CONFIG)
    path = work / 'library.yaml'
    output = m.command(dest, 'explain-config', [m.BINARY, 'explain-config', '--root', work, '--file', path])
    disk = json.loads(output.stdout) if output.returncode == 0 else {}
    client = m.LSP(dest, work)
    try:
        buffer = LIBRARY + '# unsaved input\n'
        client.open(path, buffer, 7)
        response = wait_status(client, path, buffer, lambda status: status.get('completeness', {}).get('workspace') is True)
        status = response.get('result', {})
        disk_status = disk.get('analysis', {})
        params = {'textDocument': {'uri': path.as_uri()}, 'range': {'start': {'line': 2, 'character': 19}, 'end': {'line': 2, 'character': 19}}, 'context': {'diagnostics': []}}
        actions = client.request('textDocument/codeAction', params)
        action = next((a for a in actions.get('result', []) if a.get('command', {}).get('command') == 'argo-workflows.explainAnalysis'), {})
        resolved = client.request('codeAction/resolve', action) if action else {}
        executed = client.request('workspace/executeCommand', resolved['result']['command']) if resolved.get('result', {}).get('command') else {}
        if 'result' in executed:
            client.wait(lambda event: event.get('method') == 'window/showMessage')
        shows = [event for event in client.received if event.get('method') == 'window/showMessage']
        checks = {
            'disk_source': disk_status.get('inputIdentity', {}).get('source') == 'disk',
            'disk_hash': disk_status.get('inputIdentity', {}).get('sha256') == hashlib.sha256(LIBRARY.encode()).hexdigest(),
            'buffer_identity': status.get('inputIdentity', {}).get('source') == 'buffer' and status['inputIdentity'].get('version') == 7 and status['inputIdentity'].get('sha256') == hashlib.sha256(buffer.encode()).hexdigest(),
            'owned_root_scope': status.get('root') == str(work) and status.get('scope') == 'root' and status.get('roots') == [str(work)],
            'separate_completeness': status.get('completeness') == {'document': True, 'workspace': True},
            'resolved_action': bool(action) and resolved.get('result') == action,
            'read_only_action': bool(action) and 'edit' not in action and executed.get('result', {}).get('inputIdentity') == status.get('inputIdentity'),
            'visible_explanation': bool(shows) and 'buffer' in shows[-1]['params']['message'] and str(work) in shows[-1]['params']['message'] and (work / 'caller.yaml').as_uri() in shows[-1]['params']['message'],
            'disk_unchanged': path.read_bytes() == LIBRARY.encode() and (work / '.argo-workflows-ls.yaml').read_bytes() == CONFIG.encode(),
        }
        return checks, {'disk': disk, 'status': response, 'action': action, 'resolved': resolved, 'executed': executed, 'messages': shows}
    finally:
        client.close()


def refusal_case(name, files, reason, config=CONFIG):
    dest, work = m.setup(name, files, config)
    client = m.LSP(dest, work)
    try:
        for file, text in files.items():
            client.open(work / file, text)
        path, text = work / 'library.yaml', files['library.yaml']
        response = wait_status(client, path, text, lambda s: s.get('completeness', {}).get('workspace') is True)
        operation = response.get('result', {}).get('operations', {}).get('rename', {})
        prepared = client.request('textDocument/prepareRename', {'textDocument': {'uri': path.as_uri()}, 'position': m.pos(text, 'name: library', offset=6)})
        data = prepared.get('error', {}).get('data', {})
        return {
            'explanation_reason': operation.get('allowed') is False and operation.get('reasonCode') == reason,
            'real_guard_reason': data.get('reasonCode') == reason and 'result' not in prepared,
            'next_step': bool(operation.get('nextStep')) and bool(data.get('nextStep')),
            'disk_unchanged': all((work / file).read_bytes() == text.encode() for file, text in files.items()) and (work / '.argo-workflows-ls.yaml').read_bytes() == config.encode(),
        }, {'status': response, 'prepare': prepared}
    finally:
        client.close()


def closed_file_recovery():
    dest, work = m.setup('closed-file-recovery', {'library.yaml': LIBRARY, 'caller.yaml': CALLER}, CONFIG)
    client = m.LSP(dest, work)
    try:
        path = work / 'library.yaml'
        client.open(path, LIBRARY)
        before = wait_status(client, path, LIBRARY, lambda s: s.get('completeness', {}).get('workspace') is True)
        old = before.get('result', {}).get('operations', {}).get('rename', {})
        client.open(work / 'caller.yaml', CALLER)
        after = wait_status(client, path, LIBRARY, lambda s: s.get('operations', {}).get('rename', {}).get('allowed') is True)
        prepared = client.request('textDocument/prepareRename', {'textDocument': {'uri': path.as_uri()}, 'position': m.pos(LIBRARY, 'name: library', offset=6)})
        return {
            'closed_refused': old.get('reasonCode') == 'AFFECTED_FILE_NOT_OPEN' and old.get('allowed') is False,
            'related_file': any(loc.get('uri') == (work / 'caller.yaml').as_uri() for loc in old.get('relatedLocations', [])),
            'open_recovers': after.get('result', {}).get('operations', {}).get('rename', {}).get('allowed') is True and prepared.get('result', {}).get('placeholder') == 'library',
            'disk_unchanged': path.read_bytes() == LIBRARY.encode(),
        }, {'before': before, 'after': after, 'prepare': prepared}
    finally:
        client.close()


def incomplete_scope():
    files = {'library.yaml': LIBRARY, 'broken.yaml': 'apiVersion: [\n'}
    dest, work = m.setup('incomplete-scope', files, CONFIG)
    client = m.LSP(dest, work)
    try:
        path = work / 'library.yaml'
        client.open(path, LIBRARY)
        response = explain(client, path, LIBRARY)
        status = response.get('result', {})
        references = status.get('references', {})
        return {
            'document_available': status.get('completeness', {}).get('document') is True,
            'scope_incomplete': status.get('completeness', {}).get('workspace') is False,
            'unknown_empty_distinct': references.get('count') == 0 and references.get('complete') is False,
            'refusal_explained': status.get('operations', {}).get('rename', {}).get('reasonCode') == 'INDEX_INCOMPLETE',
        }, {'status': response}
    finally:
        client.close()


def stale_action():
    dest, work = m.setup('stale-action', {'library.yaml': LIBRARY}, CONFIG)
    client = m.LSP(dest, work)
    try:
        path = work / 'library.yaml'
        client.open(path, LIBRARY)
        actions = client.request('textDocument/codeAction', {'textDocument': {'uri': path.as_uri()}, 'range': {'start': {'line': 2, 'character': 19}, 'end': {'line': 2, 'character': 19}}, 'context': {'diagnostics': []}})
        action = next((a for a in actions.get('result', []) if a.get('command', {}).get('command') == 'argo-workflows.explainAnalysis'), {})
        client.change(path, LIBRARY + '# new input\n', 2)
        response = client.request('workspace/executeCommand', action['command']) if action else {}
        fresh = client.request('textDocument/codeAction', {'textDocument': {'uri': path.as_uri()}, 'range': {'start': {'line': 2, 'character': 19}, 'end': {'line': 2, 'character': 19}}, 'context': {'only': ['source.explainAnalysis'], 'diagnostics': []}})
        fresh_action = next((a for a in fresh.get('result', []) if a.get('command', {}).get('command') == 'argo-workflows.explainAnalysis'), {})
        client.open(work / '.argo-workflows-ls.yaml', CONFIG + 'defaultNamespace: argo\n')
        config_response = client.request('workspace/executeCommand', fresh_action['command']) if fresh_action else {}
        return {
            'offered': bool(action),
            'stale_refused': response.get('error', {}).get('data', {}).get('reasonCode') == 'STALE_INPUT' and 'result' not in response,
            'config_stale_refused': config_response.get('error', {}).get('data', {}).get('reasonCode') == 'STALE_INPUT' and 'result' not in config_response,
            'no_old_message': not any(event.get('method') == 'window/showMessage' for event in client.received),
            'disk_unchanged': path.read_bytes() == LIBRARY.encode(),
        }, {'action': action, 'response': response, 'freshAction': fresh_action, 'configurationResponse': config_response}
    finally:
        client.close()


def proven_empty():
    dest, work = m.setup('proven-empty', {'library.yaml': LIBRARY}, CONFIG)
    client = m.LSP(dest, work)
    try:
        path = work / 'library.yaml'
        client.open(path, LIBRARY)
        response = wait_status(client, path, LIBRARY, lambda s: s.get('completeness', {}).get('workspace') is True)
        status = response.get('result', {})
        return {
            'complete_zero_references': status.get('references') == {'count': 0, 'complete': True},
            'rename_available': status.get('operations', {}).get('rename', {}).get('allowed') is True,
        }, {'status': response}
    finally:
        client.close()


def invalid_configuration():
    config = 'schemaVersion: 1\ntargetVersion: "unknown"\n'
    dest, work = m.setup('invalid-configuration', {'library.yaml': LIBRARY}, config)
    client = m.LSP(dest, work)
    try:
        path = work / 'library.yaml'
        client.open(path, LIBRARY)
        response = explain(client, path, LIBRARY)
        status = response.get('result', {})
        return {
            'unavailable_explained': status.get('phase') == 'unavailable' and any(r.get('reasonCode') == 'ANALYSIS_UNAVAILABLE' for r in status.get('reasons', [])),
            'not_complete': status.get('completeness') == {'document': False, 'workspace': False},
            'buffer_identity': status.get('inputIdentity', {}).get('sha256') == hashlib.sha256(LIBRARY.encode()).hexdigest(),
            'config_unchanged': (work / '.argo-workflows-ls.yaml').read_bytes() == config.encode(),
        }, {'status': response}
    finally:
        client.close()


def read_only_client(name='read-only-client', files=None, reference_reason=None):
    dest, work = m.setup(name, files or {'library.yaml': LIBRARY}, CONFIG)
    path = work / 'library.yaml'
    client = Session([m.BINARY, 'serve', '--stdio'], dest)
    request_id = 0
    def request(method, params=None):
        nonlocal request_id
        request_id += 1
        client.send(method, params, request_id)
        return client.wait(lambda message: message.get('id') == request_id)
    try:
        initialized = request('initialize', {'rootUri': work.as_uri(), 'capabilities': {}})
        client.send('initialized', {})
        client.send('textDocument/didOpen', {'textDocument': {'uri': path.as_uri(), 'version': 1, 'languageId': 'yaml', 'text': LIBRARY}})
        client.diagnostics(path.as_uri(), 1)
        deadline = time.monotonic() + 10
        while True:
            status = request('argo-workflows/explainAnalysis', {'textDocument': {'uri': path.as_uri()}, 'position': m.pos(LIBRARY, 'name: library', offset=6)})
            if status.get('result', {}).get('completeness', {}).get('workspace') is True or time.monotonic() >= deadline:
                break
            time.sleep(.02)
        params = {'textDocument': {'uri': path.as_uri()}, 'range': {'start': {'line': 2, 'character': 19}, 'end': {'line': 2, 'character': 19}}, 'context': {'only': ['source.explainAnalysis'], 'diagnostics': []}}
        actions = request('textDocument/codeAction', params)
        action = next((a for a in actions.get('result', []) if a.get('command', {}).get('command') == 'argo-workflows.explainAnalysis'), {})
        response = request('workspace/executeCommand', action['command']) if action else {}
        quickfixes = request('textDocument/codeAction', params | {'context': {'only': ['quickfix'], 'diagnostics': []}})
        request('shutdown')
        client.send('exit')
        client.proc.wait(timeout=5)
        return {
            'command_advertised': 'argo-workflows.explainAnalysis' in initialized.get('result', {}).get('capabilities', {}).get('executeCommandProvider', {}).get('commands', []),
            'read_only_usable': bool(action) and response.get('result', {}).get('completeness', {}).get('document') is True,
            'edit_capability_explained': status.get('result', {}).get('operations', {}).get('rename', {}).get('reasonCode') == 'CLIENT_CAPABILITY_MISSING',
            'action_kind_respected': quickfixes.get('result') == [],
            'reference_certainty': (status.get('result', {}).get('references', {}).get('complete') is False and status['result']['references'].get('reasonCode') == reference_reason) if reference_reason else status.get('result', {}).get('references') == {'count': 0, 'complete': True},
            'natural_exit': client.proc.returncode == 0,
        }, {'initialize': initialized, 'status': status, 'actions': actions, 'response': response, 'quickfixes': quickfixes}
    finally:
        client.close()


CASES = [
    ('input-sources', input_sources),
    ('closed-file-recovery', closed_file_recovery),
    ('incomplete-scope', incomplete_scope),
    ('ambiguous-binding', lambda: refusal_case('ambiguous-binding', {'library.yaml': LIBRARY, 'caller.yaml': CALLER, 'duplicate.yaml': LIBRARY}, 'AMBIGUOUS_BINDING')),
    ('unknown-namespace', lambda: refusal_case('unknown-namespace', {k: v.replace(', namespace: argo', '') for k, v in {'library.yaml': LIBRARY, 'caller.yaml': CALLER}.items()}, 'TARGET_UNKNOWN')),
    ('dynamic-reference', lambda: refusal_case('dynamic-reference', {'library.yaml': LIBRARY, 'caller.yaml': CALLER.replace('name: library}', 'name: "{{workflow.parameters.name}}"}')}, 'DYNAMIC_REFERENCE')),
    ('open-policy', lambda: refusal_case('open-policy', {'library.yaml': LIBRARY, 'caller.yaml': CALLER}, 'REFERENCE_POLICY_OPEN', CONFIG.replace('referencePolicy: closed', 'referencePolicy: open'))),
    ('stale-action', stale_action),
    ('proven-empty', proven_empty),
    ('invalid-configuration', invalid_configuration),
    ('read-only-client', read_only_client),
    ('read-only-dynamic', lambda: read_only_client('read-only-dynamic', {'library.yaml': LIBRARY, 'caller.yaml': CALLER.replace('name: library}', 'name: \"{{workflow.parameters.name}}\"}')}, 'DYNAMIC_REFERENCE')),
    ('read-only-ambiguous', lambda: read_only_client('read-only-ambiguous', {'library.yaml': LIBRARY, 'duplicate.yaml': LIBRARY}, 'AMBIGUOUS_BINDING')),
]
for name, run in CASES:
    try:
        checks, details = run()
        m.result(name, checks, details)
    except Exception as error:
        m.result(name, {'completed': False}, {'error': repr(error), 'traceback': traceback.format_exc()})
m.save(m.OUT / 'manifest.json', {'command': sys.argv, 'pythonVersion': sys.version, 'binarySha256': m.sha(m.BINARY), 'testSha256': m.sha(pathlib.Path(__file__)), 'helperSha256': m.sha(HELPER), 'expectedCases': [name for name, _ in CASES], 'inputHashes': {str(p.relative_to(m.OUT)): m.sha(p) for p in m.OUT.rglob('*') if p.is_file() and '/input/' in str(p)}, 'scope': 'CLI and stdio; no GUI acceptance'})
raise SystemExit(0 if len(m.RESULTS) == len(CASES) and all(row['pass'] for row in m.RESULTS) else 1)

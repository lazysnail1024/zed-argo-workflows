#!/usr/bin/env python3
"""Resource Rename through real stdio, with independent byte/binding expectations."""
import importlib.util
import json
import pathlib
import sys
import time
import traceback

HELPER = pathlib.Path(__file__).with_name('wf-audit-gaps.py')
spec = importlib.util.spec_from_file_location('wire', HELPER)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

CONFIG = 'schemaVersion: 1\ntargetVersion: "3.6.0"\nreferencePolicy: closed\n'
BASE = '''apiVersion: argoproj.io/v1alpha1
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
  entrypoint: main
  templates:
    - name: main
      dag:
        tasks:
          - name: run
            templateRef: {name: library, template: work}
'''
WHOLE = '''apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata: {name: whole, namespace: argo}
spec:
  workflowTemplateRef: {name: library}
'''
CRON = CALLER.replace('kind: Workflow\n', 'kind: CronWorkflow\n').split('spec:\n', 1)[0] + 'spec:\n  schedule: "* * * * *"\n  workflowSpec:\n' + ''.join('  ' + line for line in CALLER.split('spec:\n', 1)[1].splitlines(keepends=True))
STEPS = CALLER.replace('      dag:\n        tasks:\n          - name: run', '      steps:\n        - - name: run')
HOOKS = CALLER.replace('  entrypoint: main\n', '  entrypoint: main\n  hooks:\n    exit: {templateRef: {name: library, template: work}}\n')


def target(text, reference=False):
    return m.pos(text, 'name: library', offset=6) if not reference else m.pos(text, 'templateRef: {name: library', offset=20)


def wait_prepare(client, params):
    deadline = time.monotonic() + 5
    while True:
        response = client.request('textDocument/prepareRename', params)
        if 'incomplete' not in response.get('error', {}).get('message', '') or time.monotonic() >= deadline:
            return response
        time.sleep(.02)


def apply_all(files, work, result, versions):
    changes = result['documentChanges']
    seen, after = set(), dict(files)
    for change in changes:
        uri = change['textDocument']['uri']
        relative = next(name for name in files if (work / name).as_uri() == uri)
        assert relative not in seen, 'duplicate document edit'
        seen.add(relative)
        after[relative] = m.apply(files[relative], {'documentChanges': [change]}, uri, versions[relative])
    return after, seen


def success(name, files=None, config=CONFIG, selected='library.yaml', new_name='renamed', reference=False, unchanged=()):
    files = files or {'library.yaml': BASE, 'caller.yaml': CALLER}
    dest, work = m.setup(name, files, config)
    client = m.LSP(dest, work)
    checks, details = {}, {}
    try:
        versions = {file: 3 + n for n, file in enumerate(files)}
        for file, text in files.items():
            client.open(work / file, text, versions[file])
        params = {'textDocument': {'uri': (work / selected).as_uri()}, 'position': target(files[selected], reference)}
        prepared = wait_prepare(client, params)
        checks['prepareName'] = prepared.get('result', {}).get('placeholder') == 'library'
        response = client.request('textDocument/rename', params | {'newName': new_name})
        details['prepare'], details['rename'] = prepared, response
        checks['renameProvided'] = 'result' in response and 'error' not in response
        if not checks['renameProvided']:
            return checks, details
        after, affected = apply_all(files, work, response['result'], versions)
        expected = {file: text if file in unchanged else text.replace('name: library', 'name: ' + json.dumps(new_name))
                    for file, text in files.items()}
        checks['exactBytes'] = after == expected
        checks['affectedFiles'] = affected == {file for file in files if files[file] != expected[file]}
        for file in affected:
            client.send('textDocument/didChange', {'textDocument': {'uri': (work / file).as_uri(), 'version': versions[file] + 1},
                                                 'contentChanges': [{'text': after[file]}]})
        # The final request reacquires the complete changed input; a transient
        # diagnostic between client edits is not evidence of the final batch.
        source = next(file for file in affected if 'templateRef' in after[file] or 'workflowTemplateRef' in after[file])
        point = m.pos(after[source], 'name: ' + json.dumps(new_name), offset=7)
        deadline = time.monotonic() + 3
        while True:
            definition = client.request('textDocument/definition', {'textDocument': {'uri': (work / source).as_uri()}, 'position': point})
            if definition.get('result') or time.monotonic() >= deadline:
                break
            time.sleep(.02)
        details['definitionAfter'] = definition
        checks['bindingPreserved'] = len(definition.get('result', [])) == 1 and definition['result'][0]['uri'] == (work / 'library.yaml').as_uri()
        checks['diskUntouched'] = all((work / file).read_bytes() == text.encode() for file, text in files.items())
        return checks, details
    finally:
        client.close()


def refused(name, files=None, config=CONFIG, opened=None, selected='library.yaml', new_name='renamed', reference=False, prepare=True):
    files = files or {'library.yaml': BASE, 'caller.yaml': CALLER}
    dest, work = m.setup(name, files, config)
    client = m.LSP(dest, work)
    checks, details = {}, {}
    try:
        for file in files if opened is None else opened:
            client.open(work / file, files[file])
        params = {'textDocument': {'uri': (work / selected).as_uri()}, 'position': target(files[selected], reference)}
        before = wait_prepare(client, params)
        response = client.request('textDocument/rename', params | {'newName': new_name})
        details['prepare'], details['rename'] = before, response
        if prepare:
            checks['prepareRefused'] = before.get('error', {}).get('code') == -32602 and 'result' not in before
        checks['renameRefused'] = response.get('error', {}).get('code') == -32602 and 'result' not in response
        checks['diskUntouched'] = all((work / file).read_bytes() == text.encode() for file, text in files.items())
        return checks, details
    finally:
        client.close()


def closed_after_prepare():
    files = {'library.yaml': BASE, 'caller.yaml': CALLER}
    dest, work = m.setup('closed-after-prepare', files, CONFIG)
    client = m.LSP(dest, work)
    try:
        for file, text in files.items():
            client.open(work / file, text)
        params = {'textDocument': {'uri': (work / 'library.yaml').as_uri()}, 'position': target(BASE)}
        prepared = wait_prepare(client, params)
        client.send('textDocument/didClose', {'textDocument': {'uri': (work / 'caller.yaml').as_uri()}})
        response = client.request('textDocument/rename', params | {'newName': 'renamed'})
        return {'prepareWorked': 'result' in prepared, 'closedFileRefused': response.get('error', {}).get('code') == -32602}, {'prepare': prepared, 'rename': response}
    finally:
        client.close()


def changed_after_prepare():
    files = {'library.yaml': BASE, 'caller.yaml': CALLER}
    dest, work = m.setup('changed-after-prepare', files, CONFIG)
    client = m.LSP(dest, work)
    try:
        for file, text in files.items():
            client.open(work / file, text)
        params = {'textDocument': {'uri': (work / 'library.yaml').as_uri()}, 'position': target(BASE)}
        prepared = wait_prepare(client, params)
        caller = CALLER + '---\n' + WHOLE.replace('name: library', 'name: renamed')
        client.change(work / 'caller.yaml', caller, 2)
        response = client.request('textDocument/rename', params | {'newName': 'renamed'})
        return {'prepareWorked': 'result' in prepared, 'newUnsavedCaptureRefused': response.get('error', {}).get('code') == -32602}, {'prepare': prepared, 'rename': response}
    finally:
        client.close()


cases = [
    ('declaration', lambda: success('declaration')),
    ('automatic-version', lambda: success('automatic-version', config=CONFIG.replace('"3.6.0"', '"auto"'))),
    ('named-scope-isolation', lambda: success('named-scope-isolation', {'library.yaml': BASE, 'caller.yaml': CALLER, 'other/duplicate.yaml': BASE}, CONFIG + 'scopes:\n  - name: main\n    include: ["library.yaml", "caller.yaml"]\n  - name: other\n    include: ["other/**"]\n', unchanged=('other/duplicate.yaml',))),
    ('reference', lambda: success('reference', selected='caller.yaml', reference=True)),
    ('whole-workflow', lambda: success('whole-workflow', {'library.yaml': BASE, 'caller.yaml': WHOLE})),
    ('cron-workflow', lambda: success('cron-workflow', {'library.yaml': BASE, 'caller.yaml': CRON})),
    ('step-reference', lambda: success('step-reference', {'library.yaml': BASE, 'caller.yaml': STEPS})),
    ('hook-reference', lambda: success('hook-reference', {'library.yaml': BASE, 'caller.yaml': HOOKS})),
    ('cluster', lambda: success('cluster', {'library.yaml': BASE.replace('kind: WorkflowTemplate', 'kind: ClusterWorkflowTemplate').replace(', namespace: argo', ''), 'caller.yaml': CALLER.replace('template: work}', 'template: work, clusterScope: true}')})),
    ('default-namespace', lambda: success('default-namespace', {k: v.replace(', namespace: argo', '') for k, v in {'library.yaml': BASE, 'caller.yaml': CALLER}.items()}, CONFIG + 'defaultNamespace: argo\n')),
    ('namespace-isolation', lambda: success('namespace-isolation', {'library.yaml': BASE, 'caller.yaml': CALLER, 'other.yaml': BASE.replace('namespace: argo', 'namespace: other')}, unchanged=('other.yaml',))),
    ('numeric-string', lambda: success('numeric-string', new_name='123')),
    ('boolean-string', lambda: success('boolean-string', new_name='on')),
    ('crlf-utf16-stream', lambda: success('crlf-utf16-stream', {'library.yaml': ('# 🚀 中文\n' + BASE + '---\napiVersion: v1\nkind: ConfigMap\nmetadata: {name: untouched}\n').replace('\n', '\r\n'), 'caller.yaml': ('# 🚀 中文\n' + CALLER + '---\n' + WHOLE).replace('\n', '\r\n')})),
    ('closed-file', lambda: refused('closed-file', opened=['library.yaml'])),
    ('open-policy', lambda: refused('open-policy', config=CONFIG.replace('referencePolicy: closed', 'referencePolicy: open'))),
    ('duplicate-definition', lambda: refused('duplicate-definition', {'library.yaml': BASE, 'caller.yaml': CALLER, 'duplicate.yaml': BASE})),
    ('name-conflict', lambda: refused('name-conflict', {'library.yaml': BASE, 'caller.yaml': CALLER, 'conflict.yaml': BASE.replace('name: library', 'name: renamed')}, prepare=False)),
    ('unresolved-capture', lambda: refused('unresolved-capture', {'library.yaml': BASE, 'caller.yaml': CALLER, 'unresolved.yaml': WHOLE.replace('name: library', 'name: renamed')}, prepare=False)),
    ('unknown-namespace', lambda: refused('unknown-namespace', {k: v.replace(', namespace: argo', '') for k, v in {'library.yaml': BASE, 'caller.yaml': CALLER}.items()})),
    ('dynamic-namespace', lambda: refused('dynamic-namespace', {k: v.replace('namespace: argo', 'namespace: "{{workflow.namespace}}"') for k, v in {'library.yaml': BASE, 'caller.yaml': CALLER}.items()})),
    ('dynamic-reference', lambda: refused('dynamic-reference', {'library.yaml': BASE, 'caller.yaml': CALLER, 'dynamic.yaml': WHOLE.replace('name: library', 'name: "{{workflow.parameters.name}}"')})),
    ('derived-reference', lambda: refused('derived-reference', {'library.yaml': BASE, 'caller.yaml': CALLER.replace('  entrypoint: main', '  entrypoint: main\n  arguments: {parameters: [{name: chosen, value: &library library}]}').replace('name: library, template:', 'name: *library, template:')})),
    ('shared-declaration', lambda: refused('shared-declaration', {'library.yaml': BASE.replace('name: library,', 'name: &shared library,').replace('  entrypoint: work', '  arguments: {parameters: [{name: selected, value: *shared}]}\n  entrypoint: work'), 'caller.yaml': CALLER}, selected='caller.yaml', reference=True)),
    ('invalid-name', lambda: refused('invalid-name', new_name='Invalid_Name', prepare=False)),
    ('incomplete-peer', lambda: refused('incomplete-peer', {'library.yaml': BASE, 'caller.yaml': CALLER, 'broken.yaml': 'apiVersion: [\n'})),
    ('inline-reference', lambda: refused('inline-reference', {'library.yaml': BASE, 'caller.yaml': CALLER.replace('            templateRef:', '            inline:\n              dag: {tasks: [{name: nested, templateRef: {name: library, template: work}}]}\n            templateRef:')})),
    ('schema-only-peer', lambda: refused('schema-only-peer', {'library.yaml': BASE, 'caller.yaml': CALLER, 'binding.yaml': 'apiVersion: argoproj.io/v1alpha1\nkind: WorkflowEventBinding\nmetadata: {name: event, namespace: argo}\nspec: {event: {selector: "true"}, submit: {workflowTemplateRef: {name: library}}}\n'})),
    ('closed-after-prepare', closed_after_prepare),
    ('changed-after-prepare', changed_after_prepare),
]
selected = sys.argv[4].split(',') if len(sys.argv) == 5 and sys.argv[3] == '--cases' else [name for name, _ in cases]
if not selected or set(selected) - {name for name, _ in cases}:
    raise SystemExit('unknown or empty cases')
for name, run in cases:
    if name not in selected:
        continue
    try:
        checks, details = run()
        m.result(name, checks, details)
    except Exception as error:
        m.result(name, {'completed': False}, {'error': repr(error), 'traceback': traceback.format_exc()})
m.save(m.OUT / 'manifest.json', {'command': sys.argv, 'binarySha256': m.sha(m.BINARY), 'testSha256': m.sha(pathlib.Path(__file__)),
                                'helperSha256': m.sha(HELPER), 'expectedCases': selected})
raise SystemExit(0 if len(m.RESULTS) == len(selected) and all(row['pass'] for row in m.RESULTS) else 1)

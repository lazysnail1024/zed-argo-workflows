#!/usr/bin/env python3
"""Independent stdio contract for bounded, read-only local Definition recovery.

Expected locations come from tagged input bytes, never from server results or
production imports. Evidence is retained even when the expected recovery fails.
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

SIMPLE = '''apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata:
  name: demo
spec:
  entrypoint: <entry>main</entry>
  templates:
    - name: <main>main</main>
      container: {image: alpine}
'''
CALLS = '''apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata: {name: local-calls}
spec:
  entrypoint: <entry>main</entry>
  onExit: <exit>cleanup</exit>
  templates:
    - name: <main>main</main>
      dag:
        tasks:
          - name: run
            template: <call>worker</call>
            onExit: <task-exit>cleanup</task-exit>
    - name: <worker>worker</worker>
      container: {image: alpine}
    - name: <cleanup>cleanup</cleanup>
      container: {image: alpine}
  hooks:
    exit:
      template: <workflow-hook>cleanup</workflow-hook>
'''
UTF16 = SIMPLE.replace(
    '    - name: <main>main</main>\n      container: {image: alpine}',
    '    - {container: {image: alpine, args: ["\U0001f680"]}, name: <main>main</main>}')
MULTI = SIMPLE.replace('<entry>', '<first-entry>').replace('</entry>', '</first-entry>').replace(
    '<main>', '<first-main>').replace('</main>', '</first-main>') + '---\n' + SIMPLE.replace(
    'name: demo', 'name: second')
CASES = [
    {'name': 'same-session-tail-sequence', 'marked': SIMPLE,
     'tail': '  arguments: [\n', 'queries': [('entry', 'main')], 'positive': True},
    {'name': 'same-session-local-calls', 'marked': CALLS,
     'tail': '  arguments: [{\n', 'queries': [('entry', 'main'), ('exit', 'cleanup'),
                                            ('call', 'worker'), ('task-exit', 'cleanup'),
                                            ('workflow-hook', 'cleanup')],
     'positive': True},
    {'name': 'crlf-utf16-tail-mapping', 'marked': UTF16.replace('\n', '\r\n'),
     'tail': '  arguments: {\r\n', 'queries': [('entry', 'main')], 'positive': True},
    {'name': 'multi-document-tail', 'marked': MULTI,
     'tail': '  arguments: [\n', 'queries': [('first-entry', 'first-main'), ('entry', 'main')],
     'positive': True},
    {'name': 'damaged-template-scope', 'marked': SIMPLE,
     'tail': '      inputs: {\n', 'queries': [('entry', 'main')], 'positive': False},
    {'name': 'damaged-template-declaration', 'marked': SIMPLE.split('  templates:')[0] +
        '  templates: [{name: <main>main</main>\n',
     'tail': '', 'queries': [('entry', 'main')], 'positive': False},
    {'name': 'damaged-call', 'marked': SIMPLE.replace(
        'entrypoint: <entry>main</entry>', 'entrypoint: [<entry>main</entry>'),
     'tail': '', 'queries': [('entry', 'main')], 'positive': False},
    {'name': 'damaged-workflow-scope', 'marked': SIMPLE.split('spec:')[0] +
        'spec: {entrypoint: <entry>main</entry>, templates: [{name: <main>main</main>, container: {image: alpine}}]\n',
     'tail': '', 'queries': [('entry', 'main')], 'positive': False},
    {'name': 'damaged-hook-call-scope', 'marked': SIMPLE +
        '  hooks: {exit: {template: <hook-call>main</hook-call>\n',
     'tail': '', 'queries': [('hook-call', 'main')], 'positive': False},
    {'name': 'duplicate-template-key', 'marked': SIMPLE.replace('  templates:\n',
        '  templates: [{name: other, container: {image: alpine}}]\n  templates:\n'),
     'tail': '  arguments: [\n', 'queries': [('entry', 'main')], 'positive': False},
    {'name': 'duplicate-template-name', 'marked': SIMPLE +
        '    - name: main\n      container: {image: alpine}\n',
     'tail': '  arguments: [\n', 'queries': [('entry', 'main')], 'positive': False},
    {'name': 'encoded-duplicate-name', 'marked': SIMPLE +
        '    - name: "ma\\u0069n"\n      container: {image: alpine}\n',
     'tail': '  arguments: [\n', 'queries': [('entry', 'main')], 'positive': False},
    {'name': 'alias-duplicate-template', 'marked': SIMPLE.replace(
        '    - name: <main>main</main>\n      container: {image: alpine}',
        '    - &shared {name: <main>main</main>, container: {image: alpine}}\n    - *shared'),
     'tail': '  arguments: [\n', 'queries': [('entry', 'main')], 'positive': False},
    {'name': 'merge-derived-template', 'marked': SIMPLE.replace('spec:\n',
        'x-template: &shared {name: <derived>main</derived>, container: {image: alpine}}\nspec:\n').replace(
        '    - name: <main>main</main>\n      container: {image: alpine}', '    - <<: *shared'),
     'tail': '  arguments: [\n', 'queries': [('entry', 'derived')], 'positive': False},
    {'name': 'inherited-workflow-entrypoint', 'marked': SIMPLE.replace('spec:\n',
        'spec:\n  workflowTemplateRef: {name: base}\n'),
     'tail': '  arguments: [\n', 'queries': [('entry', 'main')], 'positive': False},
    {'name': 'dynamic-entrypoint', 'marked': SIMPLE.replace('<entry>main</entry>',
        '"<entry>{{workflow.parameters.which}}</entry>"'),
     'tail': '  arguments: [\n', 'queries': [('entry', 'main')], 'positive': False},
]
POSITIVE_ASSERTIONS = (
    'validExactDefinition', 'damagedExactDefinition', 'restoredExactDefinition',
    'validExactReferences', 'restoredExactReferences',
    'damagedReferencesRefused', 'referencesRemainIncomplete', 'documentRemainsIncomplete',
    'originalBufferIdentity', 'prepareRenameRefused', 'renameRefused',
    'quickFixWithheld', 'diskUnchanged', 'cleanLifecycle',
)
NEGATIVE_ASSERTIONS = ('noInventedDefinition', 'diskUnchanged', 'cleanLifecycle')


def save(path, value):
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))


# Keep inherited wire evidence byte-identical on hosts with non-UTF-8 locales.
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


def request(client, path, method, position, **extra):
    return client.request(method, {'textDocument': {'uri': path.as_uri()},
                                   'position': position, **extra})


def definitions(client, path, spans, queries):
    return [request(client, path, 'textDocument/definition', spans[source]['start'])
            for source, _ in queries]


def exact(responses, expected):
    return len(responses) == len(expected) and all(
        'error' not in response and response.get('result') == locations
        for response, locations in zip(responses, expected))


def refused(response, code):
    return response.get('error', {}).get('code') == code and 'result' not in response


def complete_references(client, path, spans, queries):
    position = spans[queries[0][1]]['start']
    deadline = time.monotonic() + 10
    while True:
        response = request(client, path, 'argo-workflows/explainAnalysis', position)
        if response.get('result', {}).get('completeness') == {'document': True, 'workspace': True}:
            break
        if 'error' in response or time.monotonic() >= deadline:
            raise AssertionError('complete control did not become ready: ' + repr(response))
        time.sleep(0.02)
    return request(client, path, 'textDocument/references', position,
                   context={'includeDeclaration': False})


def run(case):
    text, spans = strip_tags(case['marked'])
    damaged = text + case['tail']
    dest, work = m.setup(case['name'], {'case.yaml': text})
    path = work / 'case.yaml'
    expected = [[{'uri': path.as_uri(), 'range': spans[target]}]
                for _, target in case['queries']]
    expected_references = [{'uri': path.as_uri(), 'range': spans[source]}
                           for source, target in case['queries'] if target == case['queries'][0][1]]
    # The oracle and exact request inputs exist before starting the child.
    for name, value in [('valid.yaml', text), ('damaged.yaml', damaged), ('restored.yaml', text)]:
        (dest / name).write_bytes(value.encode('utf-8'))
    save(dest / 'expected.json', {
        'queries': [{'position': spans[source]['start'], 'target': spans[target]}
                    for source, target in case['queries']],
        'validDefinitions': expected if case['positive'] else None,
        'damagedDefinitions': expected if case['positive'] else [[] for _ in expected],
        'restoredDefinitions': expected if case['positive'] else None,
        'validAndRestoredReferences': expected_references if case['positive'] else None,
        'damagedReferencesError': -32803, 'damagedCompleteness': {'document': False},
        'damagedQuickFixError': -32803, 'prepareRenameError': -32602, 'renameError': -32602,
    })
    checks = {key: False for key in
              (POSITIVE_ASSERTIONS if case['positive'] else NEGATIVE_ASSERTIONS)}
    details, client = {}, None
    try:
        client = m.LSP(dest, work)
        if case['positive']:
            details['validDiagnostics'] = client.open(path, text)
            details['validDefinitions'] = definitions(client, path, spans, case['queries'])
            checks['validExactDefinition'] = exact(details['validDefinitions'], expected)
            details['validReferences'] = complete_references(client, path, spans, case['queries'])
            checks['validExactReferences'] = exact([details['validReferences']], [expected_references])
            details['damagedDiagnostics'] = client.change(path, damaged, 2)
        else:
            details['damagedDiagnostics'] = client.open(path, damaged, 2)
        details['damagedDefinitions'] = definitions(client, path, spans, case['queries'])
        if case['positive']:
            checks['damagedExactDefinition'] = exact(details['damagedDefinitions'], expected)
            position = spans[case['queries'][0][0]]['start']
            details['references'] = request(client, path, 'textDocument/references', position,
                                            context={'includeDeclaration': False})
            details['explanation'] = request(client, path, 'argo-workflows/explainAnalysis', position)
            status = details['explanation'].get('result', {})
            checks['damagedReferencesRefused'] = refused(details['references'], -32803)
            checks['referencesRemainIncomplete'] = status.get('references', {}).get('complete') is False
            checks['documentRemainsIncomplete'] = status.get('completeness', {}).get('document') is False
            identity = status.get('inputIdentity', {})
            checks['originalBufferIdentity'] = (identity.get('source') == 'buffer' and
                identity.get('version') == 2 and identity.get('sha256') == hashlib.sha256(damaged.encode()).hexdigest())
            details['prepareRename'] = request(client, path, 'textDocument/prepareRename', position)
            details['rename'] = request(client, path, 'textDocument/rename', position, newName='renamed')
            checks['prepareRenameRefused'] = refused(details['prepareRename'], -32602)
            checks['renameRefused'] = refused(details['rename'], -32602)
            details['quickFixes'] = client.request('textDocument/codeAction', {
                'textDocument': {'uri': path.as_uri()}, 'range': spans[case['queries'][0][0]],
                'context': {'diagnostics': details['damagedDiagnostics'], 'only': ['quickfix']}})
            checks['quickFixWithheld'] = refused(details['quickFixes'], -32803)
            details['restoredDiagnostics'] = client.change(path, text, 3)
            details['restoredDefinitions'] = definitions(client, path, spans, case['queries'])
            checks['restoredExactDefinition'] = exact(details['restoredDefinitions'], expected)
            details['restoredReferences'] = complete_references(client, path, spans, case['queries'])
            checks['restoredExactReferences'] = exact([details['restoredReferences']], [expected_references])
        else:
            checks['noInventedDefinition'] = exact(details['damagedDefinitions'], [[] for _ in expected])
    except Exception:
        details['error'] = traceback.format_exc()
    finally:
        checks['diskUnchanged'] = path.read_bytes() == text.encode('utf-8')
        if client:
            try:
                client.close()
                checks['cleanLifecycle'] = 'error' not in details
            except Exception:
                details['closeError'] = traceback.format_exc()
        m.result(case['name'], checks, details)


source = m.OUT / 'source'
source.mkdir(parents=True, exist_ok=True)
for filename in ('wf-definition-recovery.py', 'wf-audit-gaps.py'):
    (source / filename).write_bytes((HERE / filename).read_bytes())
for case in CASES:
    try:
        run(case)
    except Exception:
        m.result(case['name'], {key: False for key in
                 (POSITIVE_ASSERTIONS if case['positive'] else NEGATIVE_ASSERTIONS)},
                 {'error': traceback.format_exc()})
head = subprocess.run(['git', '-C', str(HERE.parent.parent), 'rev-parse', 'HEAD'],
                      capture_output=True, text=True, check=False)
save(m.OUT / 'manifest.json', {
    'command': sys.argv, 'caseCount': len(CASES), 'sourceHead': head.stdout.strip(),
    'binarySHA256': m.sha(m.BINARY), 'consumerSHA256': m.sha(Path(__file__)),
    'helperSHA256': m.sha(HERE / 'wf-audit-gaps.py'),
    'inputHashes': {str(path.relative_to(m.OUT)): m.sha(path)
                    for path in sorted(m.OUT.rglob('*.yaml'))},
    'expectedHashes': {str(path.relative_to(m.OUT)): m.sha(path)
                       for path in sorted(m.OUT.rglob('expected.json'))},
})
sys.exit(0 if len(m.RESULTS) == len(CASES) and all(row['pass'] for row in m.RESULTS) else 1)

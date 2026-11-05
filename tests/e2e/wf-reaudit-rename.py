#!/usr/bin/env python3
"""R-01: metadata text is data; real Argo references retain exact bindings.

Only the stdio protocol/fixture helpers are shared with the older audit. The
[[name]] markers below declare the expected edit and reference locations in
the fixtures; no production parser or binding result constructs the oracle.
"""
import argparse
import collections
import importlib.util
import json
import pathlib
import re
import sys


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('binary')
parser.add_argument('output')
parser.add_argument('--cases', help='comma-separated, nonempty exact case names')
args = parser.parse_args()
selected = None if args.cases is None else args.cases.split(',')
if selected is not None and (not all(selected) or len(set(selected)) != len(selected)):
    parser.error('--cases requires nonempty, unique exact case names')

helper_path = pathlib.Path(__file__).with_name('wf-audit-gaps.py')
spec = importlib.util.spec_from_file_location('wire', helper_path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


GLOBAL = '''apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata:
  name: rename-metadata
  namespace: argo
__METADATA__spec:
  entrypoint: main
  arguments:
    parameters: [{name: [[message]], value: hello}]
  templates:
    - name: main
__TEMPLATE_METADATA__      container:
        image: alpine
        args: ["{{workflow.parameters.[[message]]}}"]
'''


def global_fixture(metadata='', template_metadata=''):
    return GLOBAL.replace('__METADATA__', metadata).replace('__TEMPLATE_METADATA__', template_metadata)


CASES = []


def case(name, text, needle='name: message', new_name='renamed', offset=6, refused=False):
    CASES.append({'name': name, 'marked': text, 'needle': needle, 'offset': offset, 'newName': new_name, 'refused': refused})


for name, key in [('expression', 'expression'), ('from-expression', 'fromExpression'), ('from', 'from'), ('parameter', 'parameter')]:
    case('metadata-' + name + '-literal', global_fixture('  annotations:\n    ' + key + ': "workflow.parameters.message"\n    description: "workflow.parameters.message"\n'))

literal_keys = ''.join('    ' + key + ': "workflow.parameters.message"\n' for key in ('expression', 'fromExpression', 'from', 'parameter'))
case('metadata-labels-literal', global_fixture('  labels:\n' + literal_keys))
case('template-metadata-literal', global_fixture(template_metadata='      metadata:\n        annotations:\n' + ''.join('      ' + line for line in literal_keys.splitlines(keepends=True))))

prose = global_fixture('  annotations:\n' + ''.join('    ' + key + ': "human readable prose"\n' for key in ('expression', 'fromExpression', 'from', 'parameter')))
prose = prose.replace('[[message]]', 'message').replace('entrypoint: main', 'entrypoint: [[main]]').replace('name: main', 'name: [[main]]')
case('metadata-prose', prose, needle='name: main', new_name='tidy')

case('metadata-tagged', global_fixture('''  annotations:
    expression: "{{workflow.parameters.[[message]]}}"
    fromExpression: "{{=workflow.parameters.[[message]]}}"
    from: "{{workflow.parameters.[[message]]}}"
    parameter: "{{workflow.parameters.[[message]]}}"
'''))
case('metadata-unicode-crlf', global_fixture('''  annotations:
    expression: "🧪 workflow.parameters.message"
    note: "🧪 {{workflow.parameters.[[message]]}}"
''').replace('\n', '\r\n'))

INPUT_PARAMETER = '''apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata: {name: input-parameter, namespace: argo}
spec:
  entrypoint: main
  templates:
    - name: main
      inputs:
        parameters: [{name: [[message]], default: hello}]
      outputs:
        parameters:
          - name: raw
            valueFrom: {parameter: inputs.parameters.[[message]]}
          - name: expression
            valueFrom: {expression: "inputs.parameters.[[message]] + 'inputs.parameters.message'"}
      container: {image: alpine, args: ["{{inputs.parameters.[[message]]}}"]}
'''
case('input-parameter-raw', INPUT_PARAMETER)
case('input-artifact-raw', '''apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata: {name: input-artifact, namespace: argo}
spec:
  entrypoint: main
  templates:
    - name: main
      inputs:
        artifacts: [{name: [[file]], raw: {data: hello}}]
      outputs:
        artifacts:
          - name: raw
            from: inputs.artifacts.[[file]]
          - name: expression
            fromExpression: "inputs.artifacts.[[file]]"
      container: {image: alpine, args: ["{{inputs.artifacts.[[file]].path}}"]}
''', needle='name: file')
case('output-parameter-raw', '''apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata: {name: output-parameter, namespace: argo}
spec:
  entrypoint: main
  templates:
    - name: main
      dag:
        tasks: [{name: produce, template: worker}]
      outputs:
        parameters:
          - name: raw
            valueFrom: {parameter: tasks.produce.outputs.parameters.[[answer]]}
          - name: expression
            valueFrom: {expression: "tasks['produce'].outputs.parameters['[[answer]]']"}
    - name: worker
      outputs:
        parameters: [{name: [[answer]], valueFrom: {path: /tmp/value}}]
      container: {image: alpine, args: ["{{outputs.parameters.[[answer]].path}}"]}
''', needle='name: answer')
case('output-artifact-raw', '''apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata: {name: output-artifact, namespace: argo}
spec:
  entrypoint: main
  templates:
    - name: main
      dag:
        tasks:
          - name: produce
            template: worker
          - name: consume
            template: reader
            dependencies: [produce]
            arguments:
              artifacts: [{name: file, from: tasks.produce.outputs.artifacts.[[product]]}]
      outputs:
        artifacts:
          - name: raw
            from: tasks.produce.outputs.artifacts.[[product]]
          - name: expression
            fromExpression: "tasks['produce'].outputs.artifacts['[[product]]']"
    - name: worker
      outputs:
        artifacts: [{name: [[product]], path: /tmp/data}]
      container: {image: alpine, args: ["{{outputs.artifacts.[[product]].path}}"]}
    - name: reader
      inputs: {artifacts: [{name: file, path: /tmp/input}]}
      container: {image: alpine}
''', needle='name: product')

lifecycle = global_fixture().replace('  templates:\n', '''  retryStrategy: {limit: "2", expression: "workflow.parameters.[[message]] == 'retry'"}
  workflowMetadata:
    labelsFrom:
      result: {expression: "workflow.parameters.[[message]]"}
  hooks:
    running:
      expression: "workflow.parameters.[[message]] == 'hello'"
      template: notify
  templates:
''').replace('    - name: main\n', '''    - name: main
      retryStrategy: {limit: "2", expression: "workflow.parameters.[[message]] == 'retry'"}
''') + '''    - name: notify
      container: {image: alpine}
'''
case('lifecycle-retry-expressions', lifecycle)
case('data-transformation-expression', '''apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata: {name: data-expression, namespace: argo}
spec:
  entrypoint: main
  arguments:
    parameters: [{name: [[message]], value: hello}]
  templates:
    - name: main
      data:
        source:
          artifactPaths:
            name: source
            s3: {endpoint: minio:9000, bucket: inputs, key: values}
        transformation:
          - expression: "map(data, {# + workflow.parameters.[[message]]})"
''')
prefix, body = INPUT_PARAMETER.split('spec:\n', 1)
case('cron-raw-fields', prefix.replace('kind: Workflow', 'kind: CronWorkflow') + 'spec:\n  schedule: "0 * * * *"\n  workflowSpec:\n' + ''.join('  ' + line for line in body.splitlines(keepends=True)))
case('malformed-raw-expression', INPUT_PARAMETER.replace("inputs.parameters.[[message]] + 'inputs.parameters.message'", 'inputs.parameters.[[message]] +'), refused=True)

inventory = {item['name'] for item in CASES}
if selected is not None and set(selected) - inventory:
    parser.error('unknown --cases: ' + ', '.join(sorted(set(selected) - inventory)))
cases = [item for item in CASES if selected is None or item['name'] in selected]
if not cases:
    parser.error('at least one case is required')


def position(text, offset):
    head = text[:offset]
    return {'line': head.count('\n'), 'character': len(head.rsplit('\n', 1)[-1].encode('utf-16-le')) // 2}


def oracle(marked, replacement):
    original, changed, spans, previous = '', '', [], 0
    for match in re.finditer(r'\[\[([a-z][a-z0-9-]*)\]\]', marked):
        prefix = marked[previous:match.start()]
        original += prefix
        changed += prefix
        start = len(original)
        original += match.group(1)
        changed += replacement
        spans.append((start, len(original)))
        previous = match.end()
    original += marked[previous:]
    changed += marked[previous:]
    if not spans:
        raise ValueError('fixture has no explicitly marked occurrences')
    edits = [{'range': {'start': position(original, start), 'end': position(original, end)}, 'newText': replacement} for start, end in spans]
    return original, changed, edits


def canonical(values):
    return sorted(json.dumps(value, sort_keys=True) for value in values)


def error_set(diagnostics):
    return collections.Counter(canonical(d for d in diagnostics if d.get('severity') == 1))


def versioned_edits(response, uri, version):
    value = response.get('result')
    if 'error' in response or not isinstance(value, dict):
        return None
    changes = value.get('documentChanges')
    if not isinstance(changes, list) or len(changes) != 1 or changes[0].get('textDocument') != {'uri': uri, 'version': version}:
        return None
    edits = changes[0].get('edits')
    return edits if isinstance(edits, list) and edits else None


for item in cases:
    text, expected, expected_edits = oracle(item['marked'], item['newName'])
    dest, work = m.setup(item['name'], {'case.yaml': text})
    path = work / 'case.yaml'
    uri = path.as_uri()
    client = m.LSP(dest, work)
    params = {'textDocument': {'uri': uri}, 'position': m.pos(text, item['needle'], offset=item['offset'])}
    try:
        before = client.open(path, text)
        prepare = client.request('textDocument/prepareRename', params)
        response = client.request('textDocument/rename', params | {'newName': item['newName']})
        details = {'beforeDiagnostics': before, 'prepare': prepare, 'response': response, 'expected': expected, 'expectedEdits': expected_edits}
        checks = {'disk_unchanged': path.read_bytes() == text.encode('utf-8')}
        if item['refused']:
            checks.update({
                'prepare_refused': prepare.get('error', {}).get('code') == -32602 and 'result' not in prepare,
                'rename_refused': response.get('error', {}).get('code') == -32602 and 'result' not in response,
            })
        else:
            references = client.request('textDocument/references', params | {'context': {'includeDeclaration': True}})
            expected_references = [{'uri': uri, 'range': edit['range']} for edit in expected_edits]
            selected_range = next(edit['range'] for edit in expected_edits if edit['range']['start'] == params['position'])
            edits = versioned_edits(response, uri, 1)
            checks.update({
                'baseline_has_no_errors': not error_set(before),
                'prepare_exact': prepare.get('result', {}).get('range') == selected_range and 'error' not in prepare,
                'exact_references': isinstance(references.get('result'), list) and 'error' not in references and canonical(references['result']) == canonical(expected_references),
                'versioned_edit': edits is not None,
                'exact_edits': edits is not None and canonical(edits) == canonical(expected_edits),
                'exact_bytes': False,
                'no_new_errors': False,
                'undo_restores': False,
            })
            details.update({'references': references, 'expectedReferences': expected_references})
            if edits is not None:
                actual = m.apply(text, response['result'], uri, 1)
                after = client.change(path, actual, 2)
                restored = client.change(path, text, 3)
                again = client.request('textDocument/rename', params | {'newName': item['newName']})
                restored_edits = versioned_edits(again, uri, 3)
                checks.update({
                    'exact_bytes': actual == expected,
                    'no_new_errors': not (error_set(after) - error_set(before)),
                    'undo_restores': restored == before and restored_edits is not None and canonical(restored_edits) == canonical(expected_edits) and m.apply(text, again['result'], uri, 3) == expected,
                })
                details.update({'actual': actual, 'afterDiagnostics': after, 'restoredDiagnostics': restored, 'restoredRename': again})
        m.result(item['name'], checks, details)
    finally:
        client.close()

m.save(m.OUT / 'manifest.json', {
    'command': sys.argv, 'binarySha256': m.sha(m.BINARY),
    'testSha256': m.sha(pathlib.Path(__file__)), 'helperSha256': m.sha(helper_path),
    'selectedCases': [item['name'] for item in cases],
    'inputs': {str(path.relative_to(m.OUT)): m.sha(path) for path in m.OUT.rglob('*.yaml')},
})
sys.exit(0 if len(m.RESULTS) == len(cases) and all(row['pass'] for row in m.RESULTS) else 1)

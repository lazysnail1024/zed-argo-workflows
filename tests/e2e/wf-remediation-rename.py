#!/usr/bin/env python3
"""Local template closure remains independent of a static external call."""
import collections
import importlib.util
import json
import pathlib
import sys

p = pathlib.Path(__file__).with_name('wf-audit-gaps.py')
spec = importlib.util.spec_from_file_location('wire', p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def errors(diagnostics):
    # Counts alone miss one Error being replaced by a different Error. Include
    # code, range, message and multiplicity in the comparison.
    return collections.Counter(json.dumps(d, sort_keys=True) for d in diagnostics if d.get('severity') == 1)


def edit_result(response):
    return 'error' not in response and isinstance(response.get('result'), dict) and 'documentChanges' in response['result']


base = m.REN_DAG.replace('    - name: work\n', '          - name: external\n            templateRef: {name: base, template: work}\n    - name: work\n')
cases = [
    ('independent', base, m.BASE, True),
    ('namespace', base, m.BASE.replace('namespace: argo', 'namespace: other'), True),
    ('public-template', m.BASE, m.BASE, False),
    ('dynamic-external', base.replace('name: base, template:', 'name: "{{workflow.name}}", template:'), m.BASE, False),
    ('inherited', base.replace('  entrypoint: main', '  workflowTemplateRef: {name: base}\n  entrypoint: main'), m.BASE, False),
    ('inline', base.replace('templateRef: {name: base, template: work}', 'inline: {container: {image: alpine}}'), m.BASE, False),
    ('alias', base.replace('container: {image: alpine, command: [echo], args: [work]}', 'container: &shared {image: alpine, command: [echo], args: [work]}').replace('container: {image: alpine, command: [echo], args: [cleanup]}', 'container: *shared'), m.BASE, False),
]
for name, text, callee, allowed in cases:
    dest, work = m.setup(name, {'a.yaml': text, 'library.yaml': callee})
    c = m.LSP(dest, work)
    path = work / 'a.yaml'
    needle = 'name: work' if name == 'public-template' else 'name: cleanup'
    params = {'textDocument': {'uri': path.as_uri()}, 'position': m.pos(text, needle, offset=7), 'newName': 'tidy'}
    try:
        before = c.open(path, text)
        response = c.request('textDocument/rename', params)
        checks = {'policy': edit_result(response) if allowed else response.get('error', {}).get('code') == -32602 and 'result' not in response}
        details = {'beforeDiagnostics': before, 'response': response}
        if allowed:
            # Keep the assertion inventory stable when a regression refuses a
            # supported operation; an absent edit must fail these checks.
            checks.update({'exact_local_closure': False, 'no_new_errors': False, 'external_untouched': False, 'undo_restores': False})
            if edit_result(response):
                actual = m.apply(text, response['result'], path.as_uri(), 1)
                expected = text.replace('onExit: cleanup', 'onExit: tidy').replace('name: cleanup', 'name: tidy')
                after = c.change(path, actual, 2)
                checks.update({
                    'exact_local_closure': actual == expected,
                    'no_new_errors': not (errors(after) - errors(before)),
                    'external_untouched': (work / 'library.yaml').read_bytes() == callee.encode('utf-8'),
                })
                restored = c.change(path, text, 3)
                again = c.request('textDocument/rename', params)
                checks['undo_restores'] = restored == before and edit_result(again) and m.apply(text, again['result'], path.as_uri(), 3) == expected
                details.update({'actual': actual, 'expected': expected, 'afterDiagnostics': after, 'restoredDiagnostics': restored, 'restoredRename': again})
        m.result(name, checks, details)
    finally:
        c.close()

m.save(m.OUT / 'manifest.json', {'command': sys.argv, 'binarySha256': m.sha(m.BINARY), 'testSha256': m.sha(pathlib.Path(__file__)), 'inputs': {str(p.relative_to(m.OUT)): m.sha(p) for p in m.OUT.rglob('*.yaml')}})
sys.exit(0 if len(m.RESULTS) == len(cases) and all(x['pass'] for x in m.RESULTS) else 1)

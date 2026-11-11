#!/usr/bin/env python3
"""Real LSP consumers for partial keys and original-source projection guards."""
from pathlib import Path
import importlib.util
import json
import sys
import traceback

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('wire', HERE / 'wf-audit-gaps.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
MARK = '<CURSOR>'
HEAD = 'apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: shared-consumer}\n'
RECOVERED = HEAD + 'spec: {parallelism: 2}\n'
CASES = [
    ('partial-root-key', HEAD + 'spec:\n  paral' + MARK + '\n', 'parallelism', None),
    ('partial-template-key', (HEAD + 'spec:\n  templates:\n    - name: main\n      container: {image: alpine}\n      outp' + MARK + '\n').replace('\n', '\r\n'), 'outputs', None),
    ('selected-duplicate-key', HEAD + 'spec:\n  parallelism: 1\n  paral' + MARK + 'lelism: 2\n', None, 'duplicate mapping key parallelism'),
    ('explicit-standard-tag', HEAD + 'spec:\n  paral' + MARK + 'lelism: !!int 2\n', None, 'explicit YAML tag is not evaluated'),
]

for name, marked, expected, refusal in CASES:
    text = marked.replace(MARK, '')
    dest, work = m.setup(name, {'case.yaml': text})
    path = work / 'case.yaml'
    client = None
    try:
        client = m.LSP(dest, work)
        client.open(path, text)
        response = client.request('textDocument/completion', {
            'textDocument': {'uri': path.as_uri()}, 'position': m.pos(marked, MARK)})
        if refusal:
            code, report = m.cli(dest, work)
            checks = {'explicitRefusal': response.get('error', {}).get('code') == -32803,
                      'noPartialResult': 'result' not in response,
                      'cliRefusal': code == 2 and report['analysisComplete'] is False and refusal in report['notEvaluated']}
        else:
            items = response.get('result') or []
            matches = [item for item in items if item.get('label') == expected]
            checks = {'usefulCompletion': len(matches) == 1, 'noProtocolError': 'error' not in response}
            if len(matches) == 1:
                edit = matches[0]['textEdit']
                actual = m.apply(text, {'documentChanges': [{'textDocument': {'uri': path.as_uri(), 'version': 1}, 'edits': [edit]}]}, path.as_uri(), 1)
                token = 'paral' if name == 'partial-root-key' else 'outp'
                checks['exactBytes'] = actual == text.replace(token + ('\r\n' if '\r\n' in text else '\n'), expected + ': ' + ('\r\n' if '\r\n' in text else '\n'))
                (dest / 'edited.yaml').write_bytes(actual.encode())
        # Every case must recover through normal didChange with a newer version.
        diagnostics = client.change(path, RECOVERED, 2)
        hover = client.request('textDocument/hover', {
            'textDocument': {'uri': path.as_uri()}, 'position': m.pos(RECOVERED, 'parallelism')})
        checks['recoveredDiagnostics'] = not any(d.get('severity') == 1 for d in diagnostics)
        checks['recoveredHover'] = bool(hover.get('result')) and 'error' not in hover
        m.result(name, checks, {'response': response, 'recoveredHover': hover})
    except Exception:
        m.result(name, {'completed': False}, {'error': traceback.format_exc()})
    finally:
        if client:
            client.close()

m.save(m.OUT / 'manifest.json', {'command': sys.argv, 'caseCount': len(CASES),
       'binarySHA256': m.sha(m.BINARY), 'consumerSHA256': m.sha(Path(__file__))})
sys.exit(0 if len(m.RESULTS) == len(CASES) and all(row['pass'] for row in m.RESULTS) else 1)

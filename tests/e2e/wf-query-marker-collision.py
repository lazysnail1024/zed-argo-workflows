#!/usr/bin/env python3
"""Valid same-parent annotation keys must not collide with artificial query tokens."""
from pathlib import Path
import importlib.util, json, sys, traceback

HERE = Path(__file__).resolve().parent
HELPER = HERE / 'wf-audit-gaps.py'
spec = importlib.util.spec_from_file_location('wire', HELPER)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
MARK = '<CURSOR>'
BASE = 'complete domain rules, version acceptance, and resource budgets are not yet implemented'

class Client(m.LSP):
    def send(self, method, params=None, ident=None):
        frame = {'jsonrpc': '2.0', 'method': method}
        if params is not None:
            frame['params'] = params
        if ident is not None:
            frame['id'] = ident
        raw = json.dumps(frame).encode()
        with (self.dest / 'lsp.stdin.raw').open('ab') as stream:
            stream.write(('Content-Length: %d\r\n\r\n' % len(raw)).encode() + raw)
        return super().send(method, params, ident)

cases = []
for name, sibling in [('same-parent-marker', '__argo_query_key__'), ('ordinary-key-control', 'ordinary_sibling'), ('quoted-equivalent-marker', "'__argo_query_key__'"), ('escaped-equivalent-marker', '"\\u005f_argo_query_key__"')]:
    marked = ('apiVersion: argoproj.io/v1alpha1\nkind: Workflow\n'
              'metadata: {name: marker-collision}\nspec:\n  podMetadata:\n    annotations:\n'
              '      ' + sibling + ': retained\n      ord<CURSOR>inary: retained\n')
    original = marked.replace(MARK, '')
    dest, work = m.setup(name, {'case.yaml': original})
    path = work / 'case.yaml'
    position = m.pos(marked, MARK)
    start = m.pos(original, 'ordinary:')
    end = dict(start, character=start['character'] + len('ordinary'))
    expected = {'cliExit': 0, 'cliEnvelope': {
        'diagnostics': [], 'configurationErrors': [], 'filesAnalyzed': 1,
        'resourcesAnalyzed': 1, 'analysisComplete': True, 'notEvaluated': []},
        'publication': [], 'hover': {'range': {'start': start, 'end': end},
        'contains': ['Type: `string`', '3.6.0', 'b26ed4aa4dee395844531efa4a76a022183bec22'],
        'protocolError': False}, 'sourceUnchanged': True}
    (dest / 'original.yaml').write_bytes(original.encode())
    m.save(dest / 'expected.json', expected)
    cases.append((name, original, dest, work, path, position, expected))

# Persist all independent inputs and expectations before either native process starts.
m.save(m.OUT / 'manifest.json', {'command': sys.argv, 'testSha256': m.sha(Path(__file__)),
    'helperSha256': m.sha(HELPER), 'nativeSha256': m.sha(m.BINARY), 'python': sys.version,
    'expectedCases': 4, 'rationale': 'The temporary marker must not become a duplicate user key in a valid typed annotations map.',
    'inputHashes': {str(dest.relative_to(m.OUT) / 'original.yaml'): m.sha(dest / 'original.yaml') for _, _, dest, *_ in cases},
    'expectationHashes': {str(dest.relative_to(m.OUT) / 'expected.json'): m.sha(dest / 'expected.json') for _, _, dest, *_ in cases}})

for name, original, dest, work, path, position, expected in cases:
    client = None
    checks = {}
    details = {}
    try:
        code, report = m.cli(dest, work)
        checks['cliEnvelope'] = code == expected['cliExit'] and {k: report.get(k) for k in expected['cliEnvelope']} == expected['cliEnvelope']
        client = Client(dest, work)
        diagnostics = client.open(path, original)
        checks['publication'] = diagnostics == []
        response = client.request('textDocument/hover', {'textDocument': {'uri': path.as_uri()}, 'position': position})
        hover = response.get('result') or {}
        text = hover.get('contents', {}).get('value', '')
        checks['noProtocolError'] = 'error' not in response
        checks['hoverTypeAndProvenance'] = all(value in text for value in expected['hover']['contains'])
        checks['exactSourceRange'] = hover.get('range') == expected['hover']['range']
        checks['sourceUnchanged'] = path.read_bytes() == original.encode()
        details = {'cli': report, 'response': response}
    except Exception:
        details['error'] = traceback.format_exc()
        checks['completed'] = False
    finally:
        if client:
            client.close()
            checks['cleanServerExit'] = client.p.returncode == 0
        m.result(name, checks, details)
sys.exit(0 if len(m.RESULTS) == 4 and all(row['pass'] for row in m.RESULTS) else 1)

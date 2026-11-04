"""Finite independent CLI/LSP expectations. No production imports."""
import importlib.util, json, pathlib, sys, traceback

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('wire', ROOT / 'tests/e2e/wf-audit-gaps.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
MARK = '<CURSOR>'
HEAD = 'apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: independent}\n'
ENUM = ['""', '"Never"', '"OnWorkflowCompletion"', '"OnWorkflowDeletion"']

class Client(m.LSP):
    def send(self, method, params=None, ident=None):
        frame = {'jsonrpc': '2.0', 'method': method}
        if params is not None: frame['params'] = params
        if ident is not None: frame['id'] = ident
        payload = json.dumps(frame).encode()
        with (self.dest / 'lsp.stdin.raw').open('ab') as f:
            f.write(('Content-Length: %d\r\n\r\n' % len(payload)).encode() + payload)
        return super().send(method, params, ident)

def query(name, marked, method='completion', labels=None, excluded=(), applied=None, needles=(), no_result=False):
    text = marked.replace(MARK, '')
    dest, work = m.setup(name, {'case.yaml': text})
    path = work / 'case.yaml'
    (dest / 'original.yaml').write_bytes(text.encode())
    expected = {'method': method, 'exactLabels': labels, 'excluded': list(excluded), 'appliedEdits': applied, 'hoverContains': list(needles), 'noResult': no_result}
    m.save(dest / 'independent-expectations.json', expected)
    client = None
    try:
        client = Client(dest, work)
        client.open(path, text)
        response = client.request('textDocument/' + method, {'textDocument': {'uri': path.as_uri()}, 'position': m.pos(marked, MARK)})
        checks = {'noProtocolError': 'error' not in response}
        value = response.get('result')
        if method == 'hover':
            content = (value or {}).get('contents', {}).get('value', '')
            checks['hoverExpected'] = value is None if no_result else all(n in content for n in needles)
        else:
            items = value if isinstance(value, list) else []
            got = [x['label'] for x in items]
            if labels is not None: checks['exactLabels'] = sorted(got) == sorted(labels)
            checks['excluded'] = not (set(got) & set(excluded))
            if no_result: checks['empty'] = items == []
            for index, (label, old, new) in enumerate(applied or []):
                matches = [x for x in items if x['label'] == label]
                checks['uniqueCandidate-' + str(index)] = len(matches) == 1
                if len(matches) != 1: continue
                item = matches[0]
                checks['textEdit-' + str(index)] = 'textEdit' in item
                if 'textEdit' not in item: continue
                actual = m.apply(text, {'documentChanges': [{'textDocument': {'uri': path.as_uri(), 'version': 1}, 'edits': [item['textEdit']]}]}, path.as_uri(), 1)
                exact = text.replace(old, new, 1)
                (dest / ('expected-' + str(index) + '.yaml')).write_bytes(exact.encode())
                (dest / ('actual-' + str(index) + '.yaml')).write_bytes(actual.encode())
                checks['exactBytes-' + str(index)] = actual == exact
                reports = []
                for kind, data in [('actual', actual), ('expected', exact)]:
                    path.write_bytes(data.encode())
                    run = m.command(dest, kind + '-check-' + str(index), [m.BINARY, 'check', '--root', work, '--format', 'json'])
                    report = json.loads(run.stdout)
                    reports.append(report)
                    checks[kind + '-complete-resource-' + str(index)] = run.returncode == 0 and report['filesAnalyzed'] == 1 and report['resourcesAnalyzed'] == 1 and not report['configurationErrors']
                    checks[kind + '-no-diagnostics-' + str(index)] = report['diagnostics'] == []
                    checks[kind + '-honest-incomplete-' + str(index)] = report['analysisComplete'] is True and report['notEvaluated']==[]
        m.result(name, checks, {'response': response})
    except Exception:
        (dest / 'probe-error.txt').write_text(traceback.format_exc())
        m.result(name, {'probeCompleted': False}, {'error': traceback.format_exc()})
    finally:
        if client: client.close()

if sys.argv[3] == 'actual':
    body = HEAD + 'spec: {\n  artifactGC: {\n    strategy: Ne' + MARK + ', forceFinalizerRemoval: true\n  },\n  parallelism: 2\n}\n'
    query('multiline-flow-sibling', body, labels=ENUM, applied=[('"Never"', 'strategy: Ne', 'strategy: "Never"')])
    query('multiline-flow-sibling-crlf-comment', body.replace('true\n', 'true # keep\n').replace('\n', '\r\n'), labels=ENUM, applied=[('"Never"', 'strategy: Ne', 'strategy: "Never"')])
    body = HEAD + 'spec: {\n  artifactGC: {\n    strategy: Ne' + MARK + '}, parallelism: 2\n}\n'
    query('multiline-flow-closing-delimiters', body, labels=ENUM, applied=[('"Never"', 'strategy: Ne', 'strategy: "Never"')])
    body = HEAD + 'spec: {\n  artifactGC: {\n    strat' + MARK + ': Never, forceFinalizerRemoval: true\n  },\n  parallelism: 2\n}\n'
    query('multiline-flow-key-positive-control', body, labels=['strategy'], applied=[('strategy', 'strat:', 'strategy:')])
    body = HEAD + 'spec:\n  templates:\n    - name: main\n      container: {image: alpine}\n      outputs: {artifacts: [\n        {name: first, path: /tmp/first, artifactGC: {\n          strategy: Ne' + MARK + ', serviceAccountName: kept\n        }},\n        {name: next, path: /tmp/next}\n      ]}\n'
    query('multiline-flow-array-object', body, labels=ENUM, applied=[('"Never"', 'strategy: Ne', 'strategy: "Never"')])
    body = HEAD + 'spec: {\n  artifactGC: {\n    strategy: Ne' + MARK + ', forceFinalizerRemoval: true\n  }\n}\n'
    query('multiline-flow-value-hover', body, method='hover', needles=['Type: `string`', '3.6.0', 'b26ed4aa4dee395844531efa4a76a022183bec22'])
    query('comment-hover', HEAD + 'spec: {} # artifactGC ' + MARK + '\n', method='hover', no_result=True)
    foreign = '---\napiVersion: argoproj.io/v1alpha1\nkind: Application\nspec:\n  par' + MARK + '\n'
    query('foreign-after-damaged-owned', HEAD + 'spec: [\n' + foreign, labels=[], no_result=True)
    query('foreign-hover-after-damaged-owned', HEAD + 'spec: [\n' + foreign, method='hover', no_result=True)
else:
    for field in ['closedConjunction', 'closedRefConjunction']:
        query(field + '-keys', HEAD + 'spec:\n  ' + field + ':\n    ' + MARK + '\n', labels=['shared'], excluded=['blocked'])
        query(field + '-key-edit', HEAD + 'spec:\n  ' + field + ':\n    sh' + MARK + ': ok\n', labels=['shared'], applied=[('shared', 'sh:', 'shared:')])
        query(field + '-values', HEAD + 'spec:\n  ' + field + ':\n    shared: ' + MARK + '\n', labels=['"ok"'], applied=[('"ok"', 'shared: ', 'shared: "ok"')])
        query(field + '-blocked-values', HEAD + 'spec:\n  ' + field + ':\n    blocked: ' + MARK + '\n', labels=[])
    query('closed-union-keys', HEAD + 'spec:\n  closedUnion:\n    ' + MARK + '\n', labels=['left', 'right'])
    query('closed-union-left-edit', HEAD + 'spec:\n  closedUnion:\n    le' + MARK + ': a\n', labels=['left'], applied=[('left', 'le:', 'left:')])
    query('closed-union-right-edit', HEAD + 'spec:\n  closedUnion:\n    ri' + MARK + ': b\n', labels=['right'], applied=[('right', 'ri:', 'right:')])
    query('closed-selected-branch', HEAD + 'spec:\n  closedBranches:\n    mode: git\n    re' + MARK + ': repository\n', labels=['repo'], excluded=['chart', 'mode'], applied=[('repo', 're:', 'repo:')])
    query('closed-partial-discriminator', HEAD + 'spec:\n  closedBranches:\n    mode: g' + MARK + '\n', labels=['"chart"', '"git"'], applied=[('"git"', 'mode: g', 'mode: "git"'), ('"chart"', 'mode: g', 'mode: "chart"')])
    query('boolean-false-hover', HEAD + 'spec:\n  imposs' + MARK + 'ible: x\n', method='hover', needles=['Type: `never`'])
    query('intersection-never-hover', HEAD + 'spec:\n  typeContra' + MARK + 'diction: x\n', method='hover', needles=['Type: `never`'])

m.save(m.OUT / 'manifest.json', {'command': sys.argv, 'binarySha256': m.sha(m.BINARY), 'probeSha256': m.sha(pathlib.Path(__file__)), 'expectationsSha256': m.sha(ROOT / 'tests/fixtures/schema-query/contract.json'), 'protocolHelperSha256': m.sha(ROOT / 'tests/e2e/wf-audit-gaps.py'), 'caseCount': len(m.RESULTS), 'classification': sys.argv[3], 'inputHashes': {str(p.relative_to(m.OUT)): m.sha(p) for p in m.OUT.rglob('*.yaml')}})
sys.exit(0 if len(m.RESULTS) == (9 if sys.argv[3] == 'actual' else 15) and all(r['pass'] for r in m.RESULTS) else 1)

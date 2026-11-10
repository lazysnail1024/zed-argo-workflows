#!/usr/bin/env python3
"""CLI preflight refusal and bounded per-file LSP discovery/recovery.

Workspace symbols expose admitted detached candidates. A refused file must stay
Limited, publish its budget refusal when opened, and never supply partial facts.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parent
BINARY = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
HELPER = ROOT / 'wf-audit-gaps.py'
spec = importlib.util.spec_from_file_location('public_lsp_wire', HELPER)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
shutil.copyfile(HELPER, OUT / 'wire-helper.py')
EXPECTED_SHA = m.sha(BINARY)

class Client(m.LSP):
    def send(self, method, params=None, ident=None):
        value = {'jsonrpc': '2.0', 'method': method}
        if params is not None: value['params'] = params
        if ident is not None: value['id'] = ident
        data = json.dumps(value).encode()
        with (self.dest / 'lsp.stdin.raw').open('ab') as stream:
            stream.write(('Content-Length: %d\r\n\r\n' % len(data)).encode() + data)
        return super().send(method, params, ident)

def symbols(path, text, pattern):
    result = []
    for match in re.finditer(pattern, text):
        head = text[:match.start(1)]
        start = {'line': head.count('\n'), 'character': len(head.rsplit('\n', 1)[-1])}
        end = start | {'character': start['character'] + len(match[1])}
        result.append({'name': match[1], 'kind': 12, 'containerName': 'template',
                       'location': {'uri': path.as_uri(), 'range': {'start': start, 'end': end}}})
    return result

def normalize(items):
    return sorted(json.dumps(item, sort_keys=True) for item in items)

def index_states(client, work):
    result = []
    for line in bytes(client.stderr_raw).splitlines():
        try: row = json.loads(line)
        except (ValueError, UnicodeError): continue
        if row.get('phase') == 'index' and row.get('root') == str(work): result.append(row)
    return result

def settled_index(client, work, expected, marker=0):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        rows = index_states(client, work)[marker:]
        if rows and rows[-1].get('state') == expected and rows[-1].get('files') == 2:
            return rows[-1]
        time.sleep(.02)
    raise AssertionError({'expectedIndexState': expected, 'observed': index_states(client, work)})

SIBLING = ('apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\n'
           'metadata: {name: sibling}\nspec:\n  templates:\n'
           '    - name: exact\n      container: {image: alpine}\n')
def graph(mode, tail):
    # 13 fixed outer nodes; 9 nodes plus the args scalar count per template.
    # The first 99 templates each have 991 arguments; the last has `tail`.
    # Semantic tree size = 13 + 99 * (9 + 991) + (9 + tail).
    lines = ["apiVersion: argoproj.io/v1alpha1", "kind: WorkflowTemplate",
             "metadata: {name: boundary}", "spec:", "  templates:"]
    for index in range(100):
        args = ",".join(["x"] * (tail if index == 99 else 991))
        literal = "{image: alpine, args: [" + args + "]}"
        if mode == "plain":
            value = literal
        elif mode == "literal-merge":
            value = "{<<: " + literal + "}"
        elif index == 0:
            value = "&shared " + literal
        elif index == 99:
            value = literal
        else:
            value = "*shared"
        lines.extend(["    - name: t%03d" % index, "      container: " + value])
    return "\n".join(lines) + "\n"

BASE_UNKNOWN = "complete domain rules, version acceptance, and resource budgets are not yet implemented"
cases = []
for name, refused in [('alias-99902', False), ('alias-100000', True), ('plain-100000', False), ('foreign-admitted', False)]:
    text = graph('plain' if name == 'plain-100000' else 'alias', 978 if name in ['alias-100000', 'plain-100000'] else 880)
    if name == 'foreign-admitted': text = text.replace('argoproj.io/v1alpha1', 'example.io/v1').replace('WorkflowTemplate', 'Other')
    dest, work = m.setup(name, {'a-graph.yaml': text, 'z-plain.yaml': SIBLING})
    expected = {'method': 'workspace/symbol', 'params': {'query': ''},
                'graphLogicalNodes': 0 if name == 'plain-100000' else 100000 if refused else 99902,
                'graphPreflightNodes': 100098 if refused else 100000,
                'plainSiblingAggregateCharge': 0}
    expected['indexState'] = 'Limited' if refused else 'Ready'
    expected['result'] = ([] if refused or name == 'foreign-admitted' else symbols(work / 'a-graph.yaml', text, r'name: (t[0-9]{3})\n')) + symbols(work / 'z-plain.yaml', SIBLING, r'name: (exact)\n')
    m.save(dest / 'expected.json', expected)
    cases.append((name, refused, dest, work, expected))

m.save(OUT / 'manifest.json', {'command': sys.argv, 'binarySha256': EXPECTED_SHA,
       'runtimeEnvironment': {key: os.environ.get(key) for key in ['GOMEMLIMIT', 'GOGC', 'GODEBUG', 'GOMAXPROCS']},
       'testSha256': m.sha(Path(__file__)), 'helperSha256': m.sha(HELPER),
       'python': sys.version, 'platform': platform.platform(), 'caseCount': 5,
       'inputHashes': {str(path.relative_to(OUT)): m.sha(path) for path in OUT.glob('*/input/*')},
       'expectedHashes': {str(path.relative_to(OUT)): m.sha(path) for path in OUT.glob('*/expected.json')},
       'scope': 'CLI aggregate admission plus LSP per-file admission, explicit Limited state and current-buffer refusal; no performance claim or cap relaxation.'})
for name, refused, dest, work, expected in cases:
    client = None
    checks = {}
    response = None
    initial = state = None
    try:
        wanted = {'analysisComplete': not refused, 'diagnostics': [], 'configurationErrors': [], 'filesAnalyzed': 1 if refused else 2, 'resourcesAnalyzed': 1 if refused or name == 'foreign-admitted' else 2, 'notEvaluated': ['alias expansion exceeds 100000 nodes'] if refused else []}
        m.save(dest / 'cli.expected.json', wanted)
        code, report = m.cli(dest, work)
        cli_checks = {'cliExit': code == (2 if refused else 0), **{k: report.get(k) == v for k, v in wanted.items()}}
        m.save(dest / 'cli.expected.json', wanted)
        client = Client(dest, work)
        initial = client.request('workspace/symbol', {'query': ''})
        state = settled_index(client, work, expected['indexState'])
        response = client.request('workspace/symbol', {'query': ''})
        checks = {'honestInitialCandidates': 'error' not in initial and all(item in expected['result'] for item in initial.get('result', [])),
                  'exactCurrentSymbols': 'error' not in response and normalize(response.get('result', [])) == normalize(expected['result']),
                  'correctIndexCompleteness': state['state'] == expected['indexState']}
        if refused:
            path = work / 'a-graph.yaml'
            text = path.read_text()
            diagnostics = client.open(path, text)
            checks.update({'refusedFileSuppliesNoSymbols': all(item['location']['uri'] != path.as_uri() for item in response.get('result', [])),
                           'explicitBudgetReason': any(d.get('code') == 'WF_ANALYSIS_INCOMPLETE' and all(term in d.get('message', '').lower() for term in ['alias', '100000']) for d in diagnostics)})
        else:
            checks['all101Declarations'] = len(response.get('result', [])) == (1 if name == 'foreign-admitted' else 101)
        checks.update(cli_checks)
        if refused:
            recovered = graph('alias', 880)
            (work / 'a-graph.yaml').write_text(recovered)
            marker = len(index_states(client, work))
            clean = client.change(work / 'a-graph.yaml', recovered, 2)
            recovered_state = settled_index(client, work, 'Ready', marker)
            want = symbols(work / 'a-graph.yaml', recovered, r'name: (t[0-9]{3})\n') + symbols(work / 'z-plain.yaml', SIBLING, r'name: (exact)\n')
            m.save(dest / 'recovery.expected.json', want)
            reply = client.request('workspace/symbol', {'query': ''})
            m.result('refusal-recovery', {'exactCurrentSymbols': 'error' not in reply and normalize(reply.get('result', [])) == normalize(want),
                                        'explicitReadyRecovery': recovered_state['state'] == 'Ready',
                                        'budgetDiagnosticCleared': not any('100000' in d.get('message', '') for d in clean)}, {'response': reply, 'state': recovered_state, 'diagnostics': clean})
    except Exception:
        (dest / 'error.txt').write_text(traceback.format_exc())
        checks['completed'] = False
    finally:
        if client:
            client.close()
            checks['cleanServerExit'] = client.p.returncode == 0
        m.result(name, checks, {'response': response, 'initial': initial if client else None, 'state': state if client else None})
sys.exit(0 if len(m.RESULTS) == 5 and all(row['pass'] for row in m.RESULTS) else 1)

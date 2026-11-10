#!/usr/bin/env python3
"""Detached-symbol discovery and real dependency-graph edit budgets.

The default run requires the existing 512 MiB cgroup. --semantic-only executes
the same semantic assertions locally without claiming resource acceptance.
"""
from pathlib import Path
import sys,json,re,importlib.util,subprocess,threading,time,traceback,os
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('wire',HERE/'wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
BASE_UNKNOWN='complete domain rules, version acceptance, and resource budgets are not yet implemented'
# Independent logical-tree convention: map/sequence1, each map key1, scalar1.
# The fixed document has13 outer nodes. Each template contributes9+args nodes.
# 13 + 99*(9+491) + (9+478) = 50000. A tail of479 yields50001.
def graph(name,tail=478,mode='alias',enum=False):
 text='apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: {name: '+name+'}\nspec:\n'
 if enum:text+='  artifactGC: {strategy: Nver}\n';tail-=4
 text+='  templates:\n'
 for i in range(100):
  text+='    - name: t%03d\n'%i
  args=','.join('x' for _ in range(tail if i==99 else 491));literal='{image: alpine, args: ['+args+']}'
  if mode=='plain':value=literal
  elif mode=='literal-merge':value='{<<: '+literal+'}'
  elif i==0:value='&shared '+literal
  elif i==99:value=literal
  else:value='*shared' if mode=='alias' else '{<<: *shared}'
  text+='      container: '+value+'\n'
 return text
A=graph('a');B=graph('b');OVER=graph('b',479)
def norm(v):return sorted(json.dumps(x,sort_keys=True) for x in v)
def symbols(path,text):
 values=[]
 for match in re.finditer(r'name: (t[0-9]{3})\n',text):
  start=match.start(1);head=text[:start];p={'line':head.count('\n'),'character':len(head.rsplit('\n',1)[-1])};values.append({'name':match[1],'kind':12,'location':{'uri':path.as_uri(),'range':{'start':p,'end':p|{'character':p['character']+4}}}})
 return values
def budget_reason(s):return isinstance(s,str) and '100000' in s and 'alias' in s.lower() and 'merge' in s.lower()
tracked=set();peaks={};samples=[];stop=threading.Event();semantic_only='--semantic-only' in sys.argv
cgpath=None if semantic_only else Path('/proc/self/cgroup').read_text().strip().split('::',1)[1]
cg=None if semantic_only else Path('/sys/fs/cgroup')/cgpath.lstrip('/')
if cg is not None:assert int((cg/'memory.max').read_text())<=512*1024**2
initial_events=None if cg is None else (cg/'memory.events').read_text()
def sample():
 while not stop.wait(.02):
  row={'seconds':time.monotonic(),'cgroupBytes':None if cg is None else int((cg/'memory.current').read_text())}
  for pid in tracked.copy():
   try:rss=int(next(x.split()[1] for x in Path('/proc/%d/status'%pid).read_text().splitlines() if x.startswith('VmRSS:')));peaks[pid]=max(peaks.get(pid,0),rss)
   except (OSError,StopIteration):continue
  samples.append(row)
thread=threading.Thread(target=sample,daemon=True);thread.start()
class Client(m.LSP):
 def __init__(self,*args):super().__init__(*args);tracked.add(self.p.pid)
 def send(self,method,params=None,ident=None):
  value={'jsonrpc':'2.0','method':method}
  if params is not None:value['params']=params
  if ident is not None:value['id']=ident
  data=json.dumps(value).encode()
  with (self.dest/'lsp.stdin.raw').open('ab') as f:f.write(('Content-Length: %d\r\n\r\n'%len(data)).encode()+data)
  return super().send(method,params,ident)
 def close(self):
  try:return super().close()
  finally:tracked.discard(self.p.pid)
def cli(dest,work,label='check',scope=None):
 cmd=[str(m.BINARY),'check','--root',str(work),'--format','json']
 if scope:cmd+=['--scope',scope]
 with (dest/(label+'.stdout')).open('wb') as so,(dest/(label+'.stderr')).open('wb') as se:
  p=subprocess.Popen(cmd,stdout=so,stderr=se);tracked.add(p.pid)
  try:p.wait(timeout=20)
  except subprocess.TimeoutExpired:p.kill();p.wait()
  finally:tracked.discard(p.pid)
 m.save(dest/(label+'.command.json'),{'command':cmd,'exitCode':p.returncode})
 try:return p.returncode,json.loads((dest/(label+'.stdout')).read_text())
 except ValueError:return p.returncode,{}
def cli_checks(code,r,files,resources,refused,diagnostics=None):
 notes=r.get('notEvaluated',[]);return {'naturalExit':code==(2 if refused else int(any(d.get('severity')=='Error' for d in (diagnostics or [])))),'completeDiagnostics':r.get('diagnostics')==([] if diagnostics is None else diagnostics),'counts':r.get('filesAnalyzed')==files and r.get('resourcesAnalyzed')==resources,'completion':r.get('analysisComplete') is (not bool(refused)),'noConfigFailure':r.get('configurationErrors')==[],'unknownEnvelope':len(notes)==refused and all(budget_reason(n) for n in notes)}
SCOPES=m.CONFIG+'scopes:\n  - name: first\n    include: [a.yaml]\n  - name: second\n    include: [b.yaml]\n'
fixtures=[('exact-files',{'a.yaml':A,'b.yaml':B},m.CONFIG,None,2,2,0),('over-files',{'a.yaml':A,'b.yaml':OVER},m.CONFIG,None,1,1,1),('exact-stream',{'case.yaml':A+'---\n'+B},m.CONFIG,None,1,2,0),('over-stream',{'case.yaml':A+'---\n'+OVER},m.CONFIG,None,0,1,1),('over-scopes',{'a.yaml':A,'b.yaml':OVER},SCOPES,None,1,1,1),('selected-scope',{'a.yaml':A,'b.yaml':OVER},SCOPES,'second',1,1,0),('plain-over',{'a.yaml':graph('a',mode='plain'),'b.yaml':graph('b',479,'plain')},m.CONFIG,None,2,2,0),('mixed-graph-plain',{'a.yaml':A,'b.yaml':graph('b',479,'plain')},m.CONFIG,None,2,2,0),('exact-merge-files',{'a.yaml':graph('a',mode='merge'),'b.yaml':graph('b',mode='merge')},m.CONFIG,None,2,2,0),('over-merge-files',{'a.yaml':graph('a',mode='merge'),'b.yaml':graph('b',479,'merge')},m.CONFIG,None,1,1,1),('exact-literal-merge-files',{'a.yaml':graph('a',mode='literal-merge'),'b.yaml':graph('b',mode='literal-merge')},m.CONFIG,None,2,2,0),('over-literal-merge-files',{'a.yaml':graph('a',mode='literal-merge'),'b.yaml':graph('b',479,'literal-merge')},m.CONFIG,None,1,1,1),('many-graph-files',{'%02d.yaml'%i:graph('library%d'%i,mode='alias' if i%2==0 else 'merge') for i in range(16)},m.CONFIG,None,2,2,14)]
EXPECTED = {x[0] for x in fixtures} | {
    'cold-summary-over-aggregate', 'cold-summary-recovery', 'warm-summary-exact',
    'warm-summary-over-aggregate', 'warm-summary-repeat-over', 'warm-summary-recovery',
    'summary-after-cancel', 'two-root-summaries', 'selected-root-independent',
    'two-root-summary-recovery', 'fix-candidates-independent-results', 'fix-applied-clean',
    'unrelated-graph-does-not-block-fix', 'linked-exact-fix-candidates',
    'overbudget-fix-refusal', 'linked-fix-recovery', 'budget-after-cancel',
}

def index_states(client, work):
    rows = []
    for line in bytes(client.stderr_raw).splitlines():
        try:
            row = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if row.get('phase') == 'index' and row.get('root') == str(work):
            rows.append(row)
    return rows

def ready(client, work, files=2, marker=0):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        states = index_states(client, work)[marker:]
        if states and states[-1].get('state') == 'Ready' and states[-1].get('files') == files:
            return states[-1]
        time.sleep(.02)
    raise AssertionError({'root': str(work), 'expectedFiles': files, 'states': index_states(client, work)})

def replace_closed(client, work, path, text, files=2):
    marker = len(index_states(client, work))
    path.write_text(text)
    client.send('workspace/didChangeWatchedFiles', {'changes': [{'uri': path.as_uri(), 'type': 2}]})
    return ready(client, work, files, marker)

def query_case(name, client, expected, state, initial=None):
    response = client.request('workspace/symbol', {'query': ''})
    observed = [{k: x[k] for k in ['name', 'kind', 'location']} for x in response.get('result', [])]
    checks = {'exactCurrentSymbols': 'error' not in response and norm(observed) == norm(expected),
              'completedDiscovery': state.get('state') == 'Ready'}
    if initial is not None:
        first = [{k: x[k] for k in ['name', 'kind', 'location']} for x in initial.get('result', [])]
        checks['initialResponseUsesOnlyKnownCandidates'] = (
            initial.get('error', {}).get('code') == -32801 and 'result' not in initial
            or 'error' not in initial and isinstance(initial.get('result'), list)
            and set(norm(first)).issubset(set(norm(expected))))
    m.save(client.dest / (name + '.expected.json'), {'symbols': expected, 'indexState': 'Ready',
           'contract': 'Detached summaries do not reserve the aggregate materialized graph budget.'})
    m.result(name, checks, {'response': response, 'initial': initial, 'state': state})

def request_for(path, text):
    p = m.pos(text, 'Nver')
    return {'textDocument': {'uri': path.as_uri()},
            'range': {'start': p, 'end': p | {'character': p['character'] + 4}}}

def fix_outputs(client, path, text, version, request):
    offered = client.request('textDocument/codeAction', request)
    outputs, resolved = [], []
    for action in offered.get('result', []):
        response = client.request('codeAction/resolve', action)
        resolved.append(response)
        if 'result' in response:
            outputs.append(m.apply(text, response['result']['edit'], path.as_uri(), version))
    return offered, outputs, resolved

def expected_fixes(text):
    return [text.replace('Nver', value) for value in ['""', '"Never"', '"OnWorkflowCompletion"', '"OnWorkflowDeletion"']]

def refusal(response):
    return (response.get('result') == [] and 'error' not in response
            or response.get('error', {}).get('code') == -32803
            and budget_reason(response.get('error', {}).get('message')) and 'result' not in response)

try:
    # CLI remains an explicit aggregate operation, retaining all thirteen
    # exact/over, multi-document, merge, foreign/plain and scope controls.
    for name, files, config, scope, nfiles, nresources, refused in fixtures:
        dest, work = m.setup(name, files, config)
        m.save(dest/'expected.json', {'filesAnalyzed': nfiles, 'resourcesAnalyzed': nresources,
            'diagnostics': [], 'analysisComplete': not bool(refused), 'exitCode': 2 if refused else 0,
            'budgetRefusals': refused, 'budgetReasonContains': ['alias', 'merge', '100000'],
            'logicalGraphNodes': 0 if name == 'plain-over' else 50000 if name == 'mixed-graph-plain'
                else 800000 if name == 'many-graph-files' else 50001 if scope
                else 100001 if 'over' in name else 100000})
        code, report = cli(dest, work, scope=scope)
        m.result(name, cli_checks(code, report, nfiles, nresources, refused), {'report': report})

    # A one-line prefix makes every location change independently of the
    # retained symbols' names, so waiting for discovery cannot accept old spans.
    shifted_over = '# updated graph revision\n' + OVER
    for warm in (False, True):
        dest, work = m.setup('warm-transitions' if warm else 'cold-transitions',
                            {'a.yaml': A, 'b.yaml': B if warm else shifted_over})
        client = Client(dest, work)
        ap, bp = work/'a.yaml', work/'b.yaml'
        exact = symbols(ap, A) + symbols(bp, B)
        over = symbols(ap, A) + symbols(bp, shifted_over)
        try:
            initial = client.request('workspace/symbol', {'query': ''})
            state = ready(client, work)
            if not warm:
                query_case('cold-summary-over-aggregate', client, over, state, initial)
                state = replace_closed(client, work, bp, B)
                query_case('cold-summary-recovery', client, exact, state)
            else:
                query_case('warm-summary-exact', client, exact, state, initial)
                state = replace_closed(client, work, bp, shifted_over)
                query_case('warm-summary-over-aggregate', client, over, state)
                query_case('warm-summary-repeat-over', client, over, state)
                state = replace_closed(client, work, bp, B)
                query_case('warm-summary-recovery', client, exact, state)
                client.send('workspace/symbol', {'query': ''}, 9000)
                client.send('$/cancelRequest', {'id': 9000})
                client.send('workspace/symbol', {'query': ''}, 9001)
                client.wait(lambda _: len([x for x in client.received if x.get('id') in [9000, 9001]]) == 2)
                replies = [x for x in client.received if x.get('id') in [9000, 9001]]
                old = next(x for x in replies if x['id'] == 9000)
                new = next(x for x in replies if x['id'] == 9001)
                normalize = lambda response: norm([{k: x[k] for k in ['name', 'kind', 'location']}
                                                   for x in response.get('result', [])])
                m.result('summary-after-cancel', {
                    'oneTerminalEach': sorted(x['id'] for x in replies) == [9000, 9001],
                    'oldTerminalHonest': old.get('error', {}).get('code') in [-32800, -32801]
                        or 'error' not in old and normalize(old) == norm(exact),
                    'freshSummary': 'error' not in new and normalize(new) == norm(exact)},
                    {'responses': replies})
        finally:
            client.close()

    dest, work = m.setup('two-roots', {'a/a.yaml': A, 'b/b.yaml': shifted_over})
    ar, br = work/'a', work/'b'
    (ar/'.argo-workflows-ls.yaml').write_text(m.CONFIG)
    (br/'.argo-workflows-ls.yaml').write_text(m.CONFIG)
    client = Client(dest, ar)
    try:
        client.send('workspace/didChangeWorkspaceFolders', {'event': {
            'added': [{'uri': br.as_uri(), 'name': 'second'}], 'removed': []}})
        ready(client, ar, 1)
        state = ready(client, br, 1)
        query_case('two-root-summaries', client, symbols(ar/'a.yaml', A) + symbols(br/'b.yaml', shifted_over), state)
        response = client.request('textDocument/documentSymbol', {'textDocument': {'uri': (br/'b.yaml').as_uri()}})
        expected = [{'name': x['name'], 'kind': 12, 'range': x['location']['range'],
                     'selectionRange': x['location']['range']} for x in symbols(br/'b.yaml', shifted_over)]
        m.result('selected-root-independent', {
            'freshSelectedResult': 'error' not in response
                and norm([{k: x[k] for k in ['name', 'kind', 'range', 'selectionRange']}
                          for x in response.get('result', [])]) == norm(expected)},
            {'response': response, 'expected': expected})
        state = replace_closed(client, br, br/'b.yaml', B, 1)
        query_case('two-root-summary-recovery', client, symbols(ar/'a.yaml', A) + symbols(br/'b.yaml', B), state)
    finally:
        client.close()

    bad = graph('a', enum=True)
    dest, work = m.setup('fix-simulation', {'a.yaml': bad, 'b.yaml': B})
    ap, bp = work/'a.yaml', work/'b.yaml'
    client = Client(dest, work)
    try:
        diagnostics = client.open(ap, bad)
        ready(client, work)
        request = request_for(ap, bad)
        expected_diag = [{'range': request['range'], 'code': 'SCHEMA_VALIDATION',
                          'severity': 1, 'source': 'argo-workflows'}]
        response, outputs, resolved = fix_outputs(client, ap, bad, 1, request)
        m.save(dest/'expected-edits.json', expected_fixes(bad))
        m.result('fix-candidates-independent-results', {
            'fullPublication': norm([{k: d[k] for k in ['range', 'code', 'severity', 'source']}
                                     for d in diagnostics]) == norm(expected_diag),
            'allFourExactEdits': norm(outputs) == norm(expected_fixes(bad)),
            'diskUnchanged': ap.read_text() == bad and bp.read_text() == B},
            {'response': response, 'resolved': resolved})
        changed = bad.replace('Nver', '"Never"')
        if changed in outputs:
            after_ds = client.change(ap, changed, 2)
            ap.write_text(changed)
            code, report = cli(dest, work, 'after')
            m.result('fix-applied-clean', cli_checks(code, report, 2, 2, 0) | {
                'publishedClean': after_ds == [],
                'unrelatedBytesUnchanged': ap.read_bytes() == changed.encode() and bp.read_bytes() == B.encode()},
                {'report': report})
        else:
            m.result('fix-applied-clean', {'returnedApplicableEdit': False})
        ap.write_text(bad)
        client.change(ap, bad, 3)
        replace_closed(client, work, bp, OVER)
        offered, outputs, resolved = fix_outputs(client, ap, bad, 3, request)
        m.result('unrelated-graph-does-not-block-fix', {
            'allFourExactEdits': norm(outputs) == norm(expected_fixes(bad)),
            'unrelatedBytesUnchanged': bp.read_text() == OVER},
            {'response': offered, 'resolved': resolved})
    finally:
        client.close()

    # This is an actual materialization boundary, not an unrelated same-root
    # file: a has a resolved templateRef to b. The added step template costs 15
    # logical nodes, offset by removing 15 terminal args from a's graph.
    linked = graph('a', 463, enum=True) + ('    - name: invoke\n'
        '      steps:\n        - - name: call\n'
        '            templateRef: {name: b, template: t000}\n')
    dest, work = m.setup('linked-fix-budget', {'a.yaml': linked, 'b.yaml': B},
                        m.CONFIG + 'defaultNamespace: argo\n')
    ap, bp = work/'a.yaml', work/'b.yaml'
    client = Client(dest, work)
    try:
        client.open(ap, linked)
        ready(client, work)
        request = request_for(ap, linked)
        exact_action, outputs, resolved = fix_outputs(client, ap, linked, 1, request)
        m.result('linked-exact-fix-candidates', {
            'allFourExactEdits': norm(outputs) == norm(expected_fixes(linked)),
            'closedDependencyUnchanged': bp.read_text() == B},
            {'response': exact_action, 'resolved': resolved, 'logicalClosureNodes': 100000})
        over_state = replace_closed(client, work, bp, OVER)
        refused = client.request('textDocument/codeAction', request)
        repeated = client.request('textDocument/codeAction', request)
        old = client.request('codeAction/resolve', exact_action['result'][0]) if exact_action.get('result') else {}
        code, report = cli(dest, work, 'over-closure')
        m.result('overbudget-fix-refusal', {
            'noPartialEdit': refusal(refused) and refusal(repeated),
            'previousActionRefused': old.get('error', {}).get('code') == -32801 and 'result' not in old,
            'individualSummariesStillComplete': over_state['state'] == 'Ready',
            'actualAggregateBudgetReason': code == 2 and report.get('analysisComplete') is False
                and any(budget_reason(reason) for reason in report.get('notEvaluated', []))},
            {'response': refused, 'repeated': repeated, 'oldAction': old, 'report': report,
             'logicalClosureNodes': 100001})
        replace_closed(client, work, bp, B)
        recovered, outputs, resolved = fix_outputs(client, ap, linked, 1, request)
        m.result('linked-fix-recovery', {'allFourExactEdits': norm(outputs) == norm(expected_fixes(linked))},
                 {'response': recovered, 'resolved': resolved})
        client.send('textDocument/codeAction', request, 9100)
        client.send('$/cancelRequest', {'id': 9100})
        client.send('textDocument/codeAction', request, 9101)
        client.wait(lambda _: len([x for x in client.received if x.get('id') in [9100, 9101]]) == 2)
        replies = [x for x in client.received if x.get('id') in [9100, 9101]]
        old = next(x for x in replies if x['id'] == 9100)
        new = next(x for x in replies if x['id'] == 9101)
        expected_titles = sorted(action['title'] for action in exact_action.get('result', []))
        new_outputs = []
        for action in new.get('result', []):
            response = client.request('codeAction/resolve', action)
            if 'result' in response:
                new_outputs.append(m.apply(linked, response['result']['edit'], ap.as_uri(), 1))
        m.result('budget-after-cancel', {
            'oneTerminalEach': sorted(x['id'] for x in replies) == [9100, 9101],
            'oldTerminalHonest': old.get('error', {}).get('code') in [-32800, -32801]
                or 'error' not in old and sorted(a['title'] for a in old.get('result', [])) == expected_titles,
            'freshBudget': 'error' not in new and norm(new_outputs) == norm(expected_fixes(linked))},
            {'responses': replies})
    finally:
        client.close()
except Exception:
    (m.OUT/'error.txt').write_text(traceback.format_exc())
    m.result('completed', {'completed': False})
finally:
    stop.set()
    thread.join(timeout=1)
    m.save(m.OUT/'resource-observation.json', {
        'resourceAcceptance': not semantic_only, 'cgroup': cgpath,
        'memoryMax': None if cg is None else (cg/'memory.max').read_text().strip(),
        'initialMemoryEvents': initial_events,
        'finalMemoryEvents': None if cg is None else (cg/'memory.events').read_text(),
        'peakServerRSSKiB': max(peaks.values(), default=0),
        'peakCgroupBytes': None if cg is None else max((x['cgroupBytes'] for x in samples), default=0),
        'sampleCount': len(samples), 'processPeaksKiB': peaks})
    m.save(m.OUT/'resource-samples.json', samples)
    m.save(m.OUT/'manifest.json', {
        'command': sys.argv, 'resourceAcceptance': not semantic_only,
        'runtimeEnvironment': {key: os.environ.get(key) for key in ['GOMEMLIMIT', 'GOGC', 'GODEBUG', 'GOMAXPROCS']},
        'binarySha256': m.sha(m.BINARY), 'testSha256': m.sha(Path(__file__)),
        'helperSha256': m.sha(HERE/'wf-audit-gaps.py'), 'expectedCases': sorted(EXPECTED),
        'metric': 'CLI and each materialized dependency result aggregate graph nodes; detached workspace symbols retain per-file summaries.',
        'graph50000Formula': '13 + 99*(9+491) + (9+478) = 50000',
        'linked50000Formula': 'graph(enum=True,tail=463) + 15-node templateRef step = 50000',
        'inputHashes': {str(p.relative_to(m.OUT)): m.sha(p) for p in m.OUT.rglob('*.yaml')},
        'expectedHashes': {str(p.relative_to(m.OUT)): m.sha(p) for p in m.OUT.rglob('*expected*.json')}})
sys.exit(0 if len(m.RESULTS) == len(EXPECTED) and {x['case'] for x in m.RESULTS} == EXPECTED
         and all(x['pass'] for x in m.RESULTS) else 1)

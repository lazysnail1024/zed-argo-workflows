#!/usr/bin/env python3
"""Real-process graph-result budget, warm/cold admission and edit simulations."""
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
tracked=set();peaks={};samples=[];stop=threading.Event();cgpath=Path('/proc/self/cgroup').read_text().strip().split('::',1)[1];cg=Path('/sys/fs/cgroup')/cgpath.lstrip('/');assert int((cg/'memory.max').read_text())<=512*1024**2
initial_events=(cg/'memory.events').read_text()
def sample():
 while not stop.wait(.02):
  row={'seconds':time.monotonic(),'cgroupBytes':int((cg/'memory.current').read_text())}
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
EXPECTED={x[0] for x in fixtures}|{'cold-over-budget','cold-recovery','warm-exact','warm-over','warm-repeat-over','warm-recovery','budget-after-cancel','two-roots-over','selected-root-independent','two-roots-recovered','fix-candidates-independent-results','fix-applied-clean','overbudget-fix-refusal'}
def query_case(name,client,expected=None):
 response=client.request('workspace/symbol',{'query':''});checks={'explicitRefusal':response.get('error',{}).get('code')==-32803 and budget_reason(response.get('error',{}).get('message')) and 'result' not in response} if expected is None else {'exactCurrentSymbols':'error' not in response and norm([{k:x[k] for k in ['name','kind','location']} for x in response.get('result',[])])==norm(expected)};m.save(client.dest/(name+'.expected.json'),{'errorCode':-32803,'reasonContains':['alias','merge','100000']} if expected is None else expected);m.result(name,checks,{'response':response})
try:
 for name,files,config,scope,nfiles,nresources,refused in fixtures:
  dest,work=m.setup(name,files,config);m.save(dest/'expected.json',{'filesAnalyzed':nfiles,'resourcesAnalyzed':nresources,'diagnostics':[],'analysisComplete':not bool(refused),'exitCode':2 if refused else 0,'budgetRefusals':refused,'budgetReasonContains':['alias','merge','100000'],'logicalGraphNodes':0 if name=='plain-over' else 50000 if name=='mixed-graph-plain' else 800000 if name=='many-graph-files' else 50001 if scope else 100001 if 'over' in name else 100000});code,r=cli(dest,work,scope=scope);m.result(name,cli_checks(code,r,nfiles,nresources,refused),{'report':r})
 for warm in [False,True]:
  dest,work=m.setup('warm-transitions' if warm else 'cold-transitions',{'a.yaml':A,'b.yaml':B if warm else OVER});client=Client(dest,work);ap=work/'a.yaml';bp=work/'b.yaml';want=symbols(ap,A)+symbols(bp,B)
  try:
   if not warm:
    query_case('cold-over-budget',client);bp.write_text(B);query_case('cold-recovery',client,want)
   else:
    query_case('warm-exact',client,want);bp.write_text(OVER);query_case('warm-over',client);query_case('warm-repeat-over',client);bp.write_text(B);query_case('warm-recovery',client,want)
    client.send('workspace/symbol',{'query':''},9000);client.send('$/cancelRequest',{'id':9000});client.send('workspace/symbol',{'query':''},9001);client.wait(lambda _:len([x for x in client.received if x.get('id') in [9000,9001]])==2);replies=[x for x in client.received if x.get('id') in [9000,9001]];old=next(x for x in replies if x['id']==9000);new=next(x for x in replies if x['id']==9001);m.result('budget-after-cancel',{'oneTerminalEach':sorted(x['id'] for x in replies)==[9000,9001],'oldTerminalHonest':old.get('error',{}).get('code') in [-32800,-32801] or norm([{k:x[k] for k in ['name','kind','location']} for x in old.get('result',[])])==norm(want),'freshBudget':'error' not in new and norm([{k:x[k] for k in ['name','kind','location']} for x in new.get('result',[])])==norm(want)},{'responses':replies})
  finally:client.close()
 dest,work=m.setup('two-roots',{'a/a.yaml':A,'b/b.yaml':OVER});ar=work/'a';br=work/'b';(ar/'.argo-workflows-ls.yaml').write_text(m.CONFIG);(br/'.argo-workflows-ls.yaml').write_text(m.CONFIG);client=Client(dest,ar)
 try:
  client.send('workspace/didChangeWorkspaceFolders',{'event':{'added':[{'uri':br.as_uri(),'name':'second'}],'removed':[]}});query_case('two-roots-over',client);response=client.request('textDocument/documentSymbol',{'textDocument':{'uri':(br/'b.yaml').as_uri()}});expected=[{'name':x['name'],'kind':12,'range':x['location']['range'],'selectionRange':x['location']['range']} for x in symbols(br/'b.yaml',OVER)];m.result('selected-root-independent',{'freshSelectedResult':'error' not in response and norm([{k:x[k] for k in ['name','kind','range','selectionRange']} for x in response.get('result',[])])==norm(expected)},{'response':response,'expected':expected});(br/'b.yaml').write_text(B);query_case('two-roots-recovered',client,symbols(ar/'a.yaml',A)+symbols(br/'b.yaml',B))
 finally:client.close()
 bad=graph('a',enum=True);dest,work=m.setup('fix-simulation',{'a.yaml':bad,'b.yaml':B});ap=work/'a.yaml';bp=work/'b.yaml';client=Client(dest,work)
 try:
  diagnostics=client.open(ap,bad);position=m.pos(bad,'Nver');range_={'start':position,'end':position|{'character':position['character']+4}};expected_diag=[{'range':range_,'code':'SCHEMA_VALIDATION','severity':1,'source':'argo-workflows'}];request={'textDocument':{'uri':ap.as_uri()},'range':range_,'context':{'diagnostics':diagnostics}};response=client.request('textDocument/codeAction',request);outputs=[];resolved=[]
  for action in response.get('result',[]):
   r=client.request('codeAction/resolve',action);resolved.append(r)
   if 'result' in r:outputs.append(m.apply(bad,r['result']['edit'],ap.as_uri(),1))
  expected=[bad.replace('Nver',v) for v in ['""','"Never"','"OnWorkflowCompletion"','"OnWorkflowDeletion"']];m.save(dest/'expected-edits.json',expected);m.result('fix-candidates-independent-results',{'fullPublication':norm([{k:d[k] for k in ['range','code','severity','source']} for d in diagnostics])==norm(expected_diag),'allFourExactEdits':norm(outputs)==norm(expected),'diskUnchanged':ap.read_text()==bad and bp.read_text()==B},{'response':response,'resolved':resolved})
  changed=bad.replace('Nver','"Never"')
  if changed in outputs:
   after_ds=client.change(ap,changed,2);ap.write_text(changed);code,r=cli(dest,work,'after');m.result('fix-applied-clean',cli_checks(code,r,2,2,0)|{'publishedClean':after_ds==[],'unrelatedBytesUnchanged':ap.read_bytes()==changed.encode() and bp.read_bytes()==B.encode()},{'report':r})
  else:m.result('fix-applied-clean',{'returnedApplicableEdit':False})
  # Restore original bytes as a new current revision, then exceed the same
  # scope's result budget. No candidate may be inferred from partial analysis.
  ap.write_text(bad);client.change(ap,bad,3);bp.write_text(OVER);refused=client.request('textDocument/codeAction',request);m.result('overbudget-fix-refusal',{'noPartialEdit':refused.get('result')==[] or refused.get('error',{}).get('code')==-32803 and budget_reason(refused.get('error',{}).get('message')) and 'result' not in refused},{'response':refused})
 finally:client.close()
except Exception:
 (m.OUT/'error.txt').write_text(traceback.format_exc());m.result('completed',{'completed':False})
finally:
 stop.set();thread.join(timeout=1);m.save(m.OUT/'resource-observation.json',{'cgroup':cgpath,'memoryMax':(cg/'memory.max').read_text().strip(),'initialMemoryEvents':initial_events,'finalMemoryEvents':(cg/'memory.events').read_text(),'peakServerRSSKiB':max(peaks.values(),default=0),'peakCgroupBytes':max((x['cgroupBytes'] for x in samples),default=0),'sampleCount':len(samples),'processPeaksKiB':peaks});m.save(m.OUT/'resource-samples.json',samples)
 m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(Path(__file__)),'helperSha256':m.sha(HERE/'wf-audit-gaps.py'),'expectedCases':sorted(EXPECTED),'metric':'Whole resolved graph-document tree: map/sequence1,key1,scalar/null1,repeated occurrences count. Plain-only documents charge0 to aggregate allowance.','graph50000Formula':'13 + 99*(9+491) + (9+478) = 50000','inputHashes':{str(p.relative_to(m.OUT)):m.sha(p) for p in m.OUT.rglob('*.yaml')},'expectedHashes':{str(p.relative_to(m.OUT)):m.sha(p) for p in m.OUT.rglob('*expected*.json')}})
sys.exit(0 if {x['case'] for x in m.RESULTS}==EXPECTED and all(x['pass'] for x in m.RESULTS) else 1)

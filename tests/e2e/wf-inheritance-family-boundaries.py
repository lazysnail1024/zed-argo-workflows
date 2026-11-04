#!/usr/bin/env python3
"""Historical inheritance transitions, independent value errors and source identities."""
from pathlib import Path
import importlib.util,json,os,signal,subprocess,sys,threading,time,traceback
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('family',HERE/'wf-inheritance-family.py');f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f);m=f.m
PAIRS=[('admission','configmap-argument-required-input','3.1.15','3.2.0'),('forwarding','configmap-argument-required-input','3.2.4','3.2.5'),('template-empty','WorkflowTemplate-name-only-exit-default','3.2.3','3.2.4'),('default-clearing','WorkflowTemplate-name-only-exit-default','3.4.9','3.4.10'),('public-globals','WorkflowTemplate-standalone-missing','3.4.1','3.4.2'),('exit-context','exit-explicit-known','3.4.5','3.4.6'),('visited-bodies','uncalled-missing','3.4.5','3.4.6'),('forms-34','configmap-plus-path-with-literal','3.4.16','3.4.17'),('forms-35','path-form-with-literal','3.5.4','3.5.5'),('absent-marker','marker-required','3.7.15','3.7.16')]
def scenarios():
 rows=[{'name':name,'core':core,'version':before,'targets':[after,before]} for name,core,before,after in PAIRS]
 for version in ['3.2.3','3.2.4','3.6.0']:
  for override in [False,True]:rows.append({'name':'base-winning-'+('override-' if override else 'empty-')+version,'version':version,'override':override,'targets':[]})
 rows.append({'name':'independent-errors','version':'3.6.0','targets':[]})
 return rows

def row(case,version):
 if 'core' in case:
  _,marked,errors=next(x for x in f.cases(version) if x[0]==case['core'])
  if case['name']=='exit-context':marked=marked.replace('  onExit: cleanup\n','  onExit: [[baseExit|cleanup]]\n').replace('    - name: cleanup\n','    - name: [[baseExitDecl|cleanup]]\n')
 elif case['name']=='independent-errors':
  marked=f.document('independent-errors','Workflow','  entrypoint: [[entry|main]]\n'+f.args('{name: [[pDecl|p]]}, {name: [[rDecl|r]]}'),f.template(inputs='{name: q}',body='      container: {image: alpine, args: ["{{workflow.parameters.[[pUse|p]]}}", "{{workflow.parameters.[[rUse|r]]}}"]}\n'))
  errors=[f.error('pDecl','WF_ARGUMENT_VALUE_INVALID','argument parameter p requires value or an allowed valueFrom'),f.error('rDecl','WF_ARGUMENT_VALUE_INVALID','argument parameter r requires value or an allowed valueFrom'),f.missing('entry','q')]
 else:
  caller=f.document('caller','Workflow','  workflowTemplateRef: {name: [[ref|library]]}\n'+(f.args('{name: [[callerDecl|p]], value: caller}') if case['override'] else ''),None).replace('namespace: argo}', 'namespace: argo, annotations: {note: "{{workflow.parameters.[[callerUse|p]]}}"}}')
  base=f.document('library','WorkflowTemplate','  entrypoint: main\n'+f.args('{name: [[baseDecl|p]]}'),f.template(body='      container: {image: alpine, args: ["{{workflow.parameters.[[bodyUse|p]]}}"]}\n'))
  marked=caller+'---\n'+base;errors=[]
  if not case['override']:errors.append(f.invalid('ref'))
  if tuple(map(int,version.split('.')))<(3,2,4):errors.append(f.invalid('baseDecl'))
 sibling=f.document('unchanged-sibling','Workflow','  entrypoint: main\n'+f.args('{name: [[siblingDecl|identity]], value: literal}'),f.template(body='      container: {image: alpine, args: ["\U0001f40c {{workflow.parameters.[[siblingUse|identity]]}}"]}\n'))
 # Actual CRLF/non-BMP bytes make UTF16/source assertions meaningful.
 return case['name'],(marked+'---\n'+sibling).replace('\n','\r\n'),errors

def query_tags(case,version):
 def q(tag,decls,uses):return (tag,decls,uses,uses+decls)
 queries=[q('siblingUse',['siblingDecl'],['siblingUse']),q('siblingDecl',['siblingDecl'],['siblingUse'])];refuse=[]
 if case['name']=='independent-errors':
  for prefix in ['p','r']:queries += [q(prefix+'Decl',[prefix+'Decl'],[prefix+'Use']),q(prefix+'Use',[prefix+'Decl'],[prefix+'Use'])]
 if case['name']=='exit-context':
  modern=tuple(map(int,version.split('.')))>=(3,4,6)
  queries += [('exit',['baseExitDecl'] if modern else [],['exit','baseExit'] if modern else [],['exit','baseExit','baseExitDecl'] if modern else []),q('baseExitDecl',['baseExitDecl'],['exit','baseExit'] if modern else ['baseExit'])];refuse=['baseExitDecl']
 if case['name'].startswith('base-winning'):
  refuse=['baseDecl','callerUse']
  if case['override']:
   queries += [q('callerUse',['callerDecl'],['callerUse']),q('callerDecl',['callerDecl'],['callerUse']),('bodyUse',['baseDecl','callerDecl'],[],[]),q('baseDecl',['baseDecl'],[])]
  else:
   for tag in ['callerUse','bodyUse','baseDecl']:queries.append(q(tag,['baseDecl'],['callerUse','bodyUse']))
 return queries,refuse

def prepared(case,dest):
 work,path,text,records,expected,envelope=f.prepare(case['version'],[row(case,case['version'])],dest);tags=records[0]['tags']
 def loc(tag):return {'uri':path.as_uri(),'range':f.span(text,tags[tag])}
 versions=[case['version']]+case['targets'];states={}
 for version in dict.fromkeys(versions):
  _,marked,errors=row(case,version);other,other_tags=f.strip(marked)
  # Target changes never rewrite or remap a source byte.
  if other!=text or other_tags!=tags:raise ValueError('transition input must remain identical')
  diagnostics=[loc(tag)|{'code':code,'message':message,'severity':severity,'source':'argo-workflows','positionKind':origin,'targetId':str(work)+'#root'} for tag,code,message,severity,origin in errors]
  queries,refuse=query_tags(case,version)
  states[version]={'diagnostics':diagnostics,'queries':{tag:{'selection':loc(tag),'definition':[loc(x) for x in declarations],'referencesFalse':[loc(x) for x in uses],'referencesTrue':[loc(x) for x in allrefs]} for tag,declarations,uses,allrefs in queries},'renameRefused':[loc(tag) for tag in refuse]}
 m.save(dest/'expected-states.json',{'case':case,'states':states,'envelope':envelope,'bytesUnchanged':True});return work,path,text,states,envelope

def worker(name):
 selected=[c for c in scenarios() if c['name']==name]
 if '--cases' in sys.argv:selected=[c for c in selected if c['name'] in sys.argv[sys.argv.index('--cases')+1].split(',')]
 if not selected:m.save(m.OUT/'result.json',{'pass':False,'caseCount':0,'reason':'empty worker case selection','nativeStarted':False});return 1
 case=selected[0];dest=m.OUT;checks={};responses={};client=None;timer=None;fatal=None
 try:work,path,text,states,envelope=prepared(case,dest)
 except Exception:
  error=traceback.format_exc();(dest/'error.txt').write_text(error);m.save(dest/'result.json',{'pass':False,'fatal':error,'nativeStarted':False});return 3
 def published(diags,version):return ([] if version=='3.6.0' else [{'range':{'start':{'line':0,'character':0},'end':{'line':0,'character':0}},'code':'WF_EXPERIMENTAL_TARGET','source':'argo-workflows','severity':2,'message':'target '+version+' is experimental; only 3.6.0 has completed local-contract acceptance'}])+[{k:d[k] for k in ['range','code','message','source']}|{'severity':{'Error':1,'Warning':2,'Information':3}[d['severity']]} for d in diags]
 def queries(version,prefix):
  state=states[version]
  for tag,wants in state['queries'].items():
   for method in ['definition','referencesFalse','referencesTrue']:
    params={'textDocument':{'uri':path.as_uri()},'position':wants['selection']['range']['start']}
    if method!='definition':params['context']={'includeDeclaration':method=='referencesTrue'}
    response=client.request('textDocument/'+('definition' if method=='definition' else 'references'),params);key=prefix+'-'+tag+'-'+method;responses[key]=response;checks[key]='error' not in response and f.norm(response.get('result',[]))==f.norm(wants[method])
  for i,location in enumerate(state['renameRefused']):
   response=client.request('textDocument/rename',{'textDocument':{'uri':path.as_uri()},'position':location['range']['start'],'newName':'renamed'});key=prefix+'-rename-'+str(i);responses[key]=response;checks[key]=response.get('error',{}).get('code')==-32602 and 'result' not in response
 try:
  initial=case['version'];code,report=f.cli(dest,work);checks.update(f.report_checks(code,report,states[initial]['diagnostics'],envelope,work,initial));client=m.LSP(dest,work);timer=threading.Timer(30,lambda:client.p.kill() if client.p.poll() is None else None);timer.start();checks['publication']=f.norm(client.open(path,text))==f.norm(published(states[initial]['diagnostics'],initial));queries(initial,'initial')
  config=work/'.argo-workflows-ls.yaml'
  for revision,target in enumerate(case['targets'],1):
   cfg='schemaVersion: 1\ntargetVersion: "'+target+'"\nreferencePolicy: closed\n'
   if revision==1:client.send('textDocument/didOpen',{'textDocument':{'uri':config.as_uri(),'version':revision,'languageId':'yaml','text':cfg}})
   else:client.send('textDocument/didChange',{'textDocument':{'uri':config.as_uri(),'version':revision},'contentChanges':[{'text':cfg}]})
   checks['publication-'+str(revision)]=f.norm(client.diagnostics(path.as_uri(),1))==f.norm(published(states[target]['diagnostics'],target));queries(target,'transition-'+str(revision))
  checks['bytes']=path.read_bytes()==text.encode();checks['diskPinUnchanged']=config.read_text()=='schemaVersion: 1\ntargetVersion: "'+initial+'"\nreferencePolicy: closed\n'
 except Exception:fatal=traceback.format_exc();(dest/'error.txt').write_text(fatal)
 finally:
  if client:
   try:client.close()
   except Exception:fatal=(fatal or '')+traceback.format_exc()
  if timer:timer.cancel()
  checks['naturalExit']=client is not None and client.p.returncode==0
  m.save(dest/'responses.json',responses);m.save(dest/'result.json',{'case':name,'checks':checks,'fatal':fatal,'pass':bool(checks) and all(checks.values()) and fatal is None});m.save(dest/'manifest.json',{'command':sys.argv,'nativeSha256':f.digest(m.BINARY),'consumerSha256':m.sha(Path(__file__)),'familySha256':m.sha(HERE/'wf-inheritance-family.py'),'helperSha256':m.sha(HERE/'wf-audit-gaps.py'),'expectedSha256':m.sha(dest/'expected-states.json'),'inputSha256':m.sha(path)})
 return 3 if fatal or not checks['naturalExit'] else 0 if all(checks.values()) else 1

def run():
 if '--worker' in sys.argv:return worker(sys.argv[sys.argv.index('--worker')+1])
 selected=scenarios()
 if '--cases' in sys.argv:selected=[c for c in selected if c['name'] in sys.argv[sys.argv.index('--cases')+1].split(',')]
 if not selected:m.save(m.OUT/'results.json',{'pass':False,'reason':'empty case selection','cases':[]});return 1
 rows=[];deadline=time.monotonic()+7200
 for case in selected:
  dest=m.OUT/case['name'];dest.mkdir(exist_ok=False)
  if '--prepare' in sys.argv:prepared(case,dest);continue
  command=[sys.executable,str(Path(__file__).resolve()),str(m.BINARY),str(dest),'--worker',case['name']];code=124;start=time.time()
  with (dest/'driver.stdout').open('wb') as so,(dest/'driver.stderr').open('wb') as se:
   p=subprocess.Popen(command,stdout=so,stderr=se,start_new_session=True)
   try:code=p.wait(timeout=min(40,max(.1,deadline-time.monotonic())))
   except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait();(dest/'timeout.txt').write_text('Per-case or overall deadline exceeded; no retry\n')
   if code not in [0,1]:
    try:os.killpg(p.pid,signal.SIGKILL)
    except ProcessLookupError:pass
  rows.append({'case':case['name'],'command':command,'exitCode':code,'startUnix':start,'endUnix':time.time()});m.save(m.OUT/'results.json',rows);print(case['name'],code,flush=True)
  if code not in [0,1]:break
 m.save(m.OUT/'manifest.json',{'command':sys.argv,'nativeSha256':f.digest(m.BINARY),'consumerSha256':m.sha(Path(__file__)),'familySha256':m.sha(HERE/'wf-inheritance-family.py'),'helperSha256':m.sha(HERE/'wf-audit-gaps.py'),'expectedCases':[c['name'] for c in selected],'caseCount':len(rows),'python':sys.version});return 0 if '--prepare' in sys.argv or len(rows)==len(selected) and all(x['exitCode']==0 for x in rows) else 1
if __name__=='__main__':raise SystemExit(run())

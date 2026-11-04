#!/usr/bin/env python3
"""Shared entrypoint/onExit roles: exact CLI/LSP identities and the 100 ms query budget.
Run under an independent 90-second watchdog and 4 CPU / 8 GiB constraints.
"""
import importlib.util,json,pathlib,sys,time,threading,traceback,hashlib
HERE=pathlib.Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('wire',HERE/'wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
OUT=m.OUT;WORK=OUT/'input';WORK.mkdir(parents=True,exist_ok=False)
(WORK/'.argo-workflows-ls.yaml').write_text('schemaVersion: 1\ntargetVersion: "3.6.0"\nreferencePolicy: closed\n')
library='apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: {name: library, namespace: argo}\nspec:\n  entrypoint: main\n  onExit: cleanup\n  templates:\n'
for name in ['main','cleanup']:
 library+='    - name: '+name+'\n      inputs:\n        parameters:\n'+''.join(f'          - {{name: p{i:03d}, default: hello}}\n' for i in range(100))+'      container:\n        image: alpine\n        args:\n'+''.join(f'          - "{{{{inputs.parameters.p{i:03d}}}}}"\n' for i in range(100))
caller='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: CALLER, namespace: argo}\nspec:\n  workflowTemplateRef: {name: library}\n  entrypoint: main\n  onExit: cleanup\n  arguments:\n    parameters:\n'+''.join(f'      - {{name: p{i:03d}, value: caller}}\n' for i in range(100))
def location(path,text,needle,name,occurrence=0):
 off=-1
 for _ in range(occurrence+1):off=text.index(needle,off+1)
 off+=len(needle)-len(name);head=text[:off];pos={'line':head.count('\n'),'character':len(head.rsplit('\n',1)[-1].encode('utf-16-le'))//2}
 return {'uri':path.as_uri(),'range':{'start':pos,'end':dict(pos,character=pos['character']+len(name))}}
lib=WORK/'library.yaml';lib.write_text(library);exit_decl=location(lib,library,'name: cleanup','cleanup');base_exit=location(lib,library,'onExit: cleanup','cleanup');entry_input=location(lib,library,'name: p099','p099');exit_input=location(lib,library,'name: p099','p099',1);exit_use=location(lib,library,'inputs.parameters.p099','p099',1);exit_refs=[base_exit];input_refs=[exit_use]
for i in range(50):
 path=WORK/f'caller-{i:02d}.yaml';text=caller.replace('CALLER',f'caller-{i:02d}');path.write_text(text);exit_refs.append(location(path,text,'onExit: cleanup','cleanup'));input_refs.append(location(path,text,'name: p099','p099'))
path=WORK/'caller-00.yaml';text=path.read_text();selected_exit=location(path,text,'onExit: cleanup','cleanup');argument=location(path,text,'name: p099','p099')
queries=[{'name':'exit-definition-'+str(i),'method':'definition','selected':selected_exit,'expected':[exit_decl]} for i in range(3)]
queries += [{'name':'exit-references-'+str(inc),'method':'references','selected':selected_exit,'includeDeclaration':inc,'expected':exit_refs+([exit_decl] if inc else [])} for inc in [False,True]]
queries += [{'name':'argument-definition','method':'definition','selected':argument,'expected':[argument,entry_input,exit_input]}]
queries += [{'name':'argument-references-'+str(inc),'method':'references','selected':argument,'includeDeclaration':inc,'expected':[]} for inc in [False,True]]
queries += [{'name':'exit-input-definition','method':'definition','selected':exit_use,'expected':[exit_input]}]
queries += [{'name':'exit-input-references-'+str(inc),'method':'references','selected':exit_use,'includeDeclaration':inc,'expected':input_refs+([exit_input] if inc else [])} for inc in [False,True]]
expected={'queries':queries,'diagnostics':[],'filesAnalyzed':51,'resourcesAnalyzed':51,'analysisComplete':True,'exitCode':0,'notEvaluated':[],'queryBudgetSeconds':0.1}
(OUT/'expected.json').write_text(json.dumps(expected,indent=2)+'\n')
files={str(p.relative_to(WORK)):{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(WORK.iterdir()) if p.is_file()}
assert len(files)==52 and len(exit_refs)==51 and len(input_refs)==51 and len(queries)==11
(OUT/'fixture-manifest.json').write_text(json.dumps({'files':files,'yamlResourceFiles':51,'resourceCount':51,'inputsPerTemplate':100,'templatesPerLibrary':2,'argumentsPerCaller':100,'callerCount':50,'resourceFileBytes':sum(v['bytes'] for k,v in files.items() if not k.startswith('.'))},indent=2)+'\n')

EXPECTED=json.loads((OUT/'expected.json').read_text())

class Client(m.LSP):
    def send(self,method,params=None,ident=None):
        value={'jsonrpc':'2.0','method':method}
        if params is not None:value['params']=params
        if ident is not None:value['id']=ident
        data=json.dumps(value).encode()
        with (self.dest/'lsp.stdin.raw').open('ab') as f:f.write(('Content-Length: %d\r\n\r\n'%len(data)).encode()+data)
        return super().send(method,params,ident)
def norm(rows):return sorted(json.dumps(r,sort_keys=True) for r in rows)
rows=[];rss=[];stop=threading.Event();client=None;checks={};report={'queries':rows,'checks':checks}
def sample(pid):
    while not stop.wait(.05):
        try:
            status=pathlib.Path(f'/proc/{pid}/status').read_text()
            values={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in status.splitlines() if line.startswith(('VmRSS:','VmHWM:'))}
            rss.append({'elapsedSeconds':time.monotonic()-started,**values})
        except FileNotFoundError:return
started=time.monotonic()
try:
    client=Client(m.OUT,WORK);thread=threading.Thread(target=sample,args=(client.p.pid,),daemon=True);thread.start()
    path=WORK/'caller-00.yaml';begin=time.monotonic();ds=client.open(path,path.read_text());report['coldPublicationSeconds']=time.monotonic()-begin
    want=[{k:d[k] for k in ('range','code','source')}|{'severity':2} for d in EXPECTED['diagnostics'] if d['uri']==path.as_uri()]
    checks['initialFullDiagnostics']=norm([{k:d[k] for k in ('range','code','severity','source')} for d in ds])==norm(want)
    for query in EXPECTED['queries']:
        name=query['name'];selected=query['selected'];params={'textDocument':{'uri':selected['uri']},'position':selected['range']['start']};method=query['method']
        if method=='references':params['context']={'includeDeclaration':query['includeDeclaration']}
        begin=time.monotonic();response=client.request('textDocument/'+method,params);elapsed=time.monotonic()-begin
        row={'query':name,'seconds':elapsed,'response':response,'withinQueryBudget':elapsed<=EXPECTED['queryBudgetSeconds'],'exact':'error' not in response and norm(response.get('result',[]))==norm(query['expected'])}
        rows.append(row);m.save(m.OUT/'results.json',report);print(json.dumps({'query':name,'seconds':elapsed,'exact':row['exact'],'locations':len(response.get('result',[]))}),flush=True)
    code,cli=m.cli(m.OUT,WORK)
    report['cli']=cli
    checks['completeCLIDiagnostics']=norm([{k:d[k] for k in ('uri','range','code','severity','source','positionKind')} for d in cli['diagnostics']])==norm(EXPECTED['diagnostics'])
    checks['countsCompletenessExit']=code==0 and cli['analysisComplete'] is True and cli['resourcesAnalyzed']==51 and cli['filesAnalyzed']==51 and not cli['configurationErrors']
    checks['unchangedBytes']=all(m.sha(WORK/name)==record['sha256'] for name,record in files.items())
    checks['unknownEnvelope']=cli['notEvaluated']==EXPECTED['notEvaluated'] and cli['inputSource']=='disk' and cli['product']=='argo-workflows' and cli['validationScope']=='local-editing-contract' and cli['reportSchemaVersion']==1
except Exception:
    (m.OUT/'error.txt').write_text(traceback.format_exc());checks['completed']=False
finally:
    if client:
        try:client.close()
        except Exception:(m.OUT/'close-error.txt').write_text(traceback.format_exc());checks['shutdown']=False
    stop.set()
    if 'thread' in locals():thread.join(timeout=1)
    m.save(m.OUT/'rss.json',rss)
    report['wallSeconds']=time.monotonic()-started
    report['allQueries']=len(rows)==len(EXPECTED['queries'])
    report['allExact']=all(r['exact'] for r in rows) and report['allQueries']
    report['serverExit']=client.p.returncode if client else None
    report['maximumRSSKiB']=max([int(r['VmRSS'].split()[0]) for r in rss if 'VmRSS' in r] or [0])
    report['allWithinQueryBudget']=report['allQueries'] and all(r['withinQueryBudget'] for r in rows)
    report['behaviorPass']=report['allExact'] and bool(checks) and all(checks.values()) and report['serverExit']==0
    report['pass']=report['behaviorPass'] and report['allWithinQueryBudget']
    m.save(m.OUT/'results.json',report)
    m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'helperSha256':m.sha(HERE/'wf-audit-gaps.py'),'expectedSha256':m.sha(WORK.parent/'expected.json'),'fixtureManifestSha256':m.sha(WORK.parent/'fixture-manifest.json')})
raise SystemExit(0 if report['pass'] else 1)

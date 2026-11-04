#!/usr/bin/env python3
"""All locked targets: aggregate globals and independent template validation.
Each target uses real CLI/LSP processes; expected semantics do not import analysis.
"""
from pathlib import Path
import signal,hashlib,importlib.util,json,os,subprocess,sys,threading,time,traceback
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('wire',HERE/'wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
BASE='complete domain rules, version acceptance, and resource budgets are not yet implemented'
KINDS=['Workflow','CronWorkflow','WorkflowTemplate','ClusterWorkflowTemplate']
CASES=[('plain-empty','workflow.parameters',[]),('plain-seed','workflow.parameters',['seed']),('json-empty','workflow.parameters.json',[]),('json-seed','workflow.parameters.json',['seed']),('json-named','workflow.parameters.json',['json']),('named-missing','workflow.parameters.absent',[]),('named-present','workflow.parameters.seed',['seed'])]
def norm(rows):return sorted(json.dumps(x,sort_keys=True) for x in rows)
def position(text,offset):
 head=text[:offset];return {'line':head.count('\n'),'character':len(head.rsplit('\n',1)[-1].encode('utf-16-le'))//2}
def span(text,a,z):return {'start':position(text,a),'end':position(text,z)}
def allowed(version,kind,name):
 modern=tuple(map(int,version.split('.')))>=(3,4,2)
 return kind in KINDS[2:] and modern or name in ['plain-seed','json-named','named-present'] or name=='json-seed' and modern
def fixture(version,key_cases=False):
 text='';records=[]
 for kind in KINDS:
  for name,tag,parameters in CASES:
   if key_cases and name not in ['plain-empty','json-seed']:continue
   if text:text+='---\n'
   start=len(text);indent='    ' if kind=='CronWorkflow' else '  '
   text+='apiVersion: argoproj.io/v1alpha1\nkind: '+kind+'\nmetadata: {name: '+kind.lower()+'-'+name+', namespace: argo}\nspec:\n'
   if kind=='CronWorkflow':text+='  schedule: "* * * * *"\n  workflowSpec:\n'
   text+=indent+'entrypoint: main\n';decl=None
   if parameters:
    text+=indent+'arguments: {parameters: [{name: ';decl=len(text);text+=parameters[0]+', value: literal}]}\n'
   text+=indent+'templates:\n'+indent+'  - name: main\n'
   token='"{{'+tag+'}}"'
   if key_cases:text+=indent+'    metadata:\n'+indent+'      annotations:\n'+indent+'        ';a=len(text);text+=token+': retained\n'+indent+'    container: {image: alpine}\n'
   else:text+=indent+'    container: {image: alpine, args: [';a=len(text);text+=token+']}\n'
   tail=tag.rsplit('.',1)[-1];use=a+3+len(tag)-len(tail)
   records.append({'case':kind+'-'+name,'kind':kind,'name':name,'missing':not allowed(version,kind,name),'scalar':(a,a+len(token)),'use':(use,use+len(tail)),'decl':(decl,decl+len(parameters[0])) if decl is not None else None,'named':name in ['json-named','named-present'],'resourceStart':start})
 return text,records
def worker(version,key_cases):
 dest=m.OUT;work=dest/'input';work.mkdir(exist_ok=False);text,records=fixture(version,key_cases);path=work/'cases.yaml';path.write_text(text);(work/'.argo-workflows-ls.yaml').write_text('schemaVersion: 1\ntargetVersion: "'+version+'"\nreferencePolicy: closed\n');uri=path.as_uri();loc=lambda bounds:{'uri':uri,'range':span(text,*bounds)}
 expected=[];queries={}
 for case in records:
  if case['missing']:
   message='workflow parameters aggregate is not available' if case['name']=='plain-empty' else 'workflow parameter is not declared: '+('absent' if case['name']=='named-missing' else 'json')
   expected.append(loc(case['scalar'])|{'code':'WF_REFERENCE_SCOPE','severity':'Error','source':'argo-workflows','positionKind':'ambiguous','message':message,'targetId':str(work)+'#root'})
  queries[case['case']]={'selection':loc(case['use']),'definition':[loc(case['decl'])] if case['named'] else [],'referencesFalse':[loc(case['use'])] if case['named'] else [],'referencesTrue':[loc(case['use']),loc(case['decl'])] if case['named'] else []}
 envelope={'configurationErrors':[],'filesAnalyzed':1,'resourcesAnalyzed':len(records),'analysisComplete':version=='3.6.0','notEvaluated':[] if version=='3.6.0' else ['target '+version+' is experimental; only 3.6.0 has completed local-contract acceptance']}
 m.save(dest/'expected.json',{'version':version,'cases':records,'diagnostics':expected,'queries':queries,'envelope':envelope,'exitCode':int(any(d['severity']=='Error' for d in expected)) if version=='3.6.0' else 2})
 checks={};responses={};client=None;fatal=None;timer=None;start=time.monotonic()
 try:
  code,report=m.cli(dest,work);keys=['uri','range','code','severity','source','positionKind','message','targetId'];actual=[{k:d[k] for k in keys} for d in report['diagnostics']];checks['completeCLI']=norm(actual)==norm(expected);checks['envelope']=code==(int(any(d['severity']=='Error' for d in expected)) if version=='3.6.0' else 2) and {k:report.get(k) for k in envelope}==envelope;catalog_path=HERE.parents[1]/'compat/catalog.lock.json';entry=next(x for x in json.loads(catalog_path.read_text())['versions'] if x['version']==version);target={'id':str(work)+'#root','root':str(work),'scope':'root','version':version,'source':'root','schemaHash':entry['schemaSha256'],'rulesetId':None,'analysisComplete':version=='3.6.0'};checks['target']=len(report['targets'])==1 and {k:report['targets'][0].get(k) for k in target}==target;checks['catalog']=report['catalogHash']==m.sha(catalog_path);config_hash=report['targets'][0].get('configHash','');checks['configHashShape']=len(config_hash)==64 and all(c in '0123456789abcdef' for c in config_hash);checks['reportIdentity']=report['product']=='argo-workflows' and report['inputSource']=='disk' and report['reportSchemaVersion']==1 and report['validationScope']=='local-editing-contract'
  client=m.LSP(dest,work);timer=threading.Timer(30,lambda:client.p.kill() if client.p.poll() is None else None);timer.start();published=client.open(path,text);want=([] if version=='3.6.0' else [{'range':{'start':{'line':0,'character':0},'end':{'line':0,'character':0}},'code':'WF_EXPERIMENTAL_TARGET','source':'argo-workflows','severity':2,'message':'target '+version+' is experimental; only 3.6.0 has completed local-contract acceptance'}])+[{k:d[k] for k in ['range','code','message','source']}|{'severity':1} for d in expected];checks['completePublication']=norm(published)==norm(want)
  outcomes=[]
  for case in records:
   wants=queries[case['case']];qchecks={}
   for method in ['definition','referencesFalse','referencesTrue']:
    params={'textDocument':{'uri':uri},'position':wants['selection']['range']['start']}
    if method!='definition':params['context']={'includeDeclaration':method=='referencesTrue'}
    response=client.request('textDocument/'+('definition' if method=='definition' else 'references'),params);responses[case['case']+'-'+method]=response;qchecks[method]='error' not in response and norm(response.get('result',[]))==norm(wants[method])
   case_expected=[d for d in expected if d['range']==span(text,*case['scalar'])];case_actual=[d for d in actual if d['range']==span(text,*case['scalar'])];case_publication=[d for d in published if d['range']==span(text,*case['scalar'])];case_want=([] if version=='3.6.0' else [{'range':{'start':{'line':0,'character':0},'end':{'line':0,'character':0}},'code':'WF_EXPERIMENTAL_TARGET','source':'argo-workflows','severity':2,'message':'target '+version+' is experimental; only 3.6.0 has completed local-contract acceptance'}])+[{k:d[k] for k in ['range','code','message','source']}|{'severity':1} for d in case_expected]
   qchecks['CLI']=norm(case_actual)==norm(case_expected);qchecks['publication']=norm(case_publication)==norm(case_want);outcomes.append({'case':case['case'],'checks':qchecks,'pass':all(qchecks.values())})
   m.save(dest/'case-results.json',outcomes)
  checks['allCases']=len(outcomes)==len(records) and all(x['pass'] for x in outcomes);checks['bytes']=path.read_bytes()==text.encode();m.save(dest/'responses.json',responses)
 except Exception:fatal=traceback.format_exc();(dest/'error.txt').write_text(fatal)
 finally:
  if client:
   try:client.close()
   except Exception:fatal=(fatal or '')+traceback.format_exc()
  if timer:timer.cancel()
  checks['naturalExit']=client is not None and client.p.returncode==0
  result={'version':version,'keyCases':key_cases,'checks':checks,'caseCount':len(records),'pass':bool(checks) and all(checks.values()) and fatal is None,'fatal':fatal,'seconds':time.monotonic()-start};m.save(dest/'result.json',result);m.save(dest/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'consumerSha256':m.sha(Path(__file__)),'helperSha256':m.sha(HERE/'wf-audit-gaps.py'),'expectedSha256':m.sha(dest/'expected.json'),'inputSha256':m.sha(path),'configSha256':m.sha(work/'.argo-workflows-ls.yaml')})
 return 3 if fatal or not checks['naturalExit'] else 0 if result['pass'] else 1
if '--worker' in sys.argv:raise SystemExit(worker(sys.argv[sys.argv.index('--worker')+1],'--keys' in sys.argv))
catalog=json.loads((HERE.parents[1]/'compat/catalog.lock.json').read_text());versions=[x['version'] for x in catalog['versions']]
if '--targets' in sys.argv:versions=[v for v in versions if v in sys.argv[sys.argv.index('--targets')+1].split(',')]
keys='--keys' in sys.argv
if keys:versions=[v for v in versions if v in ['3.4.1','3.4.2']]
rows=[];deadline=time.monotonic()+7200
if not versions:m.save(m.OUT/'results.json',{'pass':False,'reason':'empty target selection','targets':[]});raise SystemExit(1)
for version in versions:
 dest=m.OUT/version;dest.mkdir(exist_ok=False);command=[sys.executable,str(Path(__file__).resolve()),str(m.BINARY),str(dest),'--worker',version]+(['--keys'] if keys else []);start=time.time();code=124
 with (dest/'driver.stdout').open('wb') as so,(dest/'driver.stderr').open('wb') as se:
  try:
   p=subprocess.Popen(command,stdout=so,stderr=se,start_new_session=True);code=p.wait(timeout=min(40,max(.1,deadline-time.monotonic())))
  except subprocess.TimeoutExpired:
   os.killpg(p.pid,signal.SIGKILL);p.wait();(dest/'timeout.txt').write_text('40-second per-target driver or overall deadline exceeded; group killed, no retry\n')
  if code not in [0,1]:
   try:os.killpg(p.pid,signal.SIGKILL)
   except ProcessLookupError:pass
 row={'version':version,'command':command,'exitCode':code,'startUnix':start,'endUnix':time.time()};rows.append(row);m.save(m.OUT/'results.json',rows);print(version,code,flush=True)
 if code not in [0,1]:break
m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(Path(__file__)),'helperSha256':m.sha(HERE/'wf-audit-gaps.py'),'catalogSha256':m.sha(HERE.parents[1]/'compat/catalog.lock.json'),'expectedTargets':versions,'targetCount':len(rows),'casesPerTarget':8 if keys else 28,'allTargetsComplete':len(rows)==len(versions),'python':sys.version})
raise SystemExit(0 if len(rows)==len(versions) and all(x['exitCode']==0 for x in rows) else 1)

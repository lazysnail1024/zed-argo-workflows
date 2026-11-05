#!/usr/bin/env python3
"""Independent all-target DAG mode/result/loop CLI contracts and exact envelopes."""
from pathlib import Path
import importlib.util,json,os,re,signal,subprocess,sys,time,traceback
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('wire',HERE/'wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
BASE='complete domain rules, version acceptance, and resource budgets are not yet implemented'
MODE='WF_DEPENDENCY_MODE_CONFLICT';LOOP='WF_DEPENDENCY_LOOP_REQUIRED';SYNTAX='WF_DEPENDS_SYNTAX'
LIST_MESSAGE='DAG cannot combine nonempty depends with dependencies'
CONTINUE_MESSAGE='DAG cannot combine nonempty depends with continueOn'
LOOP_MESSAGE='DAG task A must declare a loop for an items result'
OMITTED_MESSAGE='task result "Omitted" is not available before 3.3.9'
def norm(rows):return sorted(json.dumps(x,sort_keys=True) for x in rows)
def pos(text,offset):
 head=text[:offset];return {'line':head.count('\n'),'character':len(head.rsplit('\n',1)[-1].encode('utf-16-le'))//2}
def span(text,bounds):return {'start':pos(text,bounds[0]),'end':pos(text,bounds[1])}
def strip(marked):
 # Explicit source labels define the oracle; no production parsing is imported.
 text='';tags={};cursor=0
 for match in re.finditer(r'\[\[([A-Za-z0-9_-]+)\|(.*?)\]\](?!\])',marked,re.S):
  text+=marked[cursor:match.start()];a=len(text);text+=match[2];tags[match[1]]=(a,len(text));cursor=match.end()
 text+=marked[cursor:]
 # The task itself is the CEL diagnostic range, independent of a field's marker.
 start=text.index('{name: B,');end=text.index('\n',start)
 tags['task_b']=(start,end)
 return text,tags
def document(name,tasks):
 return 'apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: '+name.lower()+'}\nspec:\n  entrypoint: main\n  templates:\n    - name: main\n      dag:\n        tasks:\n'+''.join('          - '+t+'\n' for t in tasks)+'    - name: worker\n      container: {image: alpine}\n'
def task(name,fields=''):return '{name: '+name+', template: worker'+(', '+fields if fields else '')+'}'
def cases(version):
 rows=[]
 def add(name,a='',b='',c=None,errors=()):rows.append((name,document(name,[task('A',a),task('B',b)]+([task('C',c)] if c is not None else [])),list(errors)))
 list_error=('bad',MODE,LIST_MESSAGE,'exact');continue_error=('bad',MODE,CONTINUE_MESSAGE,'exact')
 add('cross-list',b='depends: A.Succeeded',c='dependencies: [[bad|[A]]]',errors=[list_error])
 add('same-list',b='depends: A.Succeeded, dependencies: [[bad|[A]]]',errors=[list_error])
 add('cross-continue-empty',a='continueOn: [[bad|{}]]',b='depends: A.Succeeded',errors=[continue_error])
 add('cross-continue-false',a='continueOn: [[bad|{failed: false}]]',b='depends: A.Succeeded',errors=[continue_error])
 add('same-continue',b='depends: A.Succeeded, continueOn: [[bad|{}]]',errors=[continue_error])
 add('same-empty-list',b='depends: A.Succeeded, dependencies: []')
 add('cross-empty-list',b='depends: A.Succeeded',c='dependencies: []')
 add('empty-depends-list-continue',a='continueOn: {}',b='depends: "", dependencies: [A]')
 add('list-continue',a='continueOn: {}',b='dependencies: [A]')
 add('all-empty',b='depends: "", dependencies: [], continueOn: {}')
 for result in ['Skipped','bare','Bogus','Omitted']:
  expression='A'+('' if result=='bare' else '.'+result);errors=[]
  if result=='Bogus':errors=[('bad',SYNTAX,'unknown task result "Bogus"','exact')]
  if result=='Omitted' and tuple(map(int,version.split('.')))<(3,3,9):errors=[('bad',SYNTAX,OMITTED_MESSAGE,'exact')]
  add('result-'+result,b='depends: [[bad|'+expression+']]',errors=errors)
 for suffix in ['AnySucceeded','AllFailed']:
  for name,field,required in [('absent','',True),('items-empty','withItems: []',True),('param-empty','withParam: ""',True),('items-one','withItems: [{item: one}]',False),('param-json-empty','withParam: "[]"',False),('sequence-empty','withSequence: {}',False),('sequence-count','withSequence: {count: "1"}',False)]:
   add(suffix+'-'+name,a=field,b='depends: [[bad|A.'+suffix+']]',errors=[('bad',LOOP,LOOP_MESSAGE,'exact')] if required else [])
 if tuple(map(int,version.split('.'))) >= (4,0,0):
  for name,marked,errors in rows:
   if name in ['same-list','same-continue','same-empty-list','empty-depends-list-continue','all-empty']:
    errors.append(('task_b','SCHEMA_VALIDATION',"at '/spec/templates/0/dag/tasks/1': 'not' failed",'exact'))
 return rows
def selected_cases(version):
 rows=cases(version)
 if '--cases' in sys.argv:rows=[c for c in rows if c[0] in sys.argv[sys.argv.index('--cases')+1].split(',')]
 return rows
def prepare(version,rows,dest):
 work=dest/'input';work.mkdir(parents=True,exist_ok=False);text='';records=[];expected=[];path=work/'cases.yaml'
 for name,marked,errors in rows:
  if text:text+='---\n'
  start=len(text);value,tags=strip(marked);text+=value;tags={k:(a+start,z+start) for k,(a,z) in tags.items()}
  diagnostics=[{'uri':path.as_uri(),'range':span(text,tags[tag]),'code':code,'message':message,'severity':'Error','source':'argo-workflows','positionKind':origin,'targetId':str(work)+'#root'} for tag,code,message,origin in errors]
  expected+=diagnostics;records.append({'case':name,'range':span(text,(start,len(text))),'tags':tags,'diagnostics':diagnostics})
 path.write_bytes(text.encode());(work/'.argo-workflows-ls.yaml').write_text('schemaVersion: 1\ntargetVersion: "'+version+'"\nreferencePolicy: closed\n')
 envelope={'configurationErrors':[],'filesAnalyzed':1,'resourcesAnalyzed':len(records),'analysisComplete':True,'notEvaluated':[]}
 m.save(dest/'expected.json',{'version':version,'cases':records,'diagnostics':expected,'envelope':envelope,'exitCode':int(any(d['severity']=='Error' for d in expected))});return work,path,text,records,expected,envelope
def report_checks(code,report,expected,envelope,work,version):
 keys=['uri','range','code','message','severity','source','positionKind','targetId'];catalog=HERE.parents[1]/'compat/catalog.lock.json';entry=next(x for x in json.loads(catalog.read_text())['versions'] if x['version']==version)
 target={'id':str(work)+'#root','root':str(work),'scope':'root','version':version,'source':'root','schemaHash':entry['schemaSha256'],'rulesetId':None,'analysisComplete':True};targets=report.get('targets',[]);config_hash=targets[0].get('configHash','') if targets else ''
 return {'completeDiagnostics':norm([{k:d[k] for k in keys} for d in report['diagnostics']])==norm(expected),'envelope':code==int(any(d['severity']=='Error' for d in expected)) and {k:report.get(k) for k in envelope}==envelope,'target':len(targets)==1 and {k:targets[0].get(k) for k in target}==target,'catalog':report['catalogHash']==m.sha(catalog),'configHashShape':len(config_hash)==64 and all(c in '0123456789abcdef' for c in config_hash),'reportIdentity':report['product']=='argo-workflows' and report['inputSource']=='disk' and report['reportSchemaVersion']==1 and report['validationScope']=='local-editing-contract'}
def worker(version):
 dest=m.OUT;rows=selected_cases(version)
 if not rows:
  m.save(dest/'result.json',{'version':version,'caseCount':0,'pass':False,'reason':'empty worker case selection','nativeStarted':False});return 1
 work,path,text,records,expected,envelope=prepare(version,rows,dest);checks={};fatal=None;start=time.monotonic()
 try:
  code,report=m.cli(dest,work);checks=report_checks(code,report,expected,envelope,work,version);outcomes=[]
  for case in records:
   first=case['range']['start']['line'];last=case['range']['end']['line'];actual=[d for d in report['diagnostics'] if first<=d['range']['start']['line']<last]
   keys=['uri','range','code','message','severity','source','positionKind','targetId'];outcomes.append({'case':case['case'],'pass':norm([{k:d[k] for k in keys} for d in actual])==norm(case['diagnostics'])})
  checks['bytes']=path.read_bytes()==text.encode();m.save(dest/'case-results.json',outcomes)
 except Exception:fatal=traceback.format_exc();(dest/'error.txt').write_text(fatal)
 result={'version':version,'checks':checks,'caseCount':len(records),'pass':bool(checks) and all(checks.values()) and fatal is None,'fatal':fatal,'seconds':time.monotonic()-start};m.save(dest/'result.json',result);m.save(dest/'manifest.json',{'command':sys.argv,'nativeSha256':m.sha(m.BINARY),'consumerSha256':m.sha(Path(__file__)),'helperSha256':m.sha(HERE/'wf-audit-gaps.py'),'expectedSha256':m.sha(dest/'expected.json'),'inputSha256':m.sha(path),'configSha256':m.sha(work/'.argo-workflows-ls.yaml')});return 3 if fatal else 0 if result['pass'] else 1
def run():
 if '--worker' in sys.argv:return worker(sys.argv[sys.argv.index('--worker')+1])
 versions=[x['version'] for x in json.loads((HERE.parents[1]/'compat/catalog.lock.json').read_text())['versions']]
 if '--targets' in sys.argv:versions=[v for v in versions if v in sys.argv[sys.argv.index('--targets')+1].split(',')]
 if not versions or not selected_cases(versions[0]):m.save(m.OUT/'results.json',{'pass':False,'reason':'empty target or case selection','targets':versions,'cases':[]});return 1
 rows=[];deadline=time.monotonic()+7200
 for version in versions:
  dest=m.OUT/version;dest.mkdir(exist_ok=False)
  if '--prepare' in sys.argv:prepare(version,selected_cases(version),dest);continue
  command=[sys.executable,str(Path(__file__).resolve()),str(m.BINARY),str(dest),'--worker',version]+(['--cases',sys.argv[sys.argv.index('--cases')+1]] if '--cases' in sys.argv else []);start=time.time();code=124
  with (dest/'driver.stdout').open('wb') as so,(dest/'driver.stderr').open('wb') as se:
   p=subprocess.Popen(command,stdout=so,stderr=se,start_new_session=True)
   try:code=p.wait(timeout=min(40,max(.1,deadline-time.monotonic())))
   except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait();(dest/'timeout.txt').write_text('Per-target or overall deadline exceeded; no retry\n')
   if code not in [0,1]:
    try:os.killpg(p.pid,signal.SIGKILL)
    except ProcessLookupError:pass
  rows.append({'version':version,'command':command,'exitCode':code,'startUnix':start,'endUnix':time.time()});m.save(m.OUT/'results.json',rows);print(version,code,flush=True)
  if code not in [0,1]:break
 m.save(m.OUT/'manifest.json',{'command':sys.argv,'nativeSha256':m.sha(m.BINARY),'consumerSha256':m.sha(Path(__file__)),'helperSha256':m.sha(HERE/'wf-audit-gaps.py'),'catalogSha256':m.sha(HERE.parents[1]/'compat/catalog.lock.json'),'expectedTargets':versions,'targetCount':len(rows),'casesPerTarget':len(selected_cases(versions[0])),'python':sys.version})
 return 0 if '--prepare' in sys.argv or len(rows)==len(versions) and all(x['exitCode']==0 for x in rows) else 1
if __name__=='__main__':raise SystemExit(run())

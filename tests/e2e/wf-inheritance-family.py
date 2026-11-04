#!/usr/bin/env python3
"""Historical call and parameter contracts through real all-target CLI processes."""
from pathlib import Path
import importlib.util,json,os,re,signal,subprocess,sys,time,traceback
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('wire',HERE/'wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
BASE='complete domain rules, version acceptance, and resource budgets are not yet implemented'
def norm(rows):return sorted(json.dumps(x,sort_keys=True) for x in rows)
def pos(text,offset):
 head=text[:offset];return {'line':head.count('\n'),'character':len(head.rsplit('\n',1)[-1].encode('utf-16-le'))//2}
def span(text,bounds):return {'start':pos(text,bounds[0]),'end':pos(text,bounds[1])}
def strip(marked):
 text='';tags={};cursor=0
 for match in re.finditer(r'\[\[([A-Za-z0-9_-]+)\|(.*?)\]\](?!\])',marked,re.S):
  if match[1] in tags:raise ValueError('duplicate source label '+match[1])
  text+=marked[cursor:match.start()];a=len(text);text+=match[2];tags[match[1]]=(a,len(text));cursor=match.end()
 text+=marked[cursor:]
 if 'form' in tags and 'configMapKeyRef' in text[tags['form'][0]:tags['form'][1]]:
  start=text.index('configMapKeyRef',tags['form'][0]);tags['cm']=(start,start+len('configMapKeyRef'))
 return text,tags
def error(tag,code,message):return (tag,code,message,'Error','exact')
def missing(tag='entry',name='p'):return error(tag,'WF_ARGUMENT_MISSING','required input parameters.'+name+' was not supplied')
def invalid(tag='argument',form=False):return error(tag,'WF_ARGUMENT_VALUE_INVALID','argument parameter p has a valueFrom form that is not allowed' if form else 'argument parameter p requires value or an allowed valueFrom')
def local(tag='exit',name='cleanup'):return error(tag,'WF_LOCAL_TEMPLATE_MISSING','local template does not exist: '+name)
def unresolved(tag='entry',name='absent'):return error(tag,'WF_TEMPLATE_REF_UNRESOLVED','resolved resource does not declare template '+name)
def template(name='main',inputs='',body=None):
 return '    - name: '+name+'\n'+('      inputs: {parameters: ['+inputs+']}\n' if inputs else '')+(body or '      container: {image: alpine}\n')
def document(name,kind,fields,templates):
 spec=fields+('  templates:\n'+templates if templates is not None else '')
 if kind=='CronWorkflow':spec='  schedule: "* * * * *"\n  workflowSpec:\n'+''.join('  '+line+'\n' for line in spec.splitlines())
 return 'apiVersion: argoproj.io/v1alpha1\nkind: '+kind+'\nmetadata: {name: '+name.lower()+', namespace: argo}\nspec:\n'+spec
def args(items):return '  arguments: {parameters: ['+items+']}\n'
def cases(version):
 v=tuple(map(int,version.split('.')));rows=[]
 def add(name,caller,errors=(),base=None):rows.append((name,caller+('---\n'+base if base else ''),list(errors)))
 def one(name,kind='Workflow',arguments='',inputs='{name: p}',fields='  entrypoint: [[entry|main]]\n',errors=(),body=None):
  add(name,document(name,kind,fields+arguments,template(inputs=inputs,body=body)),errors)
 def whole(name,fields='',basefields='  entrypoint: main\n',basetemplates=None,errors=(),kind='Workflow',localtemplates=None):
  library=name+'-library';caller=document(name,kind,'  workflowTemplateRef: {name: [[ref|'+library+']]}\n'+fields,localtemplates)
  base=document(library,'WorkflowTemplate',basefields,basetemplates if basetemplates is not None else template())
  add(name,caller,errors,base)
 one('required-missing',errors=[missing()])
 one('required-empty-string',arguments=args('{name: p, value: ""}'))
 one('required-literal',arguments=args('{name: p, value: literal}'))
 one('required-default',inputs='{name: p, default: safe}')
 whole('name-only-overrides-base',fields=args('{name: [[argument|p]]}'),basefields='  entrypoint: main\n'+args('{name: p, value: base}'),basetemplates=template(inputs='{name: p, default: safe}'),errors=[invalid()]+([missing('ref')] if v>=(3,4,10) else []))
 one('configmap-argument-required-input',arguments=args('{name: [[argument|p]], valueFrom: {[[cm|configMapKeyRef]]: {name: cfg, key: p}}}'),errors=([invalid()] if v<(3,2,0) else [])+([missing()] if v<(3,2,5) else []))
 marker='__argo-internal.absent-optional-output__'
 one('marker-required',arguments=args('{name: p, value: "'+marker+'"}'),errors=[missing()] if v>=(3,7,16) else [])
 one('marker-default',arguments=args('{name: p, value: "'+marker+'"}'),inputs='{name: p, default: safe}')
 one('near-marker-literal',arguments=args('{name: p, value: "'+marker+'x"}'))
 def exit_default(name,kind,argument=False):
  fields='  entrypoint: main\n  onExit: [[exit|cleanup]]\n'+(args('{name: [[argument|p]]}') if argument else '')
  errors=([invalid()] if v<(3,2,4) else [missing('exit')] if v>=(3,4,10) else []) if argument else []
  add(name,document(name,kind,fields,template()+template('cleanup','{name: p, default: safe}')),errors)
 exit_default('template-exit-default','WorkflowTemplate')
 for kind in ['WorkflowTemplate','ClusterWorkflowTemplate']:
  exit_default(kind+'-name-only-exit-default',kind,True)
  one(kind+'-fake-entry-input',kind)
 one('unused-name-only-argument',arguments=args('{name: [[argument|p]]}'),inputs='',errors=[invalid()])
 restricted=v[1]>5 or v[1]==5 and v[2]>=5 or v[1]==4 and v[2]>=17
 for name,source,bad in [('configmap-form-with-literal','{[[cm|configMapKeyRef]]: {name: cfg, key: p}}',False),('path-form-with-literal','{path: /tmp/p}',True),('configmap-plus-path-with-literal','{[[cm|configMapKeyRef]]: {name: cfg, key: p}, path: /tmp/p}',True)]:
  one(name,arguments=args('{name: p, value: literal, valueFrom: '+('[[form|'+source.replace('[[cm|configMapKeyRef]]','configMapKeyRef')+']]')+'}'),inputs='',errors=[invalid('form',True)] if bad and restricted else [])
 whole('entry-fallback')
 whole('entry-override',fields='  entrypoint: alternate\n',basetemplates=template()+template('alternate'))
 whole('entry-explicit-missing',fields='  entrypoint: [[entry|absent]]\n',errors=[unresolved()])
 whole('entry-fallback-absent',basefields='',errors=[error('ref','WF_TEMPLATE_REF_UNRESOLVED','effective entrypoint is required')])
 whole('caller-whole-object-precedence',fields=args('{name: p, value: caller}'),basefields='  entrypoint: main\n'+args('{name: p, value: base}, {name: q, value: base}'),basetemplates=template(inputs='{name: p}, {name: q}'))
 exits=template()+template('cleanup','{name: q}')+template('alternate')
 baseexit='  entrypoint: main\n  onExit: cleanup\n'+args('{name: q, value: base}')
 whole('exit-explicit-known',fields='  onExit: [[exit|cleanup]]\n'+args('{name: q, value: caller}'),basefields=baseexit,basetemplates=exits,errors=[local()] if v<(3,4,6) else [])
 whole('exit-explicit-missing',fields='  onExit: [[exit|absent]]\n',basefields=baseexit,basetemplates=exits,errors=[local(name='absent')] if v<(3,4,6) else [unresolved('exit')])
 whole('exit-explicit-override',fields='  onExit: [[exit|alternate]]\n',basefields=baseexit,basetemplates=exits,errors=[local(name='alternate')] if v<(3,4,6) else [])
 whole('exit-fallback-missing',basefields=baseexit,basetemplates=exits,errors=[missing('ref','q')] if v>=(3,4,6) else [])
 whole('exit-fallback-provided',fields=args('{name: q, value: caller}'),basefields=baseexit,basetemplates=exits)
 whole('exit-none')
 whole('empty-local-templates',fields='  templates: []\n')
 whole('nonempty-local-templates',fields='  templates: [[templates|[{name: local, container: {image: alpine}}]]]\n',errors=[error('templates','WF_TEMPLATE_REF_TEMPLATES','local templates cannot be combined with workflowTemplateRef')])
 whole('template-ignore-bad-fallback',kind='WorkflowTemplate',basefields='  entrypoint: [[baseEntry|absent]]\n',errors=[local('baseEntry','absent')])
 def global_case(name,visited,present,kind='Workflow'):
  target='leaf' if name.startswith('invoked') else 'main' if visited else 'unused'
  body='      container: {image: alpine, args: [ [[body|"{{workflow.parameters.p}}"]] ]}\n'
  if target=='main':templates=template(body=body)
  elif target=='leaf':templates=template(body='      steps:\n        - [{name: invoke, template: child}]\n')+template('child',body='      dag:\n        tasks: [{name: invoke, template: leaf}]\n')+template('leaf',body=body)
  else:templates=template()+template('unused',body=body)
  errors=[]
  if not present:
   if v<(3,4,2):errors.append(error('body','WF_REFERENCE_SCOPE','workflow parameter is not declared: p')[:-1]+('ambiguous',))
   if visited or v>=(3,4,6):errors.append(error('ref','WF_REFERENCE_SCOPE','workflow parameter p is not declared for template '+target+' in workflowTemplateRef '+name+'-library'))
  whole(name,basefields='  entrypoint: main\n'+(args('{name: p, value: literal}') if present else ''),basetemplates=templates,errors=errors,kind=kind)
 for name,visited,present in [('selected-missing',True,False),('selected-present',True,True),('uncalled-missing',False,False),('uncalled-present',False,True),('invoked-child-missing',True,False),('invoked-child-present',True,True)]:global_case(name,visited,present)
 for kind in ['WorkflowTemplate','ClusterWorkflowTemplate']:
  one(kind+'-standalone-missing',kind,inputs='',body='      container: {image: alpine, args: [ [[body|"{{workflow.parameters.p}}"]] ]}\n',errors=[error('body','WF_REFERENCE_SCOPE','workflow parameter is not declared: p')[:-1]+('ambiguous',)] if v<(3,4,2) else [])
 one('cron-required-missing','CronWorkflow',errors=[missing()])
 one('cron-required-empty','CronWorkflow',arguments=args('{name: p, value: ""}'))
 whole('cron-explicit-exit',kind='CronWorkflow',fields='  onExit: [[exit|cleanup]]\n'+args('{name: q, value: caller}'),basefields=baseexit,basetemplates=exits,errors=[local()] if v<(3,4,6) else [])
 global_case('cron-uncalled-global',False,False,'CronWorkflow')
 if v<(3,2,0):
  for name,marked,errors in rows:
   if name in ['configmap-argument-required-input','configmap-form-with-literal','configmap-plus-path-with-literal']:
    errors.append(('cm','SCHEMA_UNKNOWN_FIELD','field \"configMapKeyRef\" is not defined for target '+version,'Warning','exact'))
 assert len(rows)==44
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
  diagnostics=[{'uri':path.as_uri(),'range':span(text,tags[tag]),'code':code,'message':message,'severity':severity,'source':'argo-workflows','positionKind':origin,'targetId':str(work)+'#root'} for tag,code,message,severity,origin in errors]
  expected+=diagnostics;records.append({'case':name,'range':span(text,(start,len(text))),'tags':tags,'diagnostics':diagnostics,'resourceCount':value.splitlines().count('apiVersion: argoproj.io/v1alpha1')})
 path.write_bytes(text.encode());(work/'.argo-workflows-ls.yaml').write_text('schemaVersion: 1\ntargetVersion: "'+version+'"\nreferencePolicy: closed\n')
 envelope={'configurationErrors':[],'filesAnalyzed':1,'resourcesAnalyzed':text.splitlines().count('apiVersion: argoproj.io/v1alpha1'),'analysisComplete':version=='3.6.0','notEvaluated':[] if version=='3.6.0' else ['target '+version+' is experimental; only 3.6.0 has completed local-contract acceptance']}
 m.save(dest/'expected.json',{'version':version,'cases':records,'diagnostics':expected,'envelope':envelope,'exitCode':int(any(d['severity']=='Error' for d in expected)) if version=='3.6.0' else 2});return work,path,text,records,expected,envelope
def report_checks(code,report,expected,envelope,work,version):
 keys=['uri','range','code','message','severity','source','positionKind','targetId'];catalog=HERE.parents[1]/'compat/catalog.lock.json';entry=next(x for x in json.loads(catalog.read_text())['versions'] if x['version']==version)
 target={'id':str(work)+'#root','root':str(work),'scope':'root','version':version,'source':'root','schemaHash':entry['schemaSha256'],'rulesetId':None,'analysisComplete':version=='3.6.0'};targets=report.get('targets',[]);config_hash=targets[0].get('configHash','') if targets else ''
 return {'completeDiagnostics':norm([{k:d[k] for k in keys} for d in report['diagnostics']])==norm(expected),'envelope':code==(int(any(d['severity']=='Error' for d in expected)) if version=='3.6.0' else 2) and {k:report.get(k) for k in envelope}==envelope,'target':len(targets)==1 and {k:targets[0].get(k) for k in target}==target,'catalog':report['catalogHash']==m.sha(catalog),'configHashShape':len(config_hash)==64 and all(c in '0123456789abcdef' for c in config_hash),'reportIdentity':report['product']=='argo-workflows' and report['inputSource']=='disk' and report['reportSchemaVersion']==1 and report['validationScope']=='local-editing-contract'}
def digest(path):
 try:return m.sha(path)
 except OSError:return None

def cli(dest,work):
 argv=[str(m.BINARY),'check','--root',str(work),'--format','json'];started=time.time()
 try:
  result=subprocess.run(argv,capture_output=True,timeout=20)
 except (OSError,subprocess.TimeoutExpired) as exc:
  (dest/'check.stdout').write_bytes(getattr(exc,'stdout',None) or b'');(dest/'check.stderr').write_bytes(getattr(exc,'stderr',None) or b'')
  m.save(dest/'check.command.json',{'argv':argv,'exitCode':None,'error':str(exc),'timeout':isinstance(exc,subprocess.TimeoutExpired),'startUnix':started,'endUnix':time.time()});raise
 (dest/'check.stdout').write_bytes(result.stdout);(dest/'check.stderr').write_bytes(result.stderr)
 m.save(dest/'check.command.json',{'argv':argv,'exitCode':result.returncode,'startUnix':started,'endUnix':time.time()})
 if result.returncode not in [0,1,2]:raise RuntimeError('native terminated outside CLI contract: '+str(result.returncode))
 return result.returncode,json.loads(result.stdout)

def worker(version):
 dest=m.OUT;checks={};fatal=None;start=time.monotonic();records=[]
 try:
  versions=[x['version'] for x in json.loads((HERE.parents[1]/'compat/catalog.lock.json').read_text())['versions']];rows=selected_cases(version) if version in versions and ('--targets' not in sys.argv or version in sys.argv[sys.argv.index('--targets')+1].split(',')) else []
  if not rows:
   m.save(dest/'result.json',{'version':version,'caseCount':0,'pass':False,'reason':'empty worker target or case selection','nativeStarted':False});return 1
  work,path,text,records,expected,envelope=prepare(version,rows,dest)
  code,report=cli(dest,work);checks=report_checks(code,report,expected,envelope,work,version);outcomes=[]
  for case in records:
   first=case['range']['start']['line'];last=case['range']['end']['line'];actual=[d for d in report['diagnostics'] if first<=d['range']['start']['line']<last]
   keys=['uri','range','code','message','severity','source','positionKind','targetId'];outcomes.append({'case':case['case'],'pass':norm([{k:d[k] for k in keys} for d in actual])==norm(case['diagnostics'])})
  checks['bytes']=path.read_bytes()==text.encode();m.save(dest/'case-results.json',outcomes)
 except Exception:
  fatal=traceback.format_exc();(dest/'error.txt').write_text(fatal)
 result={'version':version,'checks':checks,'caseCount':len(records),'pass':bool(checks) and all(checks.values()) and fatal is None,'fatal':fatal,'seconds':time.monotonic()-start};m.save(dest/'result.json',result)
 m.save(dest/'manifest.json',{'command':sys.argv,'nativeSha256':digest(m.BINARY),'consumerSha256':digest(Path(__file__)),'helperSha256':digest(HERE/'wf-audit-gaps.py'),'expectedSha256':digest(dest/'expected.json'),'inputSha256':digest(dest/'input/cases.yaml'),'configSha256':digest(dest/'input/.argo-workflows-ls.yaml')})
 return 3 if fatal else 0 if result['pass'] else 1
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
 m.save(m.OUT/'manifest.json',{'command':sys.argv,'nativeSha256':digest(m.BINARY),'consumerSha256':m.sha(Path(__file__)),'helperSha256':m.sha(HERE/'wf-audit-gaps.py'),'catalogSha256':m.sha(HERE.parents[1]/'compat/catalog.lock.json'),'expectedTargets':versions,'targetCount':len(rows),'casesPerTarget':len(selected_cases(versions[0])),'python':sys.version})
 return 0 if '--prepare' in sys.argv or len(rows)==len(versions) and all(x['exitCode']==0 for x in rows) else 1
if __name__=='__main__':raise SystemExit(run())

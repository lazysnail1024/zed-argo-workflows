#!/usr/bin/env python3
"""Finite DAG result identities, field multiplicity, loop union and edit boundaries."""
from pathlib import Path
import importlib.util,json,os,signal,subprocess,sys,threading,time,traceback
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('family',HERE/'wf-dag-dependency-family.py');f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f);m=f.m

def scenarios():
 rows=[]
 def add(name,expression,fields='',errors=(),duplicate=False,reverse=False,version='3.6.0',rename=False,projected=False,alias=False):
  producer=f.task('[[decl|A]]',fields)
  if projected:producer='{<<: {withItems: [{item: one}]}, name: [[decl|A]], template: worker}'
  tasks=[producer]
  if duplicate:
   tasks=[f.task('[[decl|A]]','withItems: [{item: one}]' if reverse else ''),f.task('[[duplicate|A]]','' if reverse else 'withItems: [{item: one}]')]
  tasks+=[f.task('B','depends: '+expression),f.task('[[siblingDecl|C]]'),f.task('D','depends: [[siblingUse|C]].Skipped')]
  marked=f.document(name,tasks)
  if alias:marked=marked.replace('metadata: {name: '+name.lower()+'}', 'metadata: {name: '+name.lower()+', annotations: {dependency: &dependency A.Skipped}}')
  if rename:marked=marked.replace('metadata: {name: '+name.lower()+'}', 'metadata: {name: '+name.lower()+', annotations: {note: "\U0001f40c"}}').replace('\n','\r\n')
  rows.append({'name':name,'version':version,'marked':marked,'expression':expression,'errors':list(errors),'duplicate':duplicate,'rename':rename,'alias':alias,'transition':name=='omitted-before'})
 for version in ['3.3.8','3.3.9']:
  add('omitted-'+('before' if version=='3.3.8' else 'after'),'[[use1|A]].Omitted',version=version,errors=[('bad',f.SYNTAX,f.OMITTED_MESSAGE,'exact')] if version=='3.3.8' else [])
 add('skipped-rename','[[use1|A]].Skipped',rename=True)
 for suffix in ['AnySucceeded','AllFailed']:
  add(suffix+'-then-bare','[[use1|A]].'+suffix+' || [[use2|A]]',errors=[('bad',f.LOOP,f.LOOP_MESSAGE,'exact')])
  add('bare-then-'+suffix,'[[use1|A]] || [[use2|A]].'+suffix,errors=[('bad',f.LOOP,f.LOOP_MESSAGE,'exact')])
 add('two-items-results-one-error','[[use1|A]].AnySucceeded || [[use2|A]].AllFailed',errors=[('bad',f.LOOP,f.LOOP_MESSAGE,'exact')])
 add('loop-two-occurrences','[[use1|A]].AllFailed || [[use2|A]]',fields='withItems: [{item: one}]')
 add('two-forbidden-fields','[[use1|A]].Succeeded, dependencies: [[list|[A]]], continueOn: [[continue|{}]]',errors=[('list',f.MODE,f.LIST_MESSAGE,'exact'),('continue',f.MODE,f.CONTINUE_MESSAGE,'exact')])
 for reverse in [False,True]:add('duplicate-'+('loop-first' if reverse else 'loop-second'),'[[use1|A]].AnySucceeded',duplicate=True,reverse=reverse,errors=[('duplicate','WF_DUPLICATE_SYMBOL','duplicate DAG task A','exact')])
 add('projected-loop','[[use1|A]].AnySucceeded',projected=True)
 add('alias-depends-no-token','[[alias|*dependency]]',alias=True)
 return rows

def prepared(case,dest):
 work=dest/'input';work.mkdir(parents=True,exist_ok=False);path=work/'case.yaml';text,tags=f.strip(case['marked']);expression,_=f.strip(case['expression']);a=text.index('depends: '+expression)+len('depends: ');tags['bad']=(a,a+len(expression));path.write_bytes(text.encode());cfg='schemaVersion: 1\ntargetVersion: "'+case['version']+'"\nreferencePolicy: closed\n';(work/'.argo-workflows-ls.yaml').write_text(cfg)
 def loc(tag):return {'uri':path.as_uri(),'range':f.span(text,tags[tag])}
 expected=[loc(tag)|{'code':code,'message':message,'severity':'Error','source':'argo-workflows','positionKind':origin,'targetId':str(work)+'#root'} for tag,code,message,origin in case['errors']]
 uses=[loc(tag) for tag in ['use1','use2'] if tag in tags]
 if case['name']=='two-forbidden-fields':
  # The list dependency is an independent real token, in addition to depends.
  a=tags['list'][0]+1;tags['listUse']=(a,a+1);uses.append(loc('listUse'))
 declarations=[loc('decl')]+([loc('duplicate')] if case['duplicate'] else [])
 queries={}
 for tag in ['use1','use2','decl','duplicate','listUse']:
  if tag in tags:queries[tag]={'definition':declarations,'referencesFalse':[] if case['duplicate'] else uses,'referencesTrue':[] if case['duplicate'] else uses+declarations}
 for tag in ['siblingDecl','siblingUse']:queries[tag]={'definition':[loc('siblingDecl')],'referencesFalse':[loc('siblingUse')],'referencesTrue':[loc('siblingDecl'),loc('siblingUse')]}
 if case['alias']:
  # The alias is not a task-name token. Its declaration remains exact but has no exact use.
  queries['alias']={'definition':[],'referencesFalse':[],'referencesTrue':[]}
 envelope={'configurationErrors':[],'filesAnalyzed':1,'resourcesAnalyzed':1,'analysisComplete':case['version']=='3.6.0','notEvaluated':[] if case['version']=='3.6.0' else ['target '+case['version']+' is experimental; only 3.6.0 has completed local-contract acceptance']}
 changed=text
 if case['rename']:
  for a,z in sorted([tags['decl'],tags['use1']],reverse=True):changed=changed[:a]+'renamed'+changed[z:]
 m.save(dest/'expected.json',{'case':case['name'],'version':case['version'],'diagnostics':expected,'queries':{tag:values|{'selection':loc(tag)} for tag,values in queries.items()},'envelope':envelope,'exitCode':2,'renameAfter':changed if case['rename'] else None,'renameRefused':case['alias'] or case['duplicate'],'transitionTargets':['3.3.9','3.3.8'] if case['transition'] else []});return work,path,text,tags,expected,envelope,queries,changed

def worker(name):
 case=next(c for c in scenarios() if c['name']==name);dest=m.OUT;work,path,text,tags,expected,envelope,queries,changed=prepared(case,dest);checks={};client=None;timer=None;fatal=None;responses={}
 def published(rows,version):return ([] if version=='3.6.0' else [{'range':{'start':{'line':0,'character':0},'end':{'line':0,'character':0}},'code':'WF_EXPERIMENTAL_TARGET','source':'argo-workflows','severity':2,'message':'target '+version+' is experimental; only 3.6.0 has completed local-contract acceptance'}])+[{k:d[k] for k in ['range','code','message','source']}|{'severity':1} for d in rows]
 def query_sets(prefix):
  for tag,wants in queries.items():
   for method,want in wants.items():
    params={'textDocument':{'uri':path.as_uri()},'position':f.pos(text,tags[tag][0])}
    if method!='definition':params['context']={'includeDeclaration':method=='referencesTrue'}
    response=client.request('textDocument/'+('definition' if method=='definition' else 'references'),params);key=prefix+'-'+tag+'-'+method;responses[key]=response;checks[key]='error' not in response and f.norm(response.get('result',[]))==f.norm(want)
 try:
  code,report=m.cli(dest,work);checks.update(f.report_checks(code,report,expected,envelope,work,case['version']));client=m.LSP(dest,work);timer=threading.Timer(30,lambda:client.p.kill() if client.p.poll() is None else None);timer.start();checks['publication']=f.norm(client.open(path,text))==f.norm(published(expected,case['version']));query_sets('initial')
  if case['transition']:
   config=work/'.argo-workflows-ls.yaml'
   for revision,target in enumerate(['3.3.9','3.3.8'],1):
    cfg='schemaVersion: 1\ntargetVersion: "'+target+'"\nreferencePolicy: closed\n'
    if revision==1:client.send('textDocument/didOpen',{'textDocument':{'uri':config.as_uri(),'version':revision,'languageId':'yaml','text':cfg}})
    else:client.send('textDocument/didChange',{'textDocument':{'uri':config.as_uri(),'version':revision},'contentChanges':[{'text':cfg}]})
    checks['transition-'+target]=f.norm(client.diagnostics(path.as_uri(),1))==f.norm(published(expected if target=='3.3.8' else [],target));query_sets(target)
  if case['rename'] or case['alias'] or case['duplicate']:
   response=client.request('textDocument/rename',{'textDocument':{'uri':path.as_uri()},'position':f.pos(text,tags['decl'][0]),'newName':'renamed'});responses['rename']=response
   if case['rename']:
    checks['renameAvailable']='result' in response and 'error' not in response
    if checks['renameAvailable']:
     actual=m.apply(text,response['result'],path.as_uri(),1);checks['exactEditBytes']=actual==changed;checks['editedPublication']=client.change(path,actual,2)==[];path.write_bytes(actual.encode());code,report=m.cli(dest,work,'edited');checks.update({'edited-'+k:v for k,v in f.report_checks(code,report,[],envelope,work,case['version']).items()});checks['persistedEdit']=path.read_bytes()==changed.encode()
   else:checks['renameRefused']=response.get('error',{}).get('code')==-32602 and 'result' not in response
  checks['bytes']=path.read_bytes()==(changed if case['rename'] and checks.get('renameAvailable') else text).encode()
 except Exception:fatal=traceback.format_exc();(dest/'error.txt').write_text(fatal)
 finally:
  if client:
   try:client.close()
   except Exception:fatal=(fatal or '')+traceback.format_exc()
  if timer:timer.cancel()
  checks['naturalExit']=client is not None and client.p.returncode==0
  m.save(dest/'responses.json',responses);result={'case':name,'checks':checks,'fatal':fatal,'pass':bool(checks) and all(checks.values()) and fatal is None};m.save(dest/'result.json',result);m.save(dest/'manifest.json',{'command':sys.argv,'nativeSha256':m.sha(m.BINARY),'consumerSha256':m.sha(Path(__file__)),'familySha256':m.sha(HERE/'wf-dag-dependency-family.py'),'helperSha256':m.sha(HERE/'wf-audit-gaps.py'),'expectedSha256':m.sha(dest/'expected.json'),'originalSha256':__import__('hashlib').sha256(text.encode()).hexdigest(),'inputSha256':m.sha(path)})
 return 3 if fatal or not checks['naturalExit'] else 0 if result['pass'] else 1

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
 m.save(m.OUT/'manifest.json',{'command':sys.argv,'nativeSha256':m.sha(m.BINARY),'consumerSha256':m.sha(Path(__file__)),'familySha256':m.sha(HERE/'wf-dag-dependency-family.py'),'helperSha256':m.sha(HERE/'wf-audit-gaps.py'),'expectedCases':[c['name'] for c in selected],'caseCount':len(rows),'python':sys.version});return 0 if '--prepare' in sys.argv or len(rows)==len(selected) and all(x['exitCode']==0 for x in rows) else 1
if __name__=='__main__':raise SystemExit(run())

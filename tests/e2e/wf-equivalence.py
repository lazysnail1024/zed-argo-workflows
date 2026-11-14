#!/usr/bin/env python3
"""Compare event histories to a fresh real LSP with identical final overlays."""
import hashlib, json, pathlib, random, sys, tempfile
from support.session import Session
ROOT=pathlib.Path(__file__).resolve().parents[2];binary=pathlib.Path(sys.argv[1]).resolve();out=pathlib.Path(sys.argv[2]).resolve();out.mkdir(parents=True,exist_ok=True)
case=json.loads((ROOT/'tests/fixtures/contracts/stdio-buffer-lifecycle/equivalence.json').read_text());bad=(ROOT/'tests/fixtures/schema/mixed.yaml').read_text();good=bad.replace('parallelism: wrong','parallelism: 2')
rows=[]
def write(path,text):path.write_bytes(text.encode('utf-8'))
def save(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,indent=2))
def normalize(value,root):
 if isinstance(value,str):return value.replace(root.as_uri(),'ROOT')
 if isinstance(value,list):return [normalize(v,root) for v in value]
 if isinstance(value,dict):return {k:normalize(v,root) for k,v in value.items()}
 return value

def run(name,history,folder):
 work=folder/'workspace';work.mkdir(parents=True);file=work/'workflow.yaml';write(file,good);config=work/'.argo-workflows-ls.yaml';write(config,'schemaVersion: 1\ntargetVersion: "3.6.0"\n');uri=file.as_uri();s=Session([binary,'serve','--stdio'],folder)
 request_id=0;events=[]
 other=folder/'other'
 if name=='root-isolation':
  other.mkdir();write(other/'workflow.yaml','\n'+good.replace('name: example','name: other'));write(other/'.argo-workflows-ls.yaml',config.read_text())
 def request(method,params):
  nonlocal request_id
  request_id+=1;s.send(method,params,request_id);response=s.wait(lambda m:m.get('id')==request_id)
  if 'error' in response:raise AssertionError(response)
  return response['result']
 def change(text,version):
  s.send('textDocument/didChange',{'textDocument':{'uri':uri,'version':version},'contentChanges':[{'text':text}]});params=s.diagnostics(uri,version)
  if any(d['code']=='WF_ANALYSIS_UNAVAILABLE' for d in params['diagnostics']):raise AssertionError('incomplete analysis')
  events.append({'operation':'change','version':version,'text':text});return params
 try:
  request('initialize',{'rootUri':work.as_uri(),'capabilities':{},'workspaceFolders':[{'uri':work.as_uri(),'name':'rootA'}]+([{'uri':other.as_uri(),'name':'rootB'}] if name=='root-isolation' and not history else [])});s.send('initialized',{})
  s.send('textDocument/didOpen',{'textDocument':{'uri':uri,'languageId':'yaml','version':1,'text':bad if history else good}});s.diagnostics(uri,1)
  if history:
   if name=='target-isolation':
    write(config,'schemaVersion: 1\ntargetVersion: "3.5.0"\n');s.send('workspace/didChangeWatchedFiles',{'changes':[{'uri':config.as_uri(),'type':2}]});change(good,2)
    hover=request('textDocument/hover',{'textDocument':{'uri':uri},'position':{'line':6,'character':4}})
    assert '3.5.0' in hover['contents']['value'];write(config,'schemaVersion: 1\ntargetVersion: "3.6.0"\n');s.send('workspace/didChangeWatchedFiles',{'changes':[{'uri':config.as_uri(),'type':2}]})
   elif name=='root-isolation':
    s.send('workspace/didChangeWorkspaceFolders',{'event':{'added':[{'uri':other.as_uri(),'name':'other'}],'removed':[]}})
   elif name=='delete-recreate':
    file.unlink();s.send('workspace/didChangeWatchedFiles',{'changes':[{'uri':uri,'type':3}]});change(good,2);write(file,good);s.send('workspace/didChangeWatchedFiles',{'changes':[{'uri':uri,'type':1}]})
   elif name.startswith('seed-'):
    rng=random.Random(int(name.split('-')[1]));
    for version in range(2,6):change(rng.choice([bad,good]),version)
   else:
    change(good,2);change(bad,3);change(good,4)
   if name=='repeat-event':
    for _ in range(3):s.send('workspace/didChangeWatchedFiles',{'changes':[{'uri':uri,'type':2}]})
  # Matching final version and unsaved overlay; disk deliberately remains good.
  diagnostics=change(bad,7)
  assert len(diagnostics['diagnostics'])==1
  diagnostic=diagnostics['diagnostics'][0]
  expected=json.loads((ROOT/'tests/fixtures/contracts/stdio-buffer-lifecycle/case.json').read_text())['requiredDiagnostic']
  assert all(diagnostic.get(k)==v for k,v in expected.items())
  params={'textDocument':{'uri':uri},'position':{'line':5,'character':15}}
  definition=request('textDocument/definition',params);assert len(definition)==1 and definition[0]['uri']==uri and definition[0]['range']['start']=={'line':8,'character':12}
  references=request('textDocument/references',params|{'context':{'includeDeclaration':True}})
  symbols=request('textDocument/documentSymbol',{'textDocument':{'uri':uri}})
  completion=request('textDocument/completion',{'textDocument':{'uri':uri},'position':{'line':6,'character':2}})
  repeated=request('textDocument/completion',{'textDocument':{'uri':uri},'position':{'line':6,'character':2}});assert repeated==completion
  root_state={}
  if name=='root-isolation':
   otheruri=(other/'workflow.yaml').as_uri()
   inventory=request('workspace/symbol',{'query':'main'})
   assert any(item.get('location',{}).get('uri')==otheruri for item in inventory), 'second root did not become active'
   for owner,target,line in [('rootA',uri,8),('rootB',otheruri,9)]:
    found=request('textDocument/definition',{'textDocument':{'uri':target},'position':{'line':5 if owner=='rootA' else 6,'character':15}})
    assert len(found)==1 and found[0]['uri']==target and found[0]['range']['start']=={'line':line,'character':12}, 'root ownership or target range differs: '+owner
    root_state[owner]=found
  # The common parent preserves distinct workspace/ and other/ identities.
  snapshot=normalize({'roots':root_state,'diagnostics':diagnostics,'definition':definition,'references':references,'symbols':symbols,'completion':completion},folder if name=='root-isolation' else work)
  save(folder/'snapshot.json',snapshot);request('shutdown',None);s.send('exit');s.proc.wait(timeout=5);assert s.proc.returncode==0
  return snapshot
 finally:s.close();save(folder/'events.json',events)
for name in case['cases']:
 assertions={};error=None
 try:
  incremental=run(name,True,out/name/'incremental');fresh=run(name,False,out/name/'fresh');assertions={'complete_final_state':incremental==fresh,'independent_oracle':True,'repeated_query_equivalent':True}
 except Exception as e:error=repr(e);assertions={'complete_final_state':False,'independent_oracle':False,'repeated_query_equivalent':False}
 rows.append({'name':name,'assertions':assertions,'error':error,'pass':all(assertions.values())});save(out/'results.json',rows)
save(out/'manifest.json',{'command':sys.argv,'binarySha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'testSha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),'helperSha256':hashlib.sha256((ROOT/'tests/e2e/support/session.py').read_bytes()).hexdigest(),'windowsJobSha256':hashlib.sha256((ROOT/'tests/e2e/support/windows_job.py').read_bytes()).hexdigest(),'fixtureSha256':hashlib.sha256((ROOT/'tests/fixtures/contracts/stdio-buffer-lifecycle/equivalence.json').read_bytes()).hexdigest(),'inputHashes':{p.relative_to(out).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in out.rglob('*.yaml')},'cacheClaim':case['cacheClaim']})
raise SystemExit(0 if all(r['pass'] for r in rows) else 1)

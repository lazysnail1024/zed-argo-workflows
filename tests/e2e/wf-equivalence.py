#!/usr/bin/env python3
"""Compare event histories to a fresh real LSP with identical final overlays."""
import copy, hashlib, json, pathlib, random, sys, tempfile, time, traceback
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

# The applied-edit oracle is test-owned. Complete expected files live in the
# fixture and are never calculated from a server edit, source span or binding.
def utf16_position(text, offset):
 head=text[:offset]
 return {'line':head.count('\n'),'character':len(head.rsplit('\n',1)[-1].encode('utf-16-le'))//2}

def fixture_range(text, needle, occurrence=0, offset=0, length=None):
 start=-1
 for _ in range(occurrence+1):start=text.index(needle,start+1)
 start+=offset
 return {'start':utf16_position(text,start),'end':utf16_position(text,start+(len(needle)-offset if length is None else length))}

def utf16_offset(text, position):
 assert set(position)=={'line','character'}, 'invalid position fields'
 line,column=position['line'],position['character']
 assert type(line) is int and type(column) is int and line>=0 and column>=0, 'invalid position type'
 lines=text.split('\n')
 assert line<len(lines), 'line beyond the document'
 body=lines[line].removesuffix('\r')
 units=0
 for index,char in enumerate(body):
  if units==column:return sum(len(value)+1 for value in lines[:line])+index
  units+=2 if ord(char)>0xffff else 1
  assert units<=column, 'UTF-16 position splits a surrogate pair'
 assert units==column, 'column beyond the line or inside CRLF'
 return sum(len(value)+1 for value in lines[:line])+len(body)

def apply_versioned_edit(buffers, edit, allowed):
 assert isinstance(edit,dict) and 'changes' not in edit, 'versioned documentChanges required'
 changes=edit.get('documentChanges')
 assert isinstance(changes,list) and changes, 'empty workspace edit'
 result=copy.deepcopy(buffers);seen=set()
 for change in changes:
  assert isinstance(change,dict) and 'kind' not in change, 'file operations are not allowed'
  document=change.get('textDocument',{});uri=document.get('uri')
  assert uri in allowed and uri in buffers and uri not in seen, 'outside, duplicate or unexpected document'
  seen.add(uri)
  assert type(document.get('version')) is int and document['version']==buffers[uri]['version'], 'wrong or absent document version'
  edits=change.get('edits')
  assert isinstance(edits,list) and edits, 'empty document edit'
  spans=[];text=buffers[uri]['text']
  for item in edits:
   assert isinstance(item,dict) and isinstance(item.get('newText'),str), 'invalid text edit'
   item['newText'].encode('utf-8')
   a=utf16_offset(text,item['range']['start']);z=utf16_offset(text,item['range']['end'])
   assert a<=z, 'reversed edit range'
   spans.append((a,z,item['newText']))
  ordered=sorted(spans)
  for previous,current in zip(ordered,ordered[1:]):
   assert previous[1]<=current[0] and previous[0]!=current[0], 'overlapping or duplicate edits'
  for a,z,new_text in reversed(ordered):text=text[:a]+new_text+text[z:]
  result[uri]={'text':text,'version':buffers[uri]['version']+1}
 assert seen==allowed, 'missing affected document'
 return result

def edit_oracle_controls(buffers, edit, allowed, expected, folder):
 # Mutate real returned edits to prove that the local applier/oracle cannot
 # accidentally accept stale versions, wrong paths or malformed ranges.
 controls=[]
 def rejected(name,mutate):
  value=copy.deepcopy(edit);mutate(value)
  try:
   actual=apply_versioned_edit(buffers,value,allowed)
   refused={uri:row['text'] for uri,row in actual.items()}!=expected
  except (AssertionError,KeyError,TypeError,UnicodeError):refused=True
  controls.append({'name':name,'rejected':refused})
 rejected('stale-version',lambda v:v['documentChanges'][0]['textDocument'].__setitem__('version',-1))
 rejected('absent-version',lambda v:v['documentChanges'][0]['textDocument'].pop('version'))
 rejected('outside-uri',lambda v:v['documentChanges'][0]['textDocument'].__setitem__('uri',(folder/'outside.yaml').as_uri()))
 rejected('other-scope-uri',lambda v:v['documentChanges'][0]['textDocument'].__setitem__('uri',next(uri for uri in buffers if uri not in allowed)))
 rejected('duplicate-document',lambda v:v['documentChanges'].append(copy.deepcopy(v['documentChanges'][0])))
 rejected('overlapping-edits',lambda v:v['documentChanges'][0]['edits'].append(copy.deepcopy(v['documentChanges'][0]['edits'][0])))
 def split_surrogate(value):
  change=next(v for v in value['documentChanges'] if '🐌' in buffers[v['textDocument']['uri']]['text'])
  text=buffers[change['textDocument']['uri']]['text'];start=utf16_position(text,text.index('🐌'));start['character']+=1
  change['edits'][0]['range']={'start':start,'end':dict(start)}
 rejected('split-surrogate',split_surrogate)
 rejected('column-outside-line',lambda v:v['documentChanges'][0]['edits'][0]['range']['end'].__setitem__('character',1000000))
 rejected('wrong-inserted-bytes',lambda v:v['documentChanges'][0]['edits'][0].__setitem__('newText','WRONG'))
 save(folder/'oracle-controls.json',controls)
 assert all(row['rejected'] for row in controls), controls

def run_applied(name,history,folder):
 spec=case['appliedEdits'][name];work=folder/'workspace';work.mkdir(parents=True)
 write(work/'.argo-workflows-ls.yaml',case['editConfig'])
 for relative,text in spec['inputs'].items():
  path=work/relative;path.parent.mkdir(parents=True,exist_ok=True);write(path,text)
 for stage in ['inputs','afterCompletion','expected']:
  for relative,text in spec.get(stage,{}).items():
   path=folder/'oracle'/stage/relative;path.parent.mkdir(parents=True,exist_ok=True);write(path,text)
 uris={relative:(work/relative).as_uri() for relative in spec['inputs']}
 allowed={uris[relative] for relative in spec['changedFiles']}
 expected={uris[relative]:text for relative,text in spec['expected'].items()}
 buffers={};events=[];request_id=0;s=Session([binary,'serve','--stdio'],folder)
 rename=name=='applied-rename-equivalence'
 def request(method,params,raw=False):
  nonlocal request_id
  request_id+=1;s.send(method,params,request_id);response=s.wait(lambda m:m.get('id')==request_id)
  if raw:return response
  assert 'error' not in response,response
  return response['result']
 def diagnostics(uri,version,empty=False):
  value=s.diagnostics(uri,version,lambda p:(not p['diagnostics'] if empty else not any(d['code']=='WF_ANALYSIS_UNAVAILABLE' for d in p['diagnostics'])))
  return value
 def change_batch(texts,versions,empty=False):
  for relative,text in texts.items():
   uri=uris[relative];buffers[uri]={'text':text,'version':versions[relative]}
   s.send('textDocument/didChange',{'textDocument':{'uri':uri,'version':versions[relative]},'contentChanges':[{'text':text}]})
   events.append({'operation':'change','file':relative,'version':versions[relative],'text':text})
  return {relative:diagnostics(uris[relative],versions[relative],empty) for relative in texts}
 def publish_applied(after,empty=False):
  selected={relative:after[uris[relative]]['text'] for relative in spec['changedFiles']}
  return change_batch(selected,{relative:after[uris[relative]]['version'] for relative in selected},empty=empty)
 def at(relative,span):return {'textDocument':{'uri':uris[relative]},'position':span['start']}
 def resource_query(resource_name):
  relative='chosen/library.yaml';text=buffers[uris[relative]]['text'];span=fixture_range(text,'"'+resource_name+'"')
  return at(relative,span)
 def prepare(params):
  deadline=time.monotonic()+5
  while True:
   response=request('textDocument/prepareRename',params,raw=True)
   if 'incomplete' not in response.get('error',{}).get('message','') or time.monotonic()>=deadline:
    assert 'error' not in response,response
    return response['result']
   time.sleep(.02)
 def location(relative,span):return {'uri':uris[relative],'range':span}
 def canonical(locations):return sorted(locations,key=lambda value:json.dumps(value,sort_keys=True))
 def binding_observations():
  observed={}
  if rename:
   for scope,value in [('chosen',spec['newName']),('unrelated','shared')]:
    library=scope+'/library.yaml';caller=scope+'/caller.yaml';libtext=buffers[uris[library]]['text'];calltext=buffers[uris[caller]]['text']
    # Navigation names exclude YAML quotation delimiters; the edit oracle above
    # still checks the complete quoted scalar and every surrounding byte.
    declaration=location(library,fixture_range(libtext,'"'+value+'"',offset=1,length=len(value)))
    uses=[location(caller,fixture_range(calltext,'"'+value+'"',i,1,len(value))) for i in range(2)]
    definitions=[request('textDocument/definition',at(caller,use['range'])) for use in uses]
    assert definitions==[[declaration],[declaration]],(definitions,declaration)
    references=request('textDocument/references',at(library,declaration['range'])|{'context':{'includeDeclaration':True}})
    assert canonical(references)==canonical([declaration,*uses]),(references,declaration,uses)
    observed[scope]={'definitions':definitions,'references':canonical(references)}
  else:
   for relative in spec['inputs']:
    text=buffers[uris[relative]]['text'];values=[]
    for occurrence in range(2):
     declaration=location(relative,fixture_range(text,'name: message',occurrence,6,7))
     use=location(relative,fixture_range(text,'inputs.parameters.message',occurrence,len('inputs.parameters.'),7))
     definition=request('textDocument/definition',at(relative,use['range']))
     assert definition==[declaration],(definition,declaration)
     references=request('textDocument/references',at(relative,declaration['range'])|{'context':{'includeDeclaration':True}})
     assert canonical(references)==canonical([declaration,use]),(references,declaration,use)
     values.append({'definition':definition,'references':canonical(references)})
    observed[relative]=values
  return observed
 try:
  request('initialize',{'rootUri':work.as_uri(),'workspaceFolders':[{'uri':work.as_uri(),'name':'equivalence'}],'capabilities':{'workspace':{'workspaceEdit':{'documentChanges':True}},'textDocument':{'completion':{'completionItem':{'snippetSupport':False}}}}});s.send('initialized',{})
  start=dict(spec['inputs'])
  if history:
   for relative in spec['changedFiles']:
    start[relative]=spec['inputs'][relative].replace('"shared"','"previous"') if rename else spec['expected'][relative]
  for relative,text in start.items():
   uri=uris[relative];version=1 if history and uri in allowed else spec['versions'][relative]
   buffers[uri]={'text':text,'version':version}
   s.send('textDocument/didOpen',{'textDocument':{'uri':uri,'languageId':'yaml','version':version,'text':text}})
   events.append({'operation':'open','file':relative,'version':version,'text':text});diagnostics(uri,version)
  if history:
   if rename:prepare(resource_query('previous'))
   else:
    relative='chosen/workflow.yaml';text=buffers[uris[relative]]['text'];request('textDocument/definition',at(relative,fixture_range(text,'inputs.parameters.message',offset=len('inputs.parameters.'),length=7)))
   changed={relative:spec['inputs'][relative] for relative in spec['changedFiles']}
   change_batch(changed,{relative:2 for relative in changed})
   if rename:prepare(resource_query('shared'))
   else:
    relative='chosen/workflow.yaml';span=fixture_range(changed[relative],'inputs.parameters.me}',offset=len('inputs.parameters.'),length=2)
    request('textDocument/completion',{'textDocument':{'uri':uris[relative]},'position':span['end']})
   change_batch({relative:start[relative] for relative in changed},{relative:3 for relative in changed})
   change_batch(changed,{relative:spec['versions'][relative] for relative in changed})
  edits={}
  if rename:
   params=resource_query('shared');prepared=prepare(params)
   assert prepared.get('placeholder')=='shared',prepared
   edit=request('textDocument/rename',params|{'newName':spec['newName']})
   assert sum(len(row['edits']) for row in edit.get('documentChanges',[]))==spec['editCount'],edit
   edit_oracle_controls(buffers,edit,allowed,expected,folder)
   after=apply_versioned_edit(buffers,edit,allowed)
   assert {uri:row['text'] for uri,row in after.items()}==expected, 'rename did not produce the literal complete expected files'
   edits['rename']=edit;final_diagnostics=publish_applied(after,empty=True)
  else:
   relative='chosen/workflow.yaml';uri=uris[relative];text=buffers[uri]['text']
   span=fixture_range(text,'inputs.parameters.me}',offset=len('inputs.parameters.'),length=2)
   params={'textDocument':{'uri':uri},'position':span['end']}
   items=request('textDocument/completion',params);repeated=request('textDocument/completion',params)
   assert items==repeated, 'repeated completion changed at identical accepted input'
   matches=[item for item in items if item.get('label')==spec['completionLabel']]
   assert len(matches)==1 and matches[0].get('insertTextFormat',1)==1 and not matches[0].get('additionalTextEdits'),matches
   completion=matches[0]['textEdit']
   # CompletionItem has no document version; bind its returned edit to the
   # unchanged accepted request version in this test client, not a server claim.
   completion_edit={'documentChanges':[{'textDocument':{'uri':uri,'version':buffers[uri]['version']},'edits':[completion]}]}
   after=apply_versioned_edit(buffers,completion_edit,allowed)
   assert {relative:after[uris[relative]]['text'] for relative in spec['inputs']}==spec['afterCompletion'], 'completion did not produce the literal complete intermediate files'
   edits['completion']=completion;completion_diagnostics=publish_applied(after)
   text=buffers[uri]['text'];bad_span=fixture_range(text,'imagePullPolcy')
   expected_diagnostic={'range':bad_span,'code':'SCHEMA_UNKNOWN_FIELD','severity':2}
   observed_diagnostics=completion_diagnostics[relative]['diagnostics']
   assert [{key:d[key] for key in expected_diagnostic} for d in observed_diagnostics]==[expected_diagnostic], observed_diagnostics
   # A query after the change is an ordered barrier; the action must be based on
   # the newly accepted complete text even if earlier diagnostics were queued.
   actions=request('textDocument/codeAction',{'textDocument':{'uri':uri},'range':bad_span,'context':{'diagnostics':[],'only':['quickfix']}})
   matches=[action for action in actions if action.get('title')==spec['quickfixTitle']]
   assert len(matches)==1,matches
   resolved=request('codeAction/resolve',matches[0]);edit=resolved.get('edit')
   edit_oracle_controls(buffers,edit,allowed,expected,folder)
   after=apply_versioned_edit(buffers,edit,allowed)
   assert {uri:row['text'] for uri,row in after.items()}==expected, 'quick fix did not produce the literal complete final files'
   edits['quickfix']=edit;final_diagnostics=publish_applied(after,empty=True)
   stale=request('codeAction/resolve',matches[0],raw=True)
   assert stale.get('error',{}).get('code')==-32801, 'already-applied action was not rejected as stale'
  # These are publications for the actual applied edit versions; no additional
  # no-op didChange is allowed to refresh away an incorrect post-edit result.
  bindings=binding_observations()
  actual={relative:buffers[uris[relative]]['text'] for relative in spec['inputs']}
  assert actual==spec['expected']
  assert all(actual[relative]==spec['inputs'][relative] for relative in actual if relative.startswith('unrelated/'))
  assert all('\r\n' in text and '\n' not in text.replace('\r\n','') for text in actual.values()), 'CRLF changed'
  assert all(text.count('中🐌')==spec['expected'][relative].count('中🐌') for relative,text in actual.items()), 'non-BMP text changed'
  assert all((work/relative).read_bytes()==text.encode('utf-8') for relative,text in spec['inputs'].items()), 'server or test unexpectedly changed saved source'
  assert (work/'.argo-workflows-ls.yaml').read_bytes()==case['editConfig'].encode('utf-8'), 'server or test unexpectedly changed saved configuration'
  for relative,text in actual.items():
   path=folder/'applied'/relative;path.parent.mkdir(parents=True,exist_ok=True);write(path,text)
  snapshot=normalize({'texts':actual,'versions':{relative:buffers[uris[relative]]['version'] for relative in actual},'diagnostics':final_diagnostics,'bindings':bindings,'edits':edits},work)
  save(folder/'snapshot.json',snapshot);request('shutdown',None);s.send('exit');s.proc.wait(timeout=5);assert s.proc.returncode==0
  return snapshot
 finally:s.close();save(folder/'events.json',events)

for name in case['cases']:
 assertions={};error=None
 try:
  incremental=run(name,True,out/name/'incremental');fresh=run(name,False,out/name/'fresh');assertions={'complete_final_state':incremental==fresh,'independent_oracle':True,'repeated_query_equivalent':True}
 except Exception as e:error=repr(e);assertions={'complete_final_state':False,'independent_oracle':False,'repeated_query_equivalent':False}
 rows.append({'name':name,'assertions':assertions,'error':error,'pass':all(assertions.values())});save(out/'results.json',rows)
for name in case['appliedEditCases']:
 keys=['complete_final_state','independent_oracle','exact_applied_bytes','utf16_crlf_preserved','versioned_edit_guards','scope_unchanged','post_edit_bindings','post_edit_diagnostics','disk_unchanged']
 assertions={key:False for key in keys};error=None
 try:
  incremental=run_applied(name,True,out/name/'incremental');fresh=run_applied(name,False,out/name/'fresh')
  assert incremental==fresh, 'applied complete state differs between history and fresh sessions'
  assertions={key:True for key in keys}
 except Exception as e:
  error=repr(e);(out/name/'error.txt').write_text(traceback.format_exc())
 rows.append({'name':name,'assertions':assertions,'error':error,'pass':all(assertions.values())});save(out/'results.json',rows)
save(out/'manifest.json',{'command':sys.argv,'binarySha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'testSha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),'helperSha256':hashlib.sha256((ROOT/'tests/e2e/support/session.py').read_bytes()).hexdigest(),'windowsJobSha256':hashlib.sha256((ROOT/'tests/e2e/support/windows_job.py').read_bytes()).hexdigest(),'fixtureSha256':hashlib.sha256((ROOT/'tests/fixtures/contracts/stdio-buffer-lifecycle/equivalence.json').read_bytes()).hexdigest(),'inputHashes':{p.relative_to(out).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in out.rglob('*.yaml')},'cacheClaim':case['cacheClaim']})
raise SystemExit(0 if all(r['pass'] for r in rows) else 1)

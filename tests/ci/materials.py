"""Consumer-specific evidence contracts, rechecked before receipts and after download."""
import json
from pathlib import Path

def verify(name,destination,root,sha,require):
 contract=json.loads((root/'tests/ci/contracts.json').read_text())['consumers'].get(name,{}).get('materials')
 if not contract:return
 manifest=json.loads((destination/'manifest.json').read_text())
 for key,source in contract.get('sourceHashes',{}).items():require(manifest.get(key)==sha(root/source),f'{name}: wrong {key}')
 for filename in contract.get('required',[]):require((destination/filename).is_file(),f'{name}: missing required child material: {filename}')
 expected_inputs={filename:sha(root/source) for filename,source in contract.get('inputs',{}).items()}
 sessions=[destination]
 if 'sessionCases' in contract:
  cases=json.loads((root/contract['sessionCases']).read_text())['cases'];sessions=[]
  mixed=(root/'tests/fixtures/schema/mixed.yaml').read_text();good=mixed.replace('parallelism: wrong','parallelism: 2');config='schemaVersion: 1\ntargetVersion: "3.6.0"\n'
  import hashlib
  for case in cases:
   for mode in ('incremental','fresh'):
    folder=destination/case/mode;sessions.append(folder)
    for f,text in {'workspace/workflow.yaml':good,'workspace/.argo-workflows-ls.yaml':config,**({'other/workflow.yaml':'\n'+good.replace('name: example','name: other'),'other/.argo-workflows-ls.yaml':config} if case=='root-isolation' else {})}.items():
     expected_inputs[f'{case}/{mode}/{f}']=hashlib.sha256(text.encode()).hexdigest()
    require((folder/'snapshot.json').is_file() and (folder/'events.json').is_file(),name+': missing final state or event history')
 require(manifest.get('inputHashes')==expected_inputs,name+': wrong input path/hash inventory')
 for filename,digest in expected_inputs.items():require((destination/filename).is_file() and sha(destination/filename)==digest,name+': missing or changed input '+filename)
 for folder in sessions:
  for filename in ('stdout.raw','stderr.raw','traffic.json','lifecycle.json'):require((folder/filename).is_file(),name+': missing required child material: '+str(folder/filename))
  lifecycle=json.loads((folder/'lifecycle.json').read_text());require(lifecycle=={'exitCode':0,'reaped':True,'threadsDrained':True},name+': incomplete child lifecycle')
  traffic=json.loads((folder/'traffic.json').read_text());require(isinstance(traffic.get('sent'),list) and traffic['sent'] and isinstance(traffic.get('received'),list) and traffic['received'],name+': empty or invalid child traffic')
  raw=(folder/'stdout.raw').read_bytes();messages=[]
  while raw:
   header,separator,raw=raw.partition(b'\r\n\r\n');require(bool(separator),name+': malformed raw framing');headers=dict(line.decode('ascii').lower().split(':',1) for line in header.split(b'\r\n'));length=int(headers['content-length']);require(0<length<=len(raw),name+': truncated raw frame');messages.append(json.loads(raw[:length]));raw=raw[length:]
  require(messages==traffic['received'],name+': traffic differs from child stdout')
  require(traffic['sent'][0].get('method')=='initialize' and traffic['sent'][-1].get('method')=='exit',name+': missing initialization or exit')
  shutdown=[m['id'] for m in traffic['sent'] if m.get('method')=='shutdown'];require(len(shutdown)==1 and any(m.get('id')==shutdown[0] and 'result' in m and m['result'] is None for m in messages),name+': missing shutdown response')
  if name=='wf3-lsp':lifecycle_evidence(folder,traffic,root,require)

def lifecycle_evidence(folder,traffic,root,require):
 """Reconstruct this consumer's fixed input stages, independently of result flags."""
 def check(ok,reason):require(ok,'wf3-lsp: lifecycle evidence '+reason)
 try:record=json.loads((folder/'diagnostic-phases.json').read_text())
 except (ValueError,OSError):raise ValueError('wf3-lsp: lifecycle evidence invalid phase JSON') from None
 phases=record.get('phases') if isinstance(record,dict) else None
 check(isinstance(phases,list) and len(phases)==6,'missing or unexpected phases')
 timeline=traffic.get('timeline')
 check(isinstance(timeline,list) and timeline,'missing timeline')
 check(all(isinstance(e,dict) and set(e)=={'direction','message'} and e['direction'] in ('sent','received') and isinstance(e['message'],dict) for e in timeline),'invalid timeline structure')
 for direction in ('sent','received'):
  check([e['message'] for e in timeline if e['direction']==direction]==traffic[direction],'timeline projection differs from '+direction)
 initialize=traffic['sent'][0];root_uri=initialize.get('params',{}).get('rootUri')
 check(isinstance(root_uri,str) and root_uri.startswith('file:'),'missing initialized root URI')
 uri=root_uri.rstrip('/')+'/workflow.yaml';config_uri=root_uri.rstrip('/')+'/.argo-workflows-ls.yaml';bad=(root/'tests/fixtures/schema/mixed.yaml').read_text()
 def document(method,target,version=None,text=None,changes=None):
  params={'textDocument':{'uri':target}}
  if version is not None:params['textDocument']['version']=version
  if text is not None:params['textDocument'].update(languageId='yaml',text=text)
  if changes is not None:params['contentChanges']=changes
  return {'jsonrpc':'2.0','method':'textDocument/'+method,'params':params}
 expected=[
  document('didOpen',uri,1,bad),
  document('didChange',uri,2,changes=[{'range':{'start':{'line':6,'character':15},'end':{'line':6,'character':20}},'text':'2'}]),
  document('didChange',uri,1,changes=[{'text':bad.replace('entrypoint: main','entrypoint: obsolete').replace('name: main','name: obsolete')}]),
  document('didChange',uri,3,changes=[{'text':bad}]),
  document('didOpen',config_uri,1,'schemaVersion: 1\ntargetVersion: "99.0.0"\n'),
  document('didChange',config_uri,2,changes=[{'text':'schemaVersion: 1\ntargetVersion: "3.6.0"\n'}]),
  document('didClose',config_uri),document('didClose',uri)]
 relevant={'textDocument/didOpen','textDocument/didChange','textDocument/didClose','workspace/didChangeWorkspaceFolders','workspace/didChangeConfiguration','workspace/didChangeWatchedFiles'}
 events=[(i,e['message']) for i,e in enumerate(timeline) if e['direction']=='sent' and e['message'].get('method') in relevant]
 check([m for _,m in events]==expected,'document/configuration operations contradict trusted inputs')
 schema=json.loads((root/'tests/fixtures/contracts/stdio-buffer-lifecycle/case.json').read_text())['requiredDiagnostic']
 unavailable={'code':'WF_ANALYSIS_UNAVAILABLE','severity':2,'source':'argo-workflows','range':{'start':{'line':0,'character':0},'end':{'line':0,'character':0}}}
 # Stale v1 input does not invalidate the repaired v2 stage. Configuration
 # changes do invalidate same-version diagnostics and own separate expectations.
 stages=[(0,1,1,schema),(1,3,2,None),(3,4,3,schema),(4,5,3,unavailable),(5,6,3,schema),(7,None,0,None)]
 previous=-1
 for phase,(opened,invalidated,version,diagnostic) in zip(phases,stages):
  check(isinstance(phase,dict) and set(phase)=={'start','end','uri','version','diagnostics'},'invalid phase structure')
  start=phase['start'];end=phase['end'];trigger=events[opened][0];boundary=events[invalidated][0] if invalidated is not None else None
  check(type(start) is int and trigger<start<len(timeline) and start>previous,'invalid phase start or order')
  check(end==boundary and (end is None or type(end) is int and start<end<=len(timeline)),'invalid phase invalidation boundary')
  check(phase['uri']==uri and type(phase['version']) is int and phase['version']==version,'wrong phase URI/version')
  publications=[]
  for i in range(trigger+1,len(timeline) if boundary is None else boundary):
   event=timeline[i];message=event['message'];params=message.get('params',{})
   if event['direction']=='received' and message.get('method')=='textDocument/publishDiagnostics' and params.get('uri')==uri and params.get('version',0)==version:
    publications.append((i,params))
  check(publications and publications[0][0]==start,'phase does not start at its observed completion')
  check(phase['diagnostics']==publications[0][1].get('diagnostics'),'phase expectation differs from observed publication')
  for _,params in publications:
   values=params.get('diagnostics')
   check(isinstance(values,list) and (values==[] if diagnostic is None else len(values)==1 and isinstance(values[0],dict) and all(values[0].get(k)==v for k,v in diagnostic.items())),'current publication contradicts independent phase expectation')
   if version==0:check('version' not in params,'closed document publication carries a version')
  previous=start

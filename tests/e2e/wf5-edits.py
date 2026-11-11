#!/usr/bin/env python3
import json,pathlib,tempfile,subprocess,sys,hashlib,threading,queue,time
root=pathlib.Path(__file__).resolve().parents[2];binary=pathlib.Path(sys.argv[1]).resolve();out=pathlib.Path(sys.argv[2]) if len(sys.argv)>2 else root/'artifacts/wf5-edits';out.mkdir(parents=True,exist_ok=True);checks={};sent=[];received=[];raw=bytearray();q=queue.Queue();text=(root/'tests/fixtures/schema/valid.yaml').read_text().replace('parallelism','paralelism')
with tempfile.TemporaryDirectory(prefix='wf-edits-') as d:
 work=pathlib.Path(d);file=work/'case.yaml';file.write_bytes(text.encode('utf-8'));(work/'.argo-workflows-ls.yaml').write_bytes('schemaVersion: 1\ntargetVersion: "3.6.0"\n'.encode('utf-8'));uri=file.as_uri();p=subprocess.Popen([str(binary),'serve','--stdio'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 def reader():
  try:
   while True:
    headers={}
    while True:
     line=p.stdout.readline();raw.extend(line)
     if not line:raise EOFError()
     if line==b'\r\n':break
     k,v=line.decode().strip().split(':',1);headers[k.lower()]=v.strip()
    data=p.stdout.read(int(headers['content-length']));raw.extend(data);v=json.loads(data);received.append(v);q.put(v)
  except Exception as e:q.put(e)
 threading.Thread(target=reader,daemon=True).start()
 def send(method,params=None,id=None):
  m={'jsonrpc':'2.0','method':method}
  if params is not None:m['params']=params
  if id is not None:m['id']=id
  sent.append(m);data=json.dumps(m).encode();p.stdin.write(f'Content-Length: {len(data)}\r\n\r\n'.encode()+data);p.stdin.flush()
 def wait(predicate):
  end=time.monotonic()+15
  while True:
   remaining=end-time.monotonic()
   if remaining<=0:raise TimeoutError('LSP response deadline exceeded')
   v=q.get(timeout=remaining)
   if isinstance(v,Exception):raise v
   if predicate(v):return v
 def diagnosis(version):return wait(lambda x:x.get('method')=='textDocument/publishDiagnostics' and x['params']['uri']==uri and x['params'].get('version')==version)['params']['diagnostics']
 def settle_dependency():
  # Unrelated files no longer force publication of every open document. Observe
  # their actual detached symbols before testing the previous action proof.
  deadline=time.monotonic()+5;ident=100
  while time.monotonic()<deadline:
   ident+=1;send('workspace/symbol',{'query':'noop'},ident);response=wait(lambda x:x.get('id')==ident)
   if response.get('result'):return
   time.sleep(.02)
  raise TimeoutError('watched dependency did not enter the index')
 def apply(current,edit,version):
  change=edit['documentChanges'];assert len(change)==1 and change[0]['textDocument']=={'uri':uri,'version':version};lines=current.splitlines(keepends=True);replacements=[]
  for item in change[0]['edits']:
   def offset(pos):return sum(len(x) for x in lines[:pos['line']])+len(lines[pos['line']].encode('utf-16-le')[:pos['character']*2].decode('utf-16-le'))
   replacements.append((offset(item['range']['start']),offset(item['range']['end']),item['newText']))
  for a,b,value in sorted(replacements,reverse=True):current=current[:a]+value+current[b:]
  return current
 try:
  send('initialize',{'rootUri':work.as_uri(),'capabilities':{'workspace':{'workspaceEdit':{'documentChanges':True}}}},1);v=wait(lambda x:x.get('id')==1);checks['edit_capability']=bool(v['result']['capabilities'].get('codeActionProvider')) and bool(v['result']['capabilities'].get('renameProvider'));send('initialized',{});send('textDocument/didOpen',{'textDocument':{'uri':uri,'version':1,'languageId':'yaml','text':text}});ds=diagnosis(1)
  send('textDocument/codeAction',{'textDocument':{'uri':uri},'range':{'start':{'line':6,'character':2},'end':{'line':6,'character':12}},'context':{'diagnostics':ds}},2);v=wait(lambda x:x.get('id')==2);action=next(x for x in v['result'] if 'parallelism' in x['title']);dependency=work/'dependency.yaml';dependency.write_bytes('apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: {name: helper}\nspec: {templates: [{name: noop, container: {image: alpine}}]}\n'.encode('utf-8'));send('workspace/didChangeWatchedFiles',{'changes':[{'uri':dependency.as_uri(),'type':1}]});settle_dependency();send('codeAction/resolve',action,20);v=wait(lambda x:x.get('id')==20);checks['independent_scope_change_keeps_fix']='result' in v and apply(text,v['result']['edit'],1)==text.replace('paralelism','parallelism');send('textDocument/codeAction',{'textDocument':{'uri':uri},'range':{'start':{'line':6,'character':2},'end':{'line':6,'character':12}},'context':{'diagnostics':ds}},21);v=wait(lambda x:x.get('id')==21);action=next(x for x in v['result'] if 'parallelism' in x['title']);send('codeAction/resolve',action,3);v=wait(lambda x:x.get('id')==3);fixed=apply(text,v['result']['edit'],1);checks['field_bytes']=fixed==text.replace('paralelism','parallelism');(out/'field-after.yaml').write_bytes(fixed.encode('utf-8'))
  send('textDocument/didChange',{'textDocument':{'uri':uri,'version':2},'contentChanges':[{'text':fixed}]});checks['field_diagnostic_removed']=diagnosis(2)==[];send('codeAction/resolve',action,4);v=wait(lambda x:x.get('id')==4);checks['stale_rejected']=v.get('error',{}).get('code')==-32801
  send('textDocument/prepareRename',{'textDocument':{'uri':uri},'position':{'line':5,'character':15}},5);v=wait(lambda x:x.get('id')==5);checks['prepare']=v['result']['placeholder']=='main'
  send('textDocument/rename',{'textDocument':{'uri':uri},'position':{'line':5,'character':15},'newName':'renamed'},6);v=wait(lambda x:x.get('id')==6);renamed=apply(fixed,v['result'],2);checks['rename_bytes']=renamed==fixed.replace('entrypoint: main','entrypoint: renamed').replace('name: main','name: renamed');(out/'rename-after.yaml').write_bytes(renamed.encode('utf-8'))
  send('textDocument/didChange',{'textDocument':{'uri':uri,'version':3},'contentChanges':[{'text':renamed}]});checks['rename_valid']=diagnosis(3)==[]
  public=renamed.replace('kind: Workflow','kind: WorkflowTemplate');send('textDocument/didChange',{'textDocument':{'uri':uri,'version':4},'contentChanges':[{'text':public}]});diagnosis(4);send('textDocument/prepareRename',{'textDocument':{'uri':uri},'position':{'line':5,'character':15}},7);v=wait(lambda x:x.get('id')==7);checks['public_rejected']=v.get('error',{}).get('code')==-32602 and 'result' not in v
  checks['disk_unchanged']=file.read_text()==text;send('shutdown',None,8);v=wait(lambda x:x.get('id')==8);checks['shutdown']='result' in v and v['result'] is None and 'error' not in v;send('exit');p.wait(timeout=5);checks['exit']=p.returncode==0
 except Exception as e:checks['exception']=str(e);p.kill();p.wait()
 stderr=p.stderr.read()
(out/'input.yaml').write_bytes(text.encode('utf-8'));(out/'stdout.raw').write_bytes(raw);(out/'stderr.raw').write_bytes(stderr);(out/'traffic.json').write_text(json.dumps({'sent':sent,'received':received},indent=2));(out/'results.json').write_text(json.dumps([{'name':'versioned-field-fix-and-template-rename','assertions':checks,'exitCode':p.returncode,'pass':bool(checks) and all(x is True for x in checks.values())}],indent=2));sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();(out/'manifest.json').write_text(json.dumps({'command':sys.argv,'pythonVersion':sys.version,'binarySha256':sha(binary),'testSha256':sha(pathlib.Path(__file__)),'inputHashes':{p.name:sha(p) for p in out.glob('*.yaml')},'independentExpected':'replace only paralelism key; then only main declaration and entrypoint reference'},indent=2));sys.exit(0 if checks and all(x is True for x in checks.values()) else 1)

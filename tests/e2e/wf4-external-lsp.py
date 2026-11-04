#!/usr/bin/env python3
import json,pathlib,tempfile,subprocess,sys,hashlib,threading,queue,time
root=pathlib.Path(__file__).resolve().parents[2];binary=pathlib.Path(sys.argv[1]).resolve();out=pathlib.Path(sys.argv[2]) if len(sys.argv)>2 else root/'artifacts/wf4-external-lsp';out.mkdir(parents=True,exist_ok=True);checks={};sent=[];received=[];raw=bytearray();q=queue.Queue()
consumer=(root/'tests/fixtures/external/consumer.yaml').read_text();external=(root/'tests/fixtures/external/external.yaml').read_text()
with tempfile.TemporaryDirectory(prefix='wf-external-lsp-') as d:
 work=pathlib.Path(d);(work/'consumer.yaml').write_text(consumer);(work/'external.yaml').write_text(external);(work/'.argo-workflows-ls.yaml').write_text('schemaVersion: 1\ntargetVersion: "3.6.0"\n');uri=(work/'consumer.yaml').as_uri();ext=(work/'external.yaml').as_uri();p=subprocess.Popen([str(binary),'serve','--stdio'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
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
   v=q.get(timeout=max(.01,end-time.monotonic()))
   if isinstance(v,Exception):raise v
   if predicate(v):return v
 def diagnosis():return wait(lambda x:x.get('method')=='textDocument/publishDiagnostics' and x['params']['uri']==uri)['params']['diagnostics']
 try:
  send('initialize',{'rootUri':work.as_uri(),'capabilities':{}},1);wait(lambda x:x.get('id')==1);send('initialized',{});send('textDocument/didOpen',{'textDocument':{'uri':uri,'version':1,'languageId':'yaml','text':consumer}});checks['resolved']=diagnosis()==[]
  send('textDocument/definition',{'textDocument':{'uri':uri},'position':{'line':9,'character':50}},2);v=wait(lambda x:x.get('id')==2);checks['cross_file_definition']=len(v['result'])==1 and v['result'][0]['uri']==ext and v['result'][0]['range']['start']=={'line':5,'character':12}
  send('textDocument/didOpen',{'textDocument':{'uri':ext,'version':1,'languageId':'yaml','text':external.replace('name: run','name: other')}});ds=diagnosis();checks['buffer_removes_callee']=len(ds)==1 and ds[0]['code']=='WF_TEMPLATE_REF_UNRESOLVED' and ds[0]['severity']==1
  send('textDocument/didChange',{'textDocument':{'uri':ext,'version':2},'contentChanges':[{'text':external.replace('namespace: argo','namespace: elsewhere')}]});ds=diagnosis();checks['open_policy_information']=len(ds)==1 and ds[0]['code']=='WF_TEMPLATE_REF_UNRESOLVED' and ds[0]['severity']==3
  send('textDocument/didClose',{'textDocument':{'uri':ext}});checks['disk_restored']=diagnosis()==[];send('textDocument/didOpen',{'textDocument':{'uri':ext,'version':3,'languageId':'yaml','text':external.replace('spec:\n','spec:\n  entrypoint: run\n')}});diagnosis();inherited='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: consumer, namespace: argo}\nspec:\n  workflowTemplateRef: {name: shared}\n';send('textDocument/didChange',{'textDocument':{'uri':uri,'version':2},'contentChanges':[{'text':inherited}]});diagnosis();send('textDocument/definition',{'textDocument':{'uri':uri},'position':{'line':4,'character':inherited.splitlines()[4].index('shared')+1}},9);v=wait(lambda x:x.get('id')==9);checks['inherited_resource_definition']=len(v['result'])==1 and v['result'][0]['uri']==ext and v['result'][0]['range']['start']=={'line':2,'character':17};send('shutdown',None,3);wait(lambda x:x.get('id')==3);send('exit');p.wait(timeout=5);checks['exit']=p.returncode==0
 except Exception as e:checks['exception']=str(e);p.kill();p.wait()
 stderr=p.stderr.read()
(out/'stdout.raw').write_bytes(raw);(out/'stderr.raw').write_bytes(stderr);(out/'traffic.json').write_text(json.dumps({'sent':sent,'received':received},indent=2));(out/'results.json').write_text(json.dumps([{'name':'external-buffer-navigation','assertions':checks,'exitCode':p.returncode,'pass':bool(checks) and all(x is True for x in checks.values())}],indent=2));sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();(out/'manifest.json').write_text(json.dumps({'command':sys.argv,'pythonVersion':sys.version,'binarySha256':sha(binary),'testSha256':sha(pathlib.Path(__file__)),'inputHashes':{'consumer':hashlib.sha256(consumer.encode()).hexdigest(),'external':hashlib.sha256(external.encode()).hexdigest()}},indent=2));sys.exit(0 if checks and all(x is True for x in checks.values()) else 1)

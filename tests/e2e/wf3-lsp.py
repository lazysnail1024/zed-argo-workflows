#!/usr/bin/env python3
import json,pathlib,tempfile,subprocess,sys,hashlib,threading,queue,time
root=pathlib.Path(__file__).resolve().parents[2];binary=pathlib.Path(sys.argv[1]).resolve();out=pathlib.Path(sys.argv[2]) if len(sys.argv)>2 else root/'artifacts/wf3-lsp';out.mkdir(parents=True,exist_ok=True);e=json.loads((root/'tests/e2e/wf3-lsp.expected.json').read_text());assertions={};sent=[];received=[];raw=bytearray();stderr=bytearray()
with tempfile.TemporaryDirectory(prefix='wf-lsp-') as d:
 work=pathlib.Path(d);file=work/'workflow.yaml';file.write_bytes((root/'tests/fixtures/schema/valid.yaml').read_bytes());(work/'.argo-workflows-ls.yaml').write_text('schemaVersion: 1\ntargetVersion: "3.6.0"\n');uri=file.as_uri();proc=subprocess.Popen([str(binary),'serve','--stdio'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE);q=queue.Queue()
 def reader():
  try:
   while True:
    headers={}
    while True:
     line=proc.stdout.readline();raw.extend(line)
     if not line:raise EOFError()
     if line==b'\r\n':break
     k,v=line.decode().strip().split(':',1);headers[k.lower()]=v.strip()
    payload=proc.stdout.read(int(headers['content-length']));raw.extend(payload);v=json.loads(payload);received.append(v);q.put(v)
  except Exception as err:q.put(err)
 threading.Thread(target=reader,daemon=True).start()
 def send(method,params=None,id=None):
  m={'jsonrpc':'2.0','method':method}
  if params is not None:m['params']=params
  if id is not None:m['id']=id
  data=json.dumps(m).encode();sent.append(m);proc.stdin.write(f'Content-Length: {len(data)}\r\n\r\n'.encode()+data);proc.stdin.flush()
 def wait(predicate):
  end=time.monotonic()+15
  while True:
   v=q.get(timeout=max(.01,end-time.monotonic()))
   if isinstance(v,Exception):raise v
   if predicate(v):return v
 try:
  send('initialize',{'processId':None,'rootUri':work.as_uri(),'capabilities':{}},1);v=wait(lambda x:x.get('id')==1);cap=v['result']['capabilities'];assertions['sync']=cap['textDocumentSync']['change']==e['syncKind'];assertions['encoding']=cap.get('positionEncoding','utf-16')==e['positionEncoding'];send('initialized',{})
  bad=(root/'tests/fixtures/schema/mixed.yaml').read_text();send('textDocument/didOpen',{'textDocument':{'uri':uri,'languageId':'yaml','version':1,'text':bad}});v=wait(lambda x:x.get('method')=='textDocument/publishDiagnostics' and x['params'].get('version')==1);ds=v['params']['diagnostics'];assertions['buffer']=len(ds)==1 and ds[0]['code']==e['code'] and ds[0]['range']['start']=={'line':e['line'],'character':e['character']}
  send('textDocument/didChange',{'textDocument':{'uri':uri,'version':2},'contentChanges':[{'range':{'start':{'line':6,'character':15},'end':{'line':6,'character':20}},'text':'2'}]});v=wait(lambda x:x.get('method')=='textDocument/publishDiagnostics' and x['params'].get('version')==2);assertions['incremental']=v['params']['diagnostics']==[]
  send('textDocument/completion',{'textDocument':{'uri':uri},'position':{'line':6,'character':2}},10);v=wait(lambda x:x.get('id')==10);labels={x['label'] for x in v['result']};assertions['field_completion']='activeDeadlineSeconds' in labels and 'parallelism' not in labels
  send('textDocument/hover',{'textDocument':{'uri':uri},'position':{'line':6,'character':4}},11);v=wait(lambda x:x.get('id')==11);hover=v['result']['contents']['value'];assertions['field_hover']='integer' in hover and '3.6.0' in hover
  send('textDocument/definition',{'textDocument':{'uri':uri},'position':{'line':5,'character':15}},12);v=wait(lambda x:x.get('id')==12);assertions['template_definition']=len(v['result'])==1 and v['result'][0]['uri']==uri and v['result'][0]['range']['start']=={'line':8,'character':12}
  send('textDocument/references',{'textDocument':{'uri':uri},'position':{'line':8,'character':13},'context':{'includeDeclaration':True}},13);v=wait(lambda x:x.get('id')==13);assertions['template_references']={x['range']['start']['line'] for x in v['result']}=={5,8}
  send('textDocument/documentSymbol',{'textDocument':{'uri':uri}},14);v=wait(lambda x:x.get('id')==14);assertions['template_symbols']=[x['name'] for x in v['result']]==['main']
  send('textDocument/didChange',{'textDocument':{'uri':uri,'version':1},'contentChanges':[{'text':bad}]});send('workspace/symbol',{'query':''},2);wait(lambda x:x.get('id')==2);assertions['no_version_regression']=not any(x.get('method')=='textDocument/publishDiagnostics' and x['params'].get('version')==1 for x in received[received.index(v)+1:])
  send('textDocument/didChange',{'textDocument':{'uri':uri,'version':3},'contentChanges':[{'text':bad}]});wait(lambda x:x.get('method')=='textDocument/publishDiagnostics' and x['params'].get('version')==3)
  configuri=(work/'.argo-workflows-ls.yaml').as_uri();send('textDocument/didOpen',{'textDocument':{'uri':configuri,'languageId':'yaml','version':1,'text':'schemaVersion: 1\ntargetVersion: "99.0.0"\n'}});v=wait(lambda x:x.get('method')=='textDocument/publishDiagnostics' and x['params']['uri']==uri and x['params'].get('version')==3);assertions['invalid_config_clears']=v['params']['diagnostics']==[]
  send('textDocument/didChange',{'textDocument':{'uri':configuri,'version':2},'contentChanges':[{'text':'schemaVersion: 1\ntargetVersion: "3.6.0"\n'}]});v=wait(lambda x:x.get('method')=='textDocument/publishDiagnostics' and x['params']['uri']==uri and x['params'].get('version')==3);assertions['config_buffer_repairs']=len(v['params']['diagnostics'])==1
  send('textDocument/didClose',{'textDocument':{'uri':configuri}});wait(lambda x:x.get('method')=='textDocument/publishDiagnostics' and x['params']['uri']==uri)
  send('textDocument/didClose',{'textDocument':{'uri':uri}});v=wait(lambda x:x.get('method')=='textDocument/publishDiagnostics' and x['params']['uri']==uri and 'version' not in x['params']);assertions['close_disk']=v['params']['diagnostics']==[]
  send('shutdown',None,3);v=wait(lambda x:x.get('id')==3);assertions['shutdown']=v.get('result','missing') is None;send('exit');proc.wait(timeout=5);assertions['exit']=proc.returncode==e['cleanExit']
 except Exception as err:assertions['exception']=str(err);proc.kill();proc.wait()
 stderr.extend(proc.stderr.read());(out/'input-buffer.yaml').write_text(bad if 'bad' in locals() else '');(out/'input-disk.yaml').write_bytes(file.read_bytes());(out/'stdout.raw').write_bytes(raw);(out/'stderr.raw').write_bytes(stderr)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
(out/'manifest.json').write_text(json.dumps({'command':sys.argv,'serverCommand':[str(binary),'serve','--stdio'],'binarySha256':sha(binary),'testSha256':sha(pathlib.Path(__file__)),'expectedSha256':sha(root/'tests/e2e/wf3-lsp.expected.json'),'inputHashes':{p.name:sha(p) for p in out.glob('input*')}},indent=2));(out/'traffic.json').write_text(json.dumps({'sent':sent,'received':received},indent=2));(out/'results.json').write_text(json.dumps([{'name':'stdio-buffer-lifecycle','exitCode':proc.returncode,'assertions':assertions,'pass':bool(assertions) and all(x is True for x in assertions.values())}],indent=2));sys.exit(0 if assertions and all(x is True for x in assertions.values()) else 1)

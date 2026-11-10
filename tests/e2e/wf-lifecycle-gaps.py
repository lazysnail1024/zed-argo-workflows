#!/usr/bin/env python3
import pathlib,sys,json,subprocess,threading,time,hashlib,traceback,os

HERE=pathlib.Path(__file__).resolve().parent
BINARY=pathlib.Path(sys.argv[1] if len(sys.argv)>1 else HERE/'argo-workflows-ls').resolve()
OUT=pathlib.Path(sys.argv[2] if len(sys.argv)>2 else HERE.parent.parent/'artifacts/wf-lifecycle-gaps').resolve()
OUT.mkdir(parents=True,exist_ok=True)
RESULTS=[]
CFG='schemaVersion: 1\ntargetVersion: "3.6.0"\nreferencePolicy: closed\n'
SMALL='apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: {name: sample}\nspec:\n  templates:\n    - name: work\n      container: {image: alpine}\n'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,indent=2,ensure_ascii=False)+'\n')
def setup(name,files):
 dest=OUT/name;work=dest/'input';work.mkdir(parents=True,exist_ok=True);(work/'.argo-workflows-ls.yaml').write_text(CFG)
 for file,text in files.items():
  p=work/file;p.parent.mkdir(parents=True,exist_ok=True)
  if isinstance(text,bytes):p.write_bytes(text)
  else:p.write_text(text)
 return dest,work
def result(name,checks,details=None):
 r={'name':name,'assertions':checks,'pass':bool(checks) and all(v is True for v in checks.values()),'details':details or {}}
 RESULTS.append(r);save(OUT/'results.json',RESULTS);print(name,'PASS' if r['pass'] else 'FAIL',flush=True)
class Client:
 def __init__(self,dest,work):
  self.dest,self.work=dest,work;self.received=[];self.sent=[];self.raw=bytearray();self.stdinraw=bytearray();self.stderrraw=bytearray();self.cv=threading.Condition();self.error=None;self.next_id=1;self.started=time.monotonic();self.stopped=False
  self.p=subprocess.Popen([str(BINARY),'serve','--stdio'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=os.environ)
  def read_stderr():
   while True:
    data=self.p.stderr.read1(4096)
    if not data:return
    self.stderrraw.extend(data)
  self.stderr_thread=threading.Thread(target=read_stderr,daemon=True);self.stderr_thread.start()
  def reader():
   try:
    while True:
     h={}
     while True:
      line=self.p.stdout.readline();self.raw.extend(line)
      if not line:raise EOFError('stdout closed')
      if line==b'\r\n':break
      k,v=line.decode().strip().split(':',1);h[k.lower()]=v.strip()
     data=self.p.stdout.read(int(h['content-length']));self.raw.extend(data);m=json.loads(data)
     with self.cv:self.received.append({'seconds':time.monotonic()-self.started,'message':m});self.cv.notify_all()
   except Exception as e:
    with self.cv:self.error=e;self.cv.notify_all()
  self.thread=threading.Thread(target=reader,daemon=True);self.thread.start()
  self.request('initialize',{'rootUri':work.as_uri(),'capabilities':{'workspace':{'workspaceEdit':{'documentChanges':True}}}});self.send('initialized',{})
 def send(self,method,params=None,ident=None):self.batch([(method,params,ident)])
 def batch(self,requests):
  frames=bytearray()
  for method,params,ident in requests:
   m={'jsonrpc':'2.0','method':method}
   if params is not None:m['params']=params
   if ident is not None:m['id']=ident
   self.sent.append({'seconds':time.monotonic()-self.started,'message':m});b=json.dumps(m).encode();frames.extend(('Content-Length: %d\r\n\r\n'%len(b)).encode()+b)
  self.stdinraw.extend(frames);self.p.stdin.write(frames);self.p.stdin.flush()
 def messages(self):return [v['message'] for v in self.received]
 def wait(self,predicate,timeout=15):
  end=time.monotonic()+timeout
  with self.cv:
   while True:
    m=self.messages();r=predicate(m)
    if r:return r
    if self.error:raise self.error
    left=end-time.monotonic()
    if left<=0:raise TimeoutError('independent response deadline')
    self.cv.wait(left)
 def response(self,ident,occurrence=1,timeout=15):
  def match(ms):
   rr=[m for m in ms if m.get('id')==ident]
   return rr[occurrence-1] if len(rr)>=occurrence else None
  return self.wait(match,timeout)
 def request(self,method,params=None):
  self.next_id+=1;ident=self.next_id;self.send(method,params,ident);return self.response(ident)
 def diagnostics(self,path,version):return self.wait(lambda ms:next((m for m in reversed(ms) if m.get('method')=='textDocument/publishDiagnostics' and m['params']['uri']==path.as_uri() and m['params'].get('version')==version),None))
 def open(self,path,text,version=1):
  self.send('textDocument/didOpen',{'textDocument':{'uri':path.as_uri(),'version':version,'languageId':'yaml','text':text}})
  return self.diagnostics(path,version)
 def change(self,path,text,version):self.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':version},'contentChanges':[{'text':text}]})
 def index_ready(self,root,after=-1,since=0):
  deadline=time.monotonic()+20
  while time.monotonic()<deadline:
   for line in bytes(self.stderrraw)[since:].splitlines():
    try:state=json.loads(line)
    except (ValueError,UnicodeDecodeError):continue
    if state.get('phase')=='index' and state.get('state')=='Ready' and pathlib.Path(state.get('root','')).resolve()==root.resolve() and state.get('generation',-1)>after:return state
   if self.p.poll() is not None:raise RuntimeError('server exited before index completion')
   time.sleep(.01)
  raise TimeoutError('index completion deadline for '+str(root))
 def close(self):
  start=time.monotonic()
  try:
   if self.p.poll() is None:
    self.request('shutdown');self.send('exit');self.p.wait(timeout=5)
  finally:
   if self.p.poll() is None:self.p.kill();self.p.wait()
   self.thread.join(timeout=1);self.stderr_thread.join(timeout=1);(self.dest/'stdout.raw').write_bytes(self.raw);(self.dest/'stdin.raw').write_bytes(self.stdinraw);(self.dest/'stderr.raw').write_bytes(self.stderrraw);save(self.dest/'traffic.json',{'sent':self.sent,'received':self.received});save(self.dest/'command.json',{'command':[str(BINARY),'serve','--stdio'],'exitCode':self.p.returncode,'shutdownExitSeconds':time.monotonic()-start})
  return self.p.returncode,time.monotonic()-start
def symbol_params(path):return {'textDocument':{'uri':path.as_uri()}}
def huge(count=1200):return 'apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: {name: big}\nspec:\n  templates:\n'+''.join('    - name: template-%d\n      container: {image: alpine}\n'%i for i in range(count))
def saturation():
 name='saturation-id-reuse';large=huge();dest,work=setup(name,{'small.yaml':SMALL,'large.yaml':large});c=Client(dest,work)
 try:
  requests=[('textDocument/documentSymbol',symbol_params(work/'large.yaml'),1000)]
  requests += [('textDocument/documentSymbol',symbol_params(work/'small.yaml'),i) for i in range(1001,1041)]
  requests += [('$/cancelRequest',{'id':1000},None)]
  c.batch(requests)
  replies=c.wait(lambda ms:ms if len([m for m in ms if isinstance(m.get('id'),int) and 1000<=m['id']<=1040])>=41 else None,20)
  terminal=[m for m in replies if isinstance(m.get('id'),int) and 1000<=m['id']<=1040]
  canceled=next(m for m in terminal if m['id']==1000)
  c.send('textDocument/documentSymbol',symbol_params(work/'small.yaml'),1000);reuse=c.response(1000,2)
  code,seconds=c.close()
  result(name,{'one_terminal_each':sorted(m['id'] for m in terminal)==list(range(1000,1041)),'queue_full_exercised':any(m.get('error',{}).get('message')=='request queue is full' for m in terminal),'active_cancelled':canceled.get('error',{}).get('code')==-32800,'reused_completed_id_succeeds':[s['name'] for s in reuse.get('result',[])]==['work'],'clean_bounded_exit':code==0 and seconds<5}, {'responses':terminal,'reuse':reuse,'shutdownSeconds':seconds})
 except Exception:
  c.close();raise
def authority():
 name='buffer-authority';dest,work=setup(name,{'file.yaml':b'\xff'*(5*1024*1024+1)});c=Client(dest,work)
 try:
  ds=c.open(work/'file.yaml',SMALL);symbols=c.request('textDocument/documentSymbol',symbol_params(work/'file.yaml'))
  result(name,{'buffer_diagnostics':ds['params']['diagnostics']==[],'buffer_symbols':[s['name'] for s in symbols.get('result',[])]==['work'],'disk_unchanged':(work/'file.yaml').stat().st_size==5*1024*1024+1},{'diagnostics':ds,'symbols':symbols})
 finally:c.close()
def nested():
 name='nested-root-spelling';dest,work=setup(name,{'parent.yaml':SMALL.replace('name: work','name: parent'),'nested/child.yaml':SMALL.replace('name: work','name: child'),'nested/.argo-workflows-ls.yaml':CFG});c=Client(dest,work)
 try:
  initial=c.index_ready(work);child_start=len(c.stderrraw)
  c.send('workspace/didChangeWorkspaceFolders',{'event':{'added':[{'uri':(work/'nested').as_uri()+'/','name':'nested'}],'removed':[]}})
  parent_added=c.index_ready(work,after=initial['generation']);child_added=c.index_ready(work/'nested',since=child_start)
  r=c.request('workspace/symbol',{'query':'child'})
  (work/'.argo-workflows-ls.yaml').write_text(CFG+'exclude: ["nested/**"]\n')
  c.send('workspace/didChangeWatchedFiles',{'changes':[{'uri':(work/'.argo-workflows-ls.yaml').as_uri(),'type':2}]})
  c.send('workspace/didChangeWorkspaceFolders',{'event':{'added':[],'removed':[{'uri':(work/'nested').as_uri(),'name':'nested'}]}})
  removed_first=c.request('workspace/symbol',{'query':'child'});parent_removed=c.index_ready(work,after=parent_added['generation']);removed=c.request('workspace/symbol',{'query':'child'})
  child_start=len(c.stderrraw)
  c.send('workspace/didChangeWorkspaceFolders',{'event':{'added':[{'uri':(work/'nested').as_uri(),'name':'nested'}],'removed':[]}})
  parent_readded=c.index_ready(work,after=parent_removed['generation']);child_readded=c.index_ready(work/'nested',since=child_start)
  added=c.request('workspace/symbol',{'query':'child'})
  c.send('workspace/didChangeWorkspaceFolders',{'event':{'added':[],'removed':[{'uri':(work/'nested').as_uri()+'/','name':'nested'}]}})
  slash_first=c.request('workspace/symbol',{'query':'child'});parent_final=c.index_ready(work,after=parent_readded['generation']);removed_slash=c.request('workspace/symbol',{'query':'child'})
  withdrawn=lambda response:response.get('error',{}).get('code')==-32801 or 'error' not in response and response.get('result')==[]
  exact_child=lambda response:len(response.get('result',[]))==1 and response['result'][0]['name']=='child' and response['result'][0]['location']['uri']==(work/'nested'/'child.yaml').as_uri()
  result(name,{'one_owner':exact_child(r),'remove_other_spelling':withdrawn(removed_first) and removed.get('result')==[],'add_normal':exact_child(added),'remove_slash_spelling':withdrawn(slash_first) and removed_slash.get('result')==[]},{'response':r,'removedFirst':removed_first,'removed':removed,'added':added,'removedSlashFirst':slash_first,'removedSlash':removed_slash,'readiness':[initial,parent_added,child_added,parent_removed,parent_readded,child_readded,parent_final]})
 finally:c.close()
def interrupted_close(attempt):
 name='close-clear-interrupted-'+str(attempt)
 valid=huge(800).replace('spec:\n','spec:\n  parallelism: 2\n');bad=valid.replace('parallelism: 2','parallelism: wrong')
 dest,work=setup(name,{'a.yaml':valid,'b.yaml':SMALL});c=Client(dest,work)
 try:
  a=work/'a.yaml';b=work/'b.yaml';first=c.open(a,bad);c.open(b,SMALL)
  c.send('textDocument/didClose',{'textDocument':{'uri':a.as_uri()}})
  # Timing is used only to inject a race after the 120ms dispatch delay,
  # never as a completion oracle. Current-version publication is awaited below.
  time.sleep(.22)
  c.change(b,SMALL.replace('name: work','name: changed'),2);current=c.diagnostics(b,2)
  barrier=c.request('textDocument/documentSymbol',symbol_params(b))
  def cleared(ms):return next((m for m in ms if m.get('method')=='textDocument/publishDiagnostics' and m['params']['uri']==a.as_uri() and 'version' not in m['params'] and m['params']['diagnostics']==[]),None)
  clear=None
  try:clear=c.wait(cleared,3)
  except TimeoutError:pass
  publications=[m for m in c.messages() if m.get('method')=='textDocument/publishDiagnostics' and m['params']['uri']==a.as_uri()]
  result(name,{'started_with_error':any(d['code']=='SCHEMA_TYPE' for d in first['params']['diagnostics']),'new_open_buffer_published':current['params']['version']==2,'new_query_completed':'result' in barrier,'closed_uri_cleared':clear is not None},{'aPublications':publications,'barrier':barrier,'interruptionDelaySeconds':.22,'postBarrierClearDeadlineSeconds':3})
 finally:c.close()
def main():
 for name,f in [('saturation-id-reuse',saturation),('buffer-authority',authority),('nested-root-spelling',nested),('close-clear-interrupted-1',lambda:interrupted_close(1)),('close-clear-interrupted-2',lambda:interrupted_close(2))]:
  try:f()
  except Exception as e:result(name,{'runner_completed':False},{'error':repr(e),'traceback':traceback.format_exc()})
 save(OUT/'manifest.json',{'command':sys.argv,'pythonVersion':sys.version,'binarySha256':sha(BINARY),'testSha256':sha(pathlib.Path(__file__)),'expectedSha256':sha(HERE/'wf-lifecycle-gaps.expected.json'),'inputs':{str(p.relative_to(OUT)):sha(p) for p in OUT.rglob('*') if p.is_file() and '/input/' in str(p)},'cases':len(RESULTS),'pass':bool(RESULTS) and all(r['pass'] for r in RESULTS),'scope':'real LSP bounded lifecycle interleavings, not full performance budget acceptance'})
 return 0 if RESULTS and all(r['pass'] for r in RESULTS) else 1
if __name__=='__main__':sys.exit(main())

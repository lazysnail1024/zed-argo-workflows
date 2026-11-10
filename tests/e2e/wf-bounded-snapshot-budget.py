#!/usr/bin/env python3
"""Exact input/buffer limits and dependency acquisition after summary discovery.

Default execution requires the recorded cgroup. --semantic-only keeps every
semantic assertion but explicitly does not claim constrained resource acceptance.
"""
import pathlib,sys,os,subprocess,time,json,importlib.util,threading,signal,gzip,ctypes,struct
root=pathlib.Path(__file__).resolve().parents[2];spec=importlib.util.spec_from_file_location('client',root/'tests/e2e/wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
semantic_only='--semantic-only' in sys.argv;cgpath=None if semantic_only else pathlib.Path('/proc/self/cgroup').read_text().strip().split('::',1)[1];cg=None if semantic_only else pathlib.Path('/sys/fs/cgroup')/cgpath.lstrip('/');mode=os.environ.get('WF_BUDGET_CASES','all');ceiling=(768 if mode=='exact' else 448)*1024**2
if cg is not None:assert int((cg/'memory.max').read_text())<=(1024 if mode=='exact' else 512)*1024**2
MiB=1024**2;base='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: budget}\nspec: {templates: [{name: main, container: {image: alpine}}]}\n'
def sized(n):return base+'#'+'x'*(n-len(base.encode())-2)+'\n'
large=sized(5*MiB);small=sized(3*MiB);dest,work=m.setup('disk-boundary',{});first=work/'000.yaml';first.write_text(large)
for i in range(1,25):os.link(first,work/('%03d.yaml'%i))
last=work/'025.yaml';last.write_text(small)
def supervise(p,state,stop,seconds):
 identity=pathlib.Path('/proc/%d/stat'%p.pid).read_text().split(') ',1)[1].split()[19];start=time.monotonic()
 while not stop.wait(.005) and p.poll() is None:
  if cg is not None:state['peakCgroupBytes']=max(state['peakCgroupBytes'],int((cg/'memory.current').read_text()))
  try:state['peakRSSBytes']=max(state['peakRSSBytes'],int(next(x.split()[1] for x in pathlib.Path('/proc/%d/status'%p.pid).read_text().splitlines() if x.startswith('VmRSS:')))*1024)
  except (OSError,StopIteration):pass
  over_memory=(state['peakRSSBytes'] if cg is None else state['peakCgroupBytes'])>ceiling
  if over_memory or time.monotonic()-start>seconds:
   try:
    if pathlib.Path('/proc/%d/stat'%p.pid).read_text().split(') ',1)[1].split()[19]==identity:
     state['killReason']='memory guard' if over_memory else 'deadline';state['guardBytes']=ceiling
     os.kill(p.pid,signal.SIGKILL);state['killed']=True
   except OSError:pass
   return
for name,over in [('exact-128-MiB',False),('128-MiB-plus-one',True)]:
 if mode=='exact' and over:continue
 if mode=='refusal' and not over:continue
 if over:last.write_text(small+' ')
 state={'peakCgroupBytes':0,'peakRSSBytes':0,'killed':False};stop=threading.Event();cmd=[str(m.BINARY),'check','--root',str(work),'--format','json']
 with (dest/(name+'.stdout')).open('wb') as so,(dest/(name+'.stderr')).open('wb') as se:
  p=subprocess.Popen(cmd,stdout=so,stderr=se);guard=threading.Thread(target=supervise,args=(p,state,stop,8),daemon=True);guard.start();p.wait();stop.set();guard.join()
 try:r=json.loads((dest/(name+'.stdout')).read_text())
 except ValueError:r={}
 reason=any('128 MiB' in str(x) for x in r.get('configurationErrors',[])+r.get('notEvaluated',[]));checks={'naturalTerminal':p.returncode in ([2,3] if over else [0]) and not state['killed'],'boundary':(reason and r.get('filesAnalyzed')==0) if over else not reason and r.get('resourcesAnalyzed')==26};m.result(name,checks,{'exitCode':p.returncode,**state})
if mode=='exact':
 m.save(m.OUT/'manifest.json',{'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'resourceAcceptance':not semantic_only,'runtimeEnvironment':{key:os.environ.get(key) for key in ['GOMEMLIMIT','GOGC','GODEBUG','GOMAXPROCS']},'casesMode':mode,'cgroup':cgpath,'memory.max':None if cg is None else (cg/'memory.max').read_text().strip(),'guardBytes':ceiling,'classification':'Legitimate exact-boundary positive; separate from adversarial 512 MiB protection.'});sys.exit(0 if len(m.RESULTS)==1 and all(r['pass'] for r in m.RESULTS) else 1)
def index_states(client, work):
 rows=[]
 for line in bytes(client.stderr_raw).splitlines():
  try:row=json.loads(line)
  except (ValueError,UnicodeError):continue
  if row.get('phase')=='index' and row.get('root')==str(work):rows.append(row)
 return rows

def ready(client,work,files,opened=None,marker=0):
 deadline=time.monotonic()+10
 while time.monotonic()<deadline:
  rows=index_states(client,work)[marker:]
  if rows and rows[-1].get('state')=='Ready' and rows[-1].get('files')==files and (opened is None or rows[-1].get('retainedTextBytes')==opened):return rows[-1]
  time.sleep(.02)
 raise AssertionError({'root':str(work),'expectedFiles':files,'expectedOpenBytes':opened,'states':index_states(client,work)})

def summary_without_disk_read(client,path):
 # Watch the genuinely new, unwatched URI. A summary request must not turn into
 # the old whole-root raw read, independently of counters reported by the LSP.
 libc=ctypes.CDLL(None,use_errno=True)
 libc.inotify_init1.argtypes=[ctypes.c_int];libc.inotify_init1.restype=ctypes.c_int
 libc.inotify_add_watch.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_uint32];libc.inotify_add_watch.restype=ctypes.c_int
 fd=libc.inotify_init1(os.O_NONBLOCK|os.O_CLOEXEC)
 if fd<0:raise OSError(ctypes.get_errno(),'inotify_init1')
 try:
  if libc.inotify_add_watch(fd,os.fsencode(path),0x20)<0:raise OSError(ctypes.get_errno(),'inotify_add_watch')
  response=client.request('workspace/symbol',{'query':'main'})
  opened=0
  while True:
   try:data=os.read(fd,65536)
   except BlockingIOError:break
   if not data:break
   offset=0
   while offset<len(data):
    descriptor,mask,cookie,length=struct.unpack_from('iIII',data,offset);offset+=16+length
    if mask&0x4000:raise RuntimeError('inotify overflow')
    if mask&0x20:opened+=1
  return response,opened
 finally:os.close(fd)

# The two roots share the retained-buffer budget. Put unrelated padding buffers
# in one root and the selected caller in the other: its real dependency closure
# is only 3 MiB plus a small closed template, rather than 128 MiB of raw inputs.
callee='apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: {name: helper}\nspec: {templates: [{name: run, container: {image: alpine}}]}\n'
caller='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: caller}\nspec:\n  paralelism: 1\n  templates:\n    - name: main\n      steps:\n        - - name: invoke\n            templateRef: {name: helper, template: run}\n'
caller=caller+'#'+'x'*(3*MiB-len(caller.encode())-2)+'\n'
config=m.CONFIG+'defaultNamespace: argo\n'
dest,work=m.setup('buffer-storage',{'callee.yaml':callee},config)
other=dest/'second-root';other.mkdir();(other/'.argo-workflows-ls.yaml').write_text(config)
client=m.LSP(dest,work);state={'peakCgroupBytes':0,'peakRSSBytes':0,'killed':False}
stop=threading.Event();guard=threading.Thread(target=supervise,args=(client.p,state,stop,45),daemon=True);guard.start()
sent=gzip.open(dest/'sent.jsonl.gz','wt');original_send=client.send
for frame in client.sent:sent.write(json.dumps(frame)+'\n')
client.sent.clear()
def stream(*args,**kwargs):
 original_send(*args,**kwargs);sent.write(json.dumps(client.sent[-1])+'\n');client.sent.clear()
client.send=stream
try:
 ready(client,work,1)
 client.send('workspace/didChangeWorkspaceFolders',{'event':{'added':[{'uri':other.as_uri(),'name':'second'}],'removed':[]}})
 paths=[]
 for i in range(26):
  path=(other if i<25 else work)/('%03d.yaml'%i);paths.append(path)
  client.send('textDocument/didOpen',{'textDocument':{'uri':path.as_uri(),'version':1,'languageId':'yaml','text':large if i<25 else caller}})
 r=client.request('textDocument/documentSymbol',{'textDocument':{'uri':paths[-1].as_uri()}})
 m.result('exact-buffer-boundary',{'exactLastBuffer':[x['name'] for x in r.get('result',[])]==['main','invoke']},{'response':r})
 ready(client,other,25)
 indexed=ready(client,work,2,128*MiB)
 extra=work/'disk-extra.yaml';extra.write_text(base)
 r,reads=summary_without_disk_read(client,extra)
 m.result('summaries-at-full-buffer-budget',{
  'allAcceptedBufferSymbols':'error' not in r and len(r.get('result',[]))==26 and {x['location']['uri'] for x in r.get('result',[])}=={p.as_uri() for p in paths},
  'unknownDiskURIIsNotHydrated':reads==0 and all(x['location']['uri']!=extra.as_uri() for x in r.get('result',[])),
  'fullBufferReservation':indexed['retainedTextBytes']==128*MiB},{'response':r,'extraFileOpens':reads,'state':indexed})
 extra.unlink()
 position=m.pos(caller,'paralelism');request={'textDocument':{'uri':paths[-1].as_uri()},'range':{'start':position,'end':position}}
 refusal=client.request('textDocument/codeAction',request)
 m.result('buffers-reserved-before-dependency',{
  'noUnbudgetedDependencyEdit':refusal.get('result')==[] and 'error' not in refusal or refusal.get('error',{}).get('code')==-32803 and '128 MiB' in refusal.get('error',{}).get('message','') and 'result' not in refusal,
  'knownClosedTargetRetained':(work/'callee.yaml').read_text()==callee},
  {'response':refusal,'openBytes':128*MiB,'closedDependencyBytes':len(callee.encode())})
 refused=other/'overflow.yaml';refused.write_text(base.replace('name: main','name: disk-only'))
 client.send('textDocument/didOpen',{'textDocument':{'uri':refused.as_uri(),'version':1,'languageId':'yaml','text':base}})
 r=client.request('textDocument/documentSymbol',{'textDocument':{'uri':refused.as_uri()}})
 m.result('cross-root-buffer-overflow',{'explicitRefusal':r.get('error',{}).get('code')==-32602 and '128 MiB' in r.get('error',{}).get('message',''),'noDiskFallback':'result' not in r},{'response':r})
 marker=len(index_states(client,other))
 client.send('textDocument/didClose',{'textDocument':{'uri':paths[0].as_uri()}})
 client.send('textDocument/didChange',{'textDocument':{'uri':refused.as_uri(),'version':2},'contentChanges':[{'text':base}]})
 r=client.request('textDocument/documentSymbol',{'textDocument':{'uri':refused.as_uri()}})
 ready(client,other,25,123*MiB+len(base.encode()),marker)
 recovered=client.request('textDocument/codeAction',request)
 fixed=[]
 for action in recovered.get('result',[]):
  resolved=client.request('codeAction/resolve',action)
  if 'result' in resolved:fixed.append(m.apply(caller,resolved['result']['edit'],paths[-1].as_uri(),1))
 m.result('buffer-budget-recovery',{
  'fullReplacementAccepted':[x['name'] for x in r.get('result',[])]==['main'],
  'releasedBudgetRestoresExactDependencyEdit':fixed==[caller.replace('paralelism','parallelism')],
  'notKilled':not state['killed']},{'resourceObservation':state,'documentSymbols':r,'actions':recovered})
except Exception as e:m.result('buffer-budget-runner',{'completed':False},{'error':repr(e),**state})
finally:
 stop.set();guard.join()
 try:client.close()
 except Exception as error:m.result('buffer-budget-clean-exit',{'cleanExit':False},{'error':repr(error),'exitCode':client.p.returncode})
 finally:sent.close()
m.save(m.OUT/'manifest.json',{
 'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'resourceAcceptance':not semantic_only,
 'runtimeEnvironment':{key:os.environ.get(key) for key in ['GOMEMLIMIT','GOGC','GODEBUG','GOMAXPROCS']},'casesMode':mode,
 'configAccounting':'Disk configuration has a separate 1 MiB bound. Open configuration buffers count globally. Detached symbol search borrows summaries; dependency materialization reserves all authoritative open-buffer text before admitting closed input.',
 'cgroup':cgpath,'memory.max':None if cg is None else (cg/'memory.max').read_text().strip(),
 'localGuard':'Server RSS only; no cgroup acceptance' if semantic_only else 'Whole enforced cgroup memory',
 'guardBytes':ceiling,'bufferResourceObservation':state})
sys.exit(0 if len(m.RESULTS)==(6 if mode=='refusal' else 7) and all(r['pass'] for r in m.RESULTS) else 1)

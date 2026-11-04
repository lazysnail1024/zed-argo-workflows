#!/usr/bin/env python3
"""Exact 128 MiB input boundary and global authoritative-buffer storage budget."""
import pathlib,sys,os,subprocess,time,json,importlib.util,threading,signal,gzip
root=pathlib.Path(__file__).resolve().parents[2];spec=importlib.util.spec_from_file_location('client',root/'tests/e2e/wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
cgpath=pathlib.Path('/proc/self/cgroup').read_text().strip().split('::',1)[1];cg=pathlib.Path('/sys/fs/cgroup')/cgpath.lstrip('/');mode=os.environ.get('WF_BUDGET_CASES','all');ceiling=(768 if mode=='exact' else 448)*1024**2;assert int((cg/'memory.max').read_text())<=(1024 if mode=='exact' else 512)*1024**2
MiB=1024**2;base='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: budget}\nspec: {templates: [{name: main, container: {image: alpine}}]}\n'
def sized(n):return base+'#'+'x'*(n-len(base.encode())-2)+'\n'
large=sized(5*MiB);small=sized(3*MiB);dest,work=m.setup('disk-boundary',{});first=work/'000.yaml';first.write_text(large)
for i in range(1,25):os.link(first,work/('%03d.yaml'%i))
last=work/'025.yaml';last.write_text(small)
def supervise(p,state,stop,seconds):
 identity=pathlib.Path('/proc/%d/stat'%p.pid).read_text().split(') ',1)[1].split()[19];start=time.monotonic()
 while not stop.wait(.005) and p.poll() is None:
  state['peakCgroupBytes']=max(state['peakCgroupBytes'],int((cg/'memory.current').read_text()))
  try:state['peakRSSBytes']=max(state['peakRSSBytes'],int(next(x.split()[1] for x in pathlib.Path('/proc/%d/status'%p.pid).read_text().splitlines() if x.startswith('VmRSS:')))*1024)
  except (OSError,StopIteration):pass
  if state['peakCgroupBytes']>ceiling or time.monotonic()-start>seconds:
   try:
    if pathlib.Path('/proc/%d/stat'%p.pid).read_text().split(') ',1)[1].split()[19]==identity:os.kill(p.pid,signal.SIGKILL);state['killed']=True
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
 m.save(m.OUT/'manifest.json',{'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'cgroup':cgpath,'memory.max':(cg/'memory.max').read_text().strip(),'guardBytes':ceiling,'classification':'Legitimate exact-boundary positive; separate from adversarial 512 MiB protection.'});sys.exit(0 if len(m.RESULTS)==1 and all(r['pass'] for r in m.RESULTS) else 1)
# Across two roots, the live server may retain at most 128 MiB of buffer text.
dest,work=m.setup('buffer-storage',{});other=dest/'second-root';other.mkdir();(other/'.argo-workflows-ls.yaml').write_text(m.CONFIG);client=m.LSP(dest,work);state={'peakCgroupBytes':0,'peakRSSBytes':0,'killed':False};stop=threading.Event();guard=threading.Thread(target=supervise,args=(client.p,state,stop,45),daemon=True);guard.start();sent=gzip.open(dest/'sent.jsonl.gz','wt');original_send=client.send
for frame in client.sent:sent.write(json.dumps(frame)+'\n')
client.sent.clear()
def stream(*args,**kwargs):original_send(*args,**kwargs);sent.write(json.dumps(client.sent[-1])+'\n');client.sent.clear()
client.send=stream
try:
 client.send('workspace/didChangeWorkspaceFolders',{'event':{'added':[{'uri':other.as_uri(),'name':'second'}],'removed':[]}})
 paths=[]
 for i in range(26):
  path=(work if i%2==0 else other)/('%03d.yaml'%i);paths.append(path);client.send('textDocument/didOpen',{'textDocument':{'uri':path.as_uri(),'version':1,'languageId':'yaml','text':large if i<25 else small}})
 r=client.request('textDocument/documentSymbol',{'textDocument':{'uri':paths[-1].as_uri()}});m.result('exact-buffer-boundary',{'exactLastBuffer':[x['name'] for x in r.get('result',[])]==['main']})
 extra=work/'disk-extra.yaml';extra.write_text(base);r=client.request('workspace/symbol',{'query':'main'});m.result('buffers-reserved-before-disk',{'explicitBudgetRefusal':r.get('error',{}).get('code')==-32803 and '128 MiB' in r.get('error',{}).get('message',''),'noPartialResult':'result' not in r});extra.unlink()
 refused=other/'overflow.yaml';refused.write_text(base.replace('name: main','name: disk-only'));client.send('textDocument/didOpen',{'textDocument':{'uri':refused.as_uri(),'version':1,'languageId':'yaml','text':base}});r=client.request('textDocument/documentSymbol',{'textDocument':{'uri':refused.as_uri()}});m.result('cross-root-buffer-overflow',{'explicitRefusal':r.get('error',{}).get('code')==-32602 and '128 MiB' in r.get('error',{}).get('message',''),'noDiskFallback':'result' not in r})
 client.send('textDocument/didClose',{'textDocument':{'uri':paths[0].as_uri()}});client.send('textDocument/didChange',{'textDocument':{'uri':refused.as_uri(),'version':2},'contentChanges':[{'text':base}]});r=client.request('textDocument/documentSymbol',{'textDocument':{'uri':refused.as_uri()}});m.result('buffer-budget-recovery',{'fullReplacementAccepted':[x['name'] for x in r.get('result',[])]==['main'],'notKilled':not state['killed']},state)
except Exception as e:m.result('buffer-budget-runner',{'completed':False},{'error':repr(e),**state})
finally:
 stop.set();guard.join();client.close();sent.close()
m.save(m.OUT/'manifest.json',{'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'configAccounting':'Disk configuration has a separate 1 MiB bound. Open configuration buffers count in global retained buffer text. Each analysis reserves all retained buffers before loading additional disk snapshot text.','cgroup':cgpath,'memory.max':(cg/'memory.max').read_text().strip()});sys.exit(0 if len(m.RESULTS)==(5 if mode=='refusal' else 6) and all(r['pass'] for r in m.RESULTS) else 1)

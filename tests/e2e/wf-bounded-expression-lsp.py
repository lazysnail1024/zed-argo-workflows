#!/usr/bin/env python3
"""Bounded Expr refusal must preserve accepted Rename and subsequent requests.

--semantic-only preserves all semantic/time assertions with a local RSS guard;
the default path still requires the real cgroup for resource acceptance.
"""
import pathlib,sys,time,json,importlib.util,threading,os,signal
root=pathlib.Path(__file__).resolve().parents[2];spec=importlib.util.spec_from_file_location('client',root/'tests/e2e/wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
semantic_only='--semantic-only' in sys.argv;cgpath=None if semantic_only else pathlib.Path('/proc/self/cgroup').read_text().strip().split('::',1)[1];cg=None if semantic_only else pathlib.Path('/sys/fs/cgroup')/cgpath.lstrip('/')
if cg is not None:assert int((cg/'memory.max').read_text())<=512*1024**2
cases=[('accepted-256','('*256+'inputs.parameters.alpha'+')'*256,True),('depth-257','('*257+'inputs.parameters.alpha'+')'*257,False),('unary-depth','!'*30000+'inputs.parameters.alpha',False),('let-depth',''.join('let v%d = 1; '%i for i in range(10000))+'inputs.parameters.alpha',False),('large-literal',repr('x'*900000)+' + inputs.parameters.alpha',True),('malformed','inputs.parameters.alpha +',False),('large-literal-malformed',repr('x'*900000)+' +',False)]
cases += [('wide-independent-lets','string(['+','.join('let v = 1; v' for _ in range(300))+']) + inputs.parameters.alpha',True),('wide-independent-powers','string(['+','.join('2 ** 3' for _ in range(300))+']) + inputs.parameters.alpha',True),('wide-independent-conditionals','string(['+','.join('true ? 1 : 0' for _ in range(300))+']) + inputs.parameters.alpha',True)]
def document(expr):return 'apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: bounded}\nspec:\n  entrypoint: main\n  templates:\n    - name: main\n      inputs:\n        parameters: [{name: alpha, default: seed}]\n      container:\n        image: alpine\n        args: ['+json.dumps('{{='+expr+'}}')+']\n'
selected=set(filter(None,os.environ.get('WF_EXPR_CASES','').split(',')))
for name,expr,positive in cases:
 if selected and name not in selected:continue
 text=document(expr);dest,work=m.setup(name,{'case.yaml':text});client=None;stop=threading.Event();state={'peak':0,'killed':False};start=time.monotonic()
 try:
  client=m.LSP(dest,work);pid=client.p.pid;identity=pathlib.Path('/proc/%d/stat'%pid).read_text().split(') ',1)[1].split()[19]
  def guard():
   while not stop.wait(.005):
    if cg is not None:measured=int((cg/'memory.current').read_text())
    else:
     try:measured=next((int(line.split()[1])*1024 for line in pathlib.Path('/proc/%d/status'%pid).read_text().splitlines() if line.startswith('VmRSS:')),0)
     except FileNotFoundError:return
    state['peak']=max(state['peak'],measured)
    if state['peak']>384*1024**2 or time.monotonic()-start>10:
     try:
      if pathlib.Path('/proc/%d/stat'%pid).read_text().split(') ',1)[1].split()[19]==identity:os.kill(pid,signal.SIGKILL);state['killed']=True
     except OSError:pass
     return
  monitor=threading.Thread(target=guard,daemon=True);monitor.start();path=work/'case.yaml';client.open(path,text);params={'textDocument':{'uri':path.as_uri()},'position':m.pos(text,'name: alpha',offset=6),'newName':'beta'};r=client.request('textDocument/rename',params);seconds=time.monotonic()-start
  if positive:checks={'exactRename':m.apply(text,r['result'],path.as_uri(),1)==text.replace('name: alpha','name: beta').replace('parameters.alpha','parameters.beta')}
  else:checks={'refusal':r.get('error',{}).get('code')==-32602,'reason':any(w in r.get('error',{}).get('message','').lower() for w in ['depth','budget','expression'])}
  normal=document('inputs.parameters.alpha');client.change(path,normal,2);r2=client.request('textDocument/rename',params);checks.update({'normalRecovery':m.apply(normal,r2['result'],path.as_uri(),2)==normal.replace('name: alpha','name: beta').replace('parameters.alpha','parameters.beta'),'bounded':seconds<5.25 and not state['killed']});m.result(name,checks,{'seconds':seconds,'peakCgroupBytes':None if semantic_only else state['peak'],'peakProcessRSSBytes':state['peak'] if semantic_only else None})
 except Exception as e:m.result(name,{'completed':False},{'error':repr(e),**state})
 finally:
  stop.set()
  if client:client.close()
m.save(m.OUT/'manifest.json',{'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'resourceAcceptance':not semantic_only,'cgroup':cgpath,'memory.max':None if cg is None else (cg/'memory.max').read_text().strip(),'localGuard':'Server RSS only; no cgroup acceptance' if semantic_only else 'Whole enforced cgroup memory'});sys.exit(0 if len(m.RESULTS)==(len(selected) if selected else 10) and all(r['pass'] for r in m.RESULTS) else 1)

#!/usr/bin/env python3
"""Real stdio cancellation must reap Helm and a descendant retaining pipes."""
import importlib.util,json,os,pathlib,signal,sys,time,traceback
p=pathlib.Path(__file__).with_name('wf-audit-gaps.py');sp=importlib.util.spec_from_file_location('wire',p);m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m)
text='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: sample}\nspec:\n  entrypoint: main\n  templates:\n    - name: main\n      container: {image: "{{ .Values.image }}"}\n'
def live(pid):
 try:return pathlib.Path('/proc',str(pid),'stat').read_text().split()[2]!='Z'
 except FileNotFoundError:return False
pids={};c=None
try:
 d,w=m.setup('cancel',{'Chart.yaml':'apiVersion: v2\nname: sample\nversion: 0.1.0\n','templates/wf.yaml':text},config='');f=w/'templates/wf.yaml';mark=d/'started.json';helpers=d/'helpers';helpers.mkdir(exist_ok=True);tool=helpers/'helm'
 tool.write_text('#!/usr/bin/python3\nimport sys,subprocess,pathlib,json,os,time\nif sys.argv[1]=="version":print("v3.22.1");sys.exit(0)\nchild=subprocess.Popen(["/usr/bin/sleep","600"])\ntarget=pathlib.Path('+repr(str(mark))+');temporary=target.with_suffix(".tmp");temporary.write_text(json.dumps({"helm":os.getpid(),"child":child.pid,"group":os.getpgrp()}));temporary.replace(target)\ntime.sleep(600)\n');tool.chmod(0o755)
 os.environ['PATH']=str(helpers)+os.pathsep+os.environ['PATH'];os.environ['XDG_CACHE_HOME']=str(m.OUT/'cache');c=m.LSP(d,w);c.open(f,text,language_id='helm')
 r=c.request('textDocument/codeAction',{'textDocument':{'uri':f.as_uri()},'range':{'start':{'line':0,'character':0},'end':{'line':0,'character':0}},'context':{'diagnostics':[]}});cmd=next(a['command'] for a in r['result'] if a.get('command',{}).get('command')=='argo-workflows.renderCheck')
 c.send('workspace/executeCommand',{'command':cmd['command'],'arguments':cmd['arguments']},900)
 deadline=time.monotonic()+6
 while not mark.exists() and time.monotonic()<deadline:time.sleep(.02)
 assert mark.exists(),'Helm did not start'
 pids=json.loads(mark.read_text());c.send('$/cancelRequest',{'id':900});r=c.wait(lambda v:v.get('id')==900)
 deadline=time.monotonic()+2
 while any(live(pids[x]) for x in ['helm','child']) and time.monotonic()<deadline:time.sleep(.02)
 m.result('cancel-reaps-descendants',{'cancelled':r.get('error',{}).get('code')==-32800,'helm_reaped':not live(pids['helm']),'child_reaped':not live(pids['child'])},{'response':r,'pids':pids})
except Exception as e:m.result('runner',{'completed':False},{'error':repr(e),'traceback':traceback.format_exc()})
finally:
 if pids and any(live(pids[x]) for x in ['helm','child']):
  try:os.killpg(pids['group'],signal.SIGKILL)
  except ProcessLookupError:pass
 if c:c.close()
 m.save(m.OUT/'manifest.json',{'command':sys.argv,'pythonVersion':sys.version,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'helperSha256':m.sha(tool) if 'tool' in locals() else None})
sys.exit(0 if len(m.RESULTS)==1 and all(x['pass'] for x in m.RESULTS) else 1)

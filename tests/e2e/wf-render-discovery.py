#!/usr/bin/env python3
"""Real Helm default discovery and explicit editor command, no per-project config."""
import importlib.util,pathlib,sys,os,json,traceback
# Hosted Linux integration runs the same behavior contract against both pinned majors.
if len(sys.argv)==5:
 import subprocess,hashlib
 output=pathlib.Path(sys.argv[2]).resolve();output.mkdir(parents=True,exist_ok=True);rows=[];runs=[]
 for label,tool in zip(['helm3','helm4'],sys.argv[3:]):
  command=[sys.executable,__file__,sys.argv[1],str(output/label),tool]
  result=subprocess.run(command,capture_output=True,timeout=120)
  (output/(label+'.stdout')).write_bytes(result.stdout);(output/(label+'.stderr')).write_bytes(result.stderr)
  child=json.loads((output/label/'results.json').read_text()) if (output/label/'results.json').exists() else []
  rows.extend(dict(row,case=label+'/'+row['case']) for row in child)
  runs.append({'command':command,'exitCode':result.returncode,'cases':len(child)})
 (output/'results.json').write_text(json.dumps(rows,indent=2)+'\n')
 sha=lambda path:hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
 (output/'manifest.json').write_text(json.dumps({'command':sys.argv,'pythonVersion':sys.version,'runs':runs,'binarySha256':sha(sys.argv[1]),'testSha256':sha(__file__),'helmSha256':{label:sha(tool) for label,tool in zip(['helm3','helm4'],sys.argv[3:])}},indent=2)+'\n')
 sys.exit(0 if len(rows)==26 and all(row['pass'] for row in rows) and all(run['exitCode']==0 and run['cases']==13 for run in runs) else 1)
p=pathlib.Path(__file__).with_name('wf-audit-gaps.py');s=importlib.util.spec_from_file_location('wire',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
helm=pathlib.Path(sys.argv[3]).resolve()
class Client(m.LSP):
 def send(self,method,params=None,ident=None):
  if method=='initialize':params['capabilities']['window']={'showDocument':{'support':True}}
  return super().send(method,params,ident)

text='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: sample}\nspec:\n  entrypoint: main\n  templates:\n    - name: main\n      container: {image: "{{ .Values.image }}"}\n'
try:
 d,w=m.setup('render',{'chart/Chart.yaml':'apiVersion: v2\nname: sample\nversion: 0.1.0\n','chart/values.yaml':'image: alpine\n','chart/templates/wf.yaml':text},config='')
 f=w/'chart/templates/wf.yaml';env=dict(os.environ);env['XDG_CACHE_HOME']=str(m.OUT/'cache');os.environ['XDG_CACHE_HOME']=env['XDG_CACHE_HOME'];env['PATH']=str(helm.parent)+os.pathsep+env.get('PATH','')
 for label,args,cwd in [('file',['--file',str(f)],w),('relative-file',['--file','chart/templates/wf.yaml'],w),('cwd',[],f.parent),('root',['--root',str(w/'chart')],w)]:
  r=m.command(d,label,[m.BINARY,'render-check',*args],cwd=cwd,env=env);report=json.loads(r.stdout)
  m.result(label,{'complete':report.get('analysisComplete') is True,'success':r.returncode==0,'generated_location':all('.argo' in x.get('uri','') or 'render' in x.get('uri','') for x in report.get('diagnostics',[]))},report)
 os.environ['PATH']=env['PATH'];c=Client(d,w)
 try:
  c.open(f,text,language_id='helm');r=c.request('textDocument/codeAction',{'textDocument':{'uri':f.as_uri()},'range':{'start':{'line':0,'character':0},'end':{'line':0,'character':0}},'context':{'diagnostics':[]}})
  actions=[a for a in r.get('result',[]) if a.get('command',{}).get('command')=='argo-workflows.renderCheck']
  m.result('explicit-action',{'one_action':len(actions)==1},r)
  if actions:
   before=list((m.OUT/'cache').rglob('render-report.json'))
   resolved=c.request('codeAction/resolve',actions[0])
   m.result('resolve-command',{'preserved':resolved.get('result')==actions[0],'not_executed':list((m.OUT/'cache').rglob('render-report.json'))==before},resolved)
   cmd=actions[0]['command'];r=c.request('workspace/executeCommand',{'command':cmd['command'],'arguments':cmd['arguments']});m.result('execute',{'success':not r.get('error') and bool(r.get('result',{}).get('generatedFile')),'opens_generated':any(x.get('method')=='window/showDocument' and x['params']['uri']==pathlib.Path(r['result']['generatedFile']).as_uri() for x in c.received)},r)
   c.change(f,text+'# unsaved\n',2)
  r=c.request('workspace/executeCommand',{'command':'argo-workflows.renderCheck','arguments':cmd['arguments']})
  m.result('stale-refused',{'refused':r.get('error',{}).get('code')==-32801},r)
  r=c.request('workspace/executeCommand',{'command':'argo-workflows.renderCheck','arguments':[f.as_uri(),'2']})
  m.result('dirty-refused',{'save_required':'save all chart buffers' in r.get('error',{}).get('message','')},r)
  r=c.request('workspace/executeCommand',{'command':'argo-workflows.renderCheck','arguments':[(w.parent/'foreign.yaml').as_uri(),'1']})
  m.result('foreign-refused',{'refused':bool(r.get('error'))},r)
  outside=m.OUT/'outside-chart';(outside/'templates').mkdir(parents=True,exist_ok=True)
  (outside/'Chart.yaml').write_text('apiVersion: v2\nname: outside\nversion: 0.1.0\n');(outside/'templates/wf.yaml').write_text(text)
  (w/'linked-chart').symlink_to(outside,target_is_directory=True)
  linked=w/'linked-chart/templates/wf.yaml';c.open(linked,text,language_id='helm')
  r=c.request('workspace/executeCommand',{'command':'argo-workflows.renderCheck','arguments':[linked.as_uri(),'1']})
  m.result('symlink-outside-refused',{'refused':'outside workspace' in r.get('error',{}).get('message','')},r)
  structural='{{ if .Values.enabled }}\n'+text+'{{ end }}\n'
  c.change(f,structural,3)
  r=c.request('textDocument/codeAction',{'textDocument':{'uri':f.as_uri()},'range':{'start':{'line':0,'character':0},'end':{'line':0,'character':0}},'context':{'diagnostics':[]}})
  m.result('structural-render-action',{'available':any(a.get('command',{}).get('command')=='argo-workflows.renderCheck' for a in r.get('result') or [])},r)
  f.write_text(text);c.change(f,text,4)
  config='schemaVersion: 1\ntargetVersion: "3.6.0"\nscopes:\n  - name: selected\n    include: ["chart/**"]\n  - name: unrelated\n    include: ["other/**"]\n'
  (w/'.argo-workflows-ls.yaml').write_text(config)
  r=c.request('workspace/executeCommand',{'command':'argo-workflows.renderCheck','arguments':[f.as_uri(),'4']})
  m.result('selected-file-scope',{'success':not r.get('error') and bool(r.get('result',{}).get('generatedFile'))},r)
 finally:c.close()
except Exception as e:m.result('runner',{'completed':False},{'error':repr(e),'traceback':traceback.format_exc()})
finally:m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'helmSha256':m.sha(helm),'inputs':{str(x.relative_to(m.OUT)):m.sha(x) for x in m.OUT.rglob('*') if x.is_file() and 'input' in x.parts}})
sys.exit(0 if len(m.RESULTS)==13 and all(x['pass'] for x in m.RESULTS) else 1)

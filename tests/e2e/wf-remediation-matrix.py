#!/usr/bin/env python3
"""Dense DAG/parameter/artifact and multi-document/multi-scope/root workloads."""
import importlib.util,pathlib,sys,time,json,hashlib,traceback
p=pathlib.Path(__file__).with_name('wf-audit-gaps.py');spec=importlib.util.spec_from_file_location('wire',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
lib='''apiVersion: argoproj.io/v1alpha1
kind: WorkflowTemplate
metadata: {name: library, namespace: argo}
spec:
  templates:
    - name: worker
      inputs:
        parameters: [{name: message}]
        artifacts: [{name: file, path: /tmp/input}]
      outputs:
        parameters: [{name: answer, value: "{{inputs.parameters.message}}"}]
      container: {image: alpine, args: ["{{inputs.parameters.message}}"]}
'''
def caller(i):
 text='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: caller-'+str(i)+', namespace: argo}\nspec:\n  entrypoint: main\n  templates:\n    - name: main\n      dag:\n        tasks:\n'
 for task in range(20):
  value='hello' if task==0 else '{{tasks.task'+str(task-1)+'.outputs.parameters.answer}}'
  text+='          - name: task'+str(task)+'\n'
  if task:text+='            dependencies: [task'+str(task-1)+']\n'
  text+='            templateRef: {name: library, template: worker}\n            arguments:\n              parameters: [{name: message, value: "'+value+'"}]\n              artifacts: [{name: file, raw: {data: sample}}]\n'
 return text
for name,count,scoped,multiroot,multidoc in [('one',1,False,False,False),('ten',10,True,False,True),('thirty',30,True,True,True)]:
 config=m.CONFIG+('scopes:\n  - name: alpha\n    include: ["alpha/**"]\n  - name: beta\n    include: ["beta/**"]\n' if scoped else '')
 files={};paths=[]
 for group in (['alpha','beta'] if scoped else ['alpha']):files[group+'/library.yaml']=lib
 for i in range(count):
  group='beta' if scoped and i%2 else 'alpha';relative=group+f'/caller-{i}.yaml';files[relative]=caller(i);paths.append(relative)
 if multidoc:
  for group in ['alpha','beta']:
   files[group+'/library.yaml']+='---\n'+m.BASE.replace('name: base,','name: extra,')
 dest,work=m.setup(name,files,config);c=m.LSP(dest,work)
 try:
  if multiroot:
   other=dest/'other-root';other.mkdir(exist_ok=True);(other/'library.yaml').write_text(lib);(other/'.argo-workflows-ls.yaml').write_text(m.CONFIG);c.send('workspace/didChangeWorkspaceFolders',{'event':{'added':[{'uri':other.as_uri(),'name':'other'}],'removed':[]}})
  started=time.monotonic()
  for rel in paths:c.send('textDocument/didOpen',{'textDocument':{'uri':(work/rel).as_uri(),'version':1,'languageId':'yaml','text':files[rel]}})
  deadline=time.monotonic()+15
  while time.monotonic()<deadline:
   latest={x['params']['uri']:x['params']['diagnostics'] for x in c.received if x.get('method')=='textDocument/publishDiagnostics' and x['params'].get('version')==1}
   if len(latest)==count:break
   time.sleep(.02)
  path=work/paths[0];text=files[paths[0]];r=c.request('textDocument/definition',{'textDocument':{'uri':path.as_uri()},'position':m.pos(text,'template: worker',offset=12)});expected=(work/'alpha/library.yaml').as_uri();checks={'all_published':len(latest)==count,'dense_semantics_no_errors':all(not any(d['severity']==1 or d['code'].startswith('WF_ANALYSIS') for d in ds) for ds in latest.values()),'definition_isolated':len(r.get('result',[]))==1 and r['result'][0]['uri']==expected}
  broken=text.replace('template: worker','template: absent',1);ds=c.change(path,broken,2);checks['semantic_edit_detected']=any(d['code']=='WF_TEMPLATE_REF_UNRESOLVED' and d['severity']==1 for d in ds);ds=c.change(path,text,3);checks['semantic_recovery']=not any(d['severity']==1 for d in ds)
  parameter=text.replace('name: message, value:', 'name: absent, value:',1);ds=c.change(path,parameter,4);checks['parameter_edit_detected']=any(d['code']=='WF_ARGUMENT_MISSING' for d in ds);c.change(path,text,5)
  dependency=text.replace('dependencies: [task0]','dependencies: [missing]',1);ds=c.change(path,dependency,6);checks['dependency_edit_detected']=any(d['code']=='WF_DEPENDENCY_MISSING' for d in ds);c.change(path,text,7)
  m.result(name,checks,{'seconds':time.monotonic()-started,'yamlFiles':len(files),'resources':len(files)+(2 if multidoc else 0),'externalReferences':count*20,'parameterReferences':count*19,'artifactArguments':count*20,'bytes':sum(len(t.encode()) for t in files.values()),'scopeCount':2 if scoped else 1,'rootCount':2 if multiroot else 1,'openCount':count,'definition':r})
 except Exception as e:m.result(name,{'completed':False},{'error':repr(e),'traceback':traceback.format_exc()})
 finally:c.close()
m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'inputs':{str(p.relative_to(m.OUT)):m.sha(p) for p in m.OUT.rglob('*.yaml')}});sys.exit(0 if len(m.RESULTS)==3 and all(x['pass'] for x in m.RESULTS) else 1)

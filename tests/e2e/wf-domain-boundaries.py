#!/usr/bin/env python3
"""Read-only domain isolation and incomplete/ambiguous contexts, real processes."""
import pathlib,sys,json,importlib.util
root=pathlib.Path(__file__).resolve().parents[2];spec=importlib.util.spec_from_file_location('wire',root/'tests/e2e/wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
head='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: caller, namespace: argo}\nspec:\n'
local=head+'''  entrypoint: main
  templates:
    - name: main
      inputs: {parameters: [{name: message, default: hello}]}
      container: {image: alpine, args: ["{{inputs.parameters.message}}"]}
'''
for name,text,needle,expected in [
 ('unrelated-schema-error',local.replace('  entrypoint:', '  parallelism: invalid\n  entrypoint:'),'parameters.message',True),
 ('unrelated-malformed-expression',local.replace('args: [','args: ["{{= inputs[ }}", '),'parameters.message',True),
 ('unrelated-dynamic',local.replace('args: [','args: ["{{= $env.inputs }}", '),'parameters.message',True),
 ('unfinished-neighbor',local+'---\napiVersion: argoproj.io/v1alpha1\nkind: Workflow\nspec: [\n','parameters.message',True),
 ('ambiguous-declaration',local.replace('default: hello}', 'default: hello}, {name: message, default: other}'),'parameters.message','ambiguous'),
 ('dynamic-index',local.replace('inputs.parameters.message',"= inputs.parameters[workflow.name]"),'workflow.name',False),
 ('inline-shadow',local.replace('      container: {image: alpine, args: ["{{inputs.parameters.message}}"]}', '      steps:\n        - - name: nested\n            inline:\n              inputs: {parameters: [{name: message, default: inner}]}\n              container: {image: alpine, args: ["{{inputs.parameters.message}}"]}'),'parameters.message',False),
 ('foreign-gvk',local.replace('kind: Workflow','kind: Application'),'parameters.message',False),
 ('encoded-member',local.replace('parameters.message',r'parameters.me\u0073sage'),'parameters.me',False),
]:
 dest,work=m.setup(name,{'case.yaml':text});path=work/'case.yaml';client=m.LSP(dest,work)
 try:
  client.open(path,text);p=m.pos(text,needle,offset=len(needle)-2);r=client.request('textDocument/definition',{'textDocument':{'uri':path.as_uri()},'position':p});got=r.get('result',[]);decl=m.pos(text,'name: message',offset=6);checks={'exactBinding':len(got)==1 and got[0]['uri']==path.as_uri() and got[0]['range']['start']==decl if expected else got==[]}
  if expected=='ambiguous':checks['exactBinding']=len(got)==2 and sorted(x['range']['start']['character'] for x in got)==sorted(m.pos(text,'name: message',occurrence=i,offset=6)['character'] for i in [0,1]) and all(x['uri']==path.as_uri() for x in got)
  checks['noProtocolError']='error' not in r
  m.result(name,checks,{'response':r})
 finally:client.close()
# Prior Steps groups are visible; a peer within the same parallel group is not.
for name,same_group in [('previous-step',False),('parallel-peer',True)]:
 body=head+'''  templates:
    - name: main
      steps:
        - - name: producer
            template: worker
'''+('          - name: consumer\n' if same_group else '        - - name: consumer\n')+'''            template: worker
            arguments: {parameters: [{name: message, value: "{{steps.producer.outputs.result}}"}]}
    - name: worker
      inputs: {parameters: [{name: message, default: hello}]}
      container: {image: alpine}
'''
 dest,work=m.setup(name,{'case.yaml':body});path=work/'case.yaml';client=m.LSP(dest,work)
 try:
  client.open(path,body);r=client.request('textDocument/definition',{'textDocument':{'uri':path.as_uri()},'position':m.pos(body,'steps.producer',offset=7)});got=r.get('result',[]);m.result(name,{'noProtocolError':'error' not in r,'visibility':got==[] if same_group else len(got)==1 and got[0]['range']['start']==m.pos(body,'name: producer',offset=6)},{'response':r})
 finally:client.close()
callee='apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: {name: library, namespace: argo}\nspec:\n  templates:\n    - name: worker\n      container: {image: alpine}\n'
caller=head+'  templates:\n    - name: main\n      dag:\n        tasks:\n          - name: call\n            templateRef: {name: library, template: worker}\n'
for name,other,extra,config,want in [
 ('external-known',callee,{},m.CONFIG,True),
 ('external-namespace',callee.replace('namespace: argo','namespace: different'),{},m.CONFIG,False),
 ('external-ambiguous',callee,{'duplicate.yaml':callee},m.CONFIG,False),
 ('external-scope',callee,{},m.CONFIG+'scopes:\n  - name: caller\n    include: ["caller.yaml"]\n  - name: library\n    include: ["library.yaml"]\n',False),
 ('external-cluster',callee.replace('WorkflowTemplate','ClusterWorkflowTemplate').replace('namespace: argo','namespace: ignored'),{},m.CONFIG,True),
 ('external-dynamic',callee,{},m.CONFIG,False),
]:
 text=caller
 if name=='external-cluster':text=text.replace('template: worker}', 'template: worker, clusterScope: true}')
 if name=='external-dynamic':text=text.replace('name: library','name: "{{workflow.name}}"')
 dest,work=m.setup(name,{'caller.yaml':text,'library.yaml':other,**extra},config);path=work/'caller.yaml';client=m.LSP(dest,work)
 try:
  client.open(path,text);r=client.request('textDocument/definition',{'textDocument':{'uri':path.as_uri()},'position':m.pos(text,'template: worker',offset=12)});got=r.get('result',[])
  start=m.pos(other,'name: worker',offset=6);end=dict(start);end['character']+=6
  expected=[{'uri':(work/f).as_uri(),'range':{'start':start,'end':end}} for f in (['duplicate.yaml','library.yaml'] if name=='external-ambiguous' else ['library.yaml'] if want else [])]
  m.result(name,{'noProtocolError':'error' not in r,'isolation':got==expected},{'response':r,'expected':expected})
 finally:client.close()
# Escaped expression spellings are not exact editable/member locations.
text=head+"  templates:\n    - name: main\n      dag:\n        tasks:\n          - name: produce\n            template: worker\n          - name: consume\n            dependencies: [produce]\n            template: worker\n            arguments:\n              parameters:\n                - name: message\n                  value: |\n                    {{= tasks['pro\\u0064uce'].outputs.result }}\n    - name: worker\n      container: {image: alpine}\n"
dest,work=m.setup('encoded-task-member',{'case.yaml':text});path=work/'case.yaml';client=m.LSP(dest,work)
try:
 client.open(path,text);r=client.request('textDocument/definition',{'textDocument':{'uri':path.as_uri()},'position':m.pos(text,"tasks['pro",offset=8)});m.result('encoded-task-member',{'exactSourceRequired':r.get('result')==[]},{'response':r})
finally:client.close()
# A second registered root is a separate reference universe.
dest,work=m.setup('external-root',{'caller.yaml':caller});other=dest/'other-root';other.mkdir();(other/'library.yaml').write_text(callee);(other/'.argo-workflows-ls.yaml').write_text(m.CONFIG);path=work/'caller.yaml';client=m.LSP(dest,work)
try:
 client.send('workspace/didChangeWorkspaceFolders',{'event':{'added':[{'uri':other.as_uri(),'name':'other'}],'removed':[]}});client.open(path,caller);r=client.request('textDocument/definition',{'textDocument':{'uri':path.as_uri()},'position':m.pos(caller,'template: worker',offset=12)});m.result('external-root',{'isolated':r.get('result')==[]},{'response':r})
finally:client.close()
m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'helperSha256':m.sha(root/'tests/e2e/wf-audit-gaps.py'),'inputHashes':{str(p.relative_to(m.OUT)):m.sha(p) for p in m.OUT.rglob('*.yaml')}})
sys.exit(0 if len(m.RESULTS)==19 and all(x['pass'] for x in m.RESULTS) else 1)

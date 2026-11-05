#!/usr/bin/env python3
"""Independent CLI/LSP expectations for projected reference discovery and origins."""
from pathlib import Path
import importlib.util,json,re,sys,traceback
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('wire',HERE/'wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
# Expr v1.16.9 receives the source after '=' with both surrounding spaces.
# Pin the parser reason independently, before any CLI/LSP process is started.
INVALID_REFERENCE_REASON=('invalid expression: unexpected token EOF (1:28)\n'
 ' |  inputs.parameters.absent + \n'
 ' | ...........................^')
LSP_SEVERITY={'Error':1,'Warning':2,'Information':3}
HEAD='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata:\n  name: references\n  annotations:\n    marker: &marker kept\n    markerUse: *marker\n'
SIBLING='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: exact-sibling}\nspec:\n  templates:\n    - name: main\n      inputs: {parameters: [{name: [[decl|exact]], default: retained}]}\n      container: {image: alpine, args: ["{{inputs.parameters.[[use|exact]]}}"]}\n'
CASES=[]
def add(name,text,diags=(),invalid=False,policy='closed'):CASES.append((name,text,list(diags),invalid,policy))
def error(tag,message,code='WF_REFERENCE_SCOPE',origin='derived'):return (tag,code,message,origin)
def main(args='["{{inputs.parameters.absent}}"]',extra=''):
 return '{name: main, '+extra+'container: {image: alpine, args: '+args+'}}'
# Container projection: the original exact string is semantically reached through a host.
for kind in ['Workflow','CronWorkflow']:
 payload='{templates: ['+main()+']}'
 text=HEAD.replace('kind: Workflow','kind: '+kind)+'spec:\n'
 if kind=='CronWorkflow':text+='  schedule: "* * * * *"\n  workflowSpec: {<<: {templates: &templates ['+main()+']}, templates: [[bad|*templates]]}\n'
 else:text+='  <<: &spec '+payload+'\n  templates: [[bad|*templates]]\n';text=text.replace('templates: ['+main()+']','templates: &templates ['+main()+']',1)
 add('projected-template-list-'+kind,text,[error('bad','input is not declared: inputs.parameters.absent')])
text=HEAD+'<<: {spec: &spec {templates: ['+main()+']}}\nspec: [[bad|*spec]]\n'
add('aliased-spec',text,[error('bad','input is not declared: inputs.parameters.absent')])
for missing in [False,True]:
 name='missing' if missing else 'available';text=HEAD+'spec:\n  <<: {arguments: {parameters: [{name: available, value: retained}]}}\n  templates:\n    - name: main\n      container: {image: alpine, args: [[[bad|"{{workflow.parameters.'+name+'}}"]]]}\n'
 add('merged-workflow-arguments-'+name,text,[error('bad','workflow parameter is not declared: missing',origin='ambiguous')] if missing else [])
# An explicit override removes the inherited parameter; do not use merged defaults after override.
text=HEAD+'spec:\n  <<: {arguments: {parameters: [{name: available, value: retained}]}}\n  arguments: {parameters: []}\n  templates:\n    - name: main\n      container: {image: alpine, args: [[[bad|"{{workflow.parameters.available}}"]]]}\n'
add('argument-override-removes-name',text,[error('bad','workflow parameter is not declared: available',origin='ambiguous')])
# Alias task lists must retain dependency ancestry; peer Steps remain invisible.
WORKER='{name: worker, outputs: {parameters: [{name: result, value: retained}]}, container: {image: alpine}}'
for dependent in [True,False]:
 tasks='[{name: first, template: worker}, {name: middle, template: worker, '+('depends: first.Succeeded' if dependent else 'dependencies: [first]')+'}, {name: last, template: worker, '+('depends: middle.Succeeded, ' if dependent else '')+'arguments: {parameters: [{name: ignored, value: "{{tasks.first.outputs.parameters.result}}"}]}}]'
 text=HEAD+'spec:\n  templates:\n    - name: main\n      dag: {<<: {tasks: &tasks '+tasks+'}, tasks: [[bad|*tasks]]}\n    - '+WORKER+'\n'
 add('dag-ancestor-'+('visible' if dependent else 'absent'),text,[] if dependent else [error('bad','tasks.first is not visible in this context')])
for prior in [True,False]:
 steps='[[{name: first, template: worker}'+('], [' if prior else ', ')+'{name: second, template: worker, arguments: {parameters: [{name: ignored, value: "{{steps.first.outputs.parameters.result}}"}]}}]]'
 text=HEAD+'spec:\n  templates:\n    - name: main\n      <<: {steps: &steps '+steps+'}\n      steps: [[bad|*steps]]\n    - '+WORKER+'\n'
 add('steps-'+('prior-group' if prior else 'same-group'),text,[] if prior else [error('bad','steps.first is not visible in this context')])
def output_case(callee,reference='parameters.result',header=HEAD):
 return header+'spec:\n  templates:\n    - name: main\n      dag:\n        tasks:\n          - {name: first, template: worker}\n          - name: second\n            template: sink\n            dependencies: [first]\n            arguments: {parameters: [{name: ignored, value: [[bad|"{{tasks.first.outputs.'+reference+'}}"]]}]}\n    - '+callee+'\n    - {name: sink, container: {image: alpine}}\n'
add('aliased-output-set',output_case('{name: worker, <<: {outputs: &outs {parameters: [{name: result, value: retained}]}}, outputs: *outs, container: {image: alpine}}'))
add('merged-result-container',output_case('{name: worker, <<: {container: {image: alpine}}}','result'))
namehead=HEAD+'    workerName: &workerName worker\n'
add('alias-callee-name-missing-output',output_case('{name: *workerName, container: {image: alpine}}',header=namehead),[error('bad','callee does not declare output parameters.result','WF_OUTPUT_MISSING','ambiguous')])
for shape,name in [('encoded','"wor\\u006ber"'),('alias','*workerName')]:
 callee=WORKER+'\n    - {name: [[duplicate|'+name+']], container: {image: alpine}}'
 add('ambiguous-callee-'+shape,output_case(callee,header=namehead),[error('duplicate','duplicate local template worker','WF_DUPLICATE_SYMBOL','derived' if shape=='alias' else 'exact')])
# Ambiguous call identities must not select the last declaration's callee.
for shape in ['dag','steps']:
 if shape=='dag':
  calls='dag:\n        tasks:\n          - {name: repeated, template: worker}\n          - {name: [[duplicate|repeated]], template: sink}\n          - name: user\n            template: sink\n            dependencies: [repeated]\n            arguments: {parameters: [{name: ignored, value: "{{tasks.repeated.outputs.parameters.result}}"}]}\n'
  message='duplicate DAG task repeated'
 else:
  calls='steps:\n        - [{name: repeated, template: worker}]\n        - [{name: [[duplicate|repeated]], template: sink}]\n        - [{name: user, template: sink, arguments: {parameters: [{name: ignored, value: "{{steps.repeated.outputs.parameters.result}}"}]}}]\n'
  message='duplicate parallel step repeated'
 text=HEAD+'spec:\n  templates:\n    - name: main\n      '+calls+'    - '+WORKER+'\n    - {name: sink, container: {image: alpine}}\n'
 add('ambiguous-'+shape+'-call',text,[error('duplicate',message,'WF_DUPLICATE_SYMBOL','exact')])

# A separately proven dependency is not weakened by an ambiguous alternate path.
for order in ['first, repeated','repeated, first']:
 for extra in [False,True]:
  name='direct-ancestor-'+('first' if order.startswith('first') else 'last')+'-'+('alternate' if extra else 'control')
  text=HEAD+'spec:\n  templates:\n    - name: main\n      dag:\n        tasks:\n          - {name: first, template: worker}\n          - {name: repeated, template: worker'+(', dependencies: [first]' if extra else '')+'}\n          - {name: [[duplicate|repeated]], template: worker}\n          - name: user\n            template: sink\n            dependencies: ['+order+']\n            arguments: {parameters: [{name: message, value: [[bad|"{{tasks.first.outputs.parameters.result}}"]]}]}\n    - {name: worker, container: {image: alpine}}\n    - {name: sink, inputs: {parameters: [{name: message}]}, container: {image: alpine}}\n'
  add(name,text,[error('duplicate','duplicate DAG task repeated','WF_DUPLICATE_SYMBOL','exact'),error('bad','callee does not declare output parameters.result','WF_OUTPUT_MISSING','ambiguous')])

# A scalar anchor is not its use site's input environment. Parsing remains lexical.
for name,expr,want,invalid in [
 ('simple','{{inputs.parameters.absent}}',[error('bad','input is not declared: inputs.parameters.absent')],False),
 ('bracket',"{{= inputs.artifacts['absent'].path }}",[error('bad','input is not declared: inputs.artifacts.absent')],False),
 ('let-shadow',"{{= let inputs = {parameters: {absent: 'local'} }; inputs.parameters.absent }}",[],False),
 ('env',"{{= $env.inputs.parameters.absent }}",[],False),
 ('dynamic',"{{= inputs.parameters[workflow.parameters.selector] }}",[error('bad','dynamic member access is not statically resolved','WF_DYNAMIC_REFERENCE')],False),
 ('invalid',"{{= inputs.parameters.absent + }}",[error('bad','Reference analysis incomplete: '+INVALID_REFERENCE_REASON,'WF_REFERENCE_INCOMPLETE')],True)]:
 text=HEAD+'    expression: &expression '+json.dumps(expr)+'\nspec:\n  arguments: {parameters: [{name: selector, value: ignored}]}\n  templates:\n    - name: main\n      container: {image: alpine, args: [[[bad|*expression]]]}\n'
 add('alias-scalar-'+name,text,want,invalid)
# The anchor is valid in one template, but the alias use has a different local input scope.
text=HEAD+'spec:\n  templates:\n    - name: owner\n      inputs: {parameters: [{name: available, default: retained}]}\n      container: {image: alpine, args: [&expression "{{inputs.parameters.available}}"]}\n    - name: user\n      container: {image: alpine, args: [[[bad|*expression]]]}\n'
add('alias-use-site-scope',text,[error('bad','input is not declared: inputs.parameters.available')])
# Presence is projected, but this phase does not infer inherited workflow arguments.
text=HEAD+'spec: [[bad|{<<: {workflowTemplateRef: {name: missing-library}}, templates: ['+main('["{{workflow.parameters.inherited}}"]')+']}]]\n'
add('projected-inheritance-presence-only',text,[error('bad','external template resource is not resolved: missing-library','WF_TEMPLATE_REF_UNRESOLVED'),error('templates','local templates cannot be combined with workflowTemplateRef','WF_TEMPLATE_REF_TEMPLATES','exact')])
text=HEAD+'spec:\n  templates:\n    - name: owner\n      container: {image: alpine, args: [&expression [[bad|"{{inputs.parameters.available}}"]]]}\n    - name: user\n      inputs: {parameters: [{name: available, default: retained}]}\n      container: {image: alpine, args: [*expression]}\n'
add('shared-anchor-use-scope',text,[error('bad','input is not declared: inputs.parameters.available')])
# The alias offset follows a supplementary-plane character on the same CRLF line.
text=HEAD+'    expression: &expression "{{inputs.parameters.absent}}"\nspec:\n  templates:\n    - name: main\n      container: {image: alpine, args: ["\U0001f600", [[bad|*expression]]]}\n'
add('alias-range-crlf-nonbmp',text.replace('\n','\r\n'),[error('bad','input is not declared: inputs.parameters.absent')])


def strip(text):
 plain='';end=0;spans={}
 for hit in re.finditer(r'\[\[(\w+)\|(.*?)\]\]',text,re.S):
  plain+=text[end:hit.start()];a=len(plain);plain+=hit[2];spans[hit[1]]=(a,len(plain));end=hit.end()
 return plain+text[end:],spans
def pos(text,index):
 head=text[:index];return {'line':head.count('\n'),'character':len(head.rsplit('\n',1)[-1].encode('utf-16-le'))//2}
def norm(rows):return sorted(json.dumps(x,sort_keys=True) for x in rows)
class Client(m.LSP):
 def send(self,method,params=None,ident=None):
  value={'jsonrpc':'2.0','method':method}
  if params is not None:value['params']=params
  if ident is not None:value['id']=ident
  data=json.dumps(value).encode()
  with (self.dest/'lsp.stdin.raw').open('ab') as f:f.write(('Content-Length: %d\r\n\r\n'%len(data)).encode()+data)
  return super().send(method,params,ident)
if len(sys.argv)>3: CASES=[c for c in CASES if c[0] in sys.argv[3].split(',')]
prepared=[]
for name,marked,diags,invalid,policy in CASES:
 text,tags=strip(marked)
 if name=='projected-inheritance-presence-only':
  sequence='['+main('["{{workflow.parameters.inherited}}"]')+']';a=text.index(sequence);tags['templates']=(a,a+len(sequence))
 sibling,stags=strip(SIBLING);dest,work=m.setup(name,{'case.yaml':text,'sibling.yaml':sibling},m.CONFIG.replace('referencePolicy: closed','referencePolicy: '+policy));path=work/'case.yaml';sp=work/'sibling.yaml'
 def loc(p,t,spans,tag):
  a,z=spans[tag];return {'uri':p.as_uri(),'range':{'start':pos(t,a),'end':pos(t,z)}}
 expected=[loc(path,text,tags,tag)|{'code':code,'message':message,'severity':'Information' if code in ['WF_DYNAMIC_REFERENCE','WF_TEMPLATE_REF_UNRESOLVED'] else 'Warning' if code=='WF_REFERENCE_INCOMPLETE' else 'Error','source':'argo-workflows','positionKind':origin} for tag,code,message,origin in diags]
 # Without --warnings-as-errors, only Error diagnostics set exit 1; incomplete
 # analysis takes precedence and exits 2 even when its visible diagnostic warns.
 expected_exit=2 if invalid else int(any(d['severity']=='Error' for d in expected))
 notes=[INVALID_REFERENCE_REASON] if invalid else []
 publication=[{k:d[k] for k in ['range','code','message','source']}|{'severity':LSP_SEVERITY[d['severity']]} for d in expected]
 if invalid:
  publication.append({'range':{'start':{'line':0,'character':0},'end':{'line':0,'character':0}},'code':'WF_ANALYSIS_INCOMPLETE','source':'argo-workflows','severity':2,'message':'Analysis did not complete: '+INVALID_REFERENCE_REASON+'. Correct the input or configuration to retry.'})
 decl=loc(sp,sibling,stags,'decl');use=loc(sp,sibling,stags,'use')
 contract={'diagnostics':expected,'publication':publication,'exitCode':expected_exit,'filesAnalyzed':1 if invalid else 2,'resourcesAnalyzed':1 if invalid else 2,'analysisComplete':not invalid,'configurationErrors':[],'notEvaluated':notes,'exactSiblingDefinition':[decl],'exactSiblingReferencesFalse':[use],'exactSiblingReferencesTrue':[use,decl],'aliasRename':'refused','noSourceEdits':True}
 m.save(dest/'expected.json',contract);(dest/'original.yaml').write_bytes(text.encode());prepared.append((name,dest,work,path,sp,text,sibling,contract,decl,use))
m.save(m.OUT/'manifest.json',{'command':sys.argv,'nativeSha256':m.sha(m.BINARY),'testSha256':m.sha(Path(__file__)),'caseCount':len(prepared),'inputs':{str(p.relative_to(m.OUT)):m.sha(p) for p in m.OUT.glob('*/original.yaml')}})
for name,dest,work,path,sp,text,sibling,contract,decl,use in prepared:
 client=None;checks={};responses=[]
 try:
  code,report=m.cli(dest,work);keys=['uri','range','code','message','severity','source','positionKind'];checks['fullDiagnostics']=norm([{k:d[k] for k in keys} for d in report['diagnostics']])==norm(contract['diagnostics']);checks['envelope']=code==contract['exitCode'] and report['analysisComplete'] is contract['analysisComplete'] and all(report[k]==contract[k] for k in ['filesAnalyzed','resourcesAnalyzed','configurationErrors','notEvaluated'])
  client=Client(dest,work);pub=client.open(path,text);checks['publication']=norm(pub)==norm(contract['publication']);client.open(sp,sibling)
  q={'textDocument':{'uri':sp.as_uri()},'position':use['range']['start']};response=client.request('textDocument/definition',q);responses.append(response);checks['exactSiblingDefinition']=response.get('result')==[decl]
  for inc in [False,True]:
   response=client.request('textDocument/references',q|{'context':{'includeDeclaration':inc}});responses.append(response);checks['exactSiblingReferences'+str(inc)]=norm(response.get('result',[]))==norm([use]+([decl] if inc else [])) and 'error' not in response
  response=client.request('textDocument/rename',{'textDocument':{'uri':path.as_uri()},'position':m.pos(text,'name: main',offset=6) if 'name: main' in text else m.pos(text,'name: owner',offset=6),'newName':'renamed'});responses.append(response);checks['aliasRenameRefused']=response.get('error',{}).get('code')==-32602 and 'result' not in response;checks['unchangedBytes']=path.read_bytes()==text.encode() and sp.read_bytes()==sibling.encode();m.result(name,checks,{'cli':report,'publication':pub,'responses':responses})
 except Exception:m.result(name,checks|{'completed':False},{'error':traceback.format_exc(),'responses':responses})
 finally:
  if client:client.close()
sys.exit(0 if len(m.RESULTS)==len(prepared) and all(x['pass'] for x in m.RESULTS) else 1)

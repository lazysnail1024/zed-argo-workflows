#!/usr/bin/env python3
"""Independent semantic YAML ownership, values and exact-origin CLI/LSP cases."""
from pathlib import Path
import sys,json,re,importlib.util,traceback
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('wire',HERE/'wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Client(m.LSP):
 def send(self,method,params=None,ident=None):
  value={'jsonrpc':'2.0','method':method}
  if params is not None:value['params']=params
  if ident is not None:value['id']=ident
  data=json.dumps(value).encode()
  with (self.dest/'lsp.stdin.raw').open('ab') as f:f.write(('Content-Length: %d\r\n\r\n'%len(data)).encode()+data)
  return super().send(method,params,ident)
def strip(text):
 plain='';end=0;spans={}
 for match in re.finditer(r'\[\[(\w+)\|(.*?)\]\]',text,re.S):
  plain+=text[end:match.start()];a=len(plain);plain+=match[2];spans[match[1]]=(a,len(plain));end=match.end()
 return plain+text[end:],spans
def pos(text,offset):
 head=text[:offset];return {'line':head.count('\n'),'character':len(head.rsplit('\n',1)[-1].encode('utf-16-le'))//2}
def rng(text,span):return {'start':pos(text,span[0]),'end':pos(text,span[1])}
def norm(items):return sorted(json.dumps(x,sort_keys=True) for x in items)
def unique(use,decl):
 return [('definition',use,[decl],None)]+[('references',site,[use]+([decl] if inc else []),inc) for site in [use,decl] for inc in [False,True]]
def absent(use):return [('definition',use,[],None)]+[('references',use,[],inc) for inc in [False,True]]
HEAD='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: semantic, namespace: argo}\nspec:\n'
END='  templates:\n    - name: [[main|main]]\n      container: {image: alpine}\n    - name: [[other|other]]\n      container: {image: alpine}\n'
CASES=[]
def add(name,text,diagnostics=(),queries=(),resources=1,rename=None):CASES.append((name,text,list(diagnostics),list(queries),resources,rename))
# Whole-document order is legal YAML; anchors are declared before both GVK uses.
for kind in ['Workflow','WorkflowTemplate','ClusterWorkflowTemplate']:
 text='metadata: {name: semantic, namespace: argo, annotations: {api: &api argoproj.io/v1alpha1, kind: &kind '+kind+'}}\napiVersion: *api\nkind: *kind\nspec:\n  parallelism: [[bad|wrong]]\n  entrypoint: [[entry|main]]\n'+END
 add('aliased-gvk-'+kind,text,[('bad','SCHEMA_TYPE','exact')],unique('entry','main'))
cron='metadata: {name: semantic, annotations: {api: &api argoproj.io/v1alpha1, kind: &kind CronWorkflow}}\napiVersion: *api\nkind: *kind\nspec:\n  schedule: "* * * * *"\n  workflowSpec:\n    parallelism: [[bad|wrong]]\n    entrypoint: [[entry|main]]\n    templates: [{name: [[main|main]], container: {image: alpine}}]\n'
add('aliased-gvk-CronWorkflow',cron,[('bad','SCHEMA_TYPE','exact')],unique('entry','main'))
merged='<<: {apiVersion: argoproj.io/v1alpha1, kind: Workflow}\nmetadata: {name: semantic}\nspec:\n  parallelism: [[bad|wrong]]\n  entrypoint: [[entry|main]]\n'+END
add('merged-gvk',merged,[('bad','SCHEMA_TYPE','exact')],unique('entry','main'))
foreign='<<: {apiVersion: argoproj.io/v1alpha1, kind: Workflow}\napiVersion: v1\nkind: ConfigMap\nmetadata: {name: foreign}\ndata: {entrypoint: [[entry|main]]}\n'
add('explicit-foreign-override',foreign,queries=absent('entry'),resources=0)
crlf='metadata: {name: semantic, annotations: {api: &api argoproj.io/v1alpha1, kind: &kind Workflow}}\napiVersion: *api\nkind: *kind\nspec:\n  entrypoint: [[entry|main]]\n  templates: [{container: {image: "\U0001f680"}, name: [[main|main]]}]\n'
add('crlf-non-bmp-exact-sibling',crlf.replace('\n','\r\n'),queries=unique('entry','main'))
# A merged inputs map is semantically present even without a direct CST child.
base=HEAD+'  entrypoint: [[entry|main]]\n  templates:\n    - &first\n      name: base\n      inputs: {parameters: [{name: message}]}\n      container: {image: alpine}\n    - &second\n      name: fallback\n      inputs: {parameters: [{name: other, default: okay}]}\n      container: {image: alpine}\n    - <<: %s\n      name: [[main|main]]\n'
add('merged-required-input',base%'*first',[('entry','WF_ARGUMENT_MISSING','exact')],unique('entry','main'))
add('merge-sequence-first-wins',base%'[*first, *second]',[('entry','WF_ARGUMENT_MISSING','exact')],unique('entry','main'))
add('merge-sequence-reversed',base%'[*second, *first]',queries=unique('entry','main'))
add('explicit-inputs-override',base%'*first'+'      inputs: {parameters: [{name: own, default: okay}]}\n',queries=unique('entry','main'))
value=HEAD.replace('namespace: argo}', 'namespace: argo, annotations: {greeting: &greeting hello}}')+'  entrypoint: [[entry|main]]\n  arguments: {parameters: [{name: message, value: *greeting}]}\n  templates:\n    - name: [[main|main]]\n      inputs: {parameters: [{name: message}]}\n      container: {image: alpine}\n'
add('alias-argument-value',value,queries=unique('entry','main'))
inputs=HEAD+'  entrypoint: [[entry|main]]\n  templates:\n    - name: base\n      inputs: {parameters: &inputs [{name: message}]}\n      container: {image: alpine}\n    - name: [[main|main]]\n      inputs: {parameters: *inputs}\n      container: {image: alpine}\n'
add('aliased-input-sequence',inputs,[('entry','WF_ARGUMENT_MISSING','exact')],unique('entry','main'))
name=HEAD.replace('namespace: argo}', 'namespace: argo, annotations: {template: &template main}}')+'  entrypoint: [[entry|main]]\n  onExit: [[exit|other]]\n  templates:\n    - name: *template\n      container: {image: alpine}\n    - name: [[other|other]]\n      container: {image: alpine}\n'
add('derived-template-name-exact-sibling',name,queries=absent('entry')+unique('exit','other'),rename='exit')
duplicate=HEAD+'  entrypoint: [[entry|main]]\n  onExit: [[exit|other]]\n  templates:\n    - &template\n      name: [[main|main]]\n      container: {image: alpine}\n    - [[duplicate|*template]]\n    - name: [[other|other]]\n      container: {image: alpine}\n'
ambiguous=[('definition','entry',['main'],None)]+[('references',site,[],inc) for site in ['entry','main'] for inc in [False,True]]
shared=[('definition','entry',[],None),('definition','main',[],None)]+[('references',site,[],inc) for site in ['entry','main'] for inc in [False,True]]
add('aliased-template-duplicate',duplicate,[('duplicate','WF_DUPLICATE_SYMBOL','derived')],shared+unique('exit','other'),rename='exit')
unshared=duplicate.replace('    - name: [[other|other]]', '    - name: [[unshared|main]]\n      container: {image: alpine}\n    - name: [[other|other]]')
unshared_queries=[('definition','entry',['unshared'],None),('definition','main',[],None)]+[('references',site,[],inc) for site in ['entry','main','unshared'] for inc in [False,True]]
add('shared-and-unshared-exact-candidate',unshared,[('duplicate','WF_DUPLICATE_SYMBOL','derived'),('unshared','WF_DUPLICATE_SYMBOL','exact')],unshared_queries+unique('exit','other'),rename='exit')
encoded=duplicate.replace('    - [[duplicate|*template]]','    - name: [[duplicate|"ma\\u0069n"]]\n      container: {image: alpine}')
add('encoded-template-duplicate-control',encoded,[('duplicate','WF_DUPLICATE_SYMBOL','exact')],ambiguous+unique('exit','other'))
BASE_UNKNOWN='complete domain rules, version acceptance, and resource budgets are not yet implemented'
for policy in ['closed','open']:
 for name,marked,diagnostics,queries,resources,rename in CASES:
  case=policy+'-'+name;text,spans=strip(marked);dest,work=m.setup(case,{'case.yaml':text},m.CONFIG.replace('referencePolicy: closed','referencePolicy: '+policy));path=work/'case.yaml';loc=lambda tag:{'uri':path.as_uri(),'range':rng(text,spans[tag])}
  expected_ds=[{'uri':path.as_uri(),'range':rng(text,spans[tag]),'code':code,'severity':'Error','source':'argo-workflows','positionKind':origin} for tag,code,origin in diagnostics]
  expected={'diagnostics':expected_ds,'queries':[{'method':method,'at':loc(at),'locations':[loc(x) for x in targets],'includeDeclaration':inc} for method,at,targets,inc in queries],'filesAnalyzed':1,'resourcesAnalyzed':resources,'analysisComplete':True,'exitCode':int(any(d['severity']=='Error' for d in expected_ds)),'notEvaluated':[]};m.save(dest/'expected.json',expected);client=None;responses=[];checks={}
  try:
   code,report=m.cli(dest,work);checks['completeCLI']=norm([{k:d[k] for k in ['uri','range','code','severity','source','positionKind']} for d in report['diagnostics']])==norm(expected_ds);checks['countsExit']=code==int(any(d['severity']=='Error' for d in expected_ds)) and report['filesAnalyzed']==1 and report['resourcesAnalyzed']==resources and report['analysisComplete'] is True and report['configurationErrors']==[];checks['unknownEnvelope']=report['notEvaluated']==[]
   client=Client(dest,work);ds=client.open(path,text);expected_lsp=[{k:d[k] for k in ['range','code','source']}|{'severity':1} for d in expected_ds];checks['completePublication']=norm([{k:d[k] for k in ['range','code','source','severity']} for d in ds])==norm(expected_lsp)
   for i,q in enumerate(expected['queries']):
    params={'textDocument':{'uri':path.as_uri()},'position':q['at']['range']['start']}
    if q['includeDeclaration'] is not None:params['context']={'includeDeclaration':q['includeDeclaration']}
    response=client.request('textDocument/'+q['method'],params);responses.append(response);checks['query'+str(i)]='error' not in response and norm(response.get('result',[]))==norm(q['locations'])
   if rename:
    response=client.request('textDocument/rename',{'textDocument':{'uri':path.as_uri()},'position':loc(rename)['range']['start'],'newName':'replacement'});responses.append(response);checks['closedRenameStillRefused']=response.get('error',{}).get('code')==-32602 and 'result' not in response
   checks['diskBytesUnchanged']=path.read_bytes()==text.encode();m.result(case,checks,{'responses':responses,'cli':report,'publication':ds})
  except Exception:
   (dest/'error.txt').write_text(traceback.format_exc());m.result(case,checks|{'completed':False},{'responses':responses})
  finally:
   if client:client.close()
# External semantic ownership must retain roles even when their declaration has
# no exact navigable origin. Resource/template names outside the shared anchors
# remain independently exact, as do the caller's workflow-parameter uses.
for policy in ['closed','open']:
 caller_marked='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: caller, namespace: argo, annotations: {use: "{{workflow.parameters.[[wfUse|message]]}}"}}\nspec:\n  workflowTemplateRef: {name: [[ref|library]]}\n  entrypoint: [[entry|worker]]\n  arguments: {parameters: [{name: [[overlap|message]], value: hello}]}\n'
 library_marked='metadata: {name: [[library|library]], namespace: argo, annotations: {api: &api argoproj.io/v1alpha1, kind: &kind WorkflowTemplate, input: &input message}}\napiVersion: *api\nkind: *kind\nspec:\n  templates:\n    - name: [[worker|worker]]\n      inputs: {parameters: [{name: *input, default: hello}]}\n      container: {image: alpine, args: ["{{inputs.parameters.[[inputUse|message]]}}"]}\n'
 caller,cs=strip(caller_marked);library,ls=strip(library_marked);case=policy+'-external-semantic-ownership';dest,work=m.setup(case,{'caller.yaml':caller,'library.yaml':library},m.CONFIG.replace('referencePolicy: closed','referencePolicy: '+policy));cp=work/'caller.yaml';lp=work/'library.yaml';locations={tag:{'uri':cp.as_uri(),'range':rng(caller,span)} for tag,span in cs.items()};locations.update({tag:{'uri':lp.as_uri(),'range':rng(library,span)} for tag,span in ls.items()})
 queries=unique('ref','library')+unique('entry','worker')+[('definition','overlap',['overlap'],None)]+[('references','overlap',[],inc) for inc in [False,True]]+[('definition','wfUse',['overlap'],None)]+[('references','wfUse',['wfUse']+(['overlap'] if inc else []),inc) for inc in [False,True]]+absent('inputUse')
 expected={'diagnostics':[],'filesAnalyzed':2,'resourcesAnalyzed':2,'analysisComplete':True,'exitCode':0,'notEvaluated':[],'queries':[{'method':method,'at':locations[at],'locations':[locations[t] for t in targets],'includeDeclaration':inc} for method,at,targets,inc in queries]};m.save(dest/'expected.json',expected);client=None;checks={};responses=[]
 try:
  code,report=m.cli(dest,work);checks['completeCLI']=report['diagnostics']==[] and report['configurationErrors']==[];checks['countsExit']=code==0 and report['filesAnalyzed']==2 and report['resourcesAnalyzed']==2 and report['analysisComplete'] is True;checks['unknownEnvelope']=report['notEvaluated']==[];client=Client(dest,work);checks['completePublication']=client.open(cp,caller)==[]
  for i,q in enumerate(expected['queries']):
   params={'textDocument':{'uri':q['at']['uri']},'position':q['at']['range']['start']}
   if q['includeDeclaration'] is not None:params['context']={'includeDeclaration':q['includeDeclaration']}
   response=client.request('textDocument/'+q['method'],params);responses.append(response);checks['query'+str(i)]='error' not in response and norm(response.get('result',[]))==norm(q['locations'])
  response=client.request('textDocument/rename',{'textDocument':{'uri':cp.as_uri()},'position':locations['overlap']['range']['start'],'newName':'replacement'});responses.append(response);checks['overlapRenameRefused']=response.get('error',{}).get('code')==-32602 and 'result' not in response;checks['diskBytesUnchanged']=cp.read_bytes()==caller.encode() and lp.read_bytes()==library.encode();m.result(case,checks,{'responses':responses,'cli':report})
 except Exception:
  (dest/'error.txt').write_text(traceback.format_exc());m.result(case,checks|{'completed':False},{'responses':responses})
 finally:
  if client:client.close()
m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(Path(__file__)),'helperSha256':m.sha(HERE/'wf-audit-gaps.py'),'caseCount':len(m.RESULTS),'inputHashes':{str(p.relative_to(m.OUT)):m.sha(p) for p in m.OUT.rglob('*.yaml')},'expectedHashes':{str(p.relative_to(m.OUT)):m.sha(p) for p in m.OUT.rglob('expected.json')}})
sys.exit(0 if len(m.RESULTS)==36 and all(x['pass'] for x in m.RESULTS) else 1)

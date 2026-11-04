#!/usr/bin/env python3
"""Independent bounded missing-role and publication transitions; real CLI/LSP."""
from pathlib import Path
import sys,json,re,importlib.util,traceback
p=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('wire',p/'wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
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
 for x in re.finditer(r'\[\[(\w+)\|(.*?)\]\]',text,re.S):
  plain+=text[end:x.start()];a=len(plain);plain+=x[2];spans[x[1]]=(a,len(plain));end=x.end()
 return plain+text[end:],spans
def pos(text,offset):
 head=text[:offset];return {'line':head.count('\n'),'character':len(head.rsplit('\n',1)[-1].encode('utf-16-le'))//2}
def rng(text,span):return {'start':pos(text,span[0]),'end':pos(text,span[1])}
def location(path,text,spans,tag):return {'uri':path.as_uri(),'range':rng(text,spans[tag])}
def norm(v):return sorted(json.dumps(x,sort_keys=True) for x in v)
def request(client,loc,method,include=None):
 params={'textDocument':{'uri':loc['uri']},'position':loc['range']['start']}
 if include is not None:params['context']={'includeDeclaration':include}
 return client.request('textDocument/'+method,params)
HEAD='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: caller, namespace: argo, annotations: {note: "{{workflow.parameters.[[wfuse|message]]}}"}}\nspec:\n'
CALL=HEAD+'  workflowTemplateRef: {name: [[resource|library]]}\n  entrypoint: [[entry|main]]\n  arguments: {parameters: [{name: [[overlap|message]], value: hello}]}\n'
LIB='apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: {name: library, namespace: argo}\nspec:\n  templates:\n    - name: main\n      inputs: {parameters: [{name: [[input|message]], default: hello}]}\n      container: {image: alpine, args: ["{{inputs.parameters.[[inputuse|message]]}}"]}\n'
for name,kind in [('duplicate-external-resource','duplicate'),('known-zero-external-input','zero'),('known-missing-external-input','missing'),('duplicate-resource-zero-input','duplicate-zero'),('duplicate-resource-missing-input','duplicate-missing')]:
 caller,cs=strip(CALL);library,ls=strip(LIB)
 if kind in ['zero','missing','duplicate-zero','duplicate-missing']:
  library=library.replace('      inputs: {parameters: [{name: message, default: hello}]}\n','' if kind.endswith('zero') else '      inputs: {parameters: [{name: other, default: hello}]}\n').replace('container: {image: alpine, args: ["{{inputs.parameters.message}}"]}','container: {image: alpine}')
 files={'caller.yaml':caller,'library.yaml':library}
 if kind.startswith('duplicate'):files['duplicate-library.yaml']=library
 dest,work=m.setup(name,files);cp=work/'caller.yaml';overlap=location(cp,caller,cs,'overlap');wfuse=location(cp,caller,cs,'wfuse');resource=location(cp,caller,cs,'resource');ambiguous=kind=='duplicate'
 expected={'definition':[overlap]+([location(work/'library.yaml',library,ls,'input'),location(work/'duplicate-library.yaml',library,ls,'input')] if ambiguous else []),'overlapReferencesFalse':[] if ambiguous else [wfuse],'overlapReferencesTrue':[] if ambiguous else [wfuse,overlap],'workflowReferencesFalse':[wfuse],'workflowReferencesTrue':[wfuse,overlap],'diagnostics':[{'uri':cp.as_uri(),'range':resource['range'],'code':'WF_TEMPLATE_REF_AMBIGUOUS','severity':'Warning','source':'argo-workflows','positionKind':'exact'}] if kind.startswith('duplicate') else [],'filesAnalyzed':len(files),'resourcesAnalyzed':len(files),'analysisComplete':True,'exitCode':0};m.save(dest/'expected.json',expected)
 client=None;checks={};responses={}
 try:
  client=Client(dest,work);client.open(cp,caller)
  r=request(client,overlap,'definition');responses['definition']=r;checks['exactDefinition']='error' not in r and norm(r.get('result',[]))==norm(expected['definition'])
  for inc in [False,True]:
   for label,selected in [('overlap',overlap),('workflow',wfuse)]:
    r=request(client,selected,'references',inc);responses[label+str(inc)]=r;checks[label+'References'+str(inc)]='error' not in r and norm(r.get('result',[]))==norm(expected[label+'References'+str(inc)])
  code,report=m.cli(dest,work);checks['exactCLI']=norm([{k:d[k] for k in ['uri','range','code','severity','source','positionKind']} for d in report['diagnostics']])==norm(expected['diagnostics']);checks['countsExit']=code==0 and report['resourcesAnalyzed']==len(files) and report['filesAnalyzed']==len(files) and report['analysisComplete'] is True and not report['configurationErrors'];m.result(name,checks,{'responses':responses,'cli':report})
 except Exception:
  (dest/'error.txt').write_text(traceback.format_exc());m.result(name,checks|{'completed':False},{'responses':responses})
 finally:
  if client:client.close()
m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(Path(__file__)),'helperSha256':m.sha(p/'wf-audit-gaps.py'),'caseCount':len(m.RESULTS),'inputHashes':{str(x.relative_to(m.OUT)):m.sha(x) for x in m.OUT.rglob('*.yaml')},'expectedHashes':{str(x.relative_to(m.OUT)):m.sha(x) for x in m.OUT.rglob('expected.json')}})
sys.exit(0 if len(m.RESULTS)==5 and all(x['pass'] for x in m.RESULTS) else 1)

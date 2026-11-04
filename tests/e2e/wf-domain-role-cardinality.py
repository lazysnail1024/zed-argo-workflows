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
# Explicitly named external template candidates with concrete inputs cannot be
# discarded to promote the overlapping workflow argument to a unique identity.
for name,duplicate in [('duplicate-external-template','main'),('encoded-duplicate-external-template','"ma\\u0069n"')]:
 library=LIB+'    - name: [[duplicate|'+duplicate+']]\n      inputs: {parameters: [{name: [[secondInput|message]], default: second}]}\n      container: {image: alpine}\n';caller,cs=strip(CALL);library,ls=strip(library);dest,work=m.setup(name,{'caller.yaml':caller,'library.yaml':library});cp=work/'caller.yaml';lp=work/'library.yaml';overlap=location(cp,caller,cs,'overlap');wfuse=location(cp,caller,cs,'wfuse')
 expected={'definition':[overlap,location(lp,library,ls,'input'),location(lp,library,ls,'secondInput')],'overlapReferencesFalse':[],'overlapReferencesTrue':[],'workflowReferencesFalse':[wfuse],'workflowReferencesTrue':[wfuse,overlap],'diagnostics':[{'uri':lp.as_uri(),'range':rng(library,ls['duplicate']),'code':'WF_DUPLICATE_SYMBOL','severity':'Error','source':'argo-workflows','positionKind':'exact'}],'filesAnalyzed':2,'resourcesAnalyzed':2,'analysisComplete':True,'exitCode':1};m.save(dest/'expected.json',expected)
 client=None;checks={};responses={}
 try:
  client=Client(dest,work);client.open(cp,caller)
  r=request(client,overlap,'definition');responses['definition']=r;checks['exactDefinition']='error' not in r and norm(r.get('result',[]))==norm(expected['definition'])
  for inc in [False,True]:
   r=request(client,overlap,'references',inc);responses['overlap'+str(inc)]=r;checks['overlapReferences'+str(inc)]='error' not in r and r.get('result')==[]
   r=request(client,wfuse,'references',inc);responses['workflow'+str(inc)]=r;checks['workflowReferences'+str(inc)]='error' not in r and norm(r.get('result',[]))==norm(expected['workflowReferences'+str(inc)])
  code,report=m.cli(dest,work);checks['exactCLI']=norm([{k:d[k] for k in ['uri','range','code','severity','source','positionKind']} for d in report['diagnostics']])==norm(expected['diagnostics']);checks['countsExit']=code==1 and report['resourcesAnalyzed']==2 and report['filesAnalyzed']==2 and report['analysisComplete'] is True and not report['configurationErrors'];m.result(name,checks,{'responses':responses,'cli':report})
 except Exception:
  (dest/'error.txt').write_text(traceback.format_exc());m.result(name,checks|{'completed':False},{'responses':responses})
 finally:
  if client:client.close()
# Publication and query results must follow current selected scope/config buffers.
name='role-publication-transitions';config=m.CONFIG+'scopes:\n  - name: root\n    include: ["selected/*.yaml"]\n  - name: other\n    include: ["other/*.yaml"]\n';caller,cs=strip(CALL);library,ls=strip(LIB);badlib,bs=strip(LIB.replace('spec:\n','spec:\n  parallelism: [[bad|wrong]]\n'));noinput,ns=strip(LIB.replace('      inputs: {parameters: [{name: [[input|message]], default: hello}]}\n','').replace('container: {image: alpine, args: ["{{inputs.parameters.[[inputuse|message]]}}"]}','container: {image: alpine}'))
dest,work=m.setup(name,{'selected/caller.yaml':caller,'selected/library.yaml':library,'other/library.yaml':library},config);cp=work/'selected/caller.yaml';lp=work/'selected/library.yaml';cfg=work/'.argo-workflows-ls.yaml';overlap=location(cp,caller,cs,'overlap');wfuse=location(cp,caller,cs,'wfuse');resource=location(cp,caller,cs,'resource');overlay=config.replace('["selected/*.yaml"]','["selected/caller.yaml"]').replace('["other/*.yaml"]','["other/*.yaml", "selected/library.yaml"]')
expected={'initialLibraryDiagnostic':[{'range':rng(badlib,bs['bad']),'code':'SCHEMA_TYPE','severity':1,'source':'argo-workflows'}],'initialDefinitions':[overlap,location(lp,badlib,bs,'input')],'removedInputDefinitions':[overlap],'restoredDefinitions':[overlap,location(lp,library,ls,'input')],'configSeparatedDiagnostic':[{'range':resource['range'],'code':'WF_TEMPLATE_REF_UNRESOLVED','severity':1,'source':'argo-workflows'}],'configSeparatedDefinitions':[overlap],'finalCLI':{'diagnostics':[],'filesAnalyzed':3,'resourcesAnalyzed':3,'exitCode':0}};m.save(dest/'expected.json',expected)
for label,text in [('bad-library',badlib),('no-input-library',noinput),('restored-library',library),('overlay-config',overlay)]: (dest/(label+'.yaml')).write_bytes(text.encode())
def diag_norm(ds):return [{k:d[k] for k in ['range','code','severity','source']} for d in ds]
def publication(client,uri,version,start):
 def match(v):return v.get('method')=='textDocument/publishDiagnostics' and v['params']['uri']==uri and v['params'].get('version')==version
 for v in client.received[start:]:
  if match(v):return v['params']['diagnostics']
 return client.wait(match)['params']['diagnostics']
client=None;checks={};responses={}
def query_state(label,definitions,ambiguous):
 r=request(client,overlap,'definition');responses[label+'-definition']=r;checks[label+'-definition']='error' not in r and norm(r.get('result',[]))==norm(definitions)
 for inc in [False,True]:
  expectedrefs=[] if ambiguous else [wfuse]+([overlap] if inc else []);r=request(client,overlap,'references',inc);responses[label+'-refs'+str(inc)]=r;checks[label+'-refs'+str(inc)]='error' not in r and norm(r.get('result',[]))==norm(expectedrefs)
try:
 client=Client(dest,work);ds=client.open(cp,caller);checks['initialCallerClean']=ds==[];ds=client.open(lp,badlib);checks['initialLibraryFullDiagnostic']=diag_norm(ds)==expected['initialLibraryDiagnostic'];query_state('initial',expected['initialDefinitions'],True)
 start=len(client.received);ds=client.change(lp,noinput,2);checks['removedInputLibraryClean']=ds==[];checks['removedInputCallerPublished']=publication(client,cp.as_uri(),1,start)==[];query_state('removed-input',expected['removedInputDefinitions'],False)
 start=len(client.received);ds=client.change(lp,library,3);checks['restoredLibraryClean']=ds==[];checks['restoredCallerPublished']=publication(client,cp.as_uri(),1,start)==[];query_state('restored',expected['restoredDefinitions'],True)
 start=len(client.received);client.send('textDocument/didOpen',{'textDocument':{'uri':cfg.as_uri(),'version':1,'languageId':'yaml','text':overlay}});ds=publication(client,cp.as_uri(),1,start);checks['configSeparatedFullDiagnostic']=diag_norm(ds)==expected['configSeparatedDiagnostic'];query_state('separated-scope',expected['configSeparatedDefinitions'],False)
 start=len(client.received);client.send('textDocument/didClose',{'textDocument':{'uri':cfg.as_uri()}});ds=publication(client,cp.as_uri(),1,start);checks['diskConfigRestoredPublication']=ds==[];query_state('config-restored',expected['restoredDefinitions'],True)
 code,report=m.cli(dest,work);checks['diskUnchangedFullCLI']=report['diagnostics']==[] and report['filesAnalyzed']==3 and report['resourcesAnalyzed']==3 and not report['configurationErrors'] and report['analysisComplete'] is True and code==0
 m.result(name,checks,{'responses':responses,'cli':report})
except Exception:
 (dest/'error.txt').write_text(traceback.format_exc());m.result(name,checks|{'completed':False},{'responses':responses})
finally:
 if client:client.close()
m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(Path(__file__)),'helperSha256':m.sha(p/'wf-audit-gaps.py'),'caseCount':len(m.RESULTS),'inputHashes':{str(x.relative_to(m.OUT)):m.sha(x) for x in m.OUT.rglob('*.yaml')},'expectedHashes':{str(x.relative_to(m.OUT)):m.sha(x) for x in m.OUT.rglob('expected.json')}})
sys.exit(0 if len(m.RESULTS)==3 and all(x['pass'] for x in m.RESULTS) else 1)

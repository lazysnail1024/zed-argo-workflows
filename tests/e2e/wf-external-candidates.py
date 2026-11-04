#!/usr/bin/env python3
"""Exact ambiguous external navigation and unique-reference CLI/LSP oracles."""
from pathlib import Path
import sys, json, re, importlib.util, traceback
p=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('wire',p/'wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def strip(text):
 plain='';end=0;spans={}
 for x in re.finditer(r'\[\[(\w+)\|(.*?)\]\]',text,re.S):
  plain+=text[end:x.start()];a=len(plain);plain+=x[2];spans[x[1]]=(a,len(plain));end=x.end()
 return plain+text[end:],spans
def pos(text,offset):
 head=text[:offset];return {'line':head.count('\n'),'character':len(head.rsplit('\n',1)[-1].encode('utf-16-le'))//2}
def loc(path,text,spans,tag):
 a,b=spans[tag];return {'uri':path.as_uri(),'range':{'start':pos(text,a),'end':pos(text,b)}}
def norm(v):return sorted(json.dumps(x,sort_keys=True) for x in v)
def request(c,selection,method,inc=None):
 args={'textDocument':{'uri':selection['uri']},'position':selection['range']['start']}
 if inc is not None:args['context']={'includeDeclaration':inc}
 if method=='rename':args['newName']='renamed'
 return c.request('textDocument/'+method,args)
CALL='''apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata: {name: caller, namespace: argo, annotations: {note: "{{workflow.parameters.[[wfuse|message]]}}"}}
spec:
  workflowTemplateRef: {name: [[resource|library]]CLUSTER}
  entrypoint: [[entry|main]]
  arguments: {parameters: [{name: [[argument|message]], value: hello}]}
'''
TASK='''apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata: {name: caller, namespace: argo}
spec:
  entrypoint: run
  templates:
    - name: run
      dag:
        tasks:
          - name: invoke
            templateRef: {name: [[resource|library]], template: [[entry|main]]CLUSTER}
            arguments: {parameters: [{name: [[argument|message]], value: hello}]}
'''
TEMPLATE='''    - name: [[templateINDEX|NAME]]
      inputs: {parameters: [{name: [[inputINDEX|message]], default: hello}]}
      container: {image: alpine, args: ["{{inputs.parameters.[[useINDEX|message]]}}"]}
'''
def library(kind='WorkflowTemplate', encoded_resource=False, duplicate=False, encoded_template=False, namespace='argo'):
 name='"libra\\u0072y"' if encoded_resource else 'library'
 head='apiVersion: argoproj.io/v1alpha1\nkind: '+kind+'\nmetadata: {annotations: {note: "\U0001f680"}, name: [[declaration|'+name+']], namespace: '+namespace+'}\nspec:\n  templates:\n'
 head+=TEMPLATE.replace('INDEX','0').replace('NAME','main')
 if duplicate:head+=TEMPLATE.replace('INDEX','1').replace('NAME','"ma\\u0069n"' if encoded_template else 'main')
 return head
CASES=[
 ('resource-duplicates',False,'resource',False,False),
 ('cluster-resource-duplicates',True,'resource',False,False),
 ('template-duplicates',False,'template',False,False),
 ('encoded-template-duplicate',False,'template',True,False),
 ('encoded-resource-duplicate',False,'resource',True,False),
 ('task-resource-duplicates',False,'resource',False,True),
 ('task-template-duplicates',False,'template',False,True),
 ('crlf-cluster-task',True,'resource',False,True),
 ('unique-control',False,'unique',False,False),
 ('namespace-filter',False,'namespace',False,False),
 ('scope-filter',False,'scope',False,False),
]
for name,cluster,mode,encoded,task in CASES:
 marked={'caller.yaml':(TASK if task else CALL).replace('CLUSTER',', clusterScope: true' if cluster else ''), 'a-library.yaml':library('ClusterWorkflowTemplate' if cluster else 'WorkflowTemplate',duplicate=mode=='template',encoded_template=encoded)}
 if mode in ['resource','namespace','scope']:
  marked['b-library.yaml']=library('ClusterWorkflowTemplate' if cluster else 'WorkflowTemplate',encoded_resource=encoded,namespace='other' if mode=='namespace' else 'argo')
 if name.startswith('crlf'):marked={k:v.replace('\n','\r\n') for k,v in marked.items()}
 parsed={k:strip(v) for k,v in marked.items()};files={k:v[0] for k,v in parsed.items()}
 config=m.CONFIG
 if mode=='scope':config+='scopes:\n  - name: selected\n    include: [caller.yaml, a-library.yaml]\n  - name: other\n    include: [b-library.yaml]\n'
 dest,work=m.setup(name,files,config);L=lambda f,t:loc(work/f,*parsed[f],t)
 resource=L('caller.yaml','resource');entry=L('caller.yaml','entry');argument=L('caller.yaml','argument')
 candidates=['a-library.yaml']+(['b-library.yaml'] if mode=='resource' else [])
 definitions={'resource':[L(f,'declaration') for f in candidates if not(encoded and f=='b-library.yaml')], 'entry':[L(f,'template0') for f in candidates], 'argument':([] if task else [argument])+[L(f,'input0') for f in candidates]}
 if mode=='template':
  if not encoded:definitions['entry'].append(L('a-library.yaml','template1'))
  definitions['argument'].append(L('a-library.yaml','input1'))
 diagnostics=[]
 if mode=='resource':diagnostics.append(dict(resource,code='WF_TEMPLATE_REF_AMBIGUOUS',severity='Warning',source='argo-workflows',positionKind='exact'))
 if mode=='template':diagnostics.append(dict(L('a-library.yaml','template1'),code='WF_DUPLICATE_SYMBOL',severity='Error',source='argo-workflows',positionKind='exact'))
 ambiguous=mode in ['resource','template']
 queries={}
 for label,selection in [('resource',resource),('entry',entry),('argument',argument)]:
  queries[label]={'selection':selection,'definition':definitions[label]}
  for inc in [False,True]:
   if (ambiguous and not(label=='resource' and mode=='template')) or label=='argument' and not task:expected=[]
   else:expected=[selection]+(definitions[label] if inc else [])
   queries[label]['references'+str(inc)]=expected
 for f in candidates:
  for tag in ['declaration','template0','input0']+(['template1','input1'] if mode=='template' else []):
   if encoded and (tag=='template1' or f=='b-library.yaml' and tag=='declaration'):continue
   selection=L(f,tag);q={'selection':selection}
   for inc in [False,True]:
    if tag=='declaration':refs=[] if mode=='resource' else [resource]+([selection] if inc else [])
    elif tag.startswith('template'):
     refs=[] if mode=='template' else ([] if ambiguous else [entry])+([selection] if inc else [])
    else:refs=[L(f,'use'+tag[-1])]+([] if ambiguous else [argument])+([selection] if inc else [])
    q['references'+str(inc)]=refs
   queries[f+tag]=q
 expected={'queries':queries,'diagnostics':diagnostics,'filesAnalyzed':len(files),'resourcesAnalyzed':len(files),'analysisComplete':True,'exitCode':int(any(d['severity']=='Error' for d in diagnostics))};m.save(dest/'expected.json',expected)
 client=None;checks={};responses={}
 try:
  client=m.LSP(dest,work);client.open(work/'caller.yaml',files['caller.yaml'])
  for label,q in queries.items():
   for method in ['definition','referencesFalse','referencesTrue']:
    if method not in q:continue
    response=request(client,q['selection'],'definition' if method=='definition' else 'references',None if method=='definition' else method.endswith('True'));responses[label+method]=response
    checks[label+method]='error' not in response and norm(response.get('result',[]))==norm(q[method])
   response=request(client,q['selection'],'rename');responses[label+'rename']=response;checks[label+'renameRefused']='error' in response
  code,report=m.cli(dest,work);checks['completeDiagnostics']=norm([{k:d[k] for k in ['uri','range','code','severity','source','positionKind']} for d in report['diagnostics']])==norm(diagnostics);checks['countsExit']=code==int(any(d['severity']=='Error' for d in diagnostics)) and report['filesAnalyzed']==len(files) and report['resourcesAnalyzed']==len(files) and report['analysisComplete'] is True and not report['configurationErrors']
  m.result(name,checks,{'responses':responses,'cli':report})
 except Exception:
  (dest/'error.txt').write_text(traceback.format_exc());m.result(name,checks|{'completed':False},{'responses':responses})
 finally:
  if client:client.close()
m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(Path(__file__)),'helperSha256':m.sha(p/'wf-audit-gaps.py'),'caseCount':len(m.RESULTS),'inputHashes':{str(x.relative_to(m.OUT)):m.sha(x) for x in m.OUT.rglob('*.yaml')},'expectedHashes':{str(x.relative_to(m.OUT)):m.sha(x) for x in m.OUT.rglob('expected.json')}})
sys.exit(0 if len(m.RESULTS)==len(CASES) and all(x['pass'] for x in m.RESULTS) else 1)

#!/usr/bin/env python3
"""Focused independent real CLI/LSP audit; fixtures/expected files precede processes."""
import sys,json,re,hashlib,importlib.util,traceback
from pathlib import Path
BASE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('wire',BASE/'wf-audit-gaps.py'); m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Client(m.LSP):
 def send(self,method,params=None,ident=None):
  frame={'jsonrpc':'2.0','method':method}
  if params is not None:frame['params']=params
  if ident is not None:frame['id']=ident
  payload=json.dumps(frame).encode(); raw=('Content-Length: %d\r\n\r\n'%len(payload)).encode()+payload
  with (self.dest/'lsp.stdin.raw').open('ab') as f:f.write(raw)
  return super().send(method,params,ident)
def marked(text):
 spans={}; plain=''; end=0
 for hit in re.finditer(r'\[\[(\w+)\|(.*?)\]\]',text,re.S):
  plain+=text[end:hit.start()]; a=len(plain); plain+=hit[2]; spans[hit[1]]=(a,len(plain)); end=hit.end()
 return plain+text[end:],spans
def position(text,at):
 head=text[:at];return {'line':head.count('\n'),'character':len(head.rsplit('\n',1)[-1].encode('utf-16-le'))//2}
def rng(text,span):return {'start':position(text,span[0]),'end':position(text,span[1])}
def norm(v):return sorted(json.dumps(x,sort_keys=True) for x in v)
HEAD='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: audit, namespace: argo}\nspec:\n'
CASES=[]
def case(name,files,build,diagnostics=[],resources=None):
 data={}; spans={}
 for path,text in files.items():data[path],spans[path]=marked(text)
 dest,work=m.setup(name,data); uris={name:(work/name).as_uri() for name in data}
 def loc(file,tag):return {'uri':uris[file],'range':rng(data[file],spans[file][tag])}
 def query(file,tag,method,want,extra=None,end=False):
  p={'textDocument':{'uri':uris[file]},'position':position(data[file],spans[file][tag][1 if end else 0])}
  if extra:p.update(extra)
  return {'method':method,'params':p,'expected':want,'sort':method.endswith('/references') or method.endswith('/definition')}
 ops=build(loc,query,data,spans,uris)
 expected_diags=[{'uri':uris[f],'range':rng(data[f],spans[f][tag]),'code':code,'severity':'Error','source':'argo-workflows','positionKind':('ambiguous' if code=='WF_REFERENCE_SCOPE' else 'exact')} for f,tag,code in diagnostics]
 expected={'queries':ops,'diagnostics':expected_diags,'exitCode':int(bool(expected_diags)),'analysisComplete':True,'filesAnalyzed':len(data),'resourcesAnalyzed':resources if resources is not None else len(data),'configurationErrors':[]}
 for filename,text in data.items():(dest/('original-'+filename.replace('/','_'))).write_bytes(text.encode())
 m.save(dest/'expected.json',expected);CASES.append((name,dest,work,data,expected))
# Same spelling in separate categories remains separate through all owned GVKs.
body='''  arguments: {parameters: [{name: [[global|shared]], value: hello}]}
  templates:
    - name: [[main|main]]
      inputs:
        parameters: [{name: [[input|shared]], default: hello}]
        artifacts: [{name: [[artifact|shared]], path: /tmp/in, optional: true}]
      outputs:
        parameters: [{name: [[output|shared]], value: hello}]
        artifacts: [{name: [[outartifact|shared]], path: /tmp/out}]
      container:
        image: alpine
        args:
          - "{{workflow.parameters.[[globaluse|shared]]}}"
          - "{{inputs.parameters.[[inputuse|shared]]}}"
          - "{{inputs.artifacts.[[artifactuse|shared]].path}}"
          - "{{outputs.parameters.[[outputuse|shared]]}}"
          - "{{outputs.artifacts.[[outartifactuse|shared]].path}}"
'''
def categories(loc,q,data,spans,uris):
 ops=[];symbols=[]
 categories=[('global','workflow.parameters'),('main','template'),('input','main \u00b7 inputs.parameters'),('artifact','main \u00b7 inputs.artifacts'),('output','main \u00b7 outputs.parameters'),('outartifact','main \u00b7 outputs.artifacts')]
 for tag,detail in categories:
  name='main' if tag=='main' else 'shared'; kind=12 if tag=='main' else 13; l=loc('case.yaml',tag)
  symbols.append({'name':name,'kind':kind,'detail':detail,'range':l['range'],'selectionRange':l['range']})
  if tag=='main':continue
  use=tag+'use';ops.append(q('case.yaml',use,'textDocument/definition',[l]))
  for inc in [False,True]:ops.append(q('case.yaml',use,'textDocument/references',[loc('case.yaml',use)]+([l] if inc else []),{'context':{'includeDeclaration':inc}}))
 ops.append({'method':'textDocument/documentSymbol','params':{'textDocument':{'uri':uris['case.yaml']}},'expected':symbols})
 ops.append({'method':'workspace/symbol','params':{'query':'shared'},'expected':[{'name':x['name'],'kind':x['kind'],'containerName':x['detail'],'location':{'uri':uris['case.yaml'],'range':x['range']}} for x in symbols if x['name']=='shared'],'sort':True})
 return ops
for kind in ['Workflow','WorkflowTemplate','ClusterWorkflowTemplate','CronWorkflow']:
 head=HEAD.replace('kind: Workflow','kind: '+kind);text=head+body
 if kind=='CronWorkflow':text=head+'  schedule: "* * * * *"\n  workflowSpec:\n'+''.join('  '+line+'\n' for line in body.splitlines())
 if kind=='WorkflowTemplate':text=text.replace('\n','\r\n')
 case('categories-'+kind,{'case.yaml':text},categories)
# Non-string scalars never become fake declarations or corrupt the real quoted name.
text=HEAD+'''  templates:
    - name: [[main|main]]
      inputs:
        parameters:
          - {name: [[number|123]], default: bad}
          - {name: [[boolean|true]], default: bad}
          - {name: [[nil|null]], default: bad}
          - {name: "[[stringnum|123]]", default: good}
          - {name: "[[stringbool|true]]", default: good}
          - {name: "[[stringnil|null]]", default: good}
      container:
        image: alpine
        args: ["{{= inputs.parameters['[[use_num|123]]'] }}", "{{= inputs.parameters['[[use_bool|true]]'] }}", "{{= inputs.parameters['[[use_nil|null]]'] }}"]
'''
def nonstrings(loc,q,data,spans,uris):
 ops=[];symbols=[]
 for t,name in [('main','main'),('stringnum','123'),('stringbool','true'),('stringnil','null')]:
  l=loc('case.yaml',t);symbols.append({'name':name,'kind':12 if t=='main' else 13,'detail':'template' if t=='main' else 'main \u00b7 inputs.parameters','range':l['range'],'selectionRange':l['range']})
 for use,decl in [('use_num','stringnum'),('use_bool','stringbool'),('use_nil','stringnil')]:
  ops.append(q('case.yaml',use,'textDocument/definition',[loc('case.yaml',decl)]))
  for inc in [False,True]:ops.append(q('case.yaml',decl,'textDocument/references',[loc('case.yaml',use)]+([loc('case.yaml',decl)] if inc else []),{'context':{'includeDeclaration':inc}}))
 for t in ['number','boolean','nil']:ops.append(q('case.yaml',t,'textDocument/definition',[]))
 ops.append({'method':'textDocument/documentSymbol','params':{'textDocument':{'uri':uris['case.yaml']}},'expected':symbols})
 return ops
case('non-string-names',{'case.yaml':text},nonstrings,[('case.yaml',x,'SCHEMA_TYPE') for x in ['number','boolean','nil']])
# A known explicit external entrypoint makes one source name both declaration and argument.
caller=HEAD.replace('namespace: argo}', 'namespace: argo, annotations: {note: "{{workflow.parameters.[[wfuse|message]]}}"}}')+'''  workflowTemplateRef: {name: library}
  entrypoint: main
  arguments: {parameters: [{name: [[overlap|message]], value: hello}]}
'''
lib=HEAD.replace('kind: Workflow','kind: WorkflowTemplate').replace('name: audit','name: library')+'''  templates:
    - name: main
      inputs: {parameters: [{name: [[input|message]], default: hello}]}
      container: {image: alpine, args: ["{{inputs.parameters.[[inputuse|message]]}}"]}
'''
def external_overlap(loc,q,data,spans,uris):
 ops=[]
 for file,tag,defs,refs in [('caller.yaml','overlap',[loc('caller.yaml','overlap'),loc('library.yaml','input')],None),('caller.yaml','wfuse',[loc('caller.yaml','overlap')],[loc('caller.yaml','wfuse')]),('library.yaml','inputuse',[loc('library.yaml','input')],[loc('library.yaml','inputuse'),loc('caller.yaml','overlap')])]:
  ops.append(q(file,tag,'textDocument/definition',defs))
  for inc in [False,True]:ops.append(q(file,tag,'textDocument/references',[] if refs is None else refs+(defs if inc else []),{'context':{'includeDeclaration':inc}}))
 ops.append(q('caller.yaml','overlap','textDocument/prepareRename',None)|{'refusal':True})
 return ops
case('external-overlap',{'caller.yaml':caller,'library.yaml':lib},external_overlap)
# A proven external role remains part of cardinality without an exact target span.
def inexact_overlap(loc,q,data,spans,uris):
 ops=[q('caller.yaml','overlap','textDocument/definition',[loc('caller.yaml','overlap')]+([loc('library.yaml','input')] if 'duplicate' in spans['library.yaml'] else []))]
 for inc in [False,True]:
  ops.append(q('caller.yaml','overlap','textDocument/references',[],{'context':{'includeDeclaration':inc}}))
 return ops
case('external-overlap-inexact-input',{'caller.yaml':caller,'library.yaml':lib.replace('[[input|message]]', '"[[input|me\\u0073sage]]"')},inexact_overlap)
case('external-overlap-duplicate-input',{'caller.yaml':caller,'library.yaml':lib.replace('[[input|message]], default: hello}', '[[input|message]], default: hello}, {name: [[duplicate|"me\\u0073sage"]], default: second}')},inexact_overlap,[('library.yaml','duplicate','WF_DUPLICATE_SYMBOL')])

# Exact full Steps locations: previous group is visible, peer and later are not.
steps=HEAD+'''  templates:
    - name: main
      steps:
        - - name: [[early|early]]
            template: worker
        - - name: [[middle|middle]]
            template: worker
            arguments: {parameters: [{name: value, value: "{{steps.[[prior|early]].outputs.parameters.[[priorout|result]]}}"}]}
          - name: peer
            template: worker
            when: [[badpeer|"{{steps.middle.status}}"]]
        - - name: later
            template: worker
            when: "{{steps.[[allowed|middle]].status}}"
    - name: worker
      inputs: {parameters: [{name: value, default: hello}]}
      outputs: {parameters: [{name: [[out|result]], value: hello}]}
      container: {image: alpine}
'''
def steps_ops(loc,q,data,spans,uris):
 ops=[q('case.yaml',use,'textDocument/definition',[loc('case.yaml',decl)]) for use,decl in [('prior','early'),('priorout','out'),('allowed','middle')]]
 bad=q('case.yaml','badpeer','textDocument/definition',[]);bad['params']['position']['character']+=10;ops.append(bad)
 for tag,uses in [('early',['prior']),('middle',['allowed']),('out',['priorout'])]:
  for inc in [False,True]:ops.append(q('case.yaml',tag,'textDocument/references',[loc('case.yaml',u) for u in uses]+([loc('case.yaml',tag)] if inc else []),{'context':{'includeDeclaration':inc}}))
 return ops
case('steps-visibility',{'case.yaml':steps},steps_ops,[('case.yaml','badpeer','WF_REFERENCE_SCOPE')])
# Non-BMP code point before the token on the same line, CRLF and unrelated Schema error.
def completion_case(name,text,label,prefix,quoted=False,diagnostics=[]):
 def ops(loc,q,data,spans,uris):
  source=data['case.yaml'];span=spans['case.yaml']['prefix'];before,after=source[:span[0]],source[span[1]:]
  quoted_values=[label] if quoted or label=='message' else ['"'+label+'"',"'"+label+"'"]
  exact=[before+value+after for value in quoted_values]
  return [q('case.yaml','prefix','textDocument/completion',None,end=True)|{'completion':{'labels':[label],'kind':6,'insertTextFormat':1,'range':rng(source,span),'expectedBytes':exact,'metadata':['Argo Workflows 3.6.0','Workflow','namespace: argo','source: '+uris['case.yaml']]}}]
 case(name,{'case.yaml':text},ops,diagnostics)
for val,prefix in [('true','tr'),('null','nu'),('123','12')]:
 base=HEAD+'  entrypoint: [[prefix|'+prefix+']] # untouched\n  templates:\n    - name: "'+val+'"\n      container: {image: alpine}\n'
 completion_case('yaml-plain-template-'+val,base,val,prefix)
 completion_case('yaml-quoted-template-'+val,base.replace('entrypoint: [[prefix|'+prefix+']]', 'entrypoint: "[[prefix|'+prefix+']]"'),val,prefix,True)
param=HEAD+'''  templates:
    - name: main
      dag:
        tasks:
          - name: call
            template: worker
            arguments: {parameters: [{name: [[prefix|tr]], value: hello}]} # untouched
    - name: worker
      inputs: {parameters: [{name: "true", default: hello}]}
      container: {image: alpine}
'''
completion_case('yaml-plain-argument-true',param,'true','tr')
crlf=(HEAD+'  parallelism: [[invalid|wrong]]\n  templates:\n    - name: worker\n      inputs: {parameters: [{name: message, default: hello}]}\n      container: {image: alpine, args: ["{{= \'\U0001f600\' + inputs.parameters[\'[[prefix|mes]]\'] }}"]} # preserve\n').replace('\n','\r\n')
completion_case('crlf-nonbmp-sibling-error',crlf,'message','mes',diagnostics=[('case.yaml','invalid','SCHEMA_TYPE')])
# External member completion uses the same YAML string contract; resource/template
# fields have positive controls for their separate encoding path.
for quoted in [False,True]:
 text=param.replace('template: worker','templateRef: {name: library, template: worker}')
 text=text[:text.index('    - name: worker')]
 if quoted:text=text.replace('name: [[prefix|tr]]','name: "[[prefix|tr]]"')
 library=HEAD.replace('kind: Workflow','kind: WorkflowTemplate').replace('name: audit','name: library')+'  templates:\n    - name: worker\n      inputs: {parameters: [{name: "true", default: hello}]}\n      container: {image: alpine}\n'
 def external_member_ops(loc,q,data,spans,uris,quoted=quoted):
  source=data['case.yaml'];span=spans['case.yaml']['prefix'];values=['true'] if quoted else ['"true"',"'true'"]
  return [q('case.yaml','prefix','textDocument/completion',None,end=True)|{'completion':{'labels':['true'],'kind':6,'insertTextFormat':1,'range':rng(source,span),'expectedBytes':[source[:span[0]]+v+source[span[1]:] for v in values],'metadata':['Argo Workflows 3.6.0','WorkflowTemplate','namespace: argo','source: '+uris['library.yaml']]}}]
 case('yaml-external-argument-'+('quoted' if quoted else 'plain'),{'case.yaml':text,'library.yaml':library},external_member_ops)
for leaf in ['name','template']:
 fragment='{name: [[prefix|tr]], template: worker}' if leaf=='name' else '{name: library, template: [[prefix|tr]]}'
 text=HEAD+'  templates:\n    - name: main\n      dag:\n        tasks:\n          - name: call\n            templateRef: '+fragment+' # untouched\n'
 library=HEAD.replace('kind: Workflow','kind: WorkflowTemplate').replace('name: audit','name: "true"' if leaf=='name' else 'name: library')+'  templates:\n    - name: '+('worker' if leaf=='name' else '"true"')+'\n      container: {image: alpine}\n'
 def external_field_ops(loc,q,data,spans,uris):
  source=data['case.yaml'];span=spans['case.yaml']['prefix'];return [q('case.yaml','prefix','textDocument/completion',None,end=True)|{'completion':{'labels':['true'],'kind':6,'insertTextFormat':1,'range':rng(source,span),'expectedBytes':[source[:span[0]]+'"true"'+source[span[1]:],source[:span[0]]+"'true'"+source[span[1]:]],'metadata':['Argo Workflows 3.6.0','WorkflowTemplate','namespace: argo','source: '+uris['library.yaml']]}}]
 case('yaml-external-'+leaf,{'case.yaml':text,'library.yaml':library},external_field_ops)
if any(x.startswith('--cases=') for x in sys.argv):
 selected=next(x.split('=',1)[1].split(',') for x in sys.argv if x.startswith('--cases='));CASES=[c for c in CASES if c[0] in selected]

# Frozen expectations are persisted above, before starting any SUT process.
if '--author-only' in sys.argv:
 print('Authored',len(CASES),'cases');sys.exit(0)
for name,dest,work,files,expected in CASES:
 client=None;checks={};details=[]
 try:
  client=Client(dest,work)
  for filename,text in files.items():client.open(work/filename,text)
  for i,op in enumerate(expected['queries']):
   response=client.request(op['method'],op['params']);details.append({'request':op,'response':response});key=str(i)+'-'+op['method']
   if op.get('refusal'):
    checks[key]='error' in response or response.get('result') is None;continue
   checks[key+'-protocol']='error' not in response
   if 'completion' in op:
    items=response.get('result') or [];want=op['completion'];checks[key+'-labels']=[x['label'] for x in items]==want['labels']
    if len(items)==1:
     item=items[0];checks[key+'-kind']=item.get('kind')==want['kind'];checks[key+'-format']=item.get('insertTextFormat')==want['insertTextFormat'];checks[key+'-range']=item.get('textEdit',{}).get('range')==want['range'];checks[key+'-metadata']=all(x in item.get('detail','') for x in want['metadata'])
     actual=m.apply(files['case.yaml'],{'documentChanges':[{'textDocument':{'uri':(work/'case.yaml').as_uri(),'version':1},'edits':[item['textEdit']]}]},(work/'case.yaml').as_uri(),1);checks[key+'-exactBytes']=actual in want['expectedBytes'];(dest/'actual.yaml').write_bytes(actual.encode());(work/'case.yaml').write_bytes(actual.encode())
    else:checks[key+'-applied']=False
   else:
    actual=response.get('result');want=op['expected'];checks[key+'-completeResult']=norm(actual or [])==norm(want or []) if op.get('sort') else actual==want
  code,report=m.cli(dest,work)
  actual_diags=[{k:d[k] for k in ['uri','range','code','severity','source','positionKind']} for d in report['diagnostics']]
  checks['fullCLIDiagnostics']=norm(actual_diags)==norm(expected['diagnostics']); checks['exit']=code==expected['exitCode'];checks['counts']=(report['filesAnalyzed'],report['resourcesAnalyzed'])==(expected['filesAnalyzed'],expected['resourcesAnalyzed']);checks['complete']=report['analysisComplete'] is True and report['notEvaluated']==[];checks['configurationErrors']=report['configurationErrors']==[]
  m.result(name,checks,{'queries':details,'cli':report})
 except Exception:
  (dest/'error.txt').write_text(traceback.format_exc());m.result(name,checks|{'completed':False},{'queries':details})
 finally:
  if client:
   client.close()
   if client.p.returncode!=0:m.RESULTS[-1]['assertions']['lspExit']=False;m.RESULTS[-1]['pass']=False;m.save(m.OUT/'results.json',m.RESULTS)
m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(Path(__file__)),'helperSha256':m.sha(BASE/'wf-audit-gaps.py'),'caseCount':len(CASES),'files':{str(p.relative_to(m.OUT)):m.sha(p) for p in m.OUT.rglob('*') if p.is_file() and p.name!='manifest.json'},'assertions':sum(len(x['assertions']) for x in m.RESULTS)})
sys.exit(0 if len(m.RESULTS)==len(CASES) and all(x['pass'] for x in m.RESULTS) else 1)

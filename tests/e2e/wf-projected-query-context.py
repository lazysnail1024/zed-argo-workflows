#!/usr/bin/env python3
"""Independent real CLI/LSP projected ownership and field-context expectations."""
from pathlib import Path
import importlib.util,json,sys,traceback
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('wire',HERE/'wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
MARK='<CURSOR>';BASE='complete domain rules, version acceptance, and resource budgets are not yet implemented'
HEAD='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: projected}\n'
ENUM=['""','"Never"','"OnWorkflowCompletion"','"OnWorkflowDeletion"']
class Client(m.LSP):
 def send(self,method,params=None,ident=None):
  frame={'jsonrpc':'2.0','method':method}
  if params is not None:frame['params']=params
  if ident is not None:frame['id']=ident
  data=json.dumps(frame).encode()
  with (self.dest/'lsp.stdin.raw').open('ab') as f:f.write(('Content-Length: %d\r\n\r\n'%len(data)).encode()+data)
  return super().send(method,params,ident)
CASES=[]
def add(name,marked,method='completion',labels=ENUM,excluded=(),required=(),foreign=False,refusal=None):CASES.append(dict(name=name,marked=marked,method=method,labels=labels,excluded=excluded,required=required,foreign=foreign,refusal=refusal))
body='spec:\n  artifactGC:\n    strategy: Ne'+MARK+'ver\n'
alias='metadata:\n  name: projected\n  annotations:\n    api: &api argoproj.io/v1alpha1\n    type: &type Workflow\napiVersion: *api\nkind: *type\n'
add('alias-gvk-value',alias+body)
add('alias-gvk-hover-crlf',(alias+body).replace('projected','projected-\U0001f600').replace('\n','\r\n'),method='hover')
merged='<<: {apiVersion: argoproj.io/v1alpha1, kind: Workflow}\nmetadata: {name: projected}\n'
add('root-merge-value',merged+body)
add('root-merge-hover',merged+body,method='hover')
add('foreign-base-owned-override','<<: {apiVersion: example.io/v1, kind: Other}\n'+HEAD+body)
add('owned-base-foreign-override',merged+'apiVersion: example.io/v1\nkind: Other\n'+body,labels=[],foreign=True)
add('owned-base-foreign-hover',merged+'apiVersion: example.io/v1\nkind: Other\n'+body,method='hover',foreign=True)
add('explicit-value-overrides-merge',HEAD+'spec:\n  artifactGC:\n    <<: {strategy: OnWorkflowDeletion}\n    strategy: Ne'+MARK+'ver\n')
add('sequence-merge-explicit-value',HEAD+'spec:\n  artifactGC:\n    <<: [{strategy: OnWorkflowDeletion}, {strategy: OnWorkflowCompletion}]\n    strategy: Ne'+MARK+'ver\n')
add('inherited-existing-key',HEAD+'spec:\n  artifactGC:\n    <<: {strategy: Never}\n    '+MARK+'\n',labels=None,excluded=['strategy'],required=['forceFinalizerRemoval'])
add('sequence-existing-key',HEAD+'spec:\n  artifactGC:\n    <<: [{strategy: Never}, {serviceAccountName: retained}]\n    '+MARK+'\n',labels=None,excluded=['strategy','serviceAccountName'],required=['forceFinalizerRemoval'])
add('gvk-self-alias-sibling',alias.replace('apiVersion: *api','apiVer'+MARK+'sion: *api')+'spec: {}\n',method='hover')
for name,suffix,reason in [('duplicate','metadata: {name: duplicate}\n','duplicate mapping key metadata'),('unknown-alias','spec: {artifactGC: {strategy: *missing}}\n','unknown alias missing')]:
 marked=HEAD+('spec:\n  parallel'+MARK+'ism: 2\n'+suffix if name=='duplicate' else suffix.replace('strategy:', 'strat'+MARK+'egy:'))
 add('strict-'+name,marked,labels=[],refusal=reason)

add('strict-selected-duplicate',HEAD+'spec:\n  artifactGC:\n    strategy: Never\n    strat'+MARK+'egy: Never\n',labels=[],refusal='duplicate mapping key strategy')
large=HEAD+'spec:\n  artifactGC: {strategy: Ne'+MARK+'ver}\n  templates:\n    - name: main\n      container:\n        image: alpine\n        args: [&x x, '+', '.join(['*x']*60000)+']\n'
add('strict-expanded-query-budget',large,labels=[],refusal='alias expansion exceeds 100000 nodes')

add('foreign-kind-key-marker',merged+'ki'+MARK+'nd: Other\nspec: {}\n',method='hover',foreign=True)
add('foreign-kind-key-completion',merged+'ki'+MARK+'nd: Other\nspec: {}\n',labels=[],foreign=True)

if len(sys.argv)>3 and sys.argv[3]=='synthetic':
 CASES=[]
 for name,merge,required,excluded in [('merge-branch-override','<<: {mode: chart}\n    mode: git','repo','chart'),('merge-sequence-first','<<: [{mode: git}, {mode: chart}]','repo','chart'),('merge-sequence-reversed','<<: [{mode: chart}, {mode: git}]','chart','repo')]:
  add(name,HEAD+'spec:\n  branch:\n    '+merge+'\n    '+MARK+'\n',labels=[required],excluded=[excluded,'mode'],required=[required])

def normalized(report):return {k:report.get(k) for k in ['diagnostics','configurationErrors','filesAnalyzed','resourcesAnalyzed','analysisComplete','notEvaluated']}
if len(sys.argv)>4: CASES=[c for c in CASES if c['name'] in sys.argv[4].split(',')]
for case in CASES:
 name=case['name'];text=case['marked'].replace(MARK,'');dest,work=m.setup(name,{'case.yaml':text});path=work/'case.yaml';client=None
 expected_cli={'diagnostics':[],'configurationErrors':[],'filesAnalyzed':0 if case['refusal'] else 1,'resourcesAnalyzed':0 if case['refusal'] or case['foreign'] else 1,'analysisComplete':not bool(case['refusal']),'notEvaluated':([case['refusal']] if case['refusal'] else [])}
 m.save(dest/'expected.json',case|{'cli':expected_cli,'cliExit':2 if case['refusal'] else 0});(dest/'original.yaml').write_bytes(text.encode())
 try:
  code,report=m.cli(dest,work);checks={'cliEnvelope':normalized(report)==expected_cli,'cliExit':code==(2 if case['refusal'] else 0)};client=Client(dest,work);client.open(path,text)
  response=client.request('textDocument/'+case['method'],{'textDocument':{'uri':path.as_uri()},'position':m.pos(case['marked'],MARK)});value=response.get('result')
  if case['refusal']:checks.update(explicitRefusal=response.get('error',{}).get('code')==-32803,noPartial='result' not in response)
  elif case['method']=='hover':
   if case['foreign']:checks['foreignFiltered']=value is None and 'error' not in response
   else:
    content=(value or {}).get('contents',{}).get('value','');checks['hoverProvenance']=all(x in content for x in ['Type: `string`','3.6.0','b26ed4aa4dee395844531efa4a76a022183bec22']); token='apiVersion' if name=='gvk-self-alias-sibling' else 'Never'; start=m.pos(text,token); end=m.pos(text,token,offset=len(token)); checks['exactHoverRange']=(value or {}).get('range')=={'start':start,'end':end}
  else:
   items=value if isinstance(value,list) else [];labels=[x['label'] for x in items];checks['noProtocolError']='error' not in response;checks['excluded']=not(set(labels)&set(case['excluded']));checks['required']=set(case['required'])<=set(labels)
   if case['labels'] is not None:checks['exactLabels']=sorted(labels)==sorted(case['labels'])
   if case['labels']==ENUM:
    matches=[x for x in items if x['label']=='"Never"'];checks['oneEdit']=len(matches)==1
    if len(matches)==1:
     edit=matches[0]['textEdit'];expected=text.replace('strategy: Never','strategy: "Never"',1);actual=m.apply(text,{'documentChanges':[{'textDocument':{'uri':path.as_uri(),'version':1},'edits':[edit]}]},path.as_uri(),1);checks['exactBytes']=actual==expected;(dest/'expected-edited.yaml').write_bytes(expected.encode());(dest/'actual-edited.yaml').write_bytes(actual.encode());path.write_bytes(actual.encode());ec,er=m.cli(dest,work,'edited');checks['editedCLI']=ec==0 and normalized(er)==expected_cli
  if case['refusal']:
   recovered=HEAD+body.replace(MARK,'');client.change(path,recovered,version=2);path.write_bytes(recovered.encode());rp=client.request('textDocument/completion',{'textDocument':{'uri':path.as_uri()},'position':m.pos(HEAD+body,MARK)});checks['recovery']=sorted(x['label'] for x in rp.get('result',[]))==sorted(ENUM);m.save(dest/'recovery.json',{'expectedLabels':ENUM,'response':rp})
  m.result(name,checks,{'response':response,'cli':report})
 except Exception:m.result(name,{'completed':False},{'error':traceback.format_exc()})
 finally:
  if client:client.close()
m.save(m.OUT/'manifest.json',{'command':sys.argv,'nativeSha256':m.sha(m.BINARY),'testSha256':m.sha(Path(__file__)),'caseCount':len(CASES),'inputs':{str(p.relative_to(m.OUT)):m.sha(p) for p in m.OUT.glob('*/original.yaml')}})
sys.exit(0 if len(m.RESULTS)==len(CASES) and all(x['pass'] for x in m.RESULTS) else 1)

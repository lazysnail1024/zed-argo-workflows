#!/usr/bin/env python3
"""Fixed 3.6.0 entrypoint/onExit argument selection through real CLI and LSP."""
from pathlib import Path
import importlib.util,json,re,sys,traceback
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('wire',HERE/'wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
BASE='complete domain rules, version acceptance, and resource budgets are not yet implemented'
def strip(text):
 plain='';end=0;spans={}
 for hit in re.finditer(r'\[\[(\w+)\|(.*?)\]\](?!\])',text,re.S):
  plain+=text[end:hit.start()];a=len(plain);plain+=hit[2];spans[hit[1]]=(a,len(plain));end=hit.end()
 return plain+text[end:],spans
def pos(text,offset):
 head=text[:offset];return {'line':head.count('\n'),'character':len(head.rsplit('\n',1)[-1].encode('utf-16-le'))//2}
def norm(rows):return sorted(json.dumps(x,sort_keys=True) for x in rows)
CALL='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: caller, namespace: argo}\nspec:\n  workflowTemplateRef: {name: [[ref|library]]}\n'
LIB='''apiVersion: argoproj.io/v1alpha1
kind: WorkflowTemplate
metadata: {name: library, namespace: argo}
spec:
  entrypoint: run
  onExit: cleanup
  arguments:
    parameters: [{name: message, value: base}, {name: exitValue, value: base-exit}]
  templates:
    - name: run
      inputs: {parameters: [{name: message}]}
      container: {image: alpine}
    - name: cleanup
      inputs: {parameters: [{name: exitValue}]}
      container: {image: alpine}
    - name: alternate
      container: {image: alpine}
'''
NOEXIT=LIB.replace('  onExit: cleanup\n','')
SUPPLY='  arguments: {parameters: [{name: exitValue, value: caller-exit}]}\n'
CASES=[]
def missing(tag,name):return (tag,'WF_ARGUMENT_MISSING','required input '+name+' was not supplied','Error','exact')
def unresolved(tag,name):return (tag,'WF_TEMPLATE_REF_UNRESOLVED','resolved resource does not declare template '+name,'Error','exact')
def add(name,caller=CALL,library=LIB,diags=(),recovery=None,extra=None):CASES.append((name,caller,library,list(diags),recovery,extra or {}))
# Existing fallback and whole-object precedence remain positive controls.
add('entrypoint-fallback',library=NOEXIT)
add('caller-entrypoint',CALL+'  entrypoint: alternate\n',NOEXIT)
add('caller-argument-precedence',CALL+'  arguments: {parameters: [{name: message, value: caller}]}\n',NOEXIT)
add('caller-entrypoint-missing',CALL+'  entrypoint: [[entry|absent]]\n',NOEXIT,[unresolved('entry','absent')])
# onExit never borrows the merged entrypoint argument set.
add('inherited-exit-missing',diags=[missing('ref','parameters.exitValue')],recovery=CALL+SUPPLY)
add('inherited-exit-provided',CALL+SUPPLY)
add('explicit-exit-missing',CALL+'  onExit: [[exit|cleanup]]\n',LIB,[missing('exit','parameters.exitValue')])
add('explicit-exit-override',CALL+'  onExit: alternate\n')
add('explicit-exit-unknown',CALL+'  onExit: [[exit|absent]]\n',LIB,[unresolved('exit','absent')])
cron=CALL.replace('kind: Workflow\n','kind: CronWorkflow\n').replace('spec:\n  workflowTemplateRef:', 'spec:\n  schedule: "* * * * *"\n  workflowSpec:\n    workflowTemplateRef:')
add('cron-inherited-exit',cron,LIB,[missing('ref','parameters.exitValue')],cron+SUPPLY.replace('  arguments','    arguments'))
# Only a nonempty local template collection conflicts with whole-resource use.
add('empty-local-templates',CALL+'  templates: []\n',NOEXIT)
add('nonempty-local-templates',CALL+'  templates: [[local|[{name: local, container: {image: alpine}}]]]\n',NOEXIT,[('local','WF_TEMPLATE_REF_TEMPLATES','local templates cannot be combined with workflowTemplateRef','Error','exact')])
# Template validation ignores an absent own entrypoint and otherwise uses fake arguments.
required=NOEXIT.replace('  arguments:\n    parameters: [{name: message, value: base}, {name: exitValue, value: base-exit}]\n','')
for entry in ['', '  entrypoint: run\n']:
 add('template-validation-'+('absent-entry' if not entry else 'explicit-entry'),CALL.replace('kind: Workflow\n','kind: WorkflowTemplate\n')+entry,required)
add('workflow-real-entry-arguments',library=required,diags=[missing('ref','parameters.message')])
# ProcessArgs is called with validateOnly=true; fake artifact presence differs
# from parameter values. This pair does not infer rules for malformed real artifacts.
artifact=required.replace('inputs: {parameters: [{name: message}]}','inputs: {artifacts: [{name: payload, path: /tmp/payload}]}')
add('template-fake-artifact',CALL.replace('kind: Workflow\n','kind: WorkflowTemplate\n')+'  entrypoint: run\n',artifact)
add('workflow-real-artifact-missing',library=artifact,diags=[missing('ref','artifacts.payload')])
# WFT/CWT allow empty workflow argument values during template validation,
# but onExit uses those original arguments and a present empty parameter clears default.
for kind in ['WorkflowTemplate','ClusterWorkflowTemplate']:
 for empty in [False,True]:
  text='apiVersion: argoproj.io/v1alpha1\nkind: '+kind+'\nmetadata: {name: own-template}\nspec:\n  onExit: [[exit|cleanup]]\n'+('  arguments: {parameters: [{name: message}]}\n' if empty else '')+'  templates:\n    - name: cleanup\n      inputs: {parameters: [{name: message, default: retained}]}\n      container: {image: alpine}\n'
  add(kind+'-exit-'+('empty-argument' if empty else 'default'),text,NOEXIT,[missing('exit','parameters.message')] if empty else [])
add('namespace-isolation',library=NOEXIT.replace('namespace: argo','namespace: other'),diags=[('ref','WF_TEMPLATE_REF_UNRESOLVED','external template resource is not resolved: library','Error','exact')])
add('resource-ambiguity',library=NOEXIT,diags=[('ref','WF_TEMPLATE_REF_AMBIGUOUS','multiple external template resources match library','Warning','exact')],extra={'duplicate.yaml':NOEXIT})
# IgnoreEntrypoint comes from the caller before fallback resolution. The
# invalid library retains its own diagnostic, without inventing a caller call.
add('template-ignore-invalid-fallback',CALL.replace('kind: Workflow\n','kind: WorkflowTemplate\n'),required.replace('  entrypoint: run\n','  entrypoint: [[libEntry|absent]]\n'),[('libEntry','WF_LOCAL_TEMPLATE_MISSING','local template does not exist: absent','Error','exact')])
add('template-inherited-exit-still-called',CALL.replace('kind: Workflow\n','kind: WorkflowTemplate\n'),LIB,[missing('ref','parameters.exitValue')])
# An onExit call needs a unique local callee before proving missing inputs.
local_head='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: local-exit}\nspec:\n  entrypoint: main\n  onExit: [[exit|cleanup]]\n  templates:\n    - name: main\n      container: {image: alpine}\n'
required_cleanup='    - name: cleanup\n      inputs: {parameters: [{name: message}]}\n      container: {image: alpine}\n'
empty_cleanup='    - name: cleanup\n      container: {image: alpine}\n'
add('local-exit-unique-missing',local_head+required_cleanup,NOEXIT,[missing('exit','parameters.message')])
for label,first,second in [('required-first',required_cleanup,empty_cleanup),('empty-first',empty_cleanup,required_cleanup)]:
 add('local-exit-duplicate-'+label,local_head+first+second.replace('name: cleanup','name: [[duplicate|cleanup]]'),NOEXIT,[('duplicate','WF_DUPLICATE_SYMBOL','duplicate local template cleanup','Error','exact')])
# Independent duplicate arguments remain errors when the local callee is ambiguous.
for duplicate in [False, True]:
 text='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: duplicate-call}\nspec:\n  entrypoint: main\n  templates:\n    - name: main\n      dag:\n        tasks:\n          - name: invoke\n            template: worker\n            arguments:\n              parameters: [{name: message, value: first}, {name: [[argumentDuplicate|message]], value: second}]\n    - name: worker\n      inputs: {parameters: [{name: message}]}\n      container: {image: alpine}\n'
 diags=[('argumentDuplicate','WF_DUPLICATE_SYMBOL','duplicate parameter or artifact message','Error','exact')]
 if duplicate:
  text+='    - name: [[calleeDuplicate|worker]]\n      inputs: {parameters: [{name: message}]}\n      container: {image: alpine}\n'
  diags.append(('calleeDuplicate','WF_DUPLICATE_SYMBOL','duplicate local template worker','Error','exact'))
 add('duplicate-call-arguments-'+('ambiguous' if duplicate else 'unique'),text,NOEXIT,diags)
if len(sys.argv)>3:CASES=[c for c in CASES if c[0] in sys.argv[3].split(',')]
for name,marked,library,diags,recovery,extra in CASES:
 text,tags=strip(marked);library,library_tags=strip(library);dest,work=m.setup(name,{'caller.yaml':text,'library.yaml':library}|extra);path=work/'caller.yaml';client=None;checks={};responses=[]
 def location(tag):
  source,spans,target=(text,tags,path) if tag in tags else (library,library_tags,work/'library.yaml')
  a,z=spans[tag];return {'uri':target.as_uri(),'range':{'start':pos(source,a),'end':pos(source,z)}}
 expected=[location(tag)|{'code':code,'message':message,'severity':severity,'source':'argo-workflows','positionKind':origin} for tag,code,message,severity,origin in diags]
 files=2+len(extra);envelope={'configurationErrors':[],'filesAnalyzed':files,'resourcesAnalyzed':files,'analysisComplete':True,'notEvaluated':[]}
 m.save(dest/'expected.json',{'diagnostics':expected,'envelope':envelope,'exitCode':2,'recoveryDiagnostics':[] if recovery else None,'unchangedBytes':True});(dest/'original.yaml').write_bytes(text.encode())
 try:
  code,report=m.cli(dest,work);keys=['uri','range','code','message','severity','source','positionKind'];checks['fullCLI']=norm([{k:d[k] for k in keys} for d in report['diagnostics']])==norm(expected);checks['envelope']=code==int(any(d['severity']=='Error' for d in expected)) and {k:report.get(k) for k in envelope}==envelope
  client=m.LSP(dest,work);published=client.open(path,text);want=[{k:d[k] for k in ['range','code','message','source']}|{'severity':1 if d['severity']=='Error' else 2} for d in expected if d['uri']==path.as_uri()];checks['published']=norm(published)==norm(want)
  library_published=client.open(work/'library.yaml',library);library_want=[{k:d[k] for k in ['range','code','message','source']}|{'severity':1 if d['severity']=='Error' else 2} for d in expected if d['uri']==(work/'library.yaml').as_uri()];checks['libraryPublished']=norm(library_published)==norm(library_want)
  if recovery:
   fixed,_=strip(recovery);checks['recoveryPublication']=client.change(path,fixed,2)==[];checks['restoredPublication']=norm(client.change(path,text,3))==norm(want)
   recover_dest,recover_work=m.setup(name+'-recovery',{'caller.yaml':fixed,'library.yaml':library}|extra);recover_code,recover_report=m.cli(recover_dest,recover_work);checks['recoveryCLI']=recover_code==0 and recover_report['diagnostics']==[] and {k:recover_report.get(k) for k in envelope}==envelope
  checks['unchangedBytes']=path.read_bytes()==text.encode() and (work/'library.yaml').read_bytes()==library.encode();m.result(name,checks,{'cli':report,'publication':published})
 except Exception:m.result(name,checks|{'completed':False},{'error':traceback.format_exc()})
 finally:
  if client:client.close()
m.save(m.OUT/'manifest.json',{'command':sys.argv,'nativeSha256':m.sha(m.BINARY),'testSha256':m.sha(Path(__file__)),'helperSha256':m.sha(HERE/'wf-audit-gaps.py'),'cases':len(CASES),'python':sys.version,'inputs':{str(p.relative_to(m.OUT)):m.sha(p) for p in m.OUT.rglob('*.yaml')}})
sys.exit(0 if len(m.RESULTS)==len(CASES) and all(x['pass'] for x in m.RESULTS) else 1)

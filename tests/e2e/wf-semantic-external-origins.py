#!/usr/bin/env python3
"""Projected external call structures retain semantics without inventing origins."""
from pathlib import Path
import importlib.util,json,re,sys,traceback
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('wire',HERE/'wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def strip(text):
 plain='';end=0;spans={}
 for match in re.finditer(r'\[\[(\w+)\|(.*?)\]\]',text,re.S):
  plain+=text[end:match.start()];a=len(plain);plain+=match[2];spans[match[1]]=(a,len(plain));end=match.end()
 return plain+text[end:],spans
def position(text,index):
 head=text[:index];return {'line':head.count('\n'),'character':len(head.rsplit('\n',1)[-1].encode('utf-16-le'))//2}
def norm(rows):return sorted(json.dumps(x,sort_keys=True) for x in rows)
def unique(use,decl):return [('definition',use,[decl],None)]+[('references',site,[use]+([decl] if inc else []),inc) for site in [use,decl] for inc in [False,True]]
def absent(site):return [('definition',site,[],None)]+[('references',site,[],inc) for inc in [False,True]]
HEAD='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: caller, namespace: argo}\nspec:\n  entrypoint: main\n  templates:\n    - name: main\n      dag:\n        tasks:\n'
LIB='apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: {name: library, namespace: argo}\nspec:\n  templates:\n    - name: worker\n      inputs: {parameters: [{name: [[input|message]]}]}\n      container: {image: alpine}\n'
CASES=[]
for shape in ['alias','merge']:
 prototype='          - name: prototype\n            templateRef: &ref {name: library, template: [[sharedTemplate|worker]]}\n            arguments: {parameters: [{name: [[argument|message]], value: hello}]}\n' if shape=='alias' else ''
 ref='*ref' if shape=='alias' else '{<<: {name: library, template: worker}}'
 caller=HEAD+prototype+'          - name: projected\n            templateRef: [[derived|'+ref+']]\n          - name: unrelated\n            template: [[siblingUse|sibling]]\n    - name: [[sibling|sibling]]\n      container: {image: alpine}\n'
 queries=absent('derived')+unique('siblingUse','sibling')
 if shape=='alias':queries+=absent('sharedTemplate')+unique('argument','input')
 CASES.append(('external-'+shape+'-call',caller,LIB,[('derived','WF_ARGUMENT_MISSING','derived')],queries,['derived'],['siblingUse']))
merged_task=HEAD+'          - [[derived|{name: projected, <<: {templateRef: {name: library, template: worker}}}]]\n          - name: unrelated\n            template: [[siblingUse|sibling]]\n    - name: [[sibling|sibling]]\n      container: {image: alpine}\n'
CASES.append(('external-merged-task',merged_task,LIB,[('derived','WF_ARGUMENT_MISSING','derived')],absent('derived')+unique('siblingUse','sibling'),['derived'],['siblingUse']))
merged_argument=HEAD+'          - {name: projected, arguments: {parameters: [{name: [[argument|message]], value: hello}]}, <<: {templateRef: {name: library, template: worker}}}\n          - name: unrelated\n            template: [[siblingUse|sibling]]\n    - name: [[sibling|sibling]]\n      container: {image: alpine}\n'
CASES.append(('external-merged-task-argument',merged_argument,LIB,[],unique('argument','input')+unique('siblingUse','sibling'),[],['argument']))
merged_duplicate=merged_argument.replace('template: worker','template: [[derivedTemplate|worker]]')
merged_duplicate_library=LIB+'    - name: [[duplicate|worker]]\n      inputs: {parameters: [{name: [[secondInput|message]]}]}\n      container: {image: alpine}\n'
merged_duplicate_queries=[('definition','argument',['input','secondInput'],None)]+[('references','argument',[],inc) for inc in [False,True]]+[('references',site,[site] if inc else [],inc) for site in ['input','secondInput'] for inc in [False,True]]+unique('siblingUse','sibling')+absent('derivedTemplate')
CASES.append(('external-merged-task-duplicate',merged_duplicate,merged_duplicate_library,[('duplicate','WF_DUPLICATE_SYMBOL','exact')],merged_duplicate_queries,['derivedTemplate'],['argument']))
for shape in ['alias','merge']:
 for duplicate in [False,True]:
  caller='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: caller, namespace: argo, annotations: {use: "{{workflow.parameters.[[workflowUse|message]]}}"}}\nspec:\n  workflowTemplateRef: {name: library}\n  entrypoint: [[entry|worker]]\n  arguments: {parameters: [{name: [[argument|message]], value: hello}]}\n'
  meta='metadata: {name: library, namespace: argo'+(', annotations: {template: &template worker}' if shape=='alias' else '')+'}\n'
  name='    - name: *template\n' if shape=='alias' else '    - <<: {name: worker}\n'
  library='apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\n'+meta+'spec:\n  entrypoint: [[siblingUse|sibling]]\n  templates:\n'+name+'      inputs: {parameters: [{name: [[input|message]]}]}\n      container: {image: alpine, args: ["{{inputs.parameters.[[inputUse|message]]}}"]}\n'
  if duplicate:library+='    - name: [[duplicate|worker]]\n      inputs: {parameters: [{name: [[secondInput|message]]}]}\n      container: {image: alpine}\n'
  library+='    - name: [[sibling|sibling]]\n      container: {image: alpine}\n'
  queries=[('definition','entry',['duplicate'] if duplicate else [],None),('definition','argument',['argument','input']+(['secondInput'] if duplicate else []),None)]+[('references','argument',[],inc) for inc in [False,True]]
  queries+=unique('siblingUse','sibling')+[('definition','workflowUse',['argument'],None)]+[('references','workflowUse',['workflowUse']+(['argument'] if inc else []),inc) for inc in [False,True]]+[('definition','inputUse',['input'],None)]
  queries += [('references',site,['inputUse']+([] if duplicate else ['argument'])+(['input'] if inc else []),inc) for site in ['inputUse','input'] for inc in [False,True]]
  if duplicate:queries += [('references','entry',[],inc) for inc in [False,True]]
  CASES.append(('external-'+shape+'-template'+('-duplicate' if duplicate else ''),caller,library,[('duplicate','WF_DUPLICATE_SYMBOL','exact')] if duplicate else [],queries,[],['argument']))
if len(sys.argv)>3:
 assert sys.argv[3] in ['merged-task-only','merged-argument-only','merged-duplicate-only']
 selected={'merged-task-only':'external-merged-task','merged-argument-only':'external-merged-task-argument','merged-duplicate-only':'external-merged-task-duplicate'}[sys.argv[3]]
 CASES=[c for c in CASES if c[0]==selected]
BASE='complete domain rules, version acceptance, and resource budgets are not yet implemented'
for policy in ['closed','open']:
 for name,cm,lm,diagnostics,queries,no_edits,rename_refused in CASES:
  name=policy+'-'+name;caller,cs=strip(cm);library,ls=strip(lm);dest,work=m.setup(name,{'caller.yaml':caller,'library.yaml':library},m.CONFIG.replace('referencePolicy: closed','referencePolicy: '+policy));cp=work/'caller.yaml';lp=work/'library.yaml';locations={}
  for path,text,spans in [(cp,caller,cs),(lp,library,ls)]:
   for tag,(a,z) in spans.items():locations[tag]={'uri':path.as_uri(),'range':{'start':position(text,a),'end':position(text,z)}}
  expected=[locations[tag]|{'code':code,'source':'argo-workflows','severity':'Error','positionKind':origin} for tag,code,origin in diagnostics];ops=[{'method':method,'at':locations[site],'locations':[locations[t] for t in targets],'includeDeclaration':inc} for method,site,targets,inc in queries];m.save(dest/'expected.json',{'diagnostics':expected,'queries':ops,'filesAnalyzed':2,'resourcesAnalyzed':2,'exitCode':int(bool(expected)),'notEvaluated':[],'noEditsAt':[locations[x] for x in no_edits]});client=None;checks={};responses=[]
  try:
   code,report=m.cli(dest,work);checks['fullCLI']=norm([{k:d[k] for k in ['uri','range','code','source','severity','positionKind']} for d in report['diagnostics']])==norm(expected);checks['completeEnvelope']=code==int(bool(expected)) and report['filesAnalyzed']==2 and report['resourcesAnalyzed']==2 and report['analysisComplete'] is True and report['configurationErrors']==[] and report['notEvaluated']==[];client=m.LSP(dest,work)
   for path,text in [(cp,caller),(lp,library)]:
    ds=client.open(path,text);want=[{k:d[k] for k in ['range','code','source']}|{'severity':1} for d in expected if d['uri']==path.as_uri()];checks['publication'+path.name]=norm([{k:d[k] for k in ['range','code','source','severity']} for d in ds])==norm(want)
   for i,q in enumerate(ops):
    params={'textDocument':{'uri':q['at']['uri']},'position':q['at']['range']['start']}
    if q['includeDeclaration'] is not None:params['context']={'includeDeclaration':q['includeDeclaration']}
    r=client.request('textDocument/'+q['method'],params);responses.append(r);checks['query'+str(i)]='error' not in r and norm(r.get('result',[]))==norm(q['locations'])
   for tag in no_edits:
    at=locations[tag];r=client.request('textDocument/codeAction',{'textDocument':{'uri':at['uri']},'range':at['range'],'context':{'diagnostics':[]}});responses.append(r);checks['noAction'+tag]=r.get('result')==[] and 'error' not in r;r=client.request('textDocument/completion',{'textDocument':{'uri':at['uri']},'position':at['range']['start']});responses.append(r);checks['noCompletion'+tag]=r.get('result')==[] and 'error' not in r
   for tag in rename_refused:
    at=locations[tag];r=client.request('textDocument/rename',{'textDocument':{'uri':at['uri']},'position':at['range']['start'],'newName':'replacement'});responses.append(r);checks['renameRefused'+tag]=r.get('error',{}).get('code')==-32602 and 'result' not in r
   checks['unchangedBytes']=cp.read_bytes()==caller.encode() and lp.read_bytes()==library.encode();m.result(name,checks,{'cli':report,'responses':responses})
  except Exception:
   (dest/'error.txt').write_text(traceback.format_exc());m.result(name,checks|{'completed':False},{'responses':responses})
  finally:
   if client:client.close()
m.save(m.OUT/'manifest.json',{'command':sys.argv,'nativeSha256':m.sha(m.BINARY),'testSha256':m.sha(Path(__file__))});sys.exit(0 if len(m.RESULTS)==2*len(CASES) and all(r['pass'] for r in m.RESULTS) else 1)

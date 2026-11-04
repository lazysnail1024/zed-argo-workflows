#!/usr/bin/env python3
"""Exact lexical bindings use effective values without inventing derived locations."""
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
HEAD='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: lexical, annotations: {marker: &marker retained, markerUse: *marker}}\nspec:\n  templates:\n'
SIBLING='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: sibling}\nspec:\n  entrypoint: [[siblingUse|main]]\n  templates:\n    - name: [[sibling|main]]\n      container: {image: alpine}\n'
CASES=[]
for shape,dependencies in [('direct','dependencies: [first]'),('merge','<<: {dependencies: [first]}'),('alias','dependencies: *deps')]:
 head=HEAD.replace('markerUse: *marker','markerUse: *marker, deps: &deps [first]') if shape=='alias' else HEAD
 text=head+'    - name: main\n      dag:\n        tasks:\n          - {name: [[task|first]], template: worker}\n          - name: user\n            template: sink\n            '+dependencies+'\n            arguments: {parameters: [{name: message, value: "{{tasks.first.outputs.parameters.[[use|result]]}}"}]}\n    - name: worker\n      outputs: {parameters: [{name: [[decl|result]], value: retained}]}\n      container: {image: alpine}\n    - name: sink\n      inputs: {parameters: [{name: message}]}\n      container: {image: alpine}\n'
 CASES.append(('dependency-'+shape,text,[],unique('use','decl')))
for shape,call in [('direct','template: worker'),('merge','<<: {template: [[derived|worker]]}'),('alias','template: [[derived|*worker]]')]:
 head=HEAD.replace('markerUse: *marker','markerUse: *marker, worker: &worker worker') if shape=='alias' else HEAD
 text=head+'    - name: main\n      dag:\n        tasks:\n          - name: user\n            '+call+'\n            arguments: {parameters: [{name: [[use|message]], value: retained}]}\n    - name: worker\n      inputs: {parameters: [{name: [[decl|message]]}]}\n      container: {image: alpine}\n'
 CASES.append(('callee-'+shape,text,[],unique('use','decl')+(absent('derived') if shape!='direct' else [])))
# The overridden scalar is valid YAML source but is not an effective occurrence.
for override in [False,True]:
 text=HEAD+'    - name: main\n      inputs: {parameters: [{name: [[decl|message]], default: retained}]}\n      container:\n        image: alpine\n        <<: {args: ["{{inputs.parameters.[[masked|message]]}}"]}\n'+('        args: ["{{inputs.parameters.[[use|message]]}}"]\n' if override else '')
 queries=unique('use','decl')+absent('masked') if override else [('references','decl',['decl'] if inc else [],inc) for inc in [False,True]]+absent('masked')
 CASES.append(('overridden-merge' if override else 'derived-merge-only',text,[],queries))
# A second semantic callee prevents binding the argument to either input.
text=CASES[4][1]+'    - name: [[duplicate|worker]]\n      inputs: {parameters: [{name: [[second|message]]}]}\n      container: {image: alpine}\n'
queries=[('definition','use',['decl','second'],None)]+[('references',site,[site] if inc and site in ['decl','second'] else [],inc) for site in ['use','decl','second'] for inc in [False,True]]+absent('derived')
CASES.append(('duplicate-callee',text,[('duplicate','WF_DUPLICATE_SYMBOL','duplicate local template worker','exact')],queries))
# Missing input in one ambiguous callee does not make the other callee unique.
text=text.replace('      inputs: {parameters: [{name: [[second|message]]}]}\n','')
queries=[('definition','use',['decl'],None)]+[('references',site,[site] if inc and site=='decl' else [],inc) for site in ['use','decl'] for inc in [False,True]]+absent('derived')
CASES.append(('duplicate-callee-missing-input',text,[('duplicate','WF_DUPLICATE_SYMBOL','duplicate local template worker','exact')],queries))
# Workflow declarations retain their independent role when callee inputs are ambiguous.
for has_input in [True,False]:
 text=HEAD.replace('markerUse: *marker','markerUse: *marker, note: "{{workflow.parameters.[[wfuse|message]]}}"').replace('  templates:\n','  entrypoint: main\n  arguments: {parameters: [{name: [[decl|message]], value: retained}]}\n  templates:\n')+'    - name: main\n'+('      inputs: {parameters: [{name: [[input|message]], default: retained}]}\n' if has_input else '')+'      container: {image: alpine}\n    - name: [[duplicate|main]]\n      container: {image: alpine}\n'
 queries=unique('wfuse','decl')
 if has_input:
  queries=[('definition','wfuse',['decl'],None)]+[('references','wfuse',['wfuse']+(['decl'] if inc else []),inc) for inc in [False,True]]+[('definition','decl',['decl','input'],None)]+[('references','decl',[],inc) for inc in [False,True]]+[('references','input',['input'] if inc else [],inc) for inc in [False,True]]
 CASES.append(('workflow-role-'+('ambiguous-input' if has_input else 'zero-input'),text,[('duplicate','WF_DUPLICATE_SYMBOL','duplicate local template main','exact')],queries))
# Annotation keys and values both retain independently exact lexical uses.
for shape,annotation in [('value','note: "{{workflow.parameters.[[use|message]]}}"'),('key','"{{workflow.parameters.[[use|message]]}}": retained')]:
 text=HEAD.replace('markerUse: *marker','markerUse: *marker, '+annotation).replace('  templates:\n','  arguments: {parameters: [{name: [[decl|message]], value: retained}]}\n  templates:\n')+'    - name: main\n      container: {image: alpine}\n'
 CASES.append(('annotation-'+shape,text,[],unique('use','decl')))
if len(sys.argv)>3: CASES=[c for c in CASES if c[0] in sys.argv[3].split(',')]
BASE='complete domain rules, version acceptance, and resource budgets are not yet implemented'
for policy in ['closed','open']:
 for name,cm,diagnostics,queries in CASES:
  lm=SIBLING;queries=queries+unique('siblingUse','sibling');no_edits=[];rename_refused=['decl']
  name=policy+'-'+name;caller,cs=strip(cm);library,ls=strip(lm);dest,work=m.setup(name,{'caller.yaml':caller,'library.yaml':library},m.CONFIG.replace('referencePolicy: closed','referencePolicy: '+policy));cp=work/'caller.yaml';lp=work/'library.yaml';locations={}
  for path,text,spans in [(cp,caller,cs),(lp,library,ls)]:
   for tag,(a,z) in spans.items():locations[tag]={'uri':path.as_uri(),'range':{'start':position(text,a),'end':position(text,z)}}
  expected=[locations[tag]|{'code':code,'message':message,'source':'argo-workflows','severity':'Error','positionKind':origin} for tag,code,message,origin in diagnostics];ops=[{'method':method,'at':locations[site],'locations':[locations[t] for t in targets],'includeDeclaration':inc} for method,site,targets,inc in queries];m.save(dest/'expected.json',{'diagnostics':expected,'queries':ops,'filesAnalyzed':2,'resourcesAnalyzed':2,'exitCode':int(bool(expected)),'notEvaluated':[],'noEditsAt':[locations[x] for x in no_edits]});(dest/'original.yaml').write_bytes(caller.encode());client=None;checks={};responses=[]
  try:
   code,report=m.cli(dest,work);checks['fullCLI']=norm([{k:d[k] for k in ['uri','range','code','message','source','severity','positionKind']} for d in report['diagnostics']])==norm(expected);checks['completeEnvelope']=code==int(bool(expected)) and report['filesAnalyzed']==2 and report['resourcesAnalyzed']==2 and report['analysisComplete'] is True and report['configurationErrors']==[] and report['notEvaluated']==[];client=m.LSP(dest,work)
   for path,text in [(cp,caller),(lp,library)]:
    ds=client.open(path,text);want=[{k:d[k] for k in ['range','code','message','source']}|{'severity':1} for d in expected if d['uri']==path.as_uri()];checks['publication'+path.name]=norm([{k:d[k] for k in ['range','code','message','source','severity']} for d in ds])==norm(want)
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

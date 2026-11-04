#!/usr/bin/env python3
"""Fixed 3.6.0 inherited workflow parameter facts and exact source identities."""
from pathlib import Path
import importlib.util,json,re,sys,traceback
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('wire',HERE/'wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
BASE='complete domain rules, version acceptance, and resource budgets are not yet implemented'
def strip(text):
 plain='';end=0;spans={}
 for x in re.finditer(r'\[\[(\w+)\|(.*?)\]\](?!\])',text,re.S):
  plain+=text[end:x.start()];a=len(plain);plain+=x[2];spans[x[1]]=(a,len(plain));end=x.end()
 return plain+text[end:],spans
def pos(text,offset):
 head=text[:offset];return {'line':head.count('\n'),'character':len(head.rsplit('\n',1)[-1].encode('utf-16-le'))//2}
def norm(rows):return sorted(json.dumps(x,sort_keys=True) for x in rows)
def caller(label='a',argument=False,kind='Workflow'):
 args=('  arguments: {parameters: [{name: [['+label+'Decl|message]], value: caller}]}\n') if argument else ''
 text='apiVersion: argoproj.io/v1alpha1\nkind: '+kind+'\nmetadata: {name: '+label+', namespace: argo, annotations: {note: "{{workflow.parameters.[['+label+'Use|message]]}}"}}\nspec:\n  workflowTemplateRef: {name: [['+label+'Ref|library]]}\n'+args
 if kind=='CronWorkflow':text=text.replace('spec:\n','spec:\n  schedule: "* * * * *"\n  workflowSpec:\n').replace('  workflowTemplateRef:','    workflowTemplateRef:').replace('  arguments:','    arguments:')
 return text
def library(argument=True,kind='WorkflowTemplate'):
 return 'apiVersion: argoproj.io/v1alpha1\nkind: '+kind+'\nmetadata: {name: library, namespace: argo}\nspec:\n  entrypoint: main\n'+('  arguments: {parameters: [{name: [[baseDecl|message]], value: base}]}\n' if argument else '')+'  templates:\n    - name: main\n      container: {image: alpine, args: ["{{workflow.parameters.[[bodyUse|message]]}}"]}\n'
CASES=[]
def add(name,files,queries,diags=(),recovery=None):CASES.append((name,files,queries,list(diags),recovery))
# Query tuples: source token, exact definitions, references without/with declaration.
def q(tag,defs,refs):return (tag,defs,refs,[] if refs is None else refs+defs)
def ambiguous(tag,defs):return (tag,defs,[],[])
for kind in ['WorkflowTemplate','ClusterWorkflowTemplate']:
 add('independent-'+kind,{'library.yaml':library(False,kind)},[ambiguous('bodyUse',[])])
add('inherited-base',{'caller.yaml':caller(),'library.yaml':library()},[q('aUse',['baseDecl'],['aUse','bodyUse']),q('bodyUse',['baseDecl'],['aUse','bodyUse']),q('baseDecl',['baseDecl'],['aUse','bodyUse'])])
add('caller-override',{'caller.yaml':caller(argument=True),'library.yaml':library()},[q('aUse',['aDecl'],['aUse']),ambiguous('bodyUse',['baseDecl','aDecl']),q('aDecl',['aDecl'],['aUse']),q('baseDecl',['baseDecl'],[])])
add('caller-provided',{'caller.yaml':caller(argument=True),'library.yaml':library(False)},[q('aUse',['aDecl'],['aUse','bodyUse']),q('bodyUse',['aDecl'],['aUse','bodyUse']),q('aDecl',['aDecl'],['aUse','bodyUse'])])
add('two-callers-one-base',{'caller.yaml':caller(),'other.yaml':caller('b'),'library.yaml':library()},[q('bodyUse',['baseDecl'],['aUse','bUse','bodyUse']),q('aUse',['baseDecl'],['aUse','bUse','bodyUse']),q('baseDecl',['baseDecl'],['aUse','bUse','bodyUse'])])
add('two-callers-distinct-origins',{'caller.yaml':caller(argument=True),'other.yaml':caller('b',True),'library.yaml':library(False)},[ambiguous('bodyUse',['aDecl','bDecl']),q('aUse',['aDecl'],['aUse']),q('bUse',['bDecl'],['bUse']),q('aDecl',['aDecl'],['aUse'])])
missing=('bRef','WF_REFERENCE_SCOPE','workflow parameter message is not declared for template main in workflowTemplateRef library','Error','exact')
add('caller-context-missing',{'caller.yaml':caller(argument=True),'other.yaml':caller('b'),'library.yaml':library(False)},[ambiguous('bodyUse',['aDecl']),q('aUse',['aDecl'],['aUse'])],[missing],('other.yaml',caller('b',True)))
add('template-name-only-override',{'caller.yaml':caller(argument=True,kind='WorkflowTemplate').replace('value: caller','description: supplied later'),'library.yaml':library()},[q('aUse',['aDecl'],['aUse']),ambiguous('bodyUse',['baseDecl','aDecl'])])
exitlib=library().replace('  entrypoint: main\n','  entrypoint: main\n  onExit: cleanup\n')+'    - name: cleanup\n      container: {image: alpine, args: ["{{workflow.parameters.[[exitUse|message]]}}"]}\n'
add('exit-merged-globals',{'caller.yaml':caller(),'library.yaml':exitlib},[q('exitUse',['baseDecl'],['aUse','bodyUse','exitUse']),q('baseDecl',['baseDecl'],['aUse','bodyUse','exitUse'])])
duplicate=library().replace('value: base}]}','value: base}, {name: [[encodedDecl|"mess\\u0061ge"]], value: duplicate}]}')
add('encoded-duplicate-base',{'caller.yaml':caller(),'library.yaml':duplicate},[ambiguous('aUse',['baseDecl']),ambiguous('bodyUse',['baseDecl']),ambiguous('baseDecl',['baseDecl'])],[('encodedDecl','WF_DUPLICATE_SYMBOL','duplicate parameter or artifact message','Error','exact')])
derived=library().replace('metadata: {name: library, namespace: argo}','metadata: {name: library, namespace: argo, annotations: {seed: &message message}}').replace('[[baseDecl|message]]','*message')
add('derived-base',{'caller.yaml':caller(),'library.yaml':derived},[ambiguous('aUse',[]),ambiguous('bodyUse',[])])
add('namespace-isolation',{'caller.yaml':caller(),'library.yaml':library().replace('namespace: argo','namespace: other')},[ambiguous('aUse',[]),q('bodyUse',['baseDecl'],['bodyUse'])],[('aRef','WF_TEMPLATE_REF_UNRESOLVED','external template resource is not resolved: library','Error','exact')])
add('cron-inherited-crlf',{'caller.yaml':caller(kind='CronWorkflow').replace('note: "','note: "\U0001f600 ').replace('\n','\r\n'),'library.yaml':library()},[q('aUse',['baseDecl'],['aUse','bodyUse']),q('bodyUse',['baseDecl'],['aUse','bodyUse'])])
# Whole-resource uncertainty cannot be collapsed to an arbitrary parameter origin.
add('ambiguous-whole-resource',{'caller.yaml':caller(),'library.yaml':library(),'duplicate.yaml':library().replace('baseDecl','secondDecl').replace('bodyUse','secondUse').replace('workflow.parameters.[[secondUse|message]]','workflow.parameters.[[secondUse|unprovided]]')},[ambiguous('aUse',['baseDecl','secondDecl'])],[('aRef','WF_TEMPLATE_REF_AMBIGUOUS','multiple external template resources match library','Warning','exact')])
add('dynamic-whole-own-parameter',{'caller.yaml':caller(argument=True).replace('[[aRef|library]]','[[aRef|"{{workflow.parameters.target}}"]]'),'library.yaml':library(False)},[q('aUse',['aDecl'],['aUse']),ambiguous('bodyUse',[])],[('aRef','WF_DYNAMIC_REFERENCE','dynamic external reference is not statically resolved','Information','exact')])
# This adjacent key/value oracle was authored after the initial implementation.
key_body=library(False).replace('      container: {image: alpine, args: ["{{workflow.parameters.[[bodyUse|message]]}}"]}', '      metadata:\n        annotations:\n          "{{workflow.parameters.[[bodyUse|message]]}}": retained\n      container: {image: alpine}')
add('caller-context-key-missing',{'caller.yaml':caller(argument=True),'other.yaml':caller('b'),'library.yaml':key_body},[ambiguous('bodyUse',['aDecl']),q('aUse',['aDecl'],['aUse'])],[missing],('other.yaml',caller('b',True)))
# Independent oracle fixed before this probe: the aggregate JSON global exists
# only when the merged argument parameter collection is nonempty.
plain_caller = 'apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: caller, namespace: argo}\nspec:\n  workflowTemplateRef: {name: [[aRef|library]]}\n'
json_library = library(False).replace('[[bodyUse|message]]', '[[bodyUse|json]]')
for name, arguments in [('json-empty', ''), ('json-seed', '  arguments: {parameters: [{name: seed, value: supplied}]}\n')]:
 diags = [] if arguments else [('aRef', 'WF_REFERENCE_SCOPE', 'workflow parameter json is not declared for template main in workflowTemplateRef library', 'Error', 'exact')]
 add(name, {'caller.yaml': plain_caller + arguments, 'library.yaml': json_library}, [('bodyUse', [], [], [])], diags)
# Plain aggregate availability is distinct from a named parameter and JSON alias.
for name, arguments in [('plain-aggregate-empty', ''), ('plain-aggregate-seed', '  arguments: {parameters: [{name: seed, value: supplied}]}\n')]:
 plain_library = library(False).replace('workflow.parameters.[[bodyUse|message]]', 'workflow.[[bodyUse|parameters]]')
 diags = [] if arguments else [('aRef', 'WF_REFERENCE_SCOPE', 'workflow parameters aggregate is not available for template main in workflowTemplateRef library', 'Error', 'exact')]
 add(name, {'caller.yaml': plain_caller + arguments, 'library.yaml': plain_library}, [('bodyUse', [], [], [])], diags)
# Independent audit pairs preserve the accepted message identities with only
# YAML parameter names changed to json; source tags and query sets are unchanged.
add('inherited-base-json', {'caller.yaml': caller().replace('message', 'json'), 'library.yaml': library().replace('message', 'json')}, [q('aUse',['baseDecl'],['aUse','bodyUse']),q('bodyUse',['baseDecl'],['aUse','bodyUse']),q('baseDecl',['baseDecl'],['aUse','bodyUse'])])
add('caller-provided-json', {'caller.yaml': caller(argument=True).replace('message', 'json'), 'library.yaml': library(False).replace('message', 'json')}, [q('aUse',['aDecl'],['aUse','bodyUse']),q('bodyUse',['aDecl'],['aUse','bodyUse']),q('aDecl',['aDecl'],['aUse','bodyUse'])])
if len(sys.argv)>3:CASES=[x for x in CASES if x[0] in sys.argv[3].split(',')]
if not CASES:m.result('empty-selection',{'nonemptySelection':False},{'selection':sys.argv[3:]})
for name,marked,queries,diags,recovery in CASES:
 files={};tags={}
 for filename,value in marked.items():
  text,spans=strip(value);files[filename]=text
  for tag,span in spans.items():tags[tag]=(filename,span)
 dest,work=m.setup(name,files);client=None;checks={};responses={}
 def loc(tag):
  filename,(a,z)=tags[tag];text=files[filename];return {'uri':(work/filename).as_uri(),'range':{'start':pos(text,a),'end':pos(text,z)}}
 expected=[loc(tag)|{'code':code,'message':msg,'severity':severity,'source':'argo-workflows','positionKind':origin} for tag,code,msg,severity,origin in diags]
 envelope={'configurationErrors':[],'filesAnalyzed':len(files),'resourcesAnalyzed':len(files),'analysisComplete':True,'notEvaluated':[]}
 query_expected={tag:{'definition':[loc(x) for x in defs],'referencesFalse':[loc(x) for x in refs],'referencesTrue':[loc(x) for x in inc]} for tag,defs,refs,inc in queries}
 m.save(dest/'expected.json',{'diagnostics':expected,'envelope':envelope,'queries':query_expected,'exitCode':2,'unchangedBytes':True})
 try:
  code,report=m.cli(dest,work);keys=['uri','range','code','message','severity','source','positionKind'];checks['fullCLI']=norm([{k:d[k] for k in keys} for d in report['diagnostics']])==norm(expected);checks['envelope']=code==int(any(d['severity']=='Error' for d in expected)) and {k:report.get(k) for k in envelope}==envelope
  client=m.LSP(dest,work)
  for filename,text in files.items():
   published=client.open(work/filename,text);want=[{k:d[k] for k in ['range','code','message','source']}|{'severity':{'Error':1,'Warning':2,'Information':3}[d['severity']]} for d in expected if d['uri']==(work/filename).as_uri()];checks['publication-'+filename]=norm(published)==norm(want)
  for tag,wants in query_expected.items():
   location=loc(tag)
   for method in ['definition','referencesFalse','referencesTrue']:
    params={'textDocument':{'uri':location['uri']},'position':location['range']['start']}
    if method!='definition':params['context']={'includeDeclaration':method=='referencesTrue'}
    response=client.request('textDocument/'+('definition' if method=='definition' else 'references'),params);responses[tag+'-'+method]=response;checks[tag+'-'+method]='error' not in response and norm(response.get('result',[]))==norm(wants[method])
  if recovery:
   filename,marked_text=recovery;fixed,_=strip(marked_text);checks['recoveryPublication']=client.change(work/filename,fixed,2)==[];original=[{k:d[k] for k in ['range','code','message','source']}|{'severity':{'Error':1,'Warning':2,'Information':3}[d['severity']]} for d in expected if d['uri']==(work/filename).as_uri()];checks['restoredPublication']=norm(client.change(work/filename,files[filename],3))==norm(original)
  checks['bytes']=all((work/f).read_bytes()==t.encode() for f,t in files.items());m.result(name,checks,{'responses':responses,'cli':report})
 except Exception:m.result(name,checks|{'completed':False},{'error':traceback.format_exc(),'responses':responses})
 finally:
  if client:client.close()
m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(Path(__file__)),'helperSha256':m.sha(HERE/'wf-audit-gaps.py'),'caseCount':len(CASES),'python':sys.version,'inputs':{str(p.relative_to(m.OUT)):m.sha(p) for p in m.OUT.rglob('*.yaml')}})
sys.exit(0 if len(m.RESULTS)==len(CASES) and all(x['pass'] for x in m.RESULTS) else 1)

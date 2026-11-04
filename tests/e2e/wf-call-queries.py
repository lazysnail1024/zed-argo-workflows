#!/usr/bin/env python3
"""Workflow argument completion and inherited onExit identities through CLI/LSP."""
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
WORKFLOW = 'apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: caller, namespace: argo}\nspec:\n'
LIBRARY = 'apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: {name: library, namespace: argo}\nspec:\n'
TEMPLATES = '''  templates:
    - name: [[main|main]]
      container: {image: alpine}
    - name: [[cleanup|cleanup]]
      container: {image: alpine}
    - name: [[alternate|alternate]]
      container: {image: alpine}
'''
REF = '  workflowTemplateRef: {name: [[resource|library]]}\n'
def input_template(name, symbol, tag):
    return '    - name: '+name+'\n      inputs: {parameters: [{name: "[['+tag+'|'+symbol+']]", default: retained}]}\n      container: {image: alpine, args: ["{{inputs.parameters.[['+tag+'Use|'+symbol+']]}}"]}\n'

COMPLETION = [
 {'case':'local-workflow-plain',
  'files':{'caller.yaml':WORKFLOW+'  entrypoint: main\n  parallelism: [[invalid|"oops"]]\n  arguments: {parameters: [{name: [[prefix|tr]], value: hello}]} # untouched\n  templates:\n'+input_template('main','true','input')},
  'label':'true','replacement':'"true"','diagnostics_before_after':[('invalid','SCHEMA_TYPE')],
  'after_definition':['prefix_after','input'],'after_references_false':[],'after_references_true':[]},
 {'case':'local-cron-quoted',
  'files':{'caller.yaml':(WORKFLOW.replace('kind: Workflow','kind: CronWorkflow').replace('spec:\n','spec:\n  schedule: "* * * * *"\n  workflowSpec:\n')+'    entrypoint: main\n    arguments: {parameters: [{name: "[[prefix|nu]]", value: hello}]} # \U0001f600 untouched\n    templates:\n'+'  '+input_template('main','null','input').replace('\n','\n  ').rstrip()+'\n').replace('\n','\r\n')},
  'label':'null','replacement':'null','diagnostics_before_after':[],
  'after_definition':['prefix_after','input'],'after_references_false':[],'after_references_true':[]},
 {'case':'inherited-fallback-plain',
  'files':{'caller.yaml':WORKFLOW+REF+'  arguments: {parameters: [{name: [[prefix|tr]], value: hello}]} # untouched\n',
           'library.yaml':LIBRARY+'  entrypoint: main\n  templates:\n'+input_template('main','true','input')},
  'label':'true','replacement':'"true"','diagnostics_before_after':[],
  'after_definition':['prefix_after','input'],'after_references_false':[],'after_references_true':[]},
 {'case':'inherited-explicit-entrypoint',
  'files':{'caller.yaml':WORKFLOW+REF+'  entrypoint: alternate\n  arguments: {parameters: [{name: [[prefix|tr]], value: hello}]} # untouched\n',
           'library.yaml':LIBRARY+'  entrypoint: main\n  templates:\n'+input_template('main','null','defaultInput')+input_template('alternate','true','selectedInput')},
  'label':'true','replacement':'"true"','diagnostics_before_after':[],
  'after_definition':['prefix_after','selectedInput'],'after_references_false':[],'after_references_true':[]},
 {'case':'local-entry-and-exit-input-roles',
  'files':{'caller.yaml':WORKFLOW+'  entrypoint: main\n  onExit: cleanup\n  arguments: {parameters: [{name: [[prefix|tr]], value: hello}]} # untouched\n  templates:\n'+input_template('main','true','entryInput')+input_template('cleanup','true','exitInput')},
  'label':'true','replacement':'"true"','diagnostics_before_after':[],
  'after_definition':['prefix_after','entryInput','exitInput'],'after_references_false':[],'after_references_true':[]},
 {'case':'local-exit-only-input-role',
  'files':{'caller.yaml':WORKFLOW+'  entrypoint: main\n  onExit: cleanup\n  arguments: {parameters: [{name: [[prefix|tr]], value: hello}]} # untouched\n  templates:\n    - name: main\n      container: {image: alpine}\n'+input_template('cleanup','true','exitInput')},
  'label':'true','replacement':'"true"','diagnostics_before_after':[],
  'after_definition':['prefix_after','exitInput'],'after_references_false':[],'after_references_true':[]},
]

NAVIGATION = [
 {'case':'explicit-exit',
  'files':{'caller.yaml':WORKFLOW+REF+'  onExit: [[exit|cleanup]]\n','library.yaml':LIBRARY+'  entrypoint: [[baseEntry|main]]\n'+TEMPLATES},
  'query':'exit','definition':['cleanup'],'references_false':['exit'],'references_true':['exit','cleanup'],'diagnostics':[]},
 {'case':'explicit-exit-override',
  'files':{'caller.yaml':WORKFLOW+REF+'  onExit: [[exit|alternate]]\n','library.yaml':LIBRARY+'  entrypoint: main\n  onExit: [[baseExit|cleanup]]\n'+TEMPLATES},
  'query':'exit','definition':['alternate'],'references_false':['exit'],'references_true':['exit','alternate'],
  'other_query':'cleanup','other_references_false':['baseExit'],'other_references_true':['baseExit','cleanup'],'diagnostics':[]},
 {'case':'base-exit-fallback-source',
  'files':{'caller.yaml':WORKFLOW+REF,'other.yaml':WORKFLOW.replace('name: caller','name: other')+REF.replace('[[resource|library]]','library'),
           'library.yaml':LIBRARY+'  entrypoint: main\n  onExit: [[baseExit|cleanup]]\n'+TEMPLATES},
  'query':'baseExit','definition':['cleanup'],'references_false':['baseExit'],'references_true':['baseExit','cleanup'],
  'absent_caller_exit_has_no_location':True,'diagnostics':[]},
 {'case':'encoded-duplicate-exit',
  'files':{'caller.yaml':WORKFLOW+REF+'  onExit: [[exit|cleanup]]\n','library.yaml':LIBRARY+'  entrypoint: main\n'+TEMPLATES+'    - name: [[duplicate|"clean\\u0075p"]]\n      container: {image: alpine}\n'},
  'query':'exit','definition':['cleanup'],'references_false':[],'references_true':[],
  'diagnostics':[('duplicate','WF_DUPLICATE_SYMBOL','duplicate local template cleanup','Error','exact')]},
 {'case':'duplicate-library-exit',
  'files':{'caller.yaml':WORKFLOW+REF+'  onExit: [[exit|cleanup]]\n',
           'library.yaml':LIBRARY+'  entrypoint: main\n  onExit: [[baseExit|cleanup]]\n'+TEMPLATES,
           'duplicate.yaml':LIBRARY+'  entrypoint: main\n  onExit: [[otherBaseExit|cleanup]]\n'+TEMPLATES.replace('[[main|main]]','main').replace('[[cleanup|cleanup]]','[[otherCleanup|cleanup]]').replace('[[alternate|alternate]]','alternate')},
  'query':'exit','definition':['cleanup','otherCleanup'],'references_false':[],'references_true':[],
  'local_declaration_references':{'cleanup':['baseExit'],'otherCleanup':['otherBaseExit']},
  'diagnostics':[('resource','WF_TEMPLATE_REF_AMBIGUOUS','multiple external template resources match library','Warning','exact')]},
 {'case':'derived-exit-and-exact-sibling',
  'files':{'caller.yaml':WORKFLOW.replace('metadata: {name: caller, namespace: argo}','metadata: {name: caller, namespace: argo, annotations: {exit: &exit cleanup}}')+REF+'  onExit: [[derived|*exit]]\n',
           'other.yaml':WORKFLOW.replace('name: caller','name: other')+REF.replace('[[resource|library]]','library')+'  onExit: [[exact|alternate]]\n',
           'library.yaml':LIBRARY+'  entrypoint: main\n'+TEMPLATES},
  'query':'derived','definition':[],'references_false':[],'references_true':[],
  'other_query':'exact','other_definition':['alternate'],'other_references_false':['exact'],'other_references_true':['exact','alternate'],'diagnostics':[]},
]


# The explicit exit case also proves all independently known argument roles.
for case in NAVIGATION:
 if case['case']=='explicit-exit':
  case['files']={'caller.yaml':WORKFLOW.replace('namespace: argo}', 'namespace: argo, annotations: {note: "{{workflow.parameters.[[globalUse|message]]}}"}}')+REF+'  onExit: [[exit|cleanup]]\n  arguments: {parameters: [{name: [[argument|message]], value: supplied}]}\n',
                 'library.yaml':LIBRARY+'  entrypoint: [[baseEntry|main]]\n  templates:\n'+input_template('[[main|main]]','message','entryInput')+input_template('[[cleanup|cleanup]]','message','exitInput')}
  case['argumentRoles']=True

CASES=[(True,c) for c in COMPLETION]+[(False,c) for c in NAVIGATION]
if len(sys.argv)>3:CASES=[c for c in CASES if c[1]['case'] in sys.argv[3].split(',')]
if not CASES:m.result('empty-selection',{'nonemptySelection':False},{'selection':sys.argv[3:]})
for completing,case in CASES:
 name=case['case'];marked=dict(case['files']);files={};tags={}
 def decode():
  tags.clear()
  for filename,value in marked.items():
   text,spans=strip(value);files[filename]=text
   for tag,span in spans.items():tags[tag]=(filename,span)
 decode();original=dict(files);dest,work=m.setup(name,original);client=None;checks={};responses={}
 def loc(tag,root=None):
  filename,(a,z)=tags[tag];text=files[filename];return {'uri':((root or work)/filename).as_uri(),'range':{'start':pos(text,a),'end':pos(text,z)}}
 def diagnostics(root=None):
  rows=[]
  for d in case.get('diagnostics',case.get('diagnostics_before_after',[])):
   if d[1]=='SCHEMA_TYPE':tag,code=d;msg="at '/spec/parallelism': got string, want integer";severity='Error';origin='exact'
   else:tag,code,msg,severity,origin=d
   rows.append(loc(tag,root)|{'code':code,'message':msg,'severity':severity,'source':'argo-workflows','positionKind':origin})
  return rows
 envelope={'configurationErrors':[],'filesAnalyzed':len(files),'resourcesAnalyzed':len(files),'analysisComplete':True,'notEvaluated':[]}
 def cli_check(label,folder):
  code,report=m.cli(dest,folder,label);keys=['uri','range','code','message','severity','source','positionKind'];checks[label+'-diagnostics']=norm([{k:d[k] for k in keys} for d in report['diagnostics']])==norm(diagnostics(folder));checks[label+'-envelope']=code==int(any(d['severity']=='Error' for d in diagnostics(folder))) and {k:report.get(k) for k in envelope}==envelope;responses[label]=report
 def publication(filename,rows):
  want=[{k:d[k] for k in ['range','code','message','source']}|{'severity':{'Error':1,'Warning':2,'Information':3}[d['severity']]} for d in diagnostics() if d['uri']==(work/filename).as_uri()]
  return norm(rows)==norm(want)
 def query(tag,method,expected,include=None,label=None):
  selected=loc(tag);params={'textDocument':{'uri':selected['uri']},'position':selected['range']['start']}
  if include is not None:params['context']={'includeDeclaration':include}
  response=client.request('textDocument/'+method,params);key=(label or tag)+'-'+method+('' if include is None else str(include));responses[key]=response;checks[key]='error' not in response and norm(response.get('result',[]))==norm([loc(x) for x in expected])
 def refs(tag,without,with_decl):
  query(tag,'references',without,False);query(tag,'references',with_decl,True)
 before_expected={'diagnostics':diagnostics(),'envelope':envelope,'exitCode':int(any(d['severity']=='Error' for d in diagnostics())),'case':case,'originalBytesUnchanged':True};m.save(dest/'expected.json',before_expected)
 try:
  cli_check('before',work);client=m.LSP(dest,work)
  for filename,text in files.items():checks['publication-'+filename]=publication(filename,client.open(work/filename,text))
  if completing:
   selected=loc('prefix');params={'textDocument':{'uri':selected['uri']},'position':selected['range']['end']};response=client.request('textDocument/completion',params);responses['completion']=response;items=response.get('result') or []
   checks['completionProtocol']='error' not in response;checks['completionLabels']=[x['label'] for x in items]==[case['label']]
   if len(items)!=1:checks['appliedEdit']=False
   else:
    item=items[0];edit=item.get('textEdit',{});checks['editRange']=edit.get('range')==selected['range'];checks['itemKind']=item.get('kind')==6 and item.get('insertTextFormat')==1
    sourcefile='library.yaml' if 'library.yaml' in files else 'caller.yaml';kind='WorkflowTemplate' if sourcefile=='library.yaml' else ('CronWorkflow' if name=='local-cron-quoted' else 'Workflow');checks['originMetadata']=all(x in item.get('detail','') for x in [kind,'namespace: argo','source: '+(work/sourcefile).as_uri(),'Argo Workflows 3.6.0'])
    filename,(a,z)=tags['prefix'];choices=[case['replacement']] if case['replacement']=='null' else ['"true"',"'true'"];expected_bytes=[files[filename][:a]+v+files[filename][z:] for v in choices];m.save(dest/'edit-expected.json',{'range':selected['range'],'allowedWholeBytes':expected_bytes})
    actual=m.apply(files[filename],{'documentChanges':[{'textDocument':{'uri':selected['uri'],'version':1},'edits':[edit]}]},selected['uri'],1);checks['appliedBytes']=actual in expected_bytes;(dest/'actual-edited.yaml').write_bytes(actual.encode())
    replacement=edit['newText'];content=case['label'];marked_replacement=('[[prefix_after|'+content+']]') if replacement==content else replacement[0]+'[[prefix_after|'+content+']]'+replacement[-1]
    marked[filename]=re.sub(r'\[\[prefix\|.*?\]\]',lambda _:marked_replacement,marked[filename]);decode();checks['independentEditedBytes']=files[filename]==actual
    checks['editedPublication']=publication(filename,client.change(work/filename,actual,2))
    edited_dest,edited_work=m.setup(name+'-edited',files);cli_check('after',edited_work)
    query('prefix_after','definition',case['after_definition']);refs('prefix_after',[],[])
    # Input uses keep the overlapping argument reference in their own role.
    for tag in case['after_definition']:
     if tag=='prefix_after':continue
     use=tag+'Use';query(use,'definition',[tag]);refs(use,['prefix_after',use],['prefix_after',use,tag])
    checks['renameOverlapRefused']=('error' in client.request('textDocument/prepareRename',{'textDocument':{'uri':loc('prefix_after')['uri']},'position':loc('prefix_after')['range']['start']}))
  else:
   tag=case['query'];query(tag,'definition',case['definition']);refs(tag,case['references_false'],case['references_true'])
   for declaration in case['definition']:
    if 'local_declaration_references' in case:without=case['local_declaration_references'][declaration];with_decl=without+[declaration]
    elif name=='encoded-duplicate-exit':without=[];with_decl=[]
    else:without=case['references_false'];with_decl=case['references_true']
    refs(declaration,without,with_decl)
   if 'other_query' in case:
    other=case['other_query']
    if 'other_definition' in case:query(other,'definition',case['other_definition'])
    refs(other,case['other_references_false'],case['other_references_true'])
   if case.get('argumentRoles'):
    query('argument','definition',['argument','entryInput','exitInput']);refs('argument',[],[])
    query('globalUse','definition',['argument']);refs('globalUse',['globalUse'],['globalUse','argument'])
    for declaration in ['entryInput','exitInput']:
     use=declaration+'Use';query(use,'definition',[declaration]);refs(use,['argument',use],['argument',use,declaration])
   if 'main' in tags and 'baseEntry' in tags:query('baseEntry','definition',['main']);refs('main',['baseEntry'],['baseEntry','main'])
  checks['originalBytes']=all((work/f).read_bytes()==t.encode() for f,t in original.items());m.result(name,checks,{'responses':responses})
 except Exception:m.result(name,checks|{'completed':False},{'error':traceback.format_exc(),'responses':responses})
 finally:
  if client:client.close()
m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(Path(__file__)),'helperSha256':m.sha(HERE/'wf-audit-gaps.py'),'caseCount':len(CASES),'python':sys.version,'inputs':{str(p.relative_to(m.OUT)):m.sha(p) for p in m.OUT.rglob('*.yaml')}})
sys.exit(0 if len(m.RESULTS)==len(CASES) and all(x['pass'] for x in m.RESULTS) else 1)

#!/usr/bin/env python3
"""Audit remediation behavioral checks through real CLI and stdio processes."""
import importlib.util,json,pathlib,sys,time,traceback
p=pathlib.Path(__file__).with_name('wf-audit-gaps.py'); spec=importlib.util.spec_from_file_location('wire',p); m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
plain='''apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata: {name: demo, namespace: argo}
spec:
  entrypoint: main
  templates:
    - name: main
      container: {image: alpine, args: ["ok"]}
'''
def case(name,fn):
 try: fn()
 except Exception as e:m.result(name,{'completed':False},{'error':repr(e),'traceback':traceback.format_exc()})
def expressions():
 for label,arg in [('plain','ok'),('complete','{{= 1 + 1 }}'),('partial','{{= 1 + }}')]:
  text=plain.replace('  entrypoint:', '  parallelism: not-a-number\n  entrypoint:').replace('"ok"',json.dumps(arg));dest,work=m.setup('expression-'+label,{'a.yaml':text});code,data=m.cli(dest,work);c=m.LSP(dest,work)
  try:
   ds=c.open(work/'a.yaml',text);m.result('expression-'+label,{'cli_type':any(d['code']=='SCHEMA_TYPE' for d in data['diagnostics']),'lsp_type':any(d['code']=='SCHEMA_TYPE' for d in ds),'partial_visible':label!='partial' or any(d['code'].startswith('WF_ANALYSIS') or d['code']=='WF_EXPRESSION_SYNTAX' for d in ds),'partial_range':label!='partial' or any(d['range']['start']==m.pos(text,'"{{=') for d in ds),'completion':data['analysisComplete']==(label!='partial')},{'cli':data,'lsp':ds})
  finally:c.close()
def metadata():
 for group in ['labels','annotations']:
  for key in ['inline','templateRef','globalName']:
   text=plain.replace('metadata: {name: demo, namespace: argo}',f'metadata:\n  name: demo\n  namespace: argo\n  {group}: {{{key}: "true"}}');dest,work=m.setup(group+'-'+key,{'a.yaml':text});c=m.LSP(dest,work);path=work/'a.yaml'
   try:
    c.open(path,text);r=c.request('textDocument/rename',{'textDocument':{'uri':path.as_uri()},'position':m.pos(text,'name: main',offset=7),'newName':'renamed'});actual=m.apply(text,r['result'],path.as_uri(),1) if 'result' in r else None;m.result(group+'-'+key,{'rename_exact':actual==text.replace('entrypoint: main','entrypoint: renamed').replace('name: main','name: renamed')},{'response':r})
   finally:c.close()
def completion():
 base=m.BASE.replace('name: base,','name: library,').replace('work','worker')
 for leaf,prefix,label in [('name','lib','library'),('template','wo','worker')]:
  for quote in ['',"'",'"']:
   for suffix in ['', 'WRONG']:
    fragment='{name: library, template: worker}'.replace(leaf+': '+('library' if leaf=='name' else 'worker'),leaf+': '+quote+prefix+'<CURSOR>'+suffix+quote)
    marked=m.CALLER.replace('{name: base, template: work}',fragment);text=marked.replace('<CURSOR>','');name='completion-'+leaf+'-'+str(ord(quote) if quote else 0)+'-'+str(bool(suffix));dest,work=m.setup(name,{'a.yaml':text,'b.yaml':base});c=m.LSP(dest,work);path=work/'a.yaml'
    try:
     c.open(path,text);r=c.request('textDocument/completion',{'textDocument':{'uri':path.as_uri()},'position':m.pos(marked,'<CURSOR>')});items=[i for i in r.get('result',[]) if i['label']==label];actual=m.apply(text,{'documentChanges':[{'textDocument':{'uri':path.as_uri(),'version':1},'edits':[items[0]['textEdit']]}]},path.as_uri(),1) if len(items)==1 else '';m.result(name,{'candidate':len(items)==1,'suffix_removed':'WRONG' not in actual and bool(actual),'correct_value':(leaf+': '+json.dumps(label)) in actual},{'response':r,'actual':actual})
    finally:c.close()
def completion_edges():
 base=m.BASE.replace('name: base,','name: library,').replace('work','worker')
 for index,(leaf,value,label) in enumerate([('name','<CURSOR>','library'),('name',"'<CURSOR>'",'library'),('name','"<CURSOR>"','library'),('template','<CURSOR>','worker'),('template',"'<CURSOR>'",'worker'),('template','"<CURSOR>"','worker'),('name','lib<CURSOR>WRONG','library'),('template','wo<CURSOR>WRONG','worker')]):
  fragment='{note: "\u4e2d\U0001f40c", name: library, template: worker}'.replace(leaf+': '+('library' if leaf=='name' else 'worker'),leaf+': '+value)
  marked=m.CALLER.replace('{name: base, template: work}',fragment);text=marked.replace('<CURSOR>','');dest,work=m.setup('edge-'+str(index),{'a.yaml':text,'b.yaml':base});path=work/'a.yaml';c=m.LSP(dest,work)
  try:
   c.open(path,text);r=c.request('textDocument/completion',{'textDocument':{'uri':path.as_uri()},'position':m.pos(marked,'<CURSOR>')});items=[x for x in r.get('result',[]) if x['label']==label];actual=m.apply(text,{'documentChanges':[{'textDocument':{'uri':path.as_uri(),'version':1},'edits':[items[0]['textEdit']]}]},path.as_uri(),1) if len(items)==1 else '';m.result('completion-edge-'+str(index),{'candidate':len(items)==1,'exact_value':(leaf+': '+json.dumps(label)) in actual,'old_suffix_removed':'WRONG' not in actual and bool(actual),'unicode_preserved':'\u4e2d\U0001f40c' in actual},{'response':r,'actual':actual})
  finally:c.close()
def unavailable():
 text=plain.replace('entrypoint','entrypont');dest,work=m.setup('unavailable',{'a.yaml':text});c=m.LSP(dest,work);path=work/'a.yaml';config=work/'.argo-workflows-ls.yaml'
 try:
  before=c.open(path,text);c.send('textDocument/didOpen',{'textDocument':{'uri':config.as_uri(),'version':1,'languageId':'yaml','text':m.CONFIG.replace('3.6.0','99.99.99')}});ds=c.diagnostics(path.as_uri(),1);time.sleep(.2);configds=[x['params']['diagnostics'] for x in c.received if x.get('method')=='textDocument/publishDiagnostics' and x['params']['uri']==config.as_uri()];m.result('invalid-config-visible',{'workflow_status':any(d['code']=='WF_ANALYSIS_UNAVAILABLE' for d in ds),'config_status':any(configds)},{'diagnostics':ds,'config':configds});c.send('textDocument/didChange',{'textDocument':{'uri':config.as_uri(),'version':2},'contentChanges':[{'text':m.CONFIG}]});recovered=c.diagnostics(path.as_uri(),1);ds=c.change(path,text+'#'+('x'*(5*1024*1024)),2);m.result('oversize-visible',{'unavailable':any(d['code']=='WF_ANALYSIS_UNAVAILABLE' for d in ds),'config_recovered':any(d['code']=='SCHEMA_UNKNOWN_FIELD' for d in recovered)},{'diagnostics':ds});ds=c.change(path,text,3);m.result('input-recovery',{'schema_restored':any(d['code']=='SCHEMA_UNKNOWN_FIELD' for d in ds),'status_cleared':not any(d['code']=='WF_ANALYSIS_UNAVAILABLE' for d in ds)})
 finally:c.close()
def action():
 text=plain.replace('  entrypoint:', '  paralelism: 1\n  entrypoint:');config=m.CONFIG+'scopes:\n  - name: alpha\n    include: ["alpha/**"]\n  - name: beta\n    include: ["beta/**"]\n';dest,work=m.setup('actions',{'alpha/a.yaml':text,'beta/b.yaml':plain},config);c=m.LSP(dest,work);path=work/'alpha/a.yaml';other=work/'beta/b.yaml'
 try:
  ds=c.open(path,text);c.open(other,plain);pos=m.pos(text,'paralelism');r=c.request('textDocument/codeAction',{'textDocument':{'uri':path.as_uri()},'range':{'start':pos,'end':pos},'context':{'diagnostics':ds}});a=next(x for x in r['result'] if 'parallelism' in x['title']);before=c.request('codeAction/resolve',a);c.change(other,plain+'# unrelated\n',2);after=c.request('codeAction/resolve',a);m.result('action-unrelated-scope',{'baseline':bool(before.get('result',{}).get('edit')),'still_valid':bool(after.get('result',{}).get('edit'))},{'response':after});time.sleep(31);after=c.request('codeAction/resolve',a);m.result('action-reconciliation',{'still_valid':bool(after.get('result',{}).get('edit'))},{'response':after});c.change(path,text+'# changed\n',2);after=c.request('codeAction/resolve',a);m.result('action-stale',{'refused':'error' in after},{'response':after})
 finally:c.close()
for name,fn in [('expressions',expressions),('metadata',metadata),('completion',completion),('completion_edges',completion_edges),('unavailable',unavailable),('action',action)]:
 if len(sys.argv)<4 or name in sys.argv[3:]:case(name,fn)
m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'python':sys.version,'inputs':{str(p.relative_to(m.OUT)):m.sha(p) for p in m.OUT.rglob('*') if p.is_file() and '/input/' in str(p)},'cases':len(m.RESULTS),'passed':sum(x['pass'] for x in m.RESULTS)})
sys.exit(0 if m.RESULTS and all(x['pass'] for x in m.RESULTS) else 1)

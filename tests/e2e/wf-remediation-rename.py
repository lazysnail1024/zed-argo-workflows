#!/usr/bin/env python3
"""Local template closure remains independent of a static external call."""
import importlib.util,pathlib,sys,json
p=pathlib.Path(__file__).with_name('wf-audit-gaps.py');spec=importlib.util.spec_from_file_location('wire',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
base=m.REN_DAG.replace('    - name: work\n','          - name: external\n            templateRef: {name: base, template: work}\n    - name: work\n')
for name,text,callee,allowed in [
 ('independent',base,m.BASE,True),
 ('namespace',base,m.BASE.replace('namespace: argo','namespace: other'),True),
 ('public-template',m.BASE,m.BASE,False),
 ('dynamic-external',base.replace('name: base, template:', 'name: "{{workflow.name}}", template:'),m.BASE,False),
 ('inherited',base.replace('  entrypoint: main','  workflowTemplateRef: {name: base}\n  entrypoint: main'),m.BASE,False),
 ('inline',base.replace('templateRef: {name: base, template: work}','inline: {container: {image: alpine}}'),m.BASE,False),
 ('alias',base.replace('container: {image: alpine, command: [echo], args: [work]}','container: &shared {image: alpine, command: [echo], args: [work]}').replace('container: {image: alpine, command: [echo], args: [cleanup]}','container: *shared'),m.BASE,False),
]:
 dest,work=m.setup(name,{'a.yaml':text,'library.yaml':callee});c=m.LSP(dest,work);path=work/'a.yaml';needle='name: work' if name=='public-template' else 'name: cleanup'
 try:
  before=c.open(path,text);r=c.request('textDocument/rename',{'textDocument':{'uri':path.as_uri()},'position':m.pos(text,needle,offset=7),'newName':'tidy'});checks={'policy':('result' in r and isinstance(r['result'],dict) and 'error' not in r) if allowed else (r.get('error',{}).get('code')==-32602 and 'result' not in r)}
  if allowed and 'result' in r:
   actual=m.apply(text,r['result'],path.as_uri(),1);expected=text.replace('onExit: cleanup','onExit: tidy').replace('name: cleanup','name: tidy');after=c.change(path,actual,2);checks.update({'exact_local_closure':actual==expected,'no_new_errors':len([d for d in after if d['severity']==1])<=len([d for d in before if d['severity']==1]),'external_untouched':(work/'library.yaml').read_text()==callee});restored=c.change(path,text,3);again=c.request('textDocument/rename',{'textDocument':{'uri':path.as_uri()},'position':m.pos(text,needle,offset=7),'newName':'tidy'});checks['undo_restores']=restored==before and 'result' in again and m.apply(text,again['result'],path.as_uri(),3)==expected
  m.result(name,checks,{'response':r})
 finally:c.close()
m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'inputs':{str(p.relative_to(m.OUT)):m.sha(p) for p in m.OUT.rglob('*.yaml')}})
sys.exit(0 if len(m.RESULTS)==7 and all(x['pass'] for x in m.RESULTS) else 1)

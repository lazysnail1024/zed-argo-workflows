#!/usr/bin/env python3
"""Explicit synthetic query contract; never upstream Workflows acceptance."""
import pathlib,sys,json,importlib.util
root=pathlib.Path(__file__).resolve().parents[2];spec=importlib.util.spec_from_file_location('client',root/'tests/e2e/wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
head='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: synthetic}\nspec:\n';mark='<CURSOR>';rows=[('via-ref','viaRef:\n    leaf: ',['x','y']),('conjunction','conjunction:\n    choice: ',['b']),('alternative','alternative:\n    choice: ',['b','d']),('combined','combined:\n    choice: ',['b','c']),('inclusive','inclusiveChoice: ',['a','b','c']),('exclusive','exclusiveChoice: ',['a','c']),('duplicate-exclusive','duplicateExclusive: ',[]),('duplicate-inclusive','duplicateInclusive: ',['a','b']),('referenced-exclusive','referencedExclusive: ',['a','c']),('ref-sibling','refSibling: ',['b']),('default-not-enum','defaultOnly: ',[]),('active-discriminator','branch:\n    mode: g',['chart','git']),('wrapped-discriminator','wrappedBranch:\n    mode: ',['chart','git'])]
rows += [('object-exclusive','objectExclusive:\n    choice: ',['a','c']),('object-duplicate-exclusive','objectDuplicateExclusive:\n    choice: ',[]),('object-duplicate-inclusive','objectDuplicateInclusive:\n    choice: ',['a','b'])]
rows.append(('sequence-enum','sequenceValues:\n    - r',['red','blue']))
for name,body,expected in rows:
 text=head+'  '+body+'\n';dest,work=m.setup(name,{'case.yaml':text});path=work/'case.yaml';client=m.LSP(dest,work)
 try:
  client.open(path,text);r=client.request('textDocument/completion',{'textDocument':{'uri':path.as_uri()},'position':m.pos(text,'\n',occurrence=text.count('\n')-1)});items=r.get('result',[]);labels=[x['label'] for x in items];checks={'exactValues':sorted(labels)==sorted(json.dumps(x) for x in expected),'allExactEdits':all('textEdit' in x for x in items)}
  for i,item in enumerate(items):
   if 'textEdit' not in item:continue
   actual=m.apply(text,{'documentChanges':[{'textDocument':{'uri':path.as_uri(),'version':1},'edits':[item['textEdit']]}]},path.as_uri(),1);path.write_bytes(actual.encode());run=m.command(dest,'actual-'+str(i),[m.BINARY,'check','--root',work,'--format','json']);report=json.loads(run.stdout);checks['actualValueValid-'+str(i)]=run.returncode==2 and report['resourcesAnalyzed']==1 and report['filesAnalyzed']==1 and report['diagnostics']==[] and not report['configurationErrors']
  m.result(name,checks,{'response':r})
 finally:client.close()
for name,body,required,excluded,candidate in [('ref-key','viaRef:\n    le'+mark,['leaf'],[],False),('array-pointer-key','arrayPointer:\n    le'+mark,['leaf'],[],False),('selected-branch','branch:\n    mode: git\n    '+mark,['repo'],['chart','mode'],False),('wrapped-selected-branch','wrappedBranch:\n    mode: chart\n    '+mark,['chart'],['repo','mode'],False),('ambiguous-branch','branch:\n    '+mark,['repo','chart','mode'],[],True)]:
 text=head+'  '+body.replace(mark,'')+'\n';marked=head+'  '+body+'\n';dest,work=m.setup(name,{'case.yaml':text});path=work/'case.yaml';client=m.LSP(dest,work)
 try:
  client.open(path,text);r=client.request('textDocument/completion',{'textDocument':{'uri':path.as_uri()},'position':m.pos(marked,mark)});items=r.get('result',[]);labels={x['label'] for x in items};checks={'required':set(required)<=labels,'excluded':not(set(excluded)&labels),'exactEdits':all('textEdit' in x for x in items),'noRequiredExpansion':all('\n' not in x.get('textEdit',{}).get('newText','') for x in items)}
  if candidate:checks['ambiguousAnnotated']=all('candidate' in x.get('detail','').lower() for x in items if x['label'] in ['repo','chart'])
  m.result(name,checks,{'response':r})
 finally:client.close()
for name,body,needles in [('intersection-hover','typeInter'+mark+'section: x',['Type: `string`']),('union-hover','typeUn'+mark+'ion: x',['integer | string']),('contradiction-hover','typeContra'+mark+'diction: x',['Type: `never`']),('ref-hover','viaRef:\n    le'+mark+'af: x',['Referenced leaf.','string','Default','x']),('array-pointer-hover','arrayPointer:\n    le'+mark+'af: x',['Array pointer leaf.','string']),('default-hover','default'+mark+'Only: x',['seed','Default','string'])]:
 text=head+'  '+body.replace(mark,'')+'\n';dest,work=m.setup(name,{'case.yaml':text});path=work/'case.yaml';client=m.LSP(dest,work)
 try:
  client.open(path,text);r=client.request('textDocument/hover',{'textDocument':{'uri':path.as_uri()},'position':m.pos(head+'  '+body+'\n',mark)});value=(r.get('result') or {}).get('contents',{}).get('value','');m.result(name,{'annotations':all(x in value for x in needles),'noNil':'<nil>' not in value},{'response':r})
 finally:client.close()
m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'syntheticFixtureSha256':m.sha(root/'tests/fixtures/schema-query/contract.json'),'classification':'Synthetic contract only; not upstream Workflows behavior or historical acceptance.'});sys.exit(0 if len(m.RESULTS)==28 and all(x['pass'] for x in m.RESULTS) else 1)

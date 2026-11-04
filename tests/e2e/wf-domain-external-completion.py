#!/usr/bin/env python3
"""Exact external completion edits with namespace, scope and ambiguity controls."""
import pathlib,sys,json,importlib.util
root=pathlib.Path(__file__).resolve().parents[2];spec=importlib.util.spec_from_file_location('wire',root/'tests/e2e/wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
callee='apiVersion: argoproj.io/v1alpha1\nkind: WorkflowTemplate\nmetadata: {name: library, namespace: argo}\nspec:\n  templates:\n    - name: worker\n      container: {image: alpine}\n'
head='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: caller, namespace: argo}\nspec:\n  templates:\n    - name: main\n      dag:\n        tasks:\n          - name: task\n            templateRef: '
for name,fragment,label,expected_fragment,other,duplicate in [
 ('resource','{name: li<CURSOR>, template: worker}', 'library','{name: "library", template: worker}',callee,False),
 ('template','{name: library, template: wo<CURSOR>}', 'worker','{name: library, template: "worker"}',callee,False),
 ('namespace','{name: li<CURSOR>, template: worker}',None,None,callee.replace('namespace: argo','namespace: elsewhere'),False),
 ('ambiguous','{name: li<CURSOR>, template: worker}',None,None,callee,True),
 ('cluster','{name: li<CURSOR>, template: worker, clusterScope: true}','library','{name: "library", template: worker, clusterScope: true}',callee.replace('WorkflowTemplate','ClusterWorkflowTemplate'),False),
]:
 marked=head+fragment+' # keep\n';text=marked.replace('<CURSOR>','');files={'caller.yaml':text,'library.yaml':other}
 if duplicate:files['duplicate.yaml']=other
 dest,work=m.setup(name,files);path=work/'caller.yaml';client=m.LSP(dest,work)
 try:
  client.open(path,text);r=client.request('textDocument/completion',{'textDocument':{'uri':path.as_uri()},'position':m.pos(marked,'<CURSOR>')});items=r.get('result',[]);checks={'noProtocolError':'error' not in r,'exactLabels':[x['label'] for x in items]==([label] if label else [])}
  if label and len(items)==1:
   actual=m.apply(text,{'documentChanges':[{'textDocument':{'uri':path.as_uri(),'version':1},'edits':[items[0]['textEdit']]}]},path.as_uri(),1);expected=head+expected_fragment+' # keep\n';checks['exactBytes']=actual==expected;(dest/'expected.yaml').write_bytes(expected.encode());(dest/'actual.yaml').write_bytes(actual.encode());path.write_bytes(actual.encode());run=m.command(dest,'check',[m.BINARY,'check','--root',work,'--format','json']);report=json.loads(run.stdout);checks['completeDiagnosticsAndCounts']=run.returncode==0 and report['diagnostics']==[] and report['resourcesAnalyzed']==2 and report['filesAnalyzed']==2 and report['analysisComplete'] is True and not report['configurationErrors']
  m.result(name,checks,{'response':r})
 finally:client.close()
m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'inputHashes':{str(p.relative_to(m.OUT)):m.sha(p) for p in m.OUT.rglob('*.yaml')}});sys.exit(0 if len(m.RESULTS)==5 and all(x['pass'] for x in m.RESULTS) else 1)

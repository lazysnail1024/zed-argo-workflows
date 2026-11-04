#!/usr/bin/env python3
"""Domain completion contexts with exact edits and full post-edit CLI controls."""
import pathlib,sys,json,importlib.util
root=pathlib.Path(__file__).resolve().parents[2];spec=importlib.util.spec_from_file_location('wire',root/'tests/e2e/wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
head='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: complete}\nspec:\n  arguments: {parameters: [{name: global, value: hello}]}\n'
worker='''    - name: worker
      inputs:
        parameters: [{name: message, default: hello}]
        artifacts: [{name: file, path: /tmp/in, optional: true}]
      outputs: {parameters: [{name: result, value: hello}]}
      container: {image: alpine}
'''
dag='''  templates:
    - name: main
      dag:
        tasks:
          - name: produce
            template: worker
          - name: consume
            dependencies: [produce]
            template: worker
            arguments: {parameters: [{name: message, value: "VALUE"}]}
'''
rows=[('task',head+dag.replace('VALUE','{{tasks.pro<CURSOR>.outputs.parameters.result}}')+worker,'produce','pro'),('output',head+dag.replace('VALUE','{{tasks.produce.outputs.parameters.res<CURSOR>}}')+worker,'result','res'),('dependency',head+dag.replace('dependencies: [produce]','dependencies: [pro<CURSOR>]').replace('VALUE','hello')+worker,'produce','pro'),('argument',head+dag.replace('name: message, value:','name: mes<CURSOR>, value:').replace('VALUE','hello')+worker,'message','mes')]
for name,expr,label,prefix in [('workflow','{{workflow.parameters.glo<CURSOR>}}','global','glo'),('artifact','{{inputs.artifacts.fi<CURSOR>.path}}','file','fi'),('output-local','{{outputs.parameters.res<CURSOR>}}','result','res')]:
 rows.append((name,head+'  templates:\n'+worker.replace('container: {image: alpine}', 'container: {image: alpine, args: ["'+expr+'"]}'),label,prefix))
for name,marked,label,prefix in rows:
 marked=marked.replace('metadata: {name: complete}','metadata: {name: complete, annotations: {note: "\U0001f600"}}').replace('\n','\r\n');at=marked.index('<CURSOR>');text=marked.replace('<CURSOR>','');expected=text[:at-len(prefix)]+label+text[at:];dest,work=m.setup(name,{'case.yaml':text});path=work/'case.yaml';client=m.LSP(dest,work)
 try:
  client.open(path,text);r=client.request('textDocument/completion',{'textDocument':{'uri':path.as_uri()},'position':m.pos(marked,'<CURSOR>')});items=r.get('result',[]);checks={'exactCandidates':[x['label'] for x in items]==[label]}
  if len(items)==1:
   actual=m.apply(text,{'documentChanges':[{'textDocument':{'uri':path.as_uri(),'version':1},'edits':[items[0]['textEdit']]}]},path.as_uri(),1);checks['exactBytes']=actual==expected;(dest/'expected.yaml').write_bytes(expected.encode());(dest/'actual.yaml').write_bytes(actual.encode());path.write_bytes(actual.encode());run=m.command(dest,'check',[m.BINARY,'check','--root',work,'--format','json']);report=json.loads(run.stdout);checks['fullCLI']=run.returncode==0 and report['diagnostics']==[] and report['resourcesAnalyzed']==1 and report['filesAnalyzed']==1 and report['analysisComplete'] is True and not report['configurationErrors']
  m.result(name,checks,{'response':r})
 finally:client.close()
m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'inputHashes':{str(p.relative_to(m.OUT)):m.sha(p) for p in m.OUT.rglob('*.yaml')}});sys.exit(0 if len(m.RESULTS)==7 and all(x['pass'] for x in m.RESULTS) else 1)

#!/usr/bin/env python3
"""Immutable schema reuse must preserve version changes and cancellation recovery."""
import pathlib,sys,time,json,importlib.util
root=pathlib.Path(__file__).resolve().parents[2];spec=importlib.util.spec_from_file_location('client',root/'tests/e2e/wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
text='apiVersion: argoproj.io/v1alpha1\nkind: CronWorkflow\nmetadata: {name: scheduled}\nspec:\n  schedules: ["0 * * * *"]\n  workflowSpec:\n    entrypoint: main\n    templates:\n      - name: main\n        container: {image: alpine}\n'
config='schemaVersion: 1\ntargetVersion: "3.6.0"\nscopes:\n  - name: older\n    include: ["older/*.yaml"]\n    targetVersion: "3.5.0"\n  - name: newer\n    include: ["newer/*.yaml"]\n    targetVersion: "3.6.0"\n'
dest,work=m.setup('versions',{'older/case.yaml':text,'newer/case.yaml':text},config);client=m.LSP(dest,work);paths=[work/'older/case.yaml',work/'newer/case.yaml'];uris={p.as_uri() for p in paths}
def codes(ds):return {d['code'] for d in ds}
def both():
 found={}
 def collect(frame):
  if frame.get('method')=='textDocument/publishDiagnostics' and frame['params']['uri'] in uris:found[frame['params']['uri']]=frame['params']['diagnostics']
  return len(found)==2
 client.wait(collect);return [found[p.as_uri()] for p in paths]
try:
 old=client.open(paths[0],text);new=client.open(paths[1],text);m.result('two-version-identity',{'olderRequiresSchedule':codes(old)=={'SCHEMA_UNKNOWN_FIELD','SCHEMA_VALIDATION','WF_EXPERIMENTAL_TARGET'},'newerAcceptsSchedules':new==[]})
 swapped=config.replace('3.5.0','TEMP').replace('3.6.0','3.5.0').replace('TEMP','3.6.0');config_path=work/'.argo-workflows-ls.yaml';client.send('textDocument/didOpen',{'textDocument':{'uri':config_path.as_uri(),'version':1,'languageId':'yaml','text':swapped}});a,b=both();m.result('config-target-swap',{'newTargetAccepted':a==[],'oldTargetRequired':codes(b)=={'SCHEMA_UNKNOWN_FIELD','SCHEMA_VALIDATION','WF_EXPERIMENTAL_TARGET'}})
 client.send('textDocument/didChange',{'textDocument':{'uri':config_path.as_uri(),'version':2},'contentChanges':[{'text':config.replace('3.5.0','99.0.0')}]});a,b=both();m.result('unknown-target-does-not-use-cache',{'bothUnavailable':codes(a)=={'WF_ANALYSIS_UNAVAILABLE'} and codes(b)=={'WF_ANALYSIS_UNAVAILABLE'}})
 client.send('textDocument/didChange',{'textDocument':{'uri':config_path.as_uri(),'version':3},'contentChanges':[{'text':config}]});a,b=both();m.result('target-recovery',{'oldStillRequired':codes(a)=={'SCHEMA_UNKNOWN_FIELD','SCHEMA_VALIDATION','WF_EXPERIMENTAL_TARGET'},'newStillAccepted':b==[]})
finally:client.close()
dest,work=m.setup('canceled-cold-analysis',{'case.yaml':text});client=m.LSP(dest,work)
try:
 path=work/'case.yaml';bad=text.replace('entrypoint: main','entrypoint: main\n    parallelism: wrong');client.send('textDocument/didOpen',{'textDocument':{'uri':path.as_uri(),'version':1,'languageId':'yaml','text':bad}});time.sleep(.25);client.send('workspace/symbol',{'query':''},901);client.send('$/cancelRequest',{'id':901});terminal=client.wait(lambda x:x.get('id')==901);client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':2},'contentChanges':[{'text':text}]});good=client.diagnostics(path.as_uri(),2);bad_ds=client.change(path,bad,3);good_again=client.change(path,text,4);m.result('cancel-and-value-recovery',{'cancelTerminal':terminal.get('error',{}).get('code')==-32800,'currentValid':good==[] and good_again==[],'invalidStillDetected':'SCHEMA_TYPE' in codes(bad_ds),'exactlyOneTerminal':sum(x.get('id')==901 for x in client.received)==1})
finally:client.close()
m.save(m.OUT/'manifest.json',{'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'semanticSource':'https://argo-workflows.readthedocs.io/en/release-3.6/cron-workflows/','independentExpectation':'schedules is supported from 3.6; 3.5 requires schedule. Unknown configuration and canceled requests must not reuse prior target or validation results.'});sys.exit(0 if len(m.RESULTS)==5 and all(r['pass'] for r in m.RESULTS) else 1)

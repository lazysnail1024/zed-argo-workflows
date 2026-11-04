#!/usr/bin/env python3
"""Nonregular YAML inputs preserve scope completeness and opened-buffer authority."""
import pathlib,sys,os,subprocess,json,importlib.util
root=pathlib.Path(__file__).resolve().parents[2];spec=importlib.util.spec_from_file_location('client',root/'tests/e2e/wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
config='schemaVersion: 1\ntargetVersion: "3.6.0"\nreferencePolicy: closed\ndefaultNamespace: argo\nscopes:\n  - name: good\n    include: ["good/*.yaml"]\n  - name: bad\n    include: ["bad/*.yaml"]\n'
text='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: caller, namespace: argo}\nspec:\n  workflowTemplateRef: {name: missing}\n'
dest,work=m.setup('nonregular-yaml',{'good/case.yaml':text},config);(work/'bad').mkdir();fifo=work/'bad/pipe.yaml';os.mkfifo(fifo)
for name,extra,expected_files in [('all',[],1),('selected-good',['--scope','good'],1),('selected-bad',['--scope','bad'],0)]:
 cmd=[str(m.BINARY),'check','--root',str(work),'--format','json',*extra]
 try:p=subprocess.run(cmd,capture_output=True,timeout=7)
 except subprocess.TimeoutExpired as e:p=subprocess.CompletedProcess(cmd,-9,e.stdout or b'',e.stderr or b'')
 (dest/(name+'.stdout')).write_bytes(p.stdout);(dest/(name+'.stderr')).write_bytes(p.stderr)
 try:r=json.loads(p.stdout)
 except ValueError:r={}
 unresolved=[d for d in r.get('diagnostics',[]) if d['code']=='WF_TEMPLATE_REF_UNRESOLVED'];checks={'boundedTerminal':p.returncode==(1 if name=='selected-good' else 2),'completedFiles':r.get('filesAnalyzed')==expected_files,'correctScopeCompleteness':(not unresolved if name=='selected-bad' else len(unresolved)==1 and unresolved[0]['severity']=='Error'),'nonregularReason':name=='selected-good' or any('regular' in x for x in r.get('notEvaluated',[]))};m.result(name,checks,{'command':cmd,'exitCode':p.returncode})
client=m.LSP(dest,work)
try:
 valid='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: opened}\nspec: {templates: [{name: main, container: {image: alpine}}]}\n';client.open(fifo,valid);r=client.request('textDocument/documentSymbol',{'textDocument':{'uri':fifo.as_uri()}});m.result('opened-fifo-buffer',{'symbolFromBuffer':[x['name'] for x in r.get('result',[])]==['main']},{'response':r})
except Exception as e:m.result('opened-fifo-buffer',{'completed':False},{'error':repr(e)})
finally:client.close()
m.save(m.OUT/'manifest.json',{'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__))});sys.exit(0 if len(m.RESULTS)==4 and all(r['pass'] for r in m.RESULTS) else 1)

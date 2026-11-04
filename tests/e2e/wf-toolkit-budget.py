#!/usr/bin/env python3
"""Real CLI and persistent stdio LSP reject deep YAML and recover on normal input."""
import pathlib,sys,importlib.util,json,hashlib
root=pathlib.Path(__file__).resolve().parents[2]
if len(sys.argv)<3:sys.argv.append(str(root/'artifacts/wf-toolkit-budget'))
spec=importlib.util.spec_from_file_location('consumer',root/'tests/e2e/wf-audit-gaps.py');a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
text='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: deep}\nspec:\n'+''.join('  '*i+'nested:\n' for i in range(1,301))+'  '*301+'leaf: true\n'
dest,work=a.setup('depth-budget',{'case.yaml':text});lsp=None
try:
 code,v=a.cli(dest,work)
 a.result('cli-depth-budget',{'incomplete_exit':code==2,'explicit_budget_reason':any('depth' in x.lower() or 'budget' in x.lower() for x in v['notEvaluated']),'incomplete':v['analysisComplete'] is False},v)
 lsp=a.LSP(dest,work);file=work/'case.yaml';lsp.open(file,text)
 bad='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: recovered}\nspec: {parallelism: wrong}\n'
 ds=lsp.change(file,bad,2)
 a.result('lsp-recovers-after-budget',{'schema_diagnostic_returns':any(x['code']=='SCHEMA_TYPE' for x in ds),'server_alive':lsp.p.poll() is None},ds)
except Exception as e:a.result('runner-error',{'completed':False},{'error':repr(e)})
finally:
 if lsp:lsp.close()
 sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
 a.save(a.OUT/'manifest.json',{'command':sys.argv,'pythonVersion':sys.version,'binarySha256':sha(a.BINARY),'testSha256':sha(pathlib.Path(__file__)),'clientSha256':sha(root/'tests/e2e/wf-audit-gaps.py'),'inputHashes':{str(p.relative_to(a.OUT)):sha(p) for p in a.OUT.rglob('*.yaml')},'expected':'300 nested mappings exit 2 with explicit budget reason; same LSP process subsequently returns SCHEMA_TYPE on ordinary input'})
sys.exit(0 if len(a.RESULTS)==2 and all(r['pass'] for r in a.RESULTS) else 1)
